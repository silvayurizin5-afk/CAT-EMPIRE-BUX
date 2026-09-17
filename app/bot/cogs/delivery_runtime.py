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


def _delivery_image(items) -> str | None:
    """Retorna a primeira imagem congelada do pedido, independente do tipo cadastrado."""
    for item in items:
        image = str(item.image_url_snapshot or "").strip()
        if image:
            return image
    return None


def _delivery_item_lines(items) -> list[str]:
    """Resumo simples usado apenas por testes/compatibilidade."""
    if not items:
        return ["Pedido sem itens cadastrados"]
    lines: list[str] = []
    for item in items:
        quantity = int(item.quantity or 1)
        game_name = str((item.metadata_json or {}).get("game_name") or "").strip()
        game = f" • {game_name}" if game_name else ""
        lines.append(f"**{item.name_snapshot}**{game} × `{quantity}`")
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

    image_url = await tickets.resolve_order_image(items) if show_image else None
    display_lines = ([title] if title else []) + lines
    view = CardLayout(
        title=None,
        lines=display_lines,
        footer=footer or None,
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
