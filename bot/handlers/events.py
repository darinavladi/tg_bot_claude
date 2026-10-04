from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.formatting import format_dt, format_remind
from bot.keyboards import ConfirmCb, RemindCb, confirm_kb, remind_kb
from bot.parser import ParseError, parse_datetime, parse_event

router = Router(name="events")

DEFAULT_REMIND_MIN = 15
EXAMPLES = (
    "Примеры:\n"
    "• 15.10 18:30 Встреча с Аней\n"
    "• завтра в 9 стоматолог\n"
    "• в пятницу в 7 вечера кино\n"
    "• через 2 часа позвонить маме"
)


class AddEvent(StatesGroup):
    title = State()
    when = State()
    remind = State()
    confirm = State()


async def _user_tz(session: AsyncSession, message: Message, config: Config) -> str:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    return user.timezone


def _summary(title: str, starts_at: datetime, remind: int, tz: str) -> str:
    return f"📅 {title}\n🕒 {format_dt(starts_at, tz)}\n⏰ Напомню {format_remind(remind)}"


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
    title = message.text.strip()
    if not title or title.startswith("/"):
        await message.answer("Напишите название текстом, например: «Встреча с Аней».")
        return
    await state.update_data(title=title[:500])
    await state.set_state(AddEvent.when)
    await message.answer("Когда? Например: «15.10 18:30» или «завтра в 9».")


@router.message(AddEvent.when, F.text)
async def add_when(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    tz = await _user_tz(session, message, config)
    try:
        starts_at = parse_datetime(message.text, tz)
    except ParseError as e:
        await message.answer(f"{e}\nПопробуйте ещё раз, например: «15.10 18:30» или «завтра в 9».")
        return
    await state.update_data(starts_at=starts_at.isoformat())
    await state.set_state(AddEvent.remind)
    await message.answer(
        f"🕒 {format_dt(starts_at, tz)}\nКогда напомнить?", reply_markup=remind_kb()
    )


@router.callback_query(AddEvent.remind, RemindCb.filter())
async def add_remind(
    callback: CallbackQuery,
    callback_data: RemindCb,
    state: FSMContext,
    session: AsyncSession,
    config: Config,
) -> None:
    data = await state.get_data()
    await state.clear()
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    starts_at = datetime.fromisoformat(data["starts_at"])
    await repo.add_event(session, user.id, data["title"], starts_at, callback_data.minutes)
    await callback.message.edit_text(
        "✅ Сохранено\n\n"
        + _summary(data["title"], starts_at, callback_data.minutes, user.timezone)
    )
    await callback.answer()


# ---------- Быстрое добавление: просто текстом ----------


@router.message(StateFilter(None, AddEvent.confirm), F.text, ~F.text.startswith("/"))
async def quick_add(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    tz = await _user_tz(session, message, config)
    try:
        parsed = parse_event(message.text, tz)
    except ParseError as e:
        await message.answer(f"{e}\n\n{EXAMPLES}")
        return
    await state.set_state(AddEvent.confirm)
    await state.update_data(
        title=parsed.title[:500],
        starts_at=parsed.starts_at.isoformat(),
        remind=DEFAULT_REMIND_MIN,
    )
    await message.answer(
        _summary(parsed.title, parsed.starts_at, DEFAULT_REMIND_MIN, tz) + "\n\nСохранить?",
        reply_markup=confirm_kb(),
    )


@router.callback_query(AddEvent.confirm, ConfirmCb.filter(F.action == "save"))
async def confirm_save(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    data = await state.get_data()
    await state.clear()
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    starts_at = datetime.fromisoformat(data["starts_at"])
    await repo.add_event(session, user.id, data["title"], starts_at, data["remind"])
    await callback.message.edit_text(
        "✅ Сохранено\n\n" + _summary(data["title"], starts_at, data["remind"], user.timezone)
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


# ---------- Простой список (полноценное управление — на этапе 6) ----------


@router.message(Command("list"))
async def cmd_list(message: Message, session: AsyncSession, config: Config) -> None:
    tz = await _user_tz(session, message, config)
    now = datetime.now().astimezone()
    events = await repo.list_events(
        session, message.from_user.id, start=now, end=now + timedelta(days=366)
    )
    if not events:
        await message.answer("Запланированных событий нет.\n\n" + EXAMPLES)
        return
    lines = [f"• {format_dt(e.starts_at, tz)} — {e.title}" for e in events[:30]]
    more = f"\n…и ещё {len(events) - 30}" if len(events) > 30 else ""
    await message.answer("Ближайшие события:\n\n" + "\n".join(lines) + more)
