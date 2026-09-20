from __future__ import annotations

from collections.abc import Iterable

import discord

BRAND_ACCENT_HEX = 0x7B2CBF
DEFAULT_ACCENT = discord.Colour(BRAND_ACCENT_HEX)


def format_percent(value) -> str:
    """Formata porcentagens sem zeros desnecessários (10.00 -> 10%)."""
    number = float(value)
    if number.is_integer():
        return f"{int(number)}%"
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def text_block(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


class CardLayout(discord.ui.LayoutView):
    """Container V2 simples para mensagens sem controles específicos."""

    def __init__(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        lines: Iterable[str] = (),
        footer: str | None = None,
        accent_colour: discord.Colour | int | None = DEFAULT_ACCENT,
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        timeout: float | None = 180,
    ) -> None:
        super().__init__(timeout=timeout)

        body_parts: list[str] = []
        if title:
            body_parts.append(f"## {title}")
        if description:
            body_parts.append(description)
        body_parts.extend(line for line in lines if line)

        children: list[discord.ui.Item] = []
        content = "\n".join(body_parts).strip() or "\u200b"
        if thumbnail_url:
            children.append(
                discord.ui.Section(
                    discord.ui.TextDisplay(content),
                    accessory=discord.ui.Thumbnail(thumbnail_url),
                )
            )
        else:
            children.append(discord.ui.TextDisplay(content))

        if image_url:
            children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))
        if footer:
            children.append(discord.ui.Separator())
            children.append(discord.ui.TextDisplay(f"-# {footer}"))

        self.container = discord.ui.Container(*children, accent_colour=DEFAULT_ACCENT)
        self.add_item(self.container)


def add_action_row(container: discord.ui.Container, *items: discord.ui.Item) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow(*items)
    container.add_item(row)
    return row


def add_select_row(container: discord.ui.Container, select: discord.ui.Select) -> discord.ui.ActionRow:
    return add_action_row(container, select)


def strip_generic_emoji(text: str) -> str:
    """Remove alguns emojis Unicode comuns de respostas livres da IA.

    Emojis customizados do Discord (<:nome:id>/<a:nome:id>) não são alterados.
    """
    ranges = (
        (0x1F300, 0x1FAFF),
        (0x2600, 0x27BF),
    )
    return "".join(
        ch
        for ch in text
        if not any(start <= ord(ch) <= end for start, end in ranges)
    ).strip()
