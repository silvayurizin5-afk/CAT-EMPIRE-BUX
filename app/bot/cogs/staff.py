import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.bot.checks import can_admin, can_deliver, can_support
from app.db.models import Order, User
from app.db.session import SessionLocal


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
