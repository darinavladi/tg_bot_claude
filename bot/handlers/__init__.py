from aiogram import Router

from bot.handlers import categories, done, events, manage, menu, reminders, settings, start


def setup_routers() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(menu.router)  # раньше остальных: кнопка меню прерывает любой шаг
    router.include_router(settings.router)
    router.include_router(manage.router)
    router.include_router(categories.router)
    router.include_router(done.router)
    router.include_router(events.router)
    router.include_router(reminders.router)
    return router
