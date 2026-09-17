from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.db.models import OrderItem, Product, RobuxRate
from app.db.store_models import StoreCoupon, StorePanelConfig
from app.services.catalog import list_active_products
from app.services.orders import create_product_order, create_robux_order


def normalize_coupon_code(value: str) -> str:
    return value.strip().upper().replace(" ", "")[:40]


def discounted_total(total: Decimal, discount_percent: Decimal) -> Decimal:
    base = money(total)
    percent = Decimal(discount_percent)
    if percent <= 0 or percent > 100:
        raise ValueError("Percentual de desconto inválido")
    discount = money(base * percent / Decimal("100"))
    return max(Decimal("0.00"), money(base - discount))


async def get_or_create_store_panel(
    session: AsyncSession, guild_id: int
) -> StorePanelConfig:
    statement = (
        insert(StorePanelConfig)
        .values(guild_id=guild_id, selected_product_ids=[])
        .on_conflict_do_nothing(index_elements=[StorePanelConfig.guild_id])
        .returning(StorePanelConfig.id)
    )
    config_id = await session.scalar(statement)
    if config_id is None:
        config_id = await session.scalar(
            select(StorePanelConfig.id).where(StorePanelConfig.guild_id == guild_id)
        )
    if config_id is None:
        raise RuntimeError("Falha ao criar configuração do painel da loja")
    config = await session.get(AIConfig, config_id) if False else await session.get(StorePanelConfig, config_id)
    if config is None:
        raise RuntimeError("Configuração do painel da loja não encontrada")
    return config


async def list_store_products(
    session: AsyncSession,
    *,
    guild_id: int,
    config: StorePanelConfig | None = None,
    limit: int = 25,
) -> list[Product]:
    config = config or await get_or_create_store_panel(session, guild_id)
    selected_ids = [int(item) for item in (config.selected_product_ids or [])]
    if not selected_ids:
        return await list_active_products(session, guild_id=guild_id, limit=limit)

    products = list(
        (
            await session.scalars(
                select(Product).where(
                    Product.guild_id == guild_id,
                    Product.id.in_(selected_ids),
                    Product.active.is_(True),
                    (Product.stock_quantity.is_(None) | (Product.stock_quantity > 0)),
                )
            )
        ).all()
    )
    order = {product_id: index for index, product_id in enumerate(selected_ids)}
    products.sort(key=lambda product: order.get(product.id, len(order)))
    return products[:limit]


async def save_store_product_selection(
    session: AsyncSession,
    *,
    config: StorePanelConfig,
    product_ids: list[int],
) -> StorePanelConfig:
    unique_ids = list(dict.fromkeys(int(item) for item in product_ids))[:25]
    if unique_ids:
        valid_ids = set(
            (
                await session.scalars(
                    select(Product.id).where(
                        Product.guild_id == config.guild_id,
                        Product.id.in_(unique_ids),
                    )
                )
            ).all()
        )
        unique_ids = [item for item in unique_ids if item in valid_ids]
    config.selected_product_ids = unique_ids
    await session.flush()
    return config


async def upsert_coupon(
    session: AsyncSession,
    *,
    guild_id: int,
    code: str,
    discount_percent: Decimal,
    max_uses: int | None,
) -> StoreCoupon:
    normalized = normalize_coupon_code(code)
    if not normalized:
        raise ValueError("Código do cupom é obrigatório")
    percent = money(discount_percent)
    if percent <= 0 or percent > 100:
        raise ValueError("O desconto deve ficar entre 0,01% e 100%")
    if max_uses is not None and max_uses <= 0:
        raise ValueError("O limite de usos deve ser maior que zero")

    coupon = await session.scalar(
        select(StoreCoupon).where(
            StoreCoupon.guild_id == guild_id,
            StoreCoupon.code == normalized,
        )
    )
    if coupon is None:
        coupon = StoreCoupon(guild_id=guild_id, code=normalized)
        session.add(coupon)
    coupon.discount_percent = percent
    coupon.max_uses = max_uses
    coupon.active = True
    await session.flush()
    return coupon


async def list_coupons(
    session: AsyncSession, *, guild_id: int, limit: int = 25
) -> list[StoreCoupon]:
    return list(
        (
            await session.scalars(
                select(StoreCoupon)
                .where(StoreCoupon.guild_id == guild_id)
                .order_by(StoreCoupon.active.desc(), StoreCoupon.code)
                .limit(max(1, min(limit, 25)))
            )
        ).all()
    )


async def get_coupon_by_code(
    session: AsyncSession, *, guild_id: int, code: str
) -> StoreCoupon | None:
    normalized = normalize_coupon_code(code)
    if not normalized:
        return None
    coupon = await session.scalar(
        select(StoreCoupon).where(
            StoreCoupon.guild_id == guild_id,
            StoreCoupon.code == normalized,
        )
    )
    if coupon is None or not coupon.active:
        return None
    if coupon.max_uses is not None and coupon.uses >= coupon.max_uses:
        return None
    return coupon


async def set_coupon_active(
    session: AsyncSession, *, coupon: StoreCoupon, active: bool
) -> StoreCoupon:
    coupon.active = active
    await session.flush()
    return coupon


async def _claim_coupon(
    session: AsyncSession,
    *,
    guild_id: int,
    coupon_id: int | None,
) -> StoreCoupon | None:
    if coupon_id is None:
        return None
    coupon = await session.scalar(
        select(StoreCoupon).where(StoreCoupon.id == coupon_id).with_for_update()
    )
    if coupon is None or coupon.guild_id != guild_id or not coupon.active:
        raise ValueError("Cupom indisponível")
    if coupon.max_uses is not None and coupon.uses >= coupon.max_uses:
        raise ValueError("Esse cupom atingiu o limite de usos")
    coupon.uses += 1
    return coupon


async def create_store_product_order(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    product: Product,
    quantity: int = 1,
    coupon_id: int | None = None,
) -> tuple[object, StoreCoupon | None]:
    if quantity <= 0 or quantity > 99:
        raise ValueError("Quantidade inválida")
    order = await create_product_order(
        session,
        guild_id=guild_id,
        user_id=user_id,
        product=product,
        quantity=quantity,
    )
    coupon = await _claim_coupon(session, guild_id=guild_id, coupon_id=coupon_id)
    if coupon is None:
        return order, None

    original_total = money(order.total_credits)
    final_total = discounted_total(original_total, coupon.discount_percent)
    order.total_credits = final_total
    item = await session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    if item is not None:
        metadata = dict(item.metadata_json or {})
        metadata.update(
            {
                "coupon_code": coupon.code,
                "discount_percent": str(coupon.discount_percent),
                "original_total": str(original_total),
                "discounted_total": str(final_total),
            }
        )
        item.metadata_json = metadata
    await session.flush()
    return order, coupon


async def create_store_robux_order(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    rate: RobuxRate,
    robux: int,
    coupon_id: int | None = None,
) -> tuple[object, StoreCoupon | None]:
    order = await create_robux_order(
        session,
        guild_id=guild_id,
        user_id=user_id,
        rate=rate,
        robux=robux,
    )
    coupon = await _claim_coupon(session, guild_id=guild_id, coupon_id=coupon_id)
    if coupon is None:
        return order, None

    original_total = money(order.total_credits)
    final_total = discounted_total(original_total, coupon.discount_percent)
    order.total_credits = final_total
    item = await session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    if item is not None:
        metadata = dict(item.metadata_json or {})
        metadata.update(
            {
                "coupon_code": coupon.code,
                "discount_percent": str(coupon.discount_percent),
                "original_total": str(original_total),
                "discounted_total": str(final_total),
            }
        )
        item.metadata_json = metadata
    await session.flush()
    return order, coupon
