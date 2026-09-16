from decimal import Decimal
from uuid import UUID

from app.services.pix import (
    PixConfig,
    build_pix_payload,
    crc16_ccitt,
    render_pix_qr_png,
    txid_for_order,
)


def test_pix_payload_contains_dynamic_amount_key_and_txid() -> None:
    config = PixConfig(
        key="nextbuy@example.com",
        receiver_name="NEXTBUY STORE",
        receiver_city="SAO PAULO",
    )
    payload = build_pix_payload(
        amount_brl=Decimal("65.00"),
        txid="NB123ABC",
        config=config,
    )

    assert "nextbuy@example.com" in payload
    assert "540565.00" in payload
    assert "NB123ABC" in payload
    assert payload.endswith(crc16_ccitt(payload[:-4]))


def test_txid_is_stable_and_within_pix_limit() -> None:
    order_id = UUID("12345678-1234-5678-1234-567812345678")
    txid = txid_for_order(order_id)
    assert txid == "NB12345678123456781234"
    assert len(txid) <= 25
    assert txid.isalnum()


def test_qr_renderer_returns_png() -> None:
    data = render_pix_qr_png("0002010102126304D1D4")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
