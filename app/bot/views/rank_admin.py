from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy.exc import IntegrityError

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.views.terms_admin import CompleteAdminPanelView
from app.bot.workflows.feedback_permissions import (
    FeedbackPermissionSyncError,
    sync_feedback_channel_permissions,
)
from app.db.models import RankTier
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
from app.services.ranks import (
    list_all_rank_tiers,
    set_rank_tier_active,
    upsert_rank_tier,
)


async def _sync_feedback_permissions_after_response(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    try:
        await sync_feedback_channel_permissions(interaction.guild)
    except FeedbackPermissionSyncError as exc:
        await interaction.followup.send(f"Aviso: {exc}", ephemeral=True)


def _rank_lines(tier: RankTier, guild: discord.Guild | None = None) -> list[str]:
    role = guild.get_role(tier.role_id) if guild is not None else None
    return [
        f"**Status:** `{'Ativa' if tier.active else 'Desativada'}`",
        f"**Meta:** `{format_brl(tier.min_spend)}`",
        f"**Cargo:** {role.mention if role else f'`{tier.role_id}`'}",
        f"**DM ao alcançar:** {tier.dm_message or '—'}",
    ]


def build_rank_tier_embed(tier: RankTier, guild: discord.Guild | None = None) -> discord.Embed:
    """Compatibilidade com telas antigas; a interface ativa usa Components V2."""
    return discord.Embed(
        title=f"Faixa • {tier.name}",
        description="\n".join(_rank_lines(tier, guild)),
        color=discord.Color.from_rgb(43, 45, 49),
    )


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
            label="Meta em reais",
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

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with SessionLocal() as session, session.begin():
                tier = await session.get(RankTier, self.tier_id)
                if tier is None or tier.guild_id != interaction.guild.id:
                    await interaction.edit_original_response(content="Faixa não encontrada.")
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
            await interaction.edit_original_response(
                content=f"Não foi possível atualizar a faixa: {exc}"
            )
            return

        await interaction.edit_original_response(
            content="Faixa atualizada. Reabra **Gerenciar faixas** para conferir."
        )
        await _sync_feedback_permissions_after_response(interaction)


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
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            tier = await session.get(RankTier, self.tier_id)
            if tier is None or tier.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Faixa não encontrada.", view=None)
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
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=RankTierActionsLayout(tier, interaction.guild),
        )
        await _sync_feedback_permissions_after_response(interaction)


class RankTierActionsLayout(discord.ui.LayoutView):
    def __init__(self, tier: RankTier, guild: discord.Guild | None) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title=f"Faixa • {tier.name}",
            lines=_rank_lines(tier, guild),
            footer="NEXTBUY • Faixas",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        legacy = RankTierActionsView(tier.id)
        buttons = list(legacy.children)
        for item in buttons:
            legacy.remove_item(item)
        if buttons:
            add_action_row(self.container, *buttons)


class RankTierManageSelect(discord.ui.Select):
    def __init__(self, tiers: list[RankTier]) -> None:
        options = [
            discord.SelectOption(
                label=tier.name[:100],
                value=str(tier.id),
                description=(
                    f"{format_brl(tier.min_spend)} • "
                    f"{'ativa' if tier.active else 'desativada'}"
                )[:100],
            )
            for tier in tiers[:25]
        ]
        super().__init__(placeholder="Escolha a faixa", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        tier_id = int(self.values[0])
        async with SessionLocal() as session:
            tier = await session.get(RankTier, tier_id)
        if tier is None or tier.guild_id != interaction.guild.id:
            await interaction.edit_original_response(content="Faixa não encontrada.", view=None)
            return
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=RankTierActionsLayout(tier, interaction.guild),
        )


class RankTierManagementView(discord.ui.LayoutView):
    def __init__(self, tiers: list[RankTier]) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title="Gerenciar faixas",
            description="Escolha uma faixa para editar ou ativar/desativar.",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, RankTierManageSelect(tiers))


async def send_rank_tier_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        tiers = await list_all_rank_tiers(session, guild_id=interaction.guild.id)
    if not tiers:
        await interaction.edit_original_response(
            content="Nenhuma faixa cadastrada. Use **Faixa de cliente** primeiro.",
            view=None,
        )
        return
    await interaction.edit_original_response(
        content=None,
        embeds=[],
        view=RankTierManagementView(tiers),
    )


class FullAdminPanelView(CompleteAdminPanelView):
    @discord.ui.button(label="Gerenciar faixas", style=discord.ButtonStyle.primary)
    async def manage_rank_tiers(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_rank_tier_management(interaction)
