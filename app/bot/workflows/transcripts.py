import html
import re
from datetime import timedelta, timezone

import discord

_BRT = timezone(timedelta(hours=-3), name="BRT")
_CUSTOM_EMOJI_RE = re.compile(r"&lt;(a?):([A-Za-z0-9_]+):(\d+)&gt;")
_TICKET_TOPIC_RE = re.compile(
    r"^NEXTBUY order=([0-9a-fA-F-]{36}) customer=(\d{1,20})$"
)


def _ticket_metadata(topic: str | None) -> tuple[str | None, str | None]:
    match = _TICKET_TOPIC_RE.fullmatch((topic or "").strip())
    if match is None:
        return None, None
    return match.group(1)[:8].lower(), match.group(2)


def _format_text(value: str) -> str:
    escaped = html.escape(value or "")

    def emoji(match: re.Match[str]) -> str:
        animated, name, emoji_id = match.groups()
        extension = "gif" if animated else "webp"
        safe_name = html.escape(name)
        return (
            f'<img class="emoji" src="https://cdn.discordapp.com/emojis/{emoji_id}.{extension}" '
            f'alt=":{safe_name}:" title=":{safe_name}:">'
        )

    escaped = _CUSTOM_EMOJI_RE.sub(emoji, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    return escaped.replace("\n", "<br>")


def _component_text(components) -> list[str]:
    parts: list[str] = []

    def walk(component) -> None:
        content = getattr(component, "content", None)
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())

        children = getattr(component, "children", None)
        if children is None:
            children = getattr(component, "components", None)
        if children:
            for child in children:
                walk(child)
            return

        label = getattr(component, "label", None)
        if isinstance(label, str) and label.strip():
            parts.append(f"[Botão: {label.strip()}]")

        placeholder = getattr(component, "placeholder", None)
        if isinstance(placeholder, str) and placeholder.strip():
            parts.append(f"[Seletor: {placeholder.strip()}]")

    for component in components or []:
        walk(component)
    return parts


def _render_embed(embed: discord.Embed) -> str:
    parts: list[str] = []
    if embed.title:
        parts.append(f'<div class="embed-title">{_format_text(embed.title)}</div>')
    if embed.description:
        parts.append(f'<div class="embed-description">{_format_text(embed.description)}</div>')
    for field in embed.fields:
        parts.append(
            '<div class="embed-field">'
            f'<div class="embed-field-name">{_format_text(field.name)}</div>'
            f'<div>{_format_text(field.value)}</div>'
            "</div>"
        )
    if embed.footer and embed.footer.text:
        parts.append(f'<div class="embed-footer">{_format_text(embed.footer.text)}</div>')
    if not parts:
        return ""
    return f'<div class="embed">{"".join(parts)}</div>'


def _render_attachments(message: discord.Message) -> str:
    blocks: list[str] = []
    for attachment in message.attachments:
        url = html.escape(attachment.url, quote=True)
        filename = html.escape(attachment.filename)
        content_type = (attachment.content_type or "").lower()
        if content_type.startswith("image/"):
            blocks.append(
                '<a class="attachment image-attachment" '
                f'href="{url}" rel="noopener noreferrer">'
                f'<img src="{url}" alt="{filename}">'
                f'<span>{filename}</span></a>'
            )
        else:
            blocks.append(
                f'<a class="attachment file-attachment" href="{url}" '
                f'rel="noopener noreferrer">Arquivo: {filename}</a>'
            )
    return "".join(blocks)


def _message_body(message: discord.Message) -> str:
    blocks: list[str] = []
    if message.clean_content.strip():
        blocks.append(f'<div class="content">{_format_text(message.clean_content)}</div>')

    component_parts = _component_text(message.components)
    if component_parts:
        rendered = "<br>".join(_format_text(part) for part in component_parts)
        blocks.append(f'<div class="component-content">{rendered}</div>')

    blocks.extend(_render_embed(embed) for embed in message.embeds if _render_embed(embed))
    attachments = _render_attachments(message)
    if attachments:
        blocks.append(f'<div class="attachments">{attachments}</div>')

    if not blocks:
        blocks.append('<div class="content muted">Mensagem sem conteúdo textual.</div>')
    return "".join(blocks)


async def render_channel_transcript(channel: discord.TextChannel) -> bytes:
    messages = [message async for message in channel.history(limit=None, oldest_first=True)]
    rows: list[str] = []

    for message in messages:
        author_tag = html.escape(str(message.author))
        display = html.escape(message.author.display_name)
        avatar = html.escape(str(message.author.display_avatar.url), quote=True)
        timestamp = message.created_at.astimezone(_BRT).strftime("%d/%m/%Y • %H:%M:%S BRT")
        bot_badge = '<span class="bot-badge">BOT</span>' if message.author.bot else ""
        jump = html.escape(message.jump_url, quote=True)
        rows.append(
            '<article class="message">'
            f'<img class="avatar" src="{avatar}" alt="Avatar de {display}">'
            '<div class="message-main">'
            '<div class="meta">'
            f'<strong>{display}</strong>{bot_badge}'
            f'<span class="tag">{author_tag}</span>'
            f'<a class="timestamp" href="{jump}">{timestamp}</a>'
            "</div>"
            f'{_message_body(message)}'
            "</div></article>"
        )

    guild_name = html.escape(channel.guild.name)
    channel_name = html.escape(channel.name)
    title = f"NEXTBUY • #{channel_name}"
    topic = html.escape(channel.topic or "Sem tópico")
    message_count = len(messages)
    order_short, customer_id = _ticket_metadata(channel.topic)
    generated_at = discord.utils.utcnow().astimezone(_BRT).strftime("%d/%m/%Y • %H:%M BRT")
    context_pills = [
        f'<span class="pill">Servidor: {guild_name}</span>',
        f'<span class="pill">Mensagens: {message_count}</span>',
    ]
    if order_short:
        context_pills.append(f'<span class="pill">Pedido: #{html.escape(order_short)}</span>')
    if customer_id:
        context_pills.append(f'<span class="pill">Cliente: {html.escape(customer_id)}</span>')
    context_pills.append(f'<span class="pill">Gerado em: {generated_at}</span>')
    context_pills.append(f'<span class="pill">Tópico: {topic}</span>')
    summary_html = "".join(context_pills)

    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:dark;--bg:#0d0f14;--panel:#151820;--panel2:#1b1f29;--border:#292e3b;--text:#eef0f4;--muted:#969dab;--accent:#5865f2;--code:#252a36}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}}
header{{position:sticky;top:0;z-index:2;background:rgba(13,15,20,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}}
.header-inner{{max-width:1040px;margin:auto;padding:22px 28px}}
.brand{{font-size:12px;font-weight:800;letter-spacing:.12em;color:#aeb5c3;text-transform:uppercase}}
h1{{font-size:24px;margin:5px 0 6px}}
.summary{{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:13px}}
.pill{{background:var(--panel2);border:1px solid var(--border);padding:4px 9px;border-radius:999px}}
main{{max-width:1040px;margin:auto;padding:18px 28px 60px}}
.message{{display:flex;gap:12px;padding:16px 12px;border-radius:10px}}
.message:hover{{background:#12151c}}
.avatar{{width:42px;height:42px;border-radius:50%;object-fit:cover;flex:0 0 auto}}
.message-main{{min-width:0;flex:1}}
.meta{{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;margin-bottom:4px}}
.meta strong{{font-size:15px;color:#fff}}
.tag{{color:var(--muted);font-size:12px}}
.timestamp{{margin-left:auto;color:#737b8a;font-size:11px;text-decoration:none}}
.timestamp:hover{{text-decoration:underline}}
.bot-badge{{font-size:10px;font-weight:800;background:var(--accent);color:#fff;padding:1px 5px;border-radius:4px}}
.content,.component-content{{overflow-wrap:anywhere}}
.component-content{{margin-top:7px;padding:10px 12px;background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px}}
.muted{{color:var(--muted);font-style:italic}}
code{{background:var(--code);border:1px solid #343a48;padding:1px 5px;border-radius:5px;color:#dce1eb}}
.emoji{{width:22px;height:22px;vertical-align:-5px;object-fit:contain}}
.embed{{margin-top:8px;max-width:680px;background:var(--panel);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:8px;padding:12px 14px}}
.embed-title{{font-weight:800;font-size:16px;margin-bottom:6px}}
.embed-description{{color:#d8dbe2}}
.embed-field{{margin-top:10px}}
.embed-field-name{{font-weight:700;margin-bottom:2px}}
.embed-footer{{border-top:1px solid var(--border);margin-top:10px;padding-top:7px;color:var(--muted);font-size:11px}}
.attachments{{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}}
.attachment{{color:#b9c8ff;text-decoration:none;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
.image-attachment{{display:flex;flex-direction:column;max-width:360px}}
.image-attachment img{{display:block;max-width:100%;max-height:320px;object-fit:contain;background:#0a0c10}}
.image-attachment span,.file-attachment{{padding:8px 10px}}
.file-attachment{{display:inline-block}}
.empty{{padding:40px;text-align:center;color:var(--muted)}}
@media(max-width:640px){{.header-inner,main{{padding-left:14px;padding-right:14px}}.timestamp{{width:100%;margin-left:0}}.avatar{{width:36px;height:36px}}}}
</style>
</head>
<body>
<header><div class="header-inner">
<div class="brand">NEXTBUY • Transcript</div>
<h1>#{channel_name}</h1>
<div class="summary">{summary_html}</div>
</div></header>
<main>{''.join(rows) if rows else '<div class="empty">Nenhuma mensagem registrada neste ticket.</div>'}</main>
</body>
</html>"""
    return document.encode("utf-8")
