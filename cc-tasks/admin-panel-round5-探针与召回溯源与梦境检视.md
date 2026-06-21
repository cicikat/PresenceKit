# 工单：管理面板 round5 —— 探针可视化 + prompt 固定/召回溯源 + 梦境 prompt 检视器

> 仓库 `D:\ai\qq-st-bot`。先看 `AGENTS.md` + `docs/prompt-layers.md` + `docs/tools.md`。
> 接 round4 的 Prompt 层检视器（环形缓冲 + `/observe/prompt-layers/{uid}` + 堆叠视图），本轮在其上扩三块。
> 全部**只读观测**，不动生成逻辑（除捕获钩子）。涉及捕获钩子改到构建路径时跑 `python tests/run_eval.py`。

---

## A. 探针（probe）可视化

**现状（已核实）**：`main.py` 预流程——
- `_fast_path_match(_trusted_user_text)`（:50/:373）：关键词命中 → **跳过探针**直接进工具流（`will_skip_probe=True`）。
- 未命中 → 构造 `tool_detection_messages = [get_probe_prompt(location)(system), *最近2轮真实对话(:399-413), 用户消息]`，调 `llm_client.chat(..., tools=tools_schema, call_category="probe")`（:418）→ `probe_response` → `parse_tool_call_response`（:422）→ `tool_calls` → 执行。

**要捕获并展示**（每轮一条快照，复用 round4 的环形缓冲思路，新建 `/observe/probe/{uid}`）：
- 是否走了 fast-path（跳过探针）；若是，命中的关键词/工具 + `fast_path_risk`。
- 探针实际 prompt：`get_probe_prompt(location)` 全文 + 注入的最近 2 轮上下文 + 用户消息（即喂给探针的几层）。
- 提供给探针的 `tools_schema`（工具名列表即可，可展开看 schema）。
- 探针**原始返回** `probe_response`。
- **解析出的 tool_calls**（工具名 + 参数）。
- **执行结果**：每个被调工具的 `execute()` 返回 / 成功失败 / 副作用（`has_side_effect`）。

**前端**：「探针」观测页 —— 一轮探针的时间线卡：输入(prompt+上下文) → 决策(tool_calls / 或 fast-path / 或无工具) → 执行结果。uid + 轮次选择。

**验收**：发一条会触发工具的消息（如问天气）→ 探针页显示喂给探针的内容、它要调的工具、执行返回；发一条 fast-path 关键词 → 显示"跳过探针 + 命中词"。

---

## B. 对话 prompt 标注：固定注入 vs 本轮召回 + 召回上游 + 实际输出

**现状（已核实，数据基础已有）**：
- `prompt_builder.py:16-21` 已有 `LayerSpec.mode: Literal["always","tagged","scored"]` + `triggers`——即"固定/标签召回/打分召回"的判定概念。
- build_prompt 里层分两类：无条件 append（**固定**）；`if _tags & _xxx_triggers`（**标签召回**，如 diary :777、period :524、watch :551、activity :610）或 RAG 打分（**打分召回**，如 episodic/event_search）。
- meta 已含 `layers_activated / token_estimate / tags(本轮激活标签) / removed_layers`。

**扩 round4 检视器，每层补三个维度**：
1. **来源标记**：`mode` = 固定 / 标签召回 / 打分召回（用徽标区分颜色）。
2. **召回上游**：召回类层显示**为什么被召回**——标签召回 → 命中的具体 tag（`_tags & triggers` 的交集）；打分召回 → RAG 的 query + 命中条目的 score（≥ `rag_score_threshold` 0.6）。固定层标"常驻"。
3. **实际输出**：把本轮捕获的 prompt 与该轮 **LLM 实际输出**配对展示（含被 `reality_output_guard` 清洗前/后，若可得）。这样一屏看到"喂了什么 → 召回逻辑 → 模型实际说了什么"。

**实现**：
- 捕获钩子里给每层记 `{mode, triggers_checked, matched_tags, rag_query?, rag_score?}`。现有硬编码 `if _tags & X` 处，把 `X`(triggers) 和命中结果一并写进该层的捕获记录（局部改，不改注入行为）。
- 输出配对：在 pipeline 跑完 `run_llm` 后，把输出文本写回同一轮快照（与 prompt 同 turn_id 关联）。

**验收**：检视器每层有 固定/召回 徽标；点开召回层能看到命中的 tag 或 RAG 分数；能在同页看到该轮模型实际输出，可定位"这条往事是因为 tag X 被召回的 / 模型拿到了却没用"。

---

## C. 梦境 prompt 检视器（独立页，因梦境是独立 pipeline）

**现状（已核实）**：梦境走**独立** `core/dream/dream_pipeline.py` + `core/dream/dream_prompt.py` 的 `build_dream_prompt`（:225），层是 D0_jailbreak / D1_identity_core / D2_world_ruleset / D3_mes_example / D4_frozen_reality / D4.5_hidden_state… 已有 `_LayerRec(label, chars, tokens, flags, note)` 记录结构 + `dream_prompt.token` logger + `_TOK_RATIO=4` 估算。**round4 的检视器只接了主 pipeline，看不到梦境。** `/dream/chat`（`admin/routers/dream.py:96`）是入口。

**做一个梦境专属检视器**（与 A/B 同款，但独立页 + 独立缓冲）：
- 捕获 `build_dream_prompt` 的 `_records`（每层 label/chars/tokens/flags/note）+ 顶层 token 合计 + 命中的 scene_tags（`_collect_scene_tags`）+ 当前梦境世界/depth（来自 `/dream/state`）。
- 端点 `/observe/dream-prompt/{uid}`，前端「梦境 Prompt」页：层堆叠（D0–D4.5…）+ token 占比 + 哪些 D4.5 类层因 scene_tag 命中而注入（同 B 的召回溯源逻辑，梦境侧用 scene_tags）+ 该轮梦境**实际输出**。
- 与主 pipeline 检视器**并列但分开**（页面/缓冲都独立），避免混淆两套层命名（reality 的 `2_/6d_` vs dream 的 `D0_/D4.5_`）。

**验收**：进一段梦境对话 → 梦境 Prompt 页显示 D0–D4.5 各层 token、哪个梦境世界、scene_tag 命中导致的注入、模型实际梦境回复；与主 pipeline 检视器互不串。

---

## 执行顺序
1. **A 探针**（独立、价值高，先做）。
2. **C 梦境检视器**（数据结构 `_LayerRec` 已现成，捕获 + 复用 round4 前端组件即可）。
3. **B 召回溯源 + 输出配对**（改动面最广，要在每个召回层补 provenance；最后做并跑 `run_eval`）。

> 三者都复用 round4 的「环形缓冲 + 只读端点 + 堆叠视图」骨架，新增的是 探针缓冲 / 梦境缓冲 / 每层 provenance 字段。捕获钩子若改到 `build_prompt` 或 `build_dream_prompt` 返回签名 → 跑 `python tests/run_eval.py` 并同步 `docs/prompt-layers.md`（补"固定/召回 标注 + 探针观测 + 梦境检视"三节）。
