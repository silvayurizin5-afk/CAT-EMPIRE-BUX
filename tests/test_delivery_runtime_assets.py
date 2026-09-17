from decimal import Decimal

from app.bot.cogs.delivery_runtime import (
    _emoji_image_url,
    _inline_emoji_from_asset,
    _normalize_emoji,
    _regular_image_url,
    _render_delivery,
    _render_delivery_sections,
)
from app.db.models import OrderItem
from app.services.delivery_settings import BOX_EMOJI


def test_custom_product_and_game_emojis_are_used_in_default_delivery() -> None:
    game_emoji = "<:bloxfruits:111111111111111111>"
    product_emoji = "<:notifier:222222222222222222>"
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        metadata_json={
            "product_type": "gamepass",
            "game_name": "BLOX FRUITS",
            "game_emoji": game_emoji,
            "product_emoji": product_emoji,
        },
    )

    title, lines, _, _, _ = _render_delivery(
        None,
        order_id="12345678-0000-0000-0000-000000000000",
        client_mention="<@1>",
        items=[item],
    )
    text = "\n".join([title, *lines])

    assert f"> **{game_emoji} BLOX FRUITS**" in text
    assert f"**• {product_emoji} Notifier 1× · R$ 22,00**" in text
    assert f"> **{BOX_EMOJI} BLOX FRUITS**" not in text


def test_normal_game_image_becomes_small_thumbnail_without_box() -> None:
    image_url = "https://example.com/blox-fruits.png"
    product_emoji = "<:notifier:222222222222222222>"
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        metadata_json={
            "product_type": "gamepass",
            "game_name": "BLOX FRUITS",
            "game_thumbnail_url": image_url,
            "product_emoji": product_emoji,
        },
    )

    _, _, blocks, _, _, _ = _render_delivery_sections(
        None,
        order_id="12345678-0000-0000-0000-000000000000",
        client_mention="<@1>",
        items=[item],
    )
    text, thumbnail = blocks[0]

    assert thumbnail == image_url
    assert "> **BLOX FRUITS**" in text
    assert f"> **{BOX_EMOJI} BLOX FRUITS**" not in text
    assert f"**• {product_emoji} Notifier 1× · R$ 22,00**" in text


def test_custom_emoji_can_be_used_as_image_fallback() -> None:
    assert _emoji_image_url("<:blox:123456789012345678>") == (
        "https://cdn.discordapp.com/emojis/123456789012345678.png"
        "?size=512&quality=lossless"
    )
    assert _emoji_image_url("<a:blox:123456789012345678>") == (
        "https://cdn.discordapp.com/emojis/123456789012345678.gif"
        "?size=512&quality=lossless"
    )


def test_discord_emoji_cdn_url_becomes_real_emoji_mention() -> None:
    static_url = "https://cdn.discordapp.com/emojis/123456789012345678.png"
    animated_url = "https://cdn.discordapp.com/emojis/123456789012345678.gif"
    assert _normalize_emoji(static_url) == "<:emoji:123456789012345678>"
    assert _normalize_emoji(animated_url) == "<a:emoji:123456789012345678>"


def test_discord_image_asset_can_be_rendered_inline_next_to_game_name() -> None:
    image_url = "https://cdn.discordapp.com/emojis/123456789012345678.webp?size=2048"
    assert _inline_emoji_from_asset(image_url) == "<:emoji:123456789012345678>"


def test_normal_http_image_is_not_rendered_as_fake_inline_emoji() -> None:
    assert _inline_emoji_from_asset("https://example.com/blox-fruits.png") == ""


def test_regular_image_excludes_discord_emoji_urls() -> None:
    normal = "https://example.com/blox-fruits.png"
    emoji = "https://cdn.discordapp.com/emojis/123456789012345678.webp?size=2048"
    assert _regular_image_url(normal) == normal
    assert _regular_image_url(emoji) is None


def test_http_game_icon_is_valid_image_fallback() -> None:
    url = "https://example.com/blox-fruits.png"
    assert _emoji_image_url(url) == url
