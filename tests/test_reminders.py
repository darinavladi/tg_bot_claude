from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from bot import repo
from bot.db import Event, EventStatus, init_db, make_engine, make_sessionmaker
from bot.handlers import reminders as reminder_handlers
from bot.keyboards import ReminderCb
from bot.reminders import Reminders, reminder_text

USER = 7
TZ = "Europe/Moscow"
CONFIG = SimpleNamespace(default_tz=TZ)


@pytest.fixture
async def db():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        await repo.get_or_create_user(s, USER, TZ)
    yield engine, sm
    await engine.dispose()


@pytest.fixture
async def rem(db):
    _, sm = db
    bot = SimpleNamespace(send_message=AsyncMock())
    r = Reminders(bot, sm, TZ)
    r.scheduler.start(paused=True)
    yield r
    r.shutdown()


async def add(sm, minutes_from_now, remind=15, title="Встреча"):
    async with sm() as s:
        starts_at = datetime.now(UTC) + timedelta(minutes=minutes_from_now)
        return await repo.add_event(s, USER, title, starts_at, remind)


async def get(sm, event_id):
    async with sm() as s:
        return await s.get(Event, event_id)


def test_reminder_text():
    now = datetime(2026, 10, 15, 15, 15, tzinfo=UTC)
    event = SimpleNamespace(title="Кино", starts_at=datetime(2026, 10, 15, 15, 30, tzinfo=UTC))
    assert reminder_text(event, TZ, now).startswith("⏰ Через 15 минут: Кино")
    assert "18:30" in reminder_text(event, TZ, now)
    assert reminder_text(event, TZ, now + timedelta(minutes=15)).startswith("⏰ Сейчас")
    assert reminder_text(event, TZ, now + timedelta(hours=2)).startswith("⏰ Пропущенное")
    assert "1 час" in reminder_text(event, TZ, now - timedelta(minutes=45))
    assert "2 дня" in reminder_text(event, TZ, now - timedelta(days=2))


async def test_add_event_sets_remind_at(db):
    _, sm = db
    event = await add(sm, 120, remind=15)
    assert event.remind_at == event.starts_at - timedelta(minutes=15)


async def test_schedule_and_cancel(db, rem):
    _, sm = db
    event = await add(sm, 120)
    rem.schedule(event.id, event.remind_at)
    job = rem.scheduler.get_job(f"event:{event.id}")
    assert job.next_run_time == event.remind_at

    rem.cancel(event.id)
    assert rem.scheduler.get_job(f"event:{event.id}") is None


async def test_past_remind_time_runs_asap(db, rem):
    _, sm = db
    event = await add(sm, 5, remind=15)  # напоминание «за 15 мин» уже должно было быть
    rem.schedule(event.id, event.remind_at)
    job = rem.scheduler.get_job(f"event:{event.id}")
    assert job.next_run_time <= datetime.now(UTC) + timedelta(seconds=2)


async def test_send_delivers_once(db, rem):
    _, sm = db
    event = await add(sm, 15, remind=15)
    await rem.send(event.id)
    rem.bot.send_message.assert_awaited_once()
    chat_id, text_ = rem.bot.send_message.call_args.args
    assert chat_id == USER
    assert "Встреча" in text_
    assert (await get(sm, event.id)).remind_at is None

    await rem.send(event.id)  # повторный запуск ничего не шлёт
    rem.bot.send_message.assert_awaited_once()


async def test_send_skips_done_event(db, rem):
    _, sm = db
    event = await add(sm, 15)
    async with sm() as s:
        await repo.mark_done(s, USER, event.id)
    await rem.send(event.id)
    rem.bot.send_message.assert_not_awaited()


async def test_start_restores_pending(db):
    _, sm = db
    future = await add(sm, 120)
    done = await add(sm, 120)
    async with sm() as s:
        await repo.mark_done(s, USER, done.id)

    r = Reminders(SimpleNamespace(send_message=AsyncMock()), sm, TZ)
    await r.start()
    try:
        r.scheduler.pause()
        assert r.scheduler.get_job(f"event:{future.id}") is not None
        assert r.scheduler.get_job(f"event:{done.id}") is None
    finally:
        r.shutdown()


async def test_snooze(db, rem):
    _, sm = db
    event = await add(sm, 15)
    await rem.send(event.id)
    remind_at = await rem.snooze(event.id, 10)
    assert (await get(sm, event.id)).remind_at == remind_at
    job = rem.scheduler.get_job(f"event:{event.id}")
    assert (
        abs((job.next_run_time - (datetime.now(UTC) + timedelta(minutes=10))).total_seconds()) < 5
    )


async def test_migration_adds_column_to_old_db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (id BIGINT PRIMARY KEY, timezone VARCHAR(64), "
                "created_at DATETIME)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, user_id BIGINT, "
                "title VARCHAR(500), starts_at DATETIME, remind_before_min INTEGER, "
                "status VARCHAR(9), created_at DATETIME)"
            )
        )
        await conn.execute(
            text("INSERT INTO users VALUES (7, 'Europe/Moscow', '2026-01-01 00:00:00')")
        )
        await conn.execute(
            text(
                "INSERT INTO events VALUES "
                "(1, 7, 'Старое', '2099-01-01 10:00:00', 15, 'active', '2026-01-01')"
            )
        )
    await init_db(engine)
    async with make_sessionmaker(engine)() as s:
        event = await s.get(Event, 1)
        assert event.remind_at == datetime(2099, 1, 1, 9, 45, tzinfo=UTC)
        assert event.status == EventStatus.ACTIVE
    await engine.dispose()


def cb(event_id, action, minutes=0):
    return (
        SimpleNamespace(
            from_user=SimpleNamespace(id=USER),
            message=SimpleNamespace(text="⏰ Встреча", edit_text=AsyncMock()),
            answer=AsyncMock(),
        ),
        ReminderCb(action=action, event_id=event_id, minutes=minutes),
    )


async def test_done_button(db, rem):
    _, sm = db
    event = await add(sm, 120)
    rem.schedule(event.id, event.remind_at)
    callback, data = cb(event.id, "done")
    async with sm() as s:
        await reminder_handlers.reminder_done(callback, data, s, rem)
    assert "Готово" in callback.message.edit_text.call_args.args[0]
    assert (await get(sm, event.id)).status == EventStatus.DONE
    assert rem.scheduler.get_job(f"event:{event.id}") is None


async def test_snooze_button(db, rem):
    _, sm = db
    event = await add(sm, 15)
    await rem.send(event.id)
    callback, data = cb(event.id, "snooze", 60)
    async with sm() as s:
        await reminder_handlers.reminder_snooze(callback, data, s, rem, CONFIG)
    assert "Напомню снова" in callback.message.edit_text.call_args.args[0]
    assert (await get(sm, event.id)).remind_at is not None


async def test_buttons_reject_other_user(db, rem):
    _, sm = db
    event = await add(sm, 120)
    callback, data = cb(event.id, "done")
    callback.from_user.id = 999
    async with sm() as s:
        await reminder_handlers.reminder_done(callback, data, s, rem)
    callback.answer.assert_awaited_once()
    assert (await get(sm, event.id)).status == EventStatus.ACTIVE
