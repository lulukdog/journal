"""收口任务的命令行入口。给 systemd timer 调用。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import dayclose, db, config  # noqa: E402


async def main() -> int:
    settings = config.load_settings(ROOT / ".env")
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    day = sys.argv[1] if len(sys.argv) > 1 else None
    summary = await dayclose.run(settings, conn, day)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
