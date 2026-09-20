"""Configurable support tickets and durable lifecycle state."""

import sqlalchemy as sa
from alembic import op

revision = "0015_support_tickets"
down_revision = "0014_terms_ephemeral"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ticket_settings", sa.Column("options", sa.JSON(), nullable=False, server_default="{}")
    )
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), unique=True),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assignee_id", sa.BigInteger()),
        sa.Column("participants", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("close_reason", sa.String(1000)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("transcript_url", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('open', 'closed', 'deleted')", name="ck_support_ticket_state"
        ),
    )
    op.create_index("ix_support_tickets_guild_id", "support_tickets", ["guild_id"])
    op.create_index(
        "ix_support_ticket_customer_state", "support_tickets", ["guild_id", "customer_id", "state"]
    )


def downgrade():
    op.drop_table("support_tickets")
    op.drop_column("ticket_settings", "options")
