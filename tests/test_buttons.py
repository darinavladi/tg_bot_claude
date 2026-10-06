from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Config
from bot.db import init_db, make_engine, make_sessionmaker
from bot.handlers import events, menu
from bot.keyboards import MENU, CalCb, EndCb, TimeCb, calendar_kb, hours_kb, main_menu

TZ = "Europe/Moscow"
USER = 21
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")
REMINDERS = SimpleNamespace(schedule=MagicMock(), cancel=MagicMock())


def texts(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_calendar_layout():
    today = date(2026, 10, 6)  # вторник
    rows = texts(calendar_kb(2026, 10, today))
    assert rows[0] == ["Сегодня", "Завтра"]
    assert rows[1] == [" ", "Октябрь 2026", "▶️"]  # назад, в прошлое, нельзя
    assert rows[2] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    assert rows[3] == [" ", " ", " ", "·", "·", "·", "·"]  # 1–4 октября прошли
    assert rows[4][:3] == ["·", "•6•", "7"]
    assert all(len(r) == 7 for r in rows[2:])
    november = texts(calendar_kb(2026, 11, today))
    assert november[1] == ["◀️", "Ноябрь 2026", "▶️"]


def test_hours_for_today_skip_past():
    assert texts(hours_kb())[0][0] == "06:00"
    rows = texts(hours_kb(min_hour=20))
    assert rows[0] == ["20:00", "21:00", "22:00", "23:00"]
    assert rows[-1] == ["◀️ Другой день"]


def test_main_menu_buttons():
    labels = [b.text for row in main_menu().keyboard for b in row]
    assert labels == list(MENU.values())


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    async with make_sessionmaker(engine)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))


def msg(text_):
    return SimpleNamespace(
        text=text_, from_user=SimpleNamespace(id=USER), answer=AsyncMock(), edit_text=AsyncMock()
    )


def cb():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=USER),
        message=SimpleNamespace(edit_text=AsyncMock(), edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )


async def test_add_with_buttons(session, state):
    await events.cmd_add(msg("/add"), state)
    m = msg("Работа над проектом")
    await events.add_title(m, state, session, CONFIG)
    assert "Выберите день" in m.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.when

    day = datetime.now(ZoneInfo(TZ)).date() + timedelta(days=2)
    c = cb()
    await events.add_calendar(
        c, CalCb(action="day", y=day.year, m=day.month, d=day.day), state, session, CONFIG
    )
    assert "Во сколько" in c.message.edit_text.call_args.args[0]
    assert await state.get_state() == events.AddEvent.time

    c = cb()
    await events.add_time(c, TimeCb(kind="h", v=14), state, session, CONFIG)
    minutes = texts(c.message.edit_reply_markup.call_args.kwargs["reply_markup"])
    assert minutes[0] == ["14:00", "14:15", "14:30", "14:45"]

    c = cb()
    await events.add_time(c, TimeCb(kind="m", v=15), state, session, CONFIG)
    assert "До скольких" in c.message.edit_text.call_args.args[0]
    assert await state.get_state() == events.AddEvent.end

    c = cb()
    await events.add_end(c, EndCb(minutes=90), state, session, CONFIG, REMINDERS)
    assert "14:15–15:45" in c.message.edit_text.call_args.args[0]
    assert await state.get_state() == events.AddEvent.repeat


async def test_calendar_rejects_past_and_navigates(session, state):
    await state.set_state(events.AddEvent.when)
    await state.update_data(title="Кино")
    past = datetime.now(ZoneInfo(TZ)).date() - timedelta(days=1)
    c = cb()
    await events.add_calendar(
        c, CalCb(action="day", y=past.year, m=past.month, d=past.day), state, session, CONFIG
    )
    assert "прошёл" in c.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.when
    c = cb()
    await events.add_calendar(c, CalCb(action="nav", y=2030, m=1), state, session, CONFIG)
    rows = texts(c.message.edit_reply_markup.call_args.kwargs["reply_markup"])
    assert rows[1][1] == "Январь 2030"


async def test_typed_time_and_end(session, state):
    day = datetime.now(ZoneInfo(TZ)).date() + timedelta(days=3)
    await state.set_state(events.AddEvent.time)
    await state.update_data(title="Кино", day=day.isoformat())
    m = msg("19")
    await events.add_time_text(m, state, session, CONFIG, REMINDERS)
    assert "До скольких" in m.answer.call_args.args[0]
    m = msg("до 21:30")
    await events.add_end_text(m, state, session, CONFIG, REMINDERS)
    assert "19:00–21:30" in m.answer.call_args.args[0]

    # промежуток сразу — вопрос «до скольких» пропускается
    await state.set_state(events.AddEvent.time)
    m = msg("14:20–19:30")
    await events.add_time_text(m, state, session, CONFIG, REMINDERS)
    assert "14:20–19:30" in m.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.repeat


async def test_past_time_today_rejected(session, state):
    today = datetime.now(ZoneInfo(TZ))
    if today.hour == 0:
        pytest.skip("в полночь прошедших часов сегодня нет")
    await state.set_state(events.AddEvent.time)
    await state.update_data(title="Кино", day=today.date().isoformat(), hour=0)
    c = cb()
    await events.add_time(c, TimeCb(kind="m", v=0), state, session, CONFIG)
    assert "прошло" in c.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.time


async def test_menu_button_interrupts_dialog(session, state):
    await events.cmd_add(msg("/add"), state)
    m = msg(MENU["today"])
    await menu.menu_lists(m, state, session, CONFIG)
    assert await state.get_state() is None
    assert "Сегодня" in m.answer.call_args.args[0] or "ничего" in m.answer.call_args.args[0]


async def test_menu_add(state):
    m = msg(MENU["add"])
    await menu.menu_add(m, state)
    assert await state.get_state() == events.AddEvent.title


async def test_inline_menu(session, state):
    from bot.keyboards import MenuCb, inline_menu

    labels = [b.text for row in inline_menu().inline_keyboard for b in row]
    assert labels == list(MENU.values())

    m = msg("/menu")
    await menu.cmd_menu(m, state)
    assert "Главное меню" in m.answer.call_args.args[0]

    for action, expected in [
        ("today", "ничего"), ("week", "7 дней"), ("list", "событий нет"),
        ("done", "нечего завершать"), ("categories", "Ваши категории"),
        ("settings", "Настройки"), ("help", "бот-планировщик"),
    ]:  # fmt: skip
        c = SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(""), answer=AsyncMock())
        await menu.menu_button(c, MenuCb(action=action), state, session, CONFIG)
        assert expected in c.message.answer.call_args.args[0], action
        c.answer.assert_awaited()

    c = SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(""), answer=AsyncMock())
    await menu.menu_button(c, MenuCb(action="add"), state, session, CONFIG)
    assert await state.get_state() == events.AddEvent.title
