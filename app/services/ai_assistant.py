from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.db.models import Product
from app.services.ai_gateway import request_structured_ai


@dataclass(slots=True, frozen=True)
class AIIntent:
    intent: str
    product_name: str | None = None
    game_name: str | None = None
    quantity: int | None = None
    robux: int | None = None
    amount_brl: Decimal | None = None
    answer: str | None = None
    emoji_id: int | None = None


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number > 0 else None


def _catalog_lines(products: list[Product]) -> str:
    lines: list[str] = []
    for product in products[:80]:
        stock = "ilimitado" if product.stock_quantity is None else str(product.stock_quantity)
        price = "sem-preco" if product.price_credits is None else f"R${product.price_credits}"
        lines.append(
            " | ".join(
                [
                    f"nome={product.name}",
                    f"tipo={product.product_type}",
                    f"jogo={product.game_name or '-'}",
                    f"preco={price}",
                    f"estoque={stock}",
                ]
            )
        )
    return "\n".join(lines) if lines else "LOJA SEM PRODUTOS CADASTRADOS"


def _emoji_lines(emojis: list[tuple[int, str]]) -> str:
    if not emojis:
        return "NENHUM EMOJI PERSONALIZADO DISPONIVEL"
    return "\n".join(f"id={emoji_id} nome={name}" for emoji_id, name in emojis[:50])


async def parse_store_request(
    *,
    message: str,
    products: list[Product],
    provider_order: list[str],
    server_emojis: list[tuple[int, str]],
) -> tuple[AIIntent, str]:
    system_prompt = """Voce e o interpretador da NEXTBUY. Retorne APENAS JSON valido.
Voce NAO calcula precos, NAO inventa estoque, NAO cria regras da loja e NAO promete disponibilidade.
Sua funcao e interpretar a mensagem e extrair dados para o backend validar.

Intents permitidas:
- robux_calculation: cliente pede calculo envolvendo quantidade de Robux.
- money_calculation: cliente informa valor em reais e quer saber equivalente de Robux.
- item_request: pede item de jogo.
- gamepass_request: pede Game Pass.
- store_question: pergunta geral sobre a loja, atendimento ou funcionamento.
- clarify: pedido incompleto/ambiguo.

Formato obrigatorio:
{
  "intent": "...",
  "product_name": null,
  "game_name": null,
  "quantity": null,
  "robux": null,
  "amount_brl": null,
  "answer": null,
  "emoji_id": null
}

Regras:
- quantity e robux sao inteiros positivos quando existirem.
- amount_brl e numero positivo quando existir.
- answer so deve ser preenchido para store_question ou clarify e deve ser curto, em portugues do Brasil.
- Para store_question, se nao houver informacao suficiente no contexto, diga que nao pode confirmar e recomende falar com o suporte; nao invente.
- emoji_id pode ser UM id da lista de emojis do servidor somente para store_question. Nao use emoji generico. Para calculos deixe null.
"""
    user_prompt = (
        f"MENSAGEM DO CLIENTE:\n{message}\n\n"
        f"CATALOGO REAL DA LOJA:\n{_catalog_lines(products)}\n\n"
        f"EMOJIS PERSONALIZADOS DISPONIVEIS:\n{_emoji_lines(server_emojis)}"
    )
    data, provider = await request_structured_ai(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        provider_order=provider_order,
    )
    intent = _clean_string(data.get("intent")) or "clarify"
    if intent not in {
        "robux_calculation",
        "money_calculation",
        "item_request",
        "gamepass_request",
        "store_question",
        "clarify",
    }:
        intent = "clarify"
    emoji_id = _positive_int(data.get("emoji_id"))
    allowed_emoji_ids = {emoji_id_value for emoji_id_value, _ in server_emojis}
    if emoji_id not in allowed_emoji_ids:
        emoji_id = None
    return (
        AIIntent(
            intent=intent,
            product_name=_clean_string(data.get("product_name")),
            game_name=_clean_string(data.get("game_name")),
            quantity=_positive_int(data.get("quantity")),
            robux=_positive_int(data.get("robux")),
            amount_brl=_positive_decimal(data.get("amount_brl")),
            answer=_clean_string(data.get("answer")),
            emoji_id=emoji_id,
        ),
        provider,
    )
