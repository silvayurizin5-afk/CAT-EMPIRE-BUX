"""add configurable FAQ link buttons

Revision ID: 0008_faq_buttons
Revises: 0007_commerce_locks
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_faq_buttons"
down_revision: str | None = "0007_commerce_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auto_reply_buttons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "auto_reply_id",
            sa.Integer(),
            sa.ForeignKey("auto_replies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("emoji", sa.String(length=128), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "auto_reply_id",
            "sort_order",
            name="uq_auto_reply_button_order",
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 0 AND 4",
            name="ck_auto_reply_button_sort_order",
        ),
    )
    op.create_index(
        "ix_auto_reply_buttons_auto_reply_id",
        "auto_reply_buttons",
        ["auto_reply_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_auto_reply_buttons_auto_reply_id", table_name="auto_reply_buttons")
    op.drop_table("auto_reply_buttons")
