# 提醒对象（272 A）

`add_reminder` / `update_reminder` 支持 `target=user|self`。新调用应明确选择；省略时保持旧语义 user，历史正文不自动分类。list/get、prompt、恢复历史与只读 character-reminders 观测包含对象。

user 到期走自然语言生成及既有 talk gate；self 到期走冻结角色的 Path C 工具链，限定本角色 self 工具与有权限的 Agent task 生命周期工具。需要该角色启用 tool loop 与 function_calling preset；缺少前置条件则保持待重试。权限、workspace grant 与工具开关仍由原执行闸门决定。工具结果失败时可自然说明阻碍，提醒投递完成不等于事项执行成功，实际工具状态查 action trace / Agent task 观测。

生成失败保留待重试，不再在第三次后发送硬模板。生成成功后缓存脱敏回复，投递失败时复用，避免因通道失败反复执行工具。发送前复核 revision/取消；更新后不复用旧缓存。进程在工具完成但回复尚未缓存时中断，仍可能重试，应优先使用幂等 self 编辑与有 idempotency 的任务；不保证外部副作用 exactly-once。

没有新增配置开关或跨端协议。已存在且误分为 user 的条目需按 schedule_id、expected_revision 更新为 self；不能从正文可靠推断。
