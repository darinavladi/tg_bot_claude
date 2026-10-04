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
from bot.keyboards import ConfirmCb, RemindCb

USER = 42
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


async def test_quick_add_and_save(session, state):
    m = msg(f"{future_text()} Встреча с Аней")
    await events.quick_add(m, state, session, CONFIG)
    assert "Встреча с Аней" in m.answer.call_args.args[0]
    assert await state.get_state() == events.AddEvent.confirm

    cb = callback()
    await events.confirm_save(cb, state, session, CONFIG)
    assert "Сохранено" in cb.message.edit_text.call_args.args[0]
    assert await state.get_state() is None

    saved = await repo.list_events(session, USER)
    assert [e.title for e in saved] == ["Встреча с Аней"]
    assert saved[0].remind_before_min == events.DEFAULT_REMIND_MIN


async def test_quick_add_cancel_saves_nothing(session, state):
    await events.quick_add(msg(f"{future_text()} Кино"), state, session, CONFIG)
    await events.confirm_cancel(callback(), state)
    assert await repo.list_events(session, USER) == []
    assert await state.get_state() is None
    assert ConfirmCb(action="cancel")  # фабрика кнопок собирается без ошибок


async def test_quick_add_bad_text_shows_examples(session, state):
    m = msg("просто текст")
    await events.quick_add(m, state, session, CONFIG)
    assert "Примеры" in m.answer.call_args.args[0]
    assert await state.get_state() is None


async def test_step_by_step_add(session, state):
    await events.cmd_add(msg("/add"), state)
    assert await state.get_state() == events.AddEvent.title

    await events.add_title(msg("Стоматолог"), state)
    assert await state.get_state() == events.AddEvent.when

    bad = msg("когда-нибудь")
    await events.add_when(bad, state, session, CONFIG)
    assert await state.get_state() == events.AddEvent.when

    await events.add_when(msg(future_text()), state, session, CONFIG)
    assert await state.get_state() == events.AddEvent.remind

    await events.add_remind(callback(), RemindCb(minutes=60), state, session, CONFIG)
    saved = await repo.list_events(session, USER)
    assert [(e.title, e.remind_before_min) for e in saved] == [("Стоматолог", 60)]
    assert await state.get_state() is None


async def test_cancel_clears_state(state):
    await events.cmd_add(msg("/add"), state)
    m = msg("/cancel")
    await events.cmd_cancel(m, state)
    assert await state.get_state() is None
    assert m.answer.call_args.args[0] == "Отменено."


async def test_list(session, state):
    m = msg("/list")
    await events.cmd_list(m, session, CONFIG)
    assert "нет" in m.answer.call_args.args[0]

    await events.quick_add(msg(f"{future_text()} Кино"), state, session, CONFIG)
    await events.confirm_save(callback(), state, session, CONFIG)
    m = msg("/list")
    await events.cmd_list(m, session, CONFIG)
    assert "Кино" in m.answer.call_args.args[0]
