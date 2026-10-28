"""「问它」—— 明确提问，必须回答。"""

from __future__ import annotations

import sqlite3

from . import companion, llm, memory


async def answer(settings, conn: sqlite3.Connection, question: str, day: str) -> str:
    text = (question or "").strip()
    if not text:
        raise llm.LLMError("你想问什么？先说一句。")

    messages = memory.build_ask_messages(
        conn, text, day, media_dir=settings.media_dir
    )
    note = companion.ask_note(text)
    if note:
        messages.insert(1, {"role": "system", "content": note})
    reply = await llm.chat_with_images(
        settings,
        messages,
        model=settings.model_deep,
        temperature=0.6,
        timeout=120.0,
    )
    reply = reply.strip()
    memory.record_utterance(conn, day, "ask", reply)
    return reply
