import discord
from sqlalchemy import select

from app.bot.components_v2 import CardLayout
from app.bot.workflows import tickets
from app.db.models import Order
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
from app.services.delivery_settings import (
    ARROW_EMOJI,
    BOX_EMOJI,
    MEMBER_EMOJI,
    VERIFY_EMOJI,
    render_delivery,
)

_GAME_PRODUCT_TYPES = {"item", "gamepass", "game_pass", "gift"}


def _product_type(item) -> str:
    return str((item.metadata_json or {}).get("product_type") or "").strip().lower().replace(" ", "_")


def _delivery_image(items) -> str | None:
    """Compatibilidade para testes: retorna a foto congelada de item/Game Pass quando existir."""
    for item in items:
        if _product_type(item) in _GAME_PRODUCT_TYPES and item.image_url_snapshot:
            return item.image_url_snapshot
    return None


def _delivery_item_lines(items) -> list[str]:
    """Formato padrão sem seta; o runtime real usa o template configurado no admin."""
    if not items:
        return ["Pedido sem itens cadastrados"]
    lines: list[str] = []
    for item in items:
        quantity = int(item.quantity or 1)
        suffix = f" × `{quantity}`" if quantity > 1 else ""
        game_name = str((item.metadata_json or {}).get("game_name") or "").strip()
        game = f" • {game_name}" if game_name else ""
        lines.append(f"**{item.name_snapshot}**{game}{suffix}")
    return lines


async def _delivery_config(guild_id: int) -> dict[str, object] | None:
    async with SessionLocal() as session:
        panel = await session.scalar(
            select(StorePanelConfig).where(StorePanelConfig.guild_id == guild_id)
        )
        return dict(panel.delivery_config or {}) if panel is not None else None


async def publish_delivery(guild: discord.Guild, *, order_id) -> None:
    loaded = await tickets._load_order(order_id)
    if loaded is None:
        return
    order, user, items, config = loaded
    if config is None or not config.deliveries_channel_id or order.delivery_message_id:
        return

    channel = guild.get_channel(config.deliveries_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    member = guild.get_member(user.discord_user_id)
    mention = member.mention if member else f"<@{user.discord_user_id}>"
    raw_config = await _delivery_config(guild.id)
    title, lines, footer, accent, show_image = render_delivery(
        raw_config,
        order_id=order.id,
        client_mention=mention,
        items=items,
    )
    image_url = (
        await tickets.resolve_order_image(items, game_products_only=True)
        if show_image
        else None
    )

    view = CardLayout(
        title=title,
        lines=lines,
        footer=footer,
        image_url=image_url,
        accent_colour=accent,
        timeout=None,
    )
    message = await channel.send(
        view=view,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )

    async with SessionLocal() as session, session.begin():
        db_order = await session.get(Order, order.id)
        if db_order is not None:
            db_order.delivery_message_id = message.id


async def setup(bot) -> None:
    # TicketStaffView resolve esse nome no módulo em tempo de execução; substituir aqui
    # mantém os views persistentes existentes compatíveis sem duplicar o fluxo de tickets.
    tickets.publish_delivery = publish_delivery


__all__ = [
    "ARROW_EMOJI",
    "BOX_EMOJI",
    "MEMBER_EMOJI",
    "VERIFY_EMOJI",
    "_delivery_image",
    "_delivery_item_lines",
    "publish_delivery",
]
