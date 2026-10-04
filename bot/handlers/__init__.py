from aiogram import Router

from bot.handlers import events, start


def setup_routers() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(events.router)
    return router
