"""Просмотр расписания (/today, /week, /list) и управление событием: изменить, удалить."""

from datetime import UTC, datetime
from math import ceil
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.db import Event, EventStatus
from bot.formatting import format_day, format_dt, format_remind, format_repeat
from bot.keyboards import (
    EditRemindCb,
    EventCb,
    ListCb,
    RepeatCb,
    delete_confirm_kb,
    edit_remind_kb,
    event_kb,
    list_kb,
    repeat_kb,
)
from bot.parser import ParseError, parse_when
from bot.reminders import Reminders
from bot.timeutils import period_range

router = Router(name="manage")

PAGE_SIZE = 10
TITLES = {
    "today": "Сегодня",
    "week": "На неделю",
    "all": "Все запланированные события",
}
EMPTY = {
    "today": "На сегодня ничего не запланировано.",
    "week": "На ближайшие 7 дней ничего не запланировано.",
    "all": "Запланированных событий нет.",
}


class EditEvent(StatesGroup):
    title = State()
    time = State()


def render_list(
    events: list[Event], kind: str, page: int, tz: str, now: datetime
) -> tuple[str, list[tuple[str, int]], int]:
    """Текст страницы (с группировкой по дням), кнопки событий и число страниц."""
    pages = max(1, ceil(len(events) / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = events[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    header = TITLES[kind]
    if pages > 1:
        header += f" (стр. {page + 1} из {pages})"
    lines = [f"📋 {header}"]
    buttons = []
    current_day = None
    zone = ZoneInfo(tz)
    for n, event in enumerate(chunk, start=page * PAGE_SIZE + 1):
        local = event.starts_at.astimezone(zone)
        if local.date() != current_day:
            current_day = local.date()
            lines.append(f"\n{format_day(event.starts_at, tz, now)}")
        mark = " ✓" if event.starts_at < now else ""
        if getattr(event, "repeat", None):
            mark += " 🔁"
        lines.append(f"{n}. {local:%H:%M} {event.title}{mark}")
        buttons.append((f"{n}. {local:%H:%M} {event.title}"[:60], event.id))
    lines.append("\nНажмите на событие, чтобы изменить или удалить его.")
    return "\n".join(lines), buttons, pages


async def _show_list(
    target: Message, kind: str, page: int, user_id: int, session: AsyncSession, config: Config,
    edit: bool = False,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, user_id, config.default_tz)
    now = datetime.now(UTC)
    start, end = period_range(kind, user.timezone, now)
    events = await repo.list_events(session, user_id, start=start, end=end)
    if not events:
        text, markup = EMPTY[kind], None
    else:
        text, buttons, pages = render_list(events, kind, page, user.timezone, now)
        page = min(page, pages - 1)
        markup = list_kb(buttons, kind, page, pages)
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession, config: Config) -> None:
    await _show_list(message, "today", 0, message.from_user.id, session, config)


@router.message(Command("week"))
async def cmd_week(message: Message, session: AsyncSession, config: Config) -> None:
    await _show_list(message, "week", 0, message.from_user.id, session, config)


@router.message(Command("list"))
async def cmd_list(message: Message, session: AsyncSession, config: Config) -> None:
    await _show_list(message, "all", 0, message.from_user.id, session, config)


@router.callback_query(ListCb.filter())
async def list_page(
    callback: CallbackQuery, callback_data: ListCb, session: AsyncSession, config: Config
) -> None:
    await _show_list(
        callback.message, callback_data.kind, callback_data.page, callback.from_user.id,
        session, config, edit=True,
    )  # fmt: skip
    await callback.answer()


# ---------- Карточка события ----------


def card_text(event: Event, tz: str) -> str:
    text = f"📅 {event.title}\n🕒 {format_dt(event.starts_at, tz)}\n"
    if event.repeat:
        text += f"🔁 {format_repeat(event.repeat, event.starts_at, tz).capitalize()}\n"
    text += f"⏰ Напоминание {format_remind(event.remind_before_min)}"
    if event.status == EventStatus.DONE:
        text += "\n✅ Выполнено"
    return text


async def _load(
    callback: CallbackQuery, event_id: int, session: AsyncSession, config: Config
) -> tuple[Event | None, str]:
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    event = await repo.get_event(session, user.id, event_id)
    if event is None or event.status == EventStatus.CANCELLED:
        await callback.answer("Событие не найдено, возможно, оно удалено.", show_alert=True)
        return None, user.timezone
    return event, user.timezone


@router.callback_query(EventCb.filter(F.action == "show"))
async def event_show(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await callback.message.answer(card_text(event, tz), reply_markup=event_kb(event.id))
    await callback.answer()


# ---------- Удаление ----------


@router.callback_query(EventCb.filter(F.action == "delete"))
async def event_delete(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await callback.message.edit_text(
        card_text(event, tz) + "\n\nУдалить это событие?",
        reply_markup=delete_confirm_kb(event.id),
    )
    await callback.answer()


@router.callback_query(EventCb.filter(F.action == "delete_yes"))
async def event_delete_yes(
    callback: CallbackQuery,
    callback_data: EventCb,
    session: AsyncSession,
    config: Config,
    reminders: Reminders,
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    title = event.title
    await repo.delete_event(session, callback.from_user.id, event.id)
    reminders.cancel(event.id)
    await callback.message.edit_text(f"🗑 Удалено: {title}")
    await callback.answer()


@router.callback_query(EventCb.filter(F.action == "delete_no"))
async def event_delete_no(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await callback.message.edit_text(card_text(event, tz), reply_markup=event_kb(event.id))
    await callback.answer()


# ---------- Изменение напоминания ----------


@router.callback_query(EventCb.filter(F.action == "remind"))
async def event_remind(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await callback.message.edit_text(
        card_text(event, tz) + "\n\nКогда напомнить?", reply_markup=edit_remind_kb(event.id)
    )
    await callback.answer()


@router.callback_query(EditRemindCb.filter())
async def event_remind_set(
    callback: CallbackQuery,
    callback_data: EditRemindCb,
    session: AsyncSession,
    config: Config,
    reminders: Reminders,
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    event = await repo.update_event(
        session, callback.from_user.id, event.id, remind_before_min=callback_data.minutes
    )
    reminders.schedule(event.id, event.remind_at)
    await callback.message.edit_text(
        card_text(event, tz) + "\n\n✅ Напоминание изменено", reply_markup=event_kb(event.id)
    )
    await callback.answer()


# ---------- Изменение названия и времени ----------


@router.callback_query(EventCb.filter(F.action.in_({"title", "time"})))
async def event_edit_start(
    callback: CallbackQuery,
    callback_data: EventCb,
    state: FSMContext,
    session: AsyncSession,
    config: Config,
) -> None:
    event, _ = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await state.clear()
    await state.update_data(event_id=event.id)
    if callback_data.action == "title":
        await state.set_state(EditEvent.title)
        await callback.message.answer(f"Новое название для «{event.title}»? (отменить: /cancel)")
    else:
        await state.set_state(EditEvent.time)
        await callback.message.answer(
            "Новые дата и время? Например: «15.10 18:30» или «завтра в 9». (отменить: /cancel)"
        )
    await callback.answer()


@router.message(EditEvent.title, F.text, ~F.text.startswith("/"))
async def event_edit_title(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    title = message.text.strip()[:500]
    if not title:
        await message.answer("Напишите название текстом.")
        return
    data = await state.get_data()
    await state.clear()
    event = await repo.update_event(session, message.from_user.id, data["event_id"], title=title)
    if event is None:
        await message.answer("Событие не найдено.")
        return
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    await message.answer(
        "✅ Название изменено\n\n" + card_text(event, user.timezone),
        reply_markup=event_kb(event.id),
    )


@router.message(EditEvent.time, F.text, ~F.text.startswith("/"))
async def event_edit_time(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    config: Config,
    reminders: Reminders,
) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        starts_at, repeat = parse_when(message.text, user.timezone)
    except ParseError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз, например: «15.10 18:30» или «завтра в 9».")
        return
    data = await state.get_data()
    await state.clear()
    event = await repo.update_event(session, user.id, data["event_id"], starts_at=starts_at)
    if event is None:
        await message.answer("Событие не найдено.")
        return
    if repeat:
        event = await repo.set_repeat(session, user.id, event.id, repeat)
    reminders.schedule(event.id, event.remind_at)
    await message.answer(
        "✅ Время изменено\n\n" + card_text(event, user.timezone), reply_markup=event_kb(event.id)
    )


# ---------- Повтор ----------


@router.callback_query(EventCb.filter(F.action == "repeat"))
async def event_repeat(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    await callback.message.edit_text(
        card_text(event, tz) + "\n\nКак часто повторять?", reply_markup=repeat_kb(event.id)
    )
    await callback.answer()


@router.callback_query(RepeatCb.filter())
async def event_repeat_set(
    callback: CallbackQuery, callback_data: RepeatCb, session: AsyncSession, config: Config
) -> None:
    event, tz = await _load(callback, callback_data.event_id, session, config)
    if event is None:
        return
    repeat = None if callback_data.value == "none" else callback_data.value
    event = await repo.set_repeat(session, callback.from_user.id, event.id, repeat)
    await callback.message.edit_text(
        card_text(event, tz) + "\n\n✅ Повтор изменён", reply_markup=event_kb(event.id)
    )
    await callback.answer()
