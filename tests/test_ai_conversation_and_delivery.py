from decimal import Decimal
from types import SimpleNamespace

from app.bot.cogs import automation_refinement as refine
from app.bot.cogs import automation_runtime as ai
from app.bot.cogs.delivery_runtime import (
    ARROW_EMOJI,
    _delivery_image,
    _delivery_item_lines,
)
from app.db.models import OrderItem, Product
from app.services.delivery_settings import render_delivery


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
            stock_quantity=50,
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


def test_live_stock_variants_never_depend_on_llm() -> None:
    for text in ("Quais o estoque", "Me informe o estoque atual", "estoque da loja agora"):
        result = refine._quick_store_answer(_message(text), _products(), {})
        assert result is not None
        assert result["intent"] == "store_question"
        reply = str(result["reply"])
        assert "Notifier" in reply
        assert "Robux" in reply
        assert "50 em estoque" in reply


def test_live_stock_of_named_product_uses_database_snapshot() -> None:
    result = refine._quick_store_answer(
        _message("Qual o estoque do Notifier?"),
        _products(),
        {},
    )
    assert result is not None
    assert "Notifier" in str(result["reply"])
    assert "estoque ilimitado" in str(result["reply"])


def test_generic_price_question_lists_current_products() -> None:
    result = refine._quick_store_answer(
        _message("Quanto custa qualquer produto"),
        _products(),
        {},
    )
    assert result is not None
    reply = str(result["reply"])
    assert "R$ 22,00" in reply
    assert "R$ 2,90" in reply


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
    assert ARROW_EMOJI not in line
    assert "Notifier" in line
    assert "BLOX FRUITS" in line


def test_delivery_default_has_no_arrow_and_is_template_driven() -> None:
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=2,
        metadata_json={"product_type": "gamepass", "game_name": "BLOX FRUITS"},
    )
    title, lines, footer, accent, show_image = render_delivery(
        None,
        order_id="12345678-0000-0000-0000-000000000000",
        client_mention="@Cliente",
        items=[item],
    )
    text = "\n".join([title, *lines, footer])
    assert ARROW_EMOJI not in text
    assert "Notifier" in text
    assert "BLOX FRUITS" in text
    assert show_image is True
    assert accent == 0x23A55A


def test_delivery_accepts_fully_custom_text_templates() -> None:
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        metadata_json={"product_type": "gamepass", "game_name": "BLOX FRUITS"},
    )
    config = {
        "title_template": "MINHA ENTREGA",
        "body_template": "Cliente={client}\n{products}",
        "product_template": "Produto={product} | Jogo={game} | Total={line_total}",
        "footer_template": "#{order_short}",
        "accent_color": "#112233",
        "show_image": False,
    }
    title, lines, footer, accent, show_image = render_delivery(
        config,
        order_id="abcdef12-0000-0000-0000-000000000000",
        client_mention="@Cliente",
        items=[item],
    )
    assert title == "MINHA ENTREGA"
    assert "Cliente=@Cliente" in lines
    assert any("Produto=Notifier" in line for line in lines)
    assert footer == "#abcdef12"
    assert accent == 0x112233
    assert show_image is False
