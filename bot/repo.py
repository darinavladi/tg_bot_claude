from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import DEFAULT_CATEGORIES, Category, Event, EventStatus, User
from bot.timeutils import is_repeat, next_occurrence, occurrences

_KEEP = object()  # «не менять» для необязательных аргументов, где None — тоже значение


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
    category_id: int | None = None,
    ends_at: datetime | None = None,
) -> Event:
    event = Event(
        user_id=user_id,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        remind_before_min=remind_before_min,
        repeat=repeat,
        repeat_anchor=starts_at,
        category_id=category_id,
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


async def list_occurrences(
    session: AsyncSession, user_id: int, start: datetime, end: datetime, tz: str
) -> list[tuple[datetime, Event]]:
    """Активные события в интервале [start, end) с каждым повторением отдельно.

    Возвращает пары (время повторения, событие) по возрастанию времени. Разовое событие
    даёт одну пару, «каждый день» на неделе — семь.
    """
    query = select(Event).where(
        Event.user_id == user_id,
        Event.status == EventStatus.ACTIVE,
        Event.starts_at < end,
        or_(Event.starts_at >= start, Event.repeat.is_not(None)),
    )
    items = []
    for event in await session.scalars(query):
        if event.repeat is None:
            items.append((event.starts_at, event))
            continue
        anchor = event.repeat_anchor or event.starts_at
        for when in occurrences(anchor, event.repeat, tz, start, end):
            items.append((when.astimezone(UTC), event))
    items.sort(key=lambda item: (item[0], item[1].id))
    return items


async def update_event(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    *,
    title: str | None = None,
    starts_at: datetime | None = None,
    ends_at=_KEEP,
    remind_before_min: int | None = None,
) -> Event | None:
    """Меняет поля события. ends_at=None убирает конец промежутка."""
    event = await get_event(session, user_id, event_id)
    if event is None:
        return None
    if title is not None:
        event.title = title
    if ends_at is not _KEEP:
        event.ends_at = ends_at
    if starts_at is not None:
        event.starts_at = starts_at
        event.repeat_anchor = starts_at
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
    if repeat is not None and not is_repeat(repeat):
        raise ValueError(repeat)
    event = await get_event(session, user_id, event_id)
    if event is None:
        return None
    event.repeat = repeat
    if event.repeat_anchor is None:
        event.repeat_anchor = event.starts_at
    await session.commit()
    return event


async def set_category(
    session: AsyncSession, user_id: int, event_id: int, category_id: int | None
) -> Event | None:
    if category_id is not None and await get_category(session, user_id, category_id) is None:
        raise ValueError(category_id)
    event = await get_event(session, user_id, event_id)
    if event is None:
        return None
    event.category_id = category_id
    await session.commit()
    return event


def _shift(event: Event, starts_at: datetime) -> None:
    """Переносит событие на новое время вместе с концом промежутка и напоминанием."""
    if event.ends_at is not None:
        event.ends_at = starts_at + (event.ends_at - event.starts_at)
    event.starts_at = starts_at
    event.remind_at = starts_at - timedelta(minutes=event.remind_before_min)


async def list_due(session: AsyncSession, user_id: int, until: datetime) -> list[Event]:
    """Активные события, которые начинаются раньше `until`: что можно отметить завершённым."""
    query = select(Event).where(
        Event.user_id == user_id, Event.status == EventStatus.ACTIVE, Event.starts_at < until
    )
    return list(await session.scalars(query.order_by(Event.starts_at)))


async def complete(session: AsyncSession, user_id: int, event_id: int, tz: str) -> Event | None:
    """Завершает событие. Разовое закрывается, повторяющееся переходит к следующему разу."""
    event = await get_event(session, user_id, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        return None
    if event.repeat is None:
        event.status = EventStatus.DONE
        event.remind_at = None
    else:
        anchor = event.repeat_anchor or event.starts_at
        day = anchor.astimezone(ZoneInfo(tz)).day
        _shift(event, next_occurrence(event.starts_at, event.repeat, tz, event.starts_at, day=day))
    await session.commit()
    return event


# ---------- Категории ----------


async def list_categories(session: AsyncSession, user_id: int) -> list[Category]:
    """Категории пользователя по алфавиту. При первом обращении добавляет готовые."""
    user = await session.get(User, user_id)
    if user is not None and not user.categories_seeded:
        existing = set(
            await session.scalars(select(Category.name).where(Category.user_id == user_id))
        )
        for name in DEFAULT_CATEGORIES.values():
            if name not in existing:
                session.add(Category(user_id=user_id, name=name))
        user.categories_seeded = True
        await session.commit()
    query = select(Category).where(Category.user_id == user_id).order_by(Category.id)
    return list(await session.scalars(query))


async def category_names(session: AsyncSession, user_id: int) -> dict[int, str]:
    return {c.id: c.name for c in await list_categories(session, user_id)}


async def get_category(session: AsyncSession, user_id: int, category_id: int) -> Category | None:
    category = await session.get(Category, category_id)
    if category is None or category.user_id != user_id:
        return None
    return category


async def add_category(session: AsyncSession, user_id: int, name: str) -> Category:
    """Создаёт категорию; если такая уже есть (без учёта регистра), возвращает её."""
    for category in await list_categories(session, user_id):
        if category.name.lower() == name.lower():
            return category
    category = Category(user_id=user_id, name=name)
    session.add(category)
    await session.commit()
    return category


async def delete_category(session: AsyncSession, user_id: int, category_id: int) -> bool:
    """Удаляет категорию; её события остаются, но уже без категории."""
    category = await get_category(session, user_id, category_id)
    if category is None:
        return False
    events = await session.scalars(select(Event).where(Event.category_id == category_id))
    for event in events:
        event.category_id = None
    await session.delete(category)
    await session.commit()
    return True


async def roll_recurring(session: AsyncSession, now: datetime) -> list[Event]:
    """Переносит прошедшие повторяющиеся события на следующий раз.

    Событие переносится, когда оно закончилось (или началось, если конца нет) больше
    минуты назад; напоминание ставится заново по настройке события. Возвращает
    перенесённые события.
    """
    query = (
        select(Event, User.timezone)
        .join(User, User.id == Event.user_id)
        .where(
            Event.status == EventStatus.ACTIVE,
            Event.repeat.is_not(None),
            func.coalesce(Event.ends_at, Event.starts_at) < now - timedelta(minutes=1),
            # Отложенное («💤») напоминание сначала должно прийти
            or_(Event.remind_at.is_(None), Event.remind_at <= now),
        )
    )
    rolled = []
    for event, tz in (await session.execute(query)).all():
        anchor = event.repeat_anchor or event.starts_at
        day = anchor.astimezone(ZoneInfo(tz)).day
        _shift(event, next_occurrence(event.starts_at, event.repeat, tz, now, day=day))
        rolled.append(event)
    if rolled:
        await session.commit()
    return rolled
