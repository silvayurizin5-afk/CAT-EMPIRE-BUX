import re

import discord

_CUSTOM_EMOJI_RE = re.compile(
    r"^<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{1,32}):(?P<id>[0-9]{15,22})>$"
)
_EMOJI_ALIAS_RE = re.compile(r"(?<!<):(?P<name>[A-Za-z0-9_]{1,32}):(?![0-9])")


def resolve_guild_emoji_aliases(value: str, guild: discord.Guild | None) -> str:
    """Troca :nome_do_emoji: pelo mention real do emoji do servidor.

    Mentions já válidos (<:nome:id>/<a:nome:id>) são preservados. Nomes não
    encontrados também são preservados para o admin perceber o erro de digitação.
    """
    text = str(value or "")
    if not text or guild is None:
        return text

    emojis = {emoji.name.casefold(): str(emoji) for emoji in guild.emojis}

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        return emojis.get(name.casefold(), match.group(0))

    return _EMOJI_ALIAS_RE.sub(replace, text)


def select_option_emoji(value: str | None) -> discord.PartialEmoji | str | None:
    """Return only emoji values Discord accepts in select options.

    Product emoji fields are user-configurable and may contain plain text, an ID
    without a name, or malformed custom-emoji markup. Passing those values
    directly to discord.py makes the entire component payload invalid. Invalid
    values are therefore omitted instead of breaking the store/admin panel.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return None

    custom = _CUSTOM_EMOJI_RE.fullmatch(cleaned)
    if custom:
        return discord.PartialEmoji(
            name=custom.group("name"),
            id=int(custom.group("id")),
            animated=bool(custom.group("animated")),
        )

    # Unicode emoji contain at least one non-ASCII code point. Plain text such
    # as "gift" is intentionally rejected because Discord rejects it as an
    # option emoji name.
    if any(ord(char) > 127 for char in cleaned):
        return cleaned
    return None
