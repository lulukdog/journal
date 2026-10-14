from datetime import datetime

import pytest

from app import dayclose, llm, signals
from app import entries as entries_mod


class FakeSettings:
    api_key = "test-key"
    base_url = "http://localhost"
    model = "test-model"
    model_deep = "test-model"
    day_boundary_hour = 4

    def __init__(self, media_dir=None):
        self.media_dir = media_dir


@pytest.fixture
def fake_settings(media_dir):
    return FakeSettings(media_dir)


@pytest.fixture
def patch_llm(monkeypatch):
    calls = {"summary": 0, "facts": 0, "speak": 0}

    async def fake_chat(settings, messages, **kwargs):
        system = messages[0]["content"]
        if "facts" in system:
            calls["facts"] += 1
            return '{"facts": [{"key": "current_job", "value": "在做 AI 产品", "category": "work"}]}'
        if "mood" in system:
            calls["summary"] += 1
            return (
                '{"mood": -1, "mood_label": "有点钝", '
                '"headline": "开了个会", "keywords": ["累", "开会"]}'
            )
        calls["speak"] += 1
        return "你这几天提了好几次累，是我读错了吗？"

    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


def _seed_day(conn):
    for i in range(4):
        entries_mod.create_text(
            conn, "今天好累", ts=datetime(2026, 10, 13, 12, i), boundary_hour=4
        )
    entries_mod.create_text(
        conn, "午饭随便吃了点", ts=datetime(2026, 10, 13, 13, 0), boundary_hour=4
    )


@pytest.mark.asyncio
async def test_dayclose_idempotent(fake_settings, conn, patch_llm):
    """对同一天跑两次：事实不重复、信号不翻倍、话不重复说。"""
    _seed_day(conn)
    await dayclose.run(fake_settings, conn, "2026-10-13")
    first_facts = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
    first_events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    first_utter = conn.execute("SELECT COUNT(*) AS n FROM utterances").fetchone()["n"]

    await dayclose.run(fake_settings, conn, "2026-10-13")

    assert conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"] == first_facts
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == first_events
    assert conn.execute("SELECT COUNT(*) AS n FROM utterances").fetchone()["n"] == first_utter


@pytest.mark.asyncio
async def test_dayclose_marks_entries_processed(fake_settings, conn, patch_llm):
    _seed_day(conn)
    assert len(entries_mod.unprocessed(conn)) == 5
    await dayclose.run(fake_settings, conn, "2026-10-13")
    assert entries_mod.unprocessed(conn) == []


@pytest.mark.asyncio
async def test_dayclose_writes_summary(fake_settings, conn, patch_llm):
    _seed_day(conn)
    await dayclose.run(fake_settings, conn, "2026-10-13")
    row = conn.execute("SELECT * FROM days WHERE day = '2026-10-13'").fetchone()
    assert row["mood"] == -1
    assert row["mood_label"] == "有点钝"
    assert row["finalized"] == 1


@pytest.mark.asyncio
async def test_summarize_survives_bad_json(fake_settings, conn, monkeypatch):
    """模型返回废话时，卡片不能被破坏，函数不能抛异常。"""
    _seed_day(conn)

    async def bad_chat(settings, messages, **kwargs):
        return "抱歉，我不能回答这个问题。"

    monkeypatch.setattr(llm, "chat", bad_chat)
    result = await dayclose.summarize_day(fake_settings, conn, "2026-10-13")
    assert result is None
    row = conn.execute("SELECT * FROM days WHERE day = '2026-10-13'").fetchone()
    assert row is not None, "卡片行还要存在，只是没有摘要"
    assert row["mood"] is None


@pytest.mark.asyncio
async def test_dayclose_survives_llm_outage(fake_settings, conn, monkeypatch):
    """模型整个挂掉时，收口不能崩，记录也不受影响。"""
    _seed_day(conn)

    async def dead_chat(settings, messages, **kwargs):
        raise llm.LLMError("连不上模型服务")

    monkeypatch.setattr(llm, "chat", dead_chat)
    summary = await dayclose.run(fake_settings, conn, "2026-10-13")
    assert summary["steps"]["summary"] is False
    assert summary["steps"]["facts"] == 0
    assert entries_mod.list_by_day(conn, "2026-10-13"), "记录必须还在"
    # 模型没看到就不能标已处理，否则永远不会重试
    assert len(entries_mod.unprocessed(conn)) == 5


@pytest.mark.asyncio
async def test_speak_for_signal_not_repeated(fake_settings, conn, patch_llm):
    _seed_day(conn)
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    first = await dayclose.speak_for_signal(fake_settings, conn, "2026-10-13", signal)
    second = await dayclose.speak_for_signal(fake_settings, conn, "2026-10-13", signal)
    assert first
    assert second is None, "同一信号同一天不能重复开口"


@pytest.mark.asyncio
async def test_dayclose_on_empty_day_does_not_crash(fake_settings, conn, patch_llm):
    summary = await dayclose.run(fake_settings, conn, "2026-10-13")
    assert summary["day"] == "2026-10-13"


# ---------- 数字守卫 ----------


def test_parse_count_handles_both_scripts():
    assert dayclose.parse_count("4") == 4
    assert dayclose.parse_count("七") == 7
    assert dayclose.parse_count("十") == 10
    assert dayclose.parse_count("十二") == 12
    assert dayclose.parse_count("二十") == 20



def test_counts_in_finds_counted_things():
    assert dayclose.counts_in("你这三天提了 7 次累") == [7]
    assert dayclose.counts_in("提了四次") == [4]
    assert dayclose.counts_in("三天没说话") == []


def test_guard_replaces_wrong_count():
    """实测里模型把卡片上的「今天 7 段」当成了「提了 7 次累」。"""
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    out = dayclose._guard_count(signal, "这三天你写了 7 条说累，是我读错了吗？")
    assert "7" not in out
    assert "4 次" in out


def test_guard_keeps_correct_count():
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    text = "你这几天提了 4 次累，是我读错了吗？"
    assert dayclose._guard_count(signal, text) == text


def test_guard_keeps_sentence_without_numbers():
    """不提数字是安全的（提示词里就允许这么说），不该被改。"""
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    text = "你这几天老提累，是我读错了吗？"
    assert dayclose._guard_count(signal, text) == text


def test_guard_catches_chinese_wrong_count():
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    out = dayclose._guard_count(signal, "你这几天提了七次累")
    assert "4 次" in out


def test_guard_silence_gap():
    signal = signals.Signal("silence", {"gap_days": 3})
    out = dayclose._guard_count(signal, "你 5 天没说话了")
    assert "3 天" in out


@pytest.mark.asyncio
async def test_dayclose_only_touches_its_own_day(fake_settings, conn, patch_llm):
    """收口某一天时，不能把其他天的记录也标成已处理。"""
    entries_mod.create_text(
        conn, "昨天的话", ts=datetime(2026, 10, 12, 12, 0), boundary_hour=4
    )
    _seed_day(conn)
    await dayclose.run(fake_settings, conn, "2026-10-13")

    remaining = entries_mod.unprocessed(conn)
    assert len(remaining) == 1, "昨天那条不该被今天顺手标掉"
    assert remaining[0]["content"] == "昨天的话"


@pytest.mark.asyncio
async def test_speak_applies_guard_end_to_end(fake_settings, conn, monkeypatch):
    """守卫要真的接在出口上，不能只是个没人调的纯函数。"""
    _seed_day(conn)

    async def lying_chat(settings, messages, **kwargs):
        return "这三天你写了 7 条说累，是我读错了吗？"

    monkeypatch.setattr(llm, "chat", lying_chat)
    signal = signals.Signal("repeat", {"top": "累", "count": 4})
    text = await dayclose.speak_for_signal(fake_settings, conn, "2026-10-13", signal)
    assert text == "你这几天提了 4 次「累」，是我读错了吗？"

    stored = conn.execute("SELECT content FROM utterances").fetchone()["content"]
    assert stored == text, "写进库里的得是拦过的那句"
