#!/bin/sh
# Резервная копия базы (запускается из cron раз в сутки). Копии — в data/backups, хранятся 14 дней.
set -e
cd "$(dirname "$0")/.."
docker compose exec -T bot python -m bot.backup
