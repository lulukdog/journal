"""数据库备份。给 systemd timer 调用。

用 sqlite3 的在线备份 API，不是 cp —— 服务在跑的时候 cp 可能拿到半个事务。
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, db  # noqa: E402

KEEP_DAYS = 30


def backup(db_path: Path, backup_dir: Path, keep_days: int = KEEP_DAYS) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = backup_dir / f"journal-{stamp}.db"

    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    cutoff = time.time() - keep_days * 86400
    for old in backup_dir.glob("journal-*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink()

    return target


def main() -> int:
    settings = config.load_settings(Path(__file__).resolve().parent / ".env")
    db.init_db(db.connect(settings.db_path))
    target = backup(settings.db_path, settings.db_path.parent / "backups")
    print(f"backup done: {target.name} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
