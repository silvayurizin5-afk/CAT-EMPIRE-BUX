"""Validated settings and database locking shared by ticket entry points."""

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.ticket_models import SupportTicket, TicketSettings


@dataclass(frozen=True, slots=True)
class SupportOptions:
    enabled: bool = True
    panel_title: str = "Central de atendimento"
    panel_description: str = "Precisa de ajuda? Abra um ticket privado com nossa equipe."
    button_label: str = "Abrir ticket"
    button_emoji: str = "🎫"
    welcome: str = "{customer}, descreva sua dúvida. Nossa equipe vai atender você por aqui."
    color: str = "5865F2"
    banner_url: str = ""
    max_open: int = 1
    cooldown_seconds: int = 60
    transcript_required: bool = True
    customer_can_close: bool = True
    closed_category_id: int | None = None


def validate_options(values: dict) -> SupportOptions:
    unknown = set(values) - set(asdict(SupportOptions()))
    if unknown:
        raise ValueError("Configuração desconhecida: " + ", ".join(sorted(unknown)))
    merged = asdict(SupportOptions()) | values
    for key, limit in {
        "panel_title": 160,
        "panel_description": 1800,
        "button_label": 80,
        "button_emoji": 100,
        "welcome": 1800,
        "banner_url": 1000,
    }.items():
        if not isinstance(merged[key], str):
            raise ValueError(f"{key}: informe um texto.")
        merged[key] = merged[key].strip()
        if len(merged[key]) > limit or (
            not merged[key] and key not in {"banner_url", "button_emoji"}
        ):
            raise ValueError(f"{key}: texto vazio ou maior que {limit} caracteres.")
    for key, low, high in (("max_open", 1, 10), ("cooldown_seconds", 0, 86400)):
        if type(merged[key]) is not int or not low <= merged[key] <= high:
            raise ValueError(f"{key}: informe um número entre {low} e {high}.")
    for key in ("enabled", "transcript_required", "customer_can_close"):
        if type(merged[key]) is not bool:
            raise ValueError(f"{key}: informe sim ou não.")
    color = str(merged["color"]).removeprefix("#")
    if len(color) != 6 or any(c not in "0123456789abcdefABCDEF" for c in color):
        raise ValueError("A cor deve ter seis dígitos hexadecimais, exemplo: 5865F2.")
    merged["color"] = color
    if merged["banner_url"]:
        url = urlsplit(merged["banner_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Use um link HTTPS de imagem para o banner.")
    category = merged["closed_category_id"]
    if category is not None and (type(category) is not int or category <= 0):
        raise ValueError("Categoria de arquivamento inválida.")
    return SupportOptions(**merged)


async def get_support_options(session, guild_id: int) -> SupportOptions:
    row = await session.scalar(select(TicketSettings).where(TicketSettings.guild_id == guild_id))
    return validate_options(row.options or {}) if row else SupportOptions()


async def save_support_options(session, guild_id: int, patch: dict) -> SupportOptions:
    # Serialize partial edits: a visual edit must not overwrite a concurrent policy edit.
    await ticket_lock(session, "settings", guild_id)
    current = await get_support_options(session, guild_id)
    options = validate_options(asdict(current) | patch)
    await session.execute(
        insert(TicketSettings)
        .values(guild_id=guild_id, options=asdict(options))
        .on_conflict_do_update(
            index_elements=[TicketSettings.guild_id], set_={"options": asdict(options)}
        )
    )
    return options


async def ticket_lock(session, *parts) -> None:
    key = int.from_bytes(
        hashlib.blake2b(":".join(map(str, parts)).encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def check_open_limit(
    session, guild_id: int, customer_id: int, options: SupportOptions, *, reopening: bool = False
) -> None:
    # Caller holds the customer lock through channel creation and commit.
    count = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(
            SupportTicket.guild_id == guild_id,
            SupportTicket.customer_id == customer_id,
            SupportTicket.state == "open",
        )
    )
    if count >= options.max_open:
        raise ValueError(f"Limite de {options.max_open} ticket(s) aberto(s) por cliente atingido.")
    if not reopening and options.cooldown_seconds:
        latest = await session.scalar(
            select(func.max(SupportTicket.created_at)).where(
                SupportTicket.guild_id == guild_id, SupportTicket.customer_id == customer_id
            )
        )
        if latest and (datetime.now(UTC) - latest).total_seconds() < options.cooldown_seconds:
            raise ValueError(f"Aguarde {options.cooldown_seconds} segundos entre aberturas.")
