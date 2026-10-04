import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import load_config
from bot.db import init_db, make_engine, make_sessionmaker
from bot.handlers import setup_routers
from bot.middlewares import DbSessionMiddleware

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
    engine = make_engine(config.database_url)
    await init_db(engine)

    bot = Bot(token=config.bot_token)
    dp = Dispatcher(config=config)
    dp.update.middleware(DbSessionMiddleware(make_sessionmaker(engine)))
    dp.include_router(setup_routers())

    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
