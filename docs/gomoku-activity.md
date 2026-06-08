# Gomoku Activity (P0)

五子棋活动模式，Reality-side Activity。

## 设计原则

- **Gomoku 是 Activity，不是 Tool trigger**：不通过 LLM 工具调用启动，必须由用户显式 API 调用。
- **规则裁判由代码负责**：LLM 不参与判棋、胜负判定、坐标合法性检查。
- **LLM 未来只能评论/陪玩**：P0 不集成 LLM 评论；assistant 可以在单独聊天消息中讨论棋局，但落子必须走 activity API。
- **P0 无 AI 对手，无禁手**。
- **每步棋只写 activity session**，不写普通聊天记忆（不写 short_term / event_log / user_hidden_state）。

## 禁止

- 不接 LLM / Dream / trigger / scheduler / perceive_event。
- 不做人机 AI 对手，不接 Stockfish。
- 不写 short_term / hidden_state / event_log。
- 不改 reading activity。
- 不实现插件系统。

## 规则

| 规则 | 说明 |
|---|---|
| 棋盘 | 15×15（P0 固定，API 拒绝其他尺寸） |
| 先手 | 黑棋 |
| 胜利条件 | 横 / 竖 / 左斜 / 右斜 任意五连 |
| 禁手 | P0 不做禁手 |
| 平局 | P0 不做平局（全盘落满时 status 仍为 active） |

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
    {"x": 7, "y": 7, "player": "black", "move_no": 1}
  ],
  "status": "active",
  "winner": null,
  "last_move": {"x": 7, "y": 7, "player": "black", "move_no": 1}
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `board_size` | int | 固定 15 |
| `board` | list[list] | `board[y][x]`：`null` / `"black"` / `"white"` |
| `current_turn` | string | `"black"` / `"white"` |
| `move_history` | list | 按落子顺序，含 x/y/player/move_no |
| `status` | string | `"active"` / `"completed"` |
| `winner` | string\|null | `"black"` / `"white"` / `null` |
| `last_move` | dict\|null | 最近一步落子信息 |

## API

### POST /activity/gomoku/start

开局。若已有 active session 自动关闭旧 session，创建新 session。

**Body:**
```json
{ "board_size": 15, "uid": "" }
```

**Response:**
```json
{
  "session_id": "a1b2c3...",
  "board_size": 15,
  "board": [[null, ...], ...],
  "current_turn": "black",
  "status": "active"
}
```

**错误：** 422（board_size != 15）

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

落子。

**Body:**
```json
{ "session_id": "...", "x": 7, "y": 7, "uid": "" }
```

**Response（未胜）:**
```json
{
  "board": [...],
  "last_move": {"x": 7, "y": 7, "player": "black", "move_no": 1},
  "current_turn": "white",
  "status": "active",
  "winner": null
}
```

**Response（胜利）:**
```json
{
  "board": [...],
  "last_move": {...},
  "current_turn": "black",
  "status": "completed",
  "winner": "black",
  "win_line": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, ...]
}
```

**错误（409）：**
- session 不存在
- session 已关闭
- 棋局已结束（completed）
- 坐标越界
- 格子已有棋子

---

### POST /activity/gomoku/close

关闭棋局，不写长期记忆。幂等（session 已关闭时直接返回）。

**Body:**
```json
{ "session_id": "...", "uid": "" }
```

**Response:**
```json
{ "session_id": "...", "status": "closed", "closed_at": "2026-06-09T..." }
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

P0 不做禁手，不限制黑棋长连。

## 文件索引

| 文件 | 说明 |
|---|---|
| `core/activity/gomoku.py` | 规则引擎（start_game / get_active_session / make_move / close_game） |
| `admin/routers/gomoku.py` | HTTP API 路由 |
| `tests/test_gomoku_activity.py` | 20 个验收测试 |
| `core/activity/store.py` | 通用 ActivitySession 存储层（gomoku 直接复用） |
