"""SQLite 连接与建表。

单文件 SQLite。不用 Postgres —— 这台机器只有 1.7 GB 内存。
检索用 LIKE 而不是 FTS5：FTS5 的 trigram 分词器最少要 3 个字符才匹配，
而日记里大量是「累」「烦」这种一两个字，会搜不到。数据量只有几千条，
LIKE 全表扫是毫秒级。见 ledger 里的 Ruling。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('text','image')),
    content     TEXT,
    image_path  TEXT,
    processed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_day ON entries(day, ts);
CREATE INDEX IF NOT EXISTS idx_entries_unprocessed ON entries(processed);

CREATE TABLE IF NOT EXISTS days (
    day         TEXT PRIMARY KEY,
    entry_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    mood        INTEGER,
    mood_label  TEXT,
    headline    TEXT,
    keywords    TEXT,
    companion   TEXT,
    finalized   INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type TEXT    NOT NULL,
    period_key  TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    metrics     TEXT,
    created_at  TEXT    NOT NULL,
    UNIQUE(period_type, period_key)
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    category    TEXT,
    confidence  REAL    NOT NULL DEFAULT 1.0,
    first_seen  TEXT    NOT NULL,
    last_seen   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS utterances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    trigger     TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    seen_at     TEXT,
    reaction    TEXT
);
CREATE INDEX IF NOT EXISTS idx_utterances_day ON utterances(day, ts);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    signal_type TEXT    NOT NULL,
    payload     TEXT,
    resolved    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(day, signal_type)
);
"""

TABLES = ("entries", "days", "summaries", "memories", "utterances", "events")


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """已有库补列。CREATE TABLE IF NOT EXISTS 不会给旧表加字段。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(days)")}
    if cols and "companion" not in cols:
        conn.execute("ALTER TABLE days ADD COLUMN companion TEXT")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}
