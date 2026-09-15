import asyncio
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import stripe

from app.core.config import settings
from app.core.money import money


class StripeGatewayError(RuntimeError):
    pass


class StripeWebhookError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class StripeCheckout:
    session_id: str
    checkout_url: str


def amount_to_minor_units(amount_brl: Decimal) -> int:
    amount = money(amount_brl)
    return int(amount * 100)


class StripeGateway:
    def __init__(self, secret_key: str | None = None) -> None:
        self.secret_key = secret_key or settings.stripe_secret_key.get_secret_value()
        if not self.secret_key:
            raise StripeGatewayError("STRIPE_SECRET_KEY não configurada")

    async def create_credit_checkout(
        self,
        *,
        topup_id: UUID,
        amount_brl: Decimal,
    ) -> StripeCheckout:
        amount = money(amount_brl)
        unit_amount = amount_to_minor_units(amount)
        product_id = (settings.stripe_credits_product_id or "").strip()

        price_data: dict = {
            "currency": "brl",
            "unit_amount": unit_amount,
        }
        if product_id:
            price_data["product"] = product_id
        else:
            price_data["product_data"] = {
                "name": "NEXTBUY Credits",
                "description": f"{amount:.2f} créditos NEXTBUY",
            }

        metadata = {"topup_id": str(topup_id), "app": "nextbuy"}

        def _create():
            return stripe.checkout.Session.create(
                api_key=self.secret_key,
                mode="payment",
                client_reference_id=str(topup_id),
                line_items=[{"price_data": price_data, "quantity": 1}],
                metadata=metadata,
                payment_intent_data={"metadata": metadata},
                success_url=settings.stripe_checkout_success_url,
                cancel_url=settings.stripe_checkout_cancel_url,
            )

        try:
            checkout = await asyncio.to_thread(_create)
        except Exception as exc:
            raise StripeGatewayError("Falha ao criar Checkout Session da Stripe") from exc

        session_id = str(getattr(checkout, "id", "") or "")
        checkout_url = str(getattr(checkout, "url", "") or "")
        if not session_id or not checkout_url:
            raise StripeGatewayError("Stripe retornou uma Checkout Session inválida")
        return StripeCheckout(session_id=session_id, checkout_url=checkout_url)


def verify_stripe_event(
    *,
    payload: bytes,
    signature_header: str,
    secret: str,
    tolerance_seconds: int = 300,
) -> dict:
    if not payload or not signature_header or not secret:
        raise StripeWebhookError("Webhook Stripe sem assinatura ou segredo")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=secret,
            tolerance=tolerance_seconds,
        )
    except Exception as exc:
        raise StripeWebhookError("Assinatura Stripe inválida") from exc

    if hasattr(event, "to_dict_recursive"):
        return event.to_dict_recursive()
    return dict(event)
