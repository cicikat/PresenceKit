# Signal-first Autonomy

2026-09-13：静默轮已获得的 safe_summary 可在后续主动/owner 对话中作为带时间的历史工具结果接续。
未读上传与就绪生活记录也随原主动机会提供，成功模型评估后确认已读；不新增 signal、不强制发言。
资料工具继承可读授权，显式禁用优先。保留/撤回/观测与三端验收见 [资料接续](media-continuity-2026-09-13.md)。

本文定义 v1 的 proactive 工作边界。scheduler 或 sensor 可以报告事实，但不能生成面向用户的一句话。`core.autonomy` 是唯一的 proactive 决策与交付路径；`talk_owner` 是唯一的用户可见出口。

`tool_eligibility()` 只判断一项工具能否被 autonomy allowlist 显式打开；已连接的全局只读 MCP 仍可经 `global_read_inheritance` 进入工具面，不要求 `mcp_explicit`。schema、`GET /admin/autonomy/tools` 和管理面矩阵共用 `AutonomyToolDecision`（`allowed`、`decision_source`、global/deployment/self-capability/MCP/autonomy policy、danger、confirmation）。执行前仍复查当前矩阵，展示允许但执行时撤权记 `tool_call_denied`。

## 版本化契约

Signal 使用 `autonomy-signal.v1`：

```json
{
  "version": "autonomy-signal.v1",
  "source": "sensor|scheduler|desktop_wake|interval|schedule|overflow",
  "evidence": [{"fact": "..."}],
  "reason": "A bounded explanation for considering this opportunity.",
  "expiry": 0,
  "priority": 0.0,
  "memory_query": null,
  "action_mode": "none|reflect|use_tools|talk",
  "signal_id": "stable-per-signal-id",
  "expires_at": 0,
  "urgency": 0.0,
  "confidence": 0.0,
  "suggested_action": "silent|message|question|suggestion|tool_then_talk",
  "created_at": 0,
  "id": "stable-per-signal-id"
}
```

`evidence` 是系统提供的事实数据，不是用户陈述。`expiry` 是 Unix timestamp；零表示没有显式过期时间。`memory_query` 是可选的、有 anchor 的查询，不能从 greeting 或 time-of-day label 推断。

同一个 scheduler tick 产生的 signal 会在 job 入队前合并为一个 `autonomy-opportunity.v1`：

```json
{
  "version": "autonomy-opportunity.v1",
  "signals": [],
  "priority": 0.0,
  "reason": "Combined bounded reasons.",
  "expiry": 0,
  "memory_query": [],
  "action_mode": "none|reflect|use_tools|talk",
  "created_at": 0,
  "id": "opportunity-id"
}
```

Opportunity 持久化在 durable autonomy job 中。runner 接收完整 opportunity，执行有界 memory recall（`allow_strengthen = false`），并接收明确的本地 reality timestamp。普通 model text 是私有的；它可以使用 autonomy allowlist、调用一次 `talk_owner`，或无用户可见消息地结束。

runner 使用的 prompt projection 有意小于普通 chat prompt。它包含系统观察到的 activity、有界的 profile/mid-term/history 层，以及系统执行的 `memory_query` 层。Recall card 保留 source、event/recorded timestamp、speaker provenance、strength 和 source-turn ID。缺失或未知 provenance 不是有效的历史 anchor；signal evidence 始终标为 candidate reason，而不是过去的对话。成功的 tool result 和 active hardware job 是独立的事实层；两者都不要求产生用户可见消息。`talk_owner` 会拒绝没有 grounding 的 “I remember”/“you said” 等 unsupported memory claim。

Scheduler 和 sensor adapter 位于 `core.autonomy.signal_adapters`。它们只为 routine/time-background、heart-rate state change、memory reactivation、unfinished topic、desktop reopen 和 runtime restart 产生有界事实。同一 15 分钟 opportunity window 内的 candidate 按 stable routine key，或按 `reason` 与 memory key 去重。scheduler 的 `_check_*` module 是配置的 morning/night/midday/random routine fact 的唯一 producer；runner 不会再合成第二份基于时钟的副本。Routine fact 默认使用 `action_mode=none`，绝不会强制 `TALK`。过期 candidate 在入队前丢弃；urgency 可以提高记录的 priority，但绝不能绕过 Dream、active-user、conversation 或 budget gate。

### Desktop reopen signal

`POST /desktop/wake` Path B 是 signal producer，不是 assistant-turn executor。现有 perceive-event dedupe 和 Dream Guard 接受请求后，`enqueue_desktop_wake_signal()` 保存一个 `desktop_wake` signal，使用 `action_mode=reflect` 和十分钟 TTL。Evidence 只包含 reopen fact、有界的 offline duration（最多 30 天）、安全的 perceive event ID，以及 perceive dedupe key 截断后的 SHA-256 fingerprint。不会把原始 `last_seen`、原始 `last_seen_at`、request body 或 dedupe key 持久化到 signal。

HTTP response 只确认已入队，不承诺会说话。Signal 可以在下一个 tick 与其他 candidate 合并，runner 可以静默结束、只使用工具，或调用一次 `talk_owner`。Perceive duplicate 和 Dream block 都不会入队。若 autonomy 已关闭，不保存 wake signal；若入队后 autonomy 被关闭，则删除 pending wake，并记录 terminal suppression。过期 wake signal 也会得到 terminal job/run record，job 创建后发生的 Dream block 是 terminal 而不是 retryable。这些 one-shot 规则防止旧的 reopen 在稍后重新启用时触发。

当 opportunity 同时包含 desktop reopen 和其他 source 时，Dream block 按 signal 分别应用。被阻塞的 parent job 始终完成，其 `desktop_wake` signal 收到 terminal `not_replayed` event；仍然有效的 non-wake signal 合并为一个有界的 child retry job。Child 使用剩余 signal TTL 中最短的一个（绝不延长 parent TTL），记录 `retry_parent_job_id` / `retry_parent_run_id`，并保留正常的 Dream retry backoff。拆分前已过期的 non-wake signal 另行收到 terminal `expired` event。纯 non-wake opportunity 继续重试原 job；纯 wake opportunity 不创建 child。

Memory reactivation 复用 scheduler recall ledger，并使用独立阶段。选中 candidate 时，将其 stable memory key 写入 opportunity evidence；完成系统 recall 时报告 `memory_read`；第一次完成 model evaluation 时记录 `memory_candidate_evaluated`；只有已交付的 `talk_owner` call 才写入现有 successful-recall ledger，并报告 `memory_recall_talk_sent`。静默、阻塞、失败和取消的 delivery 不会冒充成功回忆。最近已经 evaluation/recalled 的 memory 会在 recall window 内被抑制，除非调用方提供显式的新 anchored context，例如新的 owner turn 或新 evidence。

Autonomy 启用时，scheduler 的 native proposal pass 仍作为只读 shadow audit 保留。它不会执行第二条 proactive turn；该 tick 的唯一 evaluator 和 delivery path 是 autonomy runner。

## 已退役的直接执行器

面向 scheduler 的 conversational trigger 现在只是 compatibility producer。如果旧 callback 到达 `scheduler._pipeline_send`，callback 的 prompt 会被丢弃，并为下一个 autonomy tick 持久化一个有界 signal。它不会进入 LLM pipeline 或 channel。Runner 一次 drain 所有 pending signal，将它们合并为一个 opportunity；`talk_owner` 仍是唯一的用户可见出口。

当前迁移覆盖 routine greeting、night/midday cue、fixed random message、普通 heart-rate/sensor attention、recall/follow-up、calendar reminder 和 birthday candidate。Birthday 与 serious health candidate 保留更高的 signal urgency，但不绕过 autonomy admission、talk gate、conversation serialization、active-user cancellation 或 proactive ledger。

手动 scheduler trigger 会排队同类型的 opportunity，绝不强制直接发送 assistant message。Delivery 还会记录 opportunity correlation ID；已被 claim 的 ID 会在下一次 `talk_owner` send 前拒绝。

`manual_trigger("period_reminder")` 是同一 signal-first 合约的输入校验例外：owner 没有
`last_period_date` 时返回稳定原因 `missing_period_date` 且不入队；有日期时只创建 autonomy
opportunity，不恢复旧的 `_pipeline_send` 直发路径。

## Unified Effective State

`GET /admin/autonomy/effective-state`（`state.read`）是 scheduler/autonomy control surface 的只读生效状态契约。它是管理页面读取开关的唯一后端入口，返回 `contract_version`、配置值、effective runtime value、override source、`restart_required` 和唯一 runtime consumer。契约还包含 scheduler task availability、autonomy queue/circuit、`talk_owner` gate、全局发送 cooldown、autonomy evaluation/daily talk budget，以及每个 trigger 的 `migrated` / `maintenance-only` / `retired` / `active` lifecycle status。

顶层 `proactive.state` 只使用 `enabled`、`disabled`、`unavailable`、`queued`、`running`、`cooled_down`、`blocked`。`proactive.reason` 是当前最先命中的阻断原因，因此客户端不需要拼接多个 status/config/ledger endpoint 来猜测“为什么没有主动行为”。所有这些配置都是 hot-reload 或 durable autonomy state，`restart_required` 当前为 `false`。

`POST /scheduler/trigger/{name}` 与 `POST /admin/autonomy/test-enqueue` 是 test-only 入口。它们只排队事实/测试任务，response 明确标记 `direct_delivery=false`；生产发送仍必须经过 scheduler tick、autonomy admission 和 `talk_owner`。

## 可观测结果

`GET /observability/autonomy-opportunities`（scope `state.read`）返回脱敏的 lifecycle stream。`status` 字段含义如下：

| Status | 含义 |
|---|---|
| `unevaluated` | Signal/opportunity 已入队或当前被 lease。 |
| `evaluated_silent` | Opportunity 已评估，但没有选择发送消息。 |
| `tools_completed_no_talk` | Tools 已完成，但没有调用 `talk_owner`。 |
| `talk_sent` | `talk_owner` 已通过 `turn_sink` 交付。 |
| `canceled_user_activity` | 真实用户 turn 优先，取消了本次运行。 |
| `expired` | Signal/opportunity 在评估前达到 TTL。 |
| `admission_blocked` | 未进入模型：用户活跃 / Dream / budget / duplicate / circuit 等 admission 门。 |
| `blocked_or_failed` | 进入评估后的 gate、lease、model 或 tool failure。 |

### 评估预算与 admission 记账（Brief 224）

- `daily.evaluations` **只统计进入模型评估的 run**。纯 admission 拦截（如 `blocked_user_active`、`duplicate`、`suppressed_daily_budget`）只写 `sources.*.last_attempt_at`，**不**增加 evaluations，也**不**推进 `last_evaluated_at` / min_interval 冷却。
- 若当日 `talks == 0`，evaluation budget **不得**把整天静音；`talks > 0` 且 evaluations 用尽时才返回 `suppressed_daily_budget`。
- Activity 僵尸 session 由 `find_active_session` 按类型 TTL lazy-expire；`dream_seed` 放弃/蒸馏失败也会关闭 session，避免永久挡住 autonomy。

Prompt snapshot 仍位于现有 admin-only run prompt endpoint 后面，不包含在这个 state-read surface 中。

拆分出的 Dream retry 在此 surface 上通过 child opportunity 的 `retry_parent_job_id` / `retry_parent_run_id`，以及 parent run 的有界 signal terminal/child-queued event 关联。不存在单独的 wake 或 retry ledger。

对于 `desktop_wake`，安全 signal ID 就是 HTTP `correlation_id`；signal 还携带 perceive event ID 和 dedupe fingerprint。现有 perceive-event audit 记录 accepted、duplicate 和 Dream-blocked gate result，而 autonomy opportunity/run record 显示 merge membership 和 terminal disposition。Path B 不引入单独的 wake ledger。

## Migration Registry

### Scheduler winner adapter (Brief 195)

Tick-based migrated proposals remain subject to the existing scheduler state,
DND, active-user, cooldown, budget and winner competition. Only the selected
winner reaches `emit_scheduler_proposal_signal()`; it is projected as bounded
factual evidence and stored for the next `autonomy.runner.tick()`. The adapter
never reads a legacy prompt factory or executor. `festival` carries a stable
festival key, local reality date and calendar source; `period_reminder` carries
only its `current|upcoming` stage and a bounded elapsed-day value. `dream_exit`
retains its `dream_id` lifecycle evidence.

`GET /observability/autonomy-opportunities` additionally returns content-free
24-hour and 7-day funnel aggregates by source. They distinguish queued signal,
opportunity creation, admission, blocking, model silence, tools-only, talk-gate
rejection and delivered talk; prompt snapshots remain admin-only.

`core/scheduler/gating.py::MIGRATED_TRIGGERS` 是 retired-speech registry。集合中的名称仍可能出现在 cooldown、proposer 或 audit code 中，但 gating layer 永远不会运行它们的 executor，兼容性 `_pipeline_send` boundary 只能持久化 signal。范围包括 time-based greeting/reminder 和 recall、watch 与 sensor event、diary 与 period reminder、overflow、presence nag、dream exit、festival/timenode、garden event、coplay commentary。`letter_writer` 是 `active` executor（SMTP 投递，不经 autonomy signal）。发言冷却按角色键 `{char_id}:{name}` 写入；必要的跨触发器全局限流仍是 `ProactiveLedger.can_send(uid=)`，不是全局 `_mark(name)` 双写。

Maintenance-only task 有意不在该 registry 中。例如 `diary_inject`、episodic/log cleanup、memory janitor、event-log salvage、hidden-state decay/consolidation、storyline aggregation 和 garden state maintenance。它们继续修改自己拥有的 state，但不会创建 assistant turn 或进入 `talk_owner`。

本设计不引入全局 EventBus，也不引入 model-visible trigger tool。

## Autonomy XML compatibility and cadence (2026-09-12, partial)
Autonomy opts into existing XML tool encoding in chat_turn; ordinary native-only callers keep
strict FC semantics. Parsed tools remain bounded by the exposed names; private prose never sends.
Existing run.events now retain safe evaluation_error/error_type metadata. No new store or scope.
Runtime settings were updated through admin APIs and read back: interval 30 minutes, 48 evaluations/day,
global proactive gap 45 minutes; daily talk cap 8 and evaluation minimum interval 15 minutes unchanged.
open: restart backend to activate code and observe real delivery/circuit recovery. No real test message sent.
Screenshot planning remains separate: desktop visual sampling is shadow-only and local consent is off;
mobile offers a text snapshot, not this requested on-demand image capture. See desktop docs/proactivity-2026-09-12.md.


## 按需截图三端接入（2026-09-12，partial）

详见仓库 `docs/screen-observation-2026-09-12.md`。后端 `observe_user_screen` 通过独立 HTTP poll/result 请求活跃电脑或手机的新截图，UUID/凭据绑定、20 秒 TTL、30 秒设备新鲜度和本地授权均参与门控；图像只在内存中处理。

管理面提供全局开关、effective state 与 `/perception/screen/status` 无正文观测；电脑视觉观察页、手机系统配置页各有独立本地授权，默认关闭。全局开启时自主工具继承启用，显式工具禁用优先；角色消息继续走原通知/免打扰链路。桌面 IPC 新增可选 onDemandEnabled；手机使用专用 screen_observation 通道与无障碍 worker，不改 mobile poll/ack/relay。

实现及构建/定向测试通过，真实双设备、锁屏、OEM 后台及 VLM/消息联合验收保持 open。管理面既有国际化测试 3 项失败保持 open，详见施工记录，不能将静态检查作为真实设备验收。
