from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot import repo
from bot.config import Config
from bot.db import Event, EventStatus, init_db, make_engine, make_sessionmaker
from bot.formatting import format_day
from bot.handlers import manage
from bot.keyboards import EditRemindCb, EventCb

USER = 5
TZ = "Europe/Moscow"
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        await repo.get_or_create_user(s, USER, TZ)
    yield maker
    await engine.dispose()


@pytest.fixture
def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))


@pytest.fixture
def reminders():
    return SimpleNamespace(schedule=MagicMock(), cancel=MagicMock())


async def add(sm, hours, title="Кино", remind=15):
    async with sm() as s:
        return await repo.add_event(
            s, USER, title, datetime.now(UTC) + timedelta(hours=hours), remind
        )


async def get(sm, event_id):
    async with sm() as s:
        return await s.get(Event, event_id)


def msg(text="", user=USER):
    return SimpleNamespace(
        text=text, from_user=SimpleNamespace(id=user), answer=AsyncMock(), edit_text=AsyncMock()
    )


def cb(user=USER):
    return SimpleNamespace(from_user=SimpleNamespace(id=user), message=msg(), answer=AsyncMock())


def ev(event_id, title, hours_from, now):
    return SimpleNamespace(id=event_id, title=title, starts_at=now + timedelta(hours=hours_from))


# ---------- Отрисовка ----------


def test_format_day():
    now = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    assert format_day(now, TZ, now) == "Сегодня, пн, 5 октября"
    assert format_day(now + timedelta(days=1), TZ, now) == "Завтра, вт, 6 октября"
    assert format_day(now + timedelta(days=10), TZ, now) == "Чт, 15 октября"


def test_render_list_groups_by_day():
    now = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)  # 09:00 по Москве
    events = [ev(1, "Врач", 1, now), ev(2, "Кино", 10, now), ev(3, "Обед", 27, now)]
    text, buttons, pages = manage.render_list(events, "week", 0, TZ, now)
    assert pages == 1
    assert "Сегодня, пн, 5 октября\n1. 10:00 Врач\n2. 19:00 Кино" in text
    assert "Завтра, вт, 6 октября\n3. 12:00 Обед" in text
    assert buttons == [("1. 10:00 Врач", 1), ("2. 19:00 Кино", 2), ("3. 12:00 Обед", 3)]


def test_render_list_paginates():
    now = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    events = [ev(i, f"E{i}", i, now) for i in range(1, 24)]
    text, buttons, pages = manage.render_list(events, "all", 2, TZ, now)
    assert pages == 3
    assert "стр. 3 из 3" in text
    assert [b[1] for b in buttons] == [21, 22, 23]
    assert "21. " in text


def test_range_today_uses_user_timezone():
    now = datetime(2026, 10, 4, 22, 30, tzinfo=UTC)  # в Москве уже 5 октября, 01:30
    start, end = manage._range("today", TZ, now)
    assert start == datetime(2026, 10, 4, 21, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 5, 21, 0, tzinfo=UTC)


# ---------- Команды списков ----------


async def test_today_week_list(sm):
    await add(sm, 0.5, "Скоро")
    await add(sm, 24 * 3, "Через три дня")
    await add(sm, 24 * 30, "Через месяц")

    async def texts(handler):
        m = msg()
        async with sm() as s:
            await handler(m, s, CONFIG)
        return m.answer.call_args.args[0]

    week = await texts(manage.cmd_week)
    assert "Через три дня" in week and "Через месяц" not in week
    every = await texts(manage.cmd_list)
    assert "Через месяц" in every


async def test_empty_list(sm):
    m = msg()
    async with sm() as s:
        await manage.cmd_today(m, s, CONFIG)
    assert m.answer.call_args.args[0] == manage.EMPTY["today"]


# ---------- Карточка и действия ----------


async def test_show_card(sm):
    event = await add(sm, 5, "Кино")
    c = cb()
    async with sm() as s:
        await manage.event_show(c, EventCb(action="show", event_id=event.id), s, CONFIG)
    assert "Кино" in c.message.answer.call_args.args[0]


async def test_delete_flow(sm, reminders):
    event = await add(sm, 5)
    c = cb()
    async with sm() as s:
        await manage.event_delete(c, EventCb(action="delete", event_id=event.id), s, CONFIG)
    assert "Удалить" in c.message.edit_text.call_args.args[0]

    c = cb()
    async with sm() as s:
        await manage.event_delete_yes(
            c, EventCb(action="delete_yes", event_id=event.id), s, CONFIG, reminders
        )
    assert "Удалено" in c.message.edit_text.call_args.args[0]
    reminders.cancel.assert_called_once_with(event.id)
    stored = await get(sm, event.id)
    assert stored.status == EventStatus.CANCELLED
    assert stored.remind_at is None


async def test_other_user_cannot_delete(sm, reminders):
    event = await add(sm, 5)
    c = cb(user=999)
    async with sm() as s:
        await manage.event_delete_yes(
            c, EventCb(action="delete_yes", event_id=event.id), s, CONFIG, reminders
        )
    c.answer.assert_awaited_once()
    reminders.cancel.assert_not_called()
    assert (await get(sm, event.id)).status == EventStatus.ACTIVE


async def test_change_remind(sm, reminders):
    event = await add(sm, 5, remind=15)
    c = cb()
    async with sm() as s:
        await manage.event_remind_set(
            c, EditRemindCb(event_id=event.id, minutes=60), s, CONFIG, reminders
        )
    stored = await get(sm, event.id)
    assert stored.remind_before_min == 60
    assert stored.remind_at == stored.starts_at - timedelta(minutes=60)
    reminders.schedule.assert_called_once_with(event.id, stored.remind_at)


async def test_edit_title(sm, state):
    event = await add(sm, 5, "Старое")
    async with sm() as s:
        await manage.event_edit_start(
            cb(), EventCb(action="title", event_id=event.id), state, s, CONFIG
        )
    assert await state.get_state() == manage.EditEvent.title
    m = msg("Новое")
    async with sm() as s:
        await manage.event_edit_title(m, state, s, CONFIG)
    assert (await get(sm, event.id)).title == "Новое"
    assert await state.get_state() is None


async def test_edit_time_reschedules(sm, state, reminders):
    event = await add(sm, 5)
    async with sm() as s:
        await manage.event_edit_start(
            cb(), EventCb(action="time", event_id=event.id), state, s, CONFIG
        )
    assert await state.get_state() == manage.EditEvent.time

    bad = msg("когда-нибудь")
    async with sm() as s:
        await manage.event_edit_time(bad, state, s, CONFIG, reminders)
    assert await state.get_state() == manage.EditEvent.time
    reminders.schedule.assert_not_called()

    target = datetime.now(UTC) + timedelta(days=10)
    async with sm() as s:
        await manage.event_edit_time(msg(f"{target:%d.%m.%Y} 18:30"), state, s, CONFIG, reminders)
    stored = await get(sm, event.id)
    local = stored.starts_at.astimezone(ZoneInfo(TZ))
    assert (local.day, local.month, local.hour, local.minute) == (target.day, target.month, 18, 30)
    assert stored.remind_at == stored.starts_at - timedelta(minutes=15)
    reminders.schedule.assert_called_once_with(event.id, stored.remind_at)
    assert await state.get_state() is None
