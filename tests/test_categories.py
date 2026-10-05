from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from bot import categories, repo
from bot.config import Config
from bot.db import Event, init_db, make_engine, make_sessionmaker
from bot.handlers import events, manage
from bot.keyboards import CategoryCb, EventCb, ListCb
from bot.parser import parse_event
from bot.reminders import reminder_text

TZ = "Europe/Moscow"
USER = 7
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo(TZ))


@pytest.mark.parametrize(
    ("text_", "category", "rest"),
    [
        ("уборка #дом", "home", "уборка "),
        ("#Работа отчёт", "work", " отчёт"),
        ("кино #отдых с Аней", "fun", "кино  с Аней"),
        ("кино #неизвестно", None, "кино #неизвестно"),
    ],
)
def test_extract_hashtag(text_, category, rest):
    assert categories.extract_hashtag(text_) == (category, rest)


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Стоматолог", "health"),
        ("Планёрка", "work"),
        ("Позвонить маме", "people"),
        ("Встреча с Аней", "people"),
        ("Кино", "fun"),
        ("Экзамен по истории", "study"),
        ("Купить продукты", "home"),
        ("Забрать посылку", None),
    ],
)
def test_guess(title, category):
    assert categories.guess(title) == category


def test_labels():
    assert categories.label("work") == "💼 Работа"
    assert categories.label(None) == "Без категории"
    assert categories.with_emoji("Врач", "health") == "🩺 Врач"
    assert categories.with_emoji("Посылка", None) == "Посылка"


def test_parse_event_category():
    parsed = parse_event("завтра в 9 стоматолог", TZ, NOW)
    assert (parsed.title, parsed.category) == ("Стоматолог", "health")
    parsed = parse_event("в субботу в 12 уборка #дом", TZ, NOW)
    assert (parsed.title, parsed.category) == ("Уборка", "home")
    assert parsed.starts_at.day == 10
    # хэштег важнее угадывания
    assert parse_event("завтра в 18 кино #работа", TZ, NOW).category == "work"
    assert parse_event("завтра в 18 забрать посылку", TZ, NOW).category is None


def test_reminder_text_has_emoji():
    event = SimpleNamespace(title="Врач", category="health", starts_at=NOW + timedelta(hours=1))
    assert "🩺 Врач" in reminder_text(event, TZ, NOW)


# ---------- База и обработчики ----------


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        await repo.get_or_create_user(s, USER, TZ)
    yield maker
    await engine.dispose()


async def add(sm, title, category, hours=5):
    async with sm() as s:
        return await repo.add_event(
            s, USER, title, datetime.now(UTC) + timedelta(hours=hours), 15, None, category
        )


def msg(text_=""):
    return SimpleNamespace(
        text=text_, from_user=SimpleNamespace(id=USER), answer=AsyncMock(), edit_text=AsyncMock()
    )


def cb():
    return SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(), answer=AsyncMock())


async def test_set_category_from_card(sm):
    event = await add(sm, "Посылка", None)
    c = cb()
    async with sm() as s:
        await manage.event_category(c, EventCb(action="category", event_id=event.id), s, CONFIG)
        assert "Выберите категорию" in c.message.edit_text.call_args.args[0]
        c = cb()
        await manage.event_category_set(c, CategoryCb(event_id=event.id, value="home"), s, CONFIG)
        assert "🏷 🏠 Дом" in c.message.edit_text.call_args.args[0]
        assert (await s.get(Event, event.id)).category == "home"
        await manage.event_category_set(
            cb(), CategoryCb(event_id=event.id, value="none"), s, CONFIG
        )
        await s.refresh(await s.get(Event, event.id))
        assert (await s.get(Event, event.id)).category is None


async def test_list_filter(sm):
    await add(sm, "Отчёт", "work", hours=2)
    await add(sm, "Врач", "health", hours=3)
    await add(sm, "Посылка", None, hours=4)

    m = msg()
    async with sm() as s:
        await manage.cmd_list(m, s, CONFIG)
    text_ = m.answer.call_args.args[0]
    assert "💼 Отчёт" in text_ and "🩺 Врач" in text_ and "Посылка" in text_
    filter_row = m.answer.call_args.kwargs["reply_markup"].inline_keyboard[-1]
    assert [b.text for b in filter_row] == ["✓ Все", "💼", "🩺"]

    c = cb()
    async with sm() as s:
        await manage.list_page(c, ListCb(kind="all", page=0, cat="health"), s, CONFIG)
    text_ = c.message.edit_text.call_args.args[0]
    assert "🩺 Здоровье" in text_ and "Врач" in text_ and "Отчёт" not in text_
    filter_row = c.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard[-1]
    assert [b.text for b in filter_row] == ["Все", "💼", "✓ 🩺"]


async def test_quick_add_saves_category(sm):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))
    m = msg("завтра в 9 стоматолог")
    async with sm() as s:
        await events.quick_add(m, state, s, CONFIG)
        assert "🏷 🩺 Здоровье" in m.answer.call_args.args[0]
        await events.confirm_save(cb(), state, s, CONFIG, SimpleNamespace(schedule=MagicMock()))
        saved = await repo.list_events(s, USER)
    assert [(e.title, e.category) for e in saved] == [("Стоматолог", "health")]


async def test_add_title_hashtag(sm):
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))
    await events.add_title(msg("Уборка #дом"), state)
    data = await state.get_data()
    assert (data["title"], data["category"]) == ("Уборка", "home")


async def test_migration_adds_category(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, user_id BIGINT, "
                "title VARCHAR(500), starts_at DATETIME, remind_before_min INTEGER, "
                "status VARCHAR(9), created_at DATETIME)"
            )
        )
    await init_db(engine)
    async with engine.connect() as conn:
        cols = [r[1] for r in await conn.execute(text("PRAGMA table_info(events)"))]
    assert "category" in cols
    await engine.dispose()
