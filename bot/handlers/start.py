from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.handlers.settings import tz_label
from bot.keyboards import MENU_TEXT, inline_menu, main_menu

router = Router(name="start")

HELP_TEXT = (
    "Я бот-планировщик: запоминаю события и вовремя напоминаю о них.\n\n"
    "➕ Нажмите «Добавить», и я спрошу по шагам: что за событие, когда, "
    "повторять ли и к какой категории отнести.\n\n"
    "✍️ Или напишите событие одной фразой:\n"
    "• завтра в 9 стоматолог\n"
    "• 15.10 14:20–19:30 работа над проектом\n"
    "• раз в 2 недели в субботу в 12 уборка #дом\n\n"
    "Кнопки внизу экрана:\n"
    "📅 Сегодня и 🗓 Неделя — расписание\n"
    "📋 Все события — всё запланированное; нажмите на событие, чтобы изменить или удалить\n"
    "✅ Завершить — отметить сделанное\n"
    "🏷 Категории — свои категории событий\n"
    "⚙️ Настройки — часовой пояс, утренняя сводка, напоминания\n\n"
    "🏠 Главное меню с кнопками в сообщении — команда /menu.\n"
    "Передумали на середине — отправьте /cancel."
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, config: Config) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    name = message.from_user.first_name
    await message.answer(
        f"Привет, {name}! 👋 {HELP_TEXT}\n\n"
        f"🌍 Время показываю по поясу {tz_label(user.timezone)}. Изменить: ⚙️ Настройки",
        reply_markup=main_menu(),
    )
    await message.answer(MENU_TEXT, reply_markup=inline_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())
