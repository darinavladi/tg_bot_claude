from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bot import repo
from bot.config import Config
from bot.db import Event, EventStatus, init_db, make_engine, make_sessionmaker
from bot.formatting import format_span
from bot.handlers import done
from bot.keyboards import DoneCb
from bot.parser import ParseError, parse_event

TZ = "Europe/Moscow"
MSK = ZoneInfo(TZ)
USER = 11
CONFIG = Config(bot_token="x", default_tz=TZ, database_url="")
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=MSK)  # понедельник


# ---------- Промежуток времени ----------


@pytest.mark.parametrize(
    ("text_", "start", "end"),
    [
        ("завтра 14:20–19:30 работа", (6, 14, 20), (6, 19, 30)),
        ("завтра с 14:20 до 19:30 работа", (6, 14, 20), (6, 19, 30)),
        ("завтра с 14 до 19 работа", (6, 14, 0), (6, 19, 0)),
        ("завтра с 9.30 до 11 работа", (6, 9, 30), (6, 11, 0)),
        ("15.10 10:00-12:00 работа", (15, 10, 0), (15, 12, 0)),
        ("в пятницу 22:00–02:00 работа", (9, 22, 0), (10, 2, 0)),  # через полночь
        ("14:20–19:30 работа", (5, 14, 20), (5, 19, 30)),  # без даты — сегодня
    ],
)
def test_parse_range(text_, start, end):
    parsed = parse_event(text_, TZ, NOW)
    s, e = parsed.starts_at.astimezone(MSK), parsed.ends_at.astimezone(MSK)
    assert (s.day, s.hour, s.minute) == start
    assert (e.day, e.hour, e.minute) == end
    assert parsed.title == "Работа"


def test_parse_range_bad_time():
    with pytest.raises(ParseError):
        parse_event("завтра 14:20–25:00 работа", TZ, NOW)


def test_format_span():
    start = datetime(2026, 10, 6, 14, 20, tzinfo=MSK)
    assert format_span(start, None, TZ, NOW) == "вт, 6 октября, 14:20"
    assert format_span(start, start + timedelta(hours=5), TZ, NOW) == "вт, 6 октября, 14:20–19:20"
    night = datetime(2026, 10, 9, 22, tzinfo=MSK)
    assert format_span(night, night + timedelta(hours=4), TZ, NOW) == "пт, 9 октября, 22:00–02:00"
    long = format_span(start, start + timedelta(days=2), TZ, NOW)
    assert long == "вт, 6 октября, 14:20 – чт, 8 октября, 14:20"


# ---------- /done ----------


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        await repo.get_or_create_user(s, USER, TZ)
    yield maker
    await engine.dispose()


def msg():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=USER), answer=AsyncMock(), edit_text=AsyncMock()
    )


async def test_done_lists_today_and_missed(sm):
    now = datetime.now(UTC)
    async with sm() as s:
        missed = await repo.add_event(s, USER, "Пропущенное", now - timedelta(days=2))
        soon = await repo.add_event(s, USER, "Скоро", now + timedelta(minutes=30))
        await repo.add_event(s, USER, "Через неделю", now + timedelta(days=7))
        m = msg()
        await done.cmd_done(m, s, CONFIG)
    buttons = m.answer.call_args.kwargs["reply_markup"].inline_keyboard
    ids = [DoneCb.unpack(row[0].callback_data).event_id for row in buttons]
    assert ids[0] == missed.id and soon.id in ids and len(ids) == 2


async def test_done_closes_one_off(sm):
    async with sm() as s:
        event = await repo.add_event(s, USER, "Врач", datetime.now(UTC) + timedelta(minutes=5))
        rem = SimpleNamespace(cancel=MagicMock(), schedule=MagicMock())
        c = SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(), answer=AsyncMock())
        await done.done_event(c, DoneCb(event_id=event.id), s, CONFIG, rem)
        assert "✅ Завершено: Врач" in c.message.edit_text.call_args.args[0]
        assert (await s.get(Event, event.id)).status == EventStatus.DONE
    rem.cancel.assert_called_once_with(event.id)


async def test_done_moves_recurring_to_next_time(sm):
    start = datetime.now(UTC) + timedelta(minutes=5)
    async with sm() as s:
        event = await repo.add_event(
            s, USER, "Зарядка", start, 15, "3d", None, start + timedelta(hours=1)
        )
        rem = SimpleNamespace(cancel=MagicMock(), schedule=MagicMock())
        c = SimpleNamespace(from_user=SimpleNamespace(id=USER), message=msg(), answer=AsyncMock())
        await done.done_event(c, DoneCb(event_id=event.id), s, CONFIG, rem)
        stored = await s.get(Event, event.id)
    assert "Следующий раз" in c.message.edit_text.call_args.args[0]
    assert stored.status == EventStatus.ACTIVE
    assert stored.starts_at == start + timedelta(days=3)
    assert stored.ends_at - stored.starts_at == timedelta(hours=1)
    rem.schedule.assert_called_once_with(event.id, stored.remind_at)


async def test_done_nothing(sm):
    m = msg()
    async with sm() as s:
        await done.cmd_done(m, s, CONFIG)
    assert m.answer.call_args.args[0] == done.NOTHING


async def test_roll_waits_until_range_ends(sm):
    now = datetime.now(UTC)
    start = now - timedelta(hours=1)
    async with sm() as s:
        event = await repo.add_event(
            s, USER, "Работа", start, 15, "1d", None, now + timedelta(hours=1)
        )
        assert await repo.roll_recurring(s, now) == []  # ещё идёт
        assert len(await repo.roll_recurring(s, now + timedelta(hours=2))) == 1
        stored = await s.get(Event, event.id)
    assert stored.starts_at == start + timedelta(days=1)
    assert stored.ends_at - stored.starts_at == timedelta(hours=2)
