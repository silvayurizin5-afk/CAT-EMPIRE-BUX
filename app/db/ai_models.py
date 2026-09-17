from sqlalchemy import BigInteger, Boolean, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, TimestampMixin


DEFAULT_PROVIDER_ORDER = [
    "groq",
    "gemini",
    "openrouter",
    "openai",
    "anthropic",
    "xai",
    "mistral",
]


class AIConfig(Base, TimestampMixin):
    __tablename__ = "ai_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    allowed_channel_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    support_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    suggestions_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    provider_order: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: list(DEFAULT_PROVIDER_ORDER),
    )
    assistant_name: Mapped[str] = mapped_column(
        String(80), default="NEXTBUY", server_default="NEXTBUY"
    )
