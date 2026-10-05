import os
import sqlite3
from datetime import datetime, timedelta

from bot.backup import backup
from bot.config import load_db_path


def test_backup_copies_and_prunes(tmp_path):
    db = tmp_path / "planner.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (42)")

    old = tmp_path / "backups" / "planner-2000-01-01_0400.db"
    old.parent.mkdir()
    old.write_bytes(b"old")
    stamp = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))

    target = backup(db)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    assert not old.exists()


def test_load_db_path(monkeypatch):
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////app/data/planner.db")
    assert str(load_db_path()) == "/app/data/planner.db"
