"""在保存后判断一句话是普通记录，还是在寻求回答。"""

from __future__ import annotations

from dataclasses import dataclass

from . import llm


DSH_RULES = """\
回答和总结内部都按 DSH 组织资料：
- Data：只列记录中明确存在的事实、数字和原话。
- State：说明这些事实当前形成的处境或矛盾，但不要把猜测写成事实。
- Hypothesis：给出暂时判断，并明确这是判断；资料不足就先问一个问题。
不要输出思考过程，不要展示隐藏推理链，只输出最终结果。
"""

ROUTER_SYSTEM = f"""\
你是日记入口的意图分类器。
判断用户这句话是：
- question：正在请求判断、建议、解释、比较、确认，或明显想让你回应
- record：只是记下事实、感受、进展、照片说明或一句没有请求的自言自语

只输出 JSON，不要输出其它文字：
{{"intent":"question"或"record","reason":"不超过20字"}}

问号不是充分条件：反问、感叹或随手写下的疑问也可以是 record。
不能确定时选 record，宁可安静记录，不要打断。
{DSH_RULES}
"""


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    reason: str = ""


async def classify(settings, conn, text: str, day: str) -> RouteDecision:
    """用一次短分类调用决定是否自动回答；分类失败默认普通记录。"""
    try:
        raw = await llm.chat(
            settings,
            [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": text.strip()},
            ],
            temperature=0,
            max_tokens=180,
            timeout=30,
        )
        data = llm.extract_json(raw)
    except llm.LLMError:
        return RouteDecision("record", "分类失败，先记录")

    intent = data.get("intent") if isinstance(data, dict) else None
    if intent not in {"record", "question"}:
        return RouteDecision("record", "无法确定，先记录")
    return RouteDecision(intent, str(data.get("reason") or "")[:20])
