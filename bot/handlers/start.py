from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="start")

HELP_TEXT = (
    "Я бот-планировщик. Пришлите мне событие с датой и временем, "
    "и я напомню о нём.\n\n"
    "Команды:\n"
    "/start — начать\n"
    "/help — помощь\n\n"
    "Добавление событий появится на следующем этапе."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(f"Привет, {name}! 👋\n\n{HELP_TEXT}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
