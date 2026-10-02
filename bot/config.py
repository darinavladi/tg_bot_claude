import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    default_tz: str


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.")
    return Config(bot_token=token, default_tz=os.getenv("DEFAULT_TZ", "Europe/Moscow"))
