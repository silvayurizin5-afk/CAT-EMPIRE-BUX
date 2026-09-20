"""Never report an archive as saved until Discord has accepted the attachment."""

import gzip
import io

import discord

from app.bot.workflows.transcripts import render_channel_transcript


async def archive_ticket(
    channel: discord.TextChannel,
    target_id: int | None,
    *,
    required: bool = True,
    summary: dict | None = None,
) -> str | None:
    if not target_id:
        if required:
            raise ValueError("Configure o canal de transcripts antes de fechar ou excluir tickets.")
        return None
    if target_id == channel.id:
        raise ValueError("O canal de transcripts não pode ser o próprio ticket.")
    target = channel.guild.get_channel(target_id)
    if target is None:
        target = await channel.guild.fetch_channel(target_id)
    if not isinstance(target, discord.TextChannel):
        raise ValueError("O canal de transcripts configurado não é um canal de texto.")
    data = await render_channel_transcript(channel, summary=summary)
    filename = f"transcript-{channel.id}.html"
    if len(data) > channel.guild.filesize_limit:
        data = gzip.compress(data)
        filename += ".gz"
    if len(data) > channel.guild.filesize_limit:
        raise ValueError("Transcript excede o limite de upload mesmo compactado. Canal preservado.")
    message = await target.send(
        content=f"**Transcript • {discord.utils.escape_markdown(channel.name)}**",
        file=discord.File(io.BytesIO(data), filename=filename),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return message.jump_url
