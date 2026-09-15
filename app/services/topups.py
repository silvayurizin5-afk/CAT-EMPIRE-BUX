from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import CreditTopUp, User
from app.db.payment_models import TopUpNotification
from app.integrations.mercado_pago import MercadoPagoClient
from app.services.audit import write_audit_log
from app.services.commerce_locks import lock_commerce_account
from app.services.wallets import apply_wallet_transaction


class TopUpValidationError(ValueError):
    pass


_INCIDENT_STATUSES = {"refunded", "charged_back", "partially_refunded"}


def _order_incident_status(status: str, status_detail: str) -> str | None:
    if status == "refunded":
        return "refunded"
    if status == "charged_back":
        return "charged_back"
    if status == "processed" and status_detail == "partially_refunded":
        return "partially_refunded"
    return None


def _payment_incident_status(status: str) -> str | None:
    if status in {"refunded", "charged_back"}:
        return status
    return None


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
    # A coluna manteve o nome legado para não quebrar recargas existentes; novas
    # recargas guardam aqui o ID da order da Orders API.
    topup.provider_preference_id = checkout.provider_order_id
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


async def _approve_topup(
    session: AsyncSession,
    *,
    topup: CreditTopUp,
    ledger_reference: str,
    provider_kind: str,
    provider_resource_id: str,
) -> CreditTopUp:
    if topup.status == "approved":
        await _ensure_approval_notification(session, topup_id=topup.id)
        return topup
    if topup.status in _INCIDENT_STATUSES:
        return topup

    user = await session.scalar(select(User).where(User.id == topup.user_id).with_for_update())
    if user is None:
        raise TopUpValidationError("Cliente da recarga não encontrado")

    topup.status = "approved"
    topup.approved_at = datetime.now(UTC)
    await apply_wallet_transaction(
        session,
        user_id=topup.user_id,
        amount=topup.credits_amount,
        kind="topup",
        reference=ledger_reference,
        details={
            "topup_id": str(topup.id),
            "provider": "mercado_pago",
            "provider_kind": provider_kind,
            "provider_resource_id": provider_resource_id,
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
            "provider": "mercado_pago",
            "provider_kind": provider_kind,
            "provider_resource_id": provider_resource_id,
        },
    )
    await _ensure_approval_notification(session, topup_id=topup.id)
    await session.flush()
    return topup


async def _handle_payment_incident(
    session: AsyncSession,
    *,
    topup: CreditTopUp,
    incident_status: str,
    provider_status: str,
    provider_status_detail: str,
    provider_resource_id: str,
) -> CreditTopUp:
    if topup.status in _INCIDENT_STATUSES:
        return topup

    was_approved = topup.status == "approved"
    topup.status = incident_status
    if not was_approved:
        await session.flush()
        return topup

    user = await session.scalar(select(User).where(User.id == topup.user_id).with_for_update())
    if user is None:
        raise TopUpValidationError("Cliente da recarga não encontrado")

    reason = (
        "Pagamento previamente creditado recebeu um reembolso, reembolso parcial "
        "ou contestação no Mercado Pago. Revisão manual necessária."
    )
    await lock_commerce_account(
        session,
        guild_id=topup.guild_id,
        user_id=topup.user_id,
        reason=reason,
        source_topup_id=topup.id,
        provider_status=provider_status,
        provider_status_detail=provider_status_detail,
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
            "provider": "mercado_pago",
            "provider_status": provider_status,
            "provider_status_detail": provider_status_detail,
            "provider_resource_id": provider_resource_id,
            "credits_previously_added": str(money(topup.credits_amount)),
            "automatic_wallet_debit": False,
        },
    )
    await session.flush()
    return topup


async def process_order_update(
    session: AsyncSession,
    *,
    order: dict,
) -> CreditTopUp | None:
    order_id = str(order.get("id") or "")
    if not order_id:
        raise TopUpValidationError("Order sem ID")

    external_reference = str(order.get("external_reference") or "")
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

    provider_order_id = topup.provider_preference_id
    if provider_order_id and provider_order_id != order_id:
        raise TopUpValidationError("Order não corresponde à recarga")
    if provider_order_id is None:
        topup.provider_preference_id = order_id

    provider_status = str(order.get("status") or "").lower()
    status_detail = str(order.get("status_detail") or "").lower()
    incident_status = _order_incident_status(provider_status, status_detail)
    if incident_status is not None:
        return await _handle_payment_incident(
            session,
            topup=topup,
            incident_status=incident_status,
            provider_status=provider_status,
            provider_status_detail=status_detail,
            provider_resource_id=order_id,
        )

    if topup.status in _INCIDENT_STATUSES:
        return topup
    if topup.status == "approved":
        await _ensure_approval_notification(session, topup_id=topup.id)
        return topup

    if provider_status != "processed" or status_detail != "accredited":
        topup.status = (status_detail or provider_status or "pending")[:24]
        await session.flush()
        return topup

    currency = str(order.get("currency") or "")
    paid_amount = money(str(order.get("total_paid_amount") or order.get("total_amount") or "0"))
    if currency != "BRL":
        raise TopUpValidationError("Moeda inesperada")
    if paid_amount != money(topup.amount_brl):
        raise TopUpValidationError("Valor pago não corresponde à recarga")

    return await _approve_topup(
        session,
        topup=topup,
        ledger_reference=f"mercado_pago:order:{order_id}",
        provider_kind="order",
        provider_resource_id=order_id,
    )


async def process_approved_payment(
    session: AsyncSession,
    *,
    payment: dict,
) -> CreditTopUp | None:
    """Processa webhooks do fluxo legado de Preferences/Payments."""
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

    provider_status = str(payment.get("status") or "").lower()
    status_detail = str(payment.get("status_detail") or "").lower()
    incident_status = _payment_incident_status(provider_status)
    if incident_status is not None:
        return await _handle_payment_incident(
            session,
            topup=topup,
            incident_status=incident_status,
            provider_status=provider_status,
            provider_status_detail=status_detail,
            provider_resource_id=payment_id,
        )

    if topup.status in _INCIDENT_STATUSES:
        return topup
    if topup.status == "approved":
        await _ensure_approval_notification(session, topup_id=topup.id)
        return topup

    if provider_status != "approved":
        topup.status = (provider_status or "pending")[:24]
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
    return await _approve_topup(
        session,
        topup=topup,
        ledger_reference=f"mercado_pago:payment:{payment_id}",
        provider_kind="payment",
        provider_resource_id=payment_id,
    )
