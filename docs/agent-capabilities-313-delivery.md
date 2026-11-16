# 313 B/C 本地交付证据与限制

实现范围：授权桌面独立目录/元数据/名称/正文搜索/读页；固定只读或编辑意图；严格单动作规划、有限修复；证据完成条件；同任务追踪和 pending 审计。沿用单角色、Task Manager、Work Session、既有 scope 和设备租约。工具语义与部署/回滚详见 [协议](device-file-agent-contract.md)，能力基线与更新见 [矩阵](agent-capability-matrix.md)。原工单 P0 尚有明确目标/no-change 证明和扫描中及时撤权缺口，不能把本地提交等同整单验收完成。

## 提交组合

- 后端实现：`ae1105be1c41298f7c02b1b134173885679a6cfd`；包含 A `be5bcb7`、B `6aa645e`、注册/翻译修复 `75c372b`、Python 3.10 兼容修复 `1640b53` 及 C。后续交付提交仅更新文档。
- 桌面：`0c1230c5a8a3e8faedd33ed37a8ca70649f847e8`，验证时工作区干净。
- 手机：`601087e7e51ebc7b57e67e2b031c5df61f94e257`，本单未修改；本地另有聊天滚动等未提交改动，不属于本单或本单验证证据。
- 后端保留用户原始未跟踪工单和无关文档删除；均未纳入本单提交。未 push、创建 PR、部署或发布，CI 未运行。三仓固定 SHA 的真实端到端组合仍为 `not_verified`。

## 可复现验证

- Python 3.12.13 / pytest 9.1.1，以及隔离 Python 3.10.20 / pytest 9.2.0：`python -m pytest tests/test_device_file_intents.py tests/test_device_file_agent.py tests/test_device_file_discovery.py tests/test_tool_incident_312.py tests/test_tool_schema_portable.py tests/test_file_path_hints.py tests/test_tool_discovery.py tests/test_tool_activity_display.py tests/test_activity_contract.py tests/test_agent_runtime_architecture_contract.py tests/test_admin_tools_mcp_ux.py tests/test_prompt_language.py tests/test_line_endings.py -q --tb=short`，各 **192 passed**，分别 50.28s / 50.32s。设备边界为合成 HTTP fixture；跨仓 activity 检查只证明其断言范围内的合同，非真实桌面联调。
- 双版本兼容失败组：`python -m pytest tests/test_agent_runtime_process_runner.py tests/test_agent_runtime_agent_tasks.py tests/test_character_self.py::test_list_works_when_self_root_is_relative tests/test_tts_provider_adapter.py -q --tb=short`，各 **46 passed**，分别 40.30s / 35.75s。两项产品修复复用现有真实子进程及 HTTP 用例，没有新增 skip 或放宽断言。
- Python 3.12：`python -m pytest -m smoke -q --tb=short -n 4`，**241 passed in 23.12s**。首次 `-n auto` 遭工具 60 秒超时，不能计作通过。
- Windows：`cargo check --no-default-features`、默认功能的 `cargo check` 均通过；`cargo test device_file_agent --lib --no-default-features` **6 passed**，覆盖真实临时文件、4100 项截断、名称搜索和 patch→verify。`npm.cmd run build` 通过，1137 modules；仍有大于 500 KiB 的 chunk 提示。
- 权限故障注入：临时撤掉 READ_INTENTS 写入拦截，`test_invalid_read_plans_fail_without_device_side_effects` 在真实 HTTP poll 断言失败：只读任务下发 create。恢复守卫后重新验证。故障注入未保留在交付代码中。
- 最终实现 `ae1105be1c41298f7c02b1b134173885679a6cfd`：`python -m pytest -n auto -q --tb=short`，每版本 8 workers，TEMP/TMP 指向仓库内 `.tmp`。Python 3.12 **8391 passed / 13 subtests passed**，1554.69s；Python 3.10 **8390 passed / 1 error / 13 subtests passed**，1560.25s。没有新增 skip。3.10 全量仍非全绿，不用后续单项通过覆盖这一结果。
- 3.10 唯一 error 为 `tests/memeval/test_memeval.py::test_memeval_case[ms-05-episodic-plus-profile-natural]` 的 fixture setup：`_production_data_entries` 用 rglob 先遍历、后过滤共享 `data/test_sandbox`，目录被并行测试清理时抛 `FileNotFoundError`。同版本隔离重跑该实际用例 **1 passed in 17.13s**。这是既有测试快照的并发竞态，本单不修改该 fixture；独立追踪见 known-issues。本轮全量没有产品断言失败。

首次全量 `python -m pytest -n auto -q --tb=short`（每版本 4 workers）：Python 3.12 **4 failed / 8387 passed / 13 subtests passed**；Python 3.10 **11 failed / 8380 passed / 13 subtests passed**。三项为新增工具缺架构登记及双语描述，已由 `75c372b` 修复并纳入 192 项回归。相对 self 根测试在临时目录与仓库分处不同盘时失败，使用仓库内 TEMP/TMP 后通过。Python 3.10 其余六项进程/Agent task 失败，根因为环境清理丢失 Windows `SystemRoot`，独立子进程探针已确认；语音节点失败为旧版 `asyncio.TimeoutError` 未被捕获，两项均由 `1640b53` 修复并通过上述 46 项回归。

## 状态与示例

- 列目录：`device_workspace_list` 后调用 `device_file_list_dir`，path 为授权内相对目录，query 空，options 为 `{}`。空目录返回成功空列表。
- 找 HTML：`device_file_find_name`，query 为 `**/*.html`，options 为 `{"mode":"glob"}`。继续使用同 grant 的 next_cursor；truncated 不代表全部。
- 查正文：`device_file_search_text`，query 为待查文字，options 为 `{"mode":"literal","content_search":true}`；返回路径、行和有限摘录。
- 阅读摘要：独立 `device_file_read`，或 `device_file_task_start(operation_intent=read)`；零写入且有新鲜证据可完成。
- 修改：`operation_intent=edit`，实际保存 ACK + 各已写路径匹配 hash 核验后才返回 saved_and_verified。
- 排队/未知：accepted 仅表示接单；queued/running/paused 继续同 task_id。outcome_unknown 先 reconcile，不自动重放写入。

## 明确未验收或未实现

真实 Windows Tauri→HTTP→聊天模型授权目录联调：**pending physical verification**。Rust 测试真实落盘与 Python HTTP 模拟分别证明各自边界，不能拼接成真实设备回执。macOS、真实低能力模型、三端聊天可见结果和计费外部服务均 not_verified。

管理面新增五项工具的中英描述，已更新 i18n 缓存版本 `device-file-reads-313-1`。使用真实 `i18n.js` / `tools.js` 渲染入口搭建隔离预览，但 Pencil 浏览器连接失败（desktop transport 未连接）。**浏览器实测未完成**；替代证据为双语注册回归和源码/缓存检查，不能代替可见页面验收。预览进程已停止。既有只读观测由 HTTP 回归验证。

无新手机执行协议；Android 无本机文件执行器，消费后端工具结果。三仓固定 SHA 的端到端协议证据仍 not_verified，交付组合必须同时记录 dirty 工作区限制。

仍缺 no-change 证明、显式复合必要目标清单、批量变更、逐条桌面取消、聚合质量指标和受限进程/浏览器 POC。独立范围见 [后续工单历史](https://github.com/cicikat/PresenceKit/blob/a0596b25fd56c10386d1719145e53f0ed00a853a/cc-tasks/313-agent-capabilities-followups.md)：D0/F0 保持 P0 遗留，D/E 为 P1，F 为 P2。不得把任一已保存文件视为自然语言全部子目标的完成证明。3.10 全量 fixture 竞态和浏览器/实机缺口也仍未关闭。
