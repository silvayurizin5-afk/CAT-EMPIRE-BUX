from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import CreditTopUp
from app.db.payment_models import TopUpNotification
from app.integrations.mercado_pago import MercadoPagoClient
from app.services.wallets import apply_wallet_transaction


class TopUpValidationError(ValueError):
    pass


async def create_topup(
    session: AsyncSession,
    *,
    user_id: int,
    guild_id: int,
    amount_brl: Decimal,
    mercado_pago: MercadoPagoClient,
) -> CreditTopUp:
    amount = require_positive(amount_brl)
    topup = CreditTopUp(
        user_id=user_id,
        guild_id=guild_id,
        amount_brl=amount,
        credits_amount=amount,
        status="creating",
    )
    session.add(topup)
    await session.flush()

    checkout = await mercado_pago.create_credit_checkout(topup_id=topup.id, amount_brl=amount)
    topup.provider_preference_id = checkout.preference_id
    topup.checkout_url = checkout.checkout_url
    topup.status = "pending"
    await session.flush()
    return topup


async def _ensure_approval_notification(
    session: AsyncSession, *, topup_id: UUID
) -> TopUpNotification:
    notification = await session.scalar(
        select(TopUpNotification).where(TopUpNotification.topup_id == topup_id)
    )
    if notification is None:
        notification = TopUpNotification(topup_id=topup_id)
        session.add(notification)
        await session.flush()
    return notification


async def process_approved_payment(
    session: AsyncSession,
    *,
    payment: dict,
) -> CreditTopUp | None:
    payment_id = str(payment.get("id", ""))
    if not payment_id:
        raise TopUpValidationError("Pagamento sem ID")

    external_reference = str(payment.get("external_reference") or "")
    if not external_reference.startswith("topup:"):
        return None

    try:
        topup_id = UUID(external_reference.removeprefix("topup:"))
    except ValueError as exc:
        raise TopUpValidationError("Referência externa inválida") from exc

    topup = await session.scalar(
        select(CreditTopUp).where(CreditTopUp.id == topup_id).with_for_update()
    )
    if topup is None:
        raise TopUpValidationError("Recarga não encontrada")

    if topup.status == "approved":
        await _ensure_approval_notification(session, topup_id=topup.id)
        return topup

    status = str(payment.get("status") or "")
    if status != "approved":
        topup.status = status or "pending"
        if topup.provider_payment_id is None:
            topup.provider_payment_id = payment_id
        await session.flush()
        return topup

    currency = str(payment.get("currency_id") or "")
    paid_amount = money(str(payment.get("transaction_amount") or "0"))
    if currency != "BRL":
        raise TopUpValidationError("Moeda inesperada")
    if paid_amount != money(topup.amount_brl):
        raise TopUpValidationError("Valor pago não corresponde à recarga")

    other_topup = await session.scalar(
        select(CreditTopUp.id).where(
            CreditTopUp.provider_payment_id == payment_id,
            CreditTopUp.id != topup.id,
        )
    )
    if other_topup is not None:
        raise TopUpValidationError("Pagamento já associado a outra recarga")

    topup.provider_payment_id = payment_id
    topup.status = "approved"
    topup.approved_at = datetime.now(UTC)
    await apply_wallet_transaction(
        session,
        user_id=topup.user_id,
        amount=topup.credits_amount,
        kind="topup",
        reference=f"mercado_pago:payment:{payment_id}",
        details={"topup_id": str(topup.id)},
    )
    await _ensure_approval_notification(session, topup_id=topup.id)
    await session.flush()
    return topup
