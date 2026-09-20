from decimal import Decimal, InvalidOperation

import discord

from app.bot.checks import can_admin
from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl, format_robux
from app.services.profiles import (
    CustomerProfile,
    clear_user_economy_adjustment,
    get_customer_profile,
    set_user_economy_target,
)


def _profile_lines(member: discord.abc.User, profile: CustomerProfile) -> list[str]:
    return [
        f"**Usuário:** {member.mention} `({member.id})`",
        f"**Total gasto:** `{format_brl(profile.total_spent)}`",
        f"**Robux contabilizados:** `{format_robux(profile.robux_purchased)}`",
        f"**Compras contabilizadas:** `{profile.completed_orders}`",
        (
            f"**Posição:** `#{profile.leaderboard_position}`"
            if profile.leaderboard_position
            else "**Posição:** `fora do ranking`"
        ),
    ]


async def _refresh_after_economy_change(
    interaction: discord.Interaction,
    *,
    target_user_id: int,
) -> None:
    if interaction.guild is None:
        return
    await refresh_leaderboard(interaction.guild)
    member = interaction.guild.get_member(target_user_id)
    if member is not None:
        await sync_customer_roles(member)


class EconomyConfigureModal(discord.ui.Modal, title="Configurar economia do usuário"):
    def __init__(
        self,
        *,
        target_user_id: int,
        profile: CustomerProfile,
    ) -> None:
        super().__init__()
        self.target_user_id = target_user_id
        self.total_spent = discord.ui.TextInput(
            label="Total gasto em R$",
            default=f"{profile.total_spent:.2f}".replace(".", ","),
            max_length=20,
            placeholder="Ex: 365,50",
        )
        self.robux = discord.ui.TextInput(
            label="Robux contabilizados",
            default=str(profile.robux_purchased),
            max_length=18,
            placeholder="Ex: 13000",
        )
        self.orders = discord.ui.TextInput(
            label="Compras contabilizadas",
            default=str(profile.completed_orders),
            max_length=12,
            placeholder="Ex: 25",
        )
        self.add_item(self.total_spent)
        self.add_item(self.robux)
        self.add_item(self.orders)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return

        raw_spent = str(self.total_spent).strip().replace(",", ".")
        raw_robux = str(self.robux).strip()
        raw_orders = str(self.orders).strip()
        try:
            spent = Decimal(raw_spent)
            robux = int(raw_robux)
            orders = int(raw_orders)
            if spent < 0 or robux < 0 or orders < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            await interaction.response.send_message(
                "Valores inválidos. Use apenas números maiores ou iguais a zero.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            profile = await set_user_economy_target(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=self.target_user_id,
                total_spent=spent,
                robux_purchased=robux,
                completed_orders=orders,
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="economy.configure",
                target_type="user",
                target_id=str(self.target_user_id),
                details={
                    "customer_discord_id": self.target_user_id,
                    "amount_brl": str(profile.total_spent),
                    "robux_amount": profile.robux_purchased,
                    "quantity": profile.completed_orders,
                },
            )

        await _refresh_after_economy_change(
            interaction,
            target_user_id=self.target_user_id,
        )
        await interaction.edit_original_response(
            content=(
                "Economia configurada. Esses valores funcionam como o novo ponto de partida; "
                "compras futuras continuam somando normalmente.\n"
                f"**R$:** {format_brl(profile.total_spent)} • "
                f"**Robux:** {format_robux(profile.robux_purchased)} • "
                f"**Compras:** {profile.completed_orders}"
            ),
            view=None,
        )


class EconomyResetConfirmView(discord.ui.View):
    def __init__(self, *, target_user_id: int, owner_id: int) -> None:
        super().__init__(timeout=90)
        self.target_user_id = target_user_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Essa confirmação pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirmar zerar economia", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            await set_user_economy_target(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=self.target_user_id,
                total_spent=Decimal("0"),
                robux_purchased=0,
                completed_orders=0,
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="economy.reset",
                target_type="user",
                target_id=str(self.target_user_id),
                details={
                    "customer_discord_id": self.target_user_id,
                    "amount_brl": "0.00",
                    "robux_amount": 0,
                    "quantity": 0,
                },
            )

        await _refresh_after_economy_change(
            interaction,
            target_user_id=self.target_user_id,
        )
        await interaction.edit_original_response(
            content=(
                "Economia zerada: **R$ 0,00**, **0 Robux** e **0 compras**. "
                "O histórico de pedidos não foi apagado; o ajuste garante que compras novas "
                "passem a contar a partir de zero."
            ),
            view=None,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="Operação cancelada.",
            view=None,
        )


class EconomyDetailLayout(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        member: discord.abc.User,
        profile: CustomerProfile,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=300)
        self.member = member
        self.profile = profile
        self.owner_id = owner_id

        card = CardLayout(
            title="Economia do usuário",
            lines=_profile_lines(member, profile),
            footer=(
                "Configurar/zerar usa ajustes; pedidos antigos permanecem preservados "
                "e compras futuras continuam somando."
            ),
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        configure = discord.ui.Button(
            label="Configurar valores",
            style=discord.ButtonStyle.primary,
        )
        configure.callback = self._configure
        reset = discord.ui.Button(
            label="Zerar economia",
            style=discord.ButtonStyle.danger,
        )
        reset.callback = self._reset
        automatic = discord.ui.Button(
            label="Restaurar histórico automático",
            style=discord.ButtonStyle.secondary,
        )
        automatic.callback = self._automatic
        add_action_row(self.container, configure, reset, automatic)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse painel pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def _configure(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            EconomyConfigureModal(
                target_user_id=self.member.id,
                profile=self.profile,
            )
        )

    async def _reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            (
                f"Zerar toda a economia de {self.member.mention}?\n"
                "Isso coloca **R$ gasto, Robux e compras em zero** sem apagar os pedidos."
            ),
            view=EconomyResetConfirmView(
                target_user_id=self.member.id,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )

    async def _automatic(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            profile = await clear_user_economy_adjustment(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=self.member.id,
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="economy.restore_automatic",
                target_type="user",
                target_id=str(self.member.id),
                details={
                    "customer_discord_id": self.member.id,
                    "amount_brl": str(profile.total_spent),
                    "robux_amount": profile.robux_purchased,
                    "quantity": profile.completed_orders,
                },
            )

        await _refresh_after_economy_change(
            interaction,
            target_user_id=self.member.id,
        )
        await interaction.edit_original_response(
            content=(
                "Ajustes manuais removidos. O usuário voltou a usar somente o histórico "
                "real de pedidos.\n"
                f"**R$:** {format_brl(profile.total_spent)} • "
                f"**Robux:** {format_robux(profile.robux_purchased)} • "
                f"**Compras:** {profile.completed_orders}"
            ),
            view=None,
        )


class EconomyUserSelect(discord.ui.UserSelect):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(
            placeholder="Selecione o usuário",
            min_values=1,
            max_values=1,
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        member = self.values[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session:
            profile = await get_customer_profile(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=member.id,
            )
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=EconomyDetailLayout(
                member=member,
                profile=profile,
                owner_id=self.owner_id,
            ),
        )


class EconomyManagementLayout(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        card = CardLayout(
            title="Gerenciar economia",
            description=(
                "Selecione um usuário para revisar, configurar ou zerar os valores "
                "usados no perfil e no ranking."
            ),
            lines=[
                "- **R$ gasto:** soma de pedidos pagos/processando/entregues.",
                "- **Robux:** compras diretas de Robux + valor em Robux das Game Pass.",
                "- **Itens comuns:** continuam somando somente no total em R$.",
            ],
            footer="NEXTBUY • Economia",
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, EconomyUserSelect(owner_id=owner_id))


async def send_economy_management(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        view=EconomyManagementLayout(owner_id=interaction.user.id),
        ephemeral=True,
    )
