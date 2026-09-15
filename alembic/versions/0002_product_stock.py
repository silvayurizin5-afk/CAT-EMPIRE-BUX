"""add product stock control

Revision ID: 0002_product_stock
Revises: 0001_initial
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_product_stock"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("stock_quantity", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_product_stock_non_negative",
        "products",
        "stock_quantity IS NULL OR stock_quantity >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_product_stock_non_negative", "products", type_="check")
    op.drop_column("products", "stock_quantity")
