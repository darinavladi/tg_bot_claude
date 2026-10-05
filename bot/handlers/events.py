from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import categories, repo
from bot.config import Config
from bot.formatting import format_dt, format_remind, format_repeat
from bot.keyboards import ConfirmCb, RemindCb, confirm_kb, remind_kb
from bot.parser import ParseError, parse_event, parse_when
from bot.reminders import Reminders

router = Router(name="events")

EXAMPLES = (
    "Примеры:\n"
    "• 15.10 18:30 Встреча с Аней\n"
    "• завтра в 9 стоматолог\n"
    "• в пятницу в 7 вечера кино\n"
    "• через 2 часа позвонить маме\n"
    "• каждый понедельник в 10 планёрка"
)


class AddEvent(StatesGroup):
    title = State()
    when = State()
    remind = State()
    confirm = State()


async def _user_tz(session: AsyncSession, message: Message, config: Config) -> str:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    return user.timezone


def _summary(
    title: str, starts_at: datetime, remind: int, tz: str, repeat: str | None = None,
    category: str | None = None,
) -> str:  # fmt: skip
    text = f"📅 {title}\n🕒 {format_dt(starts_at, tz)}\n"
    if repeat:
        text += f"🔁 {format_repeat(repeat, starts_at, tz).capitalize()}\n"
    text += f"⏰ Напомню {format_remind(remind)}"
    if category:
        text += f"\n🏷 {categories.label(category)}"
    return text


# ---------- Отмена из любого шага ----------


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменено.")


# ---------- Пошаговое добавление: /add ----------


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddEvent.title)
    await message.answer("Как назвать событие? (отменить: /cancel)")


@router.message(AddEvent.title, F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    category, title = categories.detect(message.text.strip())
    title = " ".join(title.split())
    if not title or title.startswith("/"):
        await message.answer("Напишите название текстом, например: «Встреча с Аней».")
        return
    await state.update_data(title=title[:500], category=category)
    await state.set_state(AddEvent.when)
    await message.answer("Когда? Например: «15.10 18:30» или «завтра в 9».")


@router.message(AddEvent.when, F.text)
async def add_when(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    tz = await _user_tz(session, message, config)
    try:
        starts_at, repeat = parse_when(message.text, tz)
    except ParseError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз, например: «15.10 18:30» или «завтра в 9».")
        return
    await state.update_data(starts_at=starts_at.isoformat(), repeat=repeat)
    await state.set_state(AddEvent.remind)
    when = f"🕒 {format_dt(starts_at, tz)}"
    if repeat:
        when += f"\n🔁 {format_repeat(repeat, starts_at, tz).capitalize()}"
    await message.answer(f"{when}\nКогда напомнить?", reply_markup=remind_kb())


@router.callback_query(AddEvent.remind, RemindCb.filter())
async def add_remind(
    callback: CallbackQuery,
    callback_data: RemindCb,
    state: FSMContext,
    session: AsyncSession,
    config: Config,
    reminders: Reminders,
) -> None:
    data = await state.get_data()
    await state.clear()
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    starts_at = datetime.fromisoformat(data["starts_at"])
    repeat, category = data.get("repeat"), data.get("category")
    event = await repo.add_event(
        session, user.id, data["title"], starts_at, callback_data.minutes, repeat, category
    )
    reminders.schedule(event.id, event.remind_at)
    await callback.message.edit_text(
        "✅ Сохранено\n\n"
        + _summary(data["title"], starts_at, callback_data.minutes, user.timezone, repeat, category)
    )
    await callback.answer()


# ---------- Быстрое добавление: просто текстом ----------


@router.message(StateFilter(None, AddEvent.confirm), F.text, ~F.text.startswith("/"))
async def quick_add(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    tz, remind = user.timezone, user.default_remind_min
    try:
        parsed = parse_event(message.text, tz)
    except ParseError as e:
        await message.answer(f"{e}\n\n{EXAMPLES}")
        return
    await state.set_state(AddEvent.confirm)
    await state.update_data(
        title=parsed.title[:500],
        starts_at=parsed.starts_at.isoformat(),
        remind=remind,
        repeat=parsed.repeat,
        category=parsed.category,
    )
    await message.answer(
        _summary(parsed.title, parsed.starts_at, remind, tz, parsed.repeat, parsed.category)
        + "\n\nСохранить?",
        reply_markup=confirm_kb(),
    )


@router.callback_query(AddEvent.confirm, ConfirmCb.filter(F.action == "save"))
async def confirm_save(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    config: Config,
    reminders: Reminders,
) -> None:
    data = await state.get_data()
    await state.clear()
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    starts_at = datetime.fromisoformat(data["starts_at"])
    repeat, category = data.get("repeat"), data.get("category")
    event = await repo.add_event(
        session, user.id, data["title"], starts_at, data["remind"], repeat, category
    )
    reminders.schedule(event.id, event.remind_at)
    await callback.message.edit_text(
        "✅ Сохранено\n\n"
        + _summary(data["title"], starts_at, data["remind"], user.timezone, repeat, category)
    )
    await callback.answer()


@router.callback_query(AddEvent.confirm, ConfirmCb.filter(F.action == "cancel"))
async def confirm_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Не сохранено.")
    await callback.answer()


@router.callback_query(ConfirmCb.filter())
@router.callback_query(RemindCb.filter())
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже не действует.", show_alert=True)
