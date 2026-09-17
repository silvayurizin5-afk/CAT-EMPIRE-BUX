from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ai_models import AIConfig, DEFAULT_PROVIDER_ORDER

LEGACY_DEFAULT_PROVIDER_ORDER = [
    "openai",
    "anthropic",
    "gemini",
    "xai",
    "mistral",
    "groq",
    "openrouter",
]


async def get_or_create_ai_config(session: AsyncSession, guild_id: int) -> AIConfig:
    statement = (
        insert(AIConfig)
        .values(guild_id=guild_id, provider_order=list(DEFAULT_PROVIDER_ORDER))
        .on_conflict_do_nothing(index_elements=[AIConfig.guild_id])
        .returning(AIConfig.id)
    )
    config_id = await session.scalar(statement)
    if config_id is None:
        config_id = await session.scalar(select(AIConfig.id).where(AIConfig.guild_id == guild_id))
    if config_id is None:
        raise RuntimeError("Falha ao criar configuração da IA")
    config = await session.get(AIConfig, config_id)
    if config is None:
        raise RuntimeError("Configuração da IA não encontrada")

    # Configurações criadas antes da ordem free-first ficaram persistidas no banco.
    # Só migramos a ordem padrão antiga; uma ordem personalizada pelo administrador é preservada.
    if list(config.provider_order or []) == LEGACY_DEFAULT_PROVIDER_ORDER:
        config.provider_order = list(DEFAULT_PROVIDER_ORDER)
        await session.flush()

    return config


def normalize_channel_ids(values: list[int]) -> list[int]:
    return list(dict.fromkeys(int(value) for value in values if int(value) > 0))[:25]


async def set_ai_channels(
    session: AsyncSession,
    *,
    config: AIConfig,
    channel_ids: list[int],
) -> AIConfig:
    config.allowed_channel_ids = normalize_channel_ids(channel_ids)
    await session.flush()
    return config


async def set_support_channel(
    session: AsyncSession,
    *,
    config: AIConfig,
    channel_id: int | None,
) -> AIConfig:
    config.support_channel_id = int(channel_id) if channel_id else None
    await session.flush()
    return config


async def set_suggestions_channel(
    session: AsyncSession,
    *,
    config: AIConfig,
    channel_id: int | None,
) -> AIConfig:
    config.suggestions_channel_id = int(channel_id) if channel_id else None
    await session.flush()
    return config
