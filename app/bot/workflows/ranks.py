import logging

import discord

from app.db.session import SessionLocal
from app.services.calculator import format_brl
from app.services.configs import get_or_create_guild_config
from app.services.profiles import get_customer_profile
from app.services.ranks import choose_rank_tier, list_rank_tiers

logger = logging.getLogger(__name__)


def _can_manage_role(member: discord.Member, role: discord.Role | None) -> bool:
    """Return whether the bot can manage a role under Discord's hierarchy rules."""
    if role is None:
        return False
    bot_member = member.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return False
    return role < bot_member.top_role and not role.managed


async def sync_customer_roles(member: discord.Member) -> None:
    async with SessionLocal() as session:
        config = await get_or_create_guild_config(session, member.guild.id)
        tiers = await list_rank_tiers(session, guild_id=member.guild.id)
        profile = await get_customer_profile(
            session,
            guild_id=member.guild.id,
            discord_user_id=member.id,
        )

    target_tier = choose_rank_tier(tiers, profile.total_spent)
    target_role = member.guild.get_role(target_tier.role_id) if target_tier else None

    roles_to_remove: list[discord.Role] = []
    for tier in tiers:
        role = member.guild.get_role(tier.role_id)
        if (
            role is not None
            and role in member.roles
            and role != target_role
            and _can_manage_role(member, role)
        ):
            roles_to_remove.append(role)

    customer_role = (
        member.guild.get_role(config.customer_role_id) if config.customer_role_id else None
    )

    bot_member = member.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        logger.warning(
            "Sincronização de cargos ignorada para %s: o bot não possui Manage Roles.",
            member.id,
        )
        return

    if target_role is not None and not _can_manage_role(member, target_role):
        logger.warning(
            "Faixa %s não pode ser atribuída a %s: mova o cargo do bot acima de %s na hierarquia.",
            target_role.id,
            member.id,
            target_role.id,
        )
        target_role = None
        target_tier = None

    if (
        profile.total_spent > 0
        and customer_role is not None
        and not _can_manage_role(member, customer_role)
    ):
        logger.warning(
            "Cargo de cliente %s não pode ser atribuído a %s: mova o cargo do bot acima dele na hierarquia.",
            customer_role.id,
            member.id,
        )
        customer_role = None

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="NEXTBUY: atualização de faixa")

        newly_reached = target_role is not None and target_role not in member.roles
        if newly_reached:
            await member.add_roles(target_role, reason="NEXTBUY: meta de gasto atingida")

        if profile.total_spent > 0 and customer_role is not None and customer_role not in member.roles:
            await member.add_roles(customer_role, reason="NEXTBUY: cliente com compra confirmada")
        elif (
            profile.total_spent <= 0
            and customer_role is not None
            and customer_role in member.roles
            and _can_manage_role(member, customer_role)
        ):
            await member.remove_roles(
                customer_role,
                reason="NEXTBUY: economia do cliente zerada",
            )

        if newly_reached and target_tier is not None:
            text = target_tier.dm_message.strip() or (
                f"Você atingiu a faixa **{target_tier.name}** na NEXTBUY! "
                f"Total gasto: **{format_brl(profile.total_spent)}**."
            )
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException):
                pass
    except discord.Forbidden:
        logger.warning(
            "Discord recusou a sincronização de cargos do cliente %s. "
            "Verifique Manage Roles e a posição do cargo do bot na hierarquia.",
            member.id,
        )
    except discord.HTTPException as exc:
        logger.warning(
            "Falha HTTP ao sincronizar cargos do cliente %s: %s",
            member.id,
            exc,
        )
