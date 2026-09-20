"""Add ephemeral selection message to terms.

Revision ID: 0014_terms_ephemeral
Revises: 0013_delivery_custom
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_terms_ephemeral"
down_revision: str | None = "0013_delivery_custom"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "terms_documents",
        sa.Column(
            "ephemeral_message",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("terms_documents", "ephemeral_message")
