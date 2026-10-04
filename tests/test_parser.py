from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.parser import ParseError, parse_datetime, parse_event

TZ = "Europe/Moscow"
# Воскресенье, 4 октября 2026, 09:00 по Москве
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=ZoneInfo(TZ))


def dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ))


@pytest.mark.parametrize(
    ("text", "title", "starts_at"),
    [
        ("15.10 18:30 Встреча с Аней", "Встреча с Аней", dt(2026, 10, 15, 18, 30)),
        ("встреча с Аней 15.10 18:30", "Встреча с Аней", dt(2026, 10, 15, 18, 30)),
        ("15.10.2026 в 18:30 встреча", "Встреча", dt(2026, 10, 15, 18, 30)),
        ("15.10 в 18.30 встреча", "Встреча", dt(2026, 10, 15, 18, 30)),
        ("5.11.26 9:05 тест", "Тест", dt(2026, 11, 5, 9, 5)),
        ("15.10 в 9 утра врач", "Врач", dt(2026, 10, 15, 9, 0)),
        ("15 октября в 18:30 встреча с Аней", "Встреча с Аней", dt(2026, 10, 15, 18, 30)),
        ("1 ноября в 9 утра экзамен", "Экзамен", dt(2026, 11, 1, 9, 0)),
        ("25 декабря 2026 года в 12:00 праздник", "Праздник", dt(2026, 12, 25, 12, 0)),
        ("завтра в 9 стоматолог", "Стоматолог", dt(2026, 10, 5, 9, 0)),
        ("стоматолог завтра в 9:30", "Стоматолог", dt(2026, 10, 5, 9, 30)),
        ("послезавтра в 10 утра тренировка", "Тренировка", dt(2026, 10, 6, 10, 0)),
        ("завтра в 7 вечера ужин", "Ужин", dt(2026, 10, 5, 19, 0)),
        ("в среду в 3 дня врач", "Врач", dt(2026, 10, 7, 15, 0)),
        ("в пятницу в 19:00 кино", "Кино", dt(2026, 10, 9, 19, 0)),
        ("в воскресенье в 12 обед", "Обед", dt(2026, 10, 4, 12, 0)),
        ("в следующее воскресенье в 12 обед", "Обед", dt(2026, 10, 11, 12, 0)),
        ("сегодня в 23:00 сон", "Сон", dt(2026, 10, 4, 23, 0)),
        ("в 10 тренировка", "Тренировка", dt(2026, 10, 4, 10, 0)),
        ("врач в 18.30", "Врач", dt(2026, 10, 4, 18, 30)),
        ("в полдень обед", "Обед", dt(2026, 10, 4, 12, 0)),
        ("через 2 часа позвонить маме", "Позвонить маме", dt(2026, 10, 4, 11, 0)),
        ("через полчаса чай", "Чай", dt(2026, 10, 4, 9, 30)),
        ("оплатить 5 счетов завтра в 10", "Оплатить 5 счетов", dt(2026, 10, 5, 10, 0)),
    ],
)
def test_parses(text, title, starts_at):
    parsed = parse_event(text, TZ, NOW)
    assert parsed.title == title
    assert parsed.starts_at == starts_at


def test_time_only_in_past_moves_to_tomorrow():
    assert parse_event("в 8 зарядка", TZ, NOW).starts_at == dt(2026, 10, 5, 8, 0)


def test_date_without_year_in_past_moves_to_next_year():
    assert parse_event("01.01 10:00 Новый год", TZ, NOW).starts_at == dt(2027, 1, 1, 10, 0)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("привет", "Не понял"),
        ("завтра стоматолог", "Не вижу времени"),
        ("15.10 18:30", "Не вижу названия"),
        ("32.10 18:00 что-то", "даты не бывает"),
        ("купить хлеб в 25:00", "времени не бывает"),
        ("сегодня в 8:00 зарядка", "уже прошло"),
        ("15.10.2020 18:00 старое", "уже прошло"),
    ],
)
def test_errors(text, error):
    with pytest.raises(ParseError, match=error):
        parse_event(text, TZ, NOW)


def test_respects_user_timezone():
    parsed = parse_event("завтра в 9 созвон", "Asia/Yekaterinburg", NOW)
    assert parsed.starts_at == datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("Asia/Yekaterinburg"))


def test_parse_datetime_only():
    assert parse_datetime("15.10 18:30", TZ, NOW) == dt(2026, 10, 15, 18, 30)
    assert parse_datetime("завтра в 9", TZ, NOW) == dt(2026, 10, 5, 9, 0)
