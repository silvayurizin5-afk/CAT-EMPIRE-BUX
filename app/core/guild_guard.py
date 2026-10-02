from app.core.config import settings


DEFAULT_STORE_GUILD_ID = 1549240664149991424
STORE_GUILD_ID = settings.discord_guild_id or DEFAULT_STORE_GUILD_ID


def is_store_guild(guild_id: int | None) -> bool:
    return guild_id == STORE_GUILD_ID
