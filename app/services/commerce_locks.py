from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.risk_models import CommerceLock


class CommerceLockedError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class LockedCustomer:
    lock_id: int
    user_id: int
    discord_user_id: int
    reason: str
    provider_status: str | None
    provider_status_detail: str | None
    locked_at: datetime


async def get_active_commerce_lock(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
) -> CommerceLock | None:
    return await session.scalar(
        select(CommerceLock).where(
            CommerceLock.guild_id == guild_id,
            CommerceLock.user_id == user_id,
            CommerceLock.active.is_(True),
        )
    )


async def require_commerce_unlocked(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
) -> None:
    lock = await get_active_commerce_lock(
        session,
        guild_id=guild_id,
        user_id=user_id,
    )
    if lock is not None:
        raise CommerceLockedError(
            "Sua conta está temporariamente bloqueada para compras. "
            "Fale com o suporte da NEXTBUY para revisão."
        )


async def lock_commerce_account(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    reason: str,
    source_topup_id: UUID | None,
    provider_status: str | None,
    provider_status_detail: str | None,
) -> CommerceLock:
    now = datetime.now(UTC)
    clean_reason = reason.strip()[:2000]
    statement = (
        insert(CommerceLock)
        .values(
            guild_id=guild_id,
            user_id=user_id,
            source_topup_id=source_topup_id,
            provider_status=(provider_status or "")[:32] or None,
            provider_status_detail=(provider_status_detail or "")[:64] or None,
            reason=clean_reason,
            active=True,
            locked_at=now,
            resolved_at=None,
            resolved_by_discord_id=None,
        )
        .on_conflict_do_update(
            constraint="uq_commerce_lock_guild_user",
            set_={
                "source_topup_id": source_topup_id,
                "provider_status": (provider_status or "")[:32] or None,
                "provider_status_detail": (provider_status_detail or "")[:64] or None,
                "reason": clean_reason,
                "active": True,
                "locked_at": now,
                "resolved_at": None,
                "resolved_by_discord_id": None,
            },
        )
        .returning(CommerceLock.id)
    )
    lock_id = await session.scalar(statement)
    if lock_id is None:
        raise RuntimeError("Falha ao bloquear conta comercial")
    lock = await session.get(CommerceLock, lock_id)
    if lock is None:
        raise RuntimeError("Bloqueio comercial não encontrado")
    return lock


async def resolve_commerce_lock(
    session: AsyncSession,
    *,
    lock_id: int,
    admin_discord_id: int,
) -> CommerceLock:
    lock = await session.scalar(
        select(CommerceLock).where(CommerceLock.id == lock_id).with_for_update()
    )
    if lock is None:
        raise ValueError("Bloqueio não encontrado")
    if not lock.active:
        raise ValueError("Bloqueio já foi resolvido")
    lock.active = False
    lock.resolved_at = datetime.now(UTC)
    lock.resolved_by_discord_id = admin_discord_id
    await session.flush()
    return lock


async def list_locked_customers(
    session: AsyncSession,
    *,
    guild_id: int,
    limit: int = 25,
) -> list[LockedCustomer]:
    rows = (
        await session.execute(
            select(CommerceLock, User)
            .join(User, User.id == CommerceLock.user_id)
            .where(
                CommerceLock.guild_id == guild_id,
                CommerceLock.active.is_(True),
            )
            .order_by(CommerceLock.locked_at.desc(), CommerceLock.id.desc())
            .limit(max(1, min(limit, 25)))
        )
    ).all()
    return [
        LockedCustomer(
            lock_id=lock.id,
            user_id=user.id,
            discord_user_id=user.discord_user_id,
            reason=lock.reason,
            provider_status=lock.provider_status,
            provider_status_detail=lock.provider_status_detail,
            locked_at=lock.locked_at,
        )
        for lock, user in rows
    ]
