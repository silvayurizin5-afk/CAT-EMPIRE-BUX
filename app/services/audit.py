from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit_models import AuditDelivery
from app.db.models import AuditLog, GuildConfig


@dataclass(slots=True, frozen=True)
class PendingAuditDelivery:
    delivery_id: int
    audit_log_id: int
    guild_id: int
    logs_channel_id: int
    actor_discord_id: int | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict
    created_at: datetime


async def write_audit_log(
    session: AsyncSession,
    *,
    guild_id: int,
    actor_discord_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    log = AuditLog(
        guild_id=guild_id,
        actor_discord_id=actor_discord_id,
        action=action[:80],
        target_type=(target_type or "")[:80] or None,
        target_id=(target_id or "")[:160] or None,
        details=details or {},
    )
    session.add(log)
    await session.flush()
    session.add(AuditDelivery(audit_log_id=log.id))
    await session.flush()
    return log


async def claim_pending_audit_deliveries(
    session: AsyncSession,
    *,
    limit: int = 20,
    stale_after_minutes: int = 5,
) -> list[PendingAuditDelivery]:
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=max(1, stale_after_minutes))
    rows = (
        await session.execute(
            select(AuditDelivery, AuditLog, GuildConfig)
            .join(AuditLog, AuditLog.id == AuditDelivery.audit_log_id)
            .join(GuildConfig, GuildConfig.guild_id == AuditLog.guild_id)
            .where(
                AuditDelivery.published_at.is_(None),
                GuildConfig.logs_channel_id.is_not(None),
                or_(
                    AuditDelivery.claimed_at.is_(None),
                    AuditDelivery.claimed_at < stale_before,
                ),
            )
            .order_by(AuditLog.created_at.asc(), AuditDelivery.id.asc())
            .limit(max(1, min(limit, 100)))
            .with_for_update(skip_locked=True, of=AuditDelivery)
        )
    ).all()

    claimed: list[PendingAuditDelivery] = []
    for delivery, log, config in rows:
        if config.logs_channel_id is None:
            continue
        delivery.claimed_at = now
        delivery.attempts += 1
        delivery.last_error = None
        claimed.append(
            PendingAuditDelivery(
                delivery_id=delivery.id,
                audit_log_id=log.id,
                guild_id=log.guild_id,
                logs_channel_id=config.logs_channel_id,
                actor_discord_id=log.actor_discord_id,
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                details=dict(log.details or {}),
                created_at=log.created_at,
            )
        )
    await session.flush()
    return claimed


async def mark_audit_delivery_published(
    session: AsyncSession,
    *,
    delivery_id: int,
) -> None:
    delivery = await session.scalar(
        select(AuditDelivery).where(AuditDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None or delivery.published_at is not None:
        return
    delivery.published_at = datetime.now(UTC)
    delivery.claimed_at = None
    delivery.last_error = None
    await session.flush()


async def release_audit_delivery(
    session: AsyncSession,
    *,
    delivery_id: int,
    error: str,
) -> None:
    delivery = await session.scalar(
        select(AuditDelivery).where(AuditDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None or delivery.published_at is not None:
        return
    delivery.claimed_at = None
    delivery.last_error = error[:1000]
    await session.flush()
