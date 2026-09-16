import discord

from app.bot.views.admin_compact import (
    CompactAdminPanelView as BaseCompactAdminPanelView,
)
from app.bot.views.admin_compact import build_admin_embed
from app.bot.views.embed_builder_compact import send_compact_embed_builder


class CompactAdminPanelView(BaseCompactAdminPanelView):
    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if action == "embed_builder":
            await send_compact_embed_builder(interaction)
            return
        await super().handle_action(interaction, action)


__all__ = ["CompactAdminPanelView", "build_admin_embed"]
