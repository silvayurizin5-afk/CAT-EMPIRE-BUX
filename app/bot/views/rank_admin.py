from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy.exc import IntegrityError

from app.bot.views.terms_admin import CompleteAdminPanelView
from app.db.models import RankTier
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.ranks import (
    list_all_rank_tiers,
    set_rank_tier_active,
    upsert_rank_tier,
)


def build_rank_tier_embed(tier: RankTier, guild: discord.Guild | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"Faixa • {tier.name}",
        description="Ativa" if tier.active else "Desativada",
    )
    embed.add_field(name="Meta", value=f"{tier.min_spend:.2f} créditos")
    role = guild.get_role(tier.role_id) if guild is not None else None
    embed.add_field(name="Cargo", value=role.mention if role else f"`{tier.role_id}`")
    embed.add_field(name="DM ao alcançar", value=tier.dm_message or "—", inline=False)
    return embed


class RankTierEditModal(discord.ui.Modal):
    def __init__(self, tier: RankTier) -> None:
        super().__init__(title=f"Editar {tier.name[:35]}")
        self.tier_id = tier.id
        self.name_input = discord.ui.TextInput(
            label="Nome",
            max_length=80,
            default=tier.name[:80],
        )
        self.min_spend_input = discord.ui.TextInput(
            label="Meta em créditos",
            max_length=20,
            default=f"{tier.min_spend:.2f}",
        )
        self.dm_message_input = discord.ui.TextInput(
            label="Mensagem ao atingir a meta",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1200,
            default=(tier.dm_message or "")[:1200],
        )
        self.add_item(self.name_input)
        self.add_item(self.min_spend_input)
        self.add_item(self.dm_message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            threshold = Decimal(str(self.min_spend_input).strip().replace(",", "."))
        except InvalidOperation:
            await interaction.response.send_message("Meta inválida.", ephemeral=True)
            return

        try:
            async with SessionLocal() as session, session.begin():
                tier = await session.get(RankTier, self.tier_id)
                if tier is None or tier.guild_id != interaction.guild.id:
                    await interaction.response.send_message("Faixa não encontrada.", ephemeral=True)
                    return
                was_active = tier.active
                updated = await upsert_rank_tier(
                    session,
                    guild_id=interaction.guild.id,
                    name=str(self.name_input),
                    min_spend=threshold,
                    role_id=tier.role_id,
                    dm_message=str(self.dm_message_input),
                )
                updated.active = was_active
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="rank_tier.update",
                    target_type="rank_tier",
                    target_id=str(tier.id),
                    details={
                        "name": updated.name,
                        "min_spend": str(updated.min_spend),
                        "role_id": updated.role_id,
                        "active": updated.active,
                    },
                )
        except (ValueError, IntegrityError) as exc:
            await interaction.response.send_message(
                f"Não foi possível atualizar a faixa: {exc}", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Faixa atualizada. Reabra **Gerenciar faixas** para conferir.",
            ephemeral=True,
        )


class RankTierActionsView(discord.ui.View):
    def __init__(self, tier_id: int) -> None:
        super().__init__(timeout=180)
        self.tier_id = tier_id

    @discord.ui.button(label="Editar", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            tier = await session.get(RankTier, self.tier_id)
        if tier is None or tier.guild_id != interaction.guild.id:
            await interaction.response.send_message("Faixa não encontrada.", ephemeral=True)
            return
        await interaction.response.send_modal(RankTierEditModal(tier))

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            tier = await session.get(RankTier, self.tier_id)
            if tier is None or tier.guild_id != interaction.guild.id:
                await interaction.response.send_message("Faixa não encontrada.", ephemeral=True)
                return
            await set_rank_tier_active(session, tier=tier, active=not tier.active)
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="rank_tier.toggle",
                target_type="rank_tier",
                target_id=str(tier.id),
                details={"active": tier.active, "role_id": tier.role_id},
            )
        await interaction.response.edit_message(
            content=None,
            embed=build_rank_tier_embed(tier, interaction.guild),
            view=RankTierActionsView(tier.id),
        )


class RankTierManageSelect(discord.ui.Select):
    def __init__(self, tiers: list[RankTier]) -> None:
        options = [
            discord.SelectOption(
                label=tier.name[:100],
                value=str(tier.id),
                description=(
                    f"{tier.min_spend:.2f} créditos • "
                    f"{'ativa' if tier.active else 'desativada'}"
                )[:100],
            )
            for tier in tiers[:25]
        ]
        super().__init__(placeholder="Escolha a faixa", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        tier_id = int(self.values[0])
        async with SessionLocal() as session:
            tier = await session.get(RankTier, tier_id)
        if tier is None or tier.guild_id != interaction.guild.id:
            await interaction.response.edit_message(content="Faixa não encontrada.", view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_rank_tier_embed(tier, interaction.guild),
            view=RankTierActionsView(tier.id),
        )


class RankTierManagementView(discord.ui.View):
    def __init__(self, tiers: list[RankTier]) -> None:
        super().__init__(timeout=180)
        self.add_item(RankTierManageSelect(tiers))


async def send_rank_tier_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        tiers = await list_all_rank_tiers(session, guild_id=interaction.guild.id)
    if not tiers:
        await interaction.response.send_message(
            "Nenhuma faixa cadastrada. Use **Faixa de cliente** primeiro.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "Escolha uma faixa para editar ou ativar/desativar:",
        view=RankTierManagementView(tiers),
        ephemeral=True,
    )


class FullAdminPanelView(CompleteAdminPanelView):
    @discord.ui.button(label="Gerenciar faixas", style=discord.ButtonStyle.primary)
    async def manage_rank_tiers(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_rank_tier_management(interaction)
