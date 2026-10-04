from datetime import datetime
from zoneinfo import ZoneInfo

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
