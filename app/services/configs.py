from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GuildConfig


async def get_or_create_guild_config(session: AsyncSession, guild_id: int) -> GuildConfig:
    statement = (
        insert(GuildConfig)
        .values(guild_id=guild_id)
        .on_conflict_do_nothing(index_elements=[GuildConfig.guild_id])
        .returning(GuildConfig.id)
    )
    config_id = await session.scalar(statement)
    if config_id is None:
        config_id = await session.scalar(
            select(GuildConfig.id).where(GuildConfig.guild_id == guild_id)
        )
    if config_id is None:
        raise RuntimeError("Falha ao criar configuração do servidor")
    config = await session.get(GuildConfig, config_id)
    if config is None:
        raise RuntimeError("Configuração do servidor não encontrada")
    return config
