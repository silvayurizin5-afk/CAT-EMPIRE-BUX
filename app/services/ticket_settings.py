import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ticket_models import TicketSettings

DEFAULT_TICKET_TITLE = "Pedido {order}"
DEFAULT_TICKET_INSTRUCTIONS = (
    "{customer}, seu pedido `{order}` foi confirmado. "
    "A equipe vai continuar o atendimento por aqui."
)
ALLOWED_TICKET_TOKENS = frozenset({"customer", "order", "total", "items"})
_TOKEN_PATTERN = re.compile(r"\{([a-z_]+)\}")


@dataclass(slots=True, frozen=True)
class TicketTemplateContext:
    customer: str
    order: str
    total: str
    items: str


@dataclass(slots=True, frozen=True)
class EffectiveTicketSettings:
    title_template: str = DEFAULT_TICKET_TITLE
    instruction_template: str = DEFAULT_TICKET_INSTRUCTIONS


def validate_ticket_template(value: str, *, max_length: int, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} não pode ficar vazio")
    if len(clean) > max_length:
        raise ValueError(f"{field_name} passou do limite de {max_length} caracteres")
    unknown = sorted(set(_TOKEN_PATTERN.findall(clean)) - ALLOWED_TICKET_TOKENS)
    if unknown:
        raise ValueError("Variáveis desconhecidas: " + ", ".join(f"{{{item}}}" for item in unknown))
    return clean


def render_ticket_template(template: str, context: TicketTemplateContext) -> str:
    replacements = {
        "customer": context.customer,
        "order": context.order,
        "total": context.total,
        "items": context.items,
    }

    def replace(match: re.Match[str]) -> str:
        return replacements.get(match.group(1), match.group(0))

    return _TOKEN_PATTERN.sub(replace, template)


async def get_effective_ticket_settings(
    session: AsyncSession, *, guild_id: int
) -> EffectiveTicketSettings:
    settings = await session.scalar(select(TicketSettings).where(TicketSettings.guild_id == guild_id))
    if settings is None:
        return EffectiveTicketSettings()
    return EffectiveTicketSettings(
        title_template=settings.title_template,
        instruction_template=settings.instruction_template,
    )


async def upsert_ticket_settings(
    session: AsyncSession,
    *,
    guild_id: int,
    title_template: str,
    instruction_template: str,
) -> TicketSettings:
    title = validate_ticket_template(
        title_template,
        max_length=160,
        field_name="Título",
    )
    instructions = validate_ticket_template(
        instruction_template,
        max_length=1800,
        field_name="Mensagem",
    )
    statement = (
        insert(TicketSettings)
        .values(
            guild_id=guild_id,
            title_template=title,
            instruction_template=instructions,
        )
        .on_conflict_do_update(
            index_elements=[TicketSettings.guild_id],
            set_={
                "title_template": title,
                "instruction_template": instructions,
            },
        )
        .returning(TicketSettings.id)
    )
    settings_id = await session.scalar(statement)
    if settings_id is None:
        raise RuntimeError("Falha ao salvar mensagens do ticket")
    settings = await session.get(TicketSettings, settings_id)
    if settings is None:
        raise RuntimeError("Configuração de ticket não encontrada")
    return settings
