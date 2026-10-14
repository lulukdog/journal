import pytest

from app import ask, llm, router


class Settings:
    model = "test-model"
    model_deep = "test-model"
    media_dir = None


@pytest.mark.asyncio
async def test_explicit_answer_always_answers(monkeypatch, conn):
    async def fake_chat(settings, messages, **kwargs):
        return "这是回答。"

    monkeypatch.setattr(llm, "chat", fake_chat)
    result = await ask.answer(Settings(), conn, "随便记录一下", "2026-10-14")
    assert result == "这是回答。"
