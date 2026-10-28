"""每晚收口。

顺序：摘要 → 抽事实 → 检测信号 → 为信号生成一句话 → 标记已处理。

硬要求：可重入。任何一步失败都不中断后面的步骤，
已经做过的步骤不重复做（靠 UNIQUE(day, signal_type) 和 already_spoken）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime

from . import companion, days as days_mod
from . import entries as entries_mod
from . import llm, memory, prompts, signals as signals_mod

log = logging.getLogger("journal.dayclose")


# ---------- 数字守卫 ----------
# 模型会自己重新数一遍，而且几乎每次都数错（实测把卡片里的「今天 7 段」
# 当成了「提了 7 次累」）。提示词管不住这件事。
# 而我们手里有准确数字，所以直接用代码兜底：说错数字就换成模板句。
# 说错数字比不说更糟 —— 他会立刻发现你根本没看清。

_CN_NUM = {
    "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def parse_count(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_NUM.get(head, 1) if head else 1
        ones = _CN_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_NUM.get(token)


def counts_in(text: str, units: str = "次条遍") -> list[int]:
    """数出句子里的「N 次/条/遍」。units 决定单位，不同信号单位不同。"""
    pattern = re.compile(rf"([0-9]+|[一两二三四五六七八九十]+)\s*(?:[{units}])")
    out = []
    for match in pattern.finditer(text or ""):
        value = parse_count(match.group(1))
        if value is not None:
            out.append(value)
    return out


def has_wrong_count(text: str, allowed: set[int], units: str = "次条遍") -> bool:
    """句子里的数量词有没有不准的。"""
    return any(value not in allowed for value in counts_in(text, units))


async def summarize_day(settings, conn: sqlite3.Connection, day: str) -> dict | None:
    """把当天内容折成卡片。解析失败保留旧值，不抛异常。"""
    rows = entries_mod.list_by_day(conn, day)
    if not rows:
        days_mod.ensure(conn, day)
        return None

    body = memory.entries_text(rows)
    messages = [
        {"role": "system", "content": prompts.DAY_SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": memory.build_user_content(rows, settings.media_dir),
        },
    ]
    try:
        raw = await llm.chat_with_images(
            settings, messages, temperature=0.3, max_tokens=2500
        )
        data = llm.extract_json(raw)
    except llm.LLMError as exc:
        log.warning("摘要失败 day=%s: %s", day, exc)
        days_mod.ensure(conn, day)
        return None

    mood = data.get("mood")
    try:
        mood = max(-2, min(2, int(mood))) if mood is not None else None
    except (TypeError, ValueError):
        mood = None

    keywords = data.get("keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k)[:20] for k in keywords][:5]

    days_mod.save_summary(
        conn,
        day,
        mood=mood,
        mood_label=(str(data.get("mood_label") or "")[:40] or None),
        headline=(str(data.get("headline") or "")[:80] or None),
        keywords=keywords,
    )
    return data


async def extract_facts(settings, conn: sqlite3.Connection, day: str) -> int:
    rows = entries_mod.list_by_day(conn, day)
    if not rows:
        return 0
    messages = [
        {"role": "system", "content": prompts.FACT_EXTRACT_SYSTEM},
        {"role": "user", "content": memory.entries_text(rows)},
    ]
    try:
        raw = await llm.chat(settings, messages, temperature=0.2, max_tokens=2500)
        data = llm.extract_json(raw)
    except llm.LLMError as exc:
        log.warning("抽事实失败 day=%s: %s", day, exc)
        return 0

    facts = data.get("facts")
    if not isinstance(facts, list):
        return 0

    written = 0
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        key = str(fact.get("key") or "").strip()
        value = str(fact.get("value") or "").strip()
        if not key or not value:
            continue
        memory.upsert_memory(
            conn,
            key[:60],
            value[:500],
            category=str(fact.get("category") or "other")[:20],
        )
        written += 1
    return written


async def speak_for_signal(
    settings, conn: sqlite3.Connection, day: str, signal: signals_mod.Signal
) -> str | None:
    """为一条信号生成「低承诺的询问」。先问，再判断。

    checkin 不走模型：那句问话是从他的原词来的，说错比不说更糟。
    """
    if signals_mod.already_spoken(conn, day, signal.type):
        return None

    if signal.type == "checkin":
        text = (signal.payload.get("checkin") or "").strip()
        if not text or companion.unsafe(text):
            tone = signal.payload.get("tone")
            text = companion.CHECKINS.get(tone) or ""
        if not text:
            return None
        memory.record_utterance(conn, day, f"signal:{signal.type}", text)
        return text

    hint = {
        "repeat": (
            f"在最近 3 天里，他有 {signal.payload.get('count')} 条记录提到了"
            f"「{signal.payload.get('top')}」。这个数字是准确数过的，直接用，别重新数。"
        ),
        "drift": (
            f"他最近 7 天的状态均值是 {signal.payload.get('recent_mean')}，"
            f"之前一个月是 {signal.payload.get('baseline_mean')}（阀值 -2 到 2）。"
            "数字是准确算出来的，直接用。"
        ),
        "dangling": (
            f"他 {signal.payload.get('day')} 说过：「{signal.payload.get('excerpt')}」，之后没再提。"
        ),
        "upcoming": f"他提到过这些时间点：{signal.payload.get('phrases')}。",
        "silence": f"他已经 {signal.payload.get('gap_days')} 天没说话了。",
        "risk": (
            "他写下了一句很重的话。先陪着，不要分析，不要诊断。"
            "如果撑不住，请他去找一个信得过的人。不要编造电话。"
        ),
        "checkin": (
            f"今天能听出的语气是「{signal.payload.get('tone')}」，"
            f"他的原词是 {signal.payload.get('heard')}。"
            "先轻轻问一句，不要下结论。"
        ),
    }.get(signal.type, "")

    context = (
        f"## 关于他的长期事实\n{memory.get_profile(conn)}\n\n"
        f"## 最近 7 天的卡片\n{memory.cards_text(conn, day)}\n\n"
        f"## 你注意到的事\n{hint}\n\n"
        f"## 你之前跟他说过的话（绝对不要重复）\n{memory.recent_utterances(conn, day)}"
    )
    messages = [
        {"role": "system", "content": prompts.SIGNAL_UTTERANCE_SYSTEM},
        {"role": "system", "content": context},
        {"role": "user", "content": "说出那一句话。"},
    ]
    try:
        raw = await llm.chat(settings, messages, temperature=0.8, max_tokens=1200)
    except llm.LLMError as exc:
        log.warning("生成信号话术失败 %s: %s", signal.type, exc)
        return None

    text = raw.strip().strip('"').strip("「」")[:120]
    if not text:
        return None

    text = _guard_voice(signal, text)

    memory.record_utterance(conn, day, f"signal:{signal.type}", text)
    return text


def _fallback_line(signal: signals_mod.Signal) -> str:
    """模型说教或下诊断时，换成我们自己的一句。"""
    if signal.type == "risk":
        return companion.HEAVY_LINE
    if signal.type == "repeat":
        count = signal.payload.get("count")
        word = signal.payload.get("top")
        if count is not None and word:
            return f"你这几天提了 {count} 次「{word}」，是我读错了吗？"
    if signal.type == "silence":
        gap = signal.payload.get("gap_days")
        if gap is not None:
            return f"你已经 {gap} 天没跟我说过话了。"
    if signal.type == "dangling":
        return "那件事后来怎样了？我可能记混了。"
    if signal.type == "upcoming":
        return "你之前提过一个日子。还作数吗？"
    if signal.type == "drift":
        return "最近和之前不太一样。是我看错了吗？"
    if signal.type == "checkin":
        return signal.payload.get("checkin") or "我在。想说的话我听着。"
    return "我在。想说的话我听着。"


def _guard_voice(signal: signals_mod.Signal, text: str) -> str:
    """先挡住说教和诊断，再核对数字。"""
    if companion.unsafe(text):
        log.info("语气守卫拦下了一句：%r", text)
        return _fallback_line(signal)
    return _guard_count(signal, text)


def _guard_count(signal: signals_mod.Signal, text: str) -> str:
    """句子里的数字和已知事实不符时，换成模板句。

    只在模型确实写错了数字时才生效，所以大多数时候还是它自己说的话。
    """
    if signal.type == "repeat":
        count = signal.payload.get("count")
        word = signal.payload.get("top")
        if count is not None and has_wrong_count(text, {int(count)}):
            log.info("数字守卫拦下了一句：%r", text)
            return f"你这几天提了 {count} 次「{word}」，是我读错了吗？"
    elif signal.type == "silence":
        gap = signal.payload.get("gap_days")
        if gap is not None and has_wrong_count(text, {int(gap)}, units="天"):
            log.info("数字守卫拦下了一句：%r", text)
            return f"你已经 {gap} 天没跟我说过话了。"
    return text


async def run(settings, conn: sqlite3.Connection, day: str | None = None) -> dict:
    day = day or days_mod.day_of(datetime.now(), settings.day_boundary_hour)
    summary: dict = {"day": day, "steps": {}}

    model_line = None
    try:
        summarized = await summarize_day(settings, conn, day)
        summary["steps"]["summary"] = bool(summarized)
        if isinstance(summarized, dict):
            model_line = summarized.get("companion")
    except Exception as exc:  # noqa: BLE001
        log.exception("summarize 崩了")
        summary["steps"]["summary"] = f"error: {exc}"

    try:
        summary["steps"]["facts"] = await extract_facts(settings, conn, day)
    except Exception as exc:  # noqa: BLE001
        log.exception("extract_facts 崩了")
        summary["steps"]["facts"] = f"error: {exc}"

    found = signals_mod.detect(conn, day)
    signals_mod.record(conn, found, day)
    summary["steps"]["signals"] = [s.type for s in found]

    card_line = companion.choose_card_line(found, model_line)
    if card_line:
        days_mod.save_companion(conn, day, card_line)
    summary["steps"]["companion"] = card_line

    # 已经有更重的话要说时，不再另起一句日常问候。卡片上仍留着陪伴。
    skip_checkin = any(signal.type == "risk" for signal in found)
    spoken = []
    for signal in found:
        if signal.type == "checkin" and skip_checkin:
            continue
        try:
            text = await speak_for_signal(settings, conn, day, signal)
        except Exception as exc:  # noqa: BLE001
            log.exception("speak_for_signal 崩了 %s", signal.type)
            text = None
        if text:
            spoken.append({"type": signal.type, "text": text})
    summary["steps"]["spoken"] = spoken

    pending = [r["id"] for r in entries_mod.unprocessed(conn, day=day)]
    # 只有模型真的看过了才标记已处理。否则今晚模型挂了，
    # 这些记录就永远不会被总结，而且不会重试。
    if summary["steps"].get("summary") is True:
        entries_mod.mark_processed(conn, pending)
        summary["steps"]["marked"] = len(pending)
    else:
        summary["steps"]["marked"] = 0
        summary["steps"]["deferred"] = len(pending)

    log.info("收口完成 %s", json.dumps(summary, ensure_ascii=False))
    return summary
