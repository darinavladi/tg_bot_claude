from datetime import UTC, datetime

from bot.formatting import format_dt, format_remind


def test_format_dt_converts_to_user_tz():
    utc = datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
    now = datetime(2026, 10, 4, tzinfo=UTC)
    assert format_dt(utc, "Europe/Moscow", now) == "чт, 15 октября, 18:30"
    assert format_dt(utc, "Europe/Moscow", datetime(2025, 1, 1, tzinfo=UTC)).endswith(
        "октября 2026, 18:30"
    )


def test_format_remind():
    assert format_remind(0) == "в момент начала"
    assert format_remind(15) == "за 15 мин"
    assert format_remind(60) == "за 1 ч"
    assert format_remind(1440) == "за 1 день"
