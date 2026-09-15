import logging

from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_mercado_pago_signature
from app.db.session import get_session
from app.integrations.mercado_pago import MercadoPagoClient, MercadoPagoError
from app.services.topups import TopUpValidationError, process_approved_payment

logger = logging.getLogger(__name__)
app = FastAPI(title="NEXTBUY API", version="0.2.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nextbuy-api"}


@app.post("/webhooks/mercado-pago")
async def mercado_pago_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    body = await request.json()
    data_id = request.query_params.get("data.id") or str((body.get("data") or {}).get("id") or "")
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

    event_type = str(body.get("type") or request.query_params.get("type") or "")
    if event_type != "payment" or not data_id:
        return {"status": "ignored"}

    try:
        payment = await MercadoPagoClient().get_payment(data_id)
        async with session.begin():
            topup = await process_approved_payment(session, payment=payment)
    except TopUpValidationError as exc:
        logger.warning("Webhook rejeitado: %s", exc)
        raise HTTPException(status_code=400, detail="invalid payment") from exc
    except MercadoPagoError as exc:
        logger.exception("Falha ao consultar Mercado Pago")
        raise HTTPException(status_code=502, detail="provider unavailable") from exc

    return {"status": "processed" if topup else "ignored"}
