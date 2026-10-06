"""Резервная копия базы: python -m bot.backup

Делает копию planner.db в папку backups рядом с базой и удаляет копии старше KEEP_DAYS дней.
Копирование идёт через sqlite3 backup API, поэтому его можно запускать, пока бот работает.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bot.config import load_db_path

KEEP_DAYS = 14


def backup(db_path: Path, keep_days: int = KEEP_DAYS, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    backups = db_path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"{db_path.stem}-{now:%Y-%m-%d_%H%M}.db"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    border = now - timedelta(days=keep_days)
    for old in backups.glob(f"{db_path.stem}-*.db"):
        if datetime.fromtimestamp(old.stat().st_mtime) < border:
            old.unlink()
    return target


if __name__ == "__main__":
    path = load_db_path()
    if not path.exists():
        sys.exit(f"База не найдена: {path}")
    print(f"Сохранено: {backup(path)}")
