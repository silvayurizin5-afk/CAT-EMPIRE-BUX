from decimal import Decimal
from types import SimpleNamespace

from app.bot.views.store_games import (
    is_game_product,
    product_store_option_description,
)


def _product(**values):
    defaults = {
        "product_type": "item",
        "metadata_json": {},
        "description": "",
        "price_credits": Decimal("22.00"),
        "stock_quantity": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_game_product_types_are_recognized() -> None:
    assert is_game_product(_product(product_type="Jogo"))
    assert is_game_product(_product(product_type="game"))
    assert not is_game_product(_product(product_type="gamepass"))
    assert not is_game_product(_product(product_type="robux"))


def test_store_description_can_hide_stock_and_keep_price() -> None:
    product = _product(metadata_json={"store_show_stock": False})
    assert product_store_option_description(product) == "R$ 22,00"


def test_store_custom_description_replaces_price_and_stock() -> None:
    product = _product(
        metadata_json={
            "store_show_stock": False,
            "store_selector_description": "Entrega automática • conta completa",
        }
    )
    assert product_store_option_description(product) == (
        "Entrega automática • conta completa"
    )


def test_game_uses_its_description_in_main_selector() -> None:
    product = _product(
        product_type="jogo",
        description="Itens, contas e outros produtos",
        price_credits=None,
    )
    assert product_store_option_description(product) == "Itens, contas e outros produtos"
