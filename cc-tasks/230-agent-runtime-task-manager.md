# Brief 230：Agent Runtime Task Manager 与 Receipt

> 状态：implemented（Reality-only foundation；Dream/Agent session/capability workers remain roadmap）；前置：229。

## 目标

建立统一的持久任务生命周期，覆盖一次性任务、后台任务和需要 Agent 参与的工作会话。

## 现有代码基线

- `core/autonomy/store.py` 已有 owner/character scoped job、lease、TTL、retry 和 run 记录，但语义绑定 autonomy opportunity。
- `core/scheduler/loop.py` 以 60 秒 tick 驱动多个 `_check_*`，维护任务和主动发言共用 tick 但不共用状态。
- `core/companion/store.py` 已有 running/terminal/unknown 的 receipt 语义，可作为 outcome-unknown 参考。

## 设计

新增独立 Task Manager（命名和目录由实现前代码审计确定），至少支持：

- `task_id`、`uid`、`char_id`、`realm`、`capability`、`source`
- `created / queued / running / succeeded / failed / canceled / expired / outcome_unknown`
- lease、attempt、TTL、cancel request、bounded result metadata
- `causation_ref` 只保存来源 turn/signal 的脱敏引用
- 原子落盘、幂等创建、重启恢复和孤儿 running 处理
- 统一查询接口和只读观测，不返回正文、token、完整路径或原始工具输出

## EventContext 约束

- Task Manager 不调用 `capture_turn()`，不追加 Memory Event evidence。
- Reality turn 创建 task 时只传入不可变来源引用。
- task 完成后需要发消息时，调用现有 Reality ingress adapter，创建新的 EventContext。
- Dream task 若未来存在，必须使用独立 store 和独立 capability allowlist。

## 验收

- 重复 client action 不创建第二个 task。
- 进程重启后旧 running task 变为 `outcome_unknown`，不自动重跑副作用任务。
- 取消、过期、lease 丢失和未知结果均有稳定状态和观测。
- EventContext observer 的 ingress/evidence 计数不因 task lifecycle 增长。
