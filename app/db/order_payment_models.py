from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class OrderPayment(Base):
    __tablename__ = "order_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), default="stripe", server_default="stripe")
    status: Mapped[str] = mapped_column(String(24), default="creating", server_default="creating")
    checkout_session_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
