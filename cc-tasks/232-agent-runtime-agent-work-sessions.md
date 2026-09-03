# Brief 232：Agent 工作会话与 Authored Material

> 状态：implemented；前置：230、231；本工单定义“非聊天 LLM 工作”，不开放任意文件或进程执行。

## 目标

把角色日记生成、资料整理、索引构建等需要 LLM 的后台工作定义为独立 Agent Work Session，
避免伪装成聊天 turn。

## 合同

工作会话拥有独立 `work_session_id` 和 `task_id`，输入是 bounded context，输出是指定 artifact。
它不能调用 `record_assistant_turn()`，不能写 `short_term`、`event_log`、episodic 或 identity。

允许的输出目标必须由 capability manifest 声明，例如：

- authored diary
- document summary/index
- temporary workspace artifact

不允许模型自行指定任意落盘路径或改变目标 realm。

## 日记迁移

复用现有 `_generate_and_store_diary()` 的事实层/感受层分离和 integrity check，但把调度、任务状态、
LLM 调用和 artifact 写入拆开。`inner_diary_write` 仍是静默维护任务；`daily_journal` 仍走 proactive signal。

## 验收

- 工作会话失败不会产生 assistant turn 或伪造用户消息。
- 工作输出可查询、可重试、可标记 unknown，但不会被自动提升为用户事实。
- EventContext observer 不记录 work session 为 ingress 或 evidence。
- 只有明确的独立固化流程才能把工作产物写入长期记忆。
