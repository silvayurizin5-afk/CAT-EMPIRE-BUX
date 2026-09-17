from __future__ import annotations

import json

from sqlalchemy import select

from app.bot.cogs import automation as base
from app.bot.cogs import automation_runtime as runtime
from app.bot.components_v2 import format_percent, strip_generic_emoji
from app.db.models import RobuxRate, TermsDocument
from app.db.session import SessionLocal
from app.db.store_models import StoreCoupon, StorePanelConfig
from app.services.ai_gateway import AIUnavailable, request_structured_ai
from app.services.public_knowledge import fetch_public_knowledge

_ORIGINAL_QUICK = runtime._quick_store_answer

runtime.GENERAL_AI_PROMPT += """
- Para intent other, deixe reply como null. A resposta geral será produzida por uma segunda etapa com
  contexto e fontes públicas opcionais. Sua função principal aqui é classificar corretamente.
- Se a mensagem contiver a palavra estoque sem nome de produto específico, classifique como
  store_question; o backend consultará o estoque real no PostgreSQL.
- Perguntas sobre a NEXTBUY devem usar o estado real da loja fornecido pelo backend. Nunca diga que
  não possui acesso a estoque, preços, cupons, produtos, cotações ou termos quando esses dados estiverem
  presentes em store_state/catalog.
"""


def _live_product_line(product) -> str:
    stock = runtime._stock_text(product)
    price = (
        base.format_brl(product.price_credits)
        if product.price_credits is not None
        else "preço não cadastrado"
    )
    game = f" • {product.game_name}" if product.game_name else ""
    return f"- **{product.name}**{game}: **{price}** • {stock}"


def _all_prices_answer(products) -> str:
    available = runtime._available_products(products)
    if not available:
        return "No momento não há produtos ativos disponíveis na loja."
    lines = ["Preços atuais da loja:"]
    lines.extend(_live_product_line(product) for product in available[:20])
    if len(available) > 20:
        lines.append(f"- +{len(available) - 20} produto(s)")
    return "\n".join(lines)


def _stock_product_answer(product) -> str:
    return "Estoque em tempo real:\n" + _live_product_line(product)


def _coupon_available(coupon) -> bool:
    return bool(
        coupon.active
        and (coupon.max_uses is None or int(coupon.uses or 0) < int(coupon.max_uses))
    )


def _coupon_remaining(coupon) -> str:
    if coupon.max_uses is None:
        return "uso ilimitado enquanto estiver ativo"
    remaining = max(0, int(coupon.max_uses) - int(coupon.uses or 0))
    return f"{remaining} uso(s) restante(s)"


def _coupon_answer(coupons, question: str) -> str:
    normalized = base.normalize_text(question)
    available = [coupon for coupon in coupons if _coupon_available(coupon)]

    matched = next(
        (
            coupon
            for coupon in coupons
            if coupon.code and coupon.code.casefold() in question.casefold()
        ),
        None,
    )
    if matched is not None:
        if not _coupon_available(matched):
            return f"O cupom `{matched.code}` existe, mas não está disponível no momento."
        return (
            f"O cupom `{matched.code}` está disponível: **{format_percent(matched.discount_percent)}** "
            f"de desconto • {_coupon_remaining(matched)}."
        )

    asks_how = any(
        marker in normalized
        for marker in ("como", "usar", "usa", "funciona", "aplicar", "aplica")
    )
    lines: list[str] = []
    if asks_how:
        lines.append(
            "Para usar um cupom, abra a compra do produto, toque em **Adicionar cupom** e informe o "
            "código. O desconto é validado e aplicado antes da criação do pedido."
        )

    if not available:
        lines.append("No momento, não há cupons ativos com usos disponíveis na loja.")
        return "\n".join(lines)

    lines.append("Cupons disponíveis agora:")
    for coupon in available[:20]:
        lines.append(
            f"- `{coupon.code}` • **{format_percent(coupon.discount_percent)}** • "
            f"{_coupon_remaining(coupon)}"
        )
    return "\n".join(lines)


def _quick_store_answer(message, products, state):
    normalized = base.normalize_text(message.content)
    named_product = runtime._match_product(message.content, products)
    named_game = runtime._match_game(message.content, products)

    # Estoque nunca depende do LLM: ele é lido do catálogo carregado do PostgreSQL nesta mensagem.
    if "estoque" in normalized:
        if named_product is not None:
            return {
                "intent": "store_question",
                "multiple_requests": False,
                "reply": _stock_product_answer(named_product),
                "emoji_name": None,
                "_context_game_name": named_product.game_name,
                "_context_product_name": named_product.name,
            }
        if named_game:
            answer, only_product = runtime._game_answer(products, named_game)
            return {
                "intent": "store_question",
                "multiple_requests": False,
                "reply": answer,
                "emoji_name": None,
                "_context_game_name": named_game,
                "_context_product_name": only_product,
            }
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": runtime._stock_answer(products),
            "emoji_name": None,
        }

    # Cupons precisam de consulta ao banco, feita em _general_answer (assíncrono).
    if "cupom" in normalized or "cupons" in normalized:
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": None,
            "emoji_name": None,
        }

    asks_price = any(marker in normalized for marker in runtime._PRICE_MARKERS)
    generic_price = any(
        marker in normalized
        for marker in (
            "qualquer produto",
            "todos os produtos",
            "precos dos produtos",
            "preco dos produtos",
            "lista de precos",
            "lista de preco",
        )
    )
    if asks_price and generic_price and named_product is None:
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": _all_prices_answer(products),
            "emoji_name": None,
        }

    return _ORIGINAL_QUICK(message, products, state)


def _store_specific_question(text: str) -> bool:
    normalized = base.normalize_text(text)
    return any(
        marker in normalized
        for marker in (
            "nextbuy",
            "estoque",
            "gamepass",
            "game pass",
            "robux",
            "cupom",
            "loja",
            "comprar",
            "produto",
            "pix",
            "pagamento",
            "termo",
            "entrega",
        )
    )


def _product_snapshot(products) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for product in products[:100]:
        rows.append(
            {
                "name": product.name,
                "type": product.product_type,
                "game": product.game_name,
                "description": (product.description or "")[:600],
                "price_brl": (
                    str(product.price_credits) if product.price_credits is not None else None
                ),
                "stock": product.stock_quantity,
                "stock_unlimited": product.stock_quantity is None,
                "active": bool(product.active),
                "delivery_mode": product.delivery_mode,
                "has_image": bool(product.image_url),
            }
        )
    return rows


async def _load_store_state(guild_id: int) -> dict[str, object]:
    async with SessionLocal() as session:
        coupons = list(
            (
                await session.scalars(
                    select(StoreCoupon)
                    .where(StoreCoupon.guild_id == guild_id)
                    .order_by(StoreCoupon.active.desc(), StoreCoupon.code)
                    .limit(50)
                )
            ).all()
        )
        rates = list(
            (
                await session.scalars(
                    select(RobuxRate)
                    .where(RobuxRate.guild_id == guild_id, RobuxRate.active.is_(True))
                    .order_by(RobuxRate.sort_order, RobuxRate.label)
                    .limit(25)
                )
            ).all()
        )
        terms = list(
            (
                await session.scalars(
                    select(TermsDocument)
                    .where(TermsDocument.guild_id == guild_id, TermsDocument.active.is_(True))
                    .order_by(TermsDocument.title)
                    .limit(20)
                )
            ).all()
        )
        panel = await session.scalar(
            select(StorePanelConfig).where(StorePanelConfig.guild_id == guild_id)
        )

    return {
        "coupons": [
            {
                "code": coupon.code,
                "discount_percent": str(coupon.discount_percent),
                "active": bool(coupon.active),
                "uses": int(coupon.uses or 0),
                "max_uses": coupon.max_uses,
                "available": _coupon_available(coupon),
            }
            for coupon in coupons
        ],
        "robux_rates": [
            {
                "code": rate.code,
                "label": rate.label,
                "price_per_robux": str(rate.price_per_robux),
                "delivery_label": rate.delivery_label,
            }
            for rate in rates
        ],
        "terms": [
            {
                "code": term.code,
                "title": term.title,
                "version": term.version,
                "content": term.content[:1200],
            }
            for term in terms
        ],
        "panel": (
            {
                "title": panel.title,
                "description": panel.description,
                "checkout_description": panel.checkout_description,
                "buy_button_label": panel.buy_button_label,
                "coupon_button_label": panel.coupon_button_label,
                "footer_text": panel.footer_text,
            }
            if panel is not None
            else None
        ),
        "_coupon_objects": coupons,
    }


async def _general_answer(self, message, products, *, state):
    if message.guild is None:
        return None
    order = self._provider_orders.get(message.guild.id)
    store_state = await _load_store_state(message.guild.id)
    normalized = base.normalize_text(message.content)

    if "cupom" in normalized or "cupons" in normalized:
        return _coupon_answer(store_state.pop("_coupon_objects"), message.content)

    store_state.pop("_coupon_objects", None)
    public_sources = []
    if not _store_specific_question(message.content):
        public_sources = await fetch_public_knowledge(message.content)

    payload = {
        "message": message.content,
        "conversation_context": {
            "game_name": state.get("game_name"),
            "product_name": state.get("product_name"),
            "last_intent": state.get("intent"),
        },
        "catalog": _product_snapshot(products),
        "store_state": store_state,
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
            "inventory": (
                "O catálogo desta requisição veio diretamente do PostgreSQL da NEXTBUY e representa "
                "os produtos ativos e o estoque conhecido no instante da mensagem."
            ),
            "store_state": (
                "store_state foi consultado diretamente no PostgreSQL no instante da mensagem e "
                "contém cupons, cotações, termos e configuração pública da loja."
            ),
        },
        "public_sources": public_sources,
    }
    prompt = runtime.GENERAL_QA_PROMPT + """

Aprofundamento e fontes:
- Para qualquer pergunta sobre a NEXTBUY, trate catalog e store_state como fonte primária e atual.
- Nunca diga que não possui acesso a estoque, preços, cupons, cotações ou termos se os dados estiverem
  presentes no payload. Responda diretamente com os dados fornecidos.
- Quando public_sources estiver preenchido, use esses trechos como contexto factual adicional e
  reconcilie diferenças com cuidado. Não invente uma fonte nem informação ausente dos trechos.
- Se public_sources estiver vazio, ainda pode responder conhecimento geral estável do modelo, deixando
  claro quando algo for incerto ou depender de informação atualizada.
- Dados específicos da NEXTBUY têm prioridade absoluta sobre qualquer fonte externa.
- Responda a pergunta de fato; não mande o usuário ao suporte quando for uma dúvida geral comum.
"""
    try:
        parsed, _ = await request_structured_ai(
            system_prompt=prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            provider_order=order,
        )
    except AIUnavailable:
        return None
    reply = strip_generic_emoji(str(parsed.get("reply") or "").strip())
    return reply or None


def _looks_like_commerce_request(text: str) -> bool:
    """Só pedidos/ações explícitas exigem detalhes; dúvidas sobre produtos podem ser respondidas."""
    normalized = base.normalize_text(text)
    explicit_action = any(
        marker in normalized
        for marker in (
            "quero comprar",
            "quero uma",
            "quero um",
            "vou comprar",
            "comprar ",
            "calcule ",
            "calcular ",
            "faz o calculo",
            "faca o calculo",
        )
    )
    return explicit_action


async def setup(bot) -> None:
    # Os métodos ligados pelo automation_runtime consultam essas funções pelo módulo em tempo de
    # execução; trocar os helpers aqui refina o comportamento sem duplicar o cog/listener.
    runtime._quick_store_answer = _quick_store_answer
    runtime._general_answer = _general_answer
    runtime._looks_like_commerce_request = _looks_like_commerce_request
