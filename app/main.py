"""FastAPI 应用。所有路由都在这。

认证：除了 /api/login 和 / 之外，全部要求登录。
/media/<path> 也要登录 —— 图片比文字更敏感，这是最容易漏的一道门。
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ask as ask_mod
from . import auth, config, db, llm, memory, onboarding
from . import days as days_mod
from . import entries as entries_mod

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = config.load_settings(ROOT / ".env")
    app.state.settings = settings
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    app.state.conn = conn
    try:
        yield
    finally:
        conn.close()


app = FastAPI(title="journal", lifespan=lifespan)


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def get_settings(request: Request):
    return request.app.state.settings


def today(request: Request) -> str:
    settings = request.app.state.settings
    return days_mod.day_of(datetime.now(), settings.day_boundary_hour)


class LoginBody(BaseModel):
    token: str = ""


class EntryBody(BaseModel):
    text: str | None = None
    caption: str | None = None
    image: str | None = None


class AskBody(BaseModel):
    text: str = ""


class OnboardingBody(BaseModel):
    key: str
    answer: str | None = None
    skipped: bool = False


# ---------- 认证 ----------


@app.post("/api/login")
async def login(request: Request, body: LoginBody, response: Response):
    settings = get_settings(request)
    if not settings.app_token:
        raise HTTPException(500, "服务器没有设置口令。")
    if not auth.verify_token(body.token, settings.app_token):
        raise HTTPException(401, "口令不对。")
    auth.set_session_cookie(response, settings.app_token)
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    return {"ok": auth.verify_token(
        request.cookies.get(auth.COOKIE_NAME), get_settings(request).app_token
    )}


# ---------- 状态 ----------


@app.get("/api/state", dependencies=[Depends(auth.require_auth)])
async def state(request: Request, day: str | None = None):
    conn = get_conn(request)
    target = day or today(request)
    if not _valid_day(target):
        raise HTTPException(400, "日期格式不对。")

    days_mod.ensure(conn, target)
    card = days_mod.as_dict(days_mod.get(conn, target))
    rows = entries_mod.list_by_day(conn, target)
    utterances = memory.utterances_for_day(conn, target)

    return {
        "day": target,
        "today": today(request),
        "card": card,
        "entries": [entries_mod.as_dict(r) for r in rows],
        "utterances": [dict(r) for r in utterances],
        "onboarding": onboarding.status(conn),
        "configured": get_settings(request).is_configured,
    }


def _valid_day(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# ---------- 记录 ----------


@app.post("/api/entries", dependencies=[Depends(auth.require_auth)])
async def create_entry(request: Request, body: EntryBody):
    conn = get_conn(request)
    settings = get_settings(request)
    now = datetime.now()
    boundary = settings.day_boundary_hour

    try:
        if body.image:
            data, ext = entries_mod.decode_image(body.image)
            rel = entries_mod.save_image_bytes(settings.media_dir, data, ext, now)
            entry_id = entries_mod.create_image(
                conn, rel, caption=body.caption, ts=now, boundary_hour=boundary
            )
        else:
            entry_id = entries_mod.create_text(
                conn, body.text or "", ts=now, boundary_hour=boundary
            )
    except entries_mod.EntryError as exc:
        raise HTTPException(400, str(exc)) from exc

    day = days_mod.day_of(now, boundary)
    return {"id": entry_id, "day": day}


# ---------- 问它 ----------


@app.post("/api/ask", dependencies=[Depends(auth.require_auth)])
async def ask(request: Request, body: AskBody):
    conn = get_conn(request)
    settings = get_settings(request)
    try:
        reply = await ask_mod.answer(settings, conn, body.text, today(request))
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"reply": reply}


# ---------- 引导访谈 ----------


@app.get("/api/onboarding", dependencies=[Depends(auth.require_auth)])
async def onboarding_state(request: Request):
    return onboarding.status(get_conn(request))


@app.post("/api/onboarding/answer", dependencies=[Depends(auth.require_auth)])
async def onboarding_answer(request: Request, body: OnboardingBody):
    conn = get_conn(request)
    onboarding.record_answer(conn, body.key, body.answer, skipped=body.skipped)
    return onboarding.status(conn)


# ---------- 图片 ----------


@app.get("/media/{path:path}", dependencies=[Depends(auth.require_auth)])
async def media(request: Request, path: str):
    root = Path(get_settings(request).media_dir).resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(403, "不允许。")
    if not target.is_file():
        raise HTTPException(404, "找不到这张图片。")
    return FileResponse(target)


# ---------- 页面 ----------


@app.get("/")
async def index():
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        return JSONResponse({"error": "前端还没装好。"}, status_code=500)
    return FileResponse(page)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
