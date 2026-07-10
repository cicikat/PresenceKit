# Chess Activity (P0)

国际象棋对局作为 ActivitySession 实现，与 Reading / Gomoku 并列为 P0 活动类型。

---

## 设计原则

| 约束 | 说明 |
|---|---|
| **规则裁判** | 全部由 `python-chess` 库完成，不手写走法规则 |
| **胜负判断** | `python-chess` 自动检测 checkmate / stalemate / insufficient material / 75-move / fivefold repetition |
| **无 AI 对手** | P0 不实现 AI 落子，双方均由人类（或前端）操作 |
| **无 Stockfish** | P0 不接 Stockfish，不接任何外部引擎 |
| **无外部 API** | 不发送 HTTP 请求，不调用第三方服务 |
| **LLM 角色** | LLM（助手）未来只能评论棋局或陪聊，不负责判棋，不负责落子 |
| **存储隔离** | 每步棋只写 ActivitySession，不写 short_term / event_log / user_hidden_state |
| **不接 Dream** | Chess 是 Reality-side Activity，不进入 Dream / Scenario |
| **不接 trigger** | 不由 trigger / scheduler / perceive_event 自动触发 |

---

## 路径布局

```
data/runtime/activity/{char_id}/{uid}/chess/{session_id}/session.json
```

由 `core/sandbox.DataPaths.activity_session_dir()` 统一管理，保证沙盒隔离。

---

## Session State 结构

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "turn": "white",
  "status": "active",
  "result": null,
  "termination": null,
  "move_history": [
    {
      "move_no": 1,
      "uci": "e2e4",
      "san": "e4",
      "player": "white",
      "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    }
  ],
  "last_move": null
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `fen` | string | 当前局面的 FEN 字符串 |
| `turn` | `"white"` \| `"black"` | 下一步应走方 |
| `status` | `"active"` \| `"completed"` | 棋局状态（`completed` = 分出胜负或和局） |
| `result` | `null` \| `"1-0"` \| `"0-1"` \| `"1/2-1/2"` | 对局结果；进行中为 null |
| `termination` | `null` \| `"checkmate"` \| `"stalemate"` \| … | 结局原因 |
| `move_history` | array | 所有步棋记录，追加式 |
| `last_move` | object \| null | 最近一步棋的摘要，方便快速读取 |

### termination 取值

| 值 | 含义 |
|---|---|
| `checkmate` | 将死 |
| `stalemate` | 无子可动（逼和） |
| `insufficient_material` | 双方子力不足以将死 |
| `seventyfive_moves` | 75 步规则自动和棋 |
| `fivefold_repetition` | 五次重复局面自动和棋 |

---

## HTTP API

所有端点均在 `/activity` 前缀下，需 Bearer Token 鉴权。

### `POST /activity/chess/start`

开局，创建新的 chess session。

**Request body (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `uid` | string | 否 | 用户 ID，空时使用 config 默认值 |
| `fen` | string | 否 | 自定义起始 FEN；不传则使用标准开局 |
| `include_legal_moves` | bool | 否 | true 时返回 `legal_moves` 列表 |

**Response**

```json
{
  "session_id": "a1b2c3...",
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "turn": "white",
  "status": "active"
}
```

---

### `GET /activity/chess/state`

返回当前 active chess session。

**Query params**: `uid` (可选)

**Response (有 active session)**

```json
{
  "active": true,
  "session_id": "...",
  "fen": "...",
  "turn": "black",
  "status": "active",
  ...
}
```

**Response (无 active session)**

```json
{ "active": false }
```

---

### `POST /activity/chess/move`

落子。

**Request body (JSON)**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 是 | 目标 session |
| `move` | string | 是 | UCI（`e2e4`）或 SAN（`e4`、`O-O`） |
| `uid` | string | 否 | 用户 ID |
| `include_legal_moves` | bool | 否 | true 时返回下一步的合法走法 |

**Move 格式**

P0 优先支持 UCI：

- 普通走法：`e2e4`
- 升变：`e7e8q`（最后一位为棋子类型：q/r/b/n）
- 王车易位：`e1g1`（kingside）、`e1c1`（queenside）

同时接受 SAN：`e4`、`Nf3`、`O-O`、`O-O-O`。

**错误响应**

| HTTP | 场景 |
|---|---|
| 404 | session 不存在 |
| 409 | session 已关闭 或 棋局已结束 |
| 422 | 非法走法 或 无法解析走法 |

---

### `GET /activity/chess/legal_moves`

返回当前局面所有合法走法（UCI）。

**Query params**: `session_id` (必填), `uid` (可选)

**Response**

```json
{
  "session_id": "...",
  "legal_moves": ["a2a3", "a2a4", "b2b3", ...],
  "count": 20
}
```

---

### `POST /activity/chess/close`

关闭棋局 session，不写任何长期记忆。

**Request body (JSON)**: `session_id`, `uid` (可选)

**Response**

```json
{
  "status": "closed",
  "session_id": "...",
  "closed_at": "2026-06-09T..."
}
```

---

## 胜负判断规则

python-chess 在每次 `board.push(move)` 后自动检测终局条件：

| 条件 | result | termination |
|---|---|---|
| 将死（被将军且无合法走法） | `"1-0"` 或 `"0-1"` | `"checkmate"` |
| 逼和（无合法走法，未被将军） | `"1/2-1/2"` | `"stalemate"` |
| 子力不足（双方均无法将死） | `"1/2-1/2"` | `"insufficient_material"` |
| 75 步无吃子无走卒 | `"1/2-1/2"` | `"seventyfive_moves"` |
| 五次重复局面 | `"1/2-1/2"` | `"fivefold_repetition"` |

50 步规则和三次重复局面和棋不自动生效（需玩家主动申请），P0 不实现申请接口。

---

## P1 本地 AI 对手 + pending 落子 + style tilt（Brief 43 §E）

`make_initial_state(fen, opponent, ai_style)` 的 `opponent="character_ai"` 启用本地
AI 对手（`core/activity/chess_ai.py`，minimax + alpha-beta，不接 Stockfish / 外部 API /
LLM）。AI 固定执黑。

**pending 落子**：用户落子后若轮到 AI，`apply_move()` 设 `pending_ai_turn=True`，不
立即落子；前端需显式调用 `POST /activity/chess/ai_move` 触发。

### `POST /activity/chess/ai_move`

执行待处理的 AI 落子。调用前读取最近 transcript 的 `ai_style_tilt` control
（`chess_companion.get_recent_ai_style_tilt`），对本次落子风格产生轻微影响，不
永久覆盖 session 的 `ai_style`。规则引擎负责合法性与胜负判定，LLM 不输出坐标。

**Body:** `{ "session_id": "...", "uid": "" }`

**Response（含 last_move 的 style 字段）:**
```json
{
  "session_id": "...",
  "fen": "...",
  "turn": "white",
  "status": "active",
  "result": null,
  "termination": null,
  "last_move": {
    "move_no": 2, "uci": "e7e5", "san": "e5", "player": "black", "fen_after": "...",
    "style": "gentle", "base_style": "balanced", "style_source": "activity_chat_control"
  },
  "opponent": "character_ai",
  "ai_player": "black",
  "pending_ai_turn": false
}
```

`style_source` 为 `"activity_chat_control"`（tilt 生效）或 `"base_style"`（无有效
tilt，用 session 的 `ai_style`）。

**错误：** 404 session 不存在；409 session 已关闭 / 棋局已结束 /
`pending_ai_turn=False` / 非 AI 轮次。

### control 读取顺序（同 gomoku）

1. `/chat` 回复中的 `<activity_control>{"ai_style_tilt": "..."}</activity_control>`
   经 `_parse_control` 校验后写入 transcript 的 `assistant_chat.control`。
2. `/ai_move` 调用时读取最近 10 条 transcript，取最新 `assistant_chat` 的
   `ai_style_tilt`；非法值 / 无 control 时回退到 `base_style`。
3. AI move 记录 `style` / `base_style` / `style_source`，供前端和 grounding 读取。

---

## 活动内对话（companion chat + 只读注入 Brief 43 §C）

`POST /activity/chess/chat` — 同 gomoku，只写 activity transcript，不写主记忆
（不写 short_term / event_log / user_hidden_state）。

**只读边界（Brief 43 §C 拍板）**：companion chat 会**只读**注入主聊天最近 3 轮
对话 + 人设摘要（personality，退 description，截断 ~300 字），用于让棋局陪聊更贴
合角色人设与主线语境；两者均通过 `core/activity/companion_context.py` 读取，
fail-open（读失败返回空串）。**写入边界不变**：仍不写 short_term / event_log /
user_hidden_state / afterglow。

回复在写入 transcript 前经 `core/activity/companion_text.py::strip_action_descriptions`
清洗括号动作描写与整行 Markdown 旁白（Brief 43 §A）；LLM 调用前后接入
`core.observe.prompt_capture`（`origin.origin="activity"`, `activity_type="chess"`），
使 `/observe/prompt-layers/{uid}` 能看到棋局陪聊的 prompt 快照（Brief 43 §B）。

LLM 输出协议（可选控制块）：

```
自然语言回复

<activity_control>
{"ai_style_tilt": "gentle|balanced|serious|teaching", "commentary_tone": "calm|teasing|focused|comforting"}
</activity_control>
```

`ai_style_tilt` 见上一节「P1 本地 AI 对手 + pending 落子 + style tilt」。非法值
静默丢弃；解析失败不影响可见回复。

## 模块文件

| 文件 | 职责 |
|---|---|
| `core/activity/chess.py` | 棋局逻辑（`make_initial_state` / `apply_move` / `legal_moves_uci`），不含任何外部 I/O |
| `core/activity/chess_ai.py` | 本地 AI 对手（`choose_chess_ai_move`，minimax + style） |
| `core/activity/chess_grounding.py` | 确定性棋局事实计算（`build_chess_grounding_facts`） |
| `core/activity/chess_companion.py` | 活动内对话 LLM 生成（`generate_reply` / `get_recent_ai_style_tilt`） |
| `core/activity/companion_context.py` | 只读注入 helper（`load_persona_brief` / `load_main_chat_recall`，Brief 43 §C） |
| `core/activity/companion_text.py` | 陪聊输出清洗（`strip_action_descriptions`，Brief 43 §A） |
| `admin/routers/chess.py` | HTTP API 路由，接入 `activity_store` |
| `core/activity/store.py` | 通用 ActivitySession 持久化层（chess 直接复用） |
| `tests/test_chess_activity.py` | 24 个验收测试 |
| `tests/test_chess_companion.py` | companion chat 验收测试 |
| `tests/test_chess_grounding.py` | grounding 验收测试 |
| `tests/test_chess_style_tilt.py` | style tilt 验收测试（Brief 43 §E） |
| `tests/test_chess_ai.py` | chess_ai teaching style 回归测试（Brief 43 §F） |

---

## 依赖

```
python-chess   # PyPI: chess
```

版本要求：`>= 1.0`（`board.outcome()` API 在 1.x 引入）。
