# 输入来源、开发隔离与只读任务加固

本次实现不以正文、role、通道或 owner token 证明真人作者。服务端 provenance 投影进入
事实边界、pretool 观测和工具审计；未知入口诚实返回 unknown，真人作者统一 unverified。
关键词不再产生 must_call_tool 或 required_tool_names，仅保留候选和实际执行结果。
角色在既有授权内仍可自主查证。开发身份使用独立 scope/入口，禁止正式上下文、工具和归档。
桌面必填冻结 operation_intent，在写入和 journal 之前独立拒绝只读任务的 create/patch/undo。

## 验收与证据边界

| 场景 | 证据 | 结果 |
|---|---|---|
| 条件回查不强制 | test_keyword_candidates_do_not_establish_user_obligation | 候选不产生调用义务 |
| 明确回查可检索 | 同用例的明确祈使用例、既有 Path C/探针用例 | schema 可用；不伪造真人归因 |
| 记忆命令不升权 | test_reference_claims_cannot_replace_server_input_provenance | 资料不改服务端来源和系统边界 |
| 外部开发 Agent 不进入正式会话 | test_development_ingress.py 的真实挂载 API | 专用及混合 scope 均拒绝三个 owner 入口，沙盒无正式副作用 |
| 伪称用户要求不产生归因 | 候选参数化回归与 grounding 用例 | 网页/历史命令不产生 required 状态 |
| 只读任务无写 | test_invalid_read_plans_fail_without_device_side_effects；Rust read_task_commands_cannot_mutate_a_writable_grant | 后端不下发写；设备在可写 grant 上仍独立拒绝写，无 journal/文件变更 |
| 目标不明真实暂停 | test_ambiguous_target_pauses_same_task_without_dispatch | 同 task paused/target_needs_clarification，无命令 |
| 编辑无写不能完成 | test_failed_file_work_is_failed_at_dispatch_audit_and_observation[no_write] | no_write_performed，无完成声明 |
| 自主矛盾查证可执行 | 候选/事实边界、既有 Path C 执行用例 | 未增加点名工具或逐次确认要求；真实模型自主选择质量仍待验收 |

权限修复先红：关键词五例原 must_call_tool=True；混合开发 scope 原可进入未初始化生产
pipeline；设备只读命令原返回 ok；模型隔离回归曾返回 502。恢复修复后各对应业务断言通过。
此处仅记录合成测试，未向正式私聊注入验收消息，未改私人角色卡、记忆或历史。

Python 3.12.13 / pytest 9.1.1：定向 232 passed（31.63s），smoke 241 passed
（51.63s）；随后新增澄清暂停回归，文件意图、portable schema、activity 跨仓静态合同、
架构与换行组 121 passed（33.43s）。Windows Rust device_file_agent 模块 7 passed。
不把 Python 模拟设备和 Rust 文件测试拼成真实 Tauri 端到端验收。

验证前基线：后端 `63f8cf39972437180a890f02118c61d274801010`，桌面
`0c1230c5a8a3e8faedd33ed37a8ca70649f847e8`，手机
`43232e283e9c3649dbf19e7798d90b3fb013c4db`。后端/桌面包含本次未提交差异，
固定提交组合的实际 Windows 联调为 not_verified；最终 SHA 与 CI 以 PR 为准。
手机不消费新执行字段，本次无手机实现改动。无新增运营开关，无静态资源改动。

## 两个历史窗口的只读核对

2026-11-15 从既有 VPS 读取部署标记和工具 audit 元数据，无模型调用、会话重放或写操作。
读取时服务 active，DEPLOYED_COMMIT 为 `3be197b823f7c2cb07f1087e17456832646b0321`；
这是读取时版本，不能证明事件发生时部署版本。

- 2026-11-14 22:26（UTC+08，前后各三分钟）：14:25:44 UTC workspace_list 成功；
  14:25:47 task_start 的 business_status 为 queued；同一 task 前缀 e453c36c 在
  14:25:53 为 paused/target_needs_clarification。14:26:55 autonomy_loop 被
  device_file_denied 拒绝。14:27:07 新启动请求为 task_already_running，原任务仍暂停。
  queued 仅为接单，不能据此宣称文件已读或已改。
- 22:46 同样窗口未找到 tool_audit 条目。此结果只表示该日志窗口无匹配记录，不能证明
  未发生对话、工具意图或其他链路活动。

旧 audit 无 input_source/human_authorship 字段。两个窗口的原始指令来源和真人作者
均为**不可确认**；assistant_loop/autonomy_loop 只能证明执行链，不能倒推真人委托。

## 发布与尚未验收

先后端、后桌面；回滚先桌面、后后端。旧桌面忽略 intent，无法提供设备独立守卫；
新桌面拒绝缺 intent 的旧后端命令。详见 [文件协议](device-file-agent-contract.md)。
真实 Windows 端到端、模型行为质量、最终双版本 CI 和生产部署状态需按实际后续结果更新，
本报告不将它们标为完成。原 313 的 no-change/复合目标/扫描撤权缺口仍见既有交付报告。
