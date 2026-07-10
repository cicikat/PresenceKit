# Brief 43 · 下棋活动（chess/gomoku）后端：输出清洗 + prompt 观测 + 只读记忆注入 + 主动评论 + chess style tilt

> 来源：2026-07-10 用户对"一起做事-下棋"的整体反馈排查。
> 配套前端工单：`Emerald-client/cc-tasks/18-下棋前端-棋规交互-思考窗口-主动评论.md`（其 §I 依赖本 brief §D，其余可并行先行）。
> 用户已拍板：放开只读注入主聊天近3轮+人设摘要；主动说话=关键时刻必评+20%低频随机+冷却；思考窗口 3–8s（前端实现）。

---

## 排查结论（现状，写工单的依据）

1. **（）动作描写残留**：activity companion（`core/activity/chess_companion.py` / `gomoku_companion.py` / `reading_companion.py`）直接调 `core.llm_client.chat()`，绕过主 pipeline。主链对动作描写的处理有两层——记忆侧 `core/reality_output_scrubber.py` 整行清洗、可见侧桌宠端按 say-segments 渲染——activity 链两层都没有，且 activity 的 system prompt 也没有禁止括号动作描写的指令（对比 `core/mail/letter_writer.py:48`、`core/activity/dream_seed.py:97` 都有）。
2. **prompt 观测未录入**：管理面板观测走 `core/observe/prompt_capture.py`（`set_capture_origin` / `capture` / `update_llm_output`），只有主 pipeline `build_prompt()` 路径在调。activity chat 完全没接 → `/observe/prompt-layers/{uid}` 看不到下棋的 LLM 调用。
3. **注入内容清单**（chess 为例，`chess_companion._build_messages`）：system 一句身份（只做角色名插值，**无人设内容**）+ 棋局状态 4 行 + `<game_facts>` + 活动内最近 6 条对话 + 用户当句。gomoku 多一个最近棋步摘要。**没有**主聊天 short_term、人设卡、情绪/身份/情景任何层。文档（docs/gomoku-activity.md）现行边界为"不读主记忆"。
4. **无主动说话**：唯一 LLM 入口是 `POST /activity/{chess,gomoku}/chat`，全部由用户消息触发。`/move`、`/ai_move` 不产生任何评论。
5. **思考窗口基础设施已有但没用上**：pending 机制（`pending_ai_turn` + `/ai_move` 分离端点）就是为思考窗口设计的；gomoku 已有 style tilt（chat 的 control 影响下一手风格），**chess 没有 tilt**。窗口清零是前端问题（见前端工单）。
6. **王车易位/吃过路兵/升变**：`python-chess` 规则引擎完全支持（`legal_moves_uci` 里有 `e1g1`、过路兵目标格）。是前端点击映射问题（见前端工单），后端无需改动。**升变**目前前端只发 `e7e8`（无升变后缀）→ 后端 422，属前端修。
7. **顺带发现的 bug**：`core/activity/chess_ai.py` teaching 风格里 `board.is_capture(move)` 在 `board.push(move)` **之后**调用，此时局面已变，判断必错（§F 修）。

---

## 工单拆分与依赖

| 项 | 内容 | 依赖 |
|---|---|---|
| §A | companion 输出清洗 + 禁动作指令 | 无，可并行 |
| §B | prompt 观测录入 | 无，可并行 |
| §C | 只读注入主聊天近3轮 + 人设摘要 | 无，可并行 |
| §D | 主动评论接口 `/comment` + 触发策略 | 建议在 §A/§C 之后（复用清洗与注入） |
| §E | chess style tilt（对齐 gomoku） | 无，可并行 |
| §F | chess_ai teaching is_capture bug | 无，可并行，5 分钟 |

§A–§C、§E、§F 互不冲突可并行；§D 最后做。每项独立 commit。

---

## §A 输出清洗 + 禁动作指令

新建 `core/activity/companion_text.py`：

```python
def strip_action_descriptions(text: str) -> str:
    """活动内陪聊可见输出清洗：去除括号动作描写与整行旁白。
    比 reality_output_scrubber 轻：不做动作词整行丢弃（陪聊短句易被误杀），
    只处理括号与整行标记。清洗后为空时返回原文截断（保底不吞回复）。"""
```

规则：

1. 行内与整行 `（…）` / `(…)`（跨行括号不处理，按行内匹配即可）删除。
2. 整行 `*…*`、`_…_`、`> …` 删除（复用 `reality_output_scrubber` 的整行正则常量即可，可 import 或复制，注明来源）。
3. 清洗后 strip；若为空 → 返回原文（去括号前的）前 80 字，不返回空串。

接入点（三个 companion 的 `_call_llm` 返回后、`_filter_holdback_claims` 之前/之后均可，但必须在写 transcript 之前——transcript 是下一轮上下文，脏文本会自我强化）：

- `chess_companion.generate_reply`
- `gomoku_companion.generate_reply`
- `reading_companion.generate_reply`（顺带，同一行接入）

同时在三个 companion 的 system prompt 里补一句（参照 `letter_writer.py` 措辞）：

> "只输出说出口的话。不写旁白、不写括号动作描写、不写星号动作、不用 Markdown。"

注意 chess/gomoku 的 system 常量有 `.replace("叶瑄", char_name)` 的插值逻辑（硬性规则 8），新增文案不要引入字面角色名。

**测试**：`tests/test_activity_companion_text.py` 新文件——行内括号、整行括号、星号行、全动作描写（保底不为空）、代码块不误伤可不做（陪聊无代码场景）。三个 companion 各加一条"回复含（动作）时 transcript 落盘文本已清洗"的用例（mock `_llm_client.chat` 返回脏文本）。

---

## §B prompt 观测录入

在三个 companion（chess/gomoku/reading）的 LLM 调用前后接 `core/observe/prompt_capture`，全部 try/except fail-open（观测挂了不能影响陪聊）：

```python
from core.observe.prompt_capture import set_capture_origin, capture, update_llm_output
set_capture_origin({"origin": "activity", "activity_type": "chess",
                    "session_id": session_id, "kind": "chat"})  # 主动评论时 kind="comment"
capture(uid, messages, {"tags": [], "layers_activated": [...], "token_estimate": sum(len(m["content"]) for m in messages)})
...
update_llm_output(uid, reply)
```

要求：

1. `_build_messages` 返回的每条 message 加 `_layer` 字段（如 `activity_system` / `activity_context`），否则观测面板层级表里显示 unknown。`llm_client` 对多余键的容忍与主链一致（主链 messages 本来就带 `_layer`），无需改 llm_client；若实测其某 provider 适配层会把额外键传给 API 报错，则在 capture 之后、调 LLM 之前浅拷贝剥掉 `_` 前缀键。
2. §C 落地后，注入的主聊天层/人设层也各自带独立 `_layer` 名（`activity_persona` / `activity_main_chat_recall`），观测面板可直接看到注入了什么、多少字。
3. 管理面板 UI **无需改动**：快照进同一个 ring，`origin.origin == "activity"` 可区分。

**测试**：mock LLM，调 `generate_reply` 后断言 `prompt_capture.get_snapshots(uid)` 最新快照 `origin.activity_type` 正确、`llm_output` 已回填。

---

## §C 只读注入：主聊天近 3 轮 + 人设摘要

**边界变更（用户已拍板）**：活动内聊天从"不读主记忆"放宽为"**只读**主聊天近 3 轮 + 人设摘要；写入边界完全不变（仍不写 short_term / event_log / hidden_state / afterglow）"。

在 `companion_text.py`（或新 `companion_context.py`）加两个 helper，全部 fail-open（读失败返回空串，不影响陪聊）：

```python
def load_persona_brief(char_id: str) -> str:
    """人设摘要：character_loader 加载角色卡，取 personality（截断 ~300 字），
    无 personality 时退 description 前 300 字。见 core/character_loader.py::load。"""

def load_main_chat_recall(uid: str, char_id: str, rounds: int = 3) -> str:
    """主聊天近 rounds 轮：core.memory.short_term.get_history(uid, max_turns=rounds, char_id=char_id)。
    格式化为 用户：…/{char_name}：… 行。写入时已过 _sanitize_assistant_message 脱敏，直接用。"""
```

注入位置（chess + gomoku 的 `_build_messages`；reading 本期不动）：

- 人设摘要拼进 system（`_layer="activity_persona"` 若拆独立 message，或直接并入 system 文本——**建议拆独立 system message**，便于观测和未来裁剪）。
- 主聊天层作为独立 message，`_layer="activity_main_chat_recall"`，文案头注明：`【主线聊天最近对话（只读参考，不要复述，不要把那边的话题强行接过来）】`。

**同步改动**（不做会被 doc hook 拦）：

1. `docs/gomoku-activity.md` 边界表 + `gomoku_companion.py` docstring 里 "Does NOT read from … main memory" 改为 read-only 描述。
2. `docs/chess-activity.md` LLM 角色行同步。
3. `tests/test_gomoku_memory_boundary.py` / `test_gomoku_companion.py` / `test_chess_companion.py` 若有断言"不读主记忆"的用例，改为断言"**不写**主记忆"（写边界测试全部保留，一条不删）。

**硬性规则**：所有路径经 `core/sandbox.get_paths()` 间接使用（short_term/character_loader 内部已合规，不要自己拼路径）；不写字面角色名。

---

## §D 主动评论：`POST /activity/{chess,gomoku}/comment`

新端点（两个 router 各一个，scope 同现有 `activity`）：

```
POST /activity/chess/comment   body: {session_id, uid?}
POST /activity/gomoku/comment  body: {session_id, uid?}
→ 200 {"session_id":…, "comment": str|null, "grounding": {...}}   # comment=null 表示这步不说话
→ 404/409 语义同 /chat
```

**触发策略在后端**（前端每次 AI 落子后/终局后无脑调一次，是否说话由后端裁决），在 companion 模块加：

```python
async def maybe_generate_move_comment(char_id, uid, session_id, state) -> tuple[str | None, dict]:
```

策略（用户已拍板）：

1. **关键时刻必评**：
   - chess：`build_chess_grounding_facts` 的 `is_check` / `captured_piece` 非空 / `move_hint` 为王车易位、吃过路兵、升变 / `status == "completed"`。
   - gomoku：facts 中 AI 或用户 `created_chain >= 3` / `blocked_*_chain >= 3` / 任一方 `has_four` 或 `has_open_three` / `winner` 非空。
2. 非关键时刻：`random.random() < 0.2` 且冷却满足才评。
3. **冷却**：距上次主动评论 ≥ 2 步。实现：主动评论的 transcript 条目加字段 `"proactive": true, "at_move": <当时 move_history 长度>`；策略读最近 transcript（`load_recent limit=20`）找最后一条 proactive 条目比较步数。
4. 不满足 → 返回 `(None, grounding)`，不调 LLM、不写 transcript。

生成方式：复用 `_build_messages`，把"用户说：…"末段换成内部指令：

> （系统指令：用户没有说话。请你主动对刚才这一手棋/当前局面说一句话，不超过 40 字，只输出说出口的话，依据 `<game_facts>`，不判断胜负。）

写 transcript：**只写** `assistant_chat`（含 `proactive: true` / `at_move`），不写 user_chat。经过 §A 清洗、gomoku 的 holdback 过滤同样适用。observe capture 用 `kind="comment"`（§B）。

**测试**：`tests/test_activity_comment.py`——关键时刻必评（mock facts）；普通步冷却内不评；概率分支用 `monkeypatch random.random`；comment=null 时不写 transcript；写入条目带 proactive/at_move；终局必评。

**文档**：docs/chess-activity.md、docs/gomoku-activity.md 各加端点小节 + 策略表。

---

## §E chess style tilt（对齐 gomoku 已有机制）

让思考窗口内的聊天真正能影响 chess 的下一手：

1. `chess_companion.py`：`_VALID_AI_STYLE_TILTS`（同 gomoku 四值）+ `_parse_control` 接受 `ai_style_tilt` + system prompt 的控制块示例加上该字段 + `get_recent_ai_style_tilt(char_id, uid, session_id)`（照抄 gomoku 版，activity_type 换 "chess"）。
2. `core/activity/chess.py::apply_ai_move(state, style_tilt: str | None = None)`：tilt 合法时用 tilt 风格调 `choose_chess_ai_move`，否则用 session 的 `ai_style`；move entry 加 `"style"` / `"base_style"` / `"style_source"` 字段（对齐 gomoku 的 move_history 字段，见 docs/gomoku-activity.md session 结构）。
3. `admin/routers/chess.py::chess_ai_move`：调用前读 `get_recent_ai_style_tilt`，传入。
4. docs/chess-activity.md 同步（control 协议、ai_move 行为）。

**测试**：照 `tests/test_gomoku_pending.py` 的 tilt 用例移植 3–4 条到 `test_chess_companion.py` / `test_chess_activity.py`。

---

## §F chess_ai teaching bug（一行修）

`core/activity/chess_ai.py::_apply_style` teaching 分支：

```python
# 现状（错）：push 之后调 board.is_capture(move)
board.push(move)
if board.is_check(): bonus += 200
if board.is_capture(move): bonus += 150   # ← 局面已变，必错
board.pop()
# 改为：push 前先算
is_cap = board.is_capture(move)
board.push(move)
if board.is_check(): bonus += 200
board.pop()
if is_cap: bonus += 150
```

**测试**：构造一个"下一手可吃子"的局面，断言 teaching 风格给吃子手加了 bonus（直接测 `_apply_style` 排序结果）。

---

## 通用要求

- 跑测试：`pytest -n auto`（或 `--testmon` / 指定路径），禁止裸 pytest。
- 每个 § 独立 commit，一行信息。
- 改了 `_TOOL_REGISTRY`？没有——本 brief 不新增工具（/comment 是 HTTP 端点不是 LLM 工具），无需探针注册。
- 所有新增 LLM 往返（/comment）都不在任何 send 关键路径上（独立端点，前端异步调），符合 AGENTS.md 规则 9。
- doc sync hook：docs/chess-activity.md、docs/gomoku-activity.md 必须随代码同轮更新。
