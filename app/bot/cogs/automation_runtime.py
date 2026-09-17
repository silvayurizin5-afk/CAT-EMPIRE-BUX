import json
from types import MethodType

import discord

from app.bot.cogs import automation as base
from app.bot.components_v2 import CardLayout, strip_generic_emoji
from app.services.ai_gateway import AIUnavailable, request_structured_ai

GENERAL_AI_PROMPT = (
    base.AI_SYSTEM_PROMPT
    + """

Regra adicional para perguntas gerais:
- Quando intent for other, responda a pergunta normalmente no campo reply, de forma curta, útil e
  natural. Não transforme uma pergunta geral em atendimento da loja e não mencione suporte só por
  estar fora do tema da loja.
- Não invente dados da NEXTBUY. Se a pergunta depender de informação específica da loja que não está
  no contexto, use store_question com reply null.
- Para perguntas gerais, continue sem emoji Unicode e sugira no máximo um emoji customizado existente.
- Não forneça instruções perigosas, ilegais ou que facilitem dano.
"""
)

_ORIGINAL_HANDLE_AI = base.AutomationCog._handle_ai


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
    payload = {
        "message": message.content,
        "catalog": base._snapshot(products),
        "server_emoji_names": [emoji.name for emoji in message.guild.emojis[:80]],
        "facts": {
            "currency": "BRL",
            "payment": "PIX com confirmação pela equipe",
            "robux_price_per_100": str(base.ROBUX_PRICE_PER_100),
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
            f"Não consegui responder com segurança agora. Use {channel} para falar com a equipe."
            if channel
            else "Não consegui responder agora. Fale com a equipe."
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
    intent = str(data.get("intent") or "unclear").strip().lower()
    if intent == "other":
        await self._support(
            message,
            str(data.get("reply") or "").strip() or None,
            support_id,
            str(data.get("emoji_name") or "").strip() or None,
        )
        return

    await _ORIGINAL_HANDLE_AI(
        self,
        message,
        data,
        products,
        panel,
        support_id,
        suggestions_id,
    )


async def setup(bot) -> None:
    base._reply = _safe_reply
    cog = base.AutomationCog(bot)
    cog._interpret = MethodType(_interpret, cog)
    cog._support = MethodType(_support, cog)
    cog._handle_ai = MethodType(_handle_ai, cog)
    await bot.add_cog(cog)
