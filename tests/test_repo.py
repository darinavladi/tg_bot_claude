from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import StatementError

from bot import repo
from bot.db import EventStatus, init_db, make_engine, make_sessionmaker

USER = 111
OTHER = 222


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    async with make_sessionmaker(engine)() as s:
        await repo.get_or_create_user(s, USER, "Europe/Moscow")
        await repo.get_or_create_user(s, OTHER, "Europe/Moscow")
        yield s
    await engine.dispose()


async def test_get_or_create_user_is_idempotent(session):
    user = await repo.get_or_create_user(session, USER, "Asia/Tokyo")
    assert user.timezone == "Europe/Moscow"


async def test_set_timezone(session):
    await repo.set_timezone(session, USER, "Asia/Yekaterinburg")
    user = await repo.get_or_create_user(session, USER, "Europe/Moscow")
    assert user.timezone == "Asia/Yekaterinburg"


async def test_add_and_get_event_keeps_utc(session):
    moscow = datetime(2026, 10, 15, 18, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    event = await repo.add_event(session, USER, "Встреча с Аней", moscow)
    session.expunge_all()

    loaded = await repo.get_event(session, USER, event.id)
    assert loaded.title == "Встреча с Аней"
    assert loaded.starts_at == datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
    assert loaded.starts_at.tzinfo is not None
    assert loaded.remind_before_min == 15
    assert loaded.status == EventStatus.ACTIVE


async def test_naive_datetime_rejected(session):
    with pytest.raises(StatementError, match="часовым поясом"):
        await repo.add_event(session, USER, "x", datetime(2026, 10, 15, 18, 30))
    await session.rollback()


async def test_other_user_cannot_see_event(session):
    event = await repo.add_event(session, USER, "Личное", datetime(2026, 10, 15, tzinfo=UTC))
    assert await repo.get_event(session, OTHER, event.id) is None
    assert await repo.delete_event(session, OTHER, event.id) is False
    assert await repo.list_events(session, OTHER) == []


async def test_list_events_sorted_and_filtered(session):
    base = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    late = await repo.add_event(session, USER, "Поздно", base + timedelta(hours=5))
    early = await repo.add_event(session, USER, "Рано", base)
    await repo.add_event(session, USER, "Завтра", base + timedelta(days=1))
    await repo.add_event(session, OTHER, "Чужое", base)

    day = await repo.list_events(session, USER, start=base, end=base + timedelta(days=1))
    assert [e.id for e in day] == [early.id, late.id]


async def test_done_and_deleted_hidden_from_active_list(session):
    t = datetime(2026, 10, 15, tzinfo=UTC)
    a = await repo.add_event(session, USER, "A", t)
    b = await repo.add_event(session, USER, "B", t)
    c = await repo.add_event(session, USER, "C", t)

    assert await repo.mark_done(session, USER, a.id)
    assert await repo.delete_event(session, USER, b.id)

    assert [e.id for e in await repo.list_events(session, USER)] == [c.id]
    assert len(await repo.list_events(session, USER, status=None)) == 3


async def test_update_event(session):
    t = datetime(2026, 10, 15, tzinfo=UTC)
    event = await repo.add_event(session, USER, "Старое", t)
    updated = await repo.update_event(
        session,
        USER,
        event.id,
        title="Новое",
        starts_at=t + timedelta(hours=1),
        remind_before_min=60,
    )
    assert updated.title == "Новое"
    assert updated.starts_at == t + timedelta(hours=1)
    assert updated.remind_before_min == 60
    assert await repo.update_event(session, OTHER, event.id, title="Взлом") is None
