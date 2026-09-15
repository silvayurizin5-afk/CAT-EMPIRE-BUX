from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import CreditTopUp, User
from app.db.payment_models import TopUpNotification
from app.integrations.stripe_gateway import StripeGateway
from app.services.audit import write_audit_log
from app.services.commerce_locks import lock_commerce_account
from app.services.wallets import apply_wallet_transaction


class StripeTopUpValidationError(ValueError):
    pass


_INCIDENT_STATUSES = {"refunded", "partially_refunded", "disputed"}


async def create_stripe_topup(
    session: AsyncSession,
    *,
    user_id: int,
    guild_id: int,
    amount_brl: Decimal,
    stripe_gateway: StripeGateway,
) -> CreditTopUp:
    amount = require_positive(amount_brl)
    topup = CreditTopUp(
        user_id=user_id,
        guild_id=guild_id,
        amount_brl=amount,
        credits_amount=amount,
        provider="stripe",
        status="creating",
    )
    session.add(topup)
    await session.flush()

    checkout = await stripe_gateway.create_credit_checkout(
        topup_id=topup.id,
        amount_brl=amount,
    )
    topup.provider_preference_id = checkout.session_id
    topup.checkout_url = checkout.checkout_url
    topup.status = "pending"
    await session.flush()
    return topup


async def _ensure_notification(
    session: AsyncSession,
    *,
    topup_id: UUID,
) -> TopUpNotification:
    notification = await session.scalar(
        select(TopUpNotification).where(TopUpNotification.topup_id == topup_id)
    )
    if notification is None:
        notification = TopUpNotification(topup_id=topup_id)
        session.add(notification)
        await session.flush()
    return notification


def _extract_topup_id(resource: dict) -> UUID | None:
    metadata = resource.get("metadata") or {}
    candidate = None
    if isinstance(metadata, dict):
        candidate = metadata.get("topup_id")
    candidate = candidate or resource.get("client_reference_id")
    if not candidate:
        return None
    try:
        return UUID(str(candidate))
    except ValueError:
        return None


def _extract_payment_intent_id(resource: dict) -> str | None:
    value = resource.get("payment_intent")
    if isinstance(value, dict):
        value = value.get("id")
    text = str(value or "").strip()
    return text or None


async def _load_stripe_topup(
    session: AsyncSession,
    *,
    topup_id: UUID,
) -> CreditTopUp:
    topup = await session.scalar(
        select(CreditTopUp).where(CreditTopUp.id == topup_id).with_for_update()
    )
    if topup is None:
        raise StripeTopUpValidationError("Recarga Stripe não encontrada")
    if topup.provider != "stripe":
        raise StripeTopUpValidationError("Recarga pertence a outro gateway")
    return topup


async def _approve_stripe_topup(
    session: AsyncSession,
    *,
    topup: CreditTopUp,
    checkout_session_id: str,
    payment_intent_id: str | None,
) -> CreditTopUp:
    if topup.status == "approved":
        await _ensure_notification(session, topup_id=topup.id)
        return topup
    if topup.status in _INCIDENT_STATUSES:
        return topup

    user = await session.scalar(select(User).where(User.id == topup.user_id).with_for_update())
    if user is None:
        raise StripeTopUpValidationError("Cliente da recarga não encontrado")

    if payment_intent_id:
        other = await session.scalar(
            select(CreditTopUp.id).where(
                CreditTopUp.provider == "stripe",
                CreditTopUp.provider_payment_id == payment_intent_id,
                CreditTopUp.id != topup.id,
            )
        )
        if other is not None:
            raise StripeTopUpValidationError("PaymentIntent já ligado a outra recarga")
        topup.provider_payment_id = payment_intent_id

    topup.status = "approved"
    topup.approved_at = datetime.now(UTC)
    await apply_wallet_transaction(
        session,
        user_id=topup.user_id,
        amount=topup.credits_amount,
        kind="topup",
        reference=f"stripe:checkout:{checkout_session_id}",
        details={
            "topup_id": str(topup.id),
            "provider": "stripe",
            "checkout_session_id": checkout_session_id,
            "payment_intent_id": payment_intent_id,
        },
    )
    await write_audit_log(
        session,
        guild_id=topup.guild_id,
        actor_discord_id=None,
        action="topup.approved",
        target_type="topup",
        target_id=str(topup.id),
        details={
            "amount_brl": str(money(topup.amount_brl)),
            "credits": str(money(topup.credits_amount)),
            "customer_discord_id": user.discord_user_id,
            "provider": "stripe",
            "checkout_session_id": checkout_session_id,
            "payment_intent_id": payment_intent_id,
        },
    )
    await _ensure_notification(session, topup_id=topup.id)
    await session.flush()
    return topup


async def process_stripe_checkout_event(
    session: AsyncSession,
    *,
    event_type: str,
    checkout: dict,
) -> CreditTopUp | None:
    topup_id = _extract_topup_id(checkout)
    if topup_id is None:
        return None

    topup = await _load_stripe_topup(session, topup_id=topup_id)
    session_id = str(checkout.get("id") or "")
    if not session_id:
        raise StripeTopUpValidationError("Checkout Session sem ID")
    if topup.provider_preference_id and topup.provider_preference_id != session_id:
        raise StripeTopUpValidationError("Checkout Session não corresponde à recarga")
    topup.provider_preference_id = session_id

    if event_type == "checkout.session.expired":
        if topup.status != "approved":
            topup.status = "expired"
        await session.flush()
        return topup

    if event_type == "checkout.session.async_payment_failed":
        if topup.status != "approved":
            topup.status = "failed"
        await session.flush()
        return topup

    currency = str(checkout.get("currency") or "").lower()
    amount_total = checkout.get("amount_total")
    if currency != "brl":
        raise StripeTopUpValidationError("Moeda inesperada no Checkout Stripe")
    try:
        paid_amount = money(Decimal(int(amount_total)) / 100)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise StripeTopUpValidationError("Valor inválido no Checkout Stripe") from exc
    if paid_amount != money(topup.amount_brl):
        raise StripeTopUpValidationError("Valor pago não corresponde à recarga")

    payment_status = str(checkout.get("payment_status") or "").lower()
    if payment_status != "paid":
        if topup.status != "approved":
            topup.status = "pending"
        await session.flush()
        return topup

    return await _approve_stripe_topup(
        session,
        topup=topup,
        checkout_session_id=session_id,
        payment_intent_id=_extract_payment_intent_id(checkout),
    )


async def process_stripe_incident_event(
    session: AsyncSession,
    *,
    event_type: str,
    resource: dict,
) -> CreditTopUp | None:
    topup_id = _extract_topup_id(resource)
    payment_intent_id = _extract_payment_intent_id(resource)

    topup: CreditTopUp | None = None
    if topup_id is not None:
        topup = await session.scalar(
            select(CreditTopUp)
            .where(CreditTopUp.id == topup_id, CreditTopUp.provider == "stripe")
            .with_for_update()
        )
    elif payment_intent_id:
        topup = await session.scalar(
            select(CreditTopUp)
            .where(
                CreditTopUp.provider == "stripe",
                CreditTopUp.provider_payment_id == payment_intent_id,
            )
            .with_for_update()
        )
    if topup is None:
        return None
    if topup.status in _INCIDENT_STATUSES:
        return topup

    if event_type == "charge.refunded":
        amount = int(resource.get("amount") or 0)
        amount_refunded = int(resource.get("amount_refunded") or 0)
        incident_status = "refunded" if amount > 0 and amount_refunded >= amount else "partially_refunded"
    elif event_type == "charge.dispute.created":
        incident_status = "disputed"
    else:
        return None

    was_approved = topup.status == "approved"
    topup.status = incident_status
    if not was_approved:
        await session.flush()
        return topup

    user = await session.scalar(select(User).where(User.id == topup.user_id).with_for_update())
    if user is None:
        raise StripeTopUpValidationError("Cliente da recarga não encontrado")

    reason = (
        "Pagamento Stripe previamente creditado recebeu reembolso ou contestação. "
        "A conta comercial foi bloqueada para revisão manual; nenhum débito automático foi feito."
    )
    await lock_commerce_account(
        session,
        guild_id=topup.guild_id,
        user_id=topup.user_id,
        reason=reason,
        source_topup_id=topup.id,
        provider_status=incident_status,
        provider_status_detail=event_type,
    )
    await write_audit_log(
        session,
        guild_id=topup.guild_id,
        actor_discord_id=None,
        action="topup.payment_incident",
        target_type="topup",
        target_id=str(topup.id),
        details={
            "customer_discord_id": user.discord_user_id,
            "provider": "stripe",
            "provider_status": incident_status,
            "event_type": event_type,
            "payment_intent_id": payment_intent_id,
            "credits_previously_added": str(money(topup.credits_amount)),
            "automatic_wallet_debit": False,
        },
    )
    await session.flush()
    return topup
