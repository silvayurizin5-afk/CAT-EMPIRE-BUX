import discord
from sqlalchemy import select

from app.db.models import GuildConfig
from app.db.session import SessionLocal


def _has_role(member: discord.Member, role_id: int | None) -> bool:
    return bool(role_id and any(role.id == role_id for role in member.roles))


async def can_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    if member.id == interaction.guild.owner_id or member.guild_permissions.administrator:
        return True
    async with SessionLocal() as session:
        config = await session.scalar(
            select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
        )
    return bool(config and _has_role(member, config.admin_role_id))


async def can_support(interaction: discord.Interaction) -> bool:
    if await can_admin(interaction):
        return True
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    async with SessionLocal() as session:
        config = await session.scalar(
            select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
        )
    return bool(config and _has_role(interaction.user, config.support_role_id))


async def can_deliver(interaction: discord.Interaction) -> bool:
    if await can_admin(interaction):
        return True
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    async with SessionLocal() as session:
        config = await session.scalar(
            select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
        )
    return bool(config and _has_role(interaction.user, config.delivery_role_id))
