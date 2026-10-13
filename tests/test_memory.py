from datetime import datetime

from app import memory, onboarding
from app import entries as entries_mod


def _seed(conn, texts, day="2026-10-13"):
    for i, text in enumerate(texts):
        entries_mod.create_text(
            conn, text, ts=datetime(2026, 10, 13, 12, i % 60), boundary_hour=4
        )
    return day


# ---------- 检索 ----------


def test_search_entries_matches_chinese_short_word(conn):
    """trigram FTS5 搜不到一两个字 —— 这正是用 LIKE 的原因。"""
    _seed(conn, ["今天真累", "午饭没胃口", "还是累"])
    rows = memory.search_entries(conn, "累")
    assert len(rows) == 2


def test_search_entries_empty_query_returns_nothing(conn):
    _seed(conn, ["随便写点什么"])
    assert memory.search_entries(conn, "   ") == []


def test_search_entries_respects_limit(conn):
    for i in range(50):
        entries_mod.create_text(
            conn, f"第 {i} 条关于项目", ts=datetime(2026, 10, 13, 12, 0)
        )
    assert len(memory.search_entries(conn, "项目", limit=7)) == 7


# ---------- 上下文组装 ----------


def test_build_ask_messages_has_no_raw_dump(conn):
    """Review Focus：不管库里多少条，喂给模型的 entries 段必须有上限。"""
    for i in range(500):
        entries_mod.create_text(
            conn, f"第 {i} 条关于项目的事", ts=datetime(2026, 10, 13, 12, 0)
        )
    messages = memory.build_ask_messages(conn, "项目", "2026-10-13", entry_limit=20)

    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "项目"}

    context = messages[1]["content"]
    section = context.split("## 检索到的相关记录")[1].split("## 你之前主动跟他说过的话")[0]
    listed = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(listed) <= 20, f"倒进去了 {len(listed)} 条"


def test_build_ask_messages_includes_profile(conn):
    memory.upsert_memory(conn, "income_source", "靠工资，月入两万", category="income")
    messages = memory.build_ask_messages(conn, "我该不该辞职", "2026-10-13")
    assert "月入两万" in messages[1]["content"]


def test_build_ask_messages_on_empty_db_does_not_crash(conn):
    """Review Focus #5：全新库也要能组装出上下文，不能抛异常。"""
    messages = memory.build_ask_messages(conn, "我该做什么", "2026-10-13")
    assert len(messages) == 3
    assert "还没有关于他的长期事实" in messages[1]["content"]


def test_build_ask_messages_carries_previous_utterances(conn):
    memory.record_utterance(conn, "2026-10-12", "signal:repeat", "你最近老提累")
    messages = memory.build_ask_messages(conn, "随便问问", "2026-10-13")
    assert "你最近老提累" in messages[1]["content"]


# ---------- 画像 ----------


def test_upsert_memory_updates_in_place(conn):
    memory.upsert_memory(conn, "monthly_expense", "一万", category="expense")
    memory.upsert_memory(conn, "monthly_expense", "一万二", category="expense")
    rows = conn.execute(
        "SELECT value FROM memories WHERE key = 'monthly_expense'"
    ).fetchall()
    assert len(rows) == 1, "同一个 key 不能堆积成两行"
    assert rows[0]["value"] == "一万二"


def test_profile_excludes_internal_bookkeeping(conn):
    """访谈的跳题标记是内部账，不能混进送给模型的画像。"""
    onboarding.record_answer(conn, "income_source", None, skipped=True)
    onboarding.record_answer(conn, "monthly_expense", "八千")
    profile = memory.get_profile(conn)
    assert "八千" in profile
    assert "onboarding_skipped" not in profile


def test_profile_says_so_when_empty(conn):
    assert "还没有" in memory.get_profile(conn)


# ---------- 它说过的话 ----------


def test_record_and_read_utterances(conn):
    memory.record_utterance(conn, "2026-10-13", "signal:repeat", "你最近老提累")
    rows = memory.utterances_for_day(conn, "2026-10-13")
    assert len(rows) == 1
    assert rows[0]["trigger"] == "signal:repeat"


def test_recent_utterances_excludes_ask(conn):
    """「问它」的回答不是它主动开口，不该出现在「你之前说过的话」里。"""
    memory.record_utterance(conn, "2026-10-13", "ask", "这是回答")
    memory.record_utterance(conn, "2026-10-13", "signal:repeat", "这是主动开口")
    block = memory.recent_utterances(conn, "2026-10-13")
    assert "这是主动开口" in block
    assert "这是回答" not in block


# ---------- 卡片文本 ----------


def test_cards_text_handles_empty(conn):
    assert "没有记录" in memory.cards_text(conn, "2026-10-13")


# ---------- 引导访谈 ----------


def test_onboarding_completes(conn):
    assert onboarding.is_done(conn) is False
    for _ in range(len(onboarding.QUESTIONS)):
        question = onboarding.next_question(conn)
        assert question is not None
        onboarding.record_answer(conn, question["key"], "随便答一下")
    assert onboarding.is_done(conn) is True
    assert onboarding.next_question(conn) is None


def test_onboarding_skip_counts_as_answered(conn):
    first = onboarding.next_question(conn)
    onboarding.record_answer(conn, first["key"], None, skipped=True)
    second = onboarding.next_question(conn)
    assert second["key"] != first["key"]
    assert onboarding.answered_count(conn) == 1


def test_onboarding_status_on_fresh_db_points_at_first_question(conn):
    status = onboarding.status(conn)
    assert status["done"] is False
    assert status["question"] is not None
    assert status["answered"] == 0
    assert status["total"] == len(onboarding.QUESTIONS)


def test_onboarding_answer_writes_profile_memory(conn):
    onboarding.record_answer(conn, "income_source", "靠工资，月入两万")
    assert "月入两万" in memory.get_profile(conn)


def test_blank_onboarding_answer_is_stored_as_skip(conn):
    onboarding.record_answer(conn, "income_source", "   ")
    assert onboarding.answered_count(conn) == 1
    assert "月入两万" not in memory.get_profile(conn)
    assert "income_source:" not in memory.get_profile(conn)


def test_onboarding_answers_become_the_first_profile(conn):
    """访谈答完，画像里就该有东西了 —— 它才有资格开口。"""
    for question in onboarding.QUESTIONS:
        onboarding.record_answer(conn, question["key"], f"关于{question['key']}的回答")
    profile = memory.get_profile(conn)
    for question in onboarding.QUESTIONS:
        assert f"关于{question['key']}的回答" in profile
