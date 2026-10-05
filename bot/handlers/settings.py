"""/settings: часовой пояс, утренняя сводка, напоминание по умолчанию."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.db import User
from bot.formatting import format_remind
from bot.keyboards import (
    TIMEZONES,
    SettingsCb,
    default_remind_kb,
    settings_kb,
    summary_kb,
    timezone_kb,
)
from bot.reminders import Reminders
from bot.timeutils import is_valid_tz, parse_hhmm

router = Router(name="settings")


class SettingsInput(StatesGroup):
    timezone = State()
    summary_time = State()


def tz_label(tz: str) -> str:
    return next((label for label, name in TIMEZONES if name == tz), tz)


def settings_text(user: User) -> str:
    summary = f"в {user.summary_time}" if user.summary_time else "выключена"
    return (
        "⚙️ Настройки\n\n"
        f"🌍 Часовой пояс: {tz_label(user.timezone)}\n"
        f"☀️ Утренняя сводка: {summary}\n"
        f"⏰ Напоминание по умолчанию: {format_remind(user.default_remind_min)}"
    )


async def _user(session: AsyncSession, user_id: int, config: Config) -> User:
    return await repo.get_or_create_user(session, user_id, config.default_tz)


@router.message(Command("settings"))
async def cmd_settings(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    await state.clear()
    user = await _user(session, message.from_user.id, config)
    await message.answer(settings_text(user), reply_markup=settings_kb())


@router.callback_query(SettingsCb.filter(F.action == "menu"))
async def settings_menu(
    callback: CallbackQuery, callback_data: SettingsCb, session: AsyncSession, config: Config
) -> None:
    user = await _user(session, callback.from_user.id, config)
    if callback_data.value == "tz":
        text = f"Сейчас: {tz_label(user.timezone)}. Выберите часовой пояс:"
        markup = timezone_kb()
    elif callback_data.value == "summary":
        text = (
            "Каждое утро пришлю список дел на день. Во сколько?\n"
            f"Сейчас: {user.summary_time or 'выключена'}."
        )
        markup = summary_kb()
    else:
        text = (
            "За сколько напоминать о новых событиях?\n"
            f"Сейчас: {format_remind(user.default_remind_min)}."
        )
        markup = default_remind_kb()
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


async def _save(
    target: Message,
    user_id: int,
    session: AsyncSession,
    reminders: Reminders,
    edit: bool,
    **fields,
) -> None:
    user = await repo.update_user(session, user_id, **fields)
    if "timezone" in fields or "summary_time" in fields:
        reminders.schedule_summary(user.id, user.summary_time, user.timezone)
    text = "✅ Сохранено\n\n" + settings_text(user)
    if edit:
        await target.edit_text(text, reply_markup=settings_kb())
    else:
        await target.answer(text, reply_markup=settings_kb())


# ---------- Часовой пояс ----------


@router.callback_query(SettingsCb.filter(F.action == "tz"))
async def set_timezone(
    callback: CallbackQuery,
    callback_data: SettingsCb,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    if not is_valid_tz(callback_data.value):
        await callback.answer("Неизвестный часовой пояс.", show_alert=True)
        return
    await _user(session, callback.from_user.id, config)
    await _save(
        callback.message, callback.from_user.id, session, reminders, True,
        timezone=callback_data.value,
    )  # fmt: skip
    await callback.answer()


@router.callback_query(SettingsCb.filter(F.action == "tz_other"))
async def ask_timezone(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsInput.timezone)
    await callback.message.answer(
        "Напишите часовой пояс в формате «Регион/Город», например Europe/Berlin "
        "или Asia/Almaty. (отменить: /cancel)"
    )
    await callback.answer()


@router.message(SettingsInput.timezone, F.text, ~F.text.startswith("/"))
async def input_timezone(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    tz = message.text.strip()
    if not is_valid_tz(tz):
        await message.answer("Не знаю такого пояса. Пример: Europe/Berlin. Попробуйте ещё раз.")
        return
    await state.clear()
    await _user(session, message.from_user.id, config)
    await _save(message, message.from_user.id, session, reminders, False, timezone=tz)


# ---------- Утренняя сводка ----------


@router.callback_query(SettingsCb.filter(F.action == "summary"))
async def set_summary(
    callback: CallbackQuery,
    callback_data: SettingsCb,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    value = None if callback_data.value == "off" else parse_hhmm(callback_data.value)
    await _user(session, callback.from_user.id, config)
    await _save(
        callback.message, callback.from_user.id, session, reminders, True, summary_time=value
    )
    await callback.answer()


@router.callback_query(SettingsCb.filter(F.action == "summary_custom"))
async def ask_summary_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsInput.summary_time)
    await callback.message.answer("Во сколько присылать сводку? Например: 7:30 (отменить: /cancel)")
    await callback.answer()


@router.message(SettingsInput.summary_time, F.text, ~F.text.startswith("/"))
async def input_summary_time(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    value = parse_hhmm(message.text)
    if value is None:
        await message.answer("Не понял время. Напишите, например: 7:30")
        return
    await state.clear()
    await _user(session, message.from_user.id, config)
    await _save(message, message.from_user.id, session, reminders, False, summary_time=value)


# ---------- Напоминание по умолчанию ----------


@router.callback_query(SettingsCb.filter(F.action == "remind"))
async def set_default_remind(
    callback: CallbackQuery,
    callback_data: SettingsCb,
    session: AsyncSession,
    reminders: Reminders,
    config: Config,
) -> None:
    await _user(session, callback.from_user.id, config)
    await _save(
        callback.message, callback.from_user.id, session, reminders, True,
        default_remind_min=int(callback_data.value),
    )  # fmt: skip
    await callback.answer()
