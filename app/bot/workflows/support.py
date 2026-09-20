import logging
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_support
from app.bot.workflows.ticket_archive import archive_ticket
from app.core.guild_guard import is_store_guild
from app.db.models import GuildConfig
from app.db.session import SessionLocal
from app.db.ticket_models import SupportTicket
from app.services.audit import write_audit_log
from app.services.support_tickets import check_open_limit, get_support_options, ticket_lock

logger = logging.getLogger(__name__)


async def permission_target(interaction, user_id):
    return (
        interaction.guild.get_member(user_id)
        or interaction.client.get_user(user_id)
        or await interaction.client.fetch_user(user_id)
    )


@asynccontextmanager
async def freeze_customer_messages(interaction, channel, ticket, *, enabled, writable=False):
    originals = []
    try:
        if enabled:
            for user_id in {ticket.customer_id, *(ticket.participants or [])}:
                target = await permission_target(interaction, user_id)
                original = channel.overwrites_for(target)
                originals.append((target, original))
                overwrite = discord.PermissionOverwrite.from_pair(*original.pair())
                overwrite.send_messages = writable
                overwrite.send_messages_in_threads = writable
                await channel.set_permissions(target, overwrite=overwrite)
        yield
    except Exception:
        for target, overwrite in originals:
            try:
                await channel.set_permissions(target, overwrite=overwrite)
            except discord.HTTPException:
                logger.exception("Falha ao restaurar permissão no canal %s", channel.id)
        raise


async def ticket_event(session, ticket, actor_id, action, **details):
    await write_audit_log(
        session,
        guild_id=ticket.guild_id,
        actor_discord_id=actor_id,
        action=f"ticket.{action}",
        target_type="ticket",
        target_id=str(ticket.id),
        details={
            "customer_discord_id": ticket.customer_id,
            "subject": ticket.subject,
            "channel_id": ticket.channel_id,
            "assignee_id": ticket.assignee_id,
            **details,
        },
    )


async def open_support_ticket(interaction: discord.Interaction, subject: str, description: str):
    from app.bot.views.support import SupportControls

    guild = interaction.guild
    if (
        guild is None
        or not is_store_guild(guild.id)
        or not isinstance(interaction.user, discord.Member)
    ):
        raise ValueError("Este atendimento está disponível apenas no servidor oficial.")
    subject, description = subject.strip(), description.strip()
    if not 2 <= len(subject) <= 100 or not 2 <= len(description) <= 1800:
        raise ValueError("Informe assunto (2–100 caracteres) e descrição (2–1800 caracteres).")
    channel = None
    try:
        async with SessionLocal() as session, session.begin():
            await ticket_lock(session, "customer", guild.id, interaction.user.id)
            options = await get_support_options(session, guild.id)
            if not options.enabled:
                raise ValueError("Novos tickets estão temporariamente desativados.")
            await check_open_limit(session, guild.id, interaction.user.id, options)
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == guild.id)
            )
            if not config or not (config.support_role_id or config.admin_role_id):
                raise ValueError("Configure o cargo de suporte ou administrador no /admin.")
            category = guild.get_channel(config.ticket_category_id or 0)
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError("Configure uma categoria válida para tickets no /admin.")
            if options.transcript_required and not config.transcript_channel_id:
                raise ValueError("Configure o canal de transcripts no /admin.")
            member = interaction.user
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                ),
            }
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_roles=True,
                    attach_files=True,
                )
            staff_roles = [
                guild.get_role(r) for r in (config.support_role_id, config.admin_role_id) if r
            ]
            if not any(staff_roles) or guild.default_role in staff_roles:
                raise ValueError("Configure um cargo de equipe válido; @everyone não é permitido.")
            for role in staff_roles:
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                    )
            ticket = SupportTicket(
                guild_id=guild.id, customer_id=member.id, subject=subject, description=description
            )
            session.add(ticket)
            await session.flush()
            slug = re.sub(
                r"[^a-z0-9]+",
                "-",
                unicodedata.normalize("NFKD", member.display_name)
                .encode("ascii", "ignore")
                .decode()
                .lower(),
            ).strip("-")
            channel = await guild.create_text_channel(
                name=f"suporte-{slug or 'cliente'}"[:90],
                category=category,
                overwrites=overwrites,
                topic=f"NEXTBUY support={ticket.id} customer={member.id}",
                reason="NEXTBUY: abertura de atendimento",
            )
            ticket.channel_id = channel.id
            await channel.send(
                view=SupportControls(
                    ticket.id,
                    subject=subject,
                    description=description,
                    welcome=options.welcome.replace("{customer}", member.mention)[:1800],
                    color=int(options.color, 16),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await ticket_event(session, ticket, member.id, "open")
        return channel
    except Exception:
        if channel is not None:
            try:
                await channel.delete(reason="NEXTBUY: desfazendo abertura incompleta")
            except discord.HTTPException:
                logger.exception("Falha ao remover canal de abertura incompleta %s", channel.id)
        raise


async def operate_ticket(
    interaction: discord.Interaction,
    ticket_id: UUID,
    action: str,
    *,
    reason: str = "",
    participant: discord.Member | None = None,
) -> str:
    guild = interaction.guild
    if guild is None or not is_store_guild(guild.id):
        raise ValueError("Servidor não autorizado.")
    staff = await can_support(interaction)
    async with SessionLocal() as session, session.begin():
        ticket = await session.scalar(
            select(SupportTicket)
            .where(SupportTicket.id == ticket_id, SupportTicket.guild_id == guild.id)
            .with_for_update()
        )
        if ticket is None or ticket.state == "deleted":
            raise ValueError("Ticket não encontrado ou já excluído.")
        options = await get_support_options(session, guild.id)
        owner_close = (
            action == "close"
            and interaction.user.id == ticket.customer_id
            and options.customer_can_close
        )
        if not staff and not owner_close:
            raise ValueError("Você não tem permissão para esta ação.")
        channel = guild.get_channel(ticket.channel_id or 0)
        if channel is None and ticket.channel_id:
            try:
                channel = await guild.fetch_channel(ticket.channel_id)
            except discord.NotFound:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("O canal não existe mais. Use a lista de tickets atualizada.")
        config = await session.scalar(select(GuildConfig).where(GuildConfig.guild_id == guild.id))
        if action == "claim":
            if ticket.state != "open":
                raise ValueError("Reabra o ticket antes de assumir o atendimento.")
            if ticket.assignee_id and ticket.assignee_id != interaction.user.id:
                raise ValueError("Este ticket já está com outro atendente.")
            ticket.assignee_id = interaction.user.id
            await ticket_event(session, ticket, interaction.user.id, "claim")
            result = f"Atendimento assumido por {interaction.user.mention}."
        elif action == "release":
            if ticket.assignee_id != interaction.user.id:
                raise ValueError("Somente o atendente responsável pode liberar este atendimento.")
            ticket.assignee_id = None
            await ticket_event(session, ticket, interaction.user.id, "release")
            result = "Atendimento liberado para a equipe."
        elif action in {"close", "delete", "transcript"}:
            reason = reason.strip()
            if action in {"close", "delete"} and not 2 <= len(reason) <= 1000:
                raise ValueError("Informe um motivo de 2 a 1000 caracteres.")
            if action == "close" and ticket.state != "open":
                raise ValueError("Este ticket já está fechado.")
            async with freeze_customer_messages(
                interaction, channel, ticket, enabled=action in {"close", "delete"}
            ):
                ticket.transcript_url = await archive_ticket(
                    channel,
                    config.transcript_channel_id if config else None,
                    required=options.transcript_required or action == "transcript",
                    summary={
                        "Assunto": ticket.subject,
                        "Cliente": str(ticket.customer_id),
                        "Responsável": str(ticket.assignee_id or "Não atribuído"),
                        "Motivo": reason or ticket.close_reason or "Exportação manual",
                    },
                )
                if action == "transcript":
                    await ticket_event(
                        session,
                        ticket,
                        interaction.user.id,
                        "transcript",
                        transcript_url=ticket.transcript_url,
                    )
                    return f"Transcript salvo: {ticket.transcript_url}"
                if action == "close":
                    category = guild.get_channel(options.closed_category_id or 0)
                    kwargs = (
                        {"category": category, "sync_permissions": False}
                        if isinstance(category, discord.CategoryChannel)
                        else {}
                    )
                    await channel.edit(
                        name=f"fechado-{channel.name}"[:100],
                        reason="NEXTBUY: atendimento encerrado",
                        **kwargs,
                    )
                    ticket.state = "closed"
                    ticket.close_reason = reason
                    ticket.closed_at = datetime.now(UTC)
                    result = "Ticket fechado. Use Reabrir para retomar o atendimento."
                else:
                    await channel.delete(reason=f"NEXTBUY: exclusão por {interaction.user.id}")
                    ticket.state = "deleted"
                    result = "Ticket excluído."
                await ticket_event(
                    session,
                    ticket,
                    interaction.user.id,
                    action,
                    reason=reason,
                    transcript_saved=bool(ticket.transcript_url),
                    transcript_url=ticket.transcript_url,
                )
                if action == "delete":
                    return result
        elif action == "reopen":
            if ticket.state != "closed":
                raise ValueError("Este ticket já está aberto.")
            await ticket_lock(session, "customer", guild.id, ticket.customer_id)
            await check_open_limit(session, guild.id, ticket.customer_id, options, reopening=True)
            async with freeze_customer_messages(
                interaction, channel, ticket, enabled=True, writable=True
            ):
                category = guild.get_channel(config.ticket_category_id or 0) if config else None
                kwargs = (
                    {"category": category, "sync_permissions": False}
                    if isinstance(category, discord.CategoryChannel)
                    else {}
                )
                await channel.edit(name=channel.name.removeprefix("fechado-"), **kwargs)
                ticket.state, ticket.closed_at, ticket.close_reason = "open", None, None
                await ticket_event(session, ticket, interaction.user.id, "reopen")
                result = "Ticket reaberto."
        elif action in {"add", "remove"}:
            if participant is None or participant.guild.id != guild.id or participant.bot:
                raise ValueError("Selecione um membro deste servidor.")
            if ticket.state != "open":
                raise ValueError("Reabra o ticket para alterar participantes.")
            role_ids = {config.admin_role_id, config.support_role_id} if config else set()
            if (
                participant.id in {ticket.customer_id, guild.owner_id}
                or participant.guild_permissions.administrator
                or any(role.id in role_ids for role in participant.roles)
            ):
                raise ValueError("O cliente e a equipe não podem ser alterados como participantes.")
            participants = set(ticket.participants or [])
            if action == "add":
                if len(participants) >= 10 and participant.id not in participants:
                    raise ValueError("Limite de 10 participantes adicionais atingido.")
                await channel.set_permissions(
                    participant,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )
                participants.add(participant.id)
            else:
                await channel.set_permissions(participant, overwrite=None)
                participants.discard(participant.id)
            ticket.participants = sorted(participants)
            await ticket_event(
                session, ticket, interaction.user.id, action, participant_id=participant.id
            )
            result = "Participantes atualizados."
        else:
            raise ValueError("Ação inválida.")
    # Notification failure must not roll back a completed operation.
    try:
        await channel.send(result, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.warning("Não foi possível notificar ação %s no canal %s", action, channel.id)
    return result
