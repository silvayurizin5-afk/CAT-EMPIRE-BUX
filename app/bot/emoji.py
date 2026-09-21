import re

import discord

_CUSTOM_EMOJI_RE = re.compile(
    r"^<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{1,32}):(?P<id>[0-9]{15,22})>$"
)
_EMOJI_ALIAS_RE = re.compile(r"(?<!<):(?P<name>[A-Za-z0-9_]{1,32}):(?![0-9])")
_DISCORD_EMOJI_URL_RE = re.compile(
    r"^https?://[^/]*discord(?:app)?\.(?:com|net)/emojis/"
    r"(?P<id>[0-9]{15,22})\.(?P<ext>png|webp|gif)(?:\?.*)?$",
    re.IGNORECASE,
)


def _guild_emoji(
    guild: discord.Guild | None,
    *,
    name: str | None = None,
    emoji_id: int | None = None,
):
    if guild is None:
        return None
    for emoji in guild.emojis:
        if emoji_id is not None and emoji.id == emoji_id:
            return emoji
        if name is not None and emoji.name.casefold() == name.casefold():
            return emoji
    return None


def _configured_guild_emoji(value: str, guild: discord.Guild | None):
    if guild is None:
        return None

    alias = re.fullmatch(r":(?P<name>[A-Za-z0-9_]{1,32}):", value)
    if alias:
        return _guild_emoji(guild, name=alias.group("name"))

    if value.isdigit():
        return _guild_emoji(guild, emoji_id=int(value))

    cdn = _DISCORD_EMOJI_URL_RE.fullmatch(value)
    if cdn:
        return _guild_emoji(guild, emoji_id=int(cdn.group("id")))

    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", value):
        return _guild_emoji(guild, name=value)

    return None


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


def emoji_display_value(value: str | None, guild: discord.Guild | None) -> str:
    """Normaliza qualquer formato configurável de emoji para texto exibível."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""

    if _CUSTOM_EMOJI_RE.fullmatch(cleaned):
        return cleaned

    configured = _configured_guild_emoji(cleaned, guild)
    if configured is not None:
        return str(configured)

    return cleaned


def select_option_emoji(
    value: str | None,
    guild: discord.Guild | None = None,
) -> discord.PartialEmoji | str | None:
    """Converte emojis configuráveis para um valor aceito por SelectOption.

    Aceita Unicode, mention customizado estático/animado, :alias:, nome do emoji,
    ID do emoji e URL CDN de emoji do Discord. Formatos que o Discord não aceita
    como emoji de opção são omitidos para não invalidar o componente inteiro.
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

    configured = _configured_guild_emoji(cleaned, guild)
    if configured is not None:
        return discord.PartialEmoji(
            name=configured.name,
            id=configured.id,
            animated=bool(getattr(configured, "animated", False)),
        )

    if cleaned.startswith(("http://", "https://")):
        return None

    # Unicode emoji contêm ao menos um codepoint não ASCII. Texto ASCII simples
    # não é enviado como emoji porque a API do Discord rejeita esse formato.
    if any(ord(char) > 127 for char in cleaned):
        return cleaned
    return None
