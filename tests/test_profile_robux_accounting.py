from decimal import Decimal

from app.services.profiles import _robux_from_order_item


def test_direct_robux_purchase_updates_robux_total() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "robux", "robux_amount": 1000},
            quantity=1,
        )
        == 1000
    )


def test_gamepass_updates_robux_total_and_respects_quantity() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "gamepass", "robux_amount": 2200},
            quantity=2,
        )
        == 4400
    )


def test_item_never_updates_robux_total() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "item", "robux_amount": 9999},
            quantity=3,
        )
        == 0
    )


def test_old_gamepass_order_can_use_current_product_robux_value() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "gamepass"},
            quantity=1,
            current_product_type="gamepass",
            current_product_metadata={"robux_amount": 2200},
        )
        == 2200
    )


def test_legacy_order_without_type_can_fall_back_to_current_gamepass() -> None:
    assert (
        _robux_from_order_item(
            {},
            quantity=1,
            current_product_type="game_pass",
            current_product_metadata={"robux_amount": 450},
        )
        == 450
    )


def test_legacy_direct_robux_product_infers_amount_from_normal_price() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "robux"},
            quantity=1,
            unit_price=Decimal("2.90"),
        )
        == 100
    )


def test_legacy_direct_robux_product_inference_respects_quantity() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "robux"},
            quantity=5,
            unit_price=Decimal("2.90"),
        )
        == 500
    )


def test_explicit_direct_robux_amount_has_priority_over_price_inference() -> None:
    assert (
        _robux_from_order_item(
            {"product_type": "robux", "robux_amount": 250},
            quantity=2,
            unit_price=Decimal("2.90"),
        )
        == 500
    )


def test_legacy_robux_order_can_recover_amount_from_historical_rate() -> None:
    assert (
        _robux_from_order_item(
            {"price_per_robux": "0.029"},
            item_name="Robux normal",
            quantity=1,
            unit_price=Decimal("29.00"),
        )
        == 1000
    )


def test_legacy_robux_order_can_recover_amount_from_item_name() -> None:
    assert (
        _robux_from_order_item(
            {},
            item_name="Entrega normal • 2.500 Robux",
            quantity=1,
            unit_price=Decimal("72.50"),
        )
        == 2500
    )


def test_item_named_robux_is_treated_as_legacy_direct_robux_purchase() -> None:
    assert (
        _robux_from_order_item(
            {},
            item_name="100 Robux",
            quantity=3,
            unit_price=Decimal("2.90"),
        )
        == 300
    )
