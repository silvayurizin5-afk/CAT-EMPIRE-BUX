from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


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
    return log
