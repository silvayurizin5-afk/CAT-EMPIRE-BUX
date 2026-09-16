from decimal import Decimal

import discord
from discord.ext import commands
from sqlalchemy import select

from app.bot.views.components_v2 import format_percent
from app.db.ai_models import AIConfig
from app.db.models import Product
from app.db.session import SessionLocal
from app.services.ai_assistant import AIIntent, parse_store_request
from app.services.ai_gateway import AIUnavailable
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

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
GIFT_EMOJI = "<a:gift_Nextbuy:1549633615032221788>"
DEFAULT_ACCENT = 0x2B2D31


class AIReplyView(discord.ui.LayoutView):
    def __init__(self, body: str) -> None:
        super().__init__(timeout=180)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(body[:4000]),
                accent_color=DEFAULT_ACCENT,
            )
        )


def _channel_mention(channel_id: int | None, *, fallback: str) -> str:
    return f"<#{channel_id}>" if channel_id else fallback


def _find_product(
    products: list[Product],
    *,
    product_name: str | None,
    game_name: str | None,
    product_types: set[str] | None = None,
) -> Product | None:
    name_key = normalize_text(product_name or "")
    game_key = normalize_text(game_name or "")
    candidates = [
        product
        for product in products
        if product_types is None or product.product_type in product_types
    ]
    if name_key:
        exact = [product for product in candidates if normalize_text(product.name) == name_key]
        if exact:
            candidates = exact
        else:
            fuzzy = [
                product
                for product in candidates
                if name_key in normalize_text(product.name)
                or normalize_text(product.name) in name_key
            ]
            if fuzzy:
                candidates = fuzzy
            else:
                return None
    if game_key:
        by_game = [
            product
            for product in candidates
            if normalize_text(product.game_name or "") == game_key
            or game_key in normalize_text(product.game_name or "")
        ]
        if by_game:
            candidates = by_game
        elif product_name is None:
            return None
    return candidates[0] if candidates else None


def _known_game(products: list[Product], game_name: str | None) -> bool:
    key = normalize_text(game_name or "")
    if not key:
        return False
    return any(
        key == normalize_text(product.game_name or "")
        or key in normalize_text(product.game_name or "")
        for product in products
    )


def _robux_stock(products: list[Product]) -> int | None:
    robux_products = [product for product in products if product.product_type.startswith("robux")]
    if any(product.stock_quantity is None for product in robux_products):
        return None
    return sum(product.stock_quantity or 0 for product in robux_products)


async def _coupon(
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


def _coupon_line(code: str | None, percent: Decimal | None) -> str:
    if not code or percent is None:
        return ""
    return f"\n- **Cupom:** `{code}` • **{format_percent(percent)}**"


def _robux_text(
    robux: int,
    *,
    game_name: str | None = None,
    game_icon: str | None = None,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> str:
    quote = robux_quote(robux, discount_percent)
    game = f"{game_icon + ' ' if game_icon else ''}**{game_name}**\n\n" if game_name else ""
    return (
        f"## {ROBUX_EMOJI} Cálculo — {format_robux(robux)}\n"
        f"{game}"
        f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`\n"
        f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`\n"
        f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`"
        f"{_coupon_line(coupon_code, discount_percent)}"
    )


def _money_text(
    amount: Decimal,
    *,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> str:
    factor = Decimal("1")
    if discount_percent is not None:
        factor -= Decimal(discount_percent) / Decimal("100")
    gamepass_rate = ROBUX_PRICE_PER_100 * factor
    via_plus_rate = VIA_PLUS_PRICE_PER_100 * factor
    covering_rate = (ROBUX_PRICE_PER_100 / ROBLOX_NET_AFTER_FEE) * factor
    return (
        f"## {PIX_EMOJI} Cálculo — {format_brl(amount)}\n"
        f"- **{GIFT_EMOJI} Game Pass:** `{format_robux(robux_from_brl(amount, gamepass_rate))}`\n"
        f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_robux(robux_from_brl(amount, via_plus_rate))}`\n"
        f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** "
        f"`{format_robux(robux_from_brl(amount, covering_rate))}`"
        f"{_coupon_line(coupon_code, discount_percent)}"
    )


def _server_emoji(guild: discord.Guild, emoji_id: int | None) -> str:
    if emoji_id is None:
        return ""
    emoji = guild.get_emoji(emoji_id)
    return f" {emoji}" if emoji is not None else ""


class AutomationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _context(
        self,
        message: discord.Message,
    ) -> tuple[AIConfig | None, list[Product]]:
        if message.guild is None:
            return None, []
        async with SessionLocal() as session:
            config = await session.scalar(
                select(AIConfig).where(AIConfig.guild_id == message.guild.id)
            )
            products = list(
                (
                    await session.scalars(
                        select(Product).where(
                            Product.guild_id == message.guild.id,
                            Product.active.is_(True),
                        )
                    )
                ).all()
            )
        return config, products

    async def _reply(self, message: discord.Message, body: str) -> None:
        await message.reply(
            view=AIReplyView(body),
            mention_author=True,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    async def _respond_calculation_fallback(
        self,
        message: discord.Message,
        *,
        products: list[Product],
        config: AIConfig,
    ) -> bool:
        request = parse_calculation_message(message.content)
        if request is None:
            return False
        coupon_code, discount_percent, coupon_error = await _coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await self._reply(message, coupon_error)
            return True
        if request.kind is CalculationKind.ROBUX:
            amount = int(request.amount)
            stock = _robux_stock(products)
            if stock is not None and stock < amount:
                channel = _channel_mention(
                    config.suggestions_channel_id,
                    fallback="o canal de sugestões da loja",
                )
                await self._reply(
                    message,
                    f"Não temos **{format_robux(amount)}** disponíveis no estoque agora. "
                    f"Você pode pedir disponibilidade em {channel}.",
                )
                return True
            await self._reply(
                message,
                _robux_text(
                    amount,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                ),
            )
            return True
        await self._reply(
            message,
            _money_text(
                Decimal(request.amount),
                coupon_code=coupon_code,
                discount_percent=discount_percent,
            ),
        )
        return True

    async def _handle_intent(
        self,
        message: discord.Message,
        *,
        intent: AIIntent,
        products: list[Product],
        config: AIConfig,
    ) -> None:
        coupon_code, discount_percent, coupon_error = await _coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await self._reply(message, coupon_error)
            return

        if intent.intent == "robux_calculation":
            if not intent.robux:
                await self._reply(
                    message,
                    "Me diga **quantos Robux** você quer calcular e eu faço a conta.",
                )
                return
            stock = _robux_stock(products)
            if stock is not None and stock < intent.robux:
                channel = _channel_mention(
                    config.suggestions_channel_id,
                    fallback="o canal de sugestões da loja",
                )
                await self._reply(
                    message,
                    f"No momento não temos **{format_robux(intent.robux)}** disponíveis. "
                    f"Você pode solicitar disponibilidade em {channel}.",
                )
                return
            await self._reply(
                message,
                _robux_text(
                    intent.robux,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                ),
            )
            return

        if intent.intent == "money_calculation":
            if intent.amount_brl is None:
                await self._reply(message, "Me diga o **valor em reais** que você quer calcular.")
                return
            await self._reply(
                message,
                _money_text(
                    intent.amount_brl,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                ),
            )
            return

        if intent.intent == "gamepass_request":
            if not intent.game_name:
                await self._reply(
                    message,
                    "Qual é o **jogo** da Game Pass? Envie o nome do jogo e a quantidade de Robux.",
                )
                return
            if not _known_game(products, intent.game_name):
                channel = _channel_mention(
                    config.suggestions_channel_id,
                    fallback="o canal de sugestões da loja",
                )
                await self._reply(
                    message,
                    f"Esse jogo não está disponível na loja agora. "
                    f"Você pode pedir para adicionarmos em {channel}.",
                )
                return
            if not intent.robux:
                await self._reply(
                    message,
                    "Agora me diga **quantos Robux** custa a Game Pass para eu calcular.",
                )
                return
            await self._reply(
                message,
                _robux_text(
                    intent.robux,
                    game_name=intent.game_name,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                ),
            )
            return

        if intent.intent == "item_request":
            product = _find_product(
                products,
                product_name=intent.product_name,
                game_name=intent.game_name,
                product_types={"item"},
            )
            if product is None:
                channel = _channel_mention(
                    config.suggestions_channel_id,
                    fallback="o canal de sugestões da loja",
                )
                await self._reply(
                    message,
                    f"Esse item ou jogo não está disponível na loja agora. "
                    f"Você pode sugerir em {channel}.",
                )
                return
            quantity = intent.quantity or 1
            if product.stock_quantity is not None and product.stock_quantity < quantity:
                await self._reply(
                    message,
                    f"Temos apenas **{product.stock_quantity}** unidade(s) de **{product.name}** no estoque agora.",
                )
                return
            if product.price_credits is None:
                support = _channel_mention(
                    config.support_channel_id,
                    fallback="a equipe de suporte",
                )
                await self._reply(
                    message,
                    f"**{product.name}** está cadastrado, mas ainda não tem preço disponível. "
                    f"Fale com {support}.",
                )
                return
            unit = Decimal(product.price_credits)
            total = apply_discount(unit * quantity, discount_percent)
            game = f"**{product.game_name}**\n\n" if product.game_name else ""
            body = (
                f"## {PIX_EMOJI} Cálculo — {quantity}× {product.name}\n"
                f"{game}"
                f"- **{GIFT_EMOJI} {product.name}:** `{quantity} × {format_brl(unit)}`\n"
                f"- **{PIX_EMOJI} Valor final:** `{format_brl(total)}`"
                f"{_coupon_line(coupon_code, discount_percent)}"
            )
            await self._reply(message, body)
            return

        if intent.intent == "store_question":
            answer = intent.answer or "Não consigo confirmar isso automaticamente."
            if "suporte" in normalize_text(answer) or "confirmar" in normalize_text(answer):
                support = _channel_mention(
                    config.support_channel_id,
                    fallback="a equipe de suporte",
                )
                answer = f"{answer}\n\nSe precisar confirmar, fale em {support}."
            answer += _server_emoji(message.guild, intent.emoji_id)
            await self._reply(message, answer)
            return

        answer = intent.answer or "Não entendi totalmente o pedido. Diga o jogo, produto e quantidade."
        await self._reply(message, answer)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        config, products = await self._context(message)
        if config is None or not config.enabled:
            return
        allowed = {int(channel_id) for channel_id in (config.allowed_channel_ids or [])}
        if message.channel.id not in allowed:
            return

        server_emojis = [(emoji.id, emoji.name) for emoji in message.guild.emojis]
        try:
            intent, _provider = await parse_store_request(
                message=message.content,
                products=products,
                provider_order=list(config.provider_order or []),
                server_emojis=server_emojis,
            )
        except AIUnavailable:
            handled = await self._respond_calculation_fallback(
                message,
                products=products,
                config=config,
            )
            if not handled:
                support = _channel_mention(
                    config.support_channel_id,
                    fallback="a equipe de suporte",
                )
                await self._reply(
                    message,
                    f"Não consegui interpretar essa pergunta automaticamente agora. "
                    f"Você pode falar com {support}.",
                )
            return

        await self._handle_intent(
            message,
            intent=intent,
            products=products,
            config=config,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationCog(bot))
