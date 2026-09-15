from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TermsAcceptance, TermsDocument


async def list_terms(
    session: AsyncSession,
    *,
    guild_id: int,
    limit: int = 25,
) -> list[TermsDocument]:
    return list(
        (
            await session.scalars(
                select(TermsDocument)
                .where(TermsDocument.guild_id == guild_id)
                .order_by(TermsDocument.active.desc(), TermsDocument.title, TermsDocument.id)
                .limit(max(1, min(limit, 100)))
            )
        ).all()
    )


async def set_terms_active(
    session: AsyncSession,
    *,
    terms: TermsDocument,
    active: bool,
) -> TermsDocument:
    terms.active = active
    await session.flush()
    return terms


async def list_active_terms_for_acceptance(
    session: AsyncSession,
    *,
    guild_id: int,
    limit: int = 25,
) -> list[TermsDocument]:
    return list(
        (
            await session.scalars(
                select(TermsDocument)
                .where(
                    TermsDocument.guild_id == guild_id,
                    TermsDocument.active.is_(True),
                )
                .order_by(TermsDocument.title, TermsDocument.id)
                .limit(max(1, min(limit, 25)))
            )
        ).all()
    )


async def list_missing_terms(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
) -> list[TermsDocument]:
    terms = await list_active_terms_for_acceptance(session, guild_id=guild_id)
    if not terms:
        return []

    rows = (
        await session.execute(
            select(TermsAcceptance.terms_id, TermsAcceptance.version).where(
                TermsAcceptance.user_id == user_id,
                TermsAcceptance.terms_id.in_([item.id for item in terms]),
            )
        )
    ).all()
    accepted = {(int(terms_id), int(version)) for terms_id, version in rows}
    return [item for item in terms if (item.id, item.version) not in accepted]


async def accept_current_terms(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
) -> int:
    terms = await list_active_terms_for_acceptance(session, guild_id=guild_id)
    if not terms:
        return 0

    statement = (
        insert(TermsAcceptance)
        .values(
            [
                {
                    "user_id": user_id,
                    "terms_id": item.id,
                    "version": item.version,
                }
                for item in terms
            ]
        )
        .on_conflict_do_nothing(constraint="uq_terms_acceptance_version")
    )
    await session.execute(statement)
    await session.flush()
    return len(terms)
