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


def _add_months(dt: datetime, months: int, day: int) -> datetime:
    """Сдвиг на `months` месяцев с числом `day` (31-е в коротком месяце → последний день)."""
    total = dt.year * 12 + dt.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    last = (datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return dt.replace(year=year, month=month, day=min(day, last))


def _step(local: datetime, repeat: str, day: int) -> datetime:
    """Следующее повторение после `local` (без проверки, что оно позже чего-либо)."""
    if repeat == "daily":
        return local + timedelta(days=1)
    if repeat == "weekdays":
        local += timedelta(days=1)
        while local.weekday() >= 5:
            local += timedelta(days=1)
        return local
    if repeat == "weekly":
        return local + timedelta(weeks=1)
    return _add_months(local, 1, day)


def next_occurrence(
    starts_at: datetime, repeat: str, tz: str, after: datetime, day: int | None = None
) -> datetime:
    """Ближайшее повторение события строго позже `after` (по «настенному» времени пояса tz).

    `day` — число месяца для ежемесячных событий (по умолчанию число из starts_at). Его
    передают отдельно, чтобы событие «31-го» после февраля вернулось на 31-е, а не
    осталось на 28-м.
    """
    if repeat not in REPEATS:
        raise ValueError(repeat)
    local = starts_at.astimezone(ZoneInfo(tz))
    day = day or local.day
    while local <= after:
        local = _step(local, repeat, day)
    return local


def occurrences(
    anchor: datetime, repeat: str, tz: str, start: datetime, end: datetime
) -> list[datetime]:
    """Все повторения серии, начатой в `anchor`, попадающие в интервал [start, end)."""
    if repeat not in REPEATS:
        raise ValueError(repeat)
    local = anchor.astimezone(ZoneInfo(tz))
    day = local.day
    # Старую серию не перебираем с самого начала, а сразу перескакиваем ближе к start
    if repeat == "monthly":
        months = (start.year - local.year) * 12 + start.month - local.month - 2
        if months > 0:
            local = _add_months(local, months, day)
    else:
        weeks = (start - local).days // 7 - 1
        if weeks > 0:
            local += timedelta(weeks=weeks)
    result = []
    while local < end:
        if local >= start:
            result.append(local)
        local = _step(local, repeat, day)
    return result
