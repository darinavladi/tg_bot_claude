import pytest

from bot.config import load_config


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    with pytest.raises(RuntimeError):
        load_config()


def test_reads_env(monkeypatch):
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "abc")
    monkeypatch.setenv("DEFAULT_TZ", "Asia/Yekaterinburg")
    config = load_config()
    assert config.bot_token == "abc"
    assert config.default_tz == "Asia/Yekaterinburg"


def test_routers_import():
    from bot.handlers import setup_routers

    assert setup_routers() is not None
