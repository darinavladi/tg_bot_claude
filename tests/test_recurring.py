from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from bot import repo
from bot.config import Config
from bot.db import Event, EventStatus, init_db, make_engine, make_sessionmaker
from bot.formatting import format_repeat
from bot.handlers import events, manage
from bot.handlers import reminders as reminder_handlers
from bot.keyboards import EventCb, ReminderCb, RepeatCb
from bot.parser import parse_event, parse_when
from bot.reminders import Reminders
from bot.timeutils import next_occurrence

TZ = "Europe/Moscow"
MSK = ZoneInfo(TZ)
USER = 3
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")
# Воскресенье, 4 октября 2026, 09:00 по Москве
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=MSK)


def msk(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=MSK)


# ---------- Следующее повторение ----------


@pytest.mark.parametrize(
    ("start", "repeat", "after", "expected"),
    [
        (msk(2026, 10, 4, 8), "daily", NOW, msk(2026, 10, 5, 8)),
        (msk(2026, 10, 1, 8), "daily", NOW, msk(2026, 10, 5, 8)),  # пропущенные дни
        (msk(2026, 10, 9, 8), "weekdays", msk(2026, 10, 9, 9), msk(2026, 10, 12, 8)),  # пт → пн
        (msk(2026, 10, 4, 8), "weekly", NOW, msk(2026, 10, 11, 8)),
        (msk(2026, 1, 31, 10), "monthly", msk(2026, 1, 31, 11), msk(2026, 2, 28, 10)),
        (msk(2026, 12, 15, 10), "monthly", msk(2026, 12, 16, 0), msk(2027, 1, 15, 10)),
    ],
)
def test_next_occurrence(start, repeat, after, expected):
    assert next_occurrence(start, repeat, TZ, after) == expected


def test_format_repeat():
    monday = msk(2026, 10, 5, 10)
    assert format_repeat("weekly", monday, TZ) == "каждый понедельник"
    assert format_repeat("weekly", msk(2026, 10, 7, 10), TZ) == "каждую среду"
    assert format_repeat("monthly", monday, TZ) == "каждый месяц 5-го"
    assert format_repeat("weekdays", monday, TZ) == "по будням"


# ---------- Разбор ----------


@pytest.mark.parametrize(
    ("text_", "title", "starts_at", "repeat"),
    [
        ("каждый день в 9:30 зарядка", "Зарядка", msk(2026, 10, 4, 9, 30), "daily"),
        ("каждый день в 8 зарядка", "Зарядка", msk(2026, 10, 5, 8), "daily"),
        ("ежедневно в 22:00 витамины", "Витамины", msk(2026, 10, 4, 22), "daily"),
        ("по будням в 8:30 работа", "Работа", msk(2026, 10, 5, 8, 30), "weekdays"),
        ("каждый понедельник в 10 планёрка", "Планёрка", msk(2026, 10, 5, 10), "weekly"),
        ("каждое воскресенье в 8 пробежка", "Пробежка", msk(2026, 10, 11, 8), "weekly"),
        ("каждую пятницу в 7 вечера кино", "Кино", msk(2026, 10, 9, 19), "weekly"),
        ("ежемесячно 15.10 12:00 оплата", "Оплата", msk(2026, 10, 15, 12), "monthly"),
        ("завтра в 9 врач", "Врач", msk(2026, 10, 5, 9), None),
    ],
)
def test_parse_repeat(text_, title, starts_at, repeat):
    parsed = parse_event(text_, TZ, NOW)
    assert (parsed.title, parsed.starts_at, parsed.repeat) == (title, starts_at, repeat)


def test_parse_when_repeat():
    assert parse_when("каждую среду в 19", TZ, NOW) == (msk(2026, 10, 7, 19), "weekly")


# ---------- База и перенос ----------


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        await repo.get_or_create_user(s, USER, TZ)
    yield maker
    await engine.dispose()


async def add(sm, starts_at, repeat="daily", remind=15):
    async with sm() as s:
        return await repo.add_event(s, USER, "Зарядка", starts_at, remind, repeat)


async def get(sm, event_id):
    async with sm() as s:
        return await s.get(Event, event_id)


async def test_roll_recurring(sm):
    now = datetime.now(UTC)
    passed = await add(sm, now - timedelta(hours=2))
    one_off = await add(sm, now - timedelta(hours=2), repeat=None)
    upcoming = await add(sm, now + timedelta(hours=2))
    async with sm() as s:
        rolled = await repo.roll_recurring(s, now)
    assert [e.id for e in rolled] == [passed.id]

    moved = await get(sm, passed.id)
    assert moved.starts_at == passed.starts_at + timedelta(days=1)
    assert moved.remind_at == moved.starts_at - timedelta(minutes=15)
    assert (await get(sm, one_off.id)).starts_at == one_off.starts_at
    assert (await get(sm, upcoming.id)).starts_at == upcoming.starts_at


async def test_roll_waits_for_snoozed_reminder(sm):
    now = datetime.now(UTC)
    event = await add(sm, now - timedelta(minutes=5))
    async with sm() as s:
        await repo.set_remind_at(s, event.id, now + timedelta(minutes=10))
        assert await repo.roll_recurring(s, now) == []
        # после отправки отложенного напоминания событие переносится
        await repo.set_remind_at(s, event.id, None)
        assert len(await repo.roll_recurring(s, now)) == 1


async def test_send_waits_if_remind_moved(sm):
    event = await add(sm, datetime.now(UTC) + timedelta(days=1))
    bot = SimpleNamespace(send_message=AsyncMock())
    r = Reminders(bot, sm, TZ)
    r.scheduler.start(paused=True)
    try:
        await r.send(event.id)  # срок ещё не наступил
        bot.send_message.assert_not_awaited()
        assert r.scheduler.get_job(f"event:{event.id}") is not None
    finally:
        r.shutdown()


async def test_start_rolls_and_schedules(sm):
    event = await add(sm, datetime.now(UTC) - timedelta(days=3))
    r = Reminders(SimpleNamespace(send_message=AsyncMock()), sm, TZ)
    await r.start()
    try:
        r.scheduler.pause()
        stored = await get(sm, event.id)
        assert stored.starts_at > datetime.now(UTC)
        job = r.scheduler.get_job(f"event:{event.id}")
        assert job.next_run_time == stored.remind_at
        assert r.scheduler.get_job("roll_recurring") is not None
    finally:
        r.shutdown()


# ---------- Обработчики ----------


def cb():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=USER),
        message=SimpleNamespace(text="⏰", answer=AsyncMock(), edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


async def test_done_keeps_repeating_event(sm):
    event = await add(sm, datetime.now(UTC) + timedelta(hours=1))
    rem = SimpleNamespace(cancel=MagicMock())
    async with sm() as s:
        await reminder_handlers.reminder_done(
            cb(), ReminderCb(action="done", event_id=event.id), s, rem
        )
    assert (await get(sm, event.id)).status == EventStatus.ACTIVE
    rem.cancel.assert_not_called()


async def test_repeat_buttons(sm):
    event = await add(sm, datetime.now(UTC) + timedelta(hours=1), repeat=None)
    c = cb()
    async with sm() as s:
        await manage.event_repeat(c, EventCb(action="repeat", event_id=event.id), s, CONFIG)
        await manage.event_repeat_set(cb(), RepeatCb(event_id=event.id, value="weekly"), s, CONFIG)
    assert (await get(sm, event.id)).repeat == "weekly"
    async with sm() as s:
        await manage.event_repeat_set(cb(), RepeatCb(event_id=event.id, value="none"), s, CONFIG)
    assert (await get(sm, event.id)).repeat is None


async def test_quick_add_saves_repeat(sm):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))
    m = SimpleNamespace(
        text="каждый понедельник в 10 планёрка", from_user=SimpleNamespace(id=USER),
        answer=AsyncMock(),
    )  # fmt: skip
    async with sm() as s:
        await events.quick_add(m, state, s, CONFIG)
        assert "Каждый понедельник" in m.answer.call_args.args[0]
        await events.confirm_save(cb(), state, s, CONFIG, SimpleNamespace(schedule=MagicMock()))
        saved = await repo.list_events(s, USER)
    assert [(e.title, e.repeat) for e in saved] == [("Планёрка", "weekly")]


async def test_migration_adds_repeat(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, user_id BIGINT, "
                "title VARCHAR(500), starts_at DATETIME, remind_before_min INTEGER, "
                "status VARCHAR(9), remind_at DATETIME, created_at DATETIME)"
            )
        )
    await init_db(engine)
    async with engine.connect() as conn:
        cols = [r[1] for r in await conn.execute(text("PRAGMA table_info(events)"))]
    assert "repeat" in cols
    await engine.dispose()
