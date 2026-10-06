from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from bot import categories, repo
from bot.config import Config
from bot.db import Event, init_db, make_engine, make_sessionmaker
from bot.handlers import categories as category_handlers
from bot.handlers import manage
from bot.keyboards import CategoryCb, CatManageCb, EventCb, ListCb, reminder_kb
from bot.parser import parse_event
from bot.reminders import reminder_text, summary_text

TZ = "Europe/Moscow"
USER = 7
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo(TZ))


def test_emoji_and_display():
    assert categories.emoji_of("💼 Работа") == "💼"
    assert categories.emoji_of("Собака") is None
    assert categories.with_category("Отчёт", "💼 Работа") == "💼 Отчёт"
    assert categories.with_category("Прогулка", "Собака") == "Прогулка · Собака"
    assert categories.with_category("Посылка", None) == "Посылка"


def test_clean_name():
    assert categories.clean_name("  собака  ") == "Собака"
    assert categories.clean_name("🐶   собака") == "🐶 Собака"
    assert len(categories.clean_name("а" * 100)) == 40


def test_match_hashtag():
    names = {1: "💼 Работа", 2: "🏠 Дом", 3: "Собака", 4: "📚 Учёба"}
    assert categories.match_hashtag("работа", names) == 1
    assert categories.match_hashtag("Работе", names) == 1  # «#работе» тоже подходит
    assert categories.match_hashtag("дом", names) == 2
    assert categories.match_hashtag("собака", names) == 3
    assert categories.match_hashtag("учеба", names) == 4
    assert categories.match_hashtag("кот", names) is None
    assert categories.match_hashtag("до", names) is None  # слишком коротко


def test_parse_event_hashtag_and_range():
    parsed = parse_event("в субботу 14:20–19:30 уборка #дом", TZ, NOW)
    assert (parsed.title, parsed.hashtag) == ("Уборка", "дом")
    assert parsed.starts_at.astimezone(ZoneInfo(TZ)).strftime("%d %H:%M") == "10 14:20"
    assert parsed.ends_at.astimezone(ZoneInfo(TZ)).strftime("%d %H:%M") == "10 19:30"


def test_reminder_and_summary_show_category():
    event = SimpleNamespace(
        title="Врач", category_id=1, starts_at=NOW + timedelta(hours=1), ends_at=None
    )
    assert "🩺 Врач" in reminder_text(event, TZ, NOW, "🩺 Здоровье")
    summary = summary_text([(event.starts_at, event)], TZ, NOW, {1: "🩺 Здоровье"})
    assert "10:00 🩺 Врач" in summary


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


async def cat_id(sm, name):
    async with sm() as s:
        names = await repo.category_names(s, USER)
    return next(i for i, n in names.items() if n == name)


async def add(sm, title, category_id, hours=5):
    async with sm() as s:
        return await repo.add_event(
            s, USER, title, datetime.now(UTC) + timedelta(hours=hours), 15, None, category_id
        )


def msg(text_=""):
    return SimpleNamespace(
        text=text_, from_user=SimpleNamespace(id=USER), answer=AsyncMock(), edit_text=AsyncMock()
    )


def cb():
    return SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(), answer=AsyncMock())


def state():
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=USER, user_id=USER))


async def test_default_categories_seeded_once(sm):
    async with sm() as s:
        first = await repo.list_categories(s, USER)
        assert [c.name for c in first][:2] == ["💼 Работа", "🏠 Дом"]
        await repo.delete_category(s, USER, first[0].id)
        # удалённая готовая категория не возвращается
        assert "💼 Работа" not in [c.name for c in await repo.list_categories(s, USER)]
        same = await repo.add_category(s, USER, "🏠 дом")
        assert same.id == first[1].id  # дубликаты не создаются


async def test_delete_category_keeps_events(sm):
    work = await cat_id(sm, "💼 Работа")
    event = await add(sm, "Отчёт", work)
    async with sm() as s:
        assert await repo.delete_category(s, USER, work)
        stored = await s.get(Event, event.id)
        await s.refresh(stored)
    assert stored.category_id is None


async def test_other_user_category_rejected(sm):
    async with sm() as s:
        await repo.get_or_create_user(s, 99, TZ)
        foreign = (await repo.list_categories(s, 99))[0]
        event = await repo.add_event(s, USER, "Кино", datetime.now(UTC) + timedelta(hours=1))
        with pytest.raises(ValueError):
            await repo.set_category(s, USER, event.id, foreign.id)


async def test_set_category_from_card(sm):
    event = await add(sm, "Посылка", None)
    home = await cat_id(sm, "🏠 Дом")
    c = cb()
    async with sm() as s:
        await manage.event_category(c, EventCb(action="category", event_id=event.id), s, CONFIG)
        assert "Выберите категорию" in c.message.edit_text.call_args.args[0]
        c = cb()
        await manage.event_category_set(
            c, CategoryCb(event_id=event.id, value=home), state(), s, CONFIG
        )
        assert "🏷 🏠 Дом" in c.message.edit_text.call_args.args[0]
        assert (await s.get(Event, event.id)).category_id == home
        await manage.event_category_set(
            cb(), CategoryCb(event_id=event.id, value=0), state(), s, CONFIG
        )
        assert (await s.get(Event, event.id)).category_id is None


async def test_new_category_from_card(sm):
    event = await add(sm, "Прогулка", None)
    st = state()
    async with sm() as s:
        await manage.event_category_set(
            cb(), CategoryCb(event_id=event.id, value=-1), st, s, CONFIG
        )
        assert await st.get_state() == manage.EditEvent.category
        m = msg("собака")
        await manage.event_category_text(m, st, s, CONFIG)
        assert "🏷 Собака" in m.answer.call_args.args[0]
        names = await repo.category_names(s, USER)
        assert names[(await s.get(Event, event.id)).category_id] == "Собака"


async def test_list_filter(sm):
    await add(sm, "Отчёт", await cat_id(sm, "💼 Работа"), hours=2)
    await add(sm, "Врач", await cat_id(sm, "🩺 Здоровье"), hours=3)
    await add(sm, "Посылка", None, hours=4)

    m = msg()
    async with sm() as s:
        await manage.cmd_list(m, s, CONFIG)
    text_ = m.answer.call_args.args[0]
    assert "💼 Отчёт" in text_ and "🩺 Врач" in text_ and "Посылка" in text_
    last_row = m.answer.call_args.kwargs["reply_markup"].inline_keyboard[-1]
    assert last_row[0].text == "🏷 Категория: Все"

    c = cb()
    async with sm() as s:
        await manage.list_page(c, ListCb(kind="all", page=0, pick=1), s, CONFIG)
    options = [
        b.text
        for row in c.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert options == ["✓ Все события", "💼 Работа", "🩺 Здоровье", "📂 Без категории"]

    health = await cat_id(sm, "🩺 Здоровье")
    c = cb()
    async with sm() as s:
        await manage.list_page(c, ListCb(kind="all", page=0, cat=health), s, CONFIG)
    text_ = c.message.edit_text.call_args.args[0]
    assert "🩺 Здоровье" in text_ and "Врач" in text_ and "Отчёт" not in text_

    c = cb()
    async with sm() as s:
        await manage.list_page(c, ListCb(kind="all", page=0, cat=-1), s, CONFIG)
    text_ = c.message.edit_text.call_args.args[0]
    assert "Посылка" in text_ and "Врач" not in text_


async def test_categories_command(sm):
    st = state()
    m = msg("/categories")
    async with sm() as s:
        await category_handlers.cmd_categories(m, s, CONFIG)
        assert "💼 Работа" in m.answer.call_args.args[0]

        await category_handlers.category_add(cb(), st)
        added = msg("🐶 собака")
        await category_handlers.category_name(added, st, s, CONFIG)
        assert "«🐶 Собака» добавлена" in added.answer.call_args.args[0]

        dog = await cat_id(sm, "🐶 Собака")
        c = cb()
        await category_handlers.category_ask_delete(
            c, CatManageCb(action="ask_delete", category_id=dog), s, CONFIG
        )
        assert "Удалить категорию «🐶 Собака»" in c.message.edit_text.call_args.args[0]
        c = cb()
        await category_handlers.category_delete(
            c, CatManageCb(action="delete", category_id=dog), s, CONFIG
        )
        assert "удалена" in c.message.edit_text.call_args.args[0]
        assert "🐶 Собака" not in (await repo.category_names(s, USER)).values()


async def test_migration_from_category_keys(tmp_path):
    """База из первой версии категорий (колонка events.category) переезжает в таблицу."""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (id BIGINT PRIMARY KEY, timezone VARCHAR(64), "
                "created_at DATETIME)"
            )
        )
        await conn.execute(text("INSERT INTO users (id, timezone) VALUES (7, 'Europe/Moscow')"))
        await conn.execute(
            text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, user_id BIGINT, "
                "title VARCHAR(500), starts_at DATETIME, remind_before_min INTEGER, "
                "status VARCHAR(9), repeat VARCHAR(10), category VARCHAR(10), created_at DATETIME)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO events (user_id, title, starts_at, remind_before_min, status, "
                "repeat, category) VALUES (7, 'Отчёт', '2030-01-01 10:00:00', 15, 'active', "
                "'weekly', 'work')"
            )
        )
    await init_db(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        event = await s.get(Event, 1)
        names = await repo.category_names(s, USER)
    assert event.repeat == "1w"
    assert names[event.category_id] == "💼 Работа"
    assert list(names.values()).count("💼 Работа") == 1  # готовые не задвоились
    await engine.dispose()


def test_reminder_has_no_done_button():
    texts = [b.text for row in reminder_kb(1).inline_keyboard for b in row]
    assert not any("Готово" in t for t in texts)
