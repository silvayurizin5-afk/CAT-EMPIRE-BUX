import discord

from app.bot.views.store_panel import build_store_panel_embed
from app.bot.views.store_panel_admin import STORE_PANEL_ACTIONS, StorePanelAdminView
from app.db.session import SessionLocal
from app.services.calculator import normalize_text
from app.services.store_panel import get_or_create_store_panel, list_store_products


class GameIconModal(discord.ui.Modal, title="Ícone do jogo no cálculo"):
    game_name = discord.ui.TextInput(
        label="Nome do jogo",
        placeholder="Ex: Blox Fruits",
        max_length=120,
    )
    emoji = discord.ui.TextInput(
        label="Emoji do jogo",
        placeholder="Ex: <:blox:123456789012345678>",
        required=False,
        max_length=128,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        game = str(self.game_name).strip()
        key = normalize_text(game)
        if not key:
            await interaction.response.send_message("Informe o nome do jogo.", ephemeral=True)
            return
        emoji = str(self.emoji).strip()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            icons = dict(config.game_icons or {})
            if emoji:
                icons[key] = emoji
            else:
                icons.pop(key, None)
            config.game_icons = icons
        action = "configurado" if emoji else "removido"
        await interaction.response.send_message(
            f"Ícone de **{game}** {action} para as embeds de cálculo.",
            ephemeral=True,
        )


STORE_PANEL_ACTIONS_V2 = (
    *STORE_PANEL_ACTIONS[:-1],
    (
        "game_icons",
        "Ícones dos jogos",
        "Emoji do jogo usado somente nas embeds de cálculo",
    ),
    STORE_PANEL_ACTIONS[-1],
)


class StorePanelActionSelectV2(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="O que deseja configurar na loja?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value, description=description)
                for value, label, description in STORE_PANEL_ACTIONS_V2
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, StorePanelAdminViewV2):
            await self.view.handle_action(interaction, self.values[0])


class StorePanelAdminViewV2(StorePanelAdminView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id=owner_id)
        self.clear_items()
        self.add_item(StorePanelActionSelectV2())

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if action == "game_icons":
            await interaction.response.send_modal(GameIconModal())
            return
        await super().handle_action(interaction, action)


async def send_store_panel_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, interaction.guild.id)
        products = await list_store_products(session, guild_id=interaction.guild.id, config=config)
        embed = build_store_panel_embed(config, len(products))
    await interaction.response.edit_message(
        content="Configure toda a loja por este seletor. A embed abaixo é a prévia atual.",
        embed=embed,
        view=StorePanelAdminViewV2(owner_id=interaction.user.id),
    )


__all__ = ["StorePanelAdminViewV2", "send_store_panel_admin"]
