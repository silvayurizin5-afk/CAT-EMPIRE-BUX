import re

import discord
from sqlalchemy import select

from app.bot.components_v2 import CardLayout
from app.bot.workflows import tickets
from app.db.models import Order, Product
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
from app.services.calculator import normalize_text
from app.services.delivery_settings import (
    ARROW_EMOJI,
    BOX_EMOJI,
    MEMBER_EMOJI,
    VERIFY_EMOJI,
    effective_delivery_config,
    parse_hex_color,
    product_line_values,
)

_CUSTOM_EMOJI_RE = re.compile(r"<(?P<animated>a?):[^:>]+:(?P<id>\d+)>")
_DISCORD_EMOJI_URL_RE = re.compile(
    r"^https?://(?:cdn|media)\.discordapp\.(?:com|net)/emojis/"
    r"(?P<id>\d+)\.(?P<ext>gif|png|webp)(?:\?.*)?$",
    re.IGNORECASE,
)
_DEFAULT_PRODUCT_TEMPLATES = {
    "**{product}**{game_part}{quantity_part}",
    (
        "> **{game_emoji} {game_or_product}**\n"
        "**• {product_emoji} {product} {quantity}× · {line_total}{robux_part}**\n"
        "{discount_line}"
    ),
    (
        "> **{box} {game_or_product}**\n"
        "**• {box} {product} {quantity}× · {line_total}{robux_part}**\n"
        "{discount_line}"
    ),
}
_DYNAMIC_PRODUCT_TEMPLATE = (
    "> **{game_emoji} {game_or_product}**\n"
    "**• {product_emoji} {product} {quantity}× · {line_total}{robux_part}**\n"
    "{discount_line}\n"
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


def _normalize_emoji(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _CUSTOM_EMOJI_RE.fullmatch(raw):
        return raw
    match = _DISCORD_EMOJI_URL_RE.fullmatch(raw)
    if match is None:
        return raw
    prefix = "a" if match.group("ext").lower() == "gif" else ""
    return f"<{prefix}:emoji:{match.group('id')}>"


def _emoji_image_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if raw.lower().startswith(("http://", "https://")):
        return raw
    match = _CUSTOM_EMOJI_RE.fullmatch(raw)
    if match is None:
        return None
    extension = "gif" if match.group("animated") else "png"
    return (
        f"https://cdn.discordapp.com/emojis/{match.group('id')}.{extension}"
        "?size=512&quality=lossless"
    )


async def _delivery_context(
    guild_id: int,
    items,
) -> tuple[dict[str, object] | None, str | None]:
    """Enriquece pedidos antigos com o visual atual do produto/jogo.

    Pedidos novos já guardam emoji e imagem no snapshot. Para pedidos anteriores a esse snapshot,
    buscamos o Product atual e o ícone do jogo salvo no painel da loja.
    """
    product_ids = [int(item.product_id) for item in items if item.product_id]
    async with SessionLocal() as session:
        panel = await session.scalar(
            select(StorePanelConfig).where(StorePanelConfig.guild_id == guild_id)
        )
        products = []
        if product_ids:
            products = list(
                (
                    await session.scalars(
                        select(Product).where(
                            Product.guild_id == guild_id,
                            Product.id.in_(product_ids),
                        )
                    )
                ).all()
            )

    by_id = {product.id: product for product in products}
    game_icons = dict(panel.game_icons or {}) if panel is not None else {}
    fallback_image: str | None = None

    for item in items:
        metadata = dict(item.metadata_json or {})
        product = by_id.get(int(item.product_id)) if item.product_id else None

        if product is not None:
            if not metadata.get("product_emoji") and product.emoji:
                metadata["product_emoji"] = product.emoji
            if not metadata.get("product_image_url") and product.image_url:
                metadata["product_image_url"] = product.image_url
            if not metadata.get("game_name") and product.game_name:
                metadata["game_name"] = product.game_name
            if not metadata.get("product_type") and product.product_type:
                metadata["product_type"] = product.product_type
            if not item.image_url_snapshot and product.image_url:
                item.image_url_snapshot = product.image_url

        product_emoji = _normalize_emoji(str(metadata.get("product_emoji") or ""))
        if product_emoji:
            metadata["product_emoji"] = product_emoji
            fallback_image = fallback_image or _emoji_image_url(product_emoji)

        game_name = str(metadata.get("game_name") or "").strip()
        if game_name:
            configured_game_icon = str(
                game_icons.get(normalize_text(game_name)) or ""
            ).strip()
            game_emoji = _normalize_emoji(
                str(metadata.get("game_emoji") or configured_game_icon)
            )
            if game_emoji:
                metadata["game_emoji"] = game_emoji
                fallback_image = fallback_image or _emoji_image_url(
                    configured_game_icon or game_emoji
                )

        product_image = str(metadata.get("product_image_url") or "").strip()
        if product_image:
            fallback_image = product_image

        item.metadata_json = metadata

    config = dict(panel.delivery_config or {}) if panel is not None else None
    return config, fallback_image


def _format_template(template: str, values: dict[str, object]) -> str:
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Template de entrega inválido: {exc}") from exc


def _render_delivery(
    raw_config: dict[str, object] | None,
    *,
    order_id,
    client_mention: str,
    items,
) -> tuple[str, list[str], str, int, bool]:
    config = effective_delivery_config(raw_config)
    common = {
        "delivery": str(config["delivery_emoji"]),
        "arrow": str(config["arrow_emoji"]),
        "user": str(config["user_emoji"]),
        "separator": str(config["separator_emoji"]),
        "verified": str(config["verified_emoji"]),
        "order_icon": str(config["order_emoji"]),
        "game_emoji": str(config["game_emoji"]),
        "product_emoji": str(config["product_emoji"]),
        "discount_emoji": str(config["discount_emoji"]),
        "verify": str(config["verify_emoji"]),
        "member": str(config["member_emoji"]),
        "box": str(config["box_emoji"]),
        "client": client_mention,
        "order": str(order_id),
        "order_short": str(order_id)[:8],
    }

    configured_template = str(config["product_template"])
    product_template = (
        _DYNAMIC_PRODUCT_TEMPLATE
        if configured_template in _DEFAULT_PRODUCT_TEMPLATES
        else configured_template
    )
    rendered_products: list[str] = []
    for item in items:
        rendered = _format_template(
            product_template,
            {**common, **product_line_values(item, config)},
        ).strip()
        if rendered:
            rendered_products.append(rendered)
    if not rendered_products:
        rendered_products.append("Pedido sem itens cadastrados")

    values = {**common, "products": "\n\n".join(rendered_products)}
    title = _format_template(str(config["title_template"]), values).strip()
    body = _format_template(str(config["body_template"]), values).strip()
    footer = _format_template(str(config["footer_template"]), values).strip()
    lines = body.splitlines() if body else []
    return (
        title,
        lines,
        footer,
        parse_hex_color(config["accent_color"]),
        bool(config["show_image"]),
    )


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
    raw_config, fallback_image = await _delivery_context(guild.id, items)
    title, lines, footer, accent, show_image = _render_delivery(
        raw_config,
        order_id=order.id,
        client_mention=mention,
        items=items,
    )

    image_url = None
    if show_image:
        image_url = await tickets.resolve_order_image(items)
        image_url = image_url or fallback_image

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
    tickets.publish_delivery = publish_delivery


__all__ = [
    "ARROW_EMOJI",
    "BOX_EMOJI",
    "MEMBER_EMOJI",
    "VERIFY_EMOJI",
    "_delivery_image",
    "_delivery_item_lines",
    "_emoji_image_url",
    "_normalize_emoji",
    "_render_delivery",
    "publish_delivery",
]
