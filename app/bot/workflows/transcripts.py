import html
from datetime import UTC

import discord


async def render_channel_transcript(channel: discord.TextChannel) -> bytes:
    rows: list[str] = []
    async for message in channel.history(limit=None, oldest_first=True):
        author = html.escape(str(message.author))
        display = html.escape(message.author.display_name)
        timestamp = message.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        content = html.escape(message.clean_content).replace("\n", "<br>")
        attachments = "".join(
            (
                f'<div class="attachment"><a href="{html.escape(a.url, quote=True)}" '
                f'rel="noopener noreferrer">{html.escape(a.filename)}</a></div>'
            )
            for a in message.attachments
        )
        if not content and message.embeds:
            content = "<em>[embed]</em>"
        rows.append(
            "<article class='message'>"
            f"<div class='meta'><strong>{display}</strong> <span>@{author}</span> "
            f"<time>{timestamp}</time></div>"
            f"<div class='content'>{content}</div>{attachments}</article>"
        )

    title = html.escape(f"NEXTBUY • #{channel.name}")
    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#111318;color:#e8e9ed;font:15px system-ui,Arial,sans-serif}}
main{{max-width:980px;margin:auto;padding:28px}}
h1{{font-size:22px;margin:0 0 24px}}
.message{{padding:12px 14px;border-bottom:1px solid #252933}}
.meta{{color:#a9afbc;font-size:12px;margin-bottom:5px}}
.meta strong{{color:#f4f5f7;font-size:14px}} .meta time{{float:right}}
.content{{white-space:normal;overflow-wrap:anywhere}}
a{{color:#8ab4ff}} .attachment{{margin-top:6px}}
</style>
</head>
<body><main><h1>{title}</h1>{''.join(rows)}</main></body>
</html>"""
    return document.encode("utf-8")
