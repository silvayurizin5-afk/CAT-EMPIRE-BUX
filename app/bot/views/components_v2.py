from __future__ import annotations

from decimal import Decimal

import discord


DEFAULT_ACCENT = 0x2B2D31


def format_percent(value: Decimal | int | float | str) -> str:
    number = Decimal(str(value))
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return f"{normalized}%"


def _text(title: str | None, body: str | None) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"## {title.strip()}")
    if body:
        parts.append(body.strip())
    return "\n\n".join(part for part in parts if part)


class ContainerView(discord.ui.LayoutView):
    """Simple Components V2 message with one visual container."""

    def __init__(
        self,
        *,
        title: str | None = None,
        body: str | None = None,
        accent_color: int | discord.Color | None = DEFAULT_ACCENT,
        image_url: str | None = None,
        rows: list[discord.ui.ActionRow] | None = None,
        extra_text: list[str] | None = None,
        timeout: float | None = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        children: list[discord.ui.Item] = []
        primary = _text(title, body)
        if primary:
            children.append(discord.ui.TextDisplay(primary[:4000]))
        for block in extra_text or []:
            if block.strip():
                children.append(discord.ui.TextDisplay(block.strip()[:4000]))
        if image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=image_url)
            children.append(gallery)
        children.extend(rows or [])
        self.container = discord.ui.Container(*children, accent_color=accent_color)
        self.add_item(self.container)


def action_row(*items: discord.ui.Item) -> discord.ui.ActionRow:
    return discord.ui.ActionRow(*items)


def text_only_view(
    *,
    title: str | None = None,
    body: str | None = None,
    accent_color: int | discord.Color | None = DEFAULT_ACCENT,
    timeout: float | None = 180,
) -> ContainerView:
    return ContainerView(
        title=title,
        body=body,
        accent_color=accent_color,
        timeout=timeout,
    )
