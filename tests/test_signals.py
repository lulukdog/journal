from datetime import datetime

from app import signals
from app import days as days_mod
from app import entries as entries_mod


def _entry(conn, text, day):
    y, m, d = (int(x) for x in day.split("-"))
    entries_mod.create_text(conn, text, ts=datetime(y, m, d, 12, 0), boundary_hour=4)


def test_repeat_fires_at_threshold(conn):
    """三天内同一情绪被提到 4 次 —— 这是最可靠的信号。"""
    for day in ("2026-10-11", "2026-10-12", "2026-10-13"):
        _entry(conn, "今天好累", day)
    _entry(conn, "还是累", "2026-10-13")

    found = signals.repeat(conn, "2026-10-13")
    assert found is not None
    assert found.type == "repeat"
    assert found.payload["top"] == "累"
    assert found.payload["count"] == 4


def test_repeat_needs_threshold(conn):
    """只提到 3 次不够。宁可不说，也不要说错。"""
    _entry(conn, "累", "2026-10-11")
    _entry(conn, "累", "2026-10-12")
    _entry(conn, "累", "2026-10-13")
    assert signals.repeat(conn, "2026-10-13") is None


def test_repeat_ignores_old_entries(conn):
    _entry(conn, "累", "2026-09-01")
    _entry(conn, "累", "2026-09-02")
    _entry(conn, "累", "2026-09-03")
    _entry(conn, "累", "2026-09-04")
    assert signals.repeat(conn, "2026-10-13") is None


def test_drift_fires_on_clear_drop(conn):
    for i in range(10):
        days_mod.ensure(conn, f"2026-09-{i + 1:02d}")
        days_mod.save_summary(
            conn, f"2026-09-{i + 1:02d}", mood=1, mood_label=None, headline=None, keywords=[]
        )
    for i in range(5):
        day = f"2026-10-{9 + i}"
        days_mod.ensure(conn, day)
        days_mod.save_summary(conn, day, mood=-2, mood_label=None, headline=None, keywords=[])

    found = signals.drift(conn, "2026-10-13")
    assert found is not None
    assert found.payload["delta"] < 0


def test_drift_needs_enough_samples(conn):
    days_mod.ensure(conn, "2026-10-13")
    days_mod.save_summary(conn, "2026-10-13", mood=-2, mood_label=None, headline=None, keywords=[])
    assert signals.drift(conn, "2026-10-13") is None


def test_dangling_fires_when_topic_never_returns(conn):
    _entry(conn, "要不要换个工作", "2026-09-20")
    found = signals.dangling(conn, "2026-10-13")
    assert found is not None
    assert found.payload["day"] == "2026-09-20"


def test_dangling_quiet_when_topic_returns(conn):
    _entry(conn, "要不要换个工作", "2026-09-20")
    _entry(conn, "想了想换个工作的事，算了", "2026-10-10")
    assert signals.dangling(conn, "2026-10-13") is None


def test_upcoming_finds_time_phrase(conn):
    _entry(conn, "月底要交东西", "2026-10-12")
    found = signals.upcoming(conn, "2026-10-13")
    assert found is not None
    assert any("月底" in p for p in found.payload["phrases"])


def test_silence_fires_after_gap(conn):
    found = signals.silence(conn, "2026-10-13")
    assert found is not None
    assert found.type == "silence"


def test_silence_quiet_when_recent_activity(conn):
    _entry(conn, "说了句话", "2026-10-12")
    assert signals.silence(conn, "2026-10-13") is None


def test_silence_quiet_when_wrote_today(conn):
    """今天说过话就不算沉默 —— 之前这里漏了今天，会误报。"""
    _entry(conn, "今天说了句话", "2026-10-13")
    assert signals.silence(conn, "2026-10-13") is None


def test_silence_circuit_breaker(conn):
    """被忽略两次就自动停用一段时间。不说话不一定是坏事。"""
    from app import memory

    for day in ("2026-10-05", "2026-10-08"):
        memory.record_utterance(conn, day, "signal:silence", "最近怎么样")
    assert signals.silence(conn, "2026-10-13") is None


def test_silence_breaker_resets_when_answered(conn):
    from app import memory

    first = memory.record_utterance(conn, "2026-10-05", "signal:silence", "最近怎么样")
    memory.record_utterance(conn, "2026-10-08", "signal:silence", "最近怎么样")
    conn.execute("UPDATE utterances SET reaction = 'replied' WHERE id = ?", (first,))
    conn.commit()
    assert signals.silence(conn, "2026-10-13") is not None


def test_risk_fires_on_explicit_phrase(conn):
    _entry(conn, "有时候觉得活着没意思", "2026-10-13")
    found = signals.risk(conn, "2026-10-13")
    assert found is not None
    assert found.type == "risk"


def test_risk_quiet_on_ordinary_complaint(conn):
    """「不想干了」是普通抱怨，不是风险。区分开很重要。"""
    _entry(conn, "今天真不想干了", "2026-10-13")
    assert signals.risk(conn, "2026-10-13") is None


def test_detect_puts_risk_first(conn):
    _entry(conn, "有时候觉得活着没意思", "2026-10-13")
    found = signals.detect(conn, "2026-10-13")
    assert found[0].type == "risk"


def test_record_is_idempotent(conn):
    found = signals.detect(conn, "2026-10-13")
    signals.record(conn, found, "2026-10-13")
    signals.record(conn, found, "2026-10-13")
    rows = conn.execute(
        "SELECT signal_type, COUNT(*) AS n FROM events GROUP BY signal_type"
    ).fetchall()
    assert all(r["n"] == 1 for r in rows), "同一天同一种信号不能有两条"
