from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.bot.checks import can_admin, can_deliver, can_support
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.db.models import Order, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.orders import OrderStateError, refund_order


async def _build_staff_embed(
    interaction: discord.Interaction,
    *,
    support: bool,
    delivery: bool,
    admin: bool,
) -> discord.Embed:
    if interaction.guild is None:
        return discord.Embed(title="NEXTBUY • Staff", description="Servidor inválido.")

    embed = discord.Embed(
        title="NEXTBUY • Staff",
        description="Painel interno. O cliente não enxerga nem usa esses controles.",
    )

    async with SessionLocal() as session:
        if support or admin:
            rows = (
                await session.execute(
                    select(Order, User)
                    .join(User, User.id == Order.user_id)
                    .where(
                        Order.guild_id == interaction.guild.id,
                        Order.status.in_(["paid", "processing", "delivered"]),
                        Order.ticket_channel_id.is_not(None),
                    )
                    .order_by(Order.created_at.desc())
                    .limit(15)
                )
            ).all()
            lines = [
                (
                    f"`{str(order.id)[:8]}` • <@{user.discord_user_id}> • "
                    f"**{order.status}** • <#{order.ticket_channel_id}>"
                )
                for order, user in rows
            ]
            embed.add_field(
                name="Tickets ativos",
                value="\n".join(lines) if lines else "Nenhum ticket ativo.",
                inline=False,
            )

        if delivery or admin:
            rows = (
                await session.execute(
                    select(Order, User)
                    .join(User, User.id == Order.user_id)
                    .where(
                        Order.guild_id == interaction.guild.id,
                        Order.status.in_(["paid", "processing"]),
                    )
                    .order_by(Order.paid_at.asc().nullsfirst(), Order.created_at.asc())
                    .limit(15)
                )
            ).all()
            lines = [
                (
                    f"`{str(order.id)[:8]}` • <@{user.discord_user_id}> • "
                    f"**{order.total_credits:.2f} créditos**"
                    + (f" • <#{order.ticket_channel_id}>" if order.ticket_channel_id else "")
                )
                for order, user in rows
            ]
            embed.add_field(
                name="Fila de entregas",
                value="\n".join(lines) if lines else "Nenhuma entrega pendente.",
                inline=False,
            )

    if admin:
        embed.set_footer(text="Administrador: acesso total. Use /admin para configurações da loja.")
    elif support and delivery:
        embed.set_footer(text="Acesso de atendimento e entrega.")
    elif support:
        embed.set_footer(text="Acesso de atendimento.")
    else:
        embed.set_footer(text="Acesso de entrega.")
    return embed


class RefundReasonModal(discord.ui.Modal, title="Confirmar reembolso"):
    reason = discord.ui.TextInput(
        label="Motivo",
        placeholder="Explique o motivo do reembolso",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=500,
    )

    def __init__(self, order_id: UUID) -> None:
        super().__init__()
        self.order_id = order_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.response.send_message("Somente administradores podem reembolsar.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        reason = str(self.reason).strip()
        try:
            async with SessionLocal() as session, session.begin():
                order = await refund_order(session, order_id=self.order_id, reason=reason)
                user = await session.get(User, order.user_id)
                if user is None:
                    raise RuntimeError("Cliente do pedido não encontrado")
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="order.refund",
                    target_type="order",
                    target_id=str(order.id),
                    details={
                        "reason": reason,
                        "amount": str(order.total_credits),
                        "customer_discord_id": user.discord_user_id,
                    },
                )
                discord_user_id = user.discord_user_id
                amount = order.total_credits
                ticket_channel_id = order.ticket_channel_id
        except (ValueError, OrderStateError, RuntimeError) as exc:
            await interaction.followup.send(f"Não foi possível reembolsar: {exc}", ephemeral=True)
            return

        member = interaction.guild.get_member(discord_user_id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(discord_user_id)
            except discord.NotFound:
                member = None
        if member is not None:
            await sync_customer_roles(member)
            try:
                await member.send(
                    f"Seu pedido `{str(self.order_id)[:8]}` foi reembolsado em "
                    f"**{amount:.2f} créditos**. Motivo: {reason}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await refresh_leaderboard(interaction.guild)
        if ticket_channel_id:
            ticket = interaction.guild.get_channel(ticket_channel_id)
            if isinstance(ticket, discord.TextChannel):
                try:
                    await ticket.send(
                        f"Pedido reembolsado em **{amount:.2f} créditos**.\nMotivo: {reason}",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass

        await interaction.followup.send(
            f"Pedido `{str(self.order_id)[:8]}` reembolsado. "
            f"**{amount:.2f} créditos** voltaram para o cliente.",
            ephemeral=True,
        )


class RefundOrderSelect(discord.ui.Select):
    def __init__(self, rows: list[tuple[Order, User]]) -> None:
        options = [
            discord.SelectOption(
                label=f"{str(order.id)[:8]} • {order.total_credits:.2f} créditos"[:100],
                value=str(order.id),
                description=f"Cliente {user.discord_user_id} • {order.status}"[:100],
            )
            for order, user in rows[:25]
        ]
        super().__init__(placeholder="Escolha o pedido para reembolsar", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await can_admin(interaction):
            await interaction.response.send_message("Somente administradores podem reembolsar.", ephemeral=True)
            return
        await interaction.response.send_modal(RefundReasonModal(UUID(self.values[0])))


class RefundOrderView(discord.ui.View):
    def __init__(self, rows: list[tuple[Order, User]]) -> None:
        super().__init__(timeout=180)
        self.add_item(RefundOrderSelect(rows))


class StaffPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Atualizar", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        admin = await can_admin(interaction)
        support = admin or await can_support(interaction)
        delivery = admin or await can_deliver(interaction)
        if not support and not delivery:
            await interaction.response.send_message("Você não tem acesso à área da staff.", ephemeral=True)
            return
        embed = await _build_staff_embed(
            interaction,
            support=support,
            delivery=delivery,
            admin=admin,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Reembolsar", style=discord.ButtonStyle.danger)
    async def refund(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.response.send_message("Somente administradores podem reembolsar.", ephemeral=True)
            return
        async with SessionLocal() as session:
            rows = list(
                (
                    await session.execute(
                        select(Order, User)
                        .join(User, User.id == Order.user_id)
                        .where(
                            Order.guild_id == interaction.guild.id,
                            Order.status.in_(["paid", "processing", "delivered"]),
                        )
                        .order_by(Order.created_at.desc())
                        .limit(25)
                    )
                ).all()
            )
        if not rows:
            await interaction.response.send_message(
                "Não há pedidos elegíveis para reembolso.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Escolha o pedido. O saldo será devolvido e o ranking/cargo será recalculado.",
            view=RefundOrderView(rows),
            ephemeral=True,
        )


class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="staff", description="Abre o painel interno de atendimento e entregas")
    @app_commands.guild_only()
    async def staff(self, interaction: discord.Interaction) -> None:
        admin = await can_admin(interaction)
        support = admin or await can_support(interaction)
        delivery = admin or await can_deliver(interaction)
        if not support and not delivery:
            await interaction.response.send_message("Você não tem acesso à área da staff.", ephemeral=True)
            return
        embed = await _build_staff_embed(
            interaction,
            support=support,
            delivery=delivery,
            admin=admin,
        )
        await interaction.response.send_message(embed=embed, view=StaffPanelView(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
