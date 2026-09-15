import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AutoReply


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip()


async def upsert_auto_reply(
    session: AsyncSession,
    *,
    guild_id: int,
    name: str,
    keywords: list[str],
    title: str,
    content: str,
    emoji: str | None = None,
    cooldown_seconds: int = 30,
) -> AutoReply:
    clean_name = name.strip()
    clean_keywords = [item.strip() for item in keywords if item.strip()]
    if not clean_name or not clean_keywords:
        raise ValueError("Nome e palavras-chave são obrigatórios")
    if not title.strip() or not content.strip():
        raise ValueError("Título e resposta são obrigatórios")
    if cooldown_seconds < 0:
        raise ValueError("Cooldown inválido")

    statement = (
        insert(AutoReply)
        .values(
            guild_id=guild_id,
            name=clean_name,
            keywords=clean_keywords,
            title=title.strip(),
            content=content.strip(),
            emoji=(emoji or "").strip() or None,
            cooldown_seconds=cooldown_seconds,
            active=True,
        )
        .on_conflict_do_update(
            constraint="uq_auto_reply_guild_name",
            set_={
                "keywords": clean_keywords,
                "title": title.strip(),
                "content": content.strip(),
                "emoji": (emoji or "").strip() or None,
                "cooldown_seconds": cooldown_seconds,
                "active": True,
            },
        )
        .returning(AutoReply.id)
    )
    reply_id = await session.scalar(statement)
    if reply_id is None:
        raise RuntimeError("Falha ao salvar resposta automática")
    reply = await session.get(AutoReply, reply_id)
    if reply is None:
        raise RuntimeError("Resposta automática não encontrada")
    return reply


async def find_auto_reply(
    session: AsyncSession, *, guild_id: int, message: str
) -> AutoReply | None:
    normalized_message = normalize_text(message)
    if not normalized_message:
        return None
    replies = (
        await session.scalars(
            select(AutoReply)
            .where(AutoReply.guild_id == guild_id, AutoReply.active.is_(True))
            .order_by(AutoReply.id.asc())
        )
    ).all()
    for reply in replies:
        for keyword in reply.keywords or []:
            normalized_keyword = normalize_text(str(keyword))
            if normalized_keyword and normalized_keyword in normalized_message:
                return reply
    return None
