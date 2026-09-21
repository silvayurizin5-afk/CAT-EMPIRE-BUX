from __future__ import annotations

from typing import Any

import discord
from sqlalchemy import select

from app.bot.components_v2 import CardLayout, add_select_row
from app.bot.emoji import emoji_display_value, resolve_guild_emoji_aliases, select_option_emoji
from app.core.money import money
from app.db.models import Product
from app.db.session import SessionLocal
from app.services.calculator import format_brl
from app.services.store_panel import get_or_create_store_panel

GAME_PRODUCT_TYPES = {"game", "games", "jogo", "jogos"}


def normalize_product_type(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_game_product(product: Product) -> bool:
    return normalize_product_type(product.product_type) in GAME_PRODUCT_TYPES


def product_store_metadata(product: Product) -> dict[str, Any]:
    return dict(product.metadata_json or {})


def product_store_option_description(product: Product) -> str:
    metadata = product_store_metadata(product)
    custom = str(metadata.get("store_selector_description") or "").strip()
    if custom:
        return custom[:100]

    if is_game_product(product):
        for line in (product.description or "").splitlines():
            cleaned = line.strip().lstrip("#>•-* ").strip()
            if cleaned:
                return cleaned[:100]
        return "Ver produtos, itens, contas e opções deste jogo"

    price = (
        "Indisponível"
        if product.price_credits is None
        else format_brl(money(product.price_credits))
    )
    show_stock = metadata.get("store_show_stock", True)
    if isinstance(show_stock, str):
        show_stock = show_stock.strip().lower() not in {"0", "false", "nao", "não", "no"}
    if not bool(show_stock):
        return price[:100]

    stock = "∞" if product.stock_quantity is None else str(product.stock_quantity)
    return f"{price} • estoque {stock}"[:100]


def game_panel_value(product: Product, key: str, default: str = "") -> str:
    value = product_store_metadata(product).get(key)
    return str(value).strip() if value not in (None, "") else default


def game_panel_selected_ids(product: Product) -> list[int]:
    raw = product_store_metadata(product).get("game_panel_product_ids") or []
    result: list[int] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in result:
            result.append(product_id)
    return result[:24]


def game_lookup_name(product: Product) -> str:
    return (product.game_name or product.name).strip()


async def list_game_subpanel_products(
    session,
    *,
    guild_id: int,
    game_product: Product,
    limit: int = 24,
) -> list[Product]:
    selected_ids = game_panel_selected_ids(game_product)
    rows = list(
        (
            await session.scalars(
                select(Product)
                .where(
                    Product.guild_id == guild_id,
                    Product.id != game_product.id,
                    Product.active.is_(True),
                    (Product.stock_quantity.is_(None) | (Product.stock_quantity > 0)),
                )
                .order_by(Product.sort_order, Product.name)
                .limit(100)
            )
        ).all()
    )
    rows = [item for item in rows if not is_game_product(item)]

    if selected_ids:
        by_id = {item.id: item for item in rows}
        return [by_id[item_id] for item_id in selected_ids if item_id in by_id][:limit]

    game_name = game_lookup_name(game_product).casefold()
    matched = [
        item
        for item in rows
        if (item.game_name or "").strip().casefold() == game_name
    ]
    return matched[:limit]


class GameProductSelect(discord.ui.Select):
    def __init__(
        self,
        products: list[Product],
        *,
        placeholder: str,
        guild: discord.Guild | None,
    ) -> None:
        options = [
            discord.SelectOption(
                label=product.name[:100],
                value=str(product.id),
                description=product_store_option_description(product),
                emoji=select_option_emoji(product.emoji, guild),
            )
            for product in products[:24]
        ]
        if not options:
            options = [
                discord.SelectOption(
                    label="Nenhum produto disponível",
                    value="none",
                    description="Configure produtos para este jogo no painel administrativo",
                )
            ]
        super().__init__(
            placeholder=(placeholder or "Selecione uma opção")[:100],
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if self.values[0] == "none":
            await interaction.response.send_message(
                "Nenhum produto disponível neste jogo agora.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        product_id = int(self.values[0])
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, product_id)
            config = await get_or_create_store_panel(session, interaction.guild.id)

        if (
            product is None
            or product.guild_id != interaction.guild.id
            or not product.active
            or is_game_product(product)
            or product.price_credits is None
            or (product.stock_quantity is not None and product.stock_quantity <= 0)
        ):
            await interaction.edit_original_response(
                content="Produto indisponível.",
                view=None,
            )
            return

        from app.bot.views.store_panel import build_product_checkout_card

        await interaction.edit_original_response(
            content=None,
            view=build_product_checkout_card(
                config,
                product,
                owner_id=interaction.user.id,
            ),
        )


class GameSubPanelLayout(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        game_product: Product,
        products: list[Product],
        owner_id: int,
        guild: discord.Guild | None,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        metadata = product_store_metadata(game_product)

        emoji = emoji_display_value(game_product.emoji, guild)
        default_title = f"{emoji} {game_product.name}".strip()
        title = resolve_guild_emoji_aliases(
            str(metadata.get("game_panel_title") or default_title),
            guild,
        )
        description = resolve_guild_emoji_aliases(
            str(
                metadata.get("game_panel_description")
                or game_product.description
                or f"Escolha o que deseja comprar em {game_product.name}."
            ),
            guild,
        )
        footer = resolve_guild_emoji_aliases(
            str(metadata.get("game_panel_footer_text") or ""),
            guild,
        )
        count_label = resolve_guild_emoji_aliases(
            str(metadata.get("game_panel_product_count_label") or ""),
            guild,
        )
        placeholder = str(
            metadata.get("game_panel_placeholder") or f"Selecione uma opção de {game_product.name}"
        )
        image_url = str(metadata.get("game_panel_image_url") or "").strip() or None
        thumbnail_url = str(metadata.get("game_panel_thumbnail_url") or "").strip() or None

        lines: list[str] = []
        if count_label:
            lines.append(f"**{count_label}:** `{len(products)}`")

        card = CardLayout(
            title=title,
            description=description,
            lines=lines,
            footer=footer or None,
            accent_colour=0x7B2CBF,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(
            self.container,
            GameProductSelect(
                products,
                placeholder=placeholder,
                guild=guild,
            ),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Este subpainel pertence a outro cliente.",
            ephemeral=True,
        )
        return False
