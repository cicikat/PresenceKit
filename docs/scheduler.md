# docs/scheduler.md — 调度器设计

---

## 定位

调度器负责**叶瑄的主动行为**——不等用户发消息，自己在合适的时机触发。

```
core/scheduler/loop.py           ← 主循环 + 工具函数 + 冷却管理
core/scheduler/gating.py         ← proposal 收集、状态/冷却/defer/DND 过滤、每 tick 选一
core/scheduler/execution.py      ← proposal dry-run/live 执行收口，成功后才 mark
core/scheduler/defer_queue.py    ← defer 队列（内存态）：age 跟踪 + 过期 force_send/drop
core/scheduler/proposer_registry.py ← 原生 proposer 注册表
core/scheduler/rhythm.py         ← presence、逻辑日、时间窗比例等节律 helper
core/scheduler/triggers/         ← 各触发器独立文件
    time_based.py                早安 / 晚安 / 随机消息 / 天气 / 日记 / 记忆衰减
    diary.py                     日记相关触发
    period.py                    生理期关心
    memory.py                    未完结话题追问 / 主动回忆
    birthday.py                  生日多段触发
    timenode.py                  时间节点感知
    festival.py                  节日 + 长假加速
    episodic_sweep.py            mid_term 老化扫描，批量晋升情景记忆
    garden_water.py              花园自动浇水
    garden_daily.py              花园 harvest/vase 每日扫描
    hidden_state_decay.py        用户隐性状态衰减（12h）+ 基线收敛（7d），不发言
    watch.py                     Apple Watch 心率 / 睡眠事件
    reminders.py                 到点备忘录 proposer
    sensor_aware.py              sensor 实时状态 → 主动开口（默认关闭）
    dnd.py                       请勿打扰状态（已实现，R2-D 已接入 main.py）
```

---

## 主循环

每 60 秒检查一次。每个 tick 先跑 `core/scheduler/gating.py::run_shadow_tick()` 收集原生
proposal、记录 `data/logs/gating_shadow.jsonl`，再根据 `core/scheduler/execution.py`
的 `EXECUTE_MODE` 决定是否执行 winner。当前常量为 `EXECUTE_MODE = "live"`：除 Watch
事件驱动例外外，winner 会经 `execute_prompt(dry_run=False)` 真正发送，成功后才 `_mark()`。

随后 `loop.py` 仍用 `asyncio.gather(..., return_exceptions=True)` 跑 legacy `_check_*`。
已迁移触发器会通过 `legacy_tick_should_send()` 在 live 模式下让路；维护型扫描
（如 `garden_water`、`garden_daily`、`episodic_sweep`、`log_maintenance`、`hidden_state_decay`、`hidden_state_consolidate`）仍需执行状态变更。

```
owner turn ──notify_owner_turn──→ state_machine
sensor tick ─feed_sensor_tick───→ state_machine
                                  ↓
loop.py tick ──gating log──→ logs/gating_shadow.jsonl
        │      └─ winner execute_prompt() ──live──→ _pipeline_send() → 成功后 mark
        └────legacy/maintenance asyncio.gather──→ 未迁移检查或状态扫描
```

`_pipeline_send()` 内部执行顺序（P1 gate）：

```
receive_perceive_event(source="scheduler", kind="scheduled")
  → Dream Guard (fail-closed) + TTL dedup (60s bucket, key = trigger_name)
  → ACCEPTED → log perceive_event=true + correlation_id + event_id
conversation_lock(uid)
  → fetch_context → build_prompt → run_llm
  → record_assistant_turn(bypass_gate=True)
```

- **conversation_lock** 是 uid 级，与 `desktop_wake Path B` 和 `run_owner_chat_turn` 共用同一把锁，保证同 uid 的 reality LLM 串行。
- **Dream Guard** 通过 `receive_perceive_event` fail-closed：BLOCK_ACTIVE / BLOCK_UNCERTAIN → 直接返回 None，不进 LLM。
- **TTL dedup**：同一 trigger_name 在同一 60s 时间桶内重复触发 → DUPLICATE，静默丢弃。
- **dedupe_key 组成（scheduler）**：`scheduler:uid:char:system:scheduled:hash({"trigger_name":name}):bucket` — payload 只含 trigger_name，key 稳定。
- **dedupe_key 组成（desktop_wake）**：`desktop_wake:uid:char:desktop:wake:hash({}):bucket` — payload 固定为 `{}`，last_seen 等 per-request 字段不入 hash，rapid reconnect 内幂等。
- **dedupe 原则（P1.1 稳定化）**：`event_id` / `correlation_id` 仅用于 tracing，不参与 dedupe（event_id is tracing only）。`dedupe_key` 只能包含稳定语义字段；`last_seen`、timestamp、随机 UUID、request time 等观测字段不得加入 payload（last_seen must not be part of dedupe payload）。违反此原则会导致同语义请求因 key 不同而各自通过去重、多次触发 LLM。
- **DUPLICATE / BLOCKED_DREAM no-op 保证**：命中 DUPLICATE 或 BLOCKED_DREAM 的调用必须立即返回，不进入 LLM、不调用 record / post_process、不向任何 channel fanout。
- `bypass_gate=True` 传给 `record_assistant_turn`，避免锁重入。

---

## 触发状态机（Phase 2 Step 1）

`core/scheduler/state_machine.py` 维护每个 uid 的三态：

| 状态 | 含义 |
|---|---|
| `CHATTING` | 最近收到 owner turn，主动触发器应在后续 gating 接管时静默 |
| `QUIET` | 安静期，主动触发器可作为候选 |
| `RESTLESS` | sensor 事件频繁，后续由 sensor_aware 优先 |

当前状态机只观测不干预。入口：

- `main.py` 收到 owner 的 QQ turn 后调用 `notify_owner_turn(uid)`
- `admin/routers/chat.py` 的 owner 对话入口调用 `notify_owner_turn(uid)`
- `loop.py` 在 `sensor_aware` tick 后读取现有审计结果里的候选数，调用 `feed_sensor_tick(uid, count)`

状态持久化在 `data/runtime/scheduler_user_state.json` 的 `trigger_state` 段，不覆盖同文件里的
`last_diary_share` / `followed_topics` 等运行态。每次状态切换追加到
`data/logs/trigger_state.jsonl`。冷却时间单独保存在 `data/scheduler_cooldowns.json`。

`CHATTING → QUIET` 的滞后按会话 owner turn 数和 `mood_state.get_intensity()` 动态计算；
`QUIET ↔ RESTLESS` 按 sensor 事件率和持续时间确认，避免短暂鼠标/键盘动作造成状态抖动。

---

## Gating 并行观测（Phase 2 Step 2）

`core/scheduler/gating.py` 定义 `TriggerProposal` 并实现 `collect_and_decide(uid, proposals)`：

1. 过滤 `requires_state` 不包含当前状态的候选，`bypass_state_machine=True` 跳过此过滤
2. 过滤冷却未到的候选，冷却仍沿用 `loop.py` 的 `_COOLDOWNS` / `_is_ready`
3. 多候选按 `urgency` 选最高者
4. 一个 tick 最多返回一条候选

候选已不再有 `_adapt_legacy_triggers()` 桥接，只来自
`core/scheduler/proposer_registry.py` 注册的原生 proposer；未迁移触发器不会在
`gating_shadow.jsonl` 里报名。`EXECUTE_MODE="dry_run"` 时只记录 would-send；当前
`EXECUTE_MODE="live"` 时 winner 由统一执行层真实发送，已迁移 legacy tick 自动让路。

已迁移 proposer 覆盖：watch（`hr_critical/hr_high/sleep_end`）、生日四档、`period_reminder`、
time_based 的早晚安/随机/天气/日记/主动回忆、diary 两档、`timenode`、`festival/holiday_boost`、
`reminders`、`topic_followup`、花园伴生事件（bloom/harvest/handle/vase）。`garden_water`、
`garden_daily` 扫描本体、`episodic_sweep`、`episodic_decay`、`dlq_monitor`、`sensor_aware`
仍只走 legacy 真实检查或事件驱动路径。

`run_shadow_tick()` 如果选中了带 `execute` 的 proposal，会按模式调用 `execute()`：
dry-run 写 would-send / would-mark；live 真实调用 `_pipeline_send()`。Watch 心率和睡醒事件由
`watch.py::on_watch_event()` 使用独立 `WATCH_EXECUTE_MODE` 即时执行，普通 tick 跳过，避免重复。
urgency 分档统一由 `core/scheduler/urgency.py` 提供。同一个日志也记录 live execute 被 `_pipeline_send()` 拦下的
`blocked=true` 条目，用于观察 active window 真实拦截分布；该日志只做可观测性，不改变发送、
mark 或重试行为。

shadow log 格式：

```json
{"ts": 1748000000.0, "uid": "1043484516", "state": "QUIET", "candidates": [], "would_pick": null, "reason": "no_candidates"}
```

dry-run log 格式：

```json
{"ts": 1748000000.0, "trigger_name": "morning_greeting", "would_send_prompt": "...", "would_mark": ["morning_greeting"], "would_mark_done": [], "reads_cache_ok": true}
```

blocked log 格式：

```json
{"ts": 1748000000.0, "trigger_name": "reminders", "reason": "sent_false", "would_mark": [], "would_mark_done": ["abc"], "sent": false, "blocked": true}
```

---

## Policy 表（R2-C 已完成）

`core/scheduler/policy.py` 是 **active-window / DND 决策的单一权威来源**：
`gating._decide()` 通过延迟 import 引用 `POLICY_TABLE`；`_pipeline_send()` 不再做任何
active-window / DND 过滤——R2-C 后，执行层仅负责 send + mark，仲裁全在 `gating._decide()`。

R2-C 后的状态：

- `POLICY_TABLE`（25 条）驱动 `gating._decide()` 的 active-window filter 和 DND filter。
- `loop._legacy_active_window_blocks()` 和 `_legacy_dnd_blocks()` **已删除**（R2-C）。
- `_pipeline_send()` 中无任何 active-window / DND 检查；传入的 trigger 已是 gating winner。
- `_HIGH_PRIORITY_TRIGGERS` 常量保留用于文档和测试断言（验证 POLICY_TABLE exempt 集合），
  不参与运行时决策。
- birthday_eve / afternoon / night 由 policy 的 `defer` 对齐为 `exempt`（R2-B 修复）。

两条定性保留：

- `sleep_end`：`priority=”normal”` + `active_window_behavior=”drop”`，`mark_on_drop=False`。
  `cross_marks=[“morning_greeting”]` 只表达”实际 sent 后才联动 mark”；drop 不 cross-mark，
  避免睡醒关心被拦后又压掉早安。
- `hr_high`：语义上是 defer，但 Watch proposal 受 `HEART_RATE_PROPOSAL_TTL_SECONDS=10min`
  限制，`max_defer_age_secs` 只能记为 10 分钟；超过 TTL 后 proposal 返回 `None`，等同过期 drop。

**R2-D 完成项（2026-06-11）**：

- ✅ defer 队列最小实现（`core/scheduler/defer_queue.py`，内存态）
- ✅ DND 触发词自动检测接入 `main.py` owner 消息路径

---

## Defer 队列（R2-D）

文件：`core/scheduler/defer_queue.py`

### 语义

当 `gating._decide()` 遇到 `active_window_behavior="defer"` 的触发器且用户活跃时，
不仅跳过本 tick，还将 `(uid, trigger_name)` 记录到内存队列并追踪首次推迟时间戳。
每个 tick 的 `_decide()` 调用流程：

1. `scan_expired(uid)` — 扫描超龄条目，返回 `(force_send_names, dropped_names)`；
   超龄条目从队列中删除。
2. 若 `trigger_name in force_send_names`（即 `on_defer_expire="force_send"` 且已超龄），
   该触发器被加入 `aw_allowed`，**即使用户仍在活跃窗口也可通过**。
3. 若用户活跃且触发器未能通过 `aw_allowed`，对 `defer` 行为的触发器调用 `enqueue_defer`
   （幂等：首次推迟时间戳不会被后续 tick 覆盖）。
4. 触发器被选中（`picked`）后，调用 `release_defer(uid, trigger_name)` 从队列删除。

### 到期行为

| `on_defer_expire` | 超龄后的行为 |
|---|---|
| `"force_send"` | 即使用户活跃也强制发送（bypass active_window filter） |
| `"drop"` | 清除队列条目，下次重新从零累积 age |

### 限制与生命周期

- **内存态**：进程重启后队列清零；所有 `defer` 触发器的 `max_defer_age_secs` 均较短
  （10 分钟 ~ 4 小时），重启后从新的 tick 开始重新累积 age，无实质影响。
- **不扩大发送频率**：defer 队列只做 age 追踪 + 过期决策，不创建新 proposal；
  proposal 仍来自 proposer registry 每 tick 正常返回。
- **可观测**：`get_queue_snapshot(uid)` 返回当前队列快照，含 `enqueue_ts` 和 `age_secs`；
  候选序列化中新增 `force_send` 和 `deferred_age_secs` 字段，写入 `gating_shadow.jsonl`。

### 当前配置表（defer 触发器）

| 触发器 | max_defer_age_secs | on_defer_expire |
|---|---|---|
| `hr_high` | 10 分钟 | drop |
| `weather_alert` | 30 分钟 | drop |
| `topic_followup` | 2 小时 | drop |
| `reminders` | 10 分钟 | **force_send** |
| `diary_share_reminder` | 4 小时 | drop |
| `diary_reminder` | 4 小时 | drop |

---

## DND 主入口（R2-D）

文件：`core/scheduler/triggers/dnd.py`（逻辑），`main.py`（接线）

**R2-D 完成**：`detect_and_set()` 已接入 `main.py` 的 owner 消息处理路径。

### 接线位置

`main.py::handle_message()` — 在确认 `user_id == owner_id` 的 try 块内，
`mark_user_active()` 调用之后，立即调用：

```python
from core.scheduler.triggers.dnd import detect_and_set as _dnd_detect
_dnd_detect(user_id, message.get("content", ""))
```

- 仅对 owner 消息生效（非 owner 消息不触发 DND 检测）。
- `detect_and_set` 是纯内存操作，不阻塞快速路径。
- 不调用任何 LLM，不影响 pipeline 流程。

### 触发词（设置 DND）

`学习` / `开会` / `上班` / `工作` / `在忙` / `忙着` / `复习` / `备考` / `做题` / `写作业` / `写报告`

### 结束词（清除 DND）

`下课` / `散会` / `下班` / `忙完` / `做完了` / `写完了` / `结束了` / `搞定了`

结束词优先于触发词（同一消息中若两者都出现，结束词生效）。

### DND 生效期间

- DND 默认持续 3 小时（`_DND_DURATION = 3 * 3600`），超时自动失效。
- `gating._decide()` 在 DND 活跃时，只有 `priority="emergency"` 的触发器（`hr_critical`）可通过，
  其余全部返回 `dnd_filtered`。
- Maintenance tick（`log_maintenance` / `episodic_sweep` / `hidden_state_decay` 等）不经过
  发言 gating，不受 DND 影响。

---

## R2-A（2026-06-10）+ R2-B（2026-06-11）+ R2-C（2026-06-11）+ R2-D（2026-06-11）

> R2-A 是审计和决策包。R2-B 完成"发言决策前移第一段"。R2-C 完成 legacy 安全网删除、
> 执行层收口到 send + mark only。R2-D 完成 defer 队列最小实现 + DND 主入口接线。

### 最终调度决策路径图（R2-D 后）

```
owner QQ 消息
    ↓ main.py::handle_message()
    ├─ notify_owner_turn(uid)       → state_machine
    ├─ mark_user_active()           → _last_user_message_time
    └─ detect_and_set(uid, content) → dnd._dnd_expire  ← R2-D DND 接线

每 60s tick
    ↓ loop._loop()
    ├─ gating.run_shadow_tick(uid)
    │     ↓ _collect_native_proposals(ctx)   → proposer_registry 每个 proposer
    │     ↓ _decide(uid, proposals)
    │         1. scan_expired(uid)           → defer_queue 过期处理 (R2-D)
    │         2. state filter               → TriggerState 门控
    │         3. active_window filter       → POLICY_TABLE.active_window_behavior
    │            ├─ force_send_names 豁免   → 过期 defer + on_defer_expire=force_send
    │            └─ 被过滤 defer → enqueue_defer(uid, name)  (R2-D)
    │         4. DND filter                 → is_dnd(uid), emergency 豁免
    │         5. cooldown filter            → _is_ready(name)
    │         6. max urgency 选 winner
    │         7. release_defer(uid, winner) → defer_queue 释放 (R2-D)
    │     ↓ winner.execute(dry_run=False)
    │         → execute_prompt() → _pipeline_send() → perceive_event gate
    │           → conversation_lock → run_llm → record_assistant_turn
    │           → 成功后 _mark(trigger_name)
    └─ legacy asyncio.gather(_check_*...)
          speaking 触发器: legacy_tick_should_send()=False → 让路（no-op）
          maintenance 触发器: 正常执行（不发言，不受 gating/DND 影响）
```

**最终合约**：
- 发言 trigger winner 决策 **只在** `gating._decide()`（含 POLICY_TABLE + defer_queue + DND + state）。
- `_pipeline_send()` / `execution.execute_prompt()` **只负责** send + mark；block/defer 不调 `_mark()`。
- maintenance tick 不受 active-window / DND / defer 误伤（不在 MIGRATED_TRIGGERS，不走发言路径）。
- Watch 独立 `WATCH_EXECUTE_MODE` 是独立面（S4），不属于 R2 统一路径，文档单独说明。
- `policy.py` 是运行时决策权威，被 `gating.py` 通过延迟 import 引用，`loop.py` 不直接引用。

### 当前执行面总览（R2-D 后）

| 编号 | 执行面 | 文件 | 状态 |
|---|---|---|---|
| S1 | **Gating/Proposer live 路径** | `gating.py::run_shadow_tick()` → `execute_prompt()` → `_pipeline_send()` | 生产活跃；winner 通过 `execute(dry_run=False)` 真实发送 |
| S2 | **Legacy `_check_*` gather 路径** | `loop.py::_loop()` → `asyncio.gather(_check_*...)` | 生产活跃；speaking 触发器通过 `legacy_tick_should_send()` 在 live 模式下让路，维护型触发器仍正常运行 |
| S3 | **`legacy_tick_should_send()` 让路垫片** | `execution.py` | 当前 `EXECUTE_MODE="live"` → 返回 False，阻止 legacy speaking 触发器双发 |
| S4 | **Watch 独立 `WATCH_EXECUTE_MODE`** | `triggers/watch.py` | 独立于 `execution.EXECUTE_MODE`；watch 事件驱动触发器（hr_critical/hr_high/sleep_end）在 gating.run_shadow_tick 中被 `WATCH_EVENT_DRIVEN_TRIGGERS` 排除，只走 `on_watch_event()` 路径 |
| S5 | **sensor_aware `output_mode="return"` 旁路** | `triggers/sensor_aware.py` | 调用 `_pipeline_send(output_mode="return", record_turn=False)` 拿 reply，再自行调用 `record_assistant_turn(fanout=["desktop","mobile"])`；不是完整绕过，仍经过 perceive_event gate 和 conversation_lock |
| S6 | **policy.py 決策表** | `policy.py` | **R2-C 完成**；gating._decide() 以 POLICY_TABLE 为单一权威；_pipeline_send 不再参与决策 |
| S7 | **`_pipeline_send` 执行层（仅 send + mark）** | `loop.py::_pipeline_send()` | **R2-C done**：`_legacy_active_window_blocks()` / `_legacy_dnd_blocks()` 已删除；_pipeline_send 不再做 active-window / DND 过滤 |

### 触发器分类表

**类型一：Live Proposer Speaking Trigger（已迁移，gating 执行）**

| 触发器 | 文件 | 让路逻辑 |
|---|---|---|
| morning_greeting, night_reminder, random_message, weather_alert, daily_journal, spontaneous_recall | time_based.py | `legacy_tick_should_send()` 让路 + proposer 接管 |
| diary_reminder, diary_share_reminder | diary.py | 同上 |
| period_reminder | period.py | 同上 |
| birthday_midnight/eve/afternoon/night | birthday.py | 同上 |
| timenode, festival, holiday_boost | timenode.py / festival.py | 同上 |
| reminders | reminders.py | 同上（proposer 路径，after_send 才 mark_done）|
| topic_followup | memory.py | legacy `_check_topic_followup` 是 no-op stub，proposer 接管 |
| garden_bloom | garden_water.py | legacy 通过 `legacy_send` 变量门控 + proposer 接管 |
| garden_harvest_expired/handle_ask/handle_gift/handle_self/vase_wilted | garden_daily.py | 同上 |

**类型二：Watch 事件驱动 Speaking Trigger（独立路径）**

| 触发器 | 执行路径 | 备注 |
|---|---|---|
| hr_critical, hr_high | `on_watch_event("heart_rate", ...)` → `_execute_watch_event(proposal, dry_run=False)` | WATCH_EXECUTE_MODE="live"；gating tick 跳过 |
| sleep_end | `on_watch_event("sleep_end", ...)` → 同上 | 同上 |

**类型三：Sensor 实时 Speaking Trigger（独立路径）**

| 触发器 | 执行路径 | 备注 |
|---|---|---|
| sensor_aware | `_check_sensor_aware()` → `handle_tick()` → `_pipeline_send(output_mode="return")` → `record_assistant_turn(fanout=["desktop","mobile"])` | 独立 8 分钟冷却；不走 gating |

**类型四：Maintenance Tick（纯状态/清理，不发言）**

| 触发器 | 文件 |
|---|---|
| episodic_decay, dlq_monitor | time_based.py |
| log_maintenance | loop.py 内联 |
| episodic_sweep | episodic_sweep.py |
| hidden_state_decay, hidden_state_consolidate | hidden_state_decay.py |
| diary_inject | diary.py（维护型：读日记存 diary_context，无 legacy_tick_should_send 检查）|

### 决策位置表（R2-B 后）

| 决策项 | 位置 | 备注 |
|---|---|---|
| state（CHATTING/QUIET/RESTLESS）过滤 | `gating._decide()` | 软门 |
| cooldown 过滤 | `gating._decide()` → `_is_ready()` | 基于 `_COOLDOWNS` + `_last_trigger` |
| active-window 过滤（proposer 路径）| `gating._decide()` → `_policy_active_window_behavior()` | **R2-B done**；以 POLICY_TABLE 为权威 |
| active-window 过滤（legacy 路径安全网）| ~~`loop._legacy_active_window_blocks()`~~ | **R2-C 已删除**；gating 是唯一权威 |
| DND 过滤（proposer 路径）| `gating._decide()` → `_policy_is_emergency()` | **R2-C done**；emergency 豁免，其余 blocked |
| DND 过滤（legacy 路径安全网）| ~~`loop._legacy_dnd_blocks()`~~ | **R2-C 已删除**；gating 是唯一权威 |
| 高优先级豁免（exempt） | `POLICY_TABLE.active_window_behavior == "exempt"` | hr_critical / period_reminder / birthday 系列全部 exempt |
| priority/urgency 排序 | `gating._decide()` → 按 `urgency` 选最高者 | POLICY_TABLE priority 字段未接入 urgency 排序；R2-C 范畴 |
| defer 队列 | `core/scheduler/defer_queue.py`（内存态） | **R2-D done**；`enqueue_defer` 追踪首次推迟时间；超龄后 force_send / drop；restart 清零（可接受，所有 defer 触发器 TTL 均较短） |

### 执行与 mark 表

| 路径 | 谁调用 send | 谁调用 _mark | 未发送是否 mark | 异常是否可观测 |
|---|---|---|---|---|
| Gating live（execute_prompt）| `execute_prompt()` 调用 `_pipeline_send()` | 仅在 `sent=True` 后调用 `loop._mark()` | 否（write_execute_blocked 记录）| 是（execute_dryrun.jsonl blocked 条目）|
| Legacy speaking（步退）| N/A（live 模式下不执行）| N/A | N/A | N/A |
| Watch event-driven | `_execute_watch_event()` → `execute_prompt()` → `_pipeline_send()` | 仅 sent 后 mark | 否 | 是（log_error）|
| sensor_aware | 手动 `record_assistant_turn()` | 不调用 `_mark()`（无 cooldown 名）| sensor_events.mark_proactive_sent() 8min 冷却 | 是（audit ring buffer）|
| Maintenance tick | 不 send | 立即 mark（不依赖 send 结果）| N/A | 是（log_error 各步独立）|

### R2-B 完成情况（2026-06-11）

**完成项**：
1. ✅ birthday_eve/afternoon/night policy 对齐为 `exempt`（与 `_HIGH_PRIORITY_TRIGGERS` 一致）
2. ✅ `gating._decide()` 接入 POLICY_TABLE active-window filter（proposer 路径主决策）
3. ✅ `gating._decide()` 接入 DND filter（emergency 豁免）
4. ✅ `loop._pipeline_send()` active-window 检查替换为 `_legacy_active_window_blocks()`（policy 委托）
5. ✅ `loop._pipeline_send()` DND 检查添加 `_legacy_dnd_blocks()`（policy 委托）
6. ✅ `_HIGH_PRIORITY_TRIGGERS` 与 POLICY_TABLE exempt 集对齐（无 mismatch）

### R2-C 完成情况（2026-06-11）

**完成项**：
1. ✅ `loop._legacy_active_window_blocks()` 已删除
2. ✅ `loop._legacy_dnd_blocks()` 已删除
3. ✅ `_pipeline_send()` 中无任何 active-window / DND 过滤（执行层仅 send + mark）
4. ✅ 全量发言 trigger（28 个）均有 proposer 注册，均在 `MIGRATED_TRIGGERS` 中
5. ✅ Legacy speaking `_check_*` 通过 `legacy_tick_should_send()` 在 live 模式让路（维护型保留）
6. ✅ `_HIGH_PRIORITY_TRIGGERS` 保留为文档/测试断言常量

**R2-D 完成情况（2026-06-11）**：
1. ✅ `defer` 队列实现（`core/scheduler/defer_queue.py`，内存态，age 跟踪 + 过期 force_send/drop）
2. ✅ `dnd.detect_and_set` 接入 `main.py` owner 消息路径（R2-D 主入口接线）

**仍为遗留项（R2 外或后续迭代）**：
- 120s active window 长度迁入配置（当前硬编码，影响 `_user_active_recently` 默认值）
- POLICY_TABLE `priority` 字段接入 urgency 排序（目前 priority 仅用于 DND emergency 判断；
  urgency 由 proposer 自身决定，priority 未参与 max() 选择）

---

## Pipeline 注入方式

调度器统一从 `pipeline_registry` 获取 Pipeline 实例（R7-B 后已统一）：

```python
# main.py 初始化时注册一次即可
from core.pipeline_registry import register
register(pipeline)
```

`scheduler.set_pipeline(pipeline)` 已降为 **deprecated** 兼容壳，内部委托到 `pipeline_registry.register()`，
调度器不再维护自己的 `_pipeline` 副本。

注入后，调度器通过 `_pipeline_send()` 走完整四步流程生成回复：

```python
# _pipeline_send 内部
_pipeline = pipeline_registry.get()   # 从 registry 读取
context  = await _pipeline.fetch_context(owner_id, prompt)
messages, _ = _pipeline.build_prompt(owner_id, prompt, context)
reply    = await _pipeline.run_llm(messages)
await record_assistant_turn(
    assistant_text=reply,
    uid=owner_id,
    source=TurnSource.TRIGGER,
    trigger_name=trigger_name,
    fanout="all",
    pipeline=_pipeline,
)
```

Pipeline 未注入时降级：直接发送 prompt 原文（不经过 LLM）。

`_pipeline_send` 支持 `output_mode` 参数（默认 `"speak"`）：

| `output_mode` | 行为 |
|---|---|
| `"speak"`（默认）| 生成 reply 后经 `turn_sink` 写入并广播，返回 reply 文本；被 active window 拦截、owner_id 缺失、LLM 空回复或异常时返回 `None` |
| `"return"` | 生成 reply 后经 `turn_sink` 写入但不广播，直接返回 reply 文本；失败时返回 `None` |

`sensor_aware` trigger 使用 `output_mode="return", record_turn=False` 拿到 reply 后，再显式调用
`record_assistant_turn(source=SENSOR, fanout=["desktop", "mobile"], payload={"behavior": action})`，
以便附加 action 包并跳过 QQ。其余所有 trigger 不传这些参数（保持默认 `"speak"` 行为）。

---

## Active Window 与优先级（R2-B 后）

active window 决策已完全收入 `gating._decide()`（R2-C 后），以 `POLICY_TABLE.active_window_behavior`
为单一权威。`_pipeline_send()` 不再参与决策，只负责 send + mark。窗口长度仍为硬编码 120 秒
（迁入配置为 R2-D 范畴）。

发送边界（R2-B 后）：

- **exempt**（用户活跃时也发送）：`birthday_midnight` / `birthday_eve` /
  `birthday_afternoon` / `birthday_night` / `period_reminder` / `hr_critical`。
  policy.py 中 `active_window_behavior="exempt"`，与 `_HIGH_PRIORITY_TRIGGERS` 对齐。
- **defer**（用户活跃时跳过本 tick，等下次重试）：`hr_high`、`weather_alert`、
  `topic_followup`、`reminders`、`diary_reminder`、`diary_share_reminder` 等。
  真正的 defer 队列为 R2-C 范畴，当前行为等价于"等用户不活跃的下一个 tick"。
- **drop**（用户活跃时跳过）：`random_message`、`spontaneous_recall`、
  `festival`、`holiday_boost`、`timenode`、`daily_journal`、花园伴生事件等 filler；
  `sleep_end`（normal + drop，mark_on_drop=False）。
- 普通主动消息被 gating 拦截时，`execute()` 不被调用，`_mark()` 不被调用。
- execute live 路径里，`execute_prompt()` 收到 `None` 后只写 `execute_dryrun.jsonl` 的
  `blocked=true` 观测，不调用 `after_send`，不执行 `_mark()`，也不 `mark_done()`。
- reminders proposal 由 `core/scheduler/triggers/reminders.py` 接管；只有 `_pipeline_send()`
  实际返回 sent 文本后，`after_send` 才会 `mark_done()`。`EXECUTE_MODE="dry_run"` 回滚时，
  `loop.py` 的 legacy reminder 路径也保持相同语义。

当前已收口的“未发送不 mark”语义覆盖 execute live 路径和 reminder 回滚路径：被 active
window 拦截、LLM 空回复或发送前异常时，不调用 execute 的 `after_send` / `_mark()`，也不会把
备忘录标记完成。其他 legacy trigger 若仍在 `_pipeline_send()` 后无条件 `_mark()`，需逐个按 sent
结果继续收口；reminder 旧路径已完成。

---

## 完整触发器列表与冷却时间

| 触发器名 | 冷却 | 优先级 | 所在文件 | 说明 |
|---|---|---|---|---|
| `morning_greeting` | 8h | 低 | time_based | 早安问候 |
| `night_reminder` | 5h | 低 | time_based | 晚安 |
| `random_message` | 4h | 低 | time_based | 随机日间碎碎念 |
| `weather_alert` | 6h | 低 | time_based | 特殊天气联动 |
| `daily_journal` | 1h | 低 | time_based | 叶瑄写今日手账（深夜触发） |
| `episodic_decay` | 20h | 低 | time_based | 情景记忆每日衰减 |
| `spontaneous_recall` | 4h | 低 | time_based | 主动回忆触发 |
| `dlq_monitor` | 24h | 低 | time_based | 扫 DLQ 目录，文件数 > 0 时 log warning；R8-A：legacy task 超 30 天自动归档到 `expired/` |
| `log_maintenance` | 24h | 维护 | loop.py 内联 | 清理 event_log、done reminders、dream archive、inbox/image cache，并压缩 observations |
| `activity_remind` | 20h | 低 | — | 仅预留冷却位，尚无对应实现 |
| `diary_reminder` | 20h | 低 | diary | 提醒用户写日记 |
| `diary_inject` | 6h | 低 | diary | 日记上下文注入 |
| `diary_share_reminder` | 8h | 低 | diary | 很久没看到日记时提一句 |
| `period_reminder` | 24h | **高** | period | 生理期关心 |
| `topic_followup` | 24h | 低 | memory | 未完结话题追问 |
| `birthday_midnight` | 365天 | **高** | birthday | 生日零点告白 |
| `birthday_eve` | 20h | **高** | birthday | 生日前夜预热 |
| `birthday_afternoon` | 20h | **高** | birthday | 生日下午关心 |
| `birthday_night` | 20h | **高** | birthday | 生日夜间收尾 |
| `timenode` | 20h | 低 | timenode | 时间节点感知 |
| `festival` | 20h | 低 | festival | 节日感知 |
| `holiday_boost` | 2h | 低 | festival | 长假加速发送 |
| `episodic_sweep` | 30min | 低 | episodic_sweep | mid_term 老化扫描，aged > 11h 且未晋升的条目批量入队 reflect_to_episodic |
| `garden_water` | 300min | 低 | garden_water | 30% 概率按当前 mood_state 给对应花槽自动浇水 |
| `garden_daily` | 24h | 低 | garden_daily | 扫描 harvest 过期、采后处理、花瓶枯萎 |
| `garden_bloom` | 8h | 低 | garden_water | 开花事件发言名；事件发言前单独 check/mark |
| `garden_harvest_expired` | 4h | 低 | garden_daily | 收获过期事件发言名；事件发言前单独 check/mark |
| `garden_handle_ask` | 4h | 低 | garden_daily | 采后询问用户事件发言名；事件发言前单独 check/mark |
| `garden_handle_gift` | 4h | 低 | garden_daily | 采后送给用户事件发言名；事件发言前单独 check/mark |
| `garden_handle_self` | 4h | 低 | garden_daily | 采后自己处理事件发言名；事件发言前单独 check/mark |
| `garden_vase_wilted` | 4h | 低 | garden_daily | 花瓶枯萎事件发言名；事件发言前单独 check/mark |
| `hidden_state_decay` | 12h | 维护 | hidden_state_decay | apply_time_decay：所有标量向目标半衰期衰减；不发言，stamp_trigger |
| `hidden_state_consolidate` | 7天 | 维护 | hidden_state_decay | consolidate_baselines：sensitivity/touch baseline 轻推向 SCALAR_CENTER；不发言，stamp_trigger |
| `sensor_aware`（tick） | 30s（可配置） | 低 | sensor_aware | sensor 实时状态主动开口，默认关闭 |
| `hr_high` | 30min | 低 | watch | 心率>100 提醒 |
| `hr_critical` | 1h | **高** | watch | 心率>120 告警 |
| `sleep_end` | 2h | 低 | watch | 睡眠结束感知；`admin/routers/watch.py` 合并睡眠片段后回到 `watch.on_watch_event("sleep_end", ...)` |
| ~~`sleep_report`~~ | 20h | 低 | watch | 睡眠报告（未实现，已移除冷却位） |
| reminders（备忘录） | 无冷却 | 低 | loop.py内联 | 到点即发，发完标记完成 |

---

## 冷却状态持久化

旧 `data/scheduler_state.json` 已由 `_migrate_scheduler_state_once()` 在启动时一次性拆分：

- `data/scheduler_cooldowns.json`：canonical，保存 `_last_trigger`
- `data/runtime/scheduler_user_state.json`：runtime，保存 `last_diary_share`、`trigger_state`、
  followed topics 等用户级运行态

迁移成功后旧文件会删除。冷却文件示例：

```json
{
  "triggers": {
    "morning_greeting": 1748000000.0,
    "random_message": 1748003600.0
  }
}
```

用户级运行态示例：

```json
{
  "last_diary_share": 1748001234.0,
  "trigger_state": {
    "1043484516": {
      "state": "QUIET",
      "since_ts": 1748003600.0,
      "last_owner_turn_ts": 1748003000.0,
      "session_turn_count": 0
    }
  }
}
```

启动时自动恢复（`_load_scheduler_state()` 在模块导入时执行），重启不丢失冷却状态。
状态机从 `scheduler_user_state.json` 的 `trigger_state` 段恢复。

---

## 管理面板集成

- `get_status()` → 返回所有触发器的上次触发时间、冷却剩余秒数、是否 ready
- `manual_trigger(name)` → 绕过冷却和条件检查，强制触发部分触发器（管理面板用）

当前手动触发覆盖：`morning_greeting`、`night_reminder`、`random_message`、`daily_journal`、
`period_reminder`、`diary_reminder`、`diary_share_reminder`、`topic_followup`、
生日四段、`timenode`、`festival`、`holiday_boost`。
未覆盖：天气、记忆衰减、episodic sweep、garden_water、garden_daily、watch 事件、DLQ 监控、activity switch 等。

---

## 请勿打扰（DND）模块

文件：`core/scheduler/triggers/dnd.py`

**R2-D 完成：已接入 `main.py` owner 消息路径。**

参见上方 [DND 主入口（R2-D）](#dnd-主入口r2-d) 章节。

`is_dnd(uid)` 已在 `gating._decide()` 的 DND filter 中使用（R2-B 起）；
`detect_and_set(uid, content)` 已在 `main.py::handle_message()` 的 owner block 调用（R2-D）。
`loop.py` 不直接调用 `is_dnd`（DND 决策属于 gating 层，不属于执行层）。

---

## sensor_aware 触发器

sensor 实时状态感知触发器，是"叶瑄主动开口"链路的最终出口。

| 项 | 值 |
|---|---|
| 配置位置 | `scheduler.sensor_aware.enabled` |
| tick 间隔 | `scheduler.sensor_aware.tick_interval_seconds`（默认 30） |
| 默认状态 | **disabled**（`enabled: false`） |
| 启用方式 | `config.yaml` 设置 `enabled: true`，重启服务 |
| 全局发言冷却 | 8 分钟（`_PROACTIVE_COOLDOWN_SECS`，代码常量，不暴露在 config） |
| 所在文件 | `core/scheduler/triggers/sensor_aware.py` |

### 行为级别

| 级别 | score 阈值 | WS action_type | 说明 |
|---|---|---|---|
| `passive_speak` | ≥ 35 | 无 action | 只推 `channel_message` |
| `soft_hint` | ≥ 50 | `pet_emote` | 桌宠表情切换 |
| `attention_grab` | ≥ 65 | `notify` | 系统通知 + 置顶 |
| `direct_act` | ≥ 80 | `execute` | 执行 `behavior_id` 对应动作 |

### 触发链路

```
scheduler._check_sensor_aware()         ← loop.py 每 60s 检查一次（受 tick_interval_seconds 门控）
  → sensor_events.tick()               ← 返回本 tick 候选事件列表
  → sensor_judge.judge(event)          ← 客观评分，附 intent_tier
  → BehaviorPlanner.plan(event, score) ← 硬代码行为决策，score < 35 → 丢弃
  → _pipeline_send(output_mode="return", record_turn=False) ← LLM 生成发言文本
  → record_assistant_turn(source=SENSOR, fanout=["desktop", "mobile"], payload={"behavior": action})
                                      ← 写记忆 + 推 channel_message；passive_speak 不带 action 包
  → sensor_events.mark_proactive_sent()
```

### 与 chat router 的联动

`POST /desktop/chat` 成功处理后调用 `sensor_events.notify_chat_happened()`，重置 `SILENT_TOGETHER` 和 `LONG_FOCUS` 的冷却窗口，避免"人刚聊完立刻被问候"。

### 审计接口

每次 `handle_tick()` 执行完毕（无论走哪条路径），都会向模块级 ring buffer 写入一条决策快照。Buffer 上限 50 条，纯内存，重启清零。

**接口**：`GET /scheduler/sensor_aware/audit`

| 参数 | 说明 |
|---|---|
| `n` | 返回条数，默认 50，最大 50 |
| Authorization | Bearer token（同其他管理接口） |

**响应结构**：

```json
{
  "count": 3,
  "entries": [
    {
      "tick_at": 1747900000.0,
      "candidates": [{"type": "LONG_FOCUS", "narrative": "...", ...}],
      "picked_event": {"type": "LONG_FOCUS", ...},
      "judge_input_prompt": "[SYSTEM]\n...\n\n[USER]\n...",
      "judge_output_raw": "{\"score\": 72, \"reason\": \"专注时间较长\"}",
      "judge_score": 72,
      "judge_reason": "专注时间较长",
      "tier": "medium",
      "candidate_behavior": {"level": "soft_hint", "behavior_id": "focus_acknowledged", ...},
      "pipeline_send_prompt": "（叶瑄觉得该跟她说一句。现在是下午...",
      "pipeline_send_reply": "还在忙？",
      "action_packet": {"action_type": "pet_emote", "params": {"behavior_id": "focus_acknowledged"}},
      "final_stage": "sent",
      "cooldown_remaining_seconds": null
    }
  ]
}
```

字段拿不到时为 `null`，结构始终完整（不省略 key）。

```
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/scheduler/sensor_aware/audit?n=5"
```

**实现位置**：`core/scheduler/triggers/sensor_aware_audit.py`（ring buffer） + `admin/routers/scheduler.py`（路由）。

---

## 新增触发器规范

1. 在 `core/scheduler/triggers/` 下选择合适文件（或新建），优先实现只读 `propose(ctx)`
2. proposal 通过 `proposer_registry.register_proposer()` 注册，并提供 `execute_prompt()` executor
3. 在 `loop.py` 的 `_COOLDOWNS` 字典里加冷却时间；只允许 executor 成功发送后 `_mark()`
4. 需要状态扫描的触发器才保留 `_check_xxx()` 并加入 `_loop()` gather；不要为纯发言新增 legacy 路径
5. 如果是高优先级，明确 `bypass_state_machine`，必要时加入 `_HIGH_PRIORITY_TRIGGERS`
6. 如果需要管理面板手动触发，在 `manual_trigger()` 补充 force 路径
7. 补 proposer / live / blocked 单测，并更新此文档列表
