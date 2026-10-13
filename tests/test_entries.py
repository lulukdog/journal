import base64
from datetime import datetime

import pytest

from app import entries
from tests.conftest import TINY_PNG_B64


def test_blank_text_rejected(conn):
    """Review Focus #1：空输入不该产生记录。"""
    for bad in ["", "   ", "\n\t ", None]:
        with pytest.raises(entries.EntryError):
            entries.create_text(conn, bad)
    assert entries.list_by_day(conn, "2026-10-13") == []


def test_create_text_persists_and_counts(conn):
    ts = datetime(2026, 10, 13, 12, 0)
    entry_id = entries.create_text(conn, "  今天真不想干了  ", ts=ts)
    assert entry_id > 0

    rows = entries.list_by_day(conn, "2026-10-13")
    assert len(rows) == 1
    assert rows[0]["content"] == "今天真不想干了", "首尾空白应当被去掉"
    assert rows[0]["processed"] == 0


def test_late_night_lands_on_previous_day(conn):
    """凌晨 3 点说的话算前一天。"""
    entries.create_text(conn, "睡不着", ts=datetime(2026, 10, 13, 3, 30))
    assert len(entries.list_by_day(conn, "2026-10-12")) == 1
    assert len(entries.list_by_day(conn, "2026-10-13")) == 0


def test_snapshot_bytes_detects_png():
    data = base64.b64decode(TINY_PNG_B64)
    assert entries.snapshot_bytes(data) == ".png"


def test_snapshot_bytes_rejects_unknown():
    with pytest.raises(entries.EntryError):
        entries.snapshot_bytes(b"not an image at all")


def test_decode_image_accepts_data_url():
    data, ext = entries.decode_image(f"data:image/png;base64,{TINY_PNG_B64}")
    assert ext == ".png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_decode_image_rejects_garbage():
    with pytest.raises(entries.EntryError):
        entries.decode_image("data:image/png;base64,%%%%not base64%%%%")


def test_decode_image_rejects_oversize(monkeypatch):
    """Review Focus #6：超大图片要给明确拒绝，不能 500。"""
    monkeypatch.setattr(entries, "MAX_IMAGE_BYTES", 16)
    huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    with pytest.raises(entries.EntryError) as exc:
        entries.decode_image(huge)
    assert "太大" in str(exc.value)


def test_save_image_bytes_writes_under_year_month(media_dir):
    data = base64.b64decode(TINY_PNG_B64)
    rel = entries.save_image_bytes(media_dir, data, ".png", datetime(2026, 10, 13, 12, 0))
    assert rel.startswith("2026/10/")
    assert (media_dir / rel).read_bytes() == data


def test_create_image_with_caption(conn, media_dir):
    data = base64.b64decode(TINY_PNG_B64)
    rel = entries.save_image_bytes(media_dir, data, ".png", datetime(2026, 10, 13, 12, 0))
    entries.create_image(conn, rel, caption="今天的窗外", ts=datetime(2026, 10, 13, 12, 0))
    rows = entries.list_by_day(conn, "2026-10-13")
    assert rows[0]["kind"] == "image"
    assert rows[0]["content"] == "今天的窗外"
    assert rows[0]["image_path"] == rel


def test_unprocessed_and_mark(conn):
    a = entries.create_text(conn, "一", ts=datetime(2026, 10, 13, 12, 0))
    b = entries.create_text(conn, "二", ts=datetime(2026, 10, 13, 12, 1))
    assert [r["id"] for r in entries.unprocessed(conn)] == [a, b]
    entries.mark_processed(conn, [a])
    assert [r["id"] for r in entries.unprocessed(conn)] == [b]
