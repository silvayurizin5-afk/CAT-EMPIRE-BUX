from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, Wallet


async def get_or_create_user(session: AsyncSession, discord_user_id: int) -> User:
    statement = (
        insert(User)
        .values(discord_user_id=discord_user_id)
        .on_conflict_do_nothing(index_elements=[User.discord_user_id])
        .returning(User.id)
    )
    user_id = await session.scalar(statement)

    if user_id is None:
        user_id = await session.scalar(select(User.id).where(User.discord_user_id == discord_user_id))
    if user_id is None:
        raise RuntimeError("Falha ao criar ou localizar usuário")

    await session.execute(
        insert(Wallet).values(user_id=user_id).on_conflict_do_nothing(index_elements=[Wallet.user_id])
    )
    user = await session.get(User, user_id)
    if user is None:
        raise RuntimeError("Usuário desapareceu durante a transação")
    return user
