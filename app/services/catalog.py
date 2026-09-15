from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.db.models import Product, RobuxRate, TermsDocument


async def list_active_products(
    session: AsyncSession,
    *,
    guild_id: int,
    product_type: str | None = None,
    limit: int = 25,
) -> list[Product]:
    stmt = select(Product).where(Product.guild_id == guild_id, Product.active.is_(True))
    if product_type:
        stmt = stmt.where(Product.product_type == product_type)
    stmt = stmt.order_by(Product.sort_order, Product.name).limit(limit)
    return list((await session.scalars(stmt)).all())


async def list_product_types(session: AsyncSession, *, guild_id: int) -> list[str]:
    rows = await session.scalars(
        select(Product.product_type)
        .where(Product.guild_id == guild_id, Product.active.is_(True))
        .distinct()
        .order_by(Product.product_type)
    )
    return list(rows.all())


async def create_product(
    session: AsyncSession,
    *,
    guild_id: int,
    name: str,
    slug: str,
    product_type: str,
    price_credits: Decimal | None,
    game_name: str | None = None,
    description: str = "",
) -> Product:
    product = Product(
        guild_id=guild_id,
        name=name.strip(),
        slug=slug.strip().lower(),
        product_type=product_type.strip().lower(),
        price_credits=money(price_credits) if price_credits is not None else None,
        game_name=(game_name or "").strip() or None,
        description=description.strip(),
    )
    session.add(product)
    await session.flush()
    return product


async def upsert_robux_rate(
    session: AsyncSession,
    *,
    guild_id: int,
    code: str,
    label: str,
    price_per_robux: Decimal,
    delivery_label: str | None,
) -> RobuxRate:
    normalized_code = code.strip().lower()
    rate = await session.scalar(
        select(RobuxRate).where(
            RobuxRate.guild_id == guild_id,
            RobuxRate.code == normalized_code,
        )
    )
    if rate is None:
        rate = RobuxRate(guild_id=guild_id, code=normalized_code, label=label.strip())
        session.add(rate)
    rate.label = label.strip()
    rate.price_per_robux = price_per_robux
    rate.delivery_label = (delivery_label or "").strip() or None
    rate.active = True
    await session.flush()
    return rate


async def upsert_terms(
    session: AsyncSession,
    *,
    guild_id: int,
    code: str,
    title: str,
    content: str,
    emoji: str | None,
) -> TermsDocument:
    normalized_code = code.strip().lower()
    terms = await session.scalar(
        select(TermsDocument).where(
            TermsDocument.guild_id == guild_id,
            TermsDocument.code == normalized_code,
        )
    )
    if terms is None:
        terms = TermsDocument(
            guild_id=guild_id,
            code=normalized_code,
            title=title.strip(),
            content=content.strip(),
        )
        session.add(terms)
    else:
        terms.version += 1
    terms.title = title.strip()
    terms.content = content.strip()
    terms.emoji = (emoji or "").strip() or None
    terms.active = True
    await session.flush()
    return terms


async def list_active_terms(session: AsyncSession, *, guild_id: int) -> list[TermsDocument]:
    return list(
        (
            await session.scalars(
                select(TermsDocument)
                .where(TermsDocument.guild_id == guild_id, TermsDocument.active.is_(True))
                .order_by(TermsDocument.title)
                .limit(25)
            )
        ).all()
    )
