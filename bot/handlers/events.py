"""Добавление события: по шагам (/add) или одной фразой, затем вопросы о повторе и категории.

Шаги: что за событие → когда (время или промежуток) → повторять ли → категория → сохранено.
Из фразы «каждый понедельник в 10 планёрка #работа» бот берёт всё, что в ней есть,
и спрашивает только недостающее.
"""

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import categories, repo
from bot.config import Config
from bot.formatting import format_remind, format_repeat, format_span
from bot.keyboards import (
    AddCatCb,
    AddRepeatCb,
    ConfirmCb,
    RemindCb,
    add_category_kb,
    add_repeat_kb,
)
from bot.parser import ParseError, parse_event, parse_period, parse_when
from bot.reminders import Reminders

router = Router(name="events")

EXAMPLES = (
    "Примеры:\n"
    "• 15.10 18:30 Встреча с Аней\n"
    "• завтра 14:20–19:30 работа над проектом\n"
    "• в пятницу в 7 вечера кино\n"
    "• через 2 часа позвонить маме\n"
    "• раз в 2 недели в субботу в 12 уборка #дом"
)
WHEN_HINT = "Например: «15.10 18:30», «завтра в 9» или промежуток «завтра 14:20–19:30»."
PERIOD_HINT = "Напишите период, например: «3 дня», «2 недели», «раз в месяц»."
CATEGORY_NAME_HINT = "Как назвать новую категорию? Можно начать с эмодзи, например: «🐶 Собака»."


class AddEvent(StatesGroup):
    title = State()
    when = State()
    repeat = State()  # ждём кнопку повтора
    repeat_custom = State()  # ждём свой период текстом
    category = State()  # ждём кнопку категории
    category_new = State()  # ждём название новой категории


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def describe(data: dict, tz: str, names: dict[int, str], saved: bool = False) -> str:
    """Карточка будущего события из данных диалога: то, что уже известно."""
    starts_at, ends_at = _dt(data["starts_at"]), _dt(data.get("ends_at"))
    text = f"📅 {data['title']}\n🕒 {format_span(starts_at, ends_at, tz)}"
    if data.get("repeat"):
        text += f"\n🔁 {format_repeat(data['repeat'], starts_at, tz).capitalize()}"
    if saved:
        text += f"\n⏰ Напомню {format_remind(data['remind'])}"
        category_id = data.get("category_id")
        if category_id in names:
            text += f"\n🏷 {names[category_id]}"
    return text


async def _names(session: AsyncSession, user_id: int) -> dict[int, str]:
    return await repo.category_names(session, user_id)


async def _next_step(
    target: Message,
    state: FSMContext,
    session: AsyncSession,
    user_id: int,
    tz: str,
    reminders: Reminders | None,
    edit: bool = False,
) -> None:
    """Задаёт следующий вопрос, а когда всё известно — сохраняет событие."""
    send = target.edit_text if edit else target.answer
    data = await state.get_data()
    names = await _names(session, user_id)
    if "repeat" not in data:
        await state.set_state(AddEvent.repeat)
        await send(
            describe(data, tz, names) + "\n\nПовторять это событие?", reply_markup=add_repeat_kb()
        )
        return
    if "category_id" not in data:
        await state.set_state(AddEvent.category)
        await send(
            describe(data, tz, names) + "\n\nОтнести событие к категории?",
            reply_markup=add_category_kb(list(names.items())),
        )
        return
    await state.clear()
    starts_at, ends_at = _dt(data["starts_at"]), _dt(data.get("ends_at"))
    event = await repo.add_event(
        session, user_id, data["title"], starts_at, data["remind"], data["repeat"],
        data["category_id"] or None, ends_at,
    )  # fmt: skip
    if reminders is not None:
        reminders.schedule(event.id, event.remind_at)
    await send("✅ Сохранено\n\n" + describe(data, tz, names, saved=True))


async def _set_repeat(state: FSMContext, repeat: str | None, tz: str) -> None:
    """Запоминает повтор; «по будням» с началом в выходной сдвигает на понедельник."""
    data = await state.get_data()
    update = {"repeat": repeat}
    starts_at = _dt(data["starts_at"])
    if repeat == "wd" and starts_at.weekday() >= 5:
        shift = timedelta(days=7 - starts_at.weekday())
        update["starts_at"] = (starts_at + shift).isoformat()
        if data.get("ends_at"):
            update["ends_at"] = (_dt(data["ends_at"]) + shift).isoformat()
    await state.update_data(**update)


# ---------- Отмена из любого шага ----------


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменено, событие не сохранено.")


# ---------- Шаг 1–2: что и когда (/add) ----------


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddEvent.title)
    await message.answer("Что за событие? Напишите название. (отменить: /cancel)")


@router.message(AddEvent.title, F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    title = " ".join(message.text.split())
    if not title or title.startswith("/"):
        await message.answer("Напишите название текстом, например: «Встреча с Аней».")
        return
    await state.update_data(title=title[:1].upper() + title[1:500])
    await state.set_state(AddEvent.when)
    await message.answer(f"Когда? Укажите дату и время или промежуток времени.\n{WHEN_HINT}")


@router.message(AddEvent.when, F.text)
async def add_when(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        parsed = parse_when(message.text, user.timezone)
    except ParseError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз. {WHEN_HINT}")
        return
    data = {
        "starts_at": parsed.starts_at.isoformat(),
        "ends_at": parsed.ends_at.isoformat() if parsed.ends_at else None,
        "remind": user.default_remind_min,
    }
    if parsed.repeat:
        data["repeat"] = parsed.repeat
    await state.update_data(**data)
    await _next_step(message, state, session, user.id, user.timezone, reminders)


# ---------- Быстрое добавление одной фразой ----------


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def quick_add(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders | None = None,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        parsed = parse_event(message.text, user.timezone)
    except ParseError as e:
        await message.answer(f"{e}\n\n{EXAMPLES}")
        return
    await state.clear()
    data = {
        "title": parsed.title[:500],
        "starts_at": parsed.starts_at.isoformat(),
        "ends_at": parsed.ends_at.isoformat() if parsed.ends_at else None,
        "remind": user.default_remind_min,
    }
    if parsed.repeat:
        data["repeat"] = parsed.repeat
    if parsed.hashtag:
        found = categories.match_hashtag(parsed.hashtag, await _names(session, user.id))
        if found is not None:
            data["category_id"] = found
    await state.update_data(**data)
    await _next_step(message, state, session, user.id, user.timezone, reminders)


# ---------- Шаг 3: повтор ----------


@router.callback_query(AddEvent.repeat, AddRepeatCb.filter())
async def add_repeat(
    callback: CallbackQuery, callback_data: AddRepeatCb, state: FSMContext,
    session: AsyncSession, config: Config, reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    if callback_data.value == "custom":
        await state.set_state(AddEvent.repeat_custom)
        await callback.message.edit_text(PERIOD_HINT)
        await callback.answer()
        return
    repeat = None if callback_data.value == "none" else callback_data.value
    await _set_repeat(state, repeat, user.timezone)
    await _next_step(callback.message, state, session, user.id, user.timezone, reminders, edit=True)
    await callback.answer()


@router.message(
    StateFilter(AddEvent.repeat_custom, AddEvent.repeat), F.text, ~F.text.startswith("/")
)
async def add_repeat_text(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    """Свой период текстом. На шаге с кнопками это может быть и новое событие."""
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    try:
        repeat = parse_period(message.text)
    except ParseError as e:
        if await state.get_state() == AddEvent.repeat:
            await quick_add(message, state, session, config, reminders)
            return
        await message.answer(f"{e}\n(отменить: /cancel)")
        return
    await _set_repeat(state, repeat, user.timezone)
    await _next_step(message, state, session, user.id, user.timezone, reminders)


# ---------- Шаг 4: категория ----------


@router.callback_query(AddEvent.category, AddCatCb.filter())
async def add_category(
    callback: CallbackQuery, callback_data: AddCatCb, state: FSMContext,
    session: AsyncSession, config: Config, reminders: Reminders,
) -> None:  # fmt: skip
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    if callback_data.value == -1:
        await state.set_state(AddEvent.category_new)
        await callback.message.edit_text(CATEGORY_NAME_HINT)
        await callback.answer()
        return
    category_id = callback_data.value
    if category_id and await repo.get_category(session, user.id, category_id) is None:
        category_id = 0  # категорию успели удалить
    await state.update_data(category_id=category_id)
    await _next_step(callback.message, state, session, user.id, user.timezone, reminders, edit=True)
    await callback.answer()


@router.message(
    StateFilter(AddEvent.category_new, AddEvent.category), F.text, ~F.text.startswith("/")
)
async def add_category_text(
    message: Message, state: FSMContext, session: AsyncSession, config: Config,
    reminders: Reminders,
) -> None:  # fmt: skip
    """Название новой категории. На шаге с кнопками — выбор категории по названию."""
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    name = categories.clean_name(message.text)
    if await state.get_state() == AddEvent.category:
        names = await _names(session, user.id)
        found = next((i for i, n in names.items() if n.lower() == name.lower()), None)
        if found is None and len(name.split()) == 1:
            found = categories.match_hashtag(name, names)
        if found is None:
            await quick_add(message, state, session, config, reminders)
            return
        category_id = found
    else:
        if not name:
            await message.answer(CATEGORY_NAME_HINT)
            return
        category_id = (await repo.add_category(session, user.id, name)).id
    await state.update_data(category_id=category_id)
    await _next_step(message, state, session, user.id, user.timezone, reminders)


# ---------- Кнопки, которые уже не действуют ----------


@router.callback_query(AddRepeatCb.filter())
@router.callback_query(AddCatCb.filter())
@router.callback_query(ConfirmCb.filter())
@router.callback_query(RemindCb.filter())
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже не действует.", show_alert=True)
