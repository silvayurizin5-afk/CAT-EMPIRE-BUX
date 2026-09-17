import discord

from app.bot.views.admin_compact import (
    CompactAdminPanelView as BaseCompactAdminPanelView,
)
from app.bot.views.admin_compact import build_admin_embed
from app.bot.views.ai_admin import send_ai_admin
from app.bot.views.embed_builder_compact import send_compact_embed_builder
from app.bot.views.store_panel_admin_v2 import send_store_panel_admin


class CompactAdminPanelView(BaseCompactAdminPanelView):
    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if action == "embed_builder":
            await send_compact_embed_builder(interaction)
            return
        if action == "store_panel":
            await send_store_panel_admin(interaction)
            return
        if action == "ai":
            await send_ai_admin(interaction)
            return
        await super().handle_action(interaction, action)


__all__ = ["CompactAdminPanelView", "build_admin_embed"]
