from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import Wallet, WalletTransaction


class InsufficientCreditsError(ValueError):
    pass


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    balance = await session.scalar(select(Wallet.balance).where(Wallet.user_id == user_id))
    return money(balance or ZERO)


async def apply_wallet_transaction(
    session: AsyncSession,
    *,
    user_id: int,
    amount: Decimal,
    kind: str,
    reference: str,
    details: dict | None = None,
) -> WalletTransaction:
    amount = money(amount)
    if amount == ZERO:
        raise ValueError("Transação de carteira não pode ter valor zero")

    existing = await session.scalar(
        select(WalletTransaction).where(WalletTransaction.reference == reference)
    )
    if existing is not None:
        return existing

    wallet = await session.scalar(
        select(Wallet).where(Wallet.user_id == user_id).with_for_update()
    )
    if wallet is None:
        raise RuntimeError("Carteira não encontrada")

    existing = await session.scalar(
        select(WalletTransaction).where(WalletTransaction.reference == reference)
    )
    if existing is not None:
        return existing

    new_balance = money(wallet.balance + amount)
    if new_balance < ZERO:
        raise InsufficientCreditsError("Saldo de créditos insuficiente")

    wallet.balance = new_balance
    transaction = WalletTransaction(
        user_id=user_id,
        kind=kind,
        amount=amount,
        balance_after=new_balance,
        reference=reference,
        details=details or {},
    )
    session.add(transaction)
    await session.flush()
    return transaction
