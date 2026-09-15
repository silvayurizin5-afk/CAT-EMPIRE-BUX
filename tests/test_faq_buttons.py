import discord
import pytest

from app.bot.views.faq_links import AutoReplyLinkView
from app.db.faq_models import AutoReplyButton
from app.services.faq_buttons import parse_auto_reply_buttons


def test_parse_faq_buttons_accepts_https_links_and_optional_emoji() -> None:
    buttons = parse_auto_reply_buttons(
        "Suporte | https://example.com/support | 🎫\n"
        "Termos | https://example.com/terms"
    )
    assert len(buttons) == 2
    assert buttons[0].label == "Suporte"
    assert buttons[0].url == "https://example.com/support"
    assert buttons[0].emoji == "🎫"
    assert buttons[1].emoji is None


def test_parse_faq_buttons_rejects_non_https_links() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        parse_auto_reply_buttons("Site | http://example.com")
    with pytest.raises(ValueError, match="HTTPS"):
        parse_auto_reply_buttons("Site | javascript:alert(1)")


def test_parse_faq_buttons_limits_count() -> None:
    text = "\n".join(f"B{i} | https://example.com/{i}" for i in range(6))
    with pytest.raises(ValueError, match="no máximo 5"):
        parse_auto_reply_buttons(text)


def test_faq_link_view_builds_discord_link_buttons() -> None:
    rows = [
        AutoReplyButton(
            id=1,
            auto_reply_id=10,
            label="Suporte",
            url="https://example.com/support",
            emoji=None,
            sort_order=0,
        ),
        AutoReplyButton(
            id=2,
            auto_reply_id=10,
            label="Termos",
            url="https://example.com/terms",
            emoji="📄",
            sort_order=1,
        ),
    ]
    view = AutoReplyLinkView(rows)
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    assert [item.label for item in buttons] == ["Suporte", "Termos"]
    assert [item.url for item in buttons] == [
        "https://example.com/support",
        "https://example.com/terms",
    ]
    assert all(item.style is discord.ButtonStyle.link for item in buttons)
    view.stop()
