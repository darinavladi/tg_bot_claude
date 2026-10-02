from aiogram import Router

from bot.handlers import start


def setup_routers() -> Router:
    router = Router()
    router.include_router(start.router)
    return router
