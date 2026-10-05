#!/bin/sh
# Обновить бота на сервере до последней версии из main и перезапустить.
set -e
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker compose ps
