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
from bot.formatting import format_period, format_repeat
from bot.handlers import events, manage
from bot.handlers import reminders as reminder_handlers
from bot.keyboards import AddCatCb, EventCb, ReminderCb, RepeatCb
from bot.parser import ParseError, parse_event, parse_period, parse_when
from bot.reminders import Reminders
from bot.timeutils import next_occurrence, occurrences

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


def test_next_occurrence_keeps_month_day():
    feb = msk(2026, 2, 28, 10)
    assert next_occurrence(feb, "monthly", TZ, feb, day=31) == msk(2026, 3, 31, 10)
    assert next_occurrence(feb, "monthly", TZ, feb) == msk(2026, 3, 28, 10)


def test_occurrences():
    week = (msk(2026, 10, 5, 0), msk(2026, 10, 12, 0))  # пн 5 — вс 11 октября
    daily = occurrences(msk(2026, 10, 1, 8), "daily", TZ, *week)
    assert daily == [msk(2026, 10, d, 8) for d in range(5, 12)]
    weekdays = occurrences(msk(2026, 9, 1, 8), "weekdays", TZ, *week)
    assert weekdays == [msk(2026, 10, d, 8) for d in range(5, 10)]
    assert occurrences(msk(2026, 9, 30, 8), "weekly", TZ, *week) == [msk(2026, 10, 7, 8)]
    # старая серия: начало года, без перебора с самого начала
    assert occurrences(msk(2020, 1, 1, 8), "daily", TZ, *week)[0] == msk(2026, 10, 5, 8)
    # серия ещё не началась
    assert occurrences(msk(2026, 10, 9, 8), "daily", TZ, *week) == [
        msk(2026, 10, 9, 8), msk(2026, 10, 10, 8), msk(2026, 10, 11, 8),
    ]  # fmt: skip


def test_occurrences_monthly_31():
    year = (msk(2026, 1, 1, 0), msk(2027, 1, 1, 0))
    days = [d.day for d in occurrences(msk(2026, 1, 31, 10), "monthly", TZ, *year)]
    assert days == [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    later = occurrences(msk(2020, 1, 31, 10), "monthly", TZ, msk(2026, 2, 1, 0), msk(2026, 4, 1, 0))
    assert later == [msk(2026, 2, 28, 10), msk(2026, 3, 31, 10)]


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
        ("каждый день в 9:30 зарядка", "Зарядка", msk(2026, 10, 4, 9, 30), "1d"),
        ("каждый день в 8 зарядка", "Зарядка", msk(2026, 10, 5, 8), "1d"),
        ("ежедневно в 22:00 витамины", "Витамины", msk(2026, 10, 4, 22), "1d"),
        ("по будням в 8:30 работа", "Работа", msk(2026, 10, 5, 8, 30), "wd"),
        ("каждый понедельник в 10 планёрка", "Планёрка", msk(2026, 10, 5, 10), "1w"),
        ("каждое воскресенье в 8 пробежка", "Пробежка", msk(2026, 10, 11, 8), "1w"),
        ("каждую пятницу в 7 вечера кино", "Кино", msk(2026, 10, 9, 19), "1w"),
        ("ежемесячно 15.10 12:00 оплата", "Оплата", msk(2026, 10, 15, 12), "1m"),
        ("завтра в 9 врач", "Врач", msk(2026, 10, 5, 9), None),
        ("каждые 3 дня в 9 полив цветов", "Полив цветов", msk(2026, 10, 5, 9), "3d"),
        ("раз в 2 недели в субботу в 12 уборка", "Уборка", msk(2026, 10, 10, 12), "2w"),
        ("раз в неделю в среду в 19 бассейн", "Бассейн", msk(2026, 10, 7, 19), "1w"),
        ("каждые две недели в пятницу в 18 театр", "Театр", msk(2026, 10, 9, 18), "2w"),
        ("раз в 3 месяца 20.10 в 10 стрижка", "Стрижка", msk(2026, 10, 20, 10), "3m"),
        ("каждый месяц 15.10 12:00 оплата", "Оплата", msk(2026, 10, 15, 12), "1m"),
    ],
)
def test_parse_repeat(text_, title, starts_at, repeat):
    parsed = parse_event(text_, TZ, NOW)
    assert (parsed.title, parsed.starts_at, parsed.repeat) == (title, starts_at, repeat)


def test_parse_when_repeat():
    parsed = parse_when("каждую среду в 19", TZ, NOW)
    assert (parsed.starts_at, parsed.repeat) == (msk(2026, 10, 7, 19), "1w")


@pytest.mark.parametrize(
    ("text_", "code"),
    [
        ("3 дня", "3d"), ("каждые 3 дня", "3d"), ("раз в 3 дня", "3d"), ("2 недели", "2w"),
        ("раз в 2 недели", "2w"), ("неделя", None), ("неделю", "1w"), ("месяц", "1m"),
        ("раз в месяц", "1m"), ("каждый день", "1d"), ("день", "1d"), ("по будням", "wd"),
        ("5 дней", "5d"), ("три месяца", "3m"), ("ерунда", None), ("400 дней", None),
    ],
)  # fmt: skip
def test_parse_period(text_, code):
    if code is None:
        with pytest.raises(ParseError):
            parse_period(text_)
    else:
        assert parse_period(text_) == code


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("1d", "каждый день"), ("3d", "раз в 3 дня"), ("5d", "раз в 5 дней"),
        ("21d", "раз в 21 день"), ("1w", "каждую неделю"), ("2w", "раз в 2 недели"),
        ("1m", "каждый месяц"), ("6m", "раз в 6 месяцев"), ("wd", "по будням"),
    ],
)  # fmt: skip
def test_format_period(code, expected):
    assert format_period(code) == expected


def test_format_repeat_custom():
    wednesday = msk(2026, 10, 7, 10)
    assert format_repeat("2w", wednesday, TZ) == "раз в 2 недели, по средам"
    assert format_repeat("3m", wednesday, TZ) == "раз в 3 месяца 7-го"
    assert format_repeat("3d", wednesday, TZ) == "раз в 3 дня"


def test_custom_occurrences():
    week2 = (msk(2026, 10, 5, 0), msk(2026, 10, 19, 0))
    every3 = occurrences(msk(2026, 9, 1, 8), "3d", TZ, *week2)
    # 1 сентября + 3·k: …, 1, 4, 7, 10 октября… (шаг не сбивается при перескоке)
    assert [d.day for d in every3] == [7, 10, 13, 16]
    biweekly = occurrences(msk(2026, 1, 3, 12), "2w", TZ, *week2)
    assert biweekly == [msk(2026, 10, 10, 12)]
    quarterly = occurrences(msk(2025, 1, 31, 9), "3m", TZ, msk(2026, 1, 1, 0), msk(2027, 1, 1, 0))
    assert [(d.month, d.day) for d in quarterly] == [(1, 31), (4, 30), (7, 31), (10, 31)]
    assert next_occurrence(msk(2026, 10, 5, 8), "3d", TZ, msk(2026, 10, 5, 9)) == msk(
        2026, 10, 8, 8
    )


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


async def add(sm, starts_at, repeat="1d", remind=15):
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


async def test_roll_monthly_returns_to_31st(sm):
    event = await add(sm, msk(2026, 1, 31, 10), repeat="monthly")
    async with sm() as s:
        await repo.roll_recurring(s, msk(2026, 2, 1, 0))
        assert (await get(sm, event.id)).starts_at == msk(2026, 2, 28, 10)
        await repo.roll_recurring(s, msk(2026, 3, 1, 0))
    assert (await get(sm, event.id)).starts_at == msk(2026, 3, 31, 10)


async def test_list_occurrences(sm):
    now = msk(2026, 10, 5, 12)
    start, end = msk(2026, 10, 5, 0), msk(2026, 10, 12, 0)
    daily = await add(sm, msk(2026, 10, 1, 8))
    meeting = await add(sm, msk(2026, 10, 6, 15), repeat=None)
    await add(sm, msk(2026, 10, 20, 9), repeat=None)  # за пределами недели
    async with sm() as s:
        await repo.roll_recurring(s, now)  # зарядка переехала на 6-е, но 5-е тоже видно
        items = await repo.list_occurrences(s, USER, start, end, TZ)
    assert [(w, e.id) for w, e in items][:3] == [
        (msk(2026, 10, 5, 8), daily.id),
        (msk(2026, 10, 6, 8), daily.id),
        (msk(2026, 10, 6, 15), meeting.id),
    ]
    assert len(items) == 8


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


REMINDERS = SimpleNamespace(schedule=MagicMock(), cancel=MagicMock())


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))


def msg(text_):
    return SimpleNamespace(text=text_, from_user=SimpleNamespace(id=USER), answer=AsyncMock())


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
        await manage.event_repeat_set(
            cb(), RepeatCb(event_id=event.id, value="2w"), state(), s, CONFIG, REMINDERS
        )
    assert (await get(sm, event.id)).repeat == "2w"
    async with sm() as s:
        await manage.event_repeat_set(
            cb(), RepeatCb(event_id=event.id, value="none"), state(), s, CONFIG, REMINDERS
        )
    assert (await get(sm, event.id)).repeat is None


async def test_repeat_custom_from_card(sm):
    event = await add(sm, datetime.now(UTC) + timedelta(hours=1), repeat=None)
    st = state()
    async with sm() as s:
        await manage.event_repeat_set(
            cb(), RepeatCb(event_id=event.id, value="custom"), st, s, CONFIG, REMINDERS
        )
        assert await st.get_state() == manage.EditEvent.repeat
        bad = msg("иногда")
        await manage.event_repeat_text(bad, st, s, CONFIG, REMINDERS)
        assert "Не понял период" in bad.answer.call_args.args[0]
        ok = msg("раз в 3 дня")
        await manage.event_repeat_text(ok, st, s, CONFIG, REMINDERS)
        assert "Раз в 3 дня" in ok.answer.call_args.args[0]
    assert (await get(sm, event.id)).repeat == "3d"


async def test_quick_add_saves_repeat(sm):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))
    m = SimpleNamespace(
        text="каждый понедельник в 10 планёрка", from_user=SimpleNamespace(id=USER),
        answer=AsyncMock(),
    )  # fmt: skip
    async with sm() as s:
        await events.quick_add(m, state, s, CONFIG)
        # повтор уже понятен из текста — сразу вопрос о категории
        assert "Каждый понедельник" in m.answer.call_args.args[0]
        assert await state.get_state() == events.AddEvent.category
        await events.add_category(cb(), AddCatCb(value=0), state, s, CONFIG, REMINDERS)
        saved = await repo.list_events(s, USER)
    assert [(e.title, e.repeat, e.category_id) for e in saved] == [("Планёрка", "1w", None)]


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
    assert "repeat_anchor" in cols
    assert "ends_at" in cols and "category_id" in cols
    await engine.dispose()
