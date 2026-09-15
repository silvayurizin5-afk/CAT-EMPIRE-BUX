import logging

import discord

from app.db.session import SessionLocal
from app.services.configs import get_or_create_guild_config
from app.services.profiles import get_customer_profile
from app.services.ranks import choose_rank_tier, list_rank_tiers

logger = logging.getLogger(__name__)


async def sync_customer_roles(member: discord.Member) -> None:
    async with SessionLocal() as session:
        config = await get_or_create_guild_config(session, member.guild.id)
        tiers = await list_rank_tiers(session, guild_id=member.guild.id)
        profile = await get_customer_profile(
            session,
            guild_id=member.guild.id,
            discord_user_id=member.id,
        )

    roles_to_remove: list[discord.Role] = []
    target_tier = choose_rank_tier(tiers, profile.total_spent)
    target_role = member.guild.get_role(target_tier.role_id) if target_tier else None

    for tier in tiers:
        role = member.guild.get_role(tier.role_id)
        if role is not None and role in member.roles and role != target_role:
            roles_to_remove.append(role)

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="NEXTBUY: atualização de faixa")

        newly_reached = target_role is not None and target_role not in member.roles
        if target_role is not None and newly_reached:
            await member.add_roles(target_role, reason="NEXTBUY: meta de gasto atingida")

        customer_role = (
            member.guild.get_role(config.customer_role_id) if config.customer_role_id else None
        )
        if profile.total_spent > 0 and customer_role is not None and customer_role not in member.roles:
            await member.add_roles(customer_role, reason="NEXTBUY: cliente com compra confirmada")

        if newly_reached and target_tier is not None:
            text = target_tier.dm_message.strip() or (
                f"Você atingiu a faixa **{target_tier.name}** na NEXTBUY! "
                f"Total gasto: **{profile.total_spent:.2f} créditos**."
            )
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException):
                pass
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("Falha ao sincronizar cargos do cliente %s", member.id)
