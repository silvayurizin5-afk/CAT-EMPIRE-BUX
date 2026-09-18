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
