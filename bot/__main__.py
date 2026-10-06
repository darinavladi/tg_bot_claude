import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import load_config
from bot.db import init_db, make_engine, make_sessionmaker
from bot.handlers import setup_routers
from bot.middlewares import DbSessionMiddleware
from bot.reminders import Reminders

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="add", description="Добавить событие"),
    BotCommand(command="today", description="События на сегодня"),
    BotCommand(command="week", description="События на 7 дней"),
    BotCommand(command="list", description="Все запланированные события"),
    BotCommand(command="done", description="Завершить событие"),
    BotCommand(command="categories", description="Мои категории"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="cancel", description="Отменить ввод"),
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

    sessionmaker = make_sessionmaker(engine)

    bot = Bot(token=config.bot_token)
    reminders = Reminders(bot, sessionmaker, config.default_tz)
    dp = Dispatcher(config=config, reminders=reminders)
    dp.update.middleware(DbSessionMiddleware(sessionmaker))
    dp.include_router(setup_routers())

    try:
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        await reminders.start()
        await dp.start_polling(bot)
    finally:
        reminders.shutdown()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
