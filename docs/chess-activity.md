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

## 模块文件

| 文件 | 职责 |
|---|---|
| `core/activity/chess.py` | 棋局逻辑（`make_initial_state` / `apply_move` / `legal_moves_uci`），不含任何外部 I/O |
| `admin/routers/chess.py` | HTTP API 路由，接入 `activity_store` |
| `core/activity/store.py` | 通用 ActivitySession 持久化层（chess 直接复用） |
| `tests/test_chess_activity.py` | 24 个验收测试 |

---

## 依赖

```
python-chess   # PyPI: chess
```

版本要求：`>= 1.0`（`board.outcome()` API 在 1.x 引入）。
