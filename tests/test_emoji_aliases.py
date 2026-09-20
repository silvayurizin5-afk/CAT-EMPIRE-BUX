from types import SimpleNamespace

from app.bot.emoji import resolve_guild_emoji_aliases


class FakeEmoji:
    def __init__(self, name: str, emoji_id: int, *, animated: bool = False) -> None:
        self.name = name
        self.id = emoji_id
        self.animated = animated

    def __str__(self) -> str:
        prefix = "a" if self.animated else ""
        return f"<{prefix}:{self.name}:{self.id}>"


def test_resolve_guild_emoji_aliases_replaces_server_emoji_names() -> None:
    guild = SimpleNamespace(
        emojis=[
            FakeEmoji("NextBuy", 123456789012345678),
            FakeEmoji("SETA2", 223456789012345678, animated=True),
        ]
    )
    text = resolve_guild_emoji_aliases(":NextBuy: Loja :SETA2:", guild)
    assert text == (
        "<:NextBuy:123456789012345678> Loja "
        "<a:SETA2:223456789012345678>"
    )


def test_resolve_guild_emoji_aliases_preserves_mentions_and_unknown_names() -> None:
    guild = SimpleNamespace(emojis=[FakeEmoji("NextBuy", 123456789012345678)])
    text = resolve_guild_emoji_aliases(
        "<:NextBuy:123456789012345678> :nao_existe:",
        guild,
    )
    assert text == "<:NextBuy:123456789012345678> :nao_existe:"
