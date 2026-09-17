import discord

from app.bot.views.admin import AdminPanelView
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.configs import get_or_create_guild_config


async def apply_feedback_channel_permissions(
    guild: discord.Guild,
    *,
    feedback_channel_id: int | None,
    customer_role_id: int | None,
    admin_role_id: int | None,
    support_role_id: int | None,
    delivery_role_id: int | None,
) -> bool:
    if not feedback_channel_id:
        return False
    channel = guild.get_channel(feedback_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        await channel.set_permissions(
            guild.default_role,
            view_channel=True,
            send_messages=False,
            read_message_history=True,
            reason="NEXTBUY: feedbacks públicos, escrita apenas para clientes/staff",
        )
        for role_id in {
            customer_role_id,
            admin_role_id,
            support_role_id,
            delivery_role_id,
        }:
            if role_id and (role := guild.get_role(role_id)):
                await channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    add_reactions=True,
                    read_message_history=True,
                    reason="NEXTBUY: permissão do canal de feedbacks",
                )
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


class FeedbackSettingsModal(discord.ui.Modal, title="Configurar feedbacks"):
    emoji = discord.ui.TextInput(
        label="Emoji de confirmação",
        placeholder="🐱",
        max_length=128,
    )

    def __init__(self, *, emoji: str, reminder_minutes: int, dm_cooldown_hours: int) -> None:
        super().__init__()
        self.emoji.default = emoji
        # Mantidos na assinatura por compatibilidade com as telas administrativas existentes.
        self._legacy_reminder_minutes = reminder_minutes
        self._legacy_dm_cooldown_hours = dm_cooldown_hours

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        emoji = str(self.emoji).strip() or "🐱"
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_guild_config(session, interaction.guild.id)
            config.feedback_emoji = emoji
            values = (
                config.feedback_channel_id,
                config.customer_role_id,
                config.admin_role_id,
                config.support_role_id,
                config.delivery_role_id,
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="feedback.settings.update",
                target_type="guild",
                target_id=str(interaction.guild.id),
                details={
                    "emoji": emoji,
                    "automatic_reminders": False,
                },
            )

        permissions_ok = await apply_feedback_channel_permissions(
            interaction.guild,
            feedback_channel_id=values[0],
            customer_role_id=values[1],
            admin_role_id=values[2],
            support_role_id=values[3],
            delivery_role_id=values[4],
        )
        permission_text = (
            "Permissões do canal também foram ajustadas."
            if permissions_ok
            else "Configuração salva; revise o canal/cargos se quiser aplicar as permissões automaticamente."
        )
        await interaction.response.send_message(
            "Feedbacks configurados. Os lembretes automáticos de avaliação estão desativados. "
            + permission_text,
            ephemeral=True,
        )


class ExtendedAdminPanelView(AdminPanelView):
    @discord.ui.button(label="Gerenciar produtos", style=discord.ButtonStyle.primary)
    async def manage_products(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from app.bot.views.product_admin import send_product_management

        await send_product_management(interaction)

    @discord.ui.button(label="Cotações", style=discord.ButtonStyle.primary)
    async def manage_robux_rates(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from app.bot.views.automation_admin import send_robux_rate_management

        await send_robux_rate_management(interaction)

    @discord.ui.button(label="FAQ", style=discord.ButtonStyle.primary)
    async def manage_auto_replies(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from app.bot.views.automation_admin import send_auto_reply_management

        await send_auto_reply_management(interaction)

    @discord.ui.button(label="Feedbacks", style=discord.ButtonStyle.primary)
    async def feedback_settings(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_guild_config(session, interaction.guild.id)
            emoji = config.feedback_emoji
            reminder = config.feedback_reminder_minutes
            dm_hours = config.feedback_dm_cooldown_hours
        await interaction.response.send_modal(
            FeedbackSettingsModal(
                emoji=emoji,
                reminder_minutes=reminder,
                dm_cooldown_hours=dm_hours,
            )
        )
