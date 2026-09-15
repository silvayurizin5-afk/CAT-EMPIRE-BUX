from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.faq_models import AutoReplyButton


@dataclass(slots=True, frozen=True)
class AutoReplyButtonInput:
    label: str
    url: str
    emoji: str | None = None


def _validate_button(button: AutoReplyButtonInput) -> AutoReplyButtonInput:
    label = button.label.strip()
    url = button.url.strip()
    emoji = (button.emoji or "").strip() or None

    if not label or len(label) > 80:
        raise ValueError("Cada botão precisa de um nome com até 80 caracteres")
    if len(url) > 512:
        raise ValueError("URL de botão muito grande")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Os botões do FAQ aceitam somente URLs HTTPS válidas")
    if emoji is not None and len(emoji) > 128:
        raise ValueError("Emoji de botão muito grande")
    return AutoReplyButtonInput(label=label, url=url, emoji=emoji)


def parse_auto_reply_buttons(text: str) -> list[AutoReplyButtonInput]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 5:
        raise ValueError("Use no máximo 5 botões por resposta")

    buttons: list[AutoReplyButtonInput] = []
    for line in lines:
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) < 2:
            raise ValueError(
                "Formato inválido. Use `Nome | https://link` ou `Nome | https://link | emoji`."
            )
        emoji = parts[2] if len(parts) == 3 else None
        buttons.append(
            _validate_button(
                AutoReplyButtonInput(
                    label=parts[0],
                    url=parts[1],
                    emoji=emoji,
                )
            )
        )
    return buttons


def serialize_auto_reply_buttons(buttons: list[AutoReplyButton]) -> str:
    lines = []
    for button in buttons:
        line = f"{button.label} | {button.url}"
        if button.emoji:
            line += f" | {button.emoji}"
        lines.append(line)
    return "\n".join(lines)


async def list_auto_reply_buttons(
    session: AsyncSession,
    *,
    auto_reply_id: int,
) -> list[AutoReplyButton]:
    return list(
        (
            await session.scalars(
                select(AutoReplyButton)
                .where(AutoReplyButton.auto_reply_id == auto_reply_id)
                .order_by(AutoReplyButton.sort_order, AutoReplyButton.id)
            )
        ).all()
    )


async def set_auto_reply_buttons(
    session: AsyncSession,
    *,
    auto_reply_id: int,
    buttons: list[AutoReplyButtonInput],
) -> list[AutoReplyButton]:
    if len(buttons) > 5:
        raise ValueError("Use no máximo 5 botões por resposta")
    validated = [_validate_button(button) for button in buttons]

    await session.execute(
        delete(AutoReplyButton).where(AutoReplyButton.auto_reply_id == auto_reply_id)
    )
    models = [
        AutoReplyButton(
            auto_reply_id=auto_reply_id,
            label=button.label,
            url=button.url,
            emoji=button.emoji,
            sort_order=index,
        )
        for index, button in enumerate(validated)
    ]
    session.add_all(models)
    await session.flush()
    return models
