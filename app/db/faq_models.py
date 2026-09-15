from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AutoReplyButton(Base):
    __tablename__ = "auto_reply_buttons"

    id: Mapped[int] = mapped_column(primary_key=True)
    auto_reply_id: Mapped[int] = mapped_column(
        ForeignKey("auto_replies.id", ondelete="CASCADE"),
        index=True,
    )
    label: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    emoji: Mapped[str | None] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("auto_reply_id", "sort_order", name="uq_auto_reply_button_order"),
        CheckConstraint("sort_order BETWEEN 0 AND 4", name="ck_auto_reply_button_sort_order"),
    )
