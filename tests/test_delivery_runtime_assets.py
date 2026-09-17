from decimal import Decimal

from app.bot.cogs.delivery_runtime import _emoji_image_url, _render_delivery
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


def test_custom_emoji_can_be_used_as_image_fallback() -> None:
    assert _emoji_image_url("<:blox:123456789012345678>") == (
        "https://cdn.discordapp.com/emojis/123456789012345678.png"
        "?size=512&quality=lossless"
    )
    assert _emoji_image_url("<a:blox:123456789012345678>") == (
        "https://cdn.discordapp.com/emojis/123456789012345678.gif"
        "?size=512&quality=lossless"
    )


def test_http_game_icon_is_valid_image_fallback() -> None:
    url = "https://example.com/blox-fruits.png"
    assert _emoji_image_url(url) == url
