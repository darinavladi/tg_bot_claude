from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import Event, EventStatus, User
from bot.timeutils import REPEATS, next_occurrence


async def get_or_create_user(session: AsyncSession, user_id: int, default_tz: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, timezone=default_tz)
        session.add(user)
        await session.commit()
    return user


async def update_user(session: AsyncSession, user_id: int, **fields) -> User:
    """Меняет настройки пользователя: timezone, default_remind_min, summary_time."""
    user = await session.get(User, user_id)
    if user is None:
        raise LookupError(f"Пользователь {user_id} не найден")
    for name, value in fields.items():
        if name not in ("timezone", "default_remind_min", "summary_time"):
            raise AttributeError(name)
        setattr(user, name, value)
    await session.commit()
    return user


async def set_timezone(session: AsyncSession, user_id: int, tz: str) -> None:
    await update_user(session, user_id, timezone=tz)


async def list_summary_users(session: AsyncSession) -> list[User]:
    """Пользователи, у которых включена утренняя сводка."""
    return list(await session.scalars(select(User).where(User.summary_time.is_not(None))))


async def add_event(
    session: AsyncSession,
    user_id: int,
    title: str,
    starts_at: datetime,
    remind_before_min: int = 15,
    repeat: str | None = None,
) -> Event:
    event = Event(
        user_id=user_id,
        title=title,
        starts_at=starts_at,
        remind_before_min=remind_before_min,
        repeat=repeat,
        remind_at=starts_at - timedelta(minutes=remind_before_min),
    )
    session.add(event)
    await session.commit()
    return event


async def get_event(session: AsyncSession, user_id: int, event_id: int) -> Event | None:
    """Возвращает событие, только если оно принадлежит этому пользователю."""
    event = await session.get(Event, event_id)
    if event is None or event.user_id != user_id:
        return None
    return event


async def list_events(
    session: AsyncSession,
    user_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    status: EventStatus | None = EventStatus.ACTIVE,
) -> list[Event]:
    """События пользователя в интервале [start, end), по возрастанию времени."""
    query = select(Event).where(Event.user_id == user_id)
    if status is not None:
        query = query.where(Event.status == status)
    if start is not None:
        query = query.where(Event.starts_at >= start)
    if end is not None:
        query = query.where(Event.starts_at < end)
    result = await session.scalars(query.order_by(Event.starts_at))
    return list(result)


async def update_event(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    *,
    title: str | None = None,
    starts_at: datetime | None = None,
    remind_before_min: int | None = None,
) -> Event | None:
    event = await get_event(session, user_id, event_id)
    if event is None:
        return None
    if title is not None:
        event.title = title
    if starts_at is not None:
        event.starts_at = starts_at
    if remind_before_min is not None:
        event.remind_before_min = remind_before_min
    if starts_at is not None or remind_before_min is not None:
        event.remind_at = event.starts_at - timedelta(minutes=event.remind_before_min)
    await session.commit()
    return event


async def _set_status(
    session: AsyncSession, user_id: int, event_id: int, status: EventStatus
) -> bool:
    event = await get_event(session, user_id, event_id)
    if event is None:
        return False
    event.status = status
    event.remind_at = None
    await session.commit()
    return True


async def mark_done(session: AsyncSession, user_id: int, event_id: int) -> bool:
    return await _set_status(session, user_id, event_id, EventStatus.DONE)


async def delete_event(session: AsyncSession, user_id: int, event_id: int) -> bool:
    """Мягкое удаление: событие помечается отменённым и пропадает из списков."""
    return await _set_status(session, user_id, event_id, EventStatus.CANCELLED)


async def set_remind_at(session: AsyncSession, event_id: int, remind_at: datetime | None) -> None:
    event = await session.get(Event, event_id)
    if event is not None:
        event.remind_at = remind_at
        await session.commit()


async def list_pending_reminders(session: AsyncSession) -> list[Event]:
    """Все активные события, по которым ещё нужно прислать напоминание."""
    query = select(Event).where(Event.status == EventStatus.ACTIVE, Event.remind_at.is_not(None))
    return list(await session.scalars(query.order_by(Event.remind_at)))


async def set_repeat(
    session: AsyncSession, user_id: int, event_id: int, repeat: str | None
) -> Event | None:
    if repeat is not None and repeat not in REPEATS:
        raise ValueError(repeat)
    event = await get_event(session, user_id, event_id)
    if event is None:
        return None
    event.repeat = repeat
    await session.commit()
    return event


async def roll_recurring(session: AsyncSession, now: datetime) -> list[Event]:
    """Переносит прошедшие повторяющиеся события на следующий раз.

    Событие переносится, когда его время прошло больше минуты назад; напоминание
    ставится заново по настройке события. Возвращает перенесённые события.
    """
    query = (
        select(Event, User.timezone)
        .join(User, User.id == Event.user_id)
        .where(
            Event.status == EventStatus.ACTIVE,
            Event.repeat.is_not(None),
            Event.starts_at < now - timedelta(minutes=1),
            # Отложенное («💤») напоминание сначала должно прийти
            or_(Event.remind_at.is_(None), Event.remind_at <= now),
        )
    )
    rolled = []
    for event, tz in (await session.execute(query)).all():
        event.starts_at = next_occurrence(event.starts_at, event.repeat, tz, now)
        event.remind_at = event.starts_at - timedelta(minutes=event.remind_before_min)
        rolled.append(event)
    if rolled:
        await session.commit()
    return rolled
