from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.money import money


class MercadoPagoError(RuntimeError):
    pass


@dataclass(slots=True)
class CheckoutSession:
    provider_order_id: str
    checkout_url: str

    @property
    def preference_id(self) -> str:
        """Compatibilidade temporária com o fluxo legado de Preferences."""
        return self.provider_order_id


# Compatibilidade para imports antigos enquanto o projeto migra para Orders API.
CheckoutPreference = CheckoutSession


class MercadoPagoClient:
    base_url = "https://api.mercadopago.com"

    def __init__(
        self,
        access_token: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = access_token or settings.mercado_pago_access_token.get_secret_value()
        if not token:
            raise MercadoPagoError("MERCADO_PAGO_ACCESS_TOKEN não configurado")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport

    async def create_credit_checkout(
        self,
        *,
        topup_id: UUID,
        amount_brl: Decimal,
    ) -> CheckoutSession:
        amount = money(amount_brl)
        amount_text = f"{amount:.2f}"
        payload = {
            "type": "online",
            "processing_mode": "manual",
            "total_amount": amount_text,
            "external_reference": f"topup:{topup_id}",
            "description": f"{amount_text} créditos NEXTBUY",
            "items": [
                {
                    "title": "NEXTBUY Credits",
                    "quantity": 1,
                    "unit_price": amount_text,
                    "total_amount": amount_text,
                    "unit_measure": "unit",
                }
            ],
            "config": {
                "notification_url": settings.mercado_pago_webhook_url,
            },
        }
        headers = {
            **self._headers,
            "Content-Type": "application/json",
            "X-Idempotency-Key": f"nextbuy-topup-{topup_id}",
        }
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
            response = await client.post(
                f"{self.base_url}/v1/orders",
                headers=headers,
                json=payload,
            )
        if response.is_error:
            raise MercadoPagoError(f"Falha ao criar checkout ({response.status_code})")
        try:
            data = response.json()
        except ValueError as exc:
            raise MercadoPagoError("Resposta inválida do Mercado Pago") from exc
        checkout_url = data.get("checkout_url")
        if not data.get("id") or not checkout_url:
            raise MercadoPagoError("Resposta do Mercado Pago sem checkout válido")
        return CheckoutSession(
            provider_order_id=str(data["id"]),
            checkout_url=str(checkout_url),
        )

    async def get_order(self, order_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
            response = await client.get(
                f"{self.base_url}/v1/orders/{order_id}",
                headers=self._headers,
            )
        if response.is_error:
            raise MercadoPagoError(f"Falha ao consultar order ({response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise MercadoPagoError("Resposta inválida do Mercado Pago") from exc

    async def get_payment(self, payment_id: str) -> dict:
        """Fallback para recargas criadas pelo fluxo legado de Preferences."""
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as client:
            response = await client.get(
                f"{self.base_url}/v1/payments/{payment_id}",
                headers=self._headers,
            )
        if response.is_error:
            raise MercadoPagoError(f"Falha ao consultar pagamento ({response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise MercadoPagoError("Resposta inválida do Mercado Pago") from exc
