"""认证：一个长随机口令换一个 HttpOnly cookie。

第一版没有 HTTPS（没有域名），所以口令是唯一的门。
见 spec 5.8 —— 这是记录在案的已知妥协。
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Cookie, HTTPException, Request, Response, status

COOKIE_NAME = "journal_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 365  # 一年，自己用的东西不用老登录


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
