"""Разбор текста вида «15.10 18:30 Встреча с Аней» или «завтра в 9 стоматолог».

Время разбираем сами (dateparser путает «в 7 вечера»), а дату — сами для частых
случаев (сегодня/завтра/дни недели/«15 октября»/«15.10») и через dateparser для прочих.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateparser.search import search_dates

from bot.timeutils import next_occurrence, repeat_code

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


_N_UNIT = (
    r"(?P<n>\d{1,3}|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)?\s*"
    r"(?P<u>д(?:ень|ня|ней)|сут\w*|недел[юиь]\w*|месяц\w*)"
)
_WORD_NUMS = {
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}  # fmt: skip
# Повторы: «каждый день», «по будням», «каждый понедельник», «раз в 2 недели», «каждые 3 дня»
_REPEATS = [
    (re.compile(r"\bпо\s+будням\b|\bкаждый\s+будний\s+день\b", re.I), "wd", ""),
    (re.compile(r"\b(?:раз\s+в|кажд(?:ые|ый|ую|ое))\s+" + _N_UNIT + r"\b", re.I), None, ""),
    (re.compile(r"\bежедневно\b", re.I), "1d", ""),
    (
        re.compile(
            r"\bкажд(?:ый|ую|ое)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b",
            re.I,
        ),
        "1w",
        r"в \1",
    ),
    (re.compile(r"\bеженедельно\b", re.I), "1w", ""),
    (re.compile(r"\bежемесячно\b", re.I), "1m", ""),
]
# Промежуток времени: «14:20–19:30», «с 14:20 до 19:30», «с 14 до 19», «с 9.30 до 11»
_RANGE = re.compile(
    r"(?:\bс\s*)?\b(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?:-|–|—|до)\s*(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\b"
    r"|\bс\s*(?P<h3>\d{1,2})(?:[:.](?P<m3>\d{2}))?\s*(?:до|-|–|—)\s*(?P<h4>\d{1,2})(?:[:.](?P<m4>\d{2}))?\b",
    re.IGNORECASE,
)


def _period_code(m: re.Match) -> str:
    raw = (m["n"] or "1").lower()
    n = _WORD_NUMS.get(raw) or int(raw)
    unit = m["u"].lower()
    unit = "d" if unit.startswith(("д", "сут")) else "w" if unit.startswith("недел") else "m"
    try:
        return repeat_code(n, unit)
    except ValueError as e:
        raise ParseError("Слишком большой период повтора.") from e


@dataclass(frozen=True)
class ParsedEvent:
    title: str
    starts_at: datetime  # с часовым поясом пользователя
    repeat: str | None = None  # код повтора, см. bot.timeutils («1d», «2w», «wd»…)
    ends_at: datetime | None = None  # конец промежутка «14:20–19:30»
    hashtag: str | None = None  # «#работа» из текста, без «#»


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
    hashtag, text = _find_hashtag(text.strip())
    repeat, text = _find_repeat(text)
    end_time, text = _find_range(text)
    starts_at, rest = _extract(text, tz, now)
    if repeat == "wd":
        while starts_at.weekday() >= 5:
            starts_at += timedelta(days=1)
    if repeat and starts_at <= now:
        # «каждый день в 8», а 8:00 уже прошло — значит, со следующего раза
        starts_at = next_occurrence(starts_at, repeat, tz_name, now)
    if starts_at <= now:
        raise ParseError("Это время уже прошло. Укажите время в будущем.")
    if starts_at > now + timedelta(days=366 * 5):
        raise ParseError("Слишком далёкая дата. Проверьте год.")
    ends_at = None
    if end_time is not None:
        ends_at = datetime.combine(starts_at.date(), end_time, tzinfo=tz)
        if ends_at <= starts_at:
            ends_at += timedelta(days=1)  # «с 22 до 2» — заканчивается ночью
    title = _clean_title(rest)
    if not title:
        raise ParseError("Не вижу названия события. Напишите, что запланировано.")
    return ParsedEvent(title, starts_at, repeat, ends_at, hashtag)


def _find_hashtag(text: str) -> tuple[str | None, str]:
    m = re.search(r"(?<!\w)#(\w+)", text)
    if not m:
        return None, text
    return m[1], _cut(text, m)


def _find_range(text: str) -> tuple[time | None, str]:
    """«с 14:20 до 19:30» → время конца 19:30, а в тексте остаётся «в 14:20»."""
    m = _RANGE.search(text)
    if not m:
        return None, text
    if m["h1"]:
        h1, m1, h2, m2 = m["h1"], m["m1"], m["h2"], m["m2"]
    else:
        h1, m1, h2, m2 = m["h3"], m["m3"], m["h4"], m["m4"]
    h1, m1, h2, m2 = int(h1), int(m1 or 0), int(h2), int(m2 or 0)
    if max(h1, h2) > 23 or max(m1, m2) > 59:
        raise ParseError("Такого времени не бывает. Проверьте числа.")
    return time(h2, m2), text[: m.start()] + f" в {h1}:{m1:02d} " + text[m.end() :]


def _find_repeat(text: str) -> tuple[str | None, str]:
    for pattern, repeat, replacement in _REPEATS:
        if m := pattern.search(text):
            return repeat or _period_code(m), pattern.sub(replacement, text, count=1)
    return None, text


def parse_period(text: str) -> str:
    """Период из ответа пользователя: «3 дня», «раз в 2 недели», «каждый месяц», «по будням»."""
    text = text.strip().lower()
    if text in ("день", "дня"):
        text = "1 день"
    repeat, rest = _find_repeat(text)
    if repeat is None:
        m = re.fullmatch(_N_UNIT, text)
        if not m:
            raise ParseError("Не понял период. Напишите, например: «3 дня», «2 недели», «месяц».")
        return _period_code(m)
    if rest.strip(_PUNCT):
        raise ParseError("Не понял период. Напишите, например: «3 дня», «2 недели», «месяц».")
    return repeat


def parse_when(text: str, tz_name: str, now: datetime | None = None) -> ParsedEvent:
    """Разбирает только дату/время (или промежуток) и повтор, когда название уже известно."""
    return parse_event(f"{text} __event__", tz_name, now)


def parse_datetime(text: str, tz_name: str, now: datetime | None = None) -> datetime:
    return parse_when(text, tz_name, now).starts_at
