from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product
from app.db.store_models import GameCatalogPanelConfig


_GAME_TYPES = {"game", "games", "jogo", "jogos"}


def _is_game_product(product: Product) -> bool:
    value = str(product.product_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    return value in _GAME_TYPES


async def get_or_create_game_catalog(
    session: AsyncSession,
    guild_id: int,
) -> GameCatalogPanelConfig:
    statement = (
        insert(GameCatalogPanelConfig)
        .values(guild_id=guild_id, selected_game_ids=[])
        .on_conflict_do_nothing(index_elements=[GameCatalogPanelConfig.guild_id])
        .returning(GameCatalogPanelConfig.id)
    )
    config_id = await session.scalar(statement)
    if config_id is None:
        config_id = await session.scalar(
            select(GameCatalogPanelConfig.id).where(
                GameCatalogPanelConfig.guild_id == guild_id
            )
        )
    if config_id is None:
        raise RuntimeError("Falha ao criar configuração do catálogo de jogos")
    config = await session.get(GameCatalogPanelConfig, config_id)
    if config is None:
        raise RuntimeError("Configuração do catálogo de jogos não encontrada")
    return config


async def list_catalog_games(
    session: AsyncSession,
    *,
    guild_id: int,
    config: GameCatalogPanelConfig | None = None,
    limit: int = 25,
) -> list[Product]:
    config = config or await get_or_create_game_catalog(session, guild_id)
    rows = list(
        (
            await session.scalars(
                select(Product)
                .where(
                    Product.guild_id == guild_id,
                    Product.active.is_(True),
                )
                .order_by(Product.sort_order, Product.name)
                .limit(200)
            )
        ).all()
    )
    games = [product for product in rows if _is_game_product(product)]
    selected_ids = [int(value) for value in (config.selected_game_ids or [])]
    if selected_ids:
        by_id = {product.id: product for product in games}
        games = [by_id[value] for value in selected_ids if value in by_id]
    return games[: max(1, min(limit, 25))]


async def list_all_game_products(
    session: AsyncSession,
    *,
    guild_id: int,
) -> list[Product]:
    rows = list(
        (
            await session.scalars(
                select(Product)
                .where(Product.guild_id == guild_id)
                .order_by(Product.sort_order, Product.name)
            )
        ).all()
    )
    return [product for product in rows if _is_game_product(product)]


async def save_catalog_game_selection(
    session: AsyncSession,
    *,
    config: GameCatalogPanelConfig,
    game_ids: list[int],
) -> GameCatalogPanelConfig:
    unique_ids = list(dict.fromkeys(int(value) for value in game_ids))[:25]
    if unique_ids:
        candidates = list(
            (
                await session.scalars(
                    select(Product).where(
                        Product.guild_id == config.guild_id,
                        Product.id.in_(unique_ids),
                    )
                )
            ).all()
        )
        valid = {product.id for product in candidates if _is_game_product(product)}
        unique_ids = [value for value in unique_ids if value in valid]
    config.selected_game_ids = unique_ids
    await session.flush()
    return config
