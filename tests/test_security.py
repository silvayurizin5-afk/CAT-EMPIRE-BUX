import hashlib
import hmac

from app.core.security import verify_mercado_pago_signature


def test_valid_mercado_pago_signature() -> None:
    secret = "test-secret"
    data_id = "ABC123"
    request_id = "request-1"
    ts = "1700000000"
    template = "id:abc123;request-id:request-1;ts:1700000000;"
    signature = hmac.new(secret.encode(), template.encode(), hashlib.sha256).hexdigest()

    assert verify_mercado_pago_signature(
        signature_header=f"ts={ts},v1={signature}",
        request_id=request_id,
        data_id=data_id,
        secret=secret,
        tolerance_seconds=0,
        now=1700000000,
    )


def test_invalid_signature_is_rejected() -> None:
    assert not verify_mercado_pago_signature(
        signature_header="ts=1700000000,v1=bad",
        request_id="request-1",
        data_id="123",
        secret="secret",
        tolerance_seconds=0,
        now=1700000000,
    )
