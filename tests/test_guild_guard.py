import importlib

from app.core.config import settings
import app.core.guild_guard as guild_guard


def test_bot_is_locked_to_official_store_guild() -> None:
    assert guild_guard.STORE_GUILD_ID == 1549240664149991424
    assert guild_guard.is_store_guild(1549240664149991424)
    assert not guild_guard.is_store_guild(1)
    assert not guild_guard.is_store_guild(None)


def test_bot_uses_configured_guild_id() -> None:
    original = settings.discord_guild_id
    try:
        settings.discord_guild_id = 987654321
        importlib.reload(guild_guard)
        assert guild_guard.STORE_GUILD_ID == 987654321
        assert guild_guard.is_store_guild(987654321)
        assert not guild_guard.is_store_guild(1549240664149991424)
    finally:
        settings.discord_guild_id = original
        importlib.reload(guild_guard)
