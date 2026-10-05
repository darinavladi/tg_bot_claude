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
