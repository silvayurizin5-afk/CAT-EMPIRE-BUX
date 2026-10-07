"""Apply the fixed NEXTBUY public branding media.

Revision ID: 0021_fixed_nextbuy_brand_media
Revises: 0020_remove_ai_config_constraint
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021_fixed_nextbuy_brand_media"
down_revision: str | None = "0020_remove_ai_config_constraint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUPPORT_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/1555794393049333880/"
    "1557208007073472593/NEXTBUY-SUPORTE-ezgif.com-optimize.gif"
    "?backend=b2&ex=6ac6f678&is=6ac5a4f8&"
    "hm=d8b261d723003a13b45b73db41a847e79799794a51253052417b4d1c10e1f931&"
)
BRAND_THUMBNAIL_URL = (
    "https://cdn.discordapp.com/attachments/1555794393049333880/"
    "1557208095984189571/NEXTBUY-Logo-4-ezgif.com-speed.gif"
    "?backend=b2&ex=6ac6f68d&is=6ac5a50d&"
    "hm=ab9b5d623c0f3e54a299a68df1890044b1d4f99399c9f3c90882582b1020bfbd&"
)
STORE_CATALOG_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/1555794393049333880/"
    "1557208045707075674/NEXTBUY-Discord-8-ezgif.com-optimize.gif"
    "?backend=b2&ex=6ac6f681&is=6ac5a501&"
    "hm=216ea06f0aa68842eda261646e65af93816b732e55f9b1c67de7f61267a944d0&"
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE store_panel_configs "
            "SET image_url = :banner, thumbnail_url = :thumbnail"
        ),
        {"banner": STORE_CATALOG_BANNER_URL, "thumbnail": BRAND_THUMBNAIL_URL},
    )
    bind.execute(
        sa.text(
            "UPDATE game_catalog_panel_configs "
            "SET image_url = :banner, thumbnail_url = :thumbnail"
        ),
        {"banner": STORE_CATALOG_BANNER_URL, "thumbnail": BRAND_THUMBNAIL_URL},
    )
    bind.execute(
        sa.text(
            "UPDATE ticket_settings "
            "SET options = jsonb_set("
            "COALESCE(options::jsonb, '{}'::jsonb), "
            "'{banner_url}', to_jsonb(CAST(:banner AS text)), true"
            ")::json"
        ),
        {"banner": SUPPORT_BANNER_URL},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE store_panel_configs "
            "SET image_url = NULL, thumbnail_url = NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE game_catalog_panel_configs "
            "SET image_url = NULL, thumbnail_url = NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE ticket_settings "
            "SET options = (COALESCE(options::jsonb, '{}'::jsonb) - 'banner_url')::json"
        )
    )
