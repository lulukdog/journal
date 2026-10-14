import pytest

from app import llm, router


class Settings:
    model = "test-model"


@pytest.mark.asyncio
async def test_classify_question_json(monkeypatch):
    async def fake_chat(settings, messages, **kwargs):
        return '{"intent":"question","reason":"在寻求判断"}'

    monkeypatch.setattr(llm, "chat", fake_chat)
    decision = await router.classify(Settings(), None, "我是不是该辞职？", "2026-10-14")
    assert decision.intent == "question"
    assert decision.reason == "在寻求判断"


@pytest.mark.asyncio
async def test_classify_bad_json_defaults_to_record(monkeypatch):
    async def fake_chat(settings, messages, **kwargs):
        return "不是 JSON"

    monkeypatch.setattr(llm, "chat", fake_chat)
    decision = await router.classify(Settings(), None, "今天下雨了", "2026-10-14")
    assert decision.intent == "record"


def test_dsh_prompt_is_explicit_but_not_chain_of_thought():
    assert "Data" in router.DSH_RULES
    assert "State" in router.DSH_RULES
    assert "Hypothesis" in router.DSH_RULES
    assert "不要输出思考过程" in router.DSH_RULES
