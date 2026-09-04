# Brief 237：Agent Runtime 接入、观测与旧路径退役

> 状态：implemented；前置：230-236 中对应工单；本工单完成渐进接入和重复路径删除。

## 目标

把新 Task/Capability Runtime 接入现有 tool loop、autonomy、scheduler 和管理面，并在有证据后删除
重复的旧执行路径。

## 接入顺序

1. 内置工具通过 adapter 创建 foreground task；保留现有 `execute(origin=...)` 兼容入口。
2. autonomy 作为 task producer/evaluator 使用统一 capability decision，但不再自建第二套长期任务语义。
3. scheduler 只保留 clock、signal producer 和 maintenance task registration。
4. 管理面增加 task/capability 的只读状态、审计和取消入口；客户端先显示能力状态和失败降级。
5. 完成迁移验证后，删除旧直发 executor、重复 receipt、重复状态判断和无消费配置；同步清理测试与文档。

## EventContext soak 保护

- 新 Runtime 的 task trace 独立存储、独立观测，不进入 EventContext observer 的 ingress/evidence 统计。
- 迁移期间禁止把 task lifecycle 改写成 `kind=stimulus` 或 `kind=tool` event。
- 所有用户可见 delivery 使用新的 ingress/turn；不得复用旧 turn 或在后台任务中调用 `capture_turn()`。
- 217 的 observe/enforcing gate、采样阈值和回退机制不因 Runtime 上线而改变。

## 退役条件

- 新路径完成跨重启、取消、重复请求、Dream block、scope mismatch 和结果未知测试。
- 旧路径连续 soak 后无新增调用，且观测能证明新路径覆盖率。
- 删除旧路径时同时删除其守卫、测试和文档条目；不能留下只测试已删除功能的僵尸测试。

## 跨仓闭环

- 后端：capability/task 设置、只读观测、鉴权 scope、审计。
- 桌面：能力状态、任务状态、取消和降级提示；不暴露 token、路径和原始工具参数。
- 移动端：只消费用户可见任务结果和有限状态，不继承桌面本地 OS 能力。
- 总账：更新 `docs/three-repo-interface-catalog.md`、`docs/feature-control-surface.md`、
  `docs/tools.md`、`docs/scheduler.md`、`docs/known-issues.md`。

## 本次收口证据

- 闹钟到点通过 `talk_gate` 创建新的 Reality ingress/turn，并以 schedule correlation 做幂等；
  `_pipeline_send` 不再是 reminder delivery path。
- `add_reminder` 不再回退到 legacy reminder JSON；runtime 不可用时明确返回失败。
- `manual_trigger` 对 retired/unregistered 名称 fail-closed，不再调用旧 trigger executor。
- `DELETE /observability/agent-runtime-tasks/{task_id}` 提供 admin-scoped durable cancel；
  观测仍只返回 metadata。
- 回归测试覆盖以上退役边界，且 230 task manager、scheduler trigger 和 pipeline gate 测试通过。
