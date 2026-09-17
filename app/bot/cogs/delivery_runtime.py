import re

import discord
from sqlalchemy import select

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
    "{discount_line}"
)
_THUMBNAIL_PRODUCT_TEMPLATE = (
    "> **{game_or_product}**\n"
    "**• {product_emoji} {product} {quantity}× · {line_total}{robux_part}**\n"
    "{discount_line}"
)
_PRODUCTS_MARKER = "\uFFF0NEXTBUY_PRODUCTS\uFFF1"


def _delivery_image(items) -> str | None:
    """Retorna a primeira imagem congelada do pedido, para compatibilidade/testes."""
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


def _inline_emoji_from_asset(value: str | None) -> str:
    """Converte somente assets que realmente podem aparecer inline no texto do Discord."""
    normalized = _normalize_emoji(value)
    return normalized if _CUSTOM_EMOJI_RE.fullmatch(normalized) else ""


def _regular_image_url(value: str | None) -> str | None:
    """Retorna apenas imagens HTTP comuns; URLs de emoji do Discord viram emoji inline."""
    raw = str(value or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return None
    if _DISCORD_EMOJI_URL_RE.fullmatch(raw):
        return None
    return raw


async def _delivery_context(
    guild_id: int,
    items,
) -> dict[str, object] | None:
    """Enriquece a entrega com o visual do jogo e do produto sem criar banner.

    Emoji customizado fica inline. Imagem HTTP comum vira Thumbnail pequena no bloco
    daquele produto. Nenhum asset de produto/jogo é enviado como MediaGallery.
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

    raw_config = dict(panel.delivery_config or {}) if panel is not None else None
    config = effective_delivery_config(raw_config)
    use_asset_icon = bool(config["show_image"])
    by_id = {product.id: product for product in products}
    game_icons = dict(panel.game_icons or {}) if panel is not None else {}

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

        raw_product_emoji = str(metadata.get("product_emoji") or "").strip()
        product_emoji = _inline_emoji_from_asset(raw_product_emoji)
        if product_emoji:
            metadata["product_emoji"] = product_emoji
        elif raw_product_emoji:
            metadata.pop("product_emoji", None)

        game_name = str(metadata.get("game_name") or "").strip()
        if game_name:
            configured_game_icon = str(
                game_icons.get(normalize_text(game_name)) or ""
            ).strip()
            raw_game_asset = str(metadata.get("game_emoji") or configured_game_icon).strip()
            game_emoji = _inline_emoji_from_asset(raw_game_asset)

            if game_emoji:
                metadata["game_emoji"] = game_emoji
                metadata.pop("game_thumbnail_url", None)
            else:
                metadata.pop("game_emoji", None)
                if use_asset_icon:
                    thumbnail = _regular_image_url(raw_game_asset)
                    if thumbnail is None:
                        thumbnail = _regular_image_url(
                            str(metadata.get("product_image_url") or item.image_url_snapshot or "")
                        )
                    if thumbnail:
                        metadata["game_thumbnail_url"] = thumbnail

                if "game_thumbnail_url" not in metadata and use_asset_icon:
                    # Se não houver imagem comum, um emoji de imagem do produto pode ser usado
                    # inline; nunca é transformado em banner.
                    product_image_emoji = _inline_emoji_from_asset(
                        str(metadata.get("product_image_url") or item.image_url_snapshot or "")
                    )
                    if product_image_emoji:
                        metadata["game_emoji"] = product_image_emoji

        item.metadata_json = metadata

    return raw_config


def _format_template(template: str, values: dict[str, object]) -> str:
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Template de entrega inválido: {exc}") from exc


def _common_values(config: dict[str, object], order_id, client_mention: str) -> dict[str, object]:
    return {
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


def _render_product_blocks(
    config: dict[str, object],
    common: dict[str, object],
    items,
) -> list[tuple[str, str | None]]:
    configured_template = str(config["product_template"])
    default_template = configured_template in _DEFAULT_PRODUCT_TEMPLATES
    blocks: list[tuple[str, str | None]] = []

    for item in items:
        metadata = dict(item.metadata_json or {})
        thumbnail_url = _regular_image_url(str(metadata.get("game_thumbnail_url") or ""))
        values = product_line_values(item, config)

        if default_template:
            template = _THUMBNAIL_PRODUCT_TEMPLATE if thumbnail_url else _DYNAMIC_PRODUCT_TEMPLATE
        else:
            template = configured_template
            if thumbnail_url and not metadata.get("game_emoji"):
                values["game_emoji"] = ""

        rendered = _format_template(template, {**common, **values}).strip()
        if rendered:
            blocks.append((rendered, thumbnail_url))

    if not blocks:
        blocks.append(("Pedido sem itens cadastrados", None))
    return blocks


def _render_delivery_sections(
    raw_config: dict[str, object] | None,
    *,
    order_id,
    client_mention: str,
    items,
) -> tuple[str, list[str], list[tuple[str, str | None]], list[str], str, int]:
    config = effective_delivery_config(raw_config)
    common = _common_values(config, order_id, client_mention)
    blocks = _render_product_blocks(config, common, items)

    title = _format_template(str(config["title_template"]), common).strip()
    body_with_marker = _format_template(
        str(config["body_template"]),
        {**common, "products": _PRODUCTS_MARKER},
    ).strip()
    footer = _format_template(str(config["footer_template"]), common).strip()

    if _PRODUCTS_MARKER in body_with_marker:
        before, after = body_with_marker.split(_PRODUCTS_MARKER, 1)
        before_lines = before.strip().splitlines() if before.strip() else []
        after_lines = after.strip().splitlines() if after.strip() else []
    else:
        before_lines = body_with_marker.splitlines() if body_with_marker else []
        after_lines = []

    return title, before_lines, blocks, after_lines, footer, parse_hex_color(config["accent_color"])


def _render_delivery(
    raw_config: dict[str, object] | None,
    *,
    order_id,
    client_mention: str,
    items,
) -> tuple[str, list[str], str, int, bool]:
    title, before, blocks, after, footer, accent = _render_delivery_sections(
        raw_config,
        order_id=order_id,
        client_mention=client_mention,
        items=items,
    )
    lines = list(before)
    for block, _ in blocks:
        lines.extend(block.splitlines())
    lines.extend(after)
    config = effective_delivery_config(raw_config)
    return title, lines, footer, accent, bool(config["show_image"])


class DeliveryPublicLayout(discord.ui.LayoutView):
    """Mensagem pública de entrega sem banner; imagens ficam como Thumbnail do produto."""

    def __init__(
        self,
        *,
        title: str,
        before_lines: list[str],
        product_blocks: list[tuple[str, str | None]],
        after_lines: list[str],
        footer: str,
        accent: int,
    ) -> None:
        super().__init__(timeout=None)
        children: list[discord.ui.Item] = []

        header = "\n".join(([title] if title else []) + before_lines).strip()
        if header:
            children.append(discord.ui.TextDisplay(header))

        for block, thumbnail_url in product_blocks:
            if thumbnail_url:
                children.append(
                    discord.ui.Section(
                        discord.ui.TextDisplay(block),
                        accessory=discord.ui.Thumbnail(thumbnail_url),
                    )
                )
            else:
                children.append(discord.ui.TextDisplay(block))

        tail = "\n".join(after_lines).strip()
        if tail:
            children.append(discord.ui.TextDisplay(tail))

        if footer:
            children.append(discord.ui.Separator())
            children.append(discord.ui.TextDisplay(f"-# {footer}"))

        if not children:
            children.append(discord.ui.TextDisplay("\u200b"))

        self.container = discord.ui.Container(*children, accent_colour=accent)
        self.add_item(self.container)


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
    raw_config = await _delivery_context(guild.id, items)
    title, before, blocks, after, footer, accent = _render_delivery_sections(
        raw_config,
        order_id=order.id,
        client_mention=mention,
        items=items,
    )

    # Nunca enviar image_url/MediaGallery aqui. Imagens de jogo ficam somente como
    # Thumbnail pequena no Section do produto correspondente.
    message = await channel.send(
        view=DeliveryPublicLayout(
            title=title,
            before_lines=before,
            product_blocks=blocks,
            after_lines=after,
            footer=footer,
            accent=accent,
        ),
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
    "DeliveryPublicLayout",
    "MEMBER_EMOJI",
    "VERIFY_EMOJI",
    "_delivery_image",
    "_delivery_item_lines",
    "_emoji_image_url",
    "_inline_emoji_from_asset",
    "_normalize_emoji",
    "_regular_image_url",
    "_render_delivery",
    "_render_delivery_sections",
    "publish_delivery",
]
