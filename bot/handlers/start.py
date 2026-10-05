from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import repo
from bot.config import Config
from bot.handlers.settings import tz_label

router = Router(name="start")

HELP_TEXT = (
    "Я бот-планировщик. Пришлите мне событие с датой и временем, "
    "и я напомню о нём.\n\n"
    "Например:\n"
    "• 15.10 18:30 Встреча с Аней\n"
    "• завтра в 9 стоматолог\n"
    "• в пятницу в 7 вечера кино\n"
    "• каждый понедельник в 10 планёрка\n\n"
    "Команды:\n"
    "/add — добавить событие по шагам\n"
    "/today — события на сегодня\n"
    "/week — на 7 дней\n"
    "/list — все запланированные\n"
    "/settings — часовой пояс, утренняя сводка, напоминания\n"
    "/cancel — отменить ввод\n"
    "/help — помощь"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, config: Config) -> None:
    user = await repo.get_or_create_user(session, message.from_user.id, config.default_tz)
    name = message.from_user.first_name
    await message.answer(
        f"Привет, {name}! 👋\n\n{HELP_TEXT}\n\n"
        f"🌍 Время показываю по поясу {tz_label(user.timezone)}. Изменить: /settings"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
