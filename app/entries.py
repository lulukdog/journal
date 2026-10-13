"""记录 —— 「记一笔」。

硬要求：先落库、再返回。模型调用不在这条路径上（见 spec 5.2）。
API 挂了不该影响记录。
"""

from __future__ import annotations

import base64
import binascii
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .days import day_of
from .days import refresh_counts

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 原图上限 20MB

_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

_DATA_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,(?P<data>.+)$", re.S)


class EntryError(ValueError):
    """可以直接展示给用户的中文错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def snapshot_bytes(data: bytes) -> str:
    """按魔数判断图片类型，返回扩展名。不认识的抛 EntryError。"""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise EntryError("这个格式我认不出来，请发 jpg / png / gif / webp 图片。")


def decode_image(payload: str) -> tuple[bytes, str]:
    """接受 data URL 或裸 base64，返回 (bytes, 扩展名)。"""
    match = _DATA_URL_RE.match(payload.strip())
    raw = match.group("data") if match else payload.strip()
    raw = raw.replace("\n", "").replace(" ", "")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EntryError("图片没有传完整，请重试。") from exc
    if not data:
        raise EntryError("收到了一张空图片。")
    if len(data) > MAX_IMAGE_BYTES:
        raise EntryError(
            f"图片太大了（超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB），请压一下再发。"
        )
    return data, snapshot_bytes(data)


def create_text(
    conn: sqlite3.Connection,
    text: str,
    ts: datetime | None = None,
    boundary_hour: int = 4,
) -> int:
    """存一条文字记录。空白输入被拒绝（Review Focus #1）。"""
    body = (text or "").strip()
    if not body:
        raise EntryError("空的话就不记了。")
    ts = ts or datetime.now()
    day = day_of(ts, boundary_hour)
    cur = conn.execute(
        """
        INSERT INTO entries (ts, day, kind, content, processed, created_at)
        VALUES (?, ?, 'text', ?, 0, ?)
        """,
        (ts.isoformat(timespec="seconds"), day, body, _now()),
    )
    conn.commit()
    refresh_counts(conn, day)
    return int(cur.lastrowid)


def save_image_bytes(media_dir: Path, data: bytes, ext: str, ts: datetime) -> str:
    """把原图按 年/月 存盘，返回相对于 media_dir 的路径。"""
    rel_dir = Path(f"{ts:%Y}") / f"{ts:%m}"
    target_dir = Path(media_dir) / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{ts:%Y%m%d-%H%M%S}-{int(ts.timestamp() * 1000) % 1000:03d}{ext}"
    (target_dir / name).write_bytes(data)
    return str(rel_dir / name)


def create_image(
    conn: sqlite3.Connection,
    image_path: str,
    caption: str | None = None,
    ts: datetime | None = None,
    boundary_hour: int = 4,
) -> int:
    ts = ts or datetime.now()
    day = day_of(ts, boundary_hour)
    text = (caption or "").strip() or None
    cur = conn.execute(
        """
        INSERT INTO entries (ts, day, kind, content, image_path, processed, created_at)
        VALUES (?, ?, 'image', ?, ?, 0, ?)
        """,
        (ts.isoformat(timespec="seconds"), day, text, image_path, _now()),
    )
    conn.commit()
    refresh_counts(conn, day)
    return int(cur.lastrowid)


def list_by_day(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE day = ? ORDER BY ts ASC, id ASC", (day,)
    ).fetchall()


def unprocessed(
    conn: sqlite3.Connection, limit: int = 200, day: str | None = None
) -> list[sqlite3.Row]:
    """还没被模型看过的记录。

    传 day 就只取那一天的 —— 收口是针对某一天的，
    不能把其他天的记录一起标成已处理。
    """
    if day is None:
        return conn.execute(
            "SELECT * FROM entries WHERE processed = 0 ORDER BY ts ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM entries WHERE processed = 0 AND day = ? ORDER BY ts ASC LIMIT ?",
        (day, limit),
    ).fetchall()


def mark_processed(conn: sqlite3.Connection, ids: list[int]) -> None:
    if not ids:
        return
    conn.executemany(
        "UPDATE entries SET processed = 1 WHERE id = ?", [(i,) for i in ids]
    )
    conn.commit()


def as_dict(row: sqlite3.Row) -> dict:
    return dict(row)


__all__ = [
    "EntryError",
    "MAX_IMAGE_BYTES",
    "as_dict",
    "create_image",
    "create_text",
    "decode_image",
    "list_by_day",
    "mark_processed",
    "save_image_bytes",
    "snapshot_bytes",
    "unprocessed",
]
