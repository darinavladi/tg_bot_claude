"""Главное меню: кнопки под полем ввода и сообщение «Главное меню» с кнопками внутри.

Обе вызывают те же действия, что и команды.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.handlers import categories, done, events, manage, settings, start
from bot.keyboards import MENU, MENU_TEXT, MenuCb, inline_menu, settings_kb

router = Router(name="menu")


@router.message(F.text == MENU["add"])
async def menu_add(message: Message, state: FSMContext) -> None:
    await events.cmd_add(message, state)


@router.message(F.text.in_({MENU["today"], MENU["week"], MENU["list"]}))
async def menu_lists(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    await state.clear()
    handler = {
        MENU["today"]: manage.cmd_today,
        MENU["week"]: manage.cmd_week,
        MENU["list"]: manage.cmd_list,
    }[message.text]
    await handler(message, session, config)


@router.message(F.text == MENU["done"])
async def menu_done(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    await state.clear()
    await done.cmd_done(message, session, config)


@router.message(F.text == MENU["categories"])
async def menu_categories(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    await state.clear()
    await categories.cmd_categories(message, session, config)


@router.message(F.text == MENU["settings"])
async def menu_settings(
    message: Message, state: FSMContext, session: AsyncSession, config: Config
) -> None:
    await settings.cmd_settings(message, state, session, config)


@router.message(F.text == MENU["help"])
async def menu_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await start.cmd_help(message)


# ---------- Сообщение с кнопками: /menu ----------


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=inline_menu())


@router.callback_query(MenuCb.filter())
async def menu_button(
    callback: CallbackQuery, callback_data: MenuCb, state: FSMContext, session: AsyncSession,
    config: Config,
) -> None:  # fmt: skip
    """Нажатие в сообщении-меню: ответ приходит новым сообщением, меню остаётся на месте."""
    await state.clear()
    user = await repo.get_or_create_user(session, callback.from_user.id, config.default_tz)
    target = callback.message
    action = callback_data.action
    if action == "add":
        await state.set_state(events.AddEvent.title)
        await target.answer(events.TITLE_PROMPT)
    elif action in ("today", "week", "list"):
        kind = "all" if action == "list" else action
        await manage._show_list(target, kind, 0, user.id, session, config)
    elif action == "done":
        text, markup = await done._due(session, user.id, user.timezone)
        await target.answer(text, reply_markup=markup)
    elif action == "categories":
        text, markup = await categories._overview(session, user.id)
        await target.answer(text, reply_markup=markup)
    elif action == "settings":
        await target.answer(settings.settings_text(user), reply_markup=settings_kb())
    else:
        await target.answer(start.HELP_TEXT)
    await callback.answer()
