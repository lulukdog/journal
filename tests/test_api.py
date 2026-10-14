"""接口层测试。用 TestClient 走真实的 lifespan。"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app import llm, router
from app import main as main_mod
from tests.conftest import TINY_PNG_B64

TOKEN = "test-token-please-change"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("JOURNAL_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("JOURNAL_APP_TOKEN", TOKEN)
    monkeypatch.setenv("JOURNAL_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("JOURNAL_DAY_BOUNDARY_HOUR", "4")
    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture
def authed(client):
    response = client.post("/api/login", json={"token": TOKEN})
    assert response.status_code == 200
    return client


# ---------- 认证 ----------


def test_login_rejects_wrong_token(client):
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401


def test_login_locks_out_after_repeated_failures(client):
    """口令可能很短，所以真正的防线是限流。"""
    from app import auth

    for _ in range(auth.LOCKOUT_AFTER):
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401

    locked = client.post("/api/login", json={"token": "wrong"})
    assert locked.status_code == 429
    assert "分钟" in locked.json()["detail"]

    # 锁定期内就算口令对了也不放行
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 429


def test_successful_login_resets_the_counter(client):
    from app import auth

    for _ in range(auth.LOCKOUT_AFTER - 1):
        client.post("/api/login", json={"token": "wrong"})
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 200

    # 计数器清了，又可以错几次
    for _ in range(auth.LOCKOUT_AFTER - 1):
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401


def test_state_requires_auth(client):
    assert client.get("/api/state").status_code == 401


def test_ask_requires_auth(client):
    assert client.post("/api/ask", json={"text": "嗨"}).status_code == 401


def test_entries_requires_auth(client):
    assert client.post("/api/entries", json={"text": "嗨"}).status_code == 401


def test_media_requires_auth(client):
    """Review Focus #4：图片比文字更敏感，这是最容易漏的一道门。"""
    assert client.get("/media/2026/10/whatever.png").status_code == 401


def test_index_is_public(client):
    assert client.get("/").status_code in (200, 500)


# ---------- 首次使用 ----------


def test_first_use_never_blank(authed):
    """Review Focus #5：全新库也要 200，并且指向第一道访谈题。"""
    response = authed.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["card"]["entry_count"] == 0
    assert body["onboarding"]["done"] is False
    assert body["onboarding"]["question"]["key"] == "income_source"
    assert body["configured"] is True


def test_state_rejects_bad_day(authed):
    assert authed.get("/api/state?day=not-a-date").status_code == 400


# ---------- 记录 ----------


def test_record_text(authed):
    response = authed.post("/api/entries", json={"text": "今天真累"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] > 0

    state = authed.get(f"/api/state?day={body['day']}").json()
    assert len(state["entries"]) == 1
    assert state["card"]["entry_count"] == 1


def test_blank_record_rejected(authed):
    """Review Focus #1：空输入返回 400 而不是 500，也不产生记录。"""
    response = authed.post("/api/entries", json={"text": "   "})
    assert response.status_code == 400
    assert "空" in response.json()["detail"]


def test_record_image(authed, tmp_path):
    response = authed.post(
        "/api/entries",
        json={"caption": "窗外", "image": f"data:image/png;base64,{TINY_PNG_B64}"},
    )
    assert response.status_code == 200
    day = response.json()["day"]

    state = authed.get(f"/api/state?day={day}").json()
    entry = state["entries"][0]
    assert entry["kind"] == "image"
    assert entry["content"] == "窗外"

    media = authed.get(f"/media/{entry['image_path']}")
    assert media.status_code == 200
    assert media.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_bad_image_gives_400_not_500(authed):
    response = authed.post(
        "/api/entries", json={"image": "data:image/png;base64,%%%%%%%%"}
    )
    assert response.status_code == 400


def test_media_path_traversal_blocked(authed):
    assert authed.get("/media/../../etc/passwd").status_code in (403, 404)


# ---------- 智能记录 ----------


def test_capture_records_plain_statement_without_reply(authed, monkeypatch):
    async def record(settings, conn, text, day):
        return router.RouteDecision("record", "只是记录")

    monkeypatch.setattr(router, "classify", record)
    response = authed.post("/api/capture", json={"text": "今天下雨了"})
    assert response.status_code == 200
    assert response.json()["intent"] == "record"
    assert response.json()["reply"] is None
    state = authed.get(f"/api/state?day={response.json()['day']}").json()
    assert any(e["content"] == "今天下雨了" for e in state["entries"])
    assert state["utterances"] == []


def test_capture_saves_question_then_answers(authed, monkeypatch):
    async def question(settings, conn, text, day):
        return router.RouteDecision("question", "在求判断")

    async def reply(settings, conn, text, day):
        return "先别急着决定，先看外面的行情。"

    monkeypatch.setattr(router, "classify", question)
    monkeypatch.setattr(main_mod.ask_mod, "answer", reply)
    response = authed.post("/api/capture", json={"text": "我是不是该换工作？"})
    assert response.status_code == 200
    assert response.json()["intent"] == "question"
    assert response.json()["reply"]
    state = authed.get(f"/api/state?day={response.json()['day']}").json()
    assert state["entries"][-1]["content"] == "我是不是该换工作？"
    assert response.json()["reply"] == "先别急着决定，先看外面的行情。"


# ---------- 问它 ----------


def test_ask_returns_reply(authed, monkeypatch):
    async def fake_chat(settings, messages, **kwargs):
        return "你上个月说过想动一动。要不要先把手上这笔钱算清楚？"

    monkeypatch.setattr(llm, "chat", fake_chat)
    response = authed.post("/api/ask", json={"text": "我该不该换工作"})
    assert response.status_code == 200
    assert "算清楚" in response.json()["reply"]


def test_ask_gives_502_with_readable_message(authed, monkeypatch):
    """Review Focus #2：模型挂了要给一句人话，不能白屏。"""

    async def dead_chat(settings, messages, **kwargs):
        raise llm.LLMError("模型响应太慢了，稍后再试一次。")

    monkeypatch.setattr(llm, "chat", dead_chat)
    response = authed.post("/api/ask", json={"text": "我该不该换工作"})
    assert response.status_code == 502
    assert "太慢" in response.json()["detail"]


def test_ask_with_blank_question(authed, monkeypatch):
    async def fake_chat(settings, messages, **kwargs):
        return "追问一句"

    monkeypatch.setattr(llm, "chat", fake_chat)
    response = authed.post("/api/ask", json={"text": ""})
    assert response.status_code == 502
    assert "先说一句" in response.json()["detail"]


# ---------- 引导访谈 ----------


def test_onboarding_flow(authed):
    body = authed.post(
        "/api/onboarding/answer", json={"key": "income_source", "answer": "工资"}
    ).json()
    assert body["answered"] == 1
    assert body["question"]["key"] == "monthly_expense"

    body = authed.post(
        "/api/onboarding/answer", json={"key": "monthly_expense", "skipped": True}
    ).json()
    assert body["answered"] == 2
    assert body["question"]["key"] == "family_burden"
