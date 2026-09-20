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
)
from app.services.store_panel import get_coupon_by_code

logger = logging.getLogger(__name__)

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
GIFT_EMOJI = "<a:gift_Nextbuy:1549633615032221788>"

AI_SYSTEM_PROMPT = """Você interpreta mensagens para a loja NEXTBUY.
Responda SOMENTE JSON válido, sem markdown.
Nunca invente preço, estoque, produto, política ou disponibilidade. O backend valida tudo.
Não calcule preços. Não use emojis Unicode. Para perguntas gerais, pode sugerir no máximo
um emoji customizado pelo nome, somente entre os nomes fornecidos.
Formato:
{"intent":"robux|gamepass|item|catalog|store_question|unclear|other",
"multiple_requests":false,"product_name":null,"game_name":null,"quantity":null,
"robux":null,"brl":null,"question":null,"reply":null,"emoji_name":null,"missing":[]}
Use robux para cálculo/compra de Robux; gamepass para Game Pass/Gift; item para item de jogo;
catalog para disponibilidade; store_question para dúvida geral da loja; unclear se faltam dados;
other se não houver relação clara. Em store_question, só preencha reply se o contexto sustentar.
Se a mesma mensagem pedir dois ou mais produtos, jogos, cálculos ou tipos de compra independentes,
defina multiple_requests como true. Não combine valores, não escolha apenas um dos pedidos e não
tente responder parcialmente: cada produto ou cálculo deve ser enviado em uma mensagem separada.
"""


def _ptype(product: Product) -> str:
    return normalize_text(product.product_type).replace(" ", "_")


def _available(product: Product) -> bool:
    return bool(product.active and (product.stock_quantity is None or product.stock_quantity > 0))


def _snapshot(products: list[Product]) -> list[dict[str, object]]:
    return [
        {
            "name": p.name,
            "type": p.product_type,
            "game": p.game_name,
            "active": bool(p.active),
            "stock": p.stock_quantity,
            "has_price": p.price_credits is not None,
        }
        for p in products[:80]
    ]


def _find_product(
    products: list[Product],
    *,
    name: str | None,
    game: str | None = None,
    types: set[str] | None = None,
) -> Product | None:
    wanted = normalize_text(name or "")
    wanted_game = normalize_text(game or "")
    partial: list[Product] = []
    for product in products:
        if types and _ptype(product) not in types:
            continue
        if wanted_game and normalize_text(product.game_name or "") != wanted_game:
            continue
        current = normalize_text(product.name)
        if wanted and current == wanted:
            return product
        if wanted and (wanted in current or current in wanted):
            partial.append(product)
    return partial[0] if len(partial) == 1 else None


def _game_available(products: list[Product], game: str | None) -> bool:
    wanted = normalize_text(game or "")
    return bool(
        wanted
        and any(
            _ptype(p) in {"gamepass", "game_pass", "gift", "item"}
            and normalize_text(p.game_name or "") == wanted
            and _available(p)
            for p in products
        )
    )


def _robux_available(products: list[Product], amount: int) -> bool:
    candidates = [p for p in products if _ptype(p) == "robux" and _available(p)]
    return any(p.stock_quantity is None or p.stock_quantity >= amount for p in candidates)


def _mention_channel(guild: discord.Guild, channel_id: int | None) -> str | None:
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if isinstance(channel, discord.TextChannel) else None


def _custom_emoji(guild: discord.Guild, name: str | None) -> str:
    target = (name or "").strip().lower()
    if not target:
        return ""
    for emoji in guild.emojis:
        if emoji.name.lower() == target and emoji.is_usable():
            return str(emoji)
    return ""


def _coupon_line(code: str | None, percent: Decimal | None) -> str | None:
    if not code or percent is None:
        return None
    return f"- **Cupom:** `{code}` • **{format_percent(percent)}**"


async def _reply(
    message: discord.Message,
    *,
    title: str,
    lines: list[str],
    footer: str | None = None,
) -> None:
    await message.reply(
        view=CardLayout(
            title=title,
            lines=[message.author.mention, *lines],
            footer=footer,
            timeout=180,
        ),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )


class AutomationCog(commands.Cog):
    ia = app_commands.Group(name="ia", description="Configura a IA de atendimento da NEXTBUY")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def _admin(self, interaction: discord.Interaction) -> bool:
        if await can_admin(interaction):
            return True
        target = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await target("Você não tem acesso a essa configuração.", ephemeral=True)
        return False

    @ia.command(name="canal", description="Adiciona ou remove um canal onde a IA pode responder")
    @app_commands.guild_only()
    async def ai_channel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        ativo: bool = True,
    ) -> None:
        if interaction.guild is None or not await self._admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            ids = [int(value) for value in (config.allowed_channel_ids or [])]
            if ativo and canal.id not in ids:
                ids.append(canal.id)
            elif not ativo:
                ids = [value for value in ids if value != canal.id]
            await set_ai_channels(session, config=config, channel_ids=ids)
        await interaction.edit_original_response(
            content=f"{canal.mention} {'adicionado' if ativo else 'removido'} dos canais da IA."
        )

    @ia.command(name="suporte", description="Define o canal usado quando a IA precisa da equipe")
    @app_commands.guild_only()
    async def ai_support(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        if interaction.guild is None or not await self._admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_support_channel(session, config=config, channel_id=canal.id)
        await interaction.edit_original_response(content=f"Canal de suporte: {canal.mention}")

    @ia.command(name="sugestoes", description="Define o canal para produtos ausentes da loja")
    @app_commands.guild_only()
    async def ai_suggestions(
        self, interaction: discord.Interaction, canal: discord.TextChannel
    ) -> None:
        if interaction.guild is None or not await self._admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_suggestions_channel(session, config=config, channel_id=canal.id)
        await interaction.edit_original_response(content=f"Canal de sugestões: {canal.mention}")

    @ia.command(name="status", description="Liga ou desliga a IA neste servidor")
    @app_commands.guild_only()
    async def ai_status(self, interaction: discord.Interaction, ativo: bool) -> None:
        if interaction.guild is None or not await self._admin(interaction):
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
        if interaction.guild is None or not await self._admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            ids = [int(value) for value in (config.allowed_channel_ids or [])]
            support_id = config.support_channel_id
            suggestions_id = config.suggestions_channel_id
            enabled = config.enabled
            order = list(config.provider_order or [])
        channels = ", ".join(
            channel.mention
            for channel_id in ids
            if isinstance((channel := interaction.guild.get_channel(channel_id)), discord.TextChannel)
        ) or "Nenhum"
        providers = ", ".join(item.name for item in available_providers(order)) or "nenhum"
        await interaction.edit_original_response(
            content=None,
            view=CardLayout(
                title="NEXTBUY • IA",
                lines=[
                    f"- **Status:** `{'Ativa' if enabled else 'Desativada'}`",
                    f"- **Canais autorizados:** {channels}",
                    f"- **Suporte:** {_mention_channel(interaction.guild, support_id) or 'Não configurado'}",
                    f"- **Sugestões:** {_mention_channel(interaction.guild, suggestions_id) or 'Não configurado'}",
                    f"- **Provedores disponíveis:** `{providers}`",
                ],
                footer="Fora dos canais autorizados a IA não responde.",
            ),
        )

    async def _coupon(
        self, guild_id: int, content: str
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

    async def _context(self, guild_id: int):
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
        self, message: discord.Message, products: list[Product], provider_order: list[str]
    ) -> dict[str, object] | None:
        if message.guild is None:
            return None
        payload = {
            "message": message.content,
            "catalog": _snapshot(products),
            "server_emoji_names": [emoji.name for emoji in message.guild.emojis[:80]],
            "facts": {
                "currency": "BRL",
                "payment": "PIX com confirmação pela equipe",
                "robux_price_per_100": str(ROBUX_PRICE_PER_100),
            },
        }
        try:
            parsed, _ = await request_structured_ai(
                system_prompt=AI_SYSTEM_PROMPT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                provider_order=provider_order,
            )
            return parsed
        except AIUnavailable:
            return None

    async def _unavailable(
        self, message: discord.Message, label: str, suggestions_id: int | None
    ) -> None:
        if message.guild is None:
            return
        channel = _mention_channel(message.guild, suggestions_id)
        destination = (
            f"Você pode pedir esse produto/jogo em {channel}."
            if channel
            else "Esse produto não está disponível na loja agora."
        )
        await _reply(
            message,
            title="Produto indisponível",
            lines=[f"**{label}** não está disponível no estoque atual.", destination],
        )

    async def _support(
        self,
        message: discord.Message,
        text: str | None,
        support_id: int | None,
        emoji_name: str | None = None,
    ) -> None:
        if message.guild is None:
            return
        channel = _mention_channel(message.guild, support_id)
        cleaned = strip_generic_emoji(text or "")
        emoji = _custom_emoji(message.guild, emoji_name)
        lines: list[str] = []
        if cleaned:
            lines.append(f"{emoji + ' ' if emoji else ''}{cleaned}")
        if not cleaned or channel:
            lines.append(
                f"Se precisar de confirmação da equipe, use {channel}."
                if channel
                else "Se precisar de confirmação, fale com a equipe."
            )
        await _reply(message, title="NEXTBUY", lines=lines)

    async def _handle_ai(
        self,
        message: discord.Message,
        data: dict[str, object],
        products: list[Product],
        panel: StorePanelConfig | None,
        support_id: int | None,
        suggestions_id: int | None,
    ) -> None:
        if message.guild is None:
            return
        if data.get("multiple_requests") is True:
            await _reply(
                message,
                title="Separe os pedidos",
                lines=[
                    "Identifiquei mais de um produto ou cálculo na mesma mensagem.",
                    "Envie **um produto ou cálculo por mensagem** para eu analisar cada pedido sem misturar valores.",
                ],
            )
            return

        intent = str(data.get("intent") or "unclear").strip().lower()
        product_name = str(data.get("product_name") or "").strip() or None
        game_name = str(data.get("game_name") or "").strip() or None
        code, discount, error = await self._coupon(message.guild.id, message.content)
        if error:
            await _reply(message, title="Cupom", lines=[error])
            return

        if intent == "robux":
            try:
                amount = int(data.get("robux") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                request = parse_calculation_message(message.content)
                if request is not None and request.kind is CalculationKind.ROBUX:
                    amount = int(request.amount)
            if amount <= 0:
                await _reply(
                    message,
                    title="Falta a quantidade",
                    lines=["Informe quantos **Robux** você quer calcular."],
                )
                return
            if not _robux_available(products, amount):
                await self._unavailable(message, format_robux(amount), suggestions_id)
                return
            quote = robux_quote(amount, discount)
            lines = [
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
                f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`",
                f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`",
            ]
            if coupon := _coupon_line(code, discount):
                lines.append(coupon)
            await _reply(
                message,
                title=f"{ROBUX_EMOJI} Cálculo — {format_robux(amount)}",
                lines=lines,
            )
            return

        if intent == "gamepass":
            if not game_name:
                await _reply(
                    message,
                    title="Falta o jogo",
                    lines=["Informe **qual jogo** é a Game Pass."],
                )
                return
            if not _game_available(products, game_name):
                await self._unavailable(message, f"Game Pass de {game_name}", suggestions_id)
                return
            try:
                amount = int(data.get("robux") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                await _reply(
                    message,
                    title="Falta o valor",
                    lines=["Informe a quantidade de **Robux** da Game Pass."],
                )
                return
            icon = ""
            if panel is not None:
                icon = dict(panel.game_icons or {}).get(normalize_text(game_name), "")
            quote = robux_quote(amount, discount)
            lines = [
                f"{icon + ' ' if icon else ''}**{game_name}**",
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
            ]
            if coupon := _coupon_line(code, discount):
                lines.append(coupon)
            await _reply(
                message,
                title=f"{GIFT_EMOJI} Game Pass — {format_robux(amount)}",
                lines=lines,
            )
            return

        if intent == "item":
            product = _find_product(
                products,
                name=product_name,
                game=game_name,
                types={"item"},
            )
            if product is None:
                await self._unavailable(
                    message,
                    product_name or game_name or "item solicitado",
                    suggestions_id,
                )
                return
            try:
                quantity = max(1, int(data.get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            if not _available(product) or (
                product.stock_quantity is not None and product.stock_quantity < quantity
            ):
                await self._unavailable(message, product.name, suggestions_id)
                return
            if product.price_credits is None:
                await self._support(
                    message,
                    "Esse item existe na loja, mas ainda não possui preço cadastrado.",
                    support_id,
                )
                return
            unit = Decimal(product.price_credits)
            total = apply_discount(unit * quantity, discount)
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
            if coupon := _coupon_line(code, discount):
                lines.append(coupon)
            await _reply(
                message,
                title=f"{PIX_EMOJI} Cálculo — {quantity}× {product.name}",
                lines=lines,
            )
            return

        if intent == "catalog":
            product = _find_product(products, name=product_name, game=game_name)
            if product is not None and _available(product):
                stock = (
                    "disponível"
                    if product.stock_quantity is None
                    else f"{product.stock_quantity} em estoque"
                )
                await _reply(
                    message,
                    title="Disponibilidade",
                    lines=[f"**{product.name}** está disponível: **{stock}**."],
                )
            else:
                await self._unavailable(
                    message,
                    product_name or game_name or "produto solicitado",
                    suggestions_id,
                )
            return

        if intent == "store_question":
            await self._support(
                message,
                str(data.get("reply") or "").strip() or None,
                support_id,
                str(data.get("emoji_name") or "").strip() or None,
            )
            return

        if intent == "unclear":
            missing = data.get("missing")
            names = (
                ", ".join(str(item) for item in missing if str(item).strip())
                if isinstance(missing, list)
                else ""
            )
            await _reply(
                message,
                title="Preciso de mais detalhes",
                lines=[
                    f"Faltou informar: **{names}**."
                    if names
                    else "Não consegui identificar todos os dados necessários.",
                    "Deixe o pedido mais claro e envie novamente.",
                ],
            )
            return

        await self._support(message, None, support_id)

    async def _fallback(
        self,
        message: discord.Message,
        products: list[Product],
        suggestions_id: int | None,
    ) -> bool:
        if message.guild is None:
            return False
        request = parse_calculation_message(message.content)
        if request is None:
            return False
        code, discount, error = await self._coupon(message.guild.id, message.content)
        if error:
            await _reply(message, title="Cupom", lines=[error])
            return True
        if request.kind is CalculationKind.ROBUX:
            amount = int(request.amount)
            if not _robux_available(products, amount):
                await self._unavailable(message, format_robux(amount), suggestions_id)
                return True
            quote = robux_quote(amount, discount)
            lines = [
                f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`",
                f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`",
                f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`",
            ]
        else:
            amount = Decimal(request.amount)
            factor = Decimal("1") - (Decimal(discount or 0) / Decimal("100"))
            lines = [
                f"- **{GIFT_EMOJI} Game Pass:** `{format_robux(robux_from_brl(amount, ROBUX_PRICE_PER_100 * factor))}`",
                f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_robux(robux_from_brl(amount, VIA_PLUS_PRICE_PER_100 * factor))}`",
                f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_robux(robux_from_brl(amount, (ROBUX_PRICE_PER_100 / ROBLOX_NET_AFTER_FEE) * factor))}`",
            ]
        if coupon := _coupon_line(code, discount):
            lines.append(coupon)
        title = (
            f"{ROBUX_EMOJI} Cálculo — {format_robux(int(request.amount))}"
            if request.kind is CalculationKind.ROBUX
            else f"{PIX_EMOJI} Cálculo — {format_brl(Decimal(request.amount))}"
        )
        await _reply(message, title=title, lines=lines)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.author.bot
            or message.guild is None
            or not is_store_guild(message.guild.id)
            or not message.content.strip()
        ):
            return
        config, products, panel = await self._context(message.guild.id)
        allowed = {int(value) for value in (config.allowed_channel_ids or [])}
        if not config.enabled or message.channel.id not in allowed:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0.0) < 2.0:
            return
        self._cooldowns[key] = now

        parsed = await self._interpret(message, products, list(config.provider_order or []))
        if parsed is not None:
            await self._handle_ai(
                message,
                parsed,
                products,
                panel,
                config.support_channel_id,
                config.suggestions_channel_id,
            )
            return
        if not await self._fallback(message, products, config.suggestions_channel_id):
            await self._support(message, None, config.support_channel_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationCog(bot))
