from types import SimpleNamespace

import pytest

from app.bot.emoji import emoji_display_value, select_option_emoji
from app.bot.views.terms_public import normalize_http_url, term_option_description


class FakeEmoji:
    def __init__(self, name: str, emoji_id: int, *, animated: bool = False) -> None:
        self.name = name
        self.id = emoji_id
        self.animated = animated

    def __str__(self) -> str:
        prefix = "a" if self.animated else ""
        return f"<{prefix}:{self.name}:{self.id}>"


def test_terms_urls_accept_http_and_https_only() -> None:
    assert normalize_http_url("https://example.com/banner.gif") == (
        "https://example.com/banner.gif"
    )
    assert normalize_http_url("") is None
    with pytest.raises(ValueError):
        normalize_http_url("javascript:alert(1)")


def test_term_option_description_prefers_configured_summary() -> None:
    terms = SimpleNamespace(
        summary="Veja prazos, condições e regras.",
        content="Conteúdo completo",
        version=2,
    )
    assert term_option_description(terms) == "Veja prazos, condições e regras."


def test_configurable_emoji_accepts_alias_id_and_animated_custom() -> None:
    guild = SimpleNamespace(
        emojis=[
            FakeEmoji("NEXT", 123456789012345678),
            FakeEmoji("ANIM", 223456789012345678, animated=True),
        ]
    )

    alias = select_option_emoji(":NEXT:", guild)
    by_id = select_option_emoji("223456789012345678", guild)
    animated = select_option_emoji("<a:ANIM:223456789012345678>", guild)

    assert alias is not None and alias.id == 123456789012345678
    assert by_id is not None and by_id.animated is True
    assert animated is not None and animated.animated is True
    assert emoji_display_value("NEXT", guild) == "<:NEXT:123456789012345678>"
