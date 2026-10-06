"""Главное меню: кнопки под полем ввода вызывают те же действия, что и команды."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Config
from bot.handlers import categories, done, events, manage, settings, start
from bot.keyboards import MENU

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
