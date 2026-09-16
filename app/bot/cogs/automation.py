import json
import logging
import time
from decimal import Decimal

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.bot.checks import can_admin
from app.bot.components_v2 import CardLayout, format_percent, strip_generic_emoji
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
from app.services.ai_config import (
    get_or_create_ai_config,
    set_ai_channels,
    set_suggestions_channel,
    set_support_channel,
)
from app.services.ai_gateway import AIUnavailable, available_providers, request_structured_ai
from app.services.calculator import (
    CalculationKind,
    ROBLOX_NET_AFTER_FEE,
    ROBUX_PRICE_PER_100,
    VIA_PLUS_PRICE_PER_100,
    apply_discount,
    extract_coupon_code,
    format_brl,
    format_robux,
    normalize_text,
    parse_calculation_message,
    robux_from_brl,
    robux_quote,
    strip_coupon,
)
from app.services.store_panel import get_coupon_by_code

logger = logging.getLogger(__name__)

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
GIFT_EMOJI = "<a:gift_Nextbuy:1549633615032221788>"

AI_SYSTEM_PROMPT = """Você é o interpretador da loja NEXTBUY no Discord.
Responda SOMENTE com um objeto JSON válido, sem markdown e sem texto fora do JSON.
Você não pode inventar preço, estoque, produto, política da loja ou disponibilidade.
Preço e estoque serão validados pelo backend; sua função é somente interpretar a intenção.
Não use emojis Unicode. Em perguntas gerais, você pode sugerir no máximo UM emoji customizado
pelo nome, e somente se ele estiver na lista de emojis fornecida.

Formato obrigatório:
{
  "intent": "robux|gamepass|item|catalog|store_question|unclear|other",
  "product_name": null,
  "game_name": null,
  "quantity": null,
  "robux": null,
  "brl": null,
  "question": null,
  "reply": null,
  "emoji_name": null,
  "missing": []
}

Regras:
- robux: pedido de cálculo/compra envolvendo quantidade de Robux.
- gamepass: pedido de Game Pass/Gift de um jogo; extraia jogo e Robux quando existirem.
- item: item específico de jogo; extraia nome, jogo e quantidade.
- catalog: pergunta se existe/tem estoque de um produto ou jogo.
- store_question: pergunta geral sobre a loja. Só preencha reply se a resposta estiver
  explicitamente sustentada pelo contexto fornecido. Caso contrário deixe reply null.
- unclear: intenção de compra/cálculo existe, mas faltam dados importantes; liste em missing.
- other: assunto sem relação clara com a loja.
- Nunca calcule preço dentro do JSON.
"""


def _is_available(product: Product) -> bool:
    return bool(product.active and (product.stock_quantity is None or product.stock_quantity > 0))


def _product_type(product: Product) -> str:
    return normalize_text(product.product_type).replace(" ", "_")


def _product_snapshot(products: list[Product]) -> list[dict[str, object]]:
    return [
        {
            "name": product.name,
            "type": product.product_type,
            "game": product.game_name,
            "active": bool(product.active),
            "stock": product.stock_quantity,
            "has_price": product.price_credits is not None,
        }
        for product in products[:80]
    ]


def _find_product(
    products: list[Product],
    *,
    product_name: str | None,
    game_name: str | None = None,
    allowed_types: set[str] | None = None,
) -> Product | None:
    wanted = normalize_text(product_name or "")
    wanted_game = normalize_text(game_name or "")
    candidates: list[Product] = []
    for product in products:
        if allowed_types and _product_type(product) not in allowed_types:
            continue
        if wanted_game and normalize_text(product.game_name or "") != wanted_game:
            continue
        normalized_name = normalize_text(product.name)
        if wanted and normalized_name == wanted:
            return product
        if wanted and (wanted in normalized_name or normalized_name in wanted):
            candidates.append(product)
    return candidates[0] if len(candidates) == 1 else None


def _game_exists(products: list[Product], game_name: str | None, *, types: set[str]) -> bool:
    wanted = normalize_text(game_name or "")
    if not wanted:
        return False
    return any(
        _product_type(product) in types
        and normalize_text(product.game_name or "") == wanted
        and _is_available(product)
        for product in products
    )


def _robux_available(products: list[Product], requested: int) -> bool:
    robux_products = [
        product
        for product in products
        if _product_type(product) == "robux" and _is_available(product)
    ]
    if not robux_products:
        return False
    return any(
        product.stock_quantity is None or product.stock_quantity >= requested
        for product in robux_products
    )


def _resolve_custom_emoji(guild: discord.Guild, name: str | None) -> str:
    if not name:
        return ""
    target = name.strip().lower()
    for emoji in guild.emojis:
        if emoji.name.lower() == target and emoji.is_usable():
            return str(emoji)
    return ""


def _channel_mention(guild: discord.Guild, channel_id: int | None) -> str | None:
    if not channel_id:
        return None
    channel = guild.get_channel(channel_id)
    return channel.mention if isinstance(channel, discord.TextChannel) else None


def _coupon_line(code: str | None, percent: Decimal | None) -> str:
    if not code or percent is None:
        return ""
    return f"- **Cupom:** `{code}` • **{format_percent(percent)}**"


async def _reply_card(
    message: discord.Message,
    *,
    title: str,
    lines: list[str],
    description: str | None = None,
    footer: str | None = None,
) -> None:
    if message.guild is None:
        return
    view = CardLayout(
        title=title,
        description=description,
        lines=[message.author.mention, *lines],
        footer=footer,
        timeout=180,
    )
    await message.reply(
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )


class AutomationCog(commands.Cog):
    ia = app_commands.Group(name="ia", description="Configura a IA de atendimento da NEXTBUY")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        if await can_admin(interaction):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Você não tem acesso a essa configuração.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Você não tem acesso a essa configuração.", ephemeral=True
            )
        return False

    @ia.command(name="canal", description="Adiciona ou remove um canal onde a IA pode responder")
    @app_commands.guild_only()
    async def ai_channel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        ativo: bool = True,
    ) -> None:
        if interaction.guild is None or not await self._require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            channels = [int(value) for value in (config.allowed_channel_ids or [])]
            if ativo and canal.id not in channels:
                channels.append(canal.id)
            elif not ativo:
                channels = [value for value in channels if value != canal.id]
            await set_ai_channels(session, config=config, channel_ids=channels)
        status = "adicionado" if ativo else "removido"
        await interaction.edit_original_response(
            content=f"{canal.mention} {status} dos canais autorizados da IA."
        )

    @ia.command(name="suporte", description="Define o canal usado quando a IA precisa da equipe")
    @app_commands.guild_only()
    async def ai_support(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ) -> None:
        if interaction.guild is None or not await self._require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_support_channel(session, config=config, channel_id=canal.id)
        await interaction.edit_original_response(content=f"Canal de suporte: {canal.mention}")

    @ia.command(
        name="sugestoes",
        description="Define o canal para pedidos de produtos/jogos que não estão na loja",
    )
    @app_commands.guild_only()
    async def ai_suggestions(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ) -> None:
        if interaction.guild is None or not await self._require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_suggestions_channel(session, config=config, channel_id=canal.id)
        await interaction.edit_original_response(content=f"Canal de sugestões: {canal.mention}")

    @ia.command(name="status", description="Liga ou desliga a IA neste servidor")
    @app_commands.guild_only()
    async def ai_status(self, interaction: discord.Interaction, ativo: bool) -> None:
        if interaction.guild is None or not await self._require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            config.enabled = ativo
        await interaction.edit_original_response(
            content=f"IA {'ativada' if ativo else 'desativada'} neste servidor."
        )

    @ia.command(name="ver", description="Mostra a configuração atual da IA")
    @app_commands.guild_only()
    async def ai_view(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await self._require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            channel_ids = [int(value) for value in (config.allowed_channel_ids or [])]
            support_id = config.support_channel_id
            suggestions_id = config.suggestions_channel_id
            enabled = config.enabled
            provider_order = list(config.provider_order or [])
        channel_text = ", ".join(
            channel.mention
            for channel_id in channel_ids
            if isinstance((channel := interaction.guild.get_channel(channel_id)), discord.TextChannel)
        ) or "Nenhum"
        providers = [item.name for item in available_providers(provider_order)]
        card = CardLayout(
            title="NEXTBUY • IA",
            lines=[
                f"- **Status:** `{'Ativa' if enabled else 'Desativada'}`",
                f"- **Canais autorizados:** {channel_text}",
                f"- **Suporte:** {_channel_mention(interaction.guild, support_id) or 'Não configurado'}",
                f"- **Sugestões:** {_channel_mention(interaction.guild, suggestions_id) or 'Não configurado'}",
                f"- **Provedores disponíveis:** `{', '.join(providers) if providers else 'nenhum'}`",
            ],
            footer="A IA ignora completamente canais que não estejam autorizados.",
        )
        await interaction.edit_original_response(content=None, view=card)

    async def _coupon(
        self,
        *,
        guild_id: int,
        content: str,
    ) -> tuple[str | None, Decimal | None, str | None]:
        try:
            code = extract_coupon_code(content)
        except ValueError as exc:
            return None, None, str(exc)
        if not code:
            return None, None, None
        async with SessionLocal() as session:
            coupon = await get_coupon_by_code(session, guild_id=guild_id, code=code)
        if coupon is None:
            return None, None, f"O cupom `{code}` não existe, está desativado ou acabou."
        return coupon.code, coupon.discount_percent, None

    async def _load_context(self, guild_id: int):
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, guild_id)
            products = list(
                (
                    await session.scalars(
                        select(Product)
                        .where(Product.guild_id == guild_id, Product.active.is_(True))
                        .order_by(Product.sort_order, Product.name)
                        .limit(100)
                    )
                ).all()
            )
            panel = await session.scalar(
                select(StorePanelConfig).where(StorePanelConfig.guild_id == guild_id)
            )
            return config, products, panel

    async def _interpret(
        self,
        message: discord.Message,
        *,
        products: list[Product],
        provider_order: list[str],
    ) -> dict[str, object] | None:
        if message.guild is None:
            return None
        prompt = {
            "message": message.content,
            "catalog": _product_snapshot(products),
            "server_emoji_names": [emoji.name for emoji in message.guild.emojis[:80]],
            "facts": {
                "currency": "BRL",
                "payment": "PIX com confirmação pela equipe",
                "robux_price_per_100": str(ROBUX_PRICE_PER_100),
            },
        }
        try:
            parsed, _provider = await request_structured_ai(
                system_prompt=AI_SYSTEM_PROMPT,
                user_prompt=json.dumps(prompt, ensure_ascii=False),
                provider_order=provider_order,
            )
            return parsed
        except AIUnavailable:
            return None

    async def _unavailable(
        self,
        message: discord.Message,
        *,
        product_label: str,
        suggestions_channel_id: int | None,
    ) -> None:
        if message.guild is None:
            return
        suggestions = _channel_mention(message.guild, suggestions_channel_id)
        destination = (
            f"Você pode pedir esse produto/jogo em {suggestions}."
            if suggestions
            else "Esse produto não está disponível na loja agora."
        )
        await _reply_card(
            message,
            title="Produto indisponível",
            lines=[f"**{product_label}** não está disponível no estoque atual.", destination],
        )

    async def _support_reply(
        self,
        message: discord.Message,
        *,
        text: str | None,
        support_channel_id: int | None,
        emoji_name: str | None = None,
    ) -> None:
        if message.guild is None:
            return
        support = _channel_mention(message.guild, support_channel_id)
        custom_emoji = _resolve_custom_emoji(message.guild, emoji_name)
        cleaned = strip_generic_emoji(text or "")
        lines: list[str] = []
        if cleaned:
            prefix = f"{custom_emoji} " if custom_emoji else ""
            lines.append(prefix + cleaned)
        if not cleaned or support:
            lines.append(
                f"Se precisar de confirmação da equipe, use {support}."
                if support
                else "Se precisar de confirmação, fale com a equipe."
            )
        await _reply_card(message, title="NEXTBUY", lines=lines)

    async def _handle_interpreted(
        self,
        message: discord.Message,
        parsed: dict[str, object],
        *,
        products: list[Product],
        panel: StorePanelConfig | None,
        support_channel_id: int | None,
        suggestions_channel_id: int | None,
    ) -> bool:
        if message.guild is None:
            return False
        intent = str(parsed.get("intent") or "unclear").strip().lower()
        product_name = str(parsed.get("product_name") or "").strip() or None
        game_name = str(parsed.get("game_name") or "").strip() or None
        coupon_code, discount_percent, coupon_error = await self._coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await _reply_card(message, title="Cupom", lines=[coupon_error])
            return True

        if intent == "robux":
            raw = parsed.get("robux")
            try:
                amount = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                request = parse_calculation_message(message.content)
                if request is not None and request.kind is CalculationKind.ROBUX:
                    amount = int(request.amount)
            if amount <= 0:
                await _reply_card(
                    message,
                    title="Falta a quantidade",
                    lines=["Edite sua mensagem e informe quantos **Robux** você quer calcular."],
                )
                return True
            if not _robux_available(products, amount):
                await self._unavailable(
                    message,
                    product_label=f"{format_robux(amount)}",
                    suggestions_channel_id=suggestions_channel_id,
                )
                return True
            quote = robux_quote(amount, discount_percent)
            lines = [
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
                f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`",
                f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`",
            ]
            if coupon_line := _coupon_line(coupon_code, discount_percent):
                lines.append(coupon_line)
            await _reply_card(
                message,
                title=f"{ROBUX_EMOJI} Cálculo — {format_robux(amount)}",
                lines=lines,
            )
            return True

        if intent == "gamepass":
            if not game_name:
                await _reply_card(
                    message,
                    title="Falta o jogo",
                    lines=["Edite sua mensagem e informe **qual jogo** é a Game Pass."],
                )
                return True
            if not _game_exists(products, game_name, types={"gamepass", "game_pass", "gift"}):
                await self._unavailable(
                    message,
                    product_label=f"Game Pass de {game_name}",
                    suggestions_channel_id=suggestions_channel_id,
                )
                return True
            try:
                amount = int(parsed.get("robux") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                await _reply_card(
                    message,
                    title="Falta o valor da Game Pass",
                    lines=["Edite sua mensagem e informe a quantidade de **Robux** da Game Pass."],
                )
                return True
            quote = robux_quote(amount, discount_percent)
            icon = ""
            if panel is not None:
                icon = dict(panel.game_icons or {}).get(normalize_text(game_name), "")
            lines = [
                f"{icon + ' ' if icon else ''}**{game_name}**",
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
            ]
            if coupon_line := _coupon_line(coupon_code, discount_percent):
                lines.append(coupon_line)
            await _reply_card(
                message,
                title=f"{GIFT_EMOJI} Game Pass — {format_robux(amount)}",
                lines=lines,
            )
            return True

        if intent == "item":
            product = _find_product(
                products,
                product_name=product_name,
                game_name=game_name,
                allowed_types={"item"},
            )
            if product is None:
                label = product_name or (f"item de {game_name}" if game_name else "item solicitado")
                await self._unavailable(
                    message,
                    product_label=label,
                    suggestions_channel_id=suggestions_channel_id,
                )
                return True
            try:
                quantity = max(1, int(parsed.get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            if not _is_available(product) or (
                product.stock_quantity is not None and product.stock_quantity < quantity
            ):
                await self._unavailable(
                    message,
                    product_label=product.name,
                    suggestions_channel_id=suggestions_channel_id,
                )
                return True
            if product.price_credits is None:
                await self._support_reply(
                    message,
                    text="Esse item existe na loja, mas ainda não possui preço cadastrado.",
                    support_channel_id=support_channel_id,
                )
                return True
            unit = Decimal(product.price_credits)
            total = apply_discount(unit * quantity, discount_percent)
            icon = ""
            if panel is not None and product.game_name:
                icon = dict(panel.game_icons or {}).get(normalize_text(product.game_name), "")
            lines = []
            if product.game_name:
                lines.append(f"{icon + ' ' if icon else ''}**{product.game_name}**")
            lines.extend(
                [
                    f"- **{GIFT_EMOJI} {product.name}:** `{quantity} × {format_brl(unit)}`",
                    f"- **{PIX_EMOJI} Valor final:** `{format_brl(total)}`",
                ]
            )
            if coupon_line := _coupon_line(coupon_code, discount_percent):
                lines.append(coupon_line)
            await _reply_card(
                message,
                title=f"{PIX_EMOJI} Cálculo — {quantity}× {product.name}",
                lines=lines,
            )
            return True

        if intent == "catalog":
            product = _find_product(products, product_name=product_name, game_name=game_name)
            if product is not None and _is_available(product):
                stock = "disponível" if product.stock_quantity is None else f"{product.stock_quantity} em estoque"
                await _reply_card(
                    message,
                    title="Disponibilidade",
                    lines=[f"**{product.name}** está disponível: **{stock}**."],
                )
            else:
                label = product_name or game_name or "produto solicitado"
                await self._unavailable(
                    message,
                    product_label=label,
                    suggestions_channel_id=suggestions_channel_id,
                )
            return True

        if intent == "store_question":
            await self._support_reply(
                message,
                text=str(parsed.get("reply") or "").strip() or None,
                support_channel_id=support_channel_id,
                emoji_name=str(parsed.get("emoji_name") or "").strip() or None,
            )
            return True

        if intent == "unclear":
            missing = parsed.get("missing")
            if isinstance(missing, list):
                missing_text = ", ".join(str(item) for item in missing if str(item).strip())
            else:
                missing_text = ""
            detail = (
                f"Faltou informar: **{missing_text}**."
                if missing_text
                else "Não consegui identificar todos os dados necessários."
            )
            await _reply_card(
                message,
                title="Preciso de mais detalhes",
                lines=[detail, "Edite sua mensagem e deixe o pedido mais claro."],
            )
            return True

        await self._support_reply(
            message,
            text=None,
            support_channel_id=support_channel_id,
        )
        return True

    async def _deterministic_fallback(
        self,
        message: discord.Message,
        *,
        products: list[Product],
        suggestions_channel_id: int | None,
    ) -> bool:
        request = parse_calculation_message(message.content)
        if request is None:
            return False
        coupon_code, discount_percent, coupon_error = await self._coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await _reply_card(message, title="Cupom", lines=[coupon_error])
            return True
        if request.kind is CalculationKind.ROBUX:
            amount = int(request.amount)
            if not _robux_available(products, amount):
                await self._unavailable(
                    message,
                    product_label=format_robux(amount),
                    suggestions_channel_id=suggestions_channel_id,
                )
                return True
            quote = robux_quote(amount, discount_percent)
            lines = [
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
                f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`",
                f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`",
            ]
            if coupon_line := _coupon_line(coupon_code, discount_percent):
                lines.append(coupon_line)
            await _reply_card(
                message,
                title=f"{ROBUX_EMOJI} Cálculo — {format_robux(amount)}",
                lines=lines,
            )
            return True

        amount = Decimal(request.amount)
        factor = Decimal("1")
        if discount_percent is not None:
            factor -= Decimal(discount_percent) / Decimal("100")
        gamepass_rate = ROBUX_PRICE_PER_100 * factor
        via_plus_rate = VIA_PLUS_PRICE_PER_100 * factor
        covering_rate = (ROBUX_PRICE_PER_100 / ROBLOX_NET_AFTER_FEE) * factor
        lines = [
            f"- **{GIFT_EMOJI} Game Pass:** `{format_robux(robux_from_brl(amount, gamepass_rate))}`",
            f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_robux(robux_from_brl(amount, via_plus_rate))}`",
            f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_robux(robux_from_brl(amount, covering_rate))}`",
        ]
        if coupon_line := _coupon_line(coupon_code, discount_percent):
            lines.append(coupon_line)
        await _reply_card(
            message,
            title=f"{PIX_EMOJI} Cálculo — {format_brl(amount)}",
            lines=lines,
        )
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content.strip():
            return

        config, products, panel = await self._load_context(message.guild.id)
        allowed = {int(value) for value in (config.allowed_channel_ids or [])}
        if not config.enabled or message.channel.id not in allowed:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0.0) < 2.0:
            return
        self._cooldowns[key] = now

        parsed = await self._interpret(
            message,
            products=products,
            provider_order=list(config.provider_order or []),
        )
        if parsed is not None:
            await self._handle_interpreted(
                message,
                parsed,
                products=products,
                panel=panel,
                support_channel_id=config.support_channel_id,
                suggestions_channel_id=config.suggestions_channel_id,
            )
            return

        handled = await self._deterministic_fallback(
            message,
            products=products,
            suggestions_channel_id=config.suggestions_channel_id,
        )
        if not handled:
            await self._support_reply(
                message,
                text=None,
                support_channel_id=config.support_channel_id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationCog(bot))
