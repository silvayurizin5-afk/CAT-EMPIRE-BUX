from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Uuid
from sqlalchemy import BigInteger, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, TimestampMixin


class TicketSettings(Base, TimestampMixin):
    __tablename__ = "ticket_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    title_template: Mapped[str] = mapped_column(
        String(160),
        default="Pedido {order}",
        server_default="Pedido {order}",
    )
    instruction_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "{customer}, seu pedido `{order}` foi confirmado. "
            "A equipe vai continuar o atendimento por aqui."
        ),
        server_default=(
            "{customer}, seu pedido `{order}` foi confirmado. "
            "A equipe vai continuar o atendimento por aqui."
        ),
    )


# General support is independent of the financial order lifecycle.


class SupportTicket(Base, TimestampMixin):
    __tablename__ = "support_tickets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    customer_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    subject: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    assignee_id: Mapped[int | None] = mapped_column(BigInteger)
    participants: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    close_reason: Mapped[str | None] = mapped_column(String(1000))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_url: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("state IN ('open', 'closed', 'deleted')", name="ck_support_ticket_state"),
        Index("ix_support_ticket_customer_state", "guild_id", "customer_id", "state"),
    )
