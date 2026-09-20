from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from app.bot.views.support import SupportControls, SupportPanel
from app.bot.views.support_admin import SupportSettingsModal
from app.bot.workflows import support, ticket_archive, tickets
from app.bot.workflows.transcripts import _component_media, _safe_url, render_channel_transcript
from app.core.guild_guard import STORE_GUILD_ID
from app.db.ticket_models import SupportTicket
from app.services.support_tickets import SupportOptions, validate_options


@pytest.mark.parametrize(
    "patch",
    [
        {"max_open": 0},
        {"max_open": 11},
        {"max_open": True},
        {"cooldown_seconds": -1},
        {"enabled": "yes"},
        {"color": "wrong"},
        {"banner_url": "javascript:alert(1)"},
        {"banner_url": "https://user:password@example.com/image"},
        {"panel_title": " "},
        {"unknown": 1},
        {"closed_category_id": -1},
    ],
)
def test_invalid_settings_rejected(patch):
    with pytest.raises(ValueError):
        validate_options(patch)


def test_configuration_defaults_protect_transcripts():
    assert validate_options({}).transcript_required
    assert validate_options({"color": "#00ffFF"}).color == "00ffFF"


async def test_views_survive_restart_and_modal_limits():
    for view in (SupportPanel(), SupportControls(uuid4())):
        assert view.is_persistent()
        ids = [i.custom_id for i in view.walk_children() if isinstance(i, discord.ui.Button)]
        assert len(set(ids)) == len(ids)
        assert all(len(value) <= 100 for value in ids)
    for section in ("text", "visual", "policy"):
        modal = SupportSettingsModal(SupportOptions(), section)
        assert len(modal.children) <= 5
        assert all(len(i.label) <= 45 for i in modal.children)


def channel_mock():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.name, channel.topic = 123, "suporte-cliente", None
    channel.guild = MagicMock()
    channel.guild.filesize_limit = 10_000_000
    channel.guild.name = "Loja"
    channel.guild.id = STORE_GUILD_ID
    channel.overwrites_for.return_value = discord.PermissionOverwrite(
        view_channel=True, send_messages=True
    )
    channel.set_permissions, channel.edit, channel.delete, channel.send = (
        AsyncMock() for _ in range(4)
    )
    return channel


async def test_archive_requires_destination():
    channel = channel_mock()
    with pytest.raises(ValueError, match="Configure"):
        await ticket_archive.archive_ticket(channel, None)
    assert await ticket_archive.archive_ticket(channel, None, required=False) is None
    with pytest.raises(ValueError, match="próprio"):
        await ticket_archive.archive_ticket(channel, channel.id)


async def test_archive_compresses_without_truncating(monkeypatch):
    import gzip

    channel, target = channel_mock(), channel_mock()
    channel.guild.filesize_limit = 200
    target.send.return_value = SimpleNamespace(jump_url="https://discord.com/channels/1/2/3")
    channel.guild.get_channel.return_value = target
    data = b"<html>" + b"x" * 10000 + b"</html>"
    monkeypatch.setattr(ticket_archive, "render_channel_transcript", AsyncMock(return_value=data))
    assert await ticket_archive.archive_ticket(channel, 456)
    file = target.send.call_args.kwargs["file"]
    assert file.filename.endswith(".html.gz")
    assert gzip.decompress(file.fp.getvalue()) == data


async def test_archive_oversize_does_not_upload(monkeypatch):
    import os

    channel = channel_mock()
    channel.guild.filesize_limit = 100
    channel.guild.get_channel.return_value = channel_mock()
    monkeypatch.setattr(
        ticket_archive, "render_channel_transcript", AsyncMock(return_value=os.urandom(1000))
    )
    with pytest.raises(ValueError, match="limite"):
        await ticket_archive.archive_ticket(channel, 456)
    channel.guild.get_channel.return_value.send.assert_not_awaited()


def setup_operation(monkeypatch, *, state="open", staff=True, owner=222):
    channel = channel_mock()
    interaction = MagicMock()
    interaction.guild = channel.guild
    interaction.guild.get_channel.return_value = channel
    interaction.user.id = owner
    interaction.user.mention = f"<@{owner}>"
    interaction.guild.get_member.return_value = MagicMock(spec=discord.Member)
    ticket = SupportTicket(
        id=uuid4(),
        guild_id=STORE_GUILD_ID,
        customer_id=222,
        channel_id=channel.id,
        subject="Ajuda",
        description="Detalhes",
        state=state,
        participants=[],
    )
    session = MagicMock()
    session.begin.return_value = MagicMock(
        __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)
    )
    session.scalar = AsyncMock(
        side_effect=[ticket, SimpleNamespace(transcript_channel_id=456, ticket_category_id=0)]
    )
    factory = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)
        )
    )
    monkeypatch.setattr(support, "SessionLocal", factory)
    monkeypatch.setattr(support, "can_support", AsyncMock(return_value=staff))
    monkeypatch.setattr(support, "get_support_options", AsyncMock(return_value=SupportOptions()))
    monkeypatch.setattr(support, "ticket_event", AsyncMock())
    monkeypatch.setattr(support, "ticket_lock", AsyncMock())
    monkeypatch.setattr(support, "check_open_limit", AsyncMock())
    return interaction, channel, ticket


async def test_failed_archive_preserves_ticket_and_restores_write_access(monkeypatch):
    interaction, channel, ticket = setup_operation(monkeypatch)
    monkeypatch.setattr(
        support, "archive_ticket", AsyncMock(side_effect=ValueError("upload failed"))
    )
    with pytest.raises(ValueError, match="upload failed"):
        await support.operate_ticket(interaction, ticket.id, "delete", reason="Resolvido")
    channel.delete.assert_not_awaited()
    channel.edit.assert_not_awaited()
    assert ticket.state == "open"
    assert channel.set_permissions.await_args_list[0].kwargs["overwrite"].send_messages is False
    assert channel.set_permissions.await_args_list[-1].kwargs["overwrite"].send_messages is True


async def test_close_and_reopen_preserve_private_permissions(monkeypatch):
    interaction, channel, ticket = setup_operation(monkeypatch)
    monkeypatch.setattr(
        support, "archive_ticket", AsyncMock(return_value="https://discord.com/archive")
    )
    await support.operate_ticket(interaction, ticket.id, "close", reason="Resolvido")
    assert ticket.state == "closed"
    assert ticket.close_reason == "Resolvido"
    assert ticket.transcript_url == "https://discord.com/archive"
    channel.delete.assert_not_awaited()
    interaction, channel, ticket = setup_operation(monkeypatch, state="closed")
    await support.operate_ticket(interaction, ticket.id, "reopen")
    assert ticket.state == "open"
    assert channel.set_permissions.call_args.kwargs["overwrite"].view_channel is True
    assert channel.set_permissions.call_args.kwargs["overwrite"].send_messages is True


async def test_delete_uploads_before_deleting(monkeypatch):
    interaction, channel, ticket = setup_operation(monkeypatch)
    events = []

    async def archive(*args, **kwargs):
        events.append("archive")
        return "https://discord.com/archive"

    async def delete(**kwargs):
        events.append("delete")

    monkeypatch.setattr(support, "archive_ticket", archive)
    channel.delete.side_effect = delete
    await support.operate_ticket(interaction, ticket.id, "delete", reason="Resolvido")
    assert events == ["archive", "delete"]
    assert ticket.state == "deleted"


@pytest.mark.parametrize("action", ["delete", "reopen", "claim", "transcript", "add"])
async def test_customer_cannot_perform_staff_actions(monkeypatch, action):
    interaction, channel, ticket = setup_operation(monkeypatch, staff=False)
    with pytest.raises(ValueError, match="permissão"):
        await support.operate_ticket(interaction, ticket.id, action, reason="Resolvido")
    channel.delete.assert_not_awaited()
    channel.set_permissions.assert_not_awaited()


async def test_other_customer_cannot_close(monkeypatch):
    interaction, _, ticket = setup_operation(monkeypatch, staff=False, owner=333)
    with pytest.raises(ValueError, match="permissão"):
        await support.operate_ticket(interaction, ticket.id, "close", reason="Resolvido")


async def test_claim_cannot_steal_another_staff_ticket(monkeypatch):
    interaction, _, ticket = setup_operation(monkeypatch)
    ticket.assignee_id = 333
    with pytest.raises(ValueError, match="outro atendente"):
        await support.operate_ticket(interaction, ticket.id, "claim")


async def test_order_delete_also_preserves_channel_on_archive_failure(monkeypatch):
    channel = channel_mock()
    order = SimpleNamespace(id=uuid4(), guild_id=STORE_GUILD_ID, ticket_channel_id=channel.id)
    monkeypatch.setattr(tickets, "_load_order", AsyncMock(return_value=(order, None, [], None)))
    monkeypatch.setattr(
        tickets, "_save_ticket_transcript", AsyncMock(side_effect=ValueError("archive failed"))
    )
    channel.guild.get_channel.return_value = channel
    ok, message = await tickets.delete_order_ticket(
        channel.guild, order_id=order.id, actor_discord_id=222
    )
    assert not ok
    assert "preservado" in message
    channel.delete.assert_not_awaited()


async def test_transcript_escapes_markup_and_includes_v2_media_and_reason():
    channel = channel_mock()
    channel.name = '<script>alert("x")</script>'
    message = SimpleNamespace(
        author=SimpleNamespace(
            display_name="<img>",
            display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatar.png"),
            bot=False,
        ),
        created_at=datetime.now(UTC),
        clean_content="<script>alert(1)</script>",
        components=[
            SimpleNamespace(media=SimpleNamespace(url="https://cdn.discordapp.com/image.png"))
        ],
        embeds=[],
        attachments=[],
        jump_url="https://discord.com/channels/1/2/3",
    )

    async def history(**kwargs):
        yield message

    channel.history = history
    html = (
        await render_channel_transcript(channel, summary={"Motivo": "<script>bad</script>"})
    ).decode()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'src="https://cdn.discordapp.com/image.png"' in html
    assert "Content-Security-Policy" in html
    assert "Motivo:" in html
    assert "Mensagens: 1" in html


def test_transcript_rejects_unsafe_media_urls():
    assert _safe_url("javascript:alert(1)") is None
    assert (
        _component_media([SimpleNamespace(media=SimpleNamespace(url="javascript:alert(1)"))]) == ""
    )
