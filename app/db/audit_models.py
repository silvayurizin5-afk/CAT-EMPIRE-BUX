from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AuditDelivery(Base):
    __tablename__ = "audit_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_log_id: Mapped[int] = mapped_column(
        ForeignKey("audit_logs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
