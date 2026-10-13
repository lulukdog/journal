import base64
import io
import json

import httpx
import pytest

from app import llm


class StubSettings:
    api_key = "k"
    base_url = "http://example.invalid/v1"
    model = "m"


def _thinking_transport(calls: list, budgets: list, then_content: str | None = None):
    """模拟推理模型：把 token 预算全花在思考上，正文为空。

    then_content 不为空时，第二次开始返回正文。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            budgets.append(json.loads(request.content)["max_tokens"])
        except Exception:
            pass
        calls.append(1)
        if then_content is not None and len(calls) > 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": then_content}, "finish_reason": "stop"}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "让我想想…",
                        },
                        "finish_reason": "length",
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def stub_transport(monkeypatch):
    """把 httpx.AsyncClient 换成挂了 MockTransport 的版本。"""
    state = {"calls": [], "budgets": []}
    real_client = httpx.AsyncClient

    def install(transport):
        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(llm.httpx, "AsyncClient", fake_client)

    state["install"] = install
    return state


# ---------- JSON 抠取 ----------


def test_extract_json_handles_plain_object():
    assert llm.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_handles_fenced_block():
    raw = '```json\n{"mood": -1, "mood_label": "有点钝"}\n```'
    assert llm.extract_json(raw)["mood"] == -1


def test_extract_json_handles_prose_around_object():
    assert llm.extract_json('好的，结果如下：{"mood": 1} 就是这样') == {"mood": 1}


def test_extract_json_rejects_non_json():
    with pytest.raises(llm.LLMError):
        llm.extract_json("抱歉，我不能回答这个问题。")


def test_extract_json_rejects_empty():
    with pytest.raises(llm.LLMError):
        llm.extract_json("")


# ---------- 图片压缩 ----------


def test_compress_for_model_shrinks_large_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), (10, 20, 30)).save(buf, format="JPEG")
    out = llm.compress_for_model(buf.getvalue(), max_edge=1568)
    assert len(out) < len(buf.getvalue())
    assert out[:2] == b"\xff\xd8", "输出应当是 JPEG"


def test_compress_for_model_passes_through_garbage():
    assert llm.compress_for_model(b"not an image") == b"not an image"


def test_image_data_url_is_base64_jpeg(tmp_path):
    from PIL import Image

    path = tmp_path / "a.png"
    Image.new("RGB", (20, 20), (200, 10, 10)).save(path, format="PNG")
    url = llm.image_data_url(path)
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1])[:2] == b"\xff\xd8"


# ---------- 错误归一化 ----------


@pytest.mark.asyncio
async def test_chat_explains_truncated_reasoning(stub_transport):
    """推理模型把预算花在思考上时要说人话，不能只报「空内容」。"""
    stub_transport["install"](
        _thinking_transport(stub_transport["calls"], stub_transport["budgets"])
    )
    with pytest.raises(llm.LLMError) as exc:
        await llm.chat(StubSettings(), [{"role": "user", "content": "嗨"}])
    assert "思考" in str(exc.value)
    assert len(stub_transport["calls"]) == 3, "应当重试三次再放弃"


@pytest.mark.asyncio
async def test_chat_retries_with_bigger_budget(stub_transport):
    """先花光预算、重试后成功 —— 第一次的空正文不该被当成失败。"""
    stub_transport["install"](
        _thinking_transport(
            stub_transport["calls"], stub_transport["budgets"], then_content='{"mood": 1}'
        )
    )
    out = await llm.chat(
        StubSettings(), [{"role": "user", "content": "嗨"}], max_tokens=400
    )
    assert out == '{"mood": 1}'
    assert stub_transport["budgets"][:2] == [400, 1200], "第二次的预算应当放大"


@pytest.mark.asyncio
async def test_chat_normalises_http_error(stub_transport):
    """Review Focus #2：模型报错要变成 LLMError，不能让 httpx 异常冒上去。"""
    stub_transport["install"](
        httpx.MockTransport(lambda request: httpx.Response(403, text="RBAC: access denied"))
    )
    with pytest.raises(llm.LLMError) as exc:
        await llm.chat(StubSettings(), [{"role": "user", "content": "嗨"}])
    assert "403" in str(exc.value)
    assert "RBAC" in str(exc.value)


@pytest.mark.asyncio
async def test_chat_normalises_timeout(stub_transport):
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    stub_transport["install"](httpx.MockTransport(handler))
    with pytest.raises(llm.LLMError) as exc:
        await llm.chat(StubSettings(), [{"role": "user", "content": "嗨"}])
    assert "慢" in str(exc.value)


@pytest.mark.asyncio
async def test_chat_normalises_non_json_body(stub_transport):
    stub_transport["install"](
        httpx.MockTransport(lambda request: httpx.Response(200, text="<html>502</html>"))
    )
    with pytest.raises(llm.LLMError) as exc:
        await llm.chat(StubSettings(), [{"role": "user", "content": "嗨"}])
    assert "JSON" in str(exc.value)


@pytest.mark.asyncio
async def test_chat_without_api_key_fails_fast():
    class NoKey:
        api_key = ""
        base_url = "http://example.invalid/v1"
        model = "m"

    with pytest.raises(llm.LLMError) as exc:
        await llm.chat(NoKey(), [{"role": "user", "content": "嗨"}])
    assert "密钥" in str(exc.value)
