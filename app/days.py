"""「一天」的定义，以及每天那张卡片。

一天的边界不是午夜，是凌晨 4:00 —— 半夜说的话应该算前一天。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import db as db_mod

BOUNDARY_HOUR = 4


def day_of(ts: datetime, boundary_hour: int = BOUNDARY_HOUR) -> str:
    """把一个时刻归到它所属的那一天。"""
    if ts.hour < boundary_hour:
        ts = ts - timedelta(days=1)
    return ts.strftime("%Y-%m-%d")


def day_range(day: str, boundary_hour: int = BOUNDARY_HOUR) -> tuple[str, str]:
    """返回这一天的 [起, 止) 两个 ISO 时间戳，左闭右开。"""
    start = datetime.strptime(day, "%Y-%m-%d").replace(hour=boundary_hour)
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def refresh_counts(conn: sqlite3.Connection, day: str) -> sqlite3.Row:
    """重算当天的条目数。幂等 —— 重复调用不会累加。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN kind = 'image' THEN 1 ELSE 0 END), 0) AS images
        FROM entries WHERE day = ?
        """,
        (day,),
    ).fetchone()
    total, images = int(row["total"]), int(row["images"])
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO days (day, entry_count, image_count, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(day) DO UPDATE SET
            entry_count = excluded.entry_count,
            image_count = excluded.image_count,
            updated_at  = excluded.updated_at
        """,
        (day, total, images, now),
    )
    conn.commit()
    return get(conn, day)  # type: ignore[return-value]


def get(conn: sqlite3.Connection, day: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM days WHERE day = ?", (day,)).fetchone()


def ensure(conn: sqlite3.Connection, day: str) -> sqlite3.Row:
    row = get(conn, day)
    if row is None:
        return refresh_counts(conn, day)
    return row


def save_summary(
    conn: sqlite3.Connection,
    day: str,
    *,
    mood: int | None,
    mood_label: str | None,
    headline: str | None,
    keywords: list[str] | None,
    details: dict | None = None,
) -> None:
    """写入卡片摘要。不改动 entry_count / image_count。"""
    now = datetime.now().isoformat(timespec="seconds")
    ensure(conn, day)
    conn.execute(
        """
        UPDATE days
           SET mood = ?, mood_label = ?, headline = ?, keywords = ?, summary = ?,
               finalized = 1, updated_at = ?
         WHERE day = ?
        """,
        (
            mood,
            mood_label,
            headline,
            json.dumps(keywords or [], ensure_ascii=False),
            json.dumps(details, ensure_ascii=False) if details else None,
            now,
            day,
        ),
    )
    conn.commit()


def as_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    raw = out.get("keywords")
    try:
        out["keywords"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        out["keywords"] = []
    try:
        out["summary"] = json.loads(out.get("summary")) if out.get("summary") else None
    except (TypeError, ValueError):
        out["summary"] = None
    return out


def recent_days(conn: sqlite3.Connection, end_day: str, n: int) -> list[sqlite3.Row]:
    """取截至 end_day（含）的最近 n 天卡片，按时间正序返回。"""
    end = datetime.strptime(end_day, "%Y-%m-%d")
    start = (end - timedelta(days=n - 1)).strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT * FROM days WHERE day BETWEEN ? AND ? ORDER BY day ASC",
        (start, end_day),
    ).fetchall()


__all__ = [
    "BOUNDARY_HOUR",
    "as_dict",
    "day_of",
    "day_range",
    "ensure",
    "get",
    "recent_days",
    "refresh_counts",
    "save_summary",
    "db_mod",
]
