"""认证：一个长随机口令换一个 HttpOnly cookie。

第一版没有 HTTPS（没有域名），所以口令是唯一的门。
见 spec 5.8 —— 这是记录在案的已知妥协。
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Cookie, HTTPException, Request, Response, status

COOKIE_NAME = "journal_session"
# 400 天 —— Chrome 对 cookie 的硬上限。
# 自己用的东西，登录一次就该一直记得。
SESSION_MAX_AGE = 60 * 60 * 24 * 400

# ---------- 登录限流 ----------
# 口令可能存在很弱（用户自己选的口令，比如六位数字），
# 所以真正的防线在这里：失败次数一多就锁一段时间。
# 单进程内存计数，重启就清 —— 对单人应用足够了。

LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 15 * 60
_MAX_TRACKED = 2048
_failures: dict[str, deque[float]] = defaultdict(deque)


def _now() -> float:
    return time.monotonic()


def _prune(bucket: deque[float], now: float) -> None:
    while bucket and now - bucket[0] > LOCKOUT_SECONDS:
        bucket.popleft()


def record_failure(ip: str) -> None:
    now = _now()
    bucket = _failures[ip]
    _prune(bucket, now)
    bucket.append(now)
    if len(_failures) > _MAX_TRACKED:
        for key in list(_failures)[: _MAX_TRACKED // 4]:
            _failures.pop(key, None)


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def lockout_remaining(ip: str) -> int:
    """还要等几秒才能再试。0 表示没被锁。"""
    now = _now()
    bucket = _failures.get(ip)
    if not bucket:
        return 0
    _prune(bucket, now)
    if len(bucket) < LOCKOUT_AFTER:
        return 0
    return max(1, int(LOCKOUT_SECONDS - (now - bucket[0])))


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def verify_token(candidate: str | None, expected: str) -> bool:
    """常数时间比较。空串、None、长度不符一律为假。"""
    if not candidate or not expected:
        return False
    try:
        return hmac.compare_digest(candidate, expected)
    except TypeError:
        return False


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # 无 HTTPS，见 spec 5.8
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def require_auth(request: Request) -> None:
    """FastAPI 依赖。未登录返回 401。"""
    settings = getattr(request.app.state, "settings", None)
    expected = settings.app_token if settings else ""
    candidate = request.cookies.get(COOKIE_NAME)
    if not verify_token(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录"
        )


__all__ = [
    "COOKIE_NAME",
    "SESSION_MAX_AGE",
    "clear_session_cookie",
    "new_token",
    "require_auth",
    "set_session_cookie",
    "verify_token",
    "Cookie",
]
