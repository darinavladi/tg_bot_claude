"""Отправка напоминаний.

Источник правды — колонка events.remind_at в базе. Планировщик держит задачи
только в памяти, а при запуске бота восстанавливает их из базы, поэтому
напоминания не теряются после перезапуска.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import repo
from bot.db import Event, EventStatus
from bot.formatting import format_day, format_dt
from bot.keyboards import reminder_kb
from bot.timeutils import period_range

log = logging.getLogger(__name__)


def _job_id(event_id: int) -> str:
    return f"event:{event_id}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _humanize(delta: timedelta) -> str:
    minutes = round(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} {_plural(minutes, 'минуту', 'минуты', 'минут')}"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        text = f"{hours} {_plural(hours, 'час', 'часа', 'часов')}"
        return text + (f" {rest} мин" if rest else "")
    days = round(hours / 24)
    return f"{days} {_plural(days, 'день', 'дня', 'дней')}"


def reminder_text(event: Event, tz: str, now: datetime) -> str:
    left = event.starts_at - now
    when = format_dt(event.starts_at, tz, now)
    if left > timedelta(minutes=1):
        return f"⏰ Через {_humanize(left)}: {event.title}\n🕒 {when}"
    if left > -timedelta(minutes=5):
        return f"⏰ Сейчас: {event.title}\n🕒 {when}"
    return f"⏰ Пропущенное напоминание: {event.title}\n🕒 Было {when}"


def summary_text(events: list[Event], tz: str, now: datetime) -> str:
    day = format_day(now, tz, now)
    if not events:
        return f"☀️ Доброе утро! {day}\n\nНа сегодня ничего не запланировано."
    zone = ZoneInfo(tz)
    lines = [f"{e.starts_at.astimezone(zone):%H:%M} {e.title}" for e in events]
    return f"☀️ Доброе утро! {day}\n\nПлан на сегодня:\n" + "\n".join(lines)


def _summary_job_id(user_id: int) -> str:
    return f"summary:{user_id}"


class Reminders:
    def __init__(self, bot: Bot, sessionmaker: async_sessionmaker, default_tz: str) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.default_tz = default_tz
        self.scheduler = AsyncIOScheduler(timezone=UTC)

    async def start(self) -> None:
        """Запускает планировщик и ставит все напоминания, сохранённые в базе."""
        self.scheduler.start()
        async with self.sessionmaker() as session:
            events = await repo.list_pending_reminders(session)
            users = await repo.list_summary_users(session)
        for event in events:
            self.schedule(event.id, event.remind_at)
        for user in users:
            self.schedule_summary(user.id, user.summary_time, user.timezone)
        log.info("Восстановлено напоминаний: %d, утренних сводок: %d", len(events), len(users))

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def schedule(self, event_id: int, remind_at: datetime | None) -> None:
        """Ставит (или переставляет) напоминание. Прошедшее время — отправить сразу."""
        if remind_at is None:
            self.cancel(event_id)
            return
        run_at = max(remind_at, datetime.now(UTC) + timedelta(seconds=1))
        self.scheduler.add_job(
            self.send,
            "date",
            run_date=run_at,
            args=[event_id],
            id=_job_id(event_id),
            replace_existing=True,
            misfire_grace_time=None,
        )

    def cancel(self, event_id: int) -> None:
        job = self.scheduler.get_job(_job_id(event_id))
        if job is not None:
            job.remove()

    async def snooze(self, event_id: int, minutes: int) -> datetime:
        remind_at = datetime.now(UTC) + timedelta(minutes=minutes)
        async with self.sessionmaker() as session:
            await repo.set_remind_at(session, event_id, remind_at)
        self.schedule(event_id, remind_at)
        return remind_at

    async def send(self, event_id: int) -> None:
        async with self.sessionmaker() as session:
            event = await session.get(Event, event_id)
            if event is None or event.status != EventStatus.ACTIVE or event.remind_at is None:
                return
            user = await repo.get_or_create_user(session, event.user_id, self.default_tz)
            text = reminder_text(event, user.timezone, datetime.now(UTC))
            try:
                await self.bot.send_message(event.user_id, text, reply_markup=reminder_kb(event.id))
            except TelegramAPIError:
                # Например, пользователь заблокировал бота — не пытаемся снова
                log.exception("Не удалось отправить напоминание по событию %s", event_id)
            await repo.set_remind_at(session, event_id, None)

    # ---------- Утренняя сводка ----------

    def schedule_summary(self, user_id: int, summary_time: str | None, tz: str) -> None:
        """Ежедневная сводка в summary_time («ЧЧ:ММ») по поясу пользователя; None — выключить."""
        if summary_time is None:
            job = self.scheduler.get_job(_summary_job_id(user_id))
            if job is not None:
                job.remove()
            return
        hour, minute = map(int, summary_time.split(":"))
        self.scheduler.add_job(
            self.send_summary,
            CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(tz)),
            args=[user_id],
            id=_summary_job_id(user_id),
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )

    async def send_summary(self, user_id: int) -> None:
        async with self.sessionmaker() as session:
            user = await repo.get_or_create_user(session, user_id, self.default_tz)
            if user.summary_time is None:
                return
            now = datetime.now(UTC)
            start, end = period_range("today", user.timezone, now)
            events = await repo.list_events(session, user_id, start=start, end=end)
        try:
            await self.bot.send_message(user_id, summary_text(events, user.timezone, now))
        except TelegramAPIError:
            log.exception("Не удалось отправить утреннюю сводку пользователю %s", user_id)
