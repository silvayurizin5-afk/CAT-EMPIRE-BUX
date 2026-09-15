from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.money import money


class MercadoPagoError(RuntimeError):
    pass


@dataclass(slots=True)
class CheckoutPreference:
    preference_id: str
    checkout_url: str


class MercadoPagoClient:
    base_url = "https://api.mercadopago.com"

    def __init__(self, access_token: str | None = None) -> None:
        token = access_token or settings.mercado_pago_access_token.get_secret_value()
        if not token:
            raise MercadoPagoError("MERCADO_PAGO_ACCESS_TOKEN não configurado")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def create_credit_checkout(
        self,
        *,
        topup_id: UUID,
        amount_brl: Decimal,
    ) -> CheckoutPreference:
        amount = money(amount_brl)
        payload = {
            "items": [
                {
                    "id": f"nextbuy-credits-{topup_id}",
                    "title": "NEXTBUY Credits",
                    "description": f"{amount:.2f} créditos NEXTBUY",
                    "quantity": 1,
                    "currency_id": "BRL",
                    "unit_price": float(amount),
                }
            ],
            "external_reference": f"topup:{topup_id}",
            "notification_url": settings.mercado_pago_webhook_url,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.base_url}/checkout/preferences",
                headers=self._headers,
                json=payload,
            )
        if response.is_error:
            raise MercadoPagoError(f"Falha ao criar checkout ({response.status_code})")
        data = response.json()
        checkout_url = data.get("init_point") or data.get("sandbox_init_point")
        if not data.get("id") or not checkout_url:
            raise MercadoPagoError("Resposta do Mercado Pago sem checkout válido")
        return CheckoutPreference(preference_id=str(data["id"]), checkout_url=str(checkout_url))

    async def get_payment(self, payment_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self.base_url}/v1/payments/{payment_id}", headers=self._headers
            )
        if response.is_error:
            raise MercadoPagoError(f"Falha ao consultar pagamento ({response.status_code})")
        return response.json()
