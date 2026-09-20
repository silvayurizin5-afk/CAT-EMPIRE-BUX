import discord

from app.bot.checks import can_admin
from app.bot.components_v2 import CardLayout, add_action_row
from app.bot.views.admin_forms import ConfigTargetView
from app.bot.views.support import SupportPanel, report_error, send_support_list
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.support_tickets import get_support_options, save_support_options


class SupportSettingsModal(discord.ui.Modal):
    def __init__(self, options, section):
        super().__init__(
            title={
                "text": "Textos do atendimento",
                "visual": "Visual do painel",
                "policy": "Regras do atendimento",
            }[section]
        )
        self.section = section
        fields = {
            "text": [
                ("panel_title", "Título do painel", 160),
                ("panel_description", "Descrição do painel", 1800),
                ("button_label", "Texto do botão", 80),
                ("welcome", "Boas-vindas — variável: {customer}", 1800),
            ],
            "visual": [
                ("color", "Cor hexadecimal", 7),
                ("button_emoji", "Emoji do botão", 100),
                ("banner_url", "Link HTTPS do banner (opcional)", 1000),
            ],
            "policy": [
                ("enabled", "Aceitar novos tickets? sim/não", 3),
                ("max_open", "Máximo de tickets abertos (1–10)", 2),
                ("cooldown_seconds", "Intervalo entre aberturas (0–86400 segundos)", 5),
                ("transcript_required", "Exigir transcript ao fechar/excluir? sim/não", 3),
                ("customer_can_close", "Cliente pode fechar? sim/não", 3),
            ],
        }[section]
        self.inputs = {}
        for key, label, limit in fields:
            value = getattr(options, key)
            value = ("sim" if value else "não") if isinstance(value, bool) else str(value)
            field = discord.ui.TextInput(
                label=label,
                default=value,
                max_length=limit,
                required=key not in {"button_emoji", "banner_url"},
                style=discord.TextStyle.paragraph if limit > 160 else discord.TextStyle.short,
            )
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction):
        if not await can_admin(interaction):
            await interaction.response.send_message(
                "Sem permissão de administrador.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            patch = {key: str(field).strip() for key, field in self.inputs.items()}
            if self.section == "policy":
                for key in ("enabled", "transcript_required", "customer_can_close"):
                    answer = patch[key].casefold()
                    if answer not in {"sim", "não", "nao"}:
                        raise ValueError("Responda sim ou não aos campos de ativação.")
                    patch[key] = answer == "sim"
                for key in ("max_open", "cooldown_seconds"):
                    try:
                        patch[key] = int(patch[key])
                    except ValueError as exc:
                        raise ValueError(
                            "Limite e intervalo precisam ser números inteiros."
                        ) from exc
            async with SessionLocal() as session, session.begin():
                options = await save_support_options(session, interaction.guild_id, patch)
                # Validate Discord's emoji parser before committing configuration.
                SupportPanel(options)
                await write_audit_log(
                    session,
                    guild_id=interaction.guild_id,
                    actor_discord_id=interaction.user.id,
                    action="ticket.settings",
                    details={"section": self.section},
                )
            await interaction.followup.send(
                "Configuração salva. Republique o painel para atualizar o visual.", ephemeral=True
            )
        except Exception as exc:
            await report_error(interaction, exc)


class ClosedCategorySelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Categoria para tickets fechados (opcional)",
            channel_types=[discord.ChannelType.category],
            min_values=0,
            max_values=1,
        )

    async def callback(self, interaction):
        if not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            await save_support_options(
                session,
                interaction.guild_id,
                {"closed_category_id": self.values[0].id if self.values else None},
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild_id,
                actor_discord_id=interaction.user.id,
                action="ticket.settings",
                details={"section": "Categoria de arquivamento"},
            )
        await interaction.response.send_message(
            "Categoria de arquivamento atualizada.", ephemeral=True
        )


class SupportAdmin(CardLayout):
    def __init__(self, owner_id, options):
        super().__init__(
            title="Configurar tickets",
            description=(
                f"**Abertura:** {'ativada' if options.enabled else 'desativada'}\n"
                f"**Limite por cliente:** {options.max_open} • **Intervalo:** {options.cooldown_seconds}s\n"
                f"**Transcript obrigatório:** {'sim' if options.transcript_required else 'não'}\n"
                "Configure cargos, categoria, canal de logs e transcripts antes de publicar.\n"
                "Use `/tickets publicar` no canal desejado. Os tickets de compras continuam no fluxo da loja."
            ),
            timeout=600,
        )
        self.owner_id, self.options = owner_id, options
        buttons = []
        for label, section in (("Textos", "text"), ("Visual", "visual"), ("Regras", "policy")):
            button = discord.ui.Button(label=label)

            async def callback(interaction, section=section):
                async with SessionLocal() as session:
                    options = await get_support_options(session, interaction.guild_id)
                await interaction.response.send_modal(SupportSettingsModal(options, section))

            button.callback = callback
            buttons.append(button)
        add_action_row(self.container, *buttons)
        buttons = []
        for label, action in (
            ("Prévia", "preview"),
            ("Canais", "channels"),
            ("Cargos", "roles"),
            ("Gerenciar suporte", "manage"),
        ):
            button = discord.ui.Button(label=label)

            async def callback(interaction, action=action):
                if action == "manage":
                    await send_support_list(interaction)
                elif action == "preview":
                    async with SessionLocal() as session:
                        options = await get_support_options(session, interaction.guild_id)
                    view = SupportPanel(options)
                    for item in view.walk_children():
                        if isinstance(item, discord.ui.Button):
                            item.disabled = True
                    await interaction.response.send_message(view=view, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        "Selecione o destino:",
                        view=ConfigTargetView(kind="channel" if action == "channels" else "role"),
                        ephemeral=True,
                    )

            button.callback = callback
            buttons.append(button)
        add_action_row(self.container, *buttons)
        add_action_row(self.container, ClosedCategorySelect())

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id and await can_admin(interaction):
            return True
        await interaction.response.send_message("Sem permissão para este painel.", ephemeral=True)
        return False


async def send_support_admin(interaction):
    if not await can_admin(interaction):
        await interaction.response.send_message("Sem permissão de administrador.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        options = await get_support_options(session, interaction.guild_id)
    await interaction.followup.send(view=SupportAdmin(interaction.user.id, options), ephemeral=True)
