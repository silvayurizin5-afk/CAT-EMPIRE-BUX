import discord
from sqlalchemy import select

from app.db.models import GuildConfig, RankTier
from app.db.session import SessionLocal


class FeedbackPermissionSyncError(RuntimeError):
    pass


def allowed_feedback_role_ids(config: GuildConfig, tiers: list[RankTier]) -> set[int]:
    role_ids = {
        role_id
        for role_id in (
            config.customer_role_id,
            config.admin_role_id,
            config.support_role_id,
            config.delivery_role_id,
        )
        if role_id is not None
    }
    role_ids.update(tier.role_id for tier in tiers if tier.active)
    return role_ids


async def sync_feedback_channel_permissions(guild: discord.Guild) -> bool:
    async with SessionLocal() as session:
        config = await session.scalar(
            select(GuildConfig).where(GuildConfig.guild_id == guild.id)
        )
        if config is None or config.feedback_channel_id is None:
            return False
        tiers = list(
            (
                await session.scalars(
                    select(RankTier).where(
                        RankTier.guild_id == guild.id,
                        RankTier.active.is_(True),
                    )
                )
            ).all()
        )

    channel = guild.get_channel(config.feedback_channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise FeedbackPermissionSyncError("Canal de feedbacks não encontrado")

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
            add_reactions=False,
        )
    }

    for role_id in allowed_feedback_role_ids(config, tiers):
        role = guild.get_role(role_id)
        if role is None:
            continue
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
        )

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
        )

    try:
        await channel.edit(
            overwrites=overwrites,
            reason="NEXTBUY: sincronizar permissões do canal de feedbacks",
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise FeedbackPermissionSyncError(
            "Não consegui aplicar as permissões do canal de feedbacks. "
            "Confira se o bot tem Gerenciar Canais."
        ) from exc
    return True
