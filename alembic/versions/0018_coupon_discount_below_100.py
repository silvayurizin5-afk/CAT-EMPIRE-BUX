"""prevent zero-value PIX coupons

Revision ID: 0018_coupon_discount_below_100
Revises: 0017_terms_public_panel
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0018_coupon_discount_below_100"
down_revision: str | None = "0017_terms_public_panel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE store_coupons SET discount_percent = 99.99 "
        "WHERE discount_percent >= 100"
    )
    op.drop_constraint(
        "ck_store_coupon_discount_percent",
        "store_coupons",
        type_="check",
    )
    op.create_check_constraint(
        "ck_store_coupon_discount_percent",
        "store_coupons",
        "discount_percent > 0 AND discount_percent < 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_store_coupon_discount_percent",
        "store_coupons",
        type_="check",
    )
    op.create_check_constraint(
        "ck_store_coupon_discount_percent",
        "store_coupons",
        "discount_percent > 0 AND discount_percent <= 100",
    )
