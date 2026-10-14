"""零一万物（OpenAI 兼容）客户端。

所有失败都归一化成 LLMError，带一句能直接给用户看的中文。
上层不允许把 httpx 的原始异常冒到接口上（Review Focus #2）。
"""

from __future__ import annotations

import base64
import io
import json
import re
import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx

MAX_EDGE = 1568


class LLMError(RuntimeError):
    """可以直接展示给用户的中文错误。"""


def compress_for_model(data: bytes, max_edge: int = MAX_EDGE) -> bytes:
    """把图缩到长边 <= max_edge，输出 JPEG。失败则原样返回。"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > max_edge and longest > 0:
            scale = max_edge / longest
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return data


def image_data_url(path: Path | str, max_edge: int = MAX_EDGE) -> str:
    raw = Path(path).read_bytes()
    squeezed = compress_for_model(raw, max_edge)
    return "data:image/jpeg;base64," + base64.b64encode(squeezed).decode()


def extract_json(text: str) -> Any:
    """从模型回复里抠出第一个 JSON 对象。抠不到抛 LLMError。"""
    if not text:
        raise LLMError("模型没有返回内容。")
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("模型的回复不是我能解析的格式。")
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError("模型的回复不是我能解析的格式。") from exc


async def _post(settings, payload: dict, timeout: float) -> dict:
    url = f"{settings.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMError("模型响应太慢了，稍后再试一次。") from exc
    except httpx.HTTPError as exc:
        raise LLMError("连不上模型服务，稍后再试一次。") from exc

    if response.status_code >= 400:
        detail = response.text[:200]
        raise LLMError(f"模型返回了错误（{response.status_code}）：{detail}")

    try:
        return response.json()
    except ValueError as exc:
        raise LLMError("模型返回的内容不是合法 JSON。") from exc


def _content_of(body: dict) -> tuple[str, str | None]:
    """取出 (正文, finish_reason)。结构不对就报 LLMError。"""
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回的结构不对，没有拿到内容。") from exc
    content = message.get("content")
    return (content if isinstance(content, str) else ""), choice.get("finish_reason")


def _harness_input(messages: list[dict]) -> str | list[dict]:
    """把现有 OpenAI messages 转成 DSH 的 prompt 内容块。"""
    blocks = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str):
            blocks.append({"type": "text", "text": f"[{role}]\n{content}"})
            continue
        for part in content:
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": f"[{role}]\n{part.get('text', '')}"})
            elif part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                match = re.match(r"data:([^;]+);base64,(.+)", url, re.S)
                if match:
                    blocks.append({
                        "type": "image",
                        "data": match.group(2),
                        "mimeType": match.group(1),
                    })
    return blocks[0]["text"] if len(blocks) == 1 and blocks[0]["type"] == "text" else blocks


async def _chat_dsh(settings, messages: list[dict], model: str, max_tokens: int, timeout: float) -> str:
    """通过 DeepSeek Harness Python SDK 调用 dsh --profile sdk。"""
    def run():
        try:
            from deepseek_harness import DeepSeekHarness
        except ImportError as exc:
            raise LLMError("服务器没有安装 DeepSeek Harness SDK。") from exc
        try:
            with DeepSeekHarness(
                provider="deepseek-official",
                model=model,
                max_tokens=max_tokens,
                base_url=settings.base_url,
                api_key=settings.api_key,
                dsh_home=str(settings.dsh_home),
                profile=settings.dsh_profile,
                request_timeout_seconds=timeout,
            ) as harness:
                # 每次调用使用独立 session；上下文已由日记自己组装，不能让 Harness
                # 把上一次请求的隐式会话也混进来。
                result = harness.run(
                    _harness_input(messages),
                    session_id=f"journal-{uuid.uuid4().hex}",
                )
                return result.final_response or ""
        except Exception as exc:
            raise LLMError(f"DeepSeek Harness 调用失败：{exc}") from exc

    return await asyncio.to_thread(run)


async def chat(
    settings,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4000,
    timeout: float = 60.0,
) -> str:
    """调一次模型，返回正文。

    推理模型会拿 token 预算去思考，思考完没剩多少给正文，于是正文为空、
    finish_reason 是 length。这种不是错误，是预算不够 —— 自动加倍重试。
    调参每处都猜不准，所以把这件事交给这里做。
    """
    if not settings.api_key:
        raise LLMError("服务器上没有配置模型密钥，先看看 .env。")

    if getattr(settings, "use_dsh", False):
        return await _chat_dsh(settings, messages, model or settings.model, max_tokens, timeout)

    budget = max_tokens
    for attempt in range(3):
        body = await _post(
            settings,
            {
                "model": model or settings.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": budget,
            },
            timeout,
        )
        content, finish = _content_of(body)
        if content.strip():
            return content

        if finish == "length" and attempt < 2:
            budget = min(budget * 3, 16000)
            continue
        if finish == "length":
            raise LLMError(
                "模型把 token 预算全花在思考上了，没能输出正文。换个模型试试。"
            )
        raise LLMError("模型返回了空内容。")

    raise LLMError("模型返回了空内容。")


async def chat_with_images(
    settings,
    messages: list[dict],
    *,
    model: str | None = None,
    **kwargs: Any,
) -> str:
    """带图片调模型。名字保留是为了调用处读起来清楚。

    刻意不做「模型不收图片就去掉图片重试」的降级：
    静默降级是最坏的 —— 模型哪天开始读不懂你的照片，你永远发现不了。
    现在两个模型都是 V4.1（能看图），不能看就让它直接报错。
    """
    return await chat(settings, messages, model=model, **kwargs)
