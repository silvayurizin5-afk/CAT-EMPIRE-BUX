from __future__ import annotations

import json

from app.bot.cogs import automation as base
from app.bot.cogs import automation_runtime as runtime
from app.bot.components_v2 import strip_generic_emoji
from app.services.ai_gateway import AIUnavailable, request_structured_ai
from app.services.public_knowledge import fetch_public_knowledge

_ORIGINAL_QUICK = runtime._quick_store_answer

runtime.GENERAL_AI_PROMPT += """
- Para intent other, deixe reply como null. A resposta geral será produzida por uma segunda etapa com
  contexto e fontes públicas opcionais. Sua função principal aqui é classificar corretamente.
- Se a mensagem contiver a palavra estoque sem nome de produto específico, classifique como
  store_question; o backend consultará o estoque real no PostgreSQL.
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
        )
    )


async def _general_answer(self, message, products, *, state):
    if message.guild is None:
        return None
    order = self._provider_orders.get(message.guild.id)
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
        "catalog": base._snapshot(products),
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
            "inventory": (
                "O catálogo desta requisição veio diretamente do PostgreSQL da NEXTBUY e representa "
                "os produtos ativos e o estoque conhecido no instante da mensagem."
            ),
            "coupons": (
                "O cliente aplica um código pelo botão Adicionar cupom antes de comprar; "
                "o backend valida existência, status e limite de usos."
            ),
        },
        "public_sources": public_sources,
    }
    prompt = runtime.GENERAL_QA_PROMPT + """

Aprofundamento e fontes:
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
