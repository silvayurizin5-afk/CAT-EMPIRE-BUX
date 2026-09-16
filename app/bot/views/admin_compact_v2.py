import discord

from app.bot.views.admin_compact import (
    ADMIN_ACTIONS,
    CompactAdminPanelView as BaseCompactAdminPanelView,
)
from app.bot.views.embed_builder_compact import send_compact_embed_builder
from app.bot.views.store_panel_admin_v2 import send_store_panel_admin

DEFAULT_ACCENT = 0x2B2D31


def build_admin_body() -> str:
    return (
        "## NEXTBUY • Administração\n"
        "Selecione abaixo o que deseja configurar.\n\n"
        "**Loja**\n"
        "Painel, produtos, cupons, preços em reais, estoques e publicação.\n\n"
        "**Automação**\n"
        "IA por canais autorizados, feedbacks, tickets, termos e faixas de cliente.\n\n"
        "**Servidor**\n"
        "Cargos, canais, ranking e criação visual.\n\n"
        "-# Para a IA, use /ia configurar • painel privado deste servidor"
    )


class AdminActionSelectV2(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Selecione o que deseja configurar",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value, description=description)
                for value, label, description in ADMIN_ACTIONS
            ],
            custom_id="nextbuy:admin:action",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, CompactAdminPanelView):
            await self.view.handle_action(interaction, self.values[0])


class CompactAdminPanelView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self._logic = BaseCompactAdminPanelView(owner_id=owner_id)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(build_admin_body()),
                discord.ui.ActionRow(AdminActionSelectV2()),
                accent_color=DEFAULT_ACCENT,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse painel pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if action == "embed_builder":
            await send_compact_embed_builder(interaction)
            return
        if action == "store_panel":
            await send_store_panel_admin(interaction)
            return
        await self._logic.handle_action(interaction, action)


__all__ = ["CompactAdminPanelView", "build_admin_body"]
