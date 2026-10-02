import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import load_config
from bot.handlers import setup_routers

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="help", description="Помощь"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp.include_router(setup_routers())

    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
