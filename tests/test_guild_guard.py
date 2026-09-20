from app.core.guild_guard import STORE_GUILD_ID, is_store_guild


def test_bot_is_locked_to_official_store_guild() -> None:
    assert STORE_GUILD_ID == 1549240664149991424
    assert is_store_guild(1549240664149991424)
    assert not is_store_guild(1)
    assert not is_store_guild(None)
