from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot import repo
from bot.config import Config
from bot.db import init_db, make_engine, make_sessionmaker
from bot.handlers import events
from bot.keyboards import AddCatCb, AddRepeatCb

USER = 42
REMINDERS = SimpleNamespace(schedule=lambda *a: None)
CONFIG = Config(bot_token="x", default_tz="Europe/Moscow", database_url="")


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


def msg(text):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=USER), answer=AsyncMock())


def callback():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=USER),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def future_text():
    d = datetime.now(UTC) + timedelta(days=10)
    return f"{d:%d.%m.%Y} 18:30"


async def test_quick_add_asks_repeat_then_category(session, state):
    m = msg(f"{future_text()} Встреча с Аней")
    await events.quick_add(m, state, session, CONFIG, REMINDERS)
    assert "Встреча с Аней" in m.answer.call_args.args[0]
    assert "Повторять" in m.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.repeat

    cb = callback()
    await events.add_repeat(cb, AddRepeatCb(value="none"), state, session, CONFIG, REMINDERS)
    assert "категории" in cb.message.edit_text.call_args.args[0]
    assert await state.get_state() == events.AddEvent.category

    cb = callback()
    await events.add_category(cb, AddCatCb(value=0), state, session, CONFIG, REMINDERS)
    assert "Сохранено" in cb.message.edit_text.call_args.args[0]
    assert await state.get_state() is None

    saved = await repo.list_events(session, USER)
    assert [(e.title, e.repeat, e.category_id) for e in saved] == [("Встреча с Аней", None, None)]
    assert saved[0].remind_before_min == 15


async def test_cancel_in_the_middle_saves_nothing(session, state):
    await events.quick_add(msg(f"{future_text()} Кино"), state, session, CONFIG, REMINDERS)
    await events.cmd_cancel(msg("/cancel"), state)
    assert await repo.list_events(session, USER) == []
    assert await state.get_state() is None


async def test_quick_add_bad_text_shows_examples(session, state):
    m = msg("просто текст")
    await events.quick_add(m, state, session, CONFIG)
    assert "Примеры" in m.answer.call_args.args[0]
    assert await state.get_state() is None


async def test_step_by_step_add(session, state):
    await events.cmd_add(msg("/add"), state)
    assert await state.get_state() == events.AddEvent.title
    assert "Что за событие" in (await _last_answer(events.cmd_add, state))

    await events.add_title(msg("стоматолог"), state)
    assert await state.get_state() == events.AddEvent.when

    bad = msg("когда-нибудь")
    await events.add_when(bad, state, session, CONFIG, REMINDERS)
    assert await state.get_state() == events.AddEvent.when

    when = msg(f"{future_text()[:-6]} 14:20–19:30")
    await events.add_when(when, state, session, CONFIG, REMINDERS)
    assert "14:20–19:30" in when.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.repeat

    # свой период
    await events.add_repeat(
        callback(), AddRepeatCb(value="custom"), state, session, CONFIG, REMINDERS
    )
    assert await state.get_state() == events.AddEvent.repeat_custom
    bad = msg("иногда")
    await events.add_repeat_text(bad, state, session, CONFIG, REMINDERS)
    assert "Не понял период" in bad.answer.call_args.args[0]
    period = msg("раз в 3 дня")
    await events.add_repeat_text(period, state, session, CONFIG, REMINDERS)
    assert "Раз в 3 дня" in period.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.category

    # новая категория
    await events.add_category(callback(), AddCatCb(value=-1), state, session, CONFIG, REMINDERS)
    assert await state.get_state() == events.AddEvent.category_new
    done = msg("🦷 зубы")
    await events.add_category_text(done, state, session, CONFIG, REMINDERS)
    assert "🏷 🦷 Зубы" in done.answer.call_args.args[0]
    assert await state.get_state() is None

    saved = (await repo.list_events(session, USER))[0]
    names = await repo.category_names(session, USER)
    assert (saved.title, saved.repeat, names[saved.category_id]) == ("Стоматолог", "3d", "🦷 Зубы")
    assert saved.ends_at - saved.starts_at == timedelta(hours=5, minutes=10)


async def _last_answer(handler, state):
    m = msg("/add")
    await handler(m, state)
    return m.answer.call_args.args[0]


async def test_category_typed_by_name(session, state):
    await events.quick_add(
        msg(f"каждый день {future_text()} зарядка"), state, session, CONFIG, REMINDERS
    )
    assert await state.get_state() == events.AddEvent.category
    m = msg("здоровье")
    await events.add_category_text(m, state, session, CONFIG, REMINDERS)
    assert "🏷 🩺 Здоровье" in m.answer.call_args.args[0]


async def test_hashtag_and_repeat_save_at_once(session, state):
    m = msg(f"раз в 2 недели {future_text()} уборка #дом")
    await events.quick_add(m, state, session, CONFIG, REMINDERS)
    assert "Сохранено" in m.answer.call_args.args[0]
    assert "🏷 🏠 Дом" in m.answer.call_args.args[0]
    saved = (await repo.list_events(session, USER))[0]
    assert (saved.title, saved.repeat) == ("Уборка", "2w")


async def test_new_event_text_on_repeat_step(session, state):
    await events.quick_add(msg(f"{future_text()} Кино"), state, session, CONFIG, REMINDERS)
    m = msg(f"{future_text()} Театр")
    await events.add_repeat_text(m, state, session, CONFIG, REMINDERS)
    data = await state.get_data()
    assert data["title"] == "Театр"
    assert await state.get_state() == events.AddEvent.repeat


async def test_cancel_clears_state(state):
    await events.cmd_add(msg("/add"), state)
    m = msg("/cancel")
    await events.cmd_cancel(m, state)
    assert await state.get_state() is None
    assert m.answer.call_args.args[0].startswith("Отменено")


async def test_quick_add_uses_user_default_remind(session, state):
    await repo.get_or_create_user(session, USER, "Europe/Moscow")
    await repo.update_user(session, USER, default_remind_min=60)
    await events.quick_add(msg(f"{future_text()} Кино"), state, session, CONFIG, REMINDERS)
    await events.add_repeat(
        callback(), AddRepeatCb(value="none"), state, session, CONFIG, REMINDERS
    )
    cb = callback()
    await events.add_category(cb, AddCatCb(value=0), state, session, CONFIG, REMINDERS)
    assert "за 1 ч" in cb.message.edit_text.call_args.args[0]
    assert (await repo.list_events(session, USER))[0].remind_before_min == 60
