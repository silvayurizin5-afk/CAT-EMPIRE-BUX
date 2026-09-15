from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class TopUpNotification(Base):
    __tablename__ = "topup_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    topup_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("credit_topups.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_topup_notification_attempts_non_negative"),
        Index("ix_topup_notifications_pending", "sent_at", "claimed_at"),
    )
