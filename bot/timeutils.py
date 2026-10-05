from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def period_range(kind: str, tz: str, now: datetime) -> tuple[datetime, datetime]:
    """Интервал [start, end) в UTC: today — сегодня, week — 7 дней, all — всё будущее."""
    zone = ZoneInfo(tz)
    day_start = datetime.combine(now.astimezone(zone).date(), time(), tzinfo=zone)
    if kind == "today":
        return day_start.astimezone(UTC), (day_start + timedelta(days=1)).astimezone(UTC)
    if kind == "week":
        return day_start.astimezone(UTC), (day_start + timedelta(days=7)).astimezone(UTC)
    return now.astimezone(UTC), now.astimezone(UTC) + timedelta(days=366 * 5)


def is_valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return "/" in name or name == "UTC"


def parse_hhmm(text: str) -> str | None:
    """«8:00», «08.30», «8» → «08:00»/«08:30»; иначе None."""
    text = text.strip().replace(".", ":")
    parts = text.split(":")
    if not 1 <= len(parts) <= 2 or not all(p.isdigit() for p in parts):
        return None
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) == 2 else 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


REPEATS = ("daily", "weekdays", "weekly", "monthly")


def _add_month(dt: datetime, day: int) -> datetime:
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    # 31-е число в коротком месяце → последний день месяца
    last = (datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return dt.replace(year=year, month=month, day=min(day, last))


def next_occurrence(starts_at: datetime, repeat: str, tz: str, after: datetime) -> datetime:
    """Ближайшее повторение события строго позже `after` (по «настенному» времени пояса tz)."""
    if repeat not in REPEATS:
        raise ValueError(repeat)
    local = starts_at.astimezone(ZoneInfo(tz))
    day = local.day
    while local <= after:
        if repeat == "daily":
            local += timedelta(days=1)
        elif repeat == "weekdays":
            local += timedelta(days=1)
            while local.weekday() >= 5:
                local += timedelta(days=1)
        elif repeat == "weekly":
            local += timedelta(weeks=1)
        else:
            local = _add_month(local, day)
    return local
