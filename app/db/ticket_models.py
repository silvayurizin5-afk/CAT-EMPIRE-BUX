from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, TimestampMixin


class TicketSettings(Base, TimestampMixin):
    __tablename__ = "ticket_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
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
