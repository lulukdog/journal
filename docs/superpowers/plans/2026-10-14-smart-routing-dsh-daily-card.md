# 智能判断、DSH 结构与丰富日卡片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让“记一笔”自动判断是否需要回答，用 DSH 结构约束回答和总结，并把日卡片从标签扩展为可读的事实、矛盾、判断和行动摘要。

**Architecture:** 新增智能路由层：普通记录只落库，疑问先落库再调用回答链；明确点击“问它”仍然强制回答。DSH 作为提示词中的结构化工作协议（Data / State / Hypothesis），不要求模型输出隐性推理过程。每日摘要增加 JSON 字段存储丰富内容，旧字段继续兼容，前端渐进展示。

**Tech Stack:** Python 3.10, FastAPI, SQLite, DeepSeek OpenAI-compatible API, 原生 JavaScript/CSS。

**Spec:** 本计划即为已确认的实现设计；沿用 `docs/superpowers/specs/2026-10-13-daily-journal-design.md` 的隐私、非诊断、先问再判断和凌晨 4 点边界约束。

## Global Constraints

- 普通记录不显示模型回复；只有模型判断为问题或用户明确点击“问它”时回复。
- 用户写下的原文必须先保存，模型失败不能丢记录。
- 不输出心理诊断、抑郁风险评分或临床标签。
- DSH 只作为结构化提示协议，不要求或展示模型隐藏思维链。
- 日总结只描述记录中的事实，判断必须标记为暂时判断，建议最多一个。
- SQLite 迁移必须兼容已有生产数据库，不能删除旧字段或旧数据。
- 保持现有图片全量发送、不静默去图、登录和日期选择器行为。

## Review Focus

- 一句话含有问号但只是反问或感叹时，不应机械回答。
- 模型路由失败时，原文仍然保存，接口返回可理解的降级状态。
- 老数据库没有新摘要字段时，首页仍可正常打开。
- 丰富摘要缺字段或返回坏 JSON 时，旧摘要和标签仍可展示。
- DSH 不得诱导模型把猜测写成事实。

---

### Task 1: 智能路由与 DSH 提示协议

**Files:**
- Modify: `app/prompts.py`
- Create/modify: `app/router.py`（智能判断与结构化结果解析）
- Modify: `app/ask.py`
- Test: `tests/test_router.py`, `tests/test_ask.py`

**Interfaces:**
- `router.classify(settings, conn, text, day) -> RouteDecision`
- `RouteDecision.intent` 为 `record` 或 `question`，可带 `reason`
- `ask.answer(..., mode="explicit")` 继续支持强制回答

- [ ] 写测试：严格解析 record/question JSON；坏 JSON 默认 record；分类模型失败抛出可处理的 LLMError；DSH 提示包含 Data/State/Hypothesis 且不要求输出思维链。
- [ ] 运行路由测试，确认先失败。
- [ ] 实现最小路由器：用短 JSON 分类调用；仅根据输入和必要上下文判断，不让分类调用生成回答。
- [ ] 将 ASK_SYSTEM 改为 DSH 输出纪律：具体事实、当前状态、暂时判断，资料不足先问一个问题。
- [ ] 运行路由和问答测试，确认通过。

### Task 2: API 与前端智能记录

**Files:**
- Modify: `app/main.py`, `app/static/app.js`, `app/static/index.html`
- Test: `tests/test_api.py`

**Interfaces:**
- 新增 `POST /api/capture`，先创建 entry，再返回 `{day, id, intent, reply}`。
- `intent=record` 时 `reply` 为 null；`intent=question` 时保存回答并返回回复。
- 原 `/api/entries` 和 `/api/ask` 保持兼容。

- [ ] 写测试：普通陈述只保存无回答；问题先保存后回答；模型路由失败仍返回已保存记录状态；明确 `/api/ask` 仍强制回答。
- [ ] 运行 API 测试，确认先失败。
- [ ] 实现 capture 流程，保证 entry 写库在模型调用之前。
- [ ] 将“记一笔”调用 capture；普通记录显示“记下了”，自动问题显示回答；保留“问它”按钮作为明确强制回答入口。
- [ ] 运行 API 全量测试并手测 UI。

### Task 3: 丰富日摘要数据模型与生成

**Files:**
- Modify: `app/db.py`, `app/days.py`, `app/dayclose.py`, `app/prompts.py`
- Test: `tests/test_days.py`, `tests/test_dayclose.py`

**Interfaces:**
- `days.summary` 为兼容旧数据库新增的 TEXT JSON 字段。
- `days.save_summary(..., details: dict | None = None)`。
- `days.as_dict()` 返回 `summary` 对象。

摘要 JSON 结构：`overview`, `facts`, `tension`, `hypothesis`, `next_step`, `open_loop`；其中 facts/open_loop 是字符串数组，其余是短字符串。

- [ ] 写迁移、读写和坏 JSON 测试；写摘要提示词输出结构测试。
- [ ] 运行测试确认失败。
- [ ] 实现启动时 `ALTER TABLE days ADD COLUMN summary TEXT` 的幂等迁移。
- [ ] 让日收口生成 DSH 摘要：Data 用 facts，State 用 tension，Hypothesis 用 hypothesis；不支持的内容用空数组/空字符串。
- [ ] 保留现有 mood、headline、keywords，旧摘要失败时不覆盖已有详情。
- [ ] 运行全量测试。

### Task 4: 丰富日卡片 UI

**Files:**
- Modify: `app/static/app.js`, `app/static/style.css`
- Test: `tests/test_api.py`（状态结构）以及浏览器手测

- [ ] 写状态接口测试，确认 summary 字段向前兼容。
- [ ] 实现日卡片分区：概览、事实、卡点、暂时判断、下一步、未完问题、标签。
- [ ] 空字段不渲染；旧卡片仍按原布局显示。
- [ ] 移动端检查折叠/高度，避免日卡片挤满屏幕；先展示核心内容，细节可折叠。
- [ ] 运行 123+ 测试并通过浏览器验证。

### Task 5: 部署与回归

**Files:** `README.md`, `docs/superpowers/specs/2026-10-13-daily-journal-design.md`

- [ ] 更新行为说明和 DSH 定义。
- [ ] 本地运行完整 pytest。
- [ ] 部署到服务器，运行完整 pytest。
- [ ] 用真实 UI 验证普通记录、自动问题、明确问它、旧日期和丰富日卡片。
- [ ] 检查 git diff、提交并推送。
