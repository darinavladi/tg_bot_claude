# Telegram-бот-планировщик

Бот, которому вы пишете событие с датой и временем, а он сохраняет его и напоминает.

Сейчас готов скелет: бот отвечает на `/start` и `/help`. Следующие этапы: база данных, добавление событий, напоминания.

## Запуск локально

1. Создайте бота у [@BotFather](https://t.me/BotFather) командой `/newbot` и получите токен.
2. Установите Python 3.11+ и зависимости:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   ```
3. Скопируйте `.env.example` в `.env` и впишите токен в `BOT_TOKEN`.
4. Запустите:
   ```bash
   python -m bot
   ```
5. Напишите боту `/start` в Telegram.

## Проверки

```bash
ruff check .
ruff format --check .
pytest
```
