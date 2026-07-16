# Activity Session — 设计说明

> **命名辨析**：本文的 `core/activity/` / `ActivitySession` 是用户显式发起的结构化共玩会话；
> `core/activity_manager.py` 则是角色「此刻在忙什么」的 ambient presence 状态，注入现实 prompt
> 的 `2.6_presence` 层。两者架构无关，不共享生命周期、状态或路由。

## 定位

`ActivitySession` 是 **reality-side session**，不是 trigger，不是 tool result，不进入普通短期记忆。

它用于承载用户与角色之间的结构化共同活动（reading / gomoku / chess / dream_seed），生命周期由用户显式 API 调用控制：

```
POST /activity/start   → create_session()
GET  /activity/state   → find_active_session()
POST /activity/update  → update_state()
POST /activity/close   → close_session()
```

## 与其他系统的边界

| 系统 | 关系 |
|---|---|
| short_term / history | **不写入**。活动步骤不进对话历史 |
| event_log | **不写全文**。活动状态变更不记录事件日志 |
| user_hidden_state | **不写入**。活动不影响隐性状态 |
| perceive_event | **不接入**。不走 gate/conversation_lock 流程 |
| Dream / Scenario | **完全隔离**。Dream 期间不启动/不读取 ActivitySession |
| scheduler | **不接入**。没有定时触发 |
| trigger / stimulus | **不是** trigger。必须由用户按钮/命令显式启动 |

## 数据结构

```python
@dataclass
class ActivitySession:
    session_id: str        # uuid4().hex，全局唯一
    uid: str               # 用户 id
    char_id: str           # 角色 id
    activity_type: str     # reading | gomoku | chess | dream_seed
    status: str            # active | closed
    state: dict            # activity-specific 数据（P0 为空壳，由各 activity 自定义）
    created_at: str        # ISO 8601 UTC
    updated_at: str        # ISO 8601 UTC
```

## 存储路径

```
data/runtime/activity/{char_id}/{uid}/{activity_type}/{session_id}/session.json
```

- `char_id` / `uid` 双重隔离：不同角色不共享路径，不同用户不共享路径。
- `session_id` 经 `safe_user_id()` 验证，`DataPaths._p()` 沙盒检查，不允许路径逃逸。
- `activity_type` 必须在 `ALLOWED_ACTIVITY_TYPES = {"reading", "gomoku", "chess", "dream_seed"}` 中，否则 `ValueError`。

## 单 active session 策略

同一 `(uid, char_id, activity_type)` 最多允许一个 active session。调用 `create_session()` 时若已有 active session，先将其 `close` 再创建新 session（不静默覆盖，旧 session 仍可按 session_id 查询，status = "closed"）。

## LLM 与 Activity 的关系

LLM **可以讨论**当前进行的 activity（例如棋局、阅读进度），但：

- 状态变更（落子、翻页、胜负）必须走 activity API，不由 LLM 输出决定。
- 规则合法性、胜负判断由代码执行，不由 LLM 推断。
- activity state 不通过 short_term 进入 prompt 主链；如需注入，须走专用 prompt 层（P0 未实现）。

## 模块结构

```
core/activity/
  registry.py        — Activity Registry P0-Lite（静态元信息表，见下）
  types.py           — ActivityType / ActivityStatus / ALLOWED_ACTIVITY_TYPES
  session.py         — ActivitySession dataclass + new_session_id() + now_iso()
  store.py           — create / load / find_active / update_state / close（gomoku/chess）
  activity_store.py  — reading 专用存储
  reading_session.py — ReadingSession 模型
  pdf_reader.py      — PDF 文本提取（reading 专用）
  gomoku.py          — 五子棋规则引擎 + AI
  chess.py           — 国际象棋规则引擎
  gomoku_ai.py       — Gomoku AI 评分
  gomoku_companion.py— Gomoku 活动内对话
  dream_seed.py       — 梦境预构会话、提炼、短期种子读写
  transcript.py      — 活动内对话记录（activity_local）
```

## Activity Registry P0-Lite

`core/activity/registry.py` 是所有 reality-side activity 的**唯一权威声明点**。

### 定位

- **静态元信息表**，不做 router 自动注册，不做插件系统
- 用于：contract smoke tests、memory policy 声明
- 不用于：dynamic import、MCP、前端 component schema、热加载、LLM tool dispatch

### 关键类型

```python
@dataclass(frozen=True)
class MemoryPolicy:
    writes_short_term: bool = False      # 所有 activity 默认 False
    writes_hidden_state: bool = False    # 所有 activity 默认 False
    writes_event_log: bool = False       # 所有 activity 默认 False
    transcript: "activity_local" | "none"
    summary_threshold: int | None        # None = 不生成摘要；gomoku = 12
    main_memory: "deferred" | "none"

@dataclass(frozen=True)
class ActivityMeta:
    id: str                   # "reading" | "gomoku" | "chess"
    label: str                # 中文显示名
    route_prefix: str         # 完整路径前缀，含 /activity，例如 "/activity/gomoku"
    session_store: str        # "reading_store" | "activity_store"
    session_dir_layout: str   # 相对于 data/runtime/activity/ 的路径模板
    frontend_key: str         # 与 ActivityRibbon.tsx ActivityTab 对齐
    tauri_command_prefix: str # 例如 "activity_gomoku_"
    tauri_commands: tuple     # 与 lib.rs async fn 名称一一对应
    memory_policy: MemoryPolicy
    has_companion_chat: bool
    docs_path: str
```

### 存储架构差异（由 session_store 字段声明）

| activity | session_store | store 模块 | 路径布局 |
|---|---|---|---|
| reading | `reading_store` | `core/activity/activity_store.py` | `reading/{char_id}/{uid}/{session_id}/` |
| gomoku | `activity_store` | `core/activity/store.py` | `{char_id}/{uid}/gomoku/{session_id}/` |
| chess | `activity_store` | `core/activity/store.py` | `{char_id}/{uid}/chess/{session_id}/` |
| dream_seed | `activity_store` | `core/activity/store.py` | `{char_id}/{uid}/dream_seed/{session_id}/` |

两套 store 架构均通过 sandbox 路径，不允许路径逃逸，相互独立不共享数据。

### Router 注册（手工维护）

Registry **不负责** router 注册。`admin/admin_server.py` 手工维护：

```python
app.include_router(activity.router, prefix="/activity", ...)  # /activity/current 等
app.include_router(reading.router,  prefix="/activity", ...)
app.include_router(gomoku.router,   prefix="/activity", ...)
app.include_router(chess.router,    prefix="/activity", ...)
app.include_router(dream_seed.router, prefix="/activity", ...)
```

新增 activity 时须同步更新：`registry.py`、`types.py`、`admin_server.py`、前端 CARDS 数组、`activity-api.ts`、`lib.rs`。

### Contract Smoke Tests

`tests/test_activity_contract.py` 验证：
- 每个 activity 的 `start` / `state` / `close` 路由在对应 router 对象中存在
- `has_companion_chat=True` 的 activity 有 `/chat` 路由
- registry 声明的每个 tauri command 名称在 `lib.rs` 中有 `async fn` 声明
- 每个 tauri command 名称在 `activity-api.ts` 中以字符串字面量出现

## P0 范围

P0 只实现 session 外壳（`types.py` / `session.py` / `store.py`），不实现具体游戏规则。gomoku / chess 的 `state` 字段在 P0 为用户自由传入的 dict，不做内容校验。
