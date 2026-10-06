"""/done — отметить событие завершённым (на сегодня и пропущенные)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import categories, repo
from bot.config import Config
from bot.formatting import format_hours, format_span
from bot.keyboards import DoneCb, done_kb
from bot.reminders import Reminders
from bot.timeutils import period_range

router = Router(name="done")

NOTHING = "Сейчас нечего завершать. Здесь появляются события на сегодня и пропущенные."


async def _due(
    session: AsyncSession, user_id: int, tz: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и кнопки со списком событий, которые можно завершить."""
    now = datetime.now(UTC)
    _, end_of_today = period_range("today", tz, now)
    events = await repo.list_due(session, user_id, end_of_today)
    if not events:
        return NOTHING, None
    names = await repo.category_names(session, user_id)
    today = period_range("today", tz, now)[0]
    buttons = []
    for event in events:
        hours = format_hours(event.starts_at, event.ends_at, tz)
        if event.starts_at < today:
            hours = f"{event.starts_at.astimezone(ZoneInfo(tz)):%d.%m} {hours}"
        title = categories.with_category(event.title, names.get(event.category_id))
        buttons.append((f"{hours} {title}", event.id))
    return "Какое событие завершить?", done_kb(buttons)


@router.message(Command("done"))
async def cmd_done(message: Message, session: AsyncSession, config: Config) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    text, markup = await _due(session, user.id, user.timezone)
    await message.answer(text, reply_markup=markup)


@router.callback_query(DoneCb.filter())
async def done_event(
    callback: CallbackQuery, callback_data: DoneCb, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    event = await repo.complete(session, user.id, callback_data.event_id, user.timezone)
    if event is None:
        await callback.answer("Событие уже завершено или удалено.", show_alert=True)
        text, markup = await _due(session, user.id, user.timezone)
        await callback.message.edit_text(text, reply_markup=markup)
        return
    result = f"✅ Завершено: {event.title}"
    if event.repeat:
        reminders.schedule(event.id, event.remind_at)
        result += (
            f"\n🔁 Следующий раз: {format_span(event.starts_at, event.ends_at, user.timezone)}"
        )
    else:
        reminders.cancel(event.id)
    text, markup = await _due(session, user.id, user.timezone)
    if markup is None:
        await callback.message.edit_text(result)
    else:
        await callback.message.edit_text(f"{result}\n\n{text}", reply_markup=markup)
    await callback.answer("Завершено")
