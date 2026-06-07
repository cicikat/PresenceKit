# docs/scheduler.md — 调度器设计

---

## 定位

调度器负责**叶瑄的主动行为**——不等用户发消息，自己在合适的时机触发。

```
core/scheduler/loop.py       ← 主循环 + 工具函数 + 冷却管理
core/scheduler/gating.py     ← proposal 收集、状态/冷却过滤、每 tick 选一
core/scheduler/execution.py  ← proposal dry-run/live 执行收口，成功后才 mark
core/scheduler/proposer_registry.py ← 原生 proposer 注册表
core/scheduler/rhythm.py     ← presence、逻辑日、时间窗比例等节律 helper
core/scheduler/triggers/     ← 各触发器独立文件
    time_based.py            早安 / 晚安 / 随机消息 / 天气 / 日记 / 记忆衰减
    diary.py                 日记相关触发
    period.py                生理期关心
    memory.py                未完结话题追问 / 主动回忆
    birthday.py              生日多段触发
    timenode.py              时间节点感知
    festival.py              节日 + 长假加速
    episodic_sweep.py        mid_term 老化扫描，批量晋升情景记忆
    garden_water.py          花园自动浇水
    garden_daily.py          花园 harvest/vase 每日扫描
    hidden_state_decay.py    用户隐性状态衰减（12h）+ 基线收敛（7d），不发言
    watch.py                 Apple Watch 心率 / 睡眠事件
    reminders.py             到点备忘录 proposer
    sensor_aware.py          sensor 实时状态 → 主动开口（默认关闭）
    dnd.py                   请勿打扰状态（已实现，未接入）
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

## Policy 表（scaffold）

`core/scheduler/policy.py` 目前是 **documentation-by-code / assertions**：用 `TriggerPolicy`
静态表记录每个 trigger 的语义归类、active-window 行为、defer 上限、drop/mark 边界和 cross-mark
约束。

当前状态：

- `POLICY_TABLE` 已记录 25 条配置，但没有接入 live 决策。
- `policy.py` 不被运行时模块 import，不参与 `_pipeline_send()`、gating 或 execute 的实际分支。
- 文件里的断言函数用于后续测试或接入层显式调用；现在不会自动改变调度器行为。
- 因此不要把该表描述成已上线的 defer/drop 引擎。真实 active-window 拦截仍发生在
  `loop._pipeline_send()`。

两条当前定性需要保留：

- `sleep_end`：`priority="normal"` + `active_window_behavior="drop"`，`mark_on_drop=False`。
  `cross_marks=["morning_greeting"]` 只表达“实际 sent 后才联动 mark”；drop 不 cross-mark，
  避免睡醒关心被拦后又压掉早安。
- `hr_high`：语义上是 defer，但 Watch proposal 受 `HEART_RATE_PROPOSAL_TTL_SECONDS=10min`
  限制，`max_defer_age_secs` 只能记为 10 分钟；超过 TTL 后 proposal 返回 `None`，等同过期 drop。

---

## Pipeline 注入方式

调度器有自己的 `_pipeline` 变量，由 `main.py` 初始化时调用注入：

```python
from core.scheduler import loop as scheduler
scheduler.set_pipeline(pipeline)
```

> 注意：这和 `pipeline_registry.py` 是两套机制，调度器用的是 loop.py 内部的 `_pipeline`，不是 registry。

注入后，调度器通过 `_pipeline_send()` 走完整四步流程生成回复：

```python
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

## Active Window 与优先级

当前 active window 的真实实现仍在 `loop._pipeline_send()`，窗口长度为硬编码 120 秒。
`mark_user_active()` 会记录最近 owner 输入时间；目前 QQ owner 消息、桌宠 owner chat 和手机
owner chat 都会更新该时间戳。

发送边界：

- 高优先级白名单用户活跃时也发送：`birthday_midnight` / `birthday_eve` /
  `birthday_afternoon` / `birthday_night` / `period_reminder` / `hr_critical`。
- 白名单未扩大；`hr_high`、`sleep_end`、`reminders`、普通日程问候、花园事件等仍会被 active
  window 拦截。
- 普通主动消息在 `before_send` / `_pipeline_send` 阶段被拦时，`_pipeline_send()` 返回 `None`。
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
| `dlq_monitor` | 24h | 低 | time_based | 扫 DLQ 目录，文件数 > 0 时 log warning |
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

**当前状态：已实现，尚未接入主流程。**

模块逻辑已完整（关键词检测 → 设置 3 小时 DND → 结束词清除），但 `main.py` 和 `loop.py` 均未调用 `detect_and_set()` / `is_dnd()`。如需启用：

1. 在 `main.py` 的消息处理入口调用 `dnd.detect_and_set(uid, content)`
2. 在 `_pipeline_send()` 里检查 `dnd.is_dnd(oid)` 决定是否跳过低优先级触发

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
