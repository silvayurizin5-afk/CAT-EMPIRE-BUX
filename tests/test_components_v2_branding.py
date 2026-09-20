from app.bot.components_v2 import BRAND_ACCENT_HEX, DEFAULT_ACCENT


def test_components_v2_use_nextbuy_brand_accent() -> None:
    assert BRAND_ACCENT_HEX == 0x7B2CBF
    assert DEFAULT_ACCENT.value == 0x7B2CBF
