"""听见语气，回一句短的。

这不是评估。没有分数，没有诊断，没有「你应该」。
只在听出累、有压力、难过、孤单、紧张或高兴时，给一句朋友式的话。
听不出来就安静。极端表达单独接住，仍然不贴临床标签。
"""

from __future__ import annotations

import re

# 并列时先接住更沉的那一面，不要用高兴盖过去。
PRIORITY = ("sad", "lonely", "anxious", "tired", "stressed", "happy")

# 长的词放前面，避免「好开心」被拆碎。
CUES: dict[str, tuple[str, ...]] = {
    "tired": ("没劲", "疲惫", "撑不住", "困死", "好困", "睡不着", "心累", "累"),
    "stressed": ("喘不过气", "扛不住", "忙不过来", "崩溃", "压力", "心烦", "烦"),
    "sad": ("想哭", "难过", "伤心", "委屈", "难受", "低落", "没意思", "不开心"),
    "lonely": ("没人陪", "没人懂", "好孤单", "孤单", "孤独", "寂寞"),
    "anxious": ("心慌", "不安", "紧张", "担心", "焦虑", "慌"),
    "happy": ("好开心", "开心", "高兴", "快乐", "挺好", "好爽", "哈哈", "顺利"),
}

# 「累」会嵌在「积累」里。这种不算。
_EMBEDDED = {
    "累": ("积累", "累计", "累积", "连累"),
    "烦": ("烦请", "不厌其烦", "麻烦"),
    "慌": ("慌忙",),
}

# 卡片上的陪伴：接住，不问诊。
LINES = {
    "tired": "听着有点累。先歇一会儿也行，我在。",
    "stressed": "压力挺沉的。先放这儿，不用现在想明白。",
    "sad": "听起来不太好过。我陪着，不讲道理。",
    "lonely": "一个人待着的时候，我在这儿。",
    "anxious": "心里有点紧吧。先不用理清，我听着。",
    "happy": "听着挺亮的。替你高兴一下。",
}

# 信号开口：先问，再判断。和卡片上的陪伴不是同一句，避免说两遍。
CHECKINS = {
    "tired": "你提到累了。是有点撑着，还是我读重了？",
    "stressed": "你提到压力。是最近特别紧吗？",
    "sad": "这几句有点沉。要我陪着就好，还是想说说？",
    "lonely": "你写到一个人待着。要我在这儿吗？",
    "anxious": "听着心里有点紧。要先放一放吗？",
    "happy": "今天的字是亮的。是挺高兴吧？",
}

# 明确的极端表达。不诊断，只是不要把人单独留在那句话里。
HEAVY_LINE = "这句话很重。我在。要是撑不住，去找一个你信得过的人。"

_UNSAFE = (
    "抑郁",
    "抑郁症",
    "焦虑症",
    "焦虑障碍",
    "焦虑水平",
    "情绪障碍",
    "心理疾病",
    "心理障碍",
    "诊断",
    "评分",
    "打分",
    "你应该",
    "你要学会",
    "建议你",
    "必须去",
)

QUIET = {"tone": None, "line": None, "heard": [], "checkin": None, "mentions": 0}


def _inside_longer(text: str, index: int, size: int, longer: str) -> bool:
    begin = text.find(longer)
    while begin >= 0:
        if begin <= index and index + size <= begin + len(longer):
            return True
        begin = text.find(longer, begin + 1)
    return False


def matched_cues(text: str, cues: tuple[str, ...]) -> list[str]:
    """返回这句话里真正出现的情绪词。否定（不开心）和嵌词（积累）不算。"""
    found: list[str] = []
    body = text or ""
    for cue in cues:
        start = 0
        while True:
            index = body.find(cue, start)
            if index < 0:
                break
            prev = body[index - 1] if index else ""
            if prev in "不没":
                start = index + len(cue)
                continue
            if any(
                _inside_longer(body, index, len(cue), longer)
                for longer in _EMBEDDED.get(cue, ())
            ):
                start = index + len(cue)
                continue
            found.append(cue)
            break
    return found


def _heavy_phrase(text: str) -> str | None:
    from .signals import RISK_PHRASES

    for phrase in RISK_PHRASES:
        if phrase and phrase in (text or ""):
            return phrase
    return None


def hear(text: str) -> dict | None:
    """从一段话里听日常语气。听不出返回 None。不含分数。"""
    return _from_parts([text or ""])


def hear_rows(rows) -> dict | None:
    parts = []
    for row in rows:
        if isinstance(row, str):
            parts.append(row)
        else:
            parts.append(row["content"] or "")
    return _from_parts(parts)


def _from_parts(parts: list[str]) -> dict | None:
    mentions = {tone: 0 for tone in PRIORITY}
    heard: dict[str, list[str]] = {tone: [] for tone in PRIORITY}
    for part in parts:
        if not part:
            continue
        for tone in PRIORITY:
            words = matched_cues(part, CUES[tone])
            if not words:
                continue
            mentions[tone] += 1
            for word in words:
                if word not in heard[tone]:
                    heard[tone].append(word)
    ranked = [tone for tone in PRIORITY if mentions[tone] > 0]
    if not ranked:
        return None
    tone = min(ranked, key=lambda item: (-mentions[item], PRIORITY.index(item)))
    return {
        "tone": tone,
        "line": LINES[tone],
        "heard": heard[tone],
        "checkin": CHECKINS[tone],
        "mentions": mentions[tone],
    }


def reflect(text: str) -> dict:
    """给接口用的心情回声。极端表达优先于日常语气。永远没有评分字段。"""
    phrase = _heavy_phrase(text)
    if phrase:
        return {
            "tone": "heavy",
            "line": HEAVY_LINE,
            "heard": [phrase],
            "checkin": None,
            "mentions": 1,
        }
    heard = hear(text)
    if heard is None:
        return dict(QUIET)
    return heard


def reflect_rows(rows) -> dict:
    parts = []
    for row in rows:
        if isinstance(row, str):
            parts.append(row)
        else:
            parts.append((row["content"] if row is not None else "") or "")
    return reflect("\n".join(parts))


def usable_model_line(text: str | None) -> str | None:
    """模型写的陪伴句。说教、诊断、打分、太长，都丢掉。"""
    if not isinstance(text, str):
        return None
    line = text.strip().strip('"').strip("'").strip("「」").strip()
    if not line:
        return None
    line = line.splitlines()[0].strip()
    if not line or len(line) > 40:
        return None
    if unsafe(line):
        return None
    if re.search(r"\d+\s*分", line):
        return None
    return line


def unsafe(text: str | None) -> bool:
    body = text or ""
    return any(phrase in body for phrase in _UNSAFE)


def for_api(card: dict | None, texts: list[str], *, source_if_live: str = "live") -> dict:
    """Android / 网页都能直接画的字段。line 为空表示这次保持安静。"""
    reflected = reflect("\n".join(t for t in texts if t))
    card_line = _clean_stored((card or {}).get("companion")) if card else None
    if card_line and unsafe(card_line):
        card_line = None
    if reflected["tone"] == "heavy":
        line = HEAVY_LINE
        source = "card" if card_line == HEAVY_LINE else source_if_live
    elif card_line:
        line, source = card_line, "card"
    elif reflected["line"]:
        line, source = reflected["line"], source_if_live
    else:
        line, source = None, None
    return {
        "tone": reflected["tone"],
        "line": line,
        "heard": list(reflected["heard"]),
        "source": source,
    }


def _clean_stored(text: str | None) -> str | None:
    """库里已经收下的陪伴句。只拒绝空串，不再用 40 字上限丢掉我们自己的模板。"""
    if not isinstance(text, str):
        return None
    line = text.strip()
    return line or None


def ask_note(text: str) -> str | None:
    """问它的时候，如果听得出语气，先提醒模型接住，而不是上课。"""
    mood = reflect(text)
    if not mood["tone"]:
        return None
    if mood["tone"] == "heavy":
        return (
            "他这次写下了很重的话。先陪着，很短，不评判，不分析成因。"
            "不要诊断，不要打分，不要说抑郁。提醒他如果撑不住，去找一个信得过的人。"
            "不要编造援助电话。"
        )
    return (
        "他这次的话里有情绪。先用一两句接住，像坐在旁边的朋友，短。"
        "不要上课，不要建议清单，不要把感受换成临床词，不要打分。"
        "如果他不是在问该怎么办，就不要给判断，陪着就好，最多问一个很轻的问题。"
        "如果他明确在问该怎么办，第一句仍然先接住，然后再回答。"
        "不要说出你内部用的分类名字。"
    )


def choose_card_line(found, model_line: str | None) -> str | None:
    """收口时写到日卡片上的那句。风险优先，其次才是当天听出来的语气。"""
    if any(getattr(signal, "type", None) == "risk" for signal in found):
        return HEAVY_LINE
    for signal in found:
        if getattr(signal, "type", None) != "checkin":
            continue
        payload = signal.payload or {}
        return usable_model_line(model_line) or payload.get("line") or LINES.get(payload.get("tone"))
    return None


__all__ = [
    "CHECKINS",
    "CUES",
    "HEAVY_LINE",
    "LINES",
    "QUIET",
    "ask_note",
    "choose_card_line",
    "for_api",
    "hear",
    "hear_rows",
    "matched_cues",
    "reflect",
    "reflect_rows",
    "unsafe",
    "usable_model_line",
]
