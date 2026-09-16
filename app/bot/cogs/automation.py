import re
import time
from decimal import Decimal

import discord
from discord.ext import commands
from sqlalchemy import select

from app.bot.views.faq_links import AutoReplyLinkView
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
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
from app.services.configs import get_or_create_guild_config
from app.services.faq import find_auto_reply
from app.services.faq_buttons import list_auto_reply_buttons
from app.services.store_panel import get_coupon_by_code

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
GIFT_EMOJI = "<a:gift_Nextbuy:1549633615032221788>"

_GAMEPASS_WORD = re.compile(r"\bgame\s*pass\b|\bgamepass\b", re.IGNORECASE)
_INTEGER = re.compile(r"(?<!\d)(\d{1,7})(?!\d)")


def _coupon_suffix(code: str | None, percent: Decimal | None) -> str:
    if not code or percent is None:
        return ""
    return f"\n- **Cupom:** `\"{code}\" • {percent:.2f}%`"


def _game_line(game_name: str | None, icon: str | None) -> str:
    if not game_name:
        return ""
    prefix = f"{icon} " if icon else ""
    return f"{prefix}**{game_name}**\n"


def _robux_embed(
    robux: int,
    *,
    game_name: str | None = None,
    game_icon: str | None = None,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> discord.Embed:
    quote = robux_quote(robux, discount_percent)
    description = (
        _game_line(game_name, game_icon)
        + f"- **{GIFT_EMOJI} Game Pass:** `{format_brl(quote.gamepass_brl)}`\n"
        + f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_brl(quote.via_plus_brl)}`\n"
        + f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_brl(quote.covering_fee_brl)}`"
        + _coupon_suffix(coupon_code, discount_percent)
    )
    return discord.Embed(
        title=f"{ROBUX_EMOJI} Cálculo — {format_robux(robux)}",
        description=description,
        color=discord.Color.from_rgb(43, 45, 49),
    )


def _money_embed(
    amount: Decimal,
    *,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> discord.Embed:
    factor = Decimal("1")
    if discount_percent is not None:
        factor -= Decimal(discount_percent) / Decimal("100")
    gamepass_rate = ROBUX_PRICE_PER_100 * factor
    via_plus_rate = VIA_PLUS_PRICE_PER_100 * factor
    covering_rate = (ROBUX_PRICE_PER_100 / ROBLOX_NET_AFTER_FEE) * factor
    gamepass_robux = robux_from_brl(amount, gamepass_rate)
    via_plus_robux = robux_from_brl(amount, via_plus_rate)
    covering_robux = robux_from_brl(amount, covering_rate)
    description = (
        f"- **{GIFT_EMOJI} Game Pass:** `{format_robux(gamepass_robux)}`\n"
        f"- **{ROBUX_EMOJI} Robux Via Plus:** `{format_robux(via_plus_robux)}`\n"
        f"- **{ROBUX_EMOJI} Robux cobrindo a taxa:** `{format_robux(covering_robux)}`"
        + _coupon_suffix(coupon_code, discount_percent)
    )
    return discord.Embed(
        title=f"{PIX_EMOJI} Cálculo — {format_brl(amount)}",
        description=description,
        color=discord.Color.from_rgb(43, 45, 49),
    )


def _quantity_from_item_message(message: str) -> int:
    clean = strip_coupon(message)
    matches = [int(item) for item in _INTEGER.findall(clean)]
    for value in matches:
        if 1 <= value <= 100_000:
            return value
    return 1


def _product_match(text: str, products: list[Product]) -> list[Product]:
    normalized = normalize_text(text)
    matches: list[Product] = []
    for product in products:
        name = normalize_text(product.name)
        if name and name in normalized:
            matches.append(product)
    return matches


def _game_names(products: list[Product]) -> dict[str, str]:
    result: dict[str, str] = {}
    for product in products:
        if product.game_name:
            result.setdefault(normalize_text(product.game_name), product.game_name)
    return result


def _mentioned_games(text: str, products: list[Product], icons: dict[str, str]) -> list[str]:
    normalized = normalize_text(text)
    known = _game_names(products)
    for key in icons:
        known.setdefault(normalize_text(key), key)
    return [display for key, display in known.items() if key and key in normalized]


class AutomationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._faq_cooldowns: dict[tuple[int, int, int], float] = {}

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

    async def _handle_catalog_calculator(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        async with SessionLocal() as session:
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
            panel = await session.scalar(
                select(StorePanelConfig).where(StorePanelConfig.guild_id == message.guild.id)
            )
        icons = dict(panel.game_icons or {}) if panel is not None else {}
        text = strip_coupon(message.content)
        normalized = normalize_text(text)
        item_products = [product for product in products if product.product_type == "item"]
        matched_items = _product_match(text, item_products)
        gamepass_requested = _GAMEPASS_WORD.search(text) is not None

        if gamepass_requested and matched_items:
            await message.channel.send(
                "Envie **Game Pass** e **item** em mensagens separadas para eu calcular sem conflito."
            )
            return True
        if len(matched_items) > 1:
            await message.channel.send(
                "Encontrei mais de um item na mesma mensagem. Envie cada item separadamente."
            )
            return True

        games = _mentioned_games(text, products, icons)
        if len(games) > 1:
            await message.channel.send(
                "Encontrei mais de um jogo na mesma mensagem. Envie cada cálculo separadamente."
            )
            return True

        coupon_code, discount_percent, coupon_error = await self._coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await message.channel.send(coupon_error)
            return True

        if gamepass_requested:
            if not games:
                await message.channel.send(
                    "Para calcular uma **Game Pass**, altere a mensagem e inclua o **nome do jogo**."
                )
                return True
            request = parse_calculation_message(message.content)
            if request is None or request.kind is not CalculationKind.ROBUX:
                await message.channel.send(
                    "Inclua também a **quantidade de Robux** da Game Pass na mesma mensagem."
                )
                return True
            game = games[0]
            icon = icons.get(normalize_text(game))
            await message.channel.send(
                embed=_robux_embed(
                    int(request.amount),
                    game_name=game,
                    game_icon=icon,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                )
            )
            return True

        if matched_items:
            product = matched_items[0]
            if not product.game_name or normalize_text(product.game_name) not in normalized:
                await message.channel.send(
                    "Alterе a mensagem e inclua **o nome do jogo e o nome do item** para eu reconhecer."
                )
                return True
            if product.price_credits is None:
                await message.channel.send("Esse item ainda não possui preço em reais cadastrado.")
                return True
            quantity = _quantity_from_item_message(message.content)
            unit = Decimal(product.price_credits)
            total = apply_discount(unit * quantity, discount_percent)
            game_icon = icons.get(normalize_text(product.game_name))
            description = (
                _game_line(product.game_name, game_icon)
                + f"- **{GIFT_EMOJI} {product.name}:** `{quantity} × {format_brl(unit)}`\n"
                + f"- **{PIX_EMOJI} Valor final:** `{format_brl(total)}`"
                + _coupon_suffix(coupon_code, discount_percent)
            )
            embed = discord.Embed(
                title=f"{PIX_EMOJI} Cálculo — {quantity}× {product.name}",
                description=description,
                color=discord.Color.from_rgb(43, 45, 49),
            )
            await message.channel.send(embed=embed)
            return True

        if "item" in normalized and not matched_items:
            await message.channel.send(
                "Para calcular um item, altere a mensagem e inclua **o nome do jogo e o nome do item**."
            )
            return True
        return False

    async def _handle_calculator(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        if await self._handle_catalog_calculator(message):
            return True

        request = parse_calculation_message(message.content)
        if request is None:
            return False
        coupon_code, discount_percent, coupon_error = await self._coupon(
            guild_id=message.guild.id,
            content=message.content,
        )
        if coupon_error:
            await message.channel.send(coupon_error)
            return True

        if request.kind is CalculationKind.ROBUX:
            embed = _robux_embed(
                int(request.amount),
                coupon_code=coupon_code,
                discount_percent=discount_percent,
            )
        else:
            embed = _money_embed(
                Decimal(request.amount),
                coupon_code=coupon_code,
                discount_percent=discount_percent,
            )
        await message.channel.send(embed=embed)
        return True

    async def _handle_faq(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        async with SessionLocal() as session:
            reply = await find_auto_reply(
                session,
                guild_id=message.guild.id,
                message=message.content,
            )
            if reply is None:
                return
            buttons = await list_auto_reply_buttons(session, auto_reply_id=reply.id)

        key = (message.guild.id, message.author.id, reply.id)
        now = time.monotonic()
        last = self._faq_cooldowns.get(key, 0.0)
        if now - last < reply.cooldown_seconds:
            return
        self._faq_cooldowns[key] = now

        title = f"{reply.emoji} {reply.title}" if reply.emoji else reply.title
        embed = discord.Embed(title=title, description=reply.content)
        view = AutoReplyLinkView(buttons) if buttons else None
        await message.channel.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        async with SessionLocal() as session:
            config = await get_or_create_guild_config(session, message.guild.id)
            calculator_channel_id = config.calculator_channel_id
            faq_channel_id = config.faq_channel_id

        if calculator_channel_id and message.channel.id == calculator_channel_id:
            handled = await self._handle_calculator(message)
            if handled:
                return

        if faq_channel_id and message.channel.id == faq_channel_id:
            await self._handle_faq(message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationCog(bot))
