import hashlib
import hmac
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import stripe

from app.core.config import settings
from app.integrations.stripe_gateway import (
    StripeGateway,
    StripeWebhookError,
    amount_to_minor_units,
    verify_stripe_event,
)


def test_stripe_amount_uses_brl_minor_units() -> None:
    assert amount_to_minor_units(Decimal("10.80")) == 1080
    assert amount_to_minor_units(Decimal("1.01")) == 101


@pytest.mark.asyncio
async def test_checkout_session_uses_dynamic_payment_methods_and_metadata(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.test/session")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    monkeypatch.setattr(settings, "stripe_credits_product_id", "prod_test_credits")

    topup_id = uuid4()
    checkout = await StripeGateway(secret_key="sk_test_fake").create_credit_checkout(
        topup_id=topup_id,
        amount_brl=Decimal("10.80"),
    )

    assert checkout.session_id == "cs_test_123"
    assert checkout.checkout_url == "https://checkout.stripe.test/session"
    assert captured["mode"] == "payment"
    assert captured["client_reference_id"] == str(topup_id)
    assert captured["line_items"][0]["price_data"]["currency"] == "brl"
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 1080
    assert captured["line_items"][0]["price_data"]["product"] == "prod_test_credits"
    assert captured["metadata"]["topup_id"] == str(topup_id)
    assert captured["payment_intent_data"]["metadata"]["topup_id"] == str(topup_id)
    assert "payment_method_types" not in captured


def _stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_verify_stripe_event_accepts_valid_signature() -> None:
    secret = "whsec_test"
    payload = json.dumps(
        {"id": "evt_test", "object": "event", "type": "checkout.session.completed", "data": {"object": {}}},
        separators=(",", ":"),
    ).encode()
    timestamp = int(time.time())
    event = verify_stripe_event(
        payload=payload,
        signature_header=_stripe_signature(payload, secret, timestamp),
        secret=secret,
        tolerance_seconds=300,
    )
    assert event["type"] == "checkout.session.completed"


def test_verify_stripe_event_rejects_invalid_signature() -> None:
    with pytest.raises(StripeWebhookError):
        verify_stripe_event(
            payload=b'{}',
            signature_header="t=1,v1=bad",
            secret="whsec_test",
            tolerance_seconds=300,
        )
