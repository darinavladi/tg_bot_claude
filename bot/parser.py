"""Разбор текста вида «15.10 18:30 Встреча с Аней» или «завтра в 9 стоматолог».

Время разбираем сами (dateparser путает «в 7 вечера»), а дату — сами для частых
случаев (сегодня/завтра/дни недели/«15 октября»/«15.10») и через dateparser для прочих.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateparser.search import search_dates

_PUNCT = " \t\n,.;:-—–"
_PERIOD = r"(?:\s*(?P<p{n}>утра|дня|вечера|ночи))?"

# «через 2 часа», «через 30 минут», «через полчаса», «через час», «через 3 дня»
_RELATIVE = re.compile(
    r"\bчерез\s+(?P<n>\d+|полчаса|час|день|неделю)\s*"
    r"(?P<u>мин\w*|час\w*|д(?:ень|ня|ней)|недел\w*)?",
    re.IGNORECASE,
)
# Дата и время цифрами рядом: «15.10 18:30», «15.10.2026 в 18.30»
_NUM_DATETIME = re.compile(
    r"\b(?P<d>\d{1,2})[./](?P<m>\d{1,2})(?:[./](?P<y>\d{4}|\d{2}))?"
    r"\s*(?:в\s*)?(?P<H>\d{1,2})[:.](?P<M>\d{2})\b"
)
# Время: «18:30», «в 9», «в 9 ч», «9 утра», «в 7 вечера», «в 18.30».
# Точку как разделитель принимаем только после «в», иначе «15.10» — это дата.
_TIME = re.compile(
    r"\b(?:(?P<H1>\d{1,2}):|в\s*(?P<H1d>\d{1,2})[:.])(?P<M1>\d{2})\b" + _PERIOD.format(n=1)
    + r"|\bв\s*(?P<H2>\d{1,2})\b(?![.:/]\d)(?:\s*(?:ч\b\.?|час(?:а|ов)?\b))?" + _PERIOD.format(n=2)
    + r"|\b(?P<H3>\d{1,2})\s*(?P<p3>утра|дня|вечера|ночи)"
    + r"|\b(?P<noon>в\s+полдень|в\s+полночь)\b",
    re.IGNORECASE,
)  # fmt: skip
# Дата цифрами: «15.10», «15.10.2026», «15/10/26»
_NUM_DATE = re.compile(r"\b(?P<d>\d{1,2})[./](?P<m>\d{1,2})(?:[./](?P<y>\d{4}|\d{2}))?\b")
_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}  # fmt: skip
_TEXT_DATE = re.compile(
    r"\b(?P<d>\d{1,2})\s+(?P<m>январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]"
    r"|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])(?:\s+(?P<y>\d{4}))?(?:\s*г(?:ода)?\.?)?",
    re.IGNORECASE,
)
_DAY_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_DAY_WORD = re.compile(r"\b(?P<w>послезавтра|завтра|сегодня)\b", re.IGNORECASE)
_WEEKDAY = re.compile(
    r"\b(?:в\s+|во\s+)?(?:(?:эт[оуи]|следующ\w+)\s+)?"
    r"(?P<w>понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье)\b",
    re.IGNORECASE,
)
_WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресенье"]


@dataclass(frozen=True)
class ParsedEvent:
    title: str
    starts_at: datetime  # с часовым поясом пользователя


class ParseError(ValueError):
    pass


def _cut(text: str, match: re.Match) -> str:
    return text[: match.start()] + " " + text[match.end() :]


def _to_24h(hour: int, period: str | None) -> int:
    period = (period or "").lower()
    if period in ("дня", "вечера") and hour < 12:
        return hour + 12
    if period == "ночи" and hour == 12:
        return 0
    if period == "утра" and hour == 12:
        return 0
    return hour


def _find_time(text: str) -> tuple[time | None, str]:
    m = _TIME.search(text)
    if not m:
        return None, text
    if m["noon"]:
        t = time(12) if "полдень" in m["noon"].lower() else time(0)
        return t, _cut(text, m)
    if m["M1"]:
        hour, minute, period = int(m["H1"] or m["H1d"]), int(m["M1"]), m["p1"]
    elif m["H2"]:
        hour, minute, period = int(m["H2"]), 0, m["p2"]
    else:
        hour, minute, period = int(m["H3"]), 0, m["p3"]
    hour = _to_24h(hour, period)
    if hour > 23 or minute > 59:
        raise ParseError("Такого времени не бывает. Проверьте числа.")
    return time(hour, minute), _cut(text, m)


def _year_or_next(day: int, month: int, year: int | None, today: date) -> date:
    try:
        if year is not None:
            return date(year, month, day)
        d = date(today.year, month, day)
        # «05.01», написанное в декабре, — это январь следующего года
        return d if d >= today else date(today.year + 1, month, day)
    except ValueError as e:
        raise ParseError("Такой даты не бывает. Проверьте числа.") from e


def _num_year(y: str | None) -> int | None:
    return (2000 + int(y) if len(y) == 2 else int(y)) if y else None


def _find_date(text: str, today: date, tz: ZoneInfo) -> tuple[date | None, str]:
    if m := _NUM_DATE.search(text):
        return _year_or_next(int(m["d"]), int(m["m"]), _num_year(m["y"]), today), _cut(text, m)
    if m := _TEXT_DATE.search(text):
        word = m["m"].lower()
        month = next(v for k, v in _MONTHS.items() if word.startswith(k))
        year = int(m["y"]) if m["y"] else None
        return _year_or_next(int(m["d"]), month, year, today), _cut(text, m)
    if m := _DAY_WORD.search(text):
        return today + timedelta(days=_DAY_WORDS[m["w"].lower()]), _cut(text, m)
    if m := _WEEKDAY.search(text):
        word = m["w"].lower()
        target = next(i for i, w in enumerate(_WEEKDAYS) if word.startswith(w))
        ahead = (target - today.weekday()) % 7
        if "следующ" in m.group(0).lower() and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead), _cut(text, m)
    # Прочие формулировки — через dateparser, берём только дату
    found = search_dates(
        text, languages=["ru"], settings={"PREFER_DATES_FROM": "future", "TIMEZONE": str(tz)}
    )
    if found:
        fragment, dt = max(found, key=lambda f: len(f[0]))
        if not fragment.strip().isdigit():
            return dt.date(), text.replace(fragment, " ", 1)
    return None, text


def _find_relative(text: str, now: datetime) -> tuple[datetime | None, str]:
    m = _RELATIVE.search(text)
    if not m:
        return None, text
    n_raw, unit = m["n"].lower(), (m["u"] or "").lower()
    if n_raw == "полчаса":
        delta = timedelta(minutes=30)
    elif n_raw in ("час", "день", "неделю"):
        delta = {"час": timedelta(hours=1), "день": timedelta(days=1)}.get(
            n_raw, timedelta(weeks=1)
        )
    else:
        n = int(n_raw)
        if unit.startswith("мин"):
            delta = timedelta(minutes=n)
        elif unit.startswith("час"):
            delta = timedelta(hours=n)
        elif unit.startswith("д"):
            delta = timedelta(days=n)
        elif unit.startswith("недел"):
            delta = timedelta(weeks=n)
        else:
            return None, text
    return (now + delta).replace(second=0, microsecond=0), _cut(text, m)


def _clean_title(text: str) -> str:
    title = re.sub(r"\s+", " ", text).strip(_PUNCT)
    return title[:1].upper() + title[1:] if title else ""


def _extract(text: str, tz: ZoneInfo, now: datetime) -> tuple[datetime, str]:
    starts_at, rest = _find_relative(text, now)
    if starts_at is not None:
        return starts_at, rest

    if m := _NUM_DATETIME.search(text):
        d = _year_or_next(int(m["d"]), int(m["m"]), _num_year(m["y"]), now.date())
        hour, minute = int(m["H"]), int(m["M"])
        if hour > 23 or minute > 59:
            raise ParseError("Такого времени не бывает. Проверьте числа.")
        return datetime.combine(d, time(hour, minute), tzinfo=tz), _cut(text, m)

    t, rest = _find_time(text)
    d, rest = _find_date(rest, now.date(), tz)
    if t is None:
        if d is None:
            raise ParseError("Не понял дату и время.")
        raise ParseError("Не вижу времени. Укажите его, например: «завтра в 9:00 стоматолог».")
    if d is None:
        # Только время: сегодня, а если уже прошло — завтра
        d = now.date()
        if datetime.combine(d, t, tzinfo=tz) <= now:
            d += timedelta(days=1)
    return datetime.combine(d, t, tzinfo=tz), rest


def parse_event(text: str, tz_name: str, now: datetime | None = None) -> ParsedEvent:
    """Достаёт из текста дату/время и название события.

    Бросает ParseError с понятным сообщением, если разобрать не удалось.
    """
    tz = ZoneInfo(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    starts_at, rest = _extract(text.strip(), tz, now)
    if starts_at <= now:
        raise ParseError("Это время уже прошло. Укажите время в будущем.")
    if starts_at > now + timedelta(days=366 * 5):
        raise ParseError("Слишком далёкая дата. Проверьте год.")
    title = _clean_title(rest)
    if not title:
        raise ParseError("Не вижу названия события. Напишите, что запланировано.")
    return ParsedEvent(title, starts_at)


def parse_datetime(text: str, tz_name: str, now: datetime | None = None) -> datetime:
    """Разбирает только дату и время (для пошагового /add, где название уже известно)."""
    return parse_event(f"{text} __event__", tz_name, now).starts_at
