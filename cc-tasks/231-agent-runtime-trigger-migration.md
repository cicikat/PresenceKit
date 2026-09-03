# Brief 231：Scheduler Trigger 分类与 Task/Signal 迁移

> 状态：implemented；前置：229、230；本工单只迁移调度职责，不恢复旧直发执行器。

## 代码事实

`core/scheduler/gating.py` 当前已有四类状态：

- `migrated`：主动消息来源，需转为 autonomy signal。
- `maintenance-only`：后台状态维护，不应进入 `talk_owner`。
- `retired`：旧执行器名称，仅兼容审计，不可执行。
- `active`：autonomy 原生 interval/schedule、desktop wake、restart 等入口。

## 目标映射

### Maintenance Task

以下保持静默 worker 语义：

`diary_inject`、`episodic_sweep`、`inner_diary_write`、`hidden_state_decay`、
`hidden_state_consolidate`、`event_log_salvage`、`memory_janitor`、`garden_water`、
`garden_daily`、`storyline_weekly`、`spend_monitor`、`interest_seed`、`practice` 等。

它们写各自的 state/artifact，不创建 assistant turn。

### Proactive Signal

早晚问候、随机消息、心率、未完结话题、主动回忆、desktop wake、出梦问候、生日/节日、
花园事件、陪玩评论等继续经过 signal-first autonomy：

```text
producer -> bounded signal -> opportunity merge -> autonomy evaluation -> talk_owner（可选）
```

### Retired

`scheduler_pipeline_send`、`manual_direct_trigger` 继续拒绝为旧直发执行器；兼容入口只能转为
signal 或返回明确的 test-only 排队结果。

## 23 点日记

- `inner_diary_write`：Task Plane 的 Agent-authored maintenance task。生成结果写 authored diary，
  不发言、不产生 Reality turn、不写 evidence。
- `daily_journal`：Proactive signal。由 autonomy 决定是否向用户发送；发送时创建新的 Reality turn。

二者必须保持不同 task/signal 名称和观测计数。

## 验收

- 每个 trigger 在 `TRIGGER_MIGRATION_STATUS` 中有唯一归属。
- 同一 tick 不会同时执行旧直发和 autonomy delivery。
- maintenance task 不受 `proactive=off` 的发言闸影响，但仍受自身开关和失败策略约束。
- 迁移期间保留旧行为基线测试和 EventContext 非污染测试。
