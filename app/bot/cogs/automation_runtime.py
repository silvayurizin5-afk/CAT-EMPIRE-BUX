import json
import re
import time
from types import MethodType

import discord

from app.bot.cogs import automation as base
from app.bot.components_v2 import CardLayout, strip_generic_emoji
from app.services.ai_gateway import AIUnavailable, request_structured_ai

GENERAL_AI_PROMPT = (
    base.AI_SYSTEM_PROMPT
    + """

Regras adicionais de interpretação e atendimento:
- Perguntas como "estoque", "qual o estoque atual?", "o que tem disponível?" ou "quais produtos
  vocês têm?" são dúvidas gerais da loja. Use store_question; NÃO use catalog sem um produto/jogo
  específico.
- Use catalog somente quando o cliente perguntar pela disponibilidade de um produto/jogo específico,
  por exemplo "tem Blox Fruits?" ou "tem o produto X?".
- Perguntas sobre cupom, como "como funcionam os cupons?", são store_question. Explique apenas o
  funcionamento descrito em facts.coupons; nunca invente códigos de cupom.
- Quando intent for other, responda a pergunta normalmente no campo reply, de forma curta, útil e
  natural. Não transforme uma pergunta geral em atendimento da loja e não mencione suporte só por
  estar fora do tema da loja.
- Não invente dados da NEXTBUY. Se uma dúvida específica da loja não puder ser respondida com facts ou
  catalog, use store_question com reply null.
- Se o usuário estiver completando uma solicitação anterior com apenas um número, esse número pode ser
  a quantidade de Robux que faltava; o backend mantém esse contexto e fará a associação correta.
- Para perguntas gerais, continue sem emoji Unicode e sugira no máximo um emoji customizado existente.
- Não forneça instruções perigosas, ilegais ou que facilitem dano.

Exemplos:
Usuário: "Estoque" -> store_question.
Usuário: "Qual o estoque atual?" -> store_question.
Usuário: "Tem Blox Fruits?" -> catalog, game_name="Blox Fruits".
Usuário: "Como funciona os cupons?" -> store_question.
Usuário: "Quero uma gamepass do Blox Fruits" -> gamepass, game_name="Blox Fruits", robux=null.
"""
)

_ORIGINAL_HANDLE_AI = base.AutomationCog._handle_ai
_CONTEXT_TTL_SECONDS = 300.0

_STOCK_QUESTIONS = {
    "estoque",
    "estoque atual",
    "qual o estoque",
    "qual o estoque atual",
    "o que tem em estoque",
    "o que tem disponivel",
    "quais produtos tem",
    "quais produtos estao disponiveis",
    "produtos disponiveis",
}


def _numeric_amount(text: str) -> int | None:
    if not re.fullmatch(r"\s*[\d\s.,]+\s*", text):
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    value = int(digits)
    return value if 0 < value <= 1_000_000 else None


def _stock_answer(products) -> str:
    available = [product for product in products if base._available(product)]
    if not available:
        return "No momento não há produtos ativos com estoque disponível."

    lines = ["Estoque atual da loja:"]
    for product in available[:15]:
        stock = (
            "estoque ilimitado"
            if product.stock_quantity is None
            else f"{product.stock_quantity} em estoque"
        )
        price = (
            base.format_brl(product.price_credits)
            if product.price_credits is not None
            else "preço não cadastrado"
        )
        game = f" • {product.game_name}" if product.game_name else ""
        lines.append(f"- {product.name}{game}: {price} • {stock}")
    if len(available) > 15:
        lines.append(f"- +{len(available) - 15} produto(s) disponível(is)")
    return "\n".join(lines)


def _quick_store_answer(message: discord.Message, products) -> dict[str, object] | None:
    normalized = base.normalize_text(message.content)
    if normalized in _STOCK_QUESTIONS:
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": _stock_answer(products),
            "emoji_name": None,
        }

    if "cupom" in normalized and (
        normalized == "cupom"
        or "como" in normalized
        or "funciona" in normalized
        or "usar" in normalized
        or "aplicar" in normalized
        or "cupons" in normalized
    ):
        return {
            "intent": "store_question",
            "multiple_requests": False,
            "reply": (
                "Os cupons são aplicados antes de finalizar a compra pelo botão **Adicionar cupom**. "
                "O bot valida o código, verifica se ele está ativo e ainda possui usos; quando válido, "
                "o desconto aparece no valor final antes da criação do pedido. Não vou inventar códigos "
                "que não estejam cadastrados pela equipe."
            ),
            "emoji_name": None,
        }
    return None


async def _safe_reply(
    message: discord.Message,
    *,
    title: str,
    lines: list[str],
    footer: str | None = None,
) -> None:
    def build_view() -> CardLayout:
        return CardLayout(
            title=title,
            lines=[message.author.mention, *lines],
            footer=footer,
            timeout=180,
        )

    allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
    try:
        await message.reply(
            view=build_view(),
            mention_author=False,
            allowed_mentions=allowed_mentions,
        )
    except discord.HTTPException as exc:
        # A IA pode terminar de processar depois que a mensagem original foi apagada.
        # Nesse caso o Discord rejeita somente a referência; enviamos a resposta no canal.
        if exc.code != 50035 or "message_reference" not in str(exc):
            raise
        await message.channel.send(
            view=build_view(),
            allowed_mentions=allowed_mentions,
        )


async def _interpret(
    self,
    message: discord.Message,
    products,
    provider_order: list[str],
) -> dict[str, object] | None:
    if message.guild is None:
        return None

    quick = _quick_store_answer(message, products)
    if quick is not None:
        return quick

    payload = {
        "message": message.content,
        "catalog": base._snapshot(products),
        "server_emoji_names": [emoji.name for emoji in message.guild.emojis[:80]],
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
            "coupons": (
                "O cliente aplica um código pelo botão Adicionar cupom antes de comprar. "
                "O backend valida se o cupom existe, está ativo e ainda possui usos; se válido, "
                "o desconto é aplicado ao total antes da criação do pedido."
            ),
            "inventory": (
                "O catálogo enviado contém os produtos ativos conhecidos pelo backend. "
                "Pergunta geral de estoque deve ser store_question; catalog exige item específico."
            ),
        },
    }
    try:
        parsed, _ = await request_structured_ai(
            system_prompt=GENERAL_AI_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            provider_order=provider_order,
        )
        return parsed
    except AIUnavailable:
        return None


async def _support(
    self,
    message: discord.Message,
    text: str | None,
    support_id: int | None,
    emoji_name: str | None = None,
) -> None:
    if message.guild is None:
        return

    cleaned = strip_generic_emoji(text or "")
    emoji = base._custom_emoji(message.guild, emoji_name)
    if cleaned:
        await base._reply(
            message,
            title="NEXTBUY",
            lines=[f"{emoji + ' ' if emoji else ''}{cleaned}"],
        )
        return

    channel = base._mention_channel(message.guild, support_id)
    await base._reply(
        message,
        title="NEXTBUY",
        lines=[
            f"Não consegui confirmar essa informação da loja. Use {channel} para falar com a equipe."
            if channel
            else "Não consegui confirmar essa informação da loja. Fale com a equipe."
        ],
    )


async def _handle_ai(
    self,
    message: discord.Message,
    data: dict[str, object],
    products,
    panel,
    support_id: int | None,
    suggestions_id: int | None,
) -> None:
    if message.guild is None:
        return

    key = (message.guild.id, message.author.id)
    now = time.monotonic()
    pending = self._pending_context.get(key)
    if pending is not None and now - float(pending.get("created_at", 0.0)) > _CONTEXT_TTL_SECONDS:
        self._pending_context.pop(key, None)
        pending = None

    patched = dict(data)
    intent = str(patched.get("intent") or "unclear").strip().lower()

    if pending and pending.get("intent") == "gamepass" and patched.get("multiple_requests") is not True:
        amount = _numeric_amount(message.content)
        pending_game = str(pending.get("game_name") or "").strip()
        if pending_game and amount is not None:
            patched.update(
                {
                    "intent": "gamepass",
                    "game_name": pending_game,
                    "robux": amount,
                    "multiple_requests": False,
                }
            )
            intent = "gamepass"
            self._pending_context.pop(key, None)

    if intent == "gamepass" and patched.get("multiple_requests") is not True:
        game_name = str(patched.get("game_name") or "").strip() or None
        try:
            amount = int(patched.get("robux") or 0)
        except (TypeError, ValueError):
            amount = 0
        if not game_name or amount <= 0:
            self._pending_context[key] = {
                "intent": "gamepass",
                "game_name": game_name,
                "created_at": now,
            }
        else:
            self._pending_context.pop(key, None)

    if intent == "other":
        await self._support(
            message,
            str(patched.get("reply") or "").strip() or None,
            support_id,
            str(patched.get("emoji_name") or "").strip() or None,
        )
        return

    await _ORIGINAL_HANDLE_AI(
        self,
        message,
        patched,
        products,
        panel,
        support_id,
        suggestions_id,
    )


async def setup(bot) -> None:
    base._reply = _safe_reply
    cog = base.AutomationCog(bot)
    cog._pending_context = {}
    cog._interpret = MethodType(_interpret, cog)
    cog._support = MethodType(_support, cog)
    cog._handle_ai = MethodType(_handle_ai, cog)
    await bot.add_cog(cog)
