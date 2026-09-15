from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class CommerceLock(Base):
    __tablename__ = "commerce_locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    source_topup_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("credit_topups.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_status: Mapped[str | None] = mapped_column(String(32))
    provider_status_detail: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_discord_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_commerce_lock_guild_user"),
        Index("ix_commerce_locks_guild_active", "guild_id", "active"),
    )
