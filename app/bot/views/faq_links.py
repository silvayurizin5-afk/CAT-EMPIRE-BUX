import discord

from app.db.faq_models import AutoReplyButton


class AutoReplyLinkView(discord.ui.View):
    def __init__(self, buttons: list[AutoReplyButton]) -> None:
        super().__init__(timeout=None)
        for button in buttons[:5]:
            kwargs = {
                "label": button.label[:80],
                "url": button.url,
                "style": discord.ButtonStyle.link,
            }
            if button.emoji:
                kwargs["emoji"] = button.emoji
            try:
                item = discord.ui.Button(**kwargs)
            except (TypeError, ValueError):
                kwargs.pop("emoji", None)
                item = discord.ui.Button(**kwargs)
            self.add_item(item)
