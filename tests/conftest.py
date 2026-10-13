import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db as db_mod  # noqa: E402

# 1x1 透明 PNG，用来测图片路径
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _clear_login_failures():
    """限流计数是模块级的，测试之间会互相污染 —— 每个测试前后都清掉。"""
    from app import auth

    auth._failures.clear()
    yield
    auth._failures.clear()


@pytest.fixture
def conn(tmp_path):
    c = db_mod.connect(tmp_path / "journal.db")
    db_mod.init_db(c)
    yield c
    c.close()


@pytest.fixture
def media_dir(tmp_path):
    d = tmp_path / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d
