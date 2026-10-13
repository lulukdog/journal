from datetime import datetime

from app import days
from app import entries


def test_day_of_boundary():
    """Review Focus #3：03:59 算前一天，04:01 算当天。"""
    assert days.day_of(datetime(2026, 10, 13, 3, 59)) == "2026-10-12"
    assert days.day_of(datetime(2026, 10, 13, 4, 1)) == "2026-10-13"
    assert days.day_of(datetime(2026, 10, 13, 0, 0)) == "2026-10-12"
    assert days.day_of(datetime(2026, 10, 13, 23, 59)) == "2026-10-13"


def test_day_range_is_half_open():
    start, end = days.day_range("2026-10-13")
    assert start.startswith("2026-10-13T04:00")
    assert end.startswith("2026-10-14T04:00")


def test_refresh_counts_is_idempotent(conn):
    ts = datetime(2026, 10, 13, 12, 0)
    entries.create_text(conn, "第一条", ts=ts)
    entries.create_text(conn, "第二条", ts=ts)

    first = days.refresh_counts(conn, "2026-10-13")
    assert first["entry_count"] == 2

    second = days.refresh_counts(conn, "2026-10-13")
    assert second["entry_count"] == 2, "重复调用不能累加"


def test_image_count_tracked_separately(conn):
    ts = datetime(2026, 10, 13, 12, 0)
    entries.create_text(conn, "文字", ts=ts)
    entries.create_image(conn, "2026/10/a.png", ts=ts)
    row = days.get(conn, "2026-10-13")
    assert row["entry_count"] == 2
    assert row["image_count"] == 1


def test_get_returns_none_for_empty_day(conn):
    assert days.get(conn, "1999-01-01") is None


def test_as_dict_decodes_keywords(conn):
    days.ensure(conn, "2026-10-13")
    days.save_summary(
        conn,
        "2026-10-13",
        mood=-1,
        mood_label="有点钝",
        headline="有点钝，但还没塌",
        keywords=["累", "开会"],
    )
    d = days.as_dict(days.get(conn, "2026-10-13"))
    assert d["mood"] == -1
    assert d["keywords"] == ["累", "开会"]
    assert d["finalized"] == 1
