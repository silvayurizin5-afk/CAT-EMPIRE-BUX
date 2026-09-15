from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback, FeedbackReminder, GuildConfig, Order, User


async def schedule_feedback_reminder(
    session: AsyncSession,
    *,
    order: Order,
    delay_minutes: int,
) -> FeedbackReminder:
    existing = await session.scalar(
        select(FeedbackReminder).where(FeedbackReminder.order_id == order.id)
    )
    if existing is not None:
        return existing

    reminder = FeedbackReminder(
        order_id=order.id,
        user_id=order.user_id,
        due_at=datetime.now(UTC) + timedelta(minutes=max(1, delay_minutes)),
    )
    session.add(reminder)
    await session.flush()
    return reminder


async def submit_feedback(
    session: AsyncSession,
    *,
    order_id,
    user_id: int,
    stars: int,
    comment: str,
    source: str,
    published_message_id: int | None = None,
) -> Feedback:
    if stars < 1 or stars > 5:
        raise ValueError("A nota precisa estar entre 1 e 5")
    cleaned = comment.strip()
    if not cleaned:
        raise ValueError("O comentário não pode ficar vazio")

    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None or order.user_id != user_id:
        raise ValueError("Pedido inválido para este cliente")
    if order.status != "delivered":
        raise ValueError("O pedido ainda não foi entregue")

    existing = await session.scalar(select(Feedback).where(Feedback.order_id == order_id))
    if existing is not None:
        return existing

    feedback = Feedback(
        order_id=order_id,
        user_id=user_id,
        stars=stars,
        comment=cleaned[:4000],
        source=source,
        published_message_id=published_message_id,
    )
    session.add(feedback)

    reminder = await session.scalar(
        select(FeedbackReminder).where(FeedbackReminder.order_id == order_id).with_for_update()
    )
    if reminder is not None:
        reminder.completed_at = datetime.now(UTC)
    await session.flush()
    return feedback


async def find_pending_order_for_feedback(
    session: AsyncSession,
    *,
    guild_id: int,
    discord_user_id: int,
) -> tuple[Order, User, FeedbackReminder] | None:
    row = (
        await session.execute(
            select(Order, User, FeedbackReminder)
            .join(User, User.id == Order.user_id)
            .join(FeedbackReminder, FeedbackReminder.order_id == Order.id)
            .outerjoin(Feedback, Feedback.order_id == Order.id)
            .where(
                Order.guild_id == guild_id,
                User.discord_user_id == discord_user_id,
                Order.status == "delivered",
                Feedback.id.is_(None),
                FeedbackReminder.completed_at.is_(None),
            )
            .order_by(FeedbackReminder.due_at.asc())
            .limit(1)
        )
    ).first()
    return row if row is not None else None


async def due_channel_reminders(
    session: AsyncSession, *, now: datetime, limit: int = 50
) -> list[tuple[FeedbackReminder, Order, User, GuildConfig]]:
    rows = await session.execute(
        select(FeedbackReminder, Order, User, GuildConfig)
        .join(Order, Order.id == FeedbackReminder.order_id)
        .join(User, User.id == FeedbackReminder.user_id)
        .join(GuildConfig, GuildConfig.guild_id == Order.guild_id)
        .outerjoin(Feedback, Feedback.order_id == Order.id)
        .where(
            Order.status == "delivered",
            FeedbackReminder.completed_at.is_(None),
            FeedbackReminder.channel_mention_sent_at.is_(None),
            FeedbackReminder.due_at <= now,
            Feedback.id.is_(None),
        )
        .order_by(FeedbackReminder.due_at.asc())
        .limit(limit)
    )
    return list(rows.all())


async def due_dm_reminders(
    session: AsyncSession, *, now: datetime, limit: int = 50
) -> list[tuple[FeedbackReminder, Order, User, GuildConfig]]:
    rows = await session.execute(
        select(FeedbackReminder, Order, User, GuildConfig)
        .join(Order, Order.id == FeedbackReminder.order_id)
        .join(User, User.id == FeedbackReminder.user_id)
        .join(GuildConfig, GuildConfig.guild_id == Order.guild_id)
        .outerjoin(Feedback, Feedback.order_id == Order.id)
        .where(
            Order.status == "delivered",
            FeedbackReminder.completed_at.is_(None),
            FeedbackReminder.channel_mention_sent_at.is_not(None),
            FeedbackReminder.dm_sent_at.is_(None),
            Feedback.id.is_(None),
        )
        .order_by(FeedbackReminder.channel_mention_sent_at.asc())
        .limit(limit)
    )
    result: list[tuple[FeedbackReminder, Order, User, GuildConfig]] = []
    for reminder, order, user, config in rows.all():
        cooldown = timedelta(hours=max(1, config.feedback_dm_cooldown_hours))
        if reminder.channel_mention_sent_at and reminder.channel_mention_sent_at + cooldown <= now:
            result.append((reminder, order, user, config))
    return result
