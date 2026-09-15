import json
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.core.config import settings
from app.integrations.mercado_pago import MercadoPagoClient


@pytest.mark.asyncio
async def test_credit_checkout_uses_orders_api_and_decimal_strings() -> None:
    topup_id = UUID("12345678-1234-5678-1234-567812345678")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/orders"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["X-Idempotency-Key"] == f"nextbuy-topup-{topup_id}"

        payload = json.loads(request.content)
        assert payload["type"] == "online"
        assert payload["processing_mode"] == "manual"
        assert payload["external_reference"] == f"topup:{topup_id}"
        assert payload["total_amount"] == "10.80"
        assert payload["items"][0]["unit_price"] == "10.80"
        assert payload["items"][0]["total_amount"] == "10.80"
        assert payload["config"]["notification_url"] == settings.mercado_pago_webhook_url

        return httpx.Response(
            201,
            json={
                "id": "ORDTEST123",
                "checkout_url": "https://www.mercadopago.com.br/checkout/test",
            },
        )

    client = MercadoPagoClient(
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    checkout = await client.create_credit_checkout(
        topup_id=topup_id,
        amount_brl=Decimal("10.80"),
    )

    assert checkout.provider_order_id == "ORDTEST123"
    assert checkout.preference_id == "ORDTEST123"
    assert checkout.checkout_url == "https://www.mercadopago.com.br/checkout/test"


@pytest.mark.asyncio
async def test_get_order_reads_orders_resource() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/orders/ORD123"
        return httpx.Response(
            200,
            json={
                "id": "ORD123",
                "status": "processed",
                "status_detail": "accredited",
            },
        )

    client = MercadoPagoClient(
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    order = await client.get_order("ORD123")
    assert order["status"] == "processed"
    assert order["status_detail"] == "accredited"
