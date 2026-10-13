"""引导访谈 —— 第一批素材。

从零开始积累是这类产品最容易死的地方：第三天打开发现它什么都不知道，就走了。
所以第一次打开先走这个。一次一题，任何一题都可以跳过。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import memory

SKIP_PREFIX = "onboarding_skipped_"

QUESTIONS: list[dict] = [
    {
        "key": "income_source",
        "memory_key": "income_source",
        "category": "income",
        "text": "先从最实在的开始：你现在靠什么生活？收入大概是什么量级？",
    },
    {
        "key": "monthly_expense",
        "memory_key": "monthly_expense",
        "category": "expense",
        "text": "每个月大概要花多少？手上这些能撑多久？给个大概就行。",
    },
    {
        "key": "family_burden",
        "memory_key": "family_burden",
        "category": "family",
        "text": "家里有谁需要你照顾？有什么固定的负担？",
    },
    {
        "key": "top_concern",
        "memory_key": "top_concern",
        "category": "goal",
        "text": "你现在最在意的一件事是什么？",
    },
    {
        "key": "last_breaking_point",
        "memory_key": "last_breaking_point",
        "category": "other",
        "text": "最近一次觉得「这样不行」，是什么时候？",
    },
    {
        "key": "tone_preference",
        "memory_key": "tone_preference",
        "category": "preference",
        "text": "最后一个：你希望我怎么跟你说话？直接一点，还是温和一点？",
    },
]

QUESTION_BY_KEY = {q["key"]: q for q in QUESTIONS}


def _has_answer(conn: sqlite3.Connection, question: dict) -> bool:
    row = conn.execute(
        "SELECT 1 FROM memories WHERE key IN (?, ?) LIMIT 1",
        (question["memory_key"], SKIP_PREFIX + question["key"]),
    ).fetchone()
    return row is not None


def answered_count(conn: sqlite3.Connection) -> int:
    return sum(1 for q in QUESTIONS if _has_answer(conn, q))


def is_done(conn: sqlite3.Connection) -> bool:
    return answered_count(conn) >= len(QUESTIONS)


def next_question(conn: sqlite3.Connection) -> dict | None:
    if not QUESTIONS:
        return None
    index = answered_count(conn)
    if index >= len(QUESTIONS):
        return None
    question = QUESTIONS[index]
    return {
        "key": question["key"],
        "text": question["text"],
        "index": index,
        "total": len(QUESTIONS),
    }


def record_answer(
    conn: sqlite3.Connection,
    question_key: str,
    answer: str | None,
    skipped: bool = False,
    ts: datetime | None = None,
) -> None:
    question = QUESTION_BY_KEY.get(question_key)
    if question is None:
        return
    if skipped:
        memory.upsert_memory(
            conn,
            SKIP_PREFIX + question_key,
            "1",
            category="onboarding",
            ts=ts,
        )
        return
    text = (answer or "").strip()
    if not text:
        memory.upsert_memory(
            conn, SKIP_PREFIX + question_key, "1", category="onboarding", ts=ts
        )
        return
    memory.upsert_memory(
        conn,
        question["memory_key"],
        text,
        category=question["category"],
        ts=ts,
    )


def status(conn: sqlite3.Connection) -> dict:
    """给 /api/state 用。Review Focus #5：首次使用不能是空白。"""
    done = is_done(conn)
    return {
        "done": done,
        "answered": answered_count(conn),
        "total": len(QUESTIONS),
        "question": None if done else next_question(conn),
    }


__all__ = [
    "QUESTIONS",
    "QUESTION_BY_KEY",
    "SKIP_PREFIX",
    "answered_count",
    "is_done",
    "next_question",
    "record_answer",
    "status",
]
