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

from bot import categories, repo
from bot.categories import NONE_LABEL
from bot.config import Config
from bot.db import Event, EventStatus
from bot.formatting import format_day, format_hours, format_remind, format_repeat, format_span
from bot.keyboards import (
    CategoryCb,
    EditRemindCb,
    EventCb,
    ListCb,
    RepeatCb,
    category_kb,
    delete_confirm_kb,
    edit_remind_kb,
    event_kb,
    list_filter_kb,
    list_kb,
    repeat_kb,
)
from bot.parser import ParseError, parse_period, parse_when
from bot.reminders import Reminders
from bot.timeutils import next_occurrence, period_range

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
    repeat = State()  # свой период текстом
    category = State()  # название новой категории


def render_list(
    items: list[tuple[datetime, Event]], kind: str, page: int, tz: str, now: datetime,
    names: dict[int, str] | None = None, cat_label: str = "",
) -> tuple[str, list[tuple[str, int]], int]:  # fmt: skip
    """Текст страницы (с группировкой по дням), кнопки событий и число страниц.

    items — пары (время, событие): повторяющееся событие может встречаться несколько раз.
    """
    pages = max(1, ceil(len(items) / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    header = TITLES[kind]
    if cat_label:
        header += f" · {cat_label}"
    if pages > 1:
        header += f" (стр. {page + 1} из {pages})"
    lines = [f"📋 {header}"]
    buttons = []
    current_day = None
    zone = ZoneInfo(tz)
    for n, (when, event) in enumerate(chunk, start=page * PAGE_SIZE + 1):
        local = when.astimezone(zone)
        if local.date() != current_day:
            current_day = local.date()
            lines.append(f"\n{format_day(when, tz, now)}")
        ends_at = getattr(event, "ends_at", None)
        if ends_at is not None:
            ends_at = when + (ends_at - event.starts_at)
        mark = " ✓" if (ends_at or when) < now else ""
        if getattr(event, "repeat", None):
            mark += " 🔁"
        category = (names or {}).get(getattr(event, "category_id", None))
        title = categories.with_category(event.title, category)
        hours = format_hours(when, ends_at, tz)
        lines.append(f"{n}. {hours} {title}{mark}")
        buttons.append((f"{n}. {hours} {title}"[:60], event.id))
    lines.append("\nНажмите на событие, чтобы изменить или удалить его.")
    return "\n".join(lines), buttons, pages


async def _show_list(
    target: Message, kind: str, page: int, user_id: int, session: AsyncSession, config: Config,
    edit: bool = False, cat: int = 0, pick: bool = False,
) -> None:  # fmt: skip
    """Список событий; cat — фильтр (0 — все, -1 — без категории), pick — выбор фильтра."""
    user = await repo.get_or_create_user(session, user_id, config.default_tz)
    names = await repo.category_names(session, user_id)
    now = datetime.now(UTC)
    start, end = period_range(kind, user.timezone, now)
    if kind == "all":
        # В общем списке повторяющееся событие показываем один раз, ближайшим повторением
        events = await repo.list_events(session, user_id, start=start, end=end)
        items = [(event.starts_at, event) for event in events]
    else:
        items = await repo.list_occurrences(session, user_id, start, end, user.timezone)

    present = {event.category_id if event.category_id in names else -1 for _, event in items}
    options = [(i, name) for i, name in names.items() if i in present]
    if -1 in present:
        options.append((-1, NONE_LABEL))
    labels = dict(options)
    if cat not in labels:
        cat = 0  # в этой категории больше ничего нет — показываем всё
    if pick:
        await target.edit_text(
            "Какие события показать?", reply_markup=list_filter_kb(kind, options, cat)
        )
        return
    if cat:
        items = [
            (when, event) for when, event in items
            if (event.category_id if event.category_id in names else -1) == cat
        ]  # fmt: skip
    if not items:
        text, markup = EMPTY[kind], None
    else:
        label = labels.get(cat, "") if cat else ""
        text, buttons, pages = render_list(items, kind, page, user.timezone, now, names, label)
        page = min(page, pages - 1)
        markup = list_kb(buttons, kind, page, pages, len(options) > 1, cat, label or "Все")
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
        session, config, edit=True, cat=callback_data.cat, pick=bool(callback_data.pick),
    )  # fmt: skip
    await callback.answer()


# ---------- Карточка события ----------


def card_text(event: Event, tz: str, names: dict[int, str] | None = None) -> str:
    text = f"📅 {event.title}\n🕒 {format_span(event.starts_at, event.ends_at, tz)}\n"
    if event.repeat:
        text += f"🔁 {format_repeat(event.repeat, event.starts_at, tz).capitalize()}\n"
    text += f"⏰ Напоминание {format_remind(event.remind_before_min)}"
    category = (names or {}).get(event.category_id)
    text += f"\n🏷 {category or NONE_LABEL}"
    if event.status == EventStatus.DONE:
        text += "\n✅ Выполнено"
    return text


class Card:
    """Событие, открытое по кнопке, вместе с поясом и категориями пользователя."""

    def __init__(self, event: Event, tz: str, names: dict[int, str]) -> None:
        self.event, self.tz, self.names = event, tz, names

    def text(self, note: str = "") -> str:
        body = card_text(self.event, self.tz, self.names)
        return f"{body}\n\n{note}" if note else body


async def _load(
    callback: CallbackQuery, event_id: int, session: AsyncSession, config: Config
) -> Card | None:
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    event = await repo.get_event(session, user.id, event_id)
    if event is None or event.status == EventStatus.CANCELLED:
        await callback.answer("Событие не найдено, возможно, оно удалено.", show_alert=True)
        return None
    return Card(event, user.timezone, await repo.category_names(session, user.id))


@router.callback_query(EventCb.filter(F.action == "show"))
async def event_show(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.answer(card.text(), reply_markup=event_kb(card.event.id))
    await callback.answer()


async def _answer_card(message: Message, session: AsyncSession, event: Event, tz: str, note: str):
    """Карточка события новым сообщением (после ввода текста)."""
    names = await repo.category_names(session, event.user_id)
    await message.answer(
        f"{note}\n\n{card_text(event, tz, names)}", reply_markup=event_kb(event.id)
    )


# ---------- Удаление ----------


@router.callback_query(EventCb.filter(F.action == "delete"))
async def event_delete(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.edit_text(
        card.text("Удалить это событие?"), reply_markup=delete_confirm_kb(card.event.id)
    )
    await callback.answer()


@router.callback_query(EventCb.filter(F.action == "delete_yes"))
async def event_delete_yes(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    title = card.event.title
    await repo.delete_event(session, callback.from_user.id, card.event.id)
    reminders.cancel(card.event.id)
    await callback.message.edit_text(f"🗑 Удалено: {title}")
    await callback.answer()


@router.callback_query(EventCb.filter(F.action == "delete_no"))
async def event_delete_no(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.edit_text(card.text(), reply_markup=event_kb(card.event.id))
    await callback.answer()


# ---------- Изменение напоминания ----------


@router.callback_query(EventCb.filter(F.action == "remind"))
async def event_remind(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.edit_text(
        card.text("Когда напомнить?"), reply_markup=edit_remind_kb(card.event.id)
    )
    await callback.answer()


@router.callback_query(EditRemindCb.filter())
async def event_remind_set(
    callback: CallbackQuery, callback_data: EditRemindCb, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    card.event = await repo.update_event(
        session, callback.from_user.id, card.event.id, remind_before_min=callback_data.minutes
    )
    reminders.schedule(card.event.id, card.event.remind_at)
    await callback.message.edit_text(
        card.text("✅ Напоминание изменено"), reply_markup=event_kb(card.event.id)
    )
    await callback.answer()


# ---------- Изменение названия и времени ----------


@router.callback_query(EventCb.filter(F.action.in_({"title", "time"})))
async def event_edit_start(
    callback: CallbackQuery, callback_data: EventCb, state: FSMContext, session: AsyncSession,
    config: Config,
) -> None:  # fmt: skip
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await state.clear()
    await state.update_data(event_id=card.event.id)
    if callback_data.action == "title":
        await state.set_state(EditEvent.title)
        await callback.message.answer(
            f"Новое название для «{card.event.title}»? (отменить: /cancel)"
        )
    else:
        await state.set_state(EditEvent.time)
        await callback.message.answer(
            "Новые дата и время? Например: «15.10 18:30», «завтра в 9» "
            "или промежуток «завтра 14:20–19:30». (отменить: /cancel)"
        )
    await callback.answer()


@router.message(EditEvent.title, F.text, ~F.text.startswith("/"))
async def event_edit_title(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    title = " ".join(message.text.split())[:500]
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
    await _answer_card(message, session, event, user.timezone, "✅ Название изменено")


@router.message(EditEvent.time, F.text, ~F.text.startswith("/"))
async def event_edit_time(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        parsed = parse_when(message.text, user.timezone)
    except ParseError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз, например: «15.10 18:30» или «завтра в 9».")
        return
    data = await state.get_data()
    await state.clear()
    event = await repo.update_event(
        session, user.id, data["event_id"], starts_at=parsed.starts_at, ends_at=parsed.ends_at
    )
    if event is None:
        await message.answer("Событие не найдено.")
        return
    if parsed.repeat:
        event = await repo.set_repeat(session, user.id, event.id, parsed.repeat)
    reminders.schedule(event.id, event.remind_at)
    await _answer_card(message, session, event, user.timezone, "✅ Время изменено")


# ---------- Повтор ----------


@router.callback_query(EventCb.filter(F.action == "repeat"))
async def event_repeat(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.edit_text(
        card.text("Как часто повторять?"), reply_markup=repeat_kb(card.event.id)
    )
    await callback.answer()


async def _apply_repeat(
    session: AsyncSession, event: Event, repeat: str | None, tz: str, reminders: Reminders
) -> Event:
    event = await repo.set_repeat(session, event.user_id, event.id, repeat)
    if repeat == "wd" and event.starts_at.astimezone(ZoneInfo(tz)).weekday() >= 5:
        # «по будням», а событие в выходной — переносим на понедельник
        starts_at = next_occurrence(event.starts_at, "wd", tz, event.starts_at)
        ends_at = event.ends_at and starts_at + (event.ends_at - event.starts_at)
        event = await repo.update_event(
            session, event.user_id, event.id, starts_at=starts_at, ends_at=ends_at
        )
        reminders.schedule(event.id, event.remind_at)
    return event


@router.callback_query(RepeatCb.filter())
async def event_repeat_set(
    callback: CallbackQuery, callback_data: RepeatCb, state: FSMContext, session: AsyncSession,
    config: Config, reminders: Reminders,
) -> None:  # fmt: skip
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    if callback_data.value == "custom":
        await state.clear()
        await state.update_data(event_id=card.event.id)
        await state.set_state(EditEvent.repeat)
        await callback.message.answer(
            "Как часто повторять? Например: «3 дня», «2 недели», «раз в месяц». (отменить: /cancel)"
        )
        await callback.answer()
        return
    repeat = None if callback_data.value == "none" else callback_data.value
    card.event = await _apply_repeat(session, card.event, repeat, card.tz, reminders)
    await callback.message.edit_text(
        card.text("✅ Повтор изменён"), reply_markup=event_kb(card.event.id)
    )
    await callback.answer()


@router.message(EditEvent.repeat, F.text, ~F.text.startswith("/"))
async def event_repeat_text(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        repeat = parse_period(message.text)
    except ParseError as e:
        await message.answer(f"{e}\n(отменить: /cancel)")
        return
    data = await state.get_data()
    await state.clear()
    event = await repo.get_event(session, user.id, data["event_id"])
    if event is None:
        await message.answer("Событие не найдено.")
        return
    event = await _apply_repeat(session, event, repeat, user.timezone, reminders)
    await _answer_card(message, session, event, user.timezone, "✅ Повтор изменён")


# ---------- Категория ----------


@router.callback_query(EventCb.filter(F.action == "category"))
async def event_category(
    callback: CallbackQuery, callback_data: EventCb, session: AsyncSession, config: Config
) -> None:
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    await callback.message.edit_text(
        card.text("Выберите категорию:"),
        reply_markup=category_kb(card.event.id, list(card.names.items())),
    )
    await callback.answer()


@router.callback_query(CategoryCb.filter())
async def event_category_set(
    callback: CallbackQuery, callback_data: CategoryCb, state: FSMContext,
    session: AsyncSession, config: Config,
) -> None:  # fmt: skip
    card = await _load(callback, callback_data.event_id, session, config)
    if card is None:
        return
    if callback_data.value == -1:
        await state.clear()
        await state.update_data(event_id=card.event.id)
        await state.set_state(EditEvent.category)
        await callback.message.answer(
            "Как назвать новую категорию? Можно начать с эмодзи, например: «🐶 Собака». "
            "(отменить: /cancel)"
        )
        await callback.answer()
        return
    category_id = callback_data.value if callback_data.value in card.names else None
    card.event = await repo.set_category(session, callback.from_user.id, card.event.id, category_id)
    await callback.message.edit_text(
        card.text("✅ Категория изменена"), reply_markup=event_kb(card.event.id)
    )
    await callback.answer()


@router.message(EditEvent.category, F.text, ~F.text.startswith("/"))
async def event_category_text(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    name = categories.clean_name(message.text)
    if not name:
        await message.answer("Напишите название категории текстом.")
        return
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    data = await state.get_data()
    await state.clear()
    category = await repo.add_category(session, user.id, name)
    event = await repo.set_category(session, user.id, data["event_id"], category.id)
    if event is None:
        await message.answer("Событие не найдено.")
        return
    await _answer_card(message, session, event, user.timezone, "✅ Категория изменена")
