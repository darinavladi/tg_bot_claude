from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.db import EventStatus
from bot.formatting import format_dt
from bot.keyboards import ReminderCb
from bot.reminders import Reminders

router = Router(name="reminders")


# Кнопка «Готово» была у напоминаний до появления /done; оставлена для старых сообщений
@router.callback_query(ReminderCb.filter(F.action == "done"))
async def reminder_done(
    callback: CallbackQuery, callback_data: ReminderCb, session: AsyncSession, reminders: Reminders
) -> None:
    event = await repo.get_event(session, callback.from_user.id, callback_data.event_id)
    if event is None:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    if event.repeat is None:
        # Повторяющееся событие не закрываем: оно перенесётся на следующий раз само
        await repo.mark_done(session, callback.from_user.id, event.id)
        reminders.cancel(event.id)
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ Готово")
    await callback.answer("Отмечено")


@router.callback_query(ReminderCb.filter(F.action == "snooze"))
async def reminder_snooze(
    callback: CallbackQuery,
    callback_data: ReminderCb,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    event = await repo.get_event(session, callback.from_user.id, callback_data.event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        await callback.answer("Событие уже неактивно.", show_alert=True)
        return
    remind_at = await reminders.snooze(event.id, callback_data.minutes)
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    await callback.message.edit_text(
        f"{callback.message.text}\n\n💤 Напомню снова: {format_dt(remind_at, user.timezone)}"
    )
    await callback.answer("Отложено")
