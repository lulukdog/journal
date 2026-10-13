"""配置。优先级：真实环境变量 > .env 文件 > 默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://openapi.lingyiwanwu.net/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_MODEL_DEEP = "gpt-5.6-luna"
DEFAULT_DB_PATH = "/var/lib/journal/journal.db"
DEFAULT_MEDIA_DIR = "/var/lib/journal/media"
DEFAULT_DAY_BOUNDARY_HOUR = 4


@dataclass(frozen=True)
class Settings:
    db_path: Path
    media_dir: Path
    api_key: str
    base_url: str
    model: str
    model_deep: str
    app_token: str
    day_boundary_hour: int

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.app_token)


def read_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略空行和 # 注释，值可带引号。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_settings(env_file: Path | str | None = None) -> Settings:
    data: dict[str, str] = {}
    if env_file is not None:
        data.update(read_env_file(Path(env_file)))
    data.update(os.environ)

    def get(key: str, default: str = "") -> str:
        return data.get(key, default).strip() or default

    raw_hour = get("JOURNAL_DAY_BOUNDARY_HOUR", str(DEFAULT_DAY_BOUNDARY_HOUR))
    try:
        boundary = int(raw_hour)
    except ValueError:
        boundary = DEFAULT_DAY_BOUNDARY_HOUR
    if not 0 <= boundary <= 23:
        boundary = DEFAULT_DAY_BOUNDARY_HOUR

    return Settings(
        db_path=Path(get("JOURNAL_DB_PATH", DEFAULT_DB_PATH)),
        media_dir=Path(get("JOURNAL_MEDIA_DIR", DEFAULT_MEDIA_DIR)),
        api_key=get("LINGYI_API_KEY"),
        base_url=get("LINGYI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=get("JOURNAL_MODEL", DEFAULT_MODEL),
        model_deep=get("JOURNAL_MODEL_DEEP", DEFAULT_MODEL_DEEP),
        app_token=get("JOURNAL_APP_TOKEN"),
        day_boundary_hour=boundary,
    )
