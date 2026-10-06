from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.timeutils import split_repeat

_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip


def format_dt(dt: datetime, tz_name: str, now: datetime | None = None) -> str:
    """«ср, 15 октября, 18:30» в часовом поясе пользователя (год — только если не текущий)."""
    tz = ZoneInfo(tz_name)
    local = dt.astimezone(tz)
    now = (now or datetime.now(tz)).astimezone(tz)
    year = f" {local.year}" if local.year != now.year else ""
    return (
        f"{_WEEKDAYS[local.weekday()]}, {local.day} {_MONTHS[local.month - 1]}{year}, {local:%H:%M}"
    )


def format_remind(minutes: int) -> str:
    if minutes == 0:
        return "в момент начала"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "за 1 день" if days == 1 else f"за {days} дн."
    if minutes % 60 == 0:
        return f"за {minutes // 60} ч"
    return f"за {minutes} мин"


def format_day(dt: datetime, tz_name: str, now: datetime | None = None) -> str:
    """Заголовок дня: «Сегодня, пн 5 октября», «Завтра, вт 6 октября», «Чт, 15 октября»."""
    tz = ZoneInfo(tz_name)
    local = dt.astimezone(tz).date()
    today = (now or datetime.now(tz)).astimezone(tz).date()
    base = f"{_WEEKDAYS[local.weekday()]}, {local.day} {_MONTHS[local.month - 1]}"
    if local.year != today.year:
        base += f" {local.year}"
    delta = (local - today).days
    if delta == 0:
        return f"Сегодня, {base}"
    if delta == 1:
        return f"Завтра, {base}"
    return base[:1].upper() + base[1:]


_WEEKDAYS_ACC = [
    "каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг",
    "каждую пятницу", "каждую субботу", "каждое воскресенье",
]  # fmt: skip
_WEEKDAYS_PL = [
    "по понедельникам", "по вторникам", "по средам", "по четвергам",
    "по пятницам", "по субботам", "по воскресеньям",
]  # fmt: skip
_UNIT_FORMS = {"d": ("день", "дня", "дней"), "w": ("неделю", "недели", "недель"),
               "m": ("месяц", "месяца", "месяцев")}  # fmt: skip


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(3, ("день", "дня", "дней")) → «дня»."""
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def format_period(code: str) -> str:
    """Период без привязки к дате: «каждый день», «раз в 3 дня», «по будням»."""
    n, unit = split_repeat(code)
    if unit == "wd":
        return "по будням"
    if n == 1:
        return {"d": "каждый день", "w": "каждую неделю", "m": "каждый месяц"}[unit]
    return f"раз в {n} {plural(n, _UNIT_FORMS[unit])}"


def format_repeat(repeat: str | None, starts_at: datetime, tz_name: str) -> str:
    """«каждый понедельник», «раз в 2 недели, по средам», «каждый месяц 15-го»…"""
    if repeat is None:
        return "не повторять"
    local = starts_at.astimezone(ZoneInfo(tz_name))
    n, unit = split_repeat(repeat)
    if unit == "w":
        if n == 1:
            return _WEEKDAYS_ACC[local.weekday()]
        return f"{format_period(repeat)}, {_WEEKDAYS_PL[local.weekday()]}"
    if unit == "m":
        return f"{format_period(repeat)} {local.day}-го"
    return format_period(repeat)


def format_span(starts_at: datetime, ends_at: datetime | None, tz_name: str,
                now: datetime | None = None) -> str:  # fmt: skip
    """«вт, 6 октября, 14:20–19:30»; если конец в другой день — «… 22:00 – ср, 7 октября, 02:00»."""
    text = format_dt(starts_at, tz_name, now)
    if ends_at is None:
        return text
    zone = ZoneInfo(tz_name)
    start, end = starts_at.astimezone(zone), ends_at.astimezone(zone)
    if end.date() == start.date() or (end - start < timedelta(days=1) and end.hour < 6):
        return f"{text}–{end:%H:%M}"
    return f"{text} – {format_dt(ends_at, tz_name, now)}"


def format_hours(starts_at: datetime, ends_at: datetime | None, tz_name: str) -> str:
    """Время для строки списка: «14:20» или «14:20–19:30»."""
    zone = ZoneInfo(tz_name)
    text = f"{starts_at.astimezone(zone):%H:%M}"
    if ends_at is not None:
        text += f"–{ends_at.astimezone(zone):%H:%M}"
    return text
