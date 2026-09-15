import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_mercado_pago_signature
from app.db.session import get_session
from app.integrations.mercado_pago import MercadoPagoClient, MercadoPagoError
from app.services.topups import (
    TopUpValidationError,
    process_approved_payment,
    process_order_update,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="NEXTBUY API", version="0.3.0")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nextbuy-api"}


@app.post("/webhooks/mercado-pago")
async def mercado_pago_webhook(
    request: Request,
    session: SessionDep,
) -> dict[str, str]:
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
                topup = await process_order_update(session, order=provider_resource)
            else:
                topup = await process_approved_payment(session, payment=provider_resource)
    except TopUpValidationError as exc:
        logger.warning("Webhook rejeitado: %s", exc)
        raise HTTPException(status_code=400, detail="invalid payment") from exc
    except MercadoPagoError as exc:
        logger.exception("Falha ao consultar Mercado Pago")
        raise HTTPException(status_code=502, detail="provider unavailable") from exc

    return {"status": "processed" if topup else "ignored"}
