"""六条信号 —— 「它觉得有必要」的可执行判据。

每一条都是可审计的规则，不是「让模型自己觉得」。
触发的都写进 events 表，事后能查「它为什么在那天找我」。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

# 反复出现的情绪/状态词。这些词出现得多，才说明是真的。
EMOTION_WORDS = [
    "累", "烦", "撑", "扛", "焦虑", "难受", "没意思", "睡不着",
    "压力", "委屈", "不想干", "没劲", "麻木", "疲惫",
]

# 「悬置」的标记词：说出这些词往往意味着一个没有结论的决定。
DANGLING_MARKERS = ["要不要", "该不该", "打算", "纠结", "想过", "考虑过", "犹豫"]

# 「临近」的时间表达。
UPCOMING_PATTERNS = [
    r"下(?:个)?(?:周|月|星期)",
    r"(?:这个)?(?:周|星期)[一二三四五六日天]",
    r"\d{1,2}\s*月\s*\d{1,2}\s*[号日]",
    r"\d{1,2}\s*[号日](?:之前|以前|前)?",
    r"(?:月底|月初|年底|年后|年后|明年)",
]

# 「风险」—— 单独处理，见 spec 5.6。它不做诊断，只识别明确的极端表达。
RISK_PHRASES = [
    "不想活", "活着没意思", "想死", "自杀", "结束这一切",
    "消失算了", "活不下去", "没有意义再",
]

SILENCE_GAP_DAYS = 3
SILENCE_COOLDOWN_DAYS = 14
SILENCE_MAX_IGNORED = 2
REPEAT_WINDOW_DAYS = 3
REPEAT_THRESHOLD = 4
DRIFT_RECENT_DAYS = 7
DRIFT_BASELINE_DAYS = 30
DRIFT_DELTA = 1.0
DANGLING_MIN_AGE_DAYS = 7


@dataclass(frozen=True)
class Signal:
    type: str
    payload: dict

    def to_row(self) -> dict:
        return asdict(self)


def _shift(day: str, delta: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta)).strftime(
        "%Y-%m-%d"
    )


def _entries_between(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE day BETWEEN ? AND ? ORDER BY ts ASC",
        (start, end),
    ).fetchall()


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"\s+", "", text or "")
    return {clean[i : i + 3] for i in range(len(clean) - 2)}


def repeat(conn: sqlite3.Connection, day: str) -> Signal | None:
    """同一情绪词在窗口期内被提到的天数/条数达到阈值。

    按「有几条记录提到它」计数，不是词频 —— 因为「提了四次」指的是四次，
    不是一句话里说了四遍。
    """
    start = _shift(day, -(REPEAT_WINDOW_DAYS - 1))
    rows = _entries_between(conn, start, day)
    hits: dict[str, int] = {}
    for word in EMOTION_WORDS:
        count = sum(1 for r in rows if r["content"] and word in r["content"])
        if count >= REPEAT_THRESHOLD:
            hits[word] = count
    if not hits:
        return None
    top = max(hits.items(), key=lambda kv: kv[1])
    return Signal("repeat", {"words": hits, "top": top[0], "count": top[1]})


def drift(conn: sqlite3.Connection, day: str) -> Signal | None:
    """最近 7 天的状态和之前 30 天相比，偏离超过阈值。

    变化比绝对值重要 —— 心情一直偏低的人和突然掉下来的人，意义完全不同。
    """
    recent_start = _shift(day, -(DRIFT_RECENT_DAYS - 1))
    baseline_start = _shift(day, -(DRIFT_RECENT_DAYS + DRIFT_BASELINE_DAYS))
    baseline_end = _shift(day, -DRIFT_RECENT_DAYS)

    def moods(start: str, end: str) -> list[int]:
        rows = conn.execute(
            "SELECT mood FROM days WHERE day BETWEEN ? AND ? AND mood IS NOT NULL",
            (start, end),
        ).fetchall()
        return [int(r["mood"]) for r in rows]

    recent = moods(recent_start, day)
    baseline = moods(baseline_start, baseline_end)
    if len(recent) < 3 or len(baseline) < 3:
        return None

    recent_mean = sum(recent) / len(recent)
    baseline_mean = sum(baseline) / len(baseline)
    delta = recent_mean - baseline_mean
    if abs(delta) < DRIFT_DELTA:
        return None
    return Signal(
        "drift",
        {
            "recent_mean": round(recent_mean, 2),
            "baseline_mean": round(baseline_mean, 2),
            "delta": round(delta, 2),
        },
    )


def dangling(conn: sqlite3.Connection, day: str) -> Signal | None:
    """他提过一个决定，之后就没有下文了。

    人在回避什么，往往就是最重要的那件事。
    """
    cutoff = _shift(day, -DANGLING_MIN_AGE_DAYS)
    candidates = [
        r
        for r in _entries_between(conn, _shift(day, -90), cutoff)
        if r["content"] and any(m in r["content"] for m in DANGLING_MARKERS)
    ]
    if not candidates:
        return None

    recent = _entries_between(conn, _shift(day, -(DANGLING_MIN_AGE_DAYS - 1)), day)
    recent_grams: set[str] = set()
    for row in recent:
        recent_grams |= _bigrams(row["content"] or "")

    for row in reversed(candidates):
        if _bigrams(row["content"] or "") & recent_grams:
            continue
        return Signal(
            "dangling",
            {"day": row["day"], "excerpt": (row["content"] or "")[:80]},
        )
    return None


def upcoming(conn: sqlite3.Connection, day: str) -> Signal | None:
    """他最近提到过一个时间点。

    第一版没有推送，这条信号基本没用 —— 保留检测，但不指望它。
    """
    rows = _entries_between(conn, _shift(day, -2), day)
    found: list[str] = []
    for row in rows:
        text = row["content"] or ""
        for pattern in UPCOMING_PATTERNS:
            match = re.search(pattern, text)
            if match:
                found.append(match.group(0))
    if not found:
        return None
    return Signal("upcoming", {"phrases": found[:3]})


def silence(conn: sqlite3.Connection, day: str) -> Signal | None:
    """连续几天没说话。

    这条要非常小心：不说话不一定是坏事，可能只是不需要。
    所以频率上限很低，而且被忽略两次就自动停用一段时间。
    """
    for offset in range(0, SILENCE_GAP_DAYS):
        if _entries_between(conn, _shift(day, -offset), _shift(day, -offset)):
            return None

    cooldown_start = _shift(day, -SILENCE_COOLDOWN_DAYS)
    rows = conn.execute(
        "SELECT reaction FROM utterances "
        "WHERE trigger = 'signal:silence' AND day >= ?",
        (cooldown_start,),
    ).fetchall()
    ignored = sum(1 for r in rows if r["reaction"] is None)
    if ignored >= SILENCE_MAX_IGNORED:
        return None
    return Signal("silence", {"gap_days": SILENCE_GAP_DAYS})


def risk(conn: sqlite3.Connection, day: str) -> Signal | None:
    """明确的极端表达。不做诊断，只做识别，见 spec 5.6。"""
    rows = _entries_between(conn, _shift(day, -6), day)
    for row in rows:
        text = row["content"] or ""
        for phrase in RISK_PHRASES:
            if phrase in text:
                return Signal("risk", {"phrase": phrase, "day": row["day"]})
    return None


DETECTORS = (risk, repeat, dangling, upcoming, drift, silence)


def detect(conn: sqlite3.Connection, day: str) -> list[Signal]:
    """跑全部检测。risk 排在最前，因为它优先级最高。"""
    out: list[Signal] = []
    for detector in DETECTORS:
        try:
            found = detector(conn, day)
        except Exception:
            continue
        if found is not None:
            out.append(found)
    return out


def record(conn: sqlite3.Connection, signals: list[Signal], day: str) -> None:
    """写进 events。靠 UNIQUE(day, signal_type) 保证幂等。"""
    now = datetime.now().isoformat(timespec="seconds")
    for signal in signals:
        conn.execute(
            "INSERT OR IGNORE INTO events (ts, day, signal_type, payload) "
            "VALUES (?, ?, ?, ?)",
            (now, day, signal.type, json.dumps(signal.payload, ensure_ascii=False)),
        )
    conn.commit()


def already_spoken(conn: sqlite3.Connection, day: str, signal_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM utterances WHERE day = ? AND trigger = ? LIMIT 1",
        (day, f"signal:{signal_type}"),
    ).fetchone()
    return row is not None


__all__ = [
    "DETECTORS",
    "EMOTION_WORDS",
    "RISK_PHRASES",
    "SILENCE_MAX_IGNORED",
    "Signal",
    "already_spoken",
    "detect",
    "record",
]
