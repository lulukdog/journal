"""配置。优先级：真实环境变量 > .env 文件 > 默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
# 两个模型任选：
#   deepseek-flash    → DeepSeek-V4.1-Flash，100 万上下文，**能看图**
#   deepseek-v4-pro   → DeepSeek-V4-Pro，100 万上下文，**不能看图**（纯文本）
# 默认两个都用 V4.1：图片要真的发给模型才有意义，
# 而「问它」和每日卡片都会把当天照片一并送过去。
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_MODEL_DEEP = "deepseek-v4-flash"
DEFAULT_DSH_HOME = "/var/lib/journal/dsh-home"
DEFAULT_DSH_PROFILE = "sdk-minimal"
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
    dsh_home: Path
    dsh_profile: str
    use_dsh: bool
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

    use_dsh = get("JOURNAL_USE_DSH", "1").lower() not in {"0", "false", "no"}

    return Settings(
        db_path=Path(get("JOURNAL_DB_PATH", DEFAULT_DB_PATH)),
        media_dir=Path(get("JOURNAL_MEDIA_DIR", DEFAULT_MEDIA_DIR)),
        api_key=get("JOURNAL_LLM_API_KEY"),
        base_url=get("JOURNAL_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=get("JOURNAL_MODEL", DEFAULT_MODEL),
        model_deep=get("JOURNAL_MODEL_DEEP", DEFAULT_MODEL_DEEP),
        dsh_home=Path(get("JOURNAL_DSH_HOME", DEFAULT_DSH_HOME)),
        dsh_profile=get("JOURNAL_DSH_PROFILE", DEFAULT_DSH_PROFILE),
        use_dsh=use_dsh,
        app_token=get("JOURNAL_APP_TOKEN"),
        day_boundary_hour=boundary,
    )
