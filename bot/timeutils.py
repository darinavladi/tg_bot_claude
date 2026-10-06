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


# Повтор хранится кодом: «wd» — по будням, иначе «<N><единица>»: «1d» — каждый день,
# «3d» — раз в 3 дня, «2w» — раз в 2 недели, «1m» — каждый месяц
REPEAT_UNITS = {"d": 365, "w": 52, "m": 24}  # единица → наибольшее N
LEGACY_REPEATS = {"daily": "1d", "weekly": "1w", "monthly": "1m", "weekdays": "wd"}


def repeat_code(n: int, unit: str) -> str:
    if unit not in REPEAT_UNITS or not 1 <= n <= REPEAT_UNITS[unit]:
        raise ValueError(f"{n}{unit}")
    return f"{n}{unit}"


def split_repeat(code: str) -> tuple[int, str]:
    """«3d» → (3, "d"); «wd» → (1, "wd"). ValueError для неизвестного кода."""
    code = LEGACY_REPEATS.get(code, code)
    if code == "wd":
        return 1, "wd"
    n, unit = code[:-1], code[-1:]
    if not n.isdigit() or unit not in REPEAT_UNITS or not 1 <= int(n) <= REPEAT_UNITS[unit]:
        raise ValueError(code)
    return int(n), unit


def is_repeat(code: str | None) -> bool:
    try:
        split_repeat(code or "")
    except ValueError:
        return False
    return True


def _add_months(dt: datetime, months: int, day: int) -> datetime:
    """Сдвиг на `months` месяцев с числом `day` (31-е в коротком месяце → последний день)."""
    total = dt.year * 12 + dt.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    last = (datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return dt.replace(year=year, month=month, day=min(day, last))


def _step(local: datetime, n: int, unit: str, day: int) -> datetime:
    """Следующее повторение после `local` (без проверки, что оно позже чего-либо)."""
    if unit == "d":
        return local + timedelta(days=n)
    if unit == "wd":
        local += timedelta(days=1)
        while local.weekday() >= 5:
            local += timedelta(days=1)
        return local
    if unit == "w":
        return local + timedelta(weeks=n)
    return _add_months(local, n, day)


def next_occurrence(
    starts_at: datetime, repeat: str, tz: str, after: datetime, day: int | None = None
) -> datetime:
    """Ближайшее повторение события строго позже `after` (по «настенному» времени пояса tz).

    `day` — число месяца для ежемесячных событий (по умолчанию число из starts_at). Его
    передают отдельно, чтобы событие «31-го» после февраля вернулось на 31-е, а не
    осталось на 28-м.
    """
    n, unit = split_repeat(repeat)
    local = starts_at.astimezone(ZoneInfo(tz))
    day = day or local.day
    while local <= after:
        local = _step(local, n, unit, day)
    return local


def occurrences(
    anchor: datetime, repeat: str, tz: str, start: datetime, end: datetime
) -> list[datetime]:
    """Все повторения серии, начатой в `anchor`, попадающие в интервал [start, end)."""
    n, unit = split_repeat(repeat)
    local = anchor.astimezone(ZoneInfo(tz))
    day = local.day
    # Старую серию не перебираем с самого начала, а сразу перескакиваем ближе к start,
    # целым числом периодов, чтобы не сбить шаг «раз в 3 дня»
    if unit == "m":
        months = (start.year - local.year) * 12 + start.month - local.month
        periods = months // n - 1
        if periods > 0:
            local = _add_months(local, periods * n, day)
    else:
        period_days = {"d": n, "w": 7 * n, "wd": 7}[unit]
        periods = (start - local).days // period_days - 1
        if periods > 0:
            local += timedelta(days=periods * period_days)
    result = []
    while local < end:
        if local >= start:
            result.append(local)
        local = _step(local, n, unit, day)
    return result
