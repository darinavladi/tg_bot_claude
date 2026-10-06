from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from bot import repo
from bot.config import Config
from bot.db import User, init_db, make_engine, make_sessionmaker
from bot.handlers import settings
from bot.keyboards import SettingsCb
from bot.reminders import Reminders, summary_text
from bot.timeutils import is_valid_tz, parse_hhmm

USER = 9
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
    return SimpleNamespace(schedule_summary=MagicMock())


def msg(text=""):
    return SimpleNamespace(
        text=text, from_user=SimpleNamespace(id=USER), answer=AsyncMock(), edit_text=AsyncMock()
    )


def cb():
    return SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(), answer=AsyncMock())


async def user(sm) -> User:
    async with sm() as s:
        return await s.get(User, USER)


def test_parse_hhmm():
    assert parse_hhmm("8") == "08:00"
    assert parse_hhmm("7:30") == "07:30"
    assert parse_hhmm("07.05") == "07:05"
    assert parse_hhmm("24:00") is None
    assert parse_hhmm("утром") is None


def test_is_valid_tz():
    assert is_valid_tz("Europe/Berlin")
    assert not is_valid_tz("Europe/Nowhere")
    assert not is_valid_tz("Москва")


def test_summary_text():
    now = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)  # 08:00 по Москве
    events = [
        SimpleNamespace(title="Врач", starts_at=now + timedelta(hours=2)),
        SimpleNamespace(title="Кино", starts_at=now + timedelta(hours=11)),
    ]
    text_ = summary_text([(e.starts_at, e) for e in events], TZ, now)
    assert "Сегодня, пн, 5 октября" in text_
    assert "10:00 Врач\n19:00 Кино" in text_
    assert "ничего не запланировано" in summary_text([], TZ, now)


async def test_settings_shows_defaults(sm, state):
    m = msg()
    async with sm() as s:
        await settings.cmd_settings(m, state, s, CONFIG)
    text_ = m.answer.call_args.args[0]
    assert "Москва (UTC+3)" in text_
    assert "выключена" in text_
    assert "за 15 мин" in text_


async def test_set_timezone_button(sm, reminders):
    async with sm() as s:
        await settings.set_timezone(
            cb(), SettingsCb(action="tz", value="Asia/Yekaterinburg"), s, reminders, CONFIG
        )
    assert (await user(sm)).timezone == "Asia/Yekaterinburg"
    reminders.schedule_summary.assert_called_once_with(USER, None, "Asia/Yekaterinburg")


async def test_set_timezone_typed(sm, state, reminders):
    async with sm() as s:
        await settings.ask_timezone(cb(), state)
        bad = msg("Москва")
        await settings.input_timezone(bad, state, s, reminders, CONFIG)
        assert await state.get_state() == settings.SettingsInput.timezone
        await settings.input_timezone(msg("Europe/Berlin"), state, s, reminders, CONFIG)
    assert (await user(sm)).timezone == "Europe/Berlin"
    assert await state.get_state() is None


async def test_summary_on_off(sm, reminders):
    async with sm() as s:
        await settings.set_summary(
            cb(), SettingsCb(action="summary", value="08.00"), s, reminders, CONFIG
        )
    assert (await user(sm)).summary_time == "08:00"
    reminders.schedule_summary.assert_called_with(USER, "08:00", TZ)

    async with sm() as s:
        await settings.set_summary(
            cb(), SettingsCb(action="summary", value="off"), s, reminders, CONFIG
        )
    assert (await user(sm)).summary_time is None
    reminders.schedule_summary.assert_called_with(USER, None, TZ)


async def test_summary_custom_time(sm, state, reminders):
    async with sm() as s:
        await settings.ask_summary_time(cb(), state)
        await settings.input_summary_time(msg("7:30"), state, s, reminders, CONFIG)
    assert (await user(sm)).summary_time == "07:30"


async def test_default_remind(sm, reminders):
    async with sm() as s:
        await settings.set_default_remind(
            cb(), SettingsCb(action="remind", value="60"), s, reminders, CONFIG
        )
    assert (await user(sm)).default_remind_min == 60
    reminders.schedule_summary.assert_not_called()


# ---------- Планировщик сводки ----------


async def test_schedule_summary_job_and_restore(sm):
    async with sm() as s:
        await repo.update_user(s, USER, summary_time="08:00")
    r = Reminders(SimpleNamespace(send_message=AsyncMock()), sm, TZ)
    await r.start()
    try:
        r.scheduler.pause()
        job = r.scheduler.get_job(f"summary:{USER}")
        assert job is not None
        local = job.next_run_time.astimezone(job.trigger.timezone)
        assert (local.hour, local.minute) == (8, 0)

        r.schedule_summary(USER, None, TZ)
        assert r.scheduler.get_job(f"summary:{USER}") is None
    finally:
        r.shutdown()


async def test_send_summary(sm):
    async with sm() as s:
        await repo.update_user(s, USER, summary_time="08:00")
        await repo.add_event(s, USER, "Скоро", datetime.now(UTC) + timedelta(minutes=1))
    bot = SimpleNamespace(send_message=AsyncMock())
    r = Reminders(bot, sm, TZ)
    await r.send_summary(USER)
    chat_id, text_ = bot.send_message.call_args.args
    assert chat_id == USER
    assert "Доброе утро" in text_


async def test_send_summary_skipped_when_off(sm):
    bot = SimpleNamespace(send_message=AsyncMock())
    await Reminders(bot, sm, TZ).send_summary(USER)
    bot.send_message.assert_not_awaited()


async def test_migration_adds_user_columns(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE users (id BIGINT PRIMARY KEY, timezone VARCHAR(64), "
                "created_at DATETIME)"
            )
        )
        await conn.execute(
            text("INSERT INTO users VALUES (9, 'Europe/Moscow', '2026-01-01 00:00:00')")
        )
    await init_db(engine)
    async with make_sessionmaker(engine)() as s:
        u = await s.get(User, 9)
        assert u.default_remind_min == 15
        assert u.summary_time is None
    await engine.dispose()


def test_all_keyboards_pack():
    """Каждая кнопка должна упаковываться в callback_data (без «:» в значениях и ≤ 64 байт)."""
    from bot import keyboards as k

    cats = [(1, "💼 Работа"), (22, "Собака"), (333, "🐶 Очень длинное название категории")]
    for markup in [
        k.settings_kb(), k.timezone_kb(), k.summary_kb(), k.default_remind_kb(),
        k.reminder_kb(1), k.event_kb(1), k.delete_confirm_kb(1), k.edit_remind_kb(1),
        k.list_kb([("a", 1)], "all", 1, 3, True, 333, "Собака"),
        k.list_filter_kb("week", [(-1, "📂 Без категории"), *cats], -1),
        k.repeat_kb(1), k.category_kb(1, cats), k.add_repeat_kb(), k.add_category_kb(cats),
        k.done_kb([("10:00 Врач", 5)]), k.categories_kb(cats), k.category_delete_kb(333),
        k.calendar_kb(2026, 12, date(2026, 10, 6)), k.hours_kb(), k.minutes_kb(23), k.end_kb(),
        k.inline_menu(),
    ]:  # fmt: skip
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64
