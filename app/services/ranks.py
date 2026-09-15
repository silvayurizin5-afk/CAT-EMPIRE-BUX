from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.db.models import RankTier


async def list_all_rank_tiers(session: AsyncSession, *, guild_id: int) -> list[RankTier]:
    return list(
        (
            await session.scalars(
                select(RankTier)
                .where(RankTier.guild_id == guild_id)
                .order_by(RankTier.active.desc(), RankTier.min_spend.asc(), RankTier.id.asc())
            )
        ).all()
    )


async def list_rank_tiers(session: AsyncSession, *, guild_id: int) -> list[RankTier]:
    return list(
        (
            await session.scalars(
                select(RankTier)
                .where(RankTier.guild_id == guild_id, RankTier.active.is_(True))
                .order_by(RankTier.min_spend.asc(), RankTier.id.asc())
            )
        ).all()
    )


async def set_rank_tier_active(
    session: AsyncSession,
    *,
    tier: RankTier,
    active: bool,
) -> RankTier:
    tier.active = active
    await session.flush()
    return tier


async def upsert_rank_tier(
    session: AsyncSession,
    *,
    guild_id: int,
    name: str,
    min_spend: Decimal,
    role_id: int,
    dm_message: str = "",
) -> RankTier:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Nome da faixa é obrigatório")
    threshold = money(min_spend)
    if threshold < 0:
        raise ValueError("Meta de gasto não pode ser negativa")

    statement = (
        insert(RankTier)
        .values(
            guild_id=guild_id,
            name=clean_name,
            min_spend=threshold,
            role_id=role_id,
            dm_message=dm_message.strip(),
            active=True,
        )
        .on_conflict_do_update(
            constraint="uq_rank_tier_guild_role",
            set_={
                "name": clean_name,
                "min_spend": threshold,
                "dm_message": dm_message.strip(),
                "active": True,
            },
        )
        .returning(RankTier.id)
    )
    tier_id = await session.scalar(statement)
    if tier_id is None:
        raise RuntimeError("Falha ao salvar faixa")
    tier = await session.get(RankTier, tier_id)
    if tier is None:
        raise RuntimeError("Faixa não encontrada depois de salvar")
    return tier


def choose_rank_tier(tiers: list[RankTier], total_spent: Decimal) -> RankTier | None:
    eligible = [tier for tier in tiers if money(tier.min_spend) <= money(total_spent)]
    if not eligible:
        return None
    return max(eligible, key=lambda tier: (money(tier.min_spend), tier.id))
