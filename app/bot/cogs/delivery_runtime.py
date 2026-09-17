import discord

from app.bot.components_v2 import CardLayout
from app.bot.workflows import tickets
from app.db.models import Order
from app.db.session import SessionLocal

VERIFY_EMOJI = "<a:verify:1550043693510037546>"
ARROW_EMOJI = "<a:s_ASETA2_:1550044035522109511>"
MEMBER_EMOJI = "<:member:1550043925283344458>"
BOX_EMOJI = "<:CaixaStorm:1550043608952999996>"

_GAME_PRODUCT_TYPES = {"item", "gamepass", "game_pass", "gift"}


def _product_type(item) -> str:
    return str((item.metadata_json or {}).get("product_type") or "").strip().lower().replace(" ", "_")


def _delivery_image(items) -> str | None:
    """Itens/Game Pass usam a imagem salva no próprio pedido, vinda do painel da loja."""
    for item in items:
        if _product_type(item) in _GAME_PRODUCT_TYPES and item.image_url_snapshot:
            return item.image_url_snapshot
    return None


def _delivery_item_lines(items) -> list[str]:
    if not items:
        return [f"{ARROW_EMOJI} **Pedido sem itens cadastrados**"]

    lines: list[str] = []
    for item in items:
        quantity = int(item.quantity or 1)
        suffix = f" × `{quantity}`" if quantity > 1 else ""
        game_name = str((item.metadata_json or {}).get("game_name") or "").strip()
        game = f" • {game_name}" if game_name else ""
        lines.append(f"{ARROW_EMOJI} **{item.name_snapshot}**{game}{suffix}")
    return lines


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
    image_url = _delivery_image(items)

    view = CardLayout(
        title=f"{VERIFY_EMOJI} {ARROW_EMOJI} Entrega Realizada",
        lines=[
            f"{MEMBER_EMOJI} **Cliente:** {mention}",
            f"{VERIFY_EMOJI} **Status:** Pedido entregue com sucesso",
            f"### {BOX_EMOJI} {ARROW_EMOJI} Produto(s):",
            *_delivery_item_lines(items),
        ],
        footer=f"NEXTBUY • Pedido #{str(order.id)[:8]}",
        image_url=image_url,
        accent_colour=discord.Colour.green(),
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
