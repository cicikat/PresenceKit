# Gomoku Activity (P0 + P1 + P3-pending)

五子棋活动模式，Reality-side Activity。

## 设计原则

- **Gomoku 是 Activity，不是 Tool trigger**：不通过 LLM 工具调用启动，必须由用户显式 API 调用。
- **规则裁判由代码负责**：LLM 不参与判棋、胜负判定、坐标合法性检查。
- **LLM 未来只能评论/陪玩**：P0/P1 不集成 LLM 评论；assistant 可以在单独聊天消息中讨论棋局，但落子必须走 activity API。
- **P1 本地 AI 对手**：不接 AlphaGo / 外部 API / LLM，纯启发式评分选点。
- **每步棋只写 activity session**，不写普通聊天记忆（不写 short_term / event_log / user_hidden_state）。

## 禁止

- 不接 LLM / Dream / trigger / scheduler / perceive_event。
- 不接 AlphaGo / 外部 AI API。
- 不做禁手、联网对战、复杂神经网络。
- 不写 short_term / hidden_state / event_log（含对局摘要路径）。
- 不改 chess / reading activity。
- 不接活动内聊天，不写普通 chat messages。
- 不提供手动"记住这局"按钮；记忆写入只由步数阈值自动控制。

## 规则

| 规则 | 说明 |
|---|---|
| 棋盘 | 15×15（API 拒绝其他尺寸） |
| 先手 | 黑棋（用户） |
| AI 执子 | P1 默认白棋 |
| 胜利条件 | 横 / 竖 / 左斜 / 右斜 任意五连 |
| 禁手 | 不做禁手，不限黑棋长连 |
| 平局 | 不做平局检测（全盘落满时 status 仍为 active） |

## 坐标系

- 0-based，左上角为 `(0, 0)`，x 向右，y 向下。
- 存储：`board[y][x]`，值为 `null`（空）/ `"black"` / `"white"`。
- API 传入/返回均使用 0-based 坐标。

## Session State 结构

```json
{
  "board_size": 15,
  "board": [["black", null, ...], ...],
  "current_turn": "black",
  "move_history": [
    {"x": 7, "y": 7, "player": "black", "move_no": 1},
    {"x": 8, "y": 8, "player": "white", "move_no": 2, "source": "ai",
     "style": "gentle", "base_style": "balanced", "style_source": "activity_chat_control",
     "did_hold_back": true}
  ],
  "status": "active",
  "winner": null,
  "last_move": {"x": 8, "y": 8, "player": "white", "move_no": 2, "source": "ai",
                "style": "gentle", "base_style": "balanced", "style_source": "activity_chat_control"},
  "opponent": "character_ai",
  "ai_player": "white",
  "ai_style": "balanced",
  "ai_response_mode": "pending",
  "pending_ai_turn": false
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `board_size` | int | 固定 15 |
| `board` | list[list] | `board[y][x]`：`null` / `"black"` / `"white"` |
| `current_turn` | string | `"black"` / `"white"` |
| `move_history` | list | 按落子顺序；AI 落子额外含 `source="ai"` / `style` / `base_style` / `style_source` |
| `status` | string | `"active"` / `"completed"` |
| `winner` | string\|null | `"black"` / `"white"` / `null` |
| `last_move` | dict\|null | 最近一步落子信息 |
| `opponent` | string | `"human"` / `"character_ai"` |
| `ai_player` | string | AI 执子颜色，固定 `"white"` |
| `ai_style` | string | `"balanced"` / `"gentle"` / `"serious"` / `"teaching"`（session 基础风格） |
| `ai_response_mode` | string | `"auto"`（立即 AI 落子）/ `"pending"`（等待 /ai_move） |
| `pending_ai_turn` | bool | True 时表示 AI 有一手待执行，调用 /ai_move 后清除 |

## API

### POST /activity/gomoku/start

开局。若已有 active session 自动关闭旧 session，创建新 session。

**Body:**
```json
{
  "board_size": 15,
  "uid": "",
  "opponent": "character_ai",
  "ai_style": "balanced",
  "ai_response_mode": "pending"
}
```

`opponent` / `ai_style` / `ai_response_mode` 为可选字段，默认值分别为 `"human"` / `"balanced"` / `"auto"`。

**Response:**
```json
{
  "session_id": "a1b2c3...",
  "board_size": 15,
  "board": [[null, ...], ...],
  "current_turn": "black",
  "status": "active",
  "opponent": "character_ai",
  "ai_player": "white",
  "ai_style": "balanced",
  "ai_response_mode": "pending",
  "pending_ai_turn": false
}
```

**错误：** 422（board_size != 15 / opponent 不合法 / ai_style 不合法 / ai_response_mode 不合法）

---

### GET /activity/gomoku/state

获取当前 active session 状态。

**Query:** `uid=`（可选，默认 default_user_id）

**Response（有 session）:**
```json
{ "active": true, "session_id": "...", "board_size": 15, "board": [...], ... }
```

**Response（无 session）:**
```json
{ "active": false }
```

---

### POST /activity/gomoku/move

落子。`ai_response_mode` 决定 AI 是立即落子（auto）还是等待 /ai_move（pending）。

**Body:**
```json
{ "session_id": "...", "x": 7, "y": 7, "uid": "" }
```

**Response（auto mode，未胜）:**
```json
{
  "board": [...],
  "last_move": {"x": 8, "y": 8, "player": "white", "move_no": 2, "source": "ai", "style": "balanced"},
  "move_history": [...],
  "current_turn": "black",
  "status": "active",
  "winner": null,
  "pending_ai_turn": false
}
```

**Response（pending mode，用户落子后等待 /ai_move）:**
```json
{
  "board": [...],
  "last_move": {"x": 7, "y": 7, "player": "black", "move_no": 1},
  "move_history": [{"x": 7, "y": 7, "player": "black", "move_no": 1}],
  "current_turn": "white",
  "status": "active",
  "winner": null,
  "pending_ai_turn": true
}
```

**Response（胜利）:**
```json
{
  "board": [...],
  "last_move": {...},
  "move_history": [...],
  "current_turn": "black",
  "status": "completed",
  "winner": "black",
  "win_line": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, ...],
  "pending_ai_turn": false
}
```

**AI 自动落子流程（`opponent=character_ai`，`ai_response_mode="auto"`）：**
1. 应用用户落子，检查用户是否赢
2. 若未结束且 `current_turn == ai_player`：调用 `choose_gomoku_ai_move(board, ai_player, style)` 得到合法点
3. 应用 AI 落子，再次检查胜负
4. 返回含 `move_history`（含 AI 落子记录）的完整状态，`pending_ai_turn=False`

**Pending 模式流程（`ai_response_mode="pending"`）：**
1. 应用用户落子，检查用户是否赢
2. 若未结束且 `current_turn == ai_player`：设 `pending_ai_turn=True`，不自动落子
3. 返回只含用户落子的状态，`pending_ai_turn=True`，等待前端调用 /ai_move

**错误（409）：**
- session 不存在
- session 已关闭
- 棋局已结束（completed）
- 坐标越界
- 格子已有棋子

---

### POST /activity/gomoku/ai_move

执行 pending mode 下待处理的 AI 落子。

读取最近 transcript 中的 `ai_style_tilt` control，轻微影响本次 AI 落子风格，不永久覆盖 session 的 `ai_style`。LLM 不输出坐标，规则引擎负责合法性和胜负判定。

**Body:**
```json
{ "session_id": "...", "uid": "" }
```

**Response（完整 state）:**
```json
{
  "board": [...],
  "last_move": {"x": 8, "y": 8, "player": "white", "move_no": 2, "source": "ai",
                "style": "gentle", "base_style": "balanced", "style_source": "activity_chat_control"},
  "move_history": [...],
  "current_turn": "black",
  "status": "active",
  "winner": null,
  "pending_ai_turn": false,
  "ai_player": "white",
  "opponent": "character_ai",
  "ai_style": "balanced",
  "ai_response_mode": "pending"
}
```

**错误：**
- 404: session 不存在
- 409: session 已关闭 / 棋局已结束 / 非 AI 对手 / `pending_ai_turn=False` / 非 AI 轮次

---

### POST /activity/gomoku/close

关闭棋局，按步数阈值决定是否生成对局摘要。幂等（session 已关闭时直接返回）。

**Body:**
```json
{ "session_id": "...", "uid": "" }
```

**Response（move_count ≤ 12，视为噪声，不写摘要）:**
```json
{ "session_id": "...", "status": "closed", "closed_at": "2026-06-09T..." }
```

**Response（move_count > 12，生成摘要）:**
```json
{
  "session_id": "...",
  "status": "closed",
  "closed_at": "2026-06-09T...",
  "activity_summary": "用户和他进行了一局五子棋。用户执黑，他执白，对局共 20 手，结果：黑棋获胜。"
}
```

---

## 存储路径

```
data/runtime/activity/{char_id}/{uid}/gomoku/{session_id}/session.json
```

- char_id + uid 双重隔离，不同角色/用户路径不相交。
- session_id 经沙盒路径检查，不允许路径逃逸。
- 通过 `core.activity.store`（通用 ActivitySession 存储层）读写，不直接操作文件系统。

## 胜负判断

`core/activity/gomoku.py` 中 `check_win(board, x, y, player, size)` 在落子后执行：

1. 遍历四个方向向量：横 `(1,0)` / 竖 `(0,1)` / 右斜 `(1,1)` / 左斜 `(1,-1)`。
2. 从落子点出发，向两侧延伸，统计连续同色棋子。
3. 若任一方向连续 ≥ 5，返回排序后的连线坐标列表（即 `win_line`）。
4. 无五连返回 `None`，继续切换轮次。

不做禁手，不限制黑棋长连。

## P1 本地 AI 对手

### 设计约束

- **不接外部 API / LLM / AlphaGo / 云服务**
- **AI 只是合法 move generator**：选点后交给 `check_win` 判胜负，不自行判断
- **不写长期记忆**：AI 落子只写 activity session
- **不接 trigger / scheduler / Dream**

### 算法：启发式评分

对每个空位 `(x, y)` 计算综合得分：

```
total = attack_score + defense_score + center_bias * 2 + adjacency_bias * 10
```

| 分量 | 说明 |
|---|---|
| `attack_score` | 模拟 AI 落子此处后，在四方向上的棋形得分之和 |
| `defense_score` | 模拟对手落子此处后的棋形得分（越高越需堵） |
| `center_bias` | `max(0, 7 - max(|x-7|, |y-7|))`，靠近中心得分越高 |
| `adjacency_bias` | 8连通邻居中已有棋子数，倾向靠近已有子落点 |

**棋形得分表 `(count, open_ends) → score`：**

| 棋形 | count | open_ends | 分值 |
|---|---|---|---|
| 成五（立即返回） | ≥5 | - | 100000 |
| 活四 | 4 | 2 | 10000 |
| 冲四 | 4 | 1 | 1000 |
| 活三 | 3 | 2 | 1000 |
| 眠三 | 3 | 1 | 100 |
| 活二 | 2 | 2 | 100 |
| 冲二 | 2 | 1 | 10 |
| 单子（活） | 1 | 2 | 10 |

**优先级（由评分自动体现）：**
1. AI 有一步成五 → 立刻下（attack=100000 最高）
2. 对手有一步成五 → 立刻堵（defense=100000）
3. 优先形成活四/冲四
4. 优先堵对方活四/冲四
5. 优先形成活三
6. 优先堵对方活三
7. 靠近已有棋子（adjacency_bias）
8. 开局优先中心附近（center_bias；空棋盘直接返回 (7,7)）

### 风格

风格只影响候选选点策略，不影响规则或合法性：

| 风格 | 行为 |
|---|---|
| `serious` | 选最高分候选 |
| `balanced` | 在 top-3 中按 [3,2,1] 加权随机 |
| `gentle` | 过滤 AI 能立即赢的点（除非必须防守），从 top-5 非赢点中随机选 |
| `teaching` | 额外提升能形成/堵截活三的棋形得分（+500/+300），选提升后最高分 |

**gentle 详细逻辑：**
1. 若对手任意一格 defense_score ≥ 100000（对手下一步即赢），立即堵（强制防守）
2. 否则，过滤掉 attack_score ≥ 100000 的格子（AI 一步赢招）
3. 从剩余 top-5 中随机选一点

> 即使 gentle，也不会下非法棋或明显无意义远点。"放水"只是轻微降强度。

### 后续扩展（不在 P1 范围）

- 人格层可以在单独聊天消息中生成对局评论，但不负责规则和落子
- 未来可以对 AI 落子附加情感旁白（通过 LLM 生成评论，与落子 API 分离）

## 记忆边界（P2）

### 设计决策

- **无手动"记住这局"按钮**：记忆写入完全由步数阈值自动控制。
- **阈值**：`move_count > 12`。对局总步数（含 AI 落子）≤ 12 视为误触/试棋/噪声，关闭后不写摘要。
- **棋谱全文留在 activity session**：`session.json` 中的 `move_history` 不会进入普通记忆。

### 触发条件

| 条件 | 行为 |
|---|---|
| `move_count <= 12` | 正常关闭 session，记日志 `[gomoku] skip memory summary: move_count=N <= 12`，不生成摘要 |
| `move_count > 12` | 生成轻量摘要，写入 `session/summary.json`，close 响应中包含 `activity_summary` 字段 |

### 摘要格式

摘要文本仅含"参与方 + 步数 + 结果"，不含棋谱坐标列表。

- `opponent=character_ai`：`"用户和他进行了一局五子棋。用户执黑，他执白，对局共 N 手，结果：{黑棋获胜|白棋获胜|未分胜负}。"`
- `opponent=human`：`"用户进行了一局本地双人五子棋，对局共 N 手，结果：{黑棋获胜|白棋获胜|未分胜负}。"`

### 存储路径

```
data/runtime/activity/{char_id}/{uid}/gomoku/{session_id}/summary.json
```

摘要 JSON 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string | 摘要文本 |
| `move_count` | int | 对局总步数 |
| `winner` | string\|null | `"black"` / `"white"` / `null` |
| `opponent` | string | `"human"` / `"character_ai"` |
| `generated_at` | string | ISO 8601 UTC 时间戳 |

### 主记忆接入（待后续实现）

当前摘要**只落到 activity session 目录的 `summary.json`**，不接入 `short_term` / `event_log` / `user_hidden_state`。后续可通过主记忆的安全写入入口（slow_queue / fixation_pipeline）读取摘要并推入 episodic 层，无需改动本文件内的边界逻辑。

## 活动内对话（P0 + 只读注入，Brief 43 §C）

五子棋 session 内，用户可以和他自然聊天。后端生成 LLM 回复并写入 transcript，
**不写主记忆**（不写 short_term / event_log / user_hidden_state）。

**只读边界（Brief 43 §C 拍板）**：companion chat 现在会**只读**注入主聊天最近 3
轮对话（`core.memory.short_term.get_history`）+ 人设摘要（`core.character_loader`
的 personality，退 description，截断 ~300 字），用于让活动内回复贴合角色人设与
主线语境。两个注入源都通过 `core/activity/companion_context.py` 读取，全部
fail-open（读失败返回空串，不影响陪聊）。**写入边界完全不变**：仍不写
short_term / event_log / user_hidden_state / afterglow。

### POST /activity/gomoku/chat

**Body:**
```json
{
  "session_id": "...",
  "message": "你是不是在让着我",
  "uid": ""
}
```

**约束：**
- `message` 不能为空 → 422
- `message` 超出 1000 字 → 422
- `session_id` 不存在 → 404
- session 非 active → 409

**Response:**
```json
{
  "session_id": "...",
  "reply": "我只是没有急着把局面收死。你这一手倒是比刚才稳。",
  "control": {
    "ai_style_tilt": "gentle",
    "commentary_tone": "calm"
  }
}
```

`control` 为可选，无控制意图时返回空对象 `{}`。

### transcript.jsonl 存储路径

```
data/runtime/activity/{char_id}/{uid}/gomoku/{session_id}/transcript.jsonl
```

每行一条 JSON 记录：

```jsonl
{"type":"user_chat","text":"你是不是在让着我","ts":"2026-06-09T..."}
{"type":"assistant_chat","text":"我只是没有急着把局面收死。","ts":"2026-06-09T...","control":{"ai_style_tilt":"gentle"}}
```

- `type`: `user_chat` / `assistant_chat`
- `control` 字段仅在非空时出现

### 边界保证

| 禁止写入 | 状态 |
|---|---|
| `short_term`（history） | 禁止 ✓ |
| `user_hidden_state` | 禁止 ✓ |
| `event_log` | 禁止 ✓ |
| `afterglow / impression` | 禁止 ✓ |
| Dream / trigger / scheduler | 不接 ✓ |
| 修改 board / move_history / winner / status | 不允许 ✓ |
| activity summary | 不触发（summary 仍只在 close 阶段按 move_count > 12 处理） ✓ |

### LLM 输出协议

模型自然回复，可选在末尾附加控制块：

```
自然语言回复

<activity_control>
{"ai_style_tilt":"gentle","commentary_tone":"calm"}
</activity_control>
```

- `ai_style_tilt`: `"gentle"` / `"balanced"` / `"serious"` / `"teaching"` / 省略
- `commentary_tone`: `"calm"` / `"teasing"` / `"focused"` / `"comforting"` / 省略
- 非法值静默丢弃；解析失败不影响可见回复
- **P0 control 只保存到 transcript，不影响 AI 落子风格**

### LLM 接入（P0）

P0 直接调用 `core.llm_client.chat()`，绕过主 pipeline（不会触发
short_term / event_log / memory 写入）。LLM 失败时返回 fallback 回复，transcript 仍写入。

### P1 (pending mode) — 对话影响 AI 下一手风格

已实现（P3-pending）。

**设计约束：**
- LLM 不输出坐标，不参与落子决策
- transcript 不写主记忆（不写 short_term / event_log / user_hidden_state）
- 风格 tilt 只影响本次 apply_ai_move，不永久覆盖 session 的 ai_style
- 规则引擎负责合法性与胜负

**control 读取顺序：**
1. `/chat` 生成的 `assistant_chat` 条目若含 `control.ai_style_tilt`（已通过 _parse_control 验证），写入 transcript
2. `/ai_move` 调用时读取最近 10 条 transcript，取最新 assistant_chat 的 `ai_style_tilt`
3. 若有效，作为 `style_tilt` 传给 `apply_ai_move()`；无效/无则使用 `base_style`
4. AI move 记录 `style` / `base_style` / `style_source`，供 grounding 读取

---

## 文件索引

| 文件 | 说明 |
|---|---|
| `core/activity/gomoku.py` | 规则引擎（start_game / get_active_session / make_move / apply_ai_move / close_game） |
| `core/activity/gomoku_ai.py` | 本地 AI move generator（choose_gomoku_ai_move + 启发式评分） |
| `core/activity/gomoku_grounding.py` | 确定性棋局事实计算（build_gomoku_grounding_facts，含 did_hold_back 逻辑） |
| `core/activity/gomoku_companion.py` | 活动内对话 LLM 生成（generate_reply / get_recent_ai_style_tilt） |
| `core/activity/companion_context.py` | 只读注入 helper（load_persona_brief / load_main_chat_recall，Brief 43 §C） |
| `core/activity/companion_text.py` | 陪聊输出清洗（strip_action_descriptions，Brief 43 §A） |
| `core/activity/transcript.py` | transcript.jsonl 存储层（append_entry / load_recent） |
| `admin/routers/gomoku.py` | HTTP API 路由（含 /ai_move） |
| `tests/test_gomoku_activity.py` | P0 验收测试（20 用例） |
| `tests/test_gomoku_ai.py` | P1 验收测试（15 用例） |
| `tests/test_gomoku_memory_boundary.py` | P2 记忆边界测试（17 用例） |
| `tests/test_gomoku_companion.py` | companion chat 测试（24 用例） |
| `tests/test_gomoku_grounding.py` | grounding 测试（11 用例） |
| `tests/test_gomoku_pending.py` | P3 pending AI turn 测试（22 用例） |
| `core/activity/store.py` | 通用 ActivitySession 存储层（gomoku 直接复用，含 save_summary / load_summary） |
