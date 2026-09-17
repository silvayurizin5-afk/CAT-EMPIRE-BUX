from decimal import Decimal
from types import SimpleNamespace

from app.bot.cogs import automation_runtime as ai
from app.bot.cogs.delivery_runtime import (
    ARROW_EMOJI,
    _delivery_image,
    _delivery_item_lines,
)
from app.db.models import OrderItem, Product


def _products() -> list[Product]:
    return [
        Product(
            id=1,
            guild_id=1,
            name="Notifier",
            slug="notifier",
            product_type="gamepass",
            game_name="BLOX FRUITS",
            description="",
            price_credits=Decimal("22.00"),
            image_url="https://example.com/notifier.png",
            emoji=None,
            delivery_mode="manual",
            stock_quantity=None,
            active=True,
            sort_order=0,
            metadata_json={},
        ),
        Product(
            id=2,
            guild_id=1,
            name="Robux",
            slug="robux",
            product_type="robux",
            game_name=None,
            description="",
            price_credits=Decimal("2.90"),
            image_url=None,
            emoji=None,
            delivery_mode="manual",
            stock_quantity=None,
            active=True,
            sort_order=1,
            metadata_json={},
        ),
    ]


def _message(content: str):
    return SimpleNamespace(content=content)


def test_ai_lists_games_without_calling_product_unavailable() -> None:
    result = ai._quick_store_answer(_message("Quais jogos tem?"), _products(), {})
    assert result is not None
    assert result["intent"] == "store_question"
    assert "BLOX FRUITS" in str(result["reply"])


def test_ai_keeps_game_context_for_gamepass_follow_up() -> None:
    state = {"game_name": "BLOX FRUITS"}
    result = ai._quick_store_answer(_message("E quais gamepass"), _products(), state)
    assert result is not None
    assert "Notifier" in str(result["reply"])
    assert result["_context_product_name"] == "Notifier"


def test_ai_price_follow_up_uses_last_product() -> None:
    state = {"game_name": "BLOX FRUITS", "product_name": "Notifier"}
    result = ai._quick_store_answer(_message("Quanto custa"), _products(), state)
    assert result is not None
    reply = str(result["reply"])
    assert "A Game Pass" in reply
    assert "R$ 22,00" in reply


def test_ai_named_gamepass_uses_catalog_price() -> None:
    result = ai._quick_store_answer(
        _message("Quanto custa esse Notifier"),
        _products(),
        {},
    )
    assert result is not None
    assert "A Game Pass" in str(result["reply"])
    assert "R$ 22,00" in str(result["reply"])


def test_ai_generic_gamepass_quote_does_not_require_game() -> None:
    result = ai._quick_store_answer(
        _message("Quanto custa uma gamepass de 500 Robux"),
        _products(),
        {},
    )
    assert result is not None
    reply = str(result["reply"])
    assert "Uma Game Pass" in reply
    assert "500 Robux" in reply
    assert "R$ 14,50" in reply


def test_ai_recognizes_singular_game_name_variant() -> None:
    assert ai._match_game("Blox fruit", _products()) == "BLOX FRUITS"


def test_delivery_uses_game_product_snapshot_image() -> None:
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        image_url_snapshot="https://example.com/notifier.png",
        metadata_json={"product_type": "gamepass", "game_name": "BLOX FRUITS"},
    )
    assert _delivery_image([item]) == "https://example.com/notifier.png"
    line = _delivery_item_lines([item])[0]
    assert ARROW_EMOJI in line
    assert "Notifier" in line
    assert "BLOX FRUITS" in line
