from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditTopUp, User
from app.db.payment_models import TopUpNotification


@dataclass(slots=True)
class PendingTopUpNotification:
    notification_id: int
    topup_id: str
    guild_id: int
    discord_user_id: int
    credits_amount: Decimal
    provider: str


async def claim_pending_topup_notifications(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    lease_seconds: int = 300,
    limit: int = 25,
) -> list[PendingTopUpNotification]:
    current = now or datetime.now(UTC)
    expired = current - timedelta(seconds=max(30, lease_seconds))
    rows = (
        await session.execute(
            select(TopUpNotification, CreditTopUp, User)
            .join(CreditTopUp, CreditTopUp.id == TopUpNotification.topup_id)
            .join(User, User.id == CreditTopUp.user_id)
            .where(
                TopUpNotification.sent_at.is_(None),
                or_(
                    TopUpNotification.claimed_at.is_(None),
                    TopUpNotification.claimed_at <= expired,
                ),
            )
            .order_by(TopUpNotification.created_at.asc())
            .limit(max(1, min(limit, 100)))
            .with_for_update(skip_locked=True)
        )
    ).all()

    result: list[PendingTopUpNotification] = []
    for notification, topup, user in rows:
        notification.claimed_at = current
        notification.attempts += 1
        result.append(
            PendingTopUpNotification(
                notification_id=notification.id,
                topup_id=str(topup.id),
                guild_id=topup.guild_id,
                discord_user_id=user.discord_user_id,
                credits_amount=topup.credits_amount,
                provider=topup.provider,
            )
        )
    await session.flush()
    return result


async def mark_topup_notification_sent(
    session: AsyncSession, *, notification_id: int, note: str | None = None
) -> None:
    notification = await session.scalar(
        select(TopUpNotification)
        .where(TopUpNotification.id == notification_id)
        .with_for_update()
    )
    if notification is None or notification.sent_at is not None:
        return
    notification.sent_at = datetime.now(UTC)
    notification.claimed_at = None
    notification.last_error = (note or "")[:1000] or None
    await session.flush()


async def release_topup_notification(
    session: AsyncSession, *, notification_id: int, error: str
) -> None:
    notification = await session.scalar(
        select(TopUpNotification)
        .where(TopUpNotification.id == notification_id)
        .with_for_update()
    )
    if notification is None or notification.sent_at is not None:
        return
    notification.claimed_at = None
    notification.last_error = error[:1000]
    await session.flush()
