"""记忆：分层读取，以及组装每次对话的上下文。

L0 原始 entries → L1 日卡片 → L2 周/月 → L3 画像（memories）

关键约定：喂给模型的是「有限条检索结果 + 画像」，永远不是把 entries 全量倒进去。
Review Focus：组装出来的 messages 里 entries 段必须有上限。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import days as days_mod
from . import entries as entries_mod
from . import prompts

ENTRY_LIMIT = 20
CARD_DAYS = 7
UTTERANCE_LIMIT = 5


def search_entries(
    conn: sqlite3.Connection, query: str, limit: int = ENTRY_LIMIT
) -> list[sqlite3.Row]:
    """按关键词找相关记录。

    用 LIKE 不用 FTS5：trigram 分词器最少要 3 个字符，
    而日记里大量是「累」「烦」这种一两个字。见 ledger 的 Ruling。
    """
    terms = [t for t in (query or "").replace("，", " ").split() if t]
    if not terms:
        return []

    clauses = []
    params: list[str] = []
    for term in terms[:5]:
        clauses.append("(content LIKE ? OR image_path LIKE ?)")
        params.extend([f"%{term}%", f"%{term}%"])

    sql = (
        "SELECT * FROM entries WHERE "
        + " OR ".join(clauses)
        + " ORDER BY ts DESC LIMIT ?"
    )
    params.append(str(limit))
    return conn.execute(sql, params).fetchall()


def get_profile(conn: sqlite3.Connection) -> str:
    """送给模型的「关于他是谁」。

    排除 category='onboarding' —— 那是跳题标记之类的内部账，
    不是关于他的事实，不能混进画像里。
    """
    rows = conn.execute(
        "SELECT key, value, category FROM memories "
        "WHERE status = 'active' AND (category IS NULL OR category != 'onboarding') "
        "ORDER BY category, key"
    ).fetchall()
    if not rows:
        return "（还没有关于他的长期事实，因为访谈还没做。）"
    lines = []
    for row in rows:
        label = row["category"] or "other"
        lines.append(f"- [{label}] {row['key']}: {row['value']}")
    return "\n".join(lines)


def upsert_memory(
    conn: sqlite3.Connection,
    key: str,
    value: str,
    category: str | None = None,
    ts: datetime | None = None,
    confidence: float = 1.0,
) -> None:
    """同一 key 覆盖旧值（旧值被标记 superseded，不删）。"""
    now = (ts or datetime.now()).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO memories (key, value, category, confidence, first_seen, last_seen, status)
        VALUES (?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(key) DO UPDATE SET
            value      = excluded.value,
            category   = COALESCE(excluded.category, memories.category),
            confidence = excluded.confidence,
            last_seen  = excluded.last_seen,
            status     = 'active'
        """,
        (key, value, category, confidence, now, now),
    )
    conn.commit()


def cards_text(conn: sqlite3.Connection, day: str, n: int = CARD_DAYS) -> str:
    rows = days_mod.recent_days(conn, day, n)
    if not rows:
        return "（最近没有记录。）"
    lines = []
    for row in rows:
        parts = [f"{row['day']}：{row['entry_count']} 段"]
        if row["mood_label"]:
            parts.append(f"他自己的话「{row['mood_label']}」")
        if row["headline"]:
            parts.append(row["headline"])
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def recent_utterances(
    conn: sqlite3.Connection, day: str, n: int = UTTERANCE_LIMIT
) -> str:
    rows = conn.execute(
        "SELECT ts, trigger, content FROM utterances "
        "WHERE trigger != 'ask' ORDER BY ts DESC LIMIT ?",
        (n,),
    ).fetchall()
    if not rows:
        return "（你还没主动跟他说过话。）"
    return "\n".join(f"- {r['ts'][:10]}: {r['content']}" for r in rows)


def entries_text(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "（没有检索到相关记录。）"
    lines = []
    for row in rows:
        stamp = row["ts"][:16].replace("T", " ")
        body = row["content"] or "（一张图片）"
        suffix = " [图片]" if row["kind"] == "image" else ""
        lines.append(f"- {stamp} {body}{suffix}")
    return "\n".join(lines)


def build_ask_messages(
    conn: sqlite3.Connection,
    question: str,
    day: str,
    *,
    entry_limit: int = ENTRY_LIMIT,
) -> list[dict]:
    """组装「问它」的上下文。entries 段严格限量。"""
    hits = search_entries(conn, question, limit=entry_limit)
    if len(hits) < min(5, entry_limit):
        recent = entries_mod.list_by_day(conn, day)
        seen = {r["id"] for r in hits}
        for row in reversed(recent):
            if row["id"] not in seen:
                hits.append(row)
            if len(hits) >= entry_limit:
                break

    context = (
        "## 关于他的长期事实\n"
        f"{get_profile(conn)}\n\n"
        f"## 最近 {CARD_DAYS} 天的卡片\n"
        f"{cards_text(conn, day)}\n\n"
        "## 检索到的相关记录\n"
        f"{entries_text(hits[:entry_limit])}\n\n"
        "## 你之前主动跟他说过的话（别重复）\n"
        f"{recent_utterances(conn, day)}\n"
    )

    return [
        {"role": "system", "content": prompts.ASK_SYSTEM},
        {"role": "system", "content": context},
        {"role": "user", "content": question},
    ]


def record_utterance(
    conn: sqlite3.Connection,
    day: str,
    trigger: str,
    content: str,
    ts: datetime | None = None,
) -> int:
    now = (ts or datetime.now()).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO utterances (ts, day, trigger, content) VALUES (?, ?, ?, ?)",
        (now, day, trigger, content),
    )
    conn.commit()
    return int(cur.lastrowid)


def utterances_for_day(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM utterances WHERE day = ? ORDER BY ts ASC", (day,)
    ).fetchall()


__all__ = [
    "CARD_DAYS",
    "ENTRY_LIMIT",
    "build_ask_messages",
    "cards_text",
    "entries_text",
    "get_profile",
    "recent_utterances",
    "record_utterance",
    "search_entries",
    "upsert_memory",
    "utterances_for_day",
    "json",
    "timedelta",
]
