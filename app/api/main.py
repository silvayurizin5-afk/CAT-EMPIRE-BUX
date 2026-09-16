import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_mercado_pago_signature
from app.db.session import get_session
from app.integrations.mercado_pago import MercadoPagoClient, MercadoPagoError
from app.integrations.stripe_gateway import StripeWebhookError, verify_stripe_event
from app.services.stripe_orders import (
    StripeOrderValidationError,
    process_stripe_order_checkout_event,
)
from app.services.stripe_topups import (
    StripeTopUpValidationError,
    process_stripe_checkout_event,
    process_stripe_incident_event,
)
from app.services.topups import (
    TopUpValidationError,
    process_approved_payment,
    process_order_update,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="NEXTBUY API", version="0.4.0")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nextbuy-api", "payment_gateway": "stripe"}


@app.get("/payments/success", response_class=HTMLResponse)
async def payment_success() -> str:
    return (
        "<!doctype html><html><body style='font-family:sans-serif;background:#111;color:#fff;"
        "display:grid;place-items:center;min-height:100vh'>"
        "<main><h1>Pagamento recebido</h1>"
        "<p>Volte para o Discord e use Verificar pagamento para continuar seu pedido.</p>"
        "</main></body></html>"
    )


@app.get("/payments/cancel", response_class=HTMLResponse)
async def payment_cancel() -> str:
    return (
        "<!doctype html><html><body style='font-family:sans-serif;background:#111;color:#fff;"
        "display:grid;place-items:center;min-height:100vh'>"
        "<main><h1>Pagamento cancelado</h1>"
        "<p>Nenhuma cobrança foi concluída. Você pode voltar ao Discord e tentar novamente.</p>"
        "</main></body></html>"
    )


@app.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    secret = settings.stripe_webhook_secret.get_secret_value()
    try:
        event = verify_stripe_event(
            payload=payload,
            signature_header=signature,
            secret=secret,
            tolerance_seconds=settings.webhook_signature_tolerance_seconds,
        )
    except StripeWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature") from exc

    event_type = str(event.get("type") or "")
    data = event.get("data") or {}
    resource = data.get("object") if isinstance(data, dict) else None
    if not isinstance(resource, dict):
        return {"status": "ignored"}

    checkout_events = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    incident_events = {"charge.refunded", "charge.dispute.created"}
    if event_type not in checkout_events | incident_events:
        return {"status": "ignored"}

    metadata = resource.get("metadata") or {}
    is_order_checkout = isinstance(metadata, dict) and bool(metadata.get("order_id"))

    try:
        async with session.begin():
            if event_type in checkout_events and is_order_checkout:
                processed = await process_stripe_order_checkout_event(
                    session,
                    event_type=event_type,
                    checkout=resource,
                )
            elif event_type in checkout_events:
                processed = await process_stripe_checkout_event(
                    session,
                    event_type=event_type,
                    checkout=resource,
                )
            else:
                processed = await process_stripe_incident_event(
                    session,
                    event_type=event_type,
                    resource=resource,
                )
    except (StripeOrderValidationError, StripeTopUpValidationError) as exc:
        logger.warning("Webhook Stripe rejeitado: %s", exc)
        raise HTTPException(status_code=400, detail="invalid stripe event") from exc

    return {"status": "processed" if processed else "ignored"}


@app.post("/webhooks/mercado-pago")
async def mercado_pago_webhook(
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
    """Compatibilidade temporária para pagamentos antigos do Mercado Pago."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid payload")

    body_data = body.get("data")
    body_data_id = body_data.get("id") if isinstance(body_data, dict) else None
    data_id = request.query_params.get("data.id") or str(body_data_id or "")
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    secret = settings.mercado_pago_webhook_secret.get_secret_value()

    if not verify_mercado_pago_signature(
        signature_header=signature,
        request_id=request_id,
        data_id=data_id,
        secret=secret,
        tolerance_seconds=settings.webhook_signature_tolerance_seconds,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    event_type = str(body.get("type") or request.query_params.get("type") or "").lower()
    if event_type not in {"order", "payment"} or not data_id:
        return {"status": "ignored"}

    try:
        mercado_pago = MercadoPagoClient()
        if event_type == "order":
            provider_resource = await mercado_pago.get_order(data_id)
        else:
            provider_resource = await mercado_pago.get_payment(data_id)

        async with session.begin():
            if event_type == "order":
                legacy_payment = await process_order_update(session, order=provider_resource)
            else:
                legacy_payment = await process_approved_payment(session, payment=provider_resource)
    except TopUpValidationError as exc:
        logger.warning("Webhook legado do Mercado Pago rejeitado: %s", exc)
        raise HTTPException(status_code=400, detail="invalid payment") from exc
    except MercadoPagoError as exc:
        logger.exception("Falha ao consultar Mercado Pago")
        raise HTTPException(status_code=502, detail="provider unavailable") from exc

    return {"status": "processed" if legacy_payment else "ignored"}
