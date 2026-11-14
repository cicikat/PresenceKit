# VPS Agent 工具事件取证（工单文件 312，正文编号 311）

取证日期：2026-11-14。仅通过既有 SSH 凭据读取实际生产实例，输出经过字段白名单过滤；未导出聊天、模型原文、文件正文、搜索查询或凭据。

## 部署与时区

- 实例：既有 PresenceKit VPS（主机地址脱敏），systemd `presencekit`，工作目录 `<部署目录>`，配置来源 `config.yaml`，Python 3.12.3。
- 12:33 UTC 检查：`active/running`，`NRestarts=0`，当前启动时间 **11:52:42 UTC**。journal 确认 11:52:39 停止、11:52:41 停妥、11:52:42 启动；因此搜索发生在重启前，两次文件任务和 20:07 自主调用发生在同一重启后进程。`NRestarts=0` 不代表从未人工重启。
- `DEPLOYED_COMMIT`：`dec3f16a014c09c47e05820b15011935812734a1`。部署目录没有可用 Git HEAD，不能声称是干净 Git checkout。取证时本地 main 为 `60a2da9`，其后两次提交为记录/工单。
- 将 VPS 实际文件逐字节对照该提交：`device_file_agent.py`、`device_file_tools.py`、`web_search.py`、`tool_dispatcher.py`、`tool_exposure.py`、autonomy `runner.py` / `policy.py` 全部一致。未把部署标记当成所有源码一致的证明，未检查的运行覆盖文件不作断言。
- VPS 为 `Etc/UTC`。七个指定事件的 Unix 时间与 UI 秒数均精确对应 **UI = UTC + 08:00**。这证明这些记录的显示偏移；客户端操作系统时区未独立读取。
- `device_file_agent.enabled=true`；配置中有 proxy 项。未输出代理 URL，未探测或更改代理。独立规划器走 `llm_client.chat(call_category="chat")`；当时实际 provider/preset 的非敏感名称未取得可靠关联证据，不能从当前配置倒推。

## 事件表

ID 仅保留前八位。来源中的 `<角色>/<owner>` 均为同一匹配作用域，未公开真实身份。

| UI (+08:00) | UTC | 工具 / request ID | 外层记录 | 业务证据 |
|---|---|---|---|---|
| 19:45:45 | 11:45:45 | web_search / a47484cc | audit success，trace ok | API ok=false，DDGSException，3553 ms |
| 19:45:51 | 11:45:51 | web_search / 338f78a5 | audit success，trace ok | API ok=false，DDGSException，4063 ms |
| 19:59:48 | 11:59:48 | device_workspace_list / 39bf1eb6 | audit/trace success | 列表调用成功；历史列表内容未持久化，不能还原设备 epoch、alias、grant revision 或写许可 |
| 19:59:52 | 11:59:52 | device_file_task_start / 451a4f8f | success | 创建任务 1d97a353，初始 queued，随后 failed / invalid_file_plan |
| 19:59:56 | 11:59:56 | device_file_task_status / 139f082f | success | 此时任务 1 已终结；其持久状态为 failed / invalid_file_plan |
| 19:59:59 | 11:59:59 | device_file_task_start / b31eabfd | success | 创建另一任务 35c944f5，非 task_already_running；随后 failed / action_budget_exceeded |
| 20:00:06 | 12:00:06 | device_file_task_status / 5fc089c2 | success | 此时任务 2 已终结；其持久状态为 failed / action_budget_exceeded |
| 20:07:11 | 12:07:11 | device_workspace_list / aecf8384 | audit failed / tool_failed，trace error | origin=autonomy_loop；原始返回文本未保留，见下文权限归因 |

audit 的 `before/after/key_params` 未保留本次返回和 task_id。start 与 task 的关联由同作用域、唯一创建时间（52.893 / 59.498 秒）、事件顺序、Work Session 和 payload 交叉建立，置信度高。两次 status 的参数 ID 和完整返回未持久化；表中业务状态来自对应任务的终态，不能冒称恢复了原始 status 响应。

## 文件任务时间线与真实设备证据

1. **任务 1d97a353 / session fafc10ec**：11:59:52.893 创建 → 53.015 claim/start → 53.071 Work Session 创建 → 54.902 Task failed `invalid_file_plan` → 54.919 Work Session failed。
2. **任务 35c944f5 / session 825bdfeb**：11:59:59.498 创建 → 59.598 claim/start → 59.620 Work Session 创建 → 12:00:01.264 Task failed `action_budget_exceeded` → 01.295 Work Session failed。

两份 `device-file-agent.v1` payload 均为 `acknowledged=[]`、`pending=null`、`changes=[]`、`checks=[]`，没有未决写入标记。保存的设备 ACK 数、写入数、校验数均为 **0**。源码在 poll 暴露命令前持久化 pending，接受结果后保留 acknowledged；这些任务没有持久化的已派发命令或成功读取证据。不能把 earlier 任务的 ACK 算到这两次任务中。

每次 find/read/patch/verify 的 op、seq 和起止时间没有历史记录可还原；根据空 pending/ACK 未发现派发证据。桌面日志未取得，**需桌面日志补证**；没有访问源 HTML，不能证明该文件当时或现在的内容/hash，也不能宣称已读取或修改。

部署版 `_worker` 在 `_plan` 前设置 `invalid_file_plan`，之后在 `Action.model_validate()` **之前**设置 `action_budget_exceeded`，宽泛 except 吞掉异常。这是已证实的错误归因缺陷。但当时 JSON / Pydantic 异常类型、校验位置、plan 次数与 seq 未保留：任务 1 的具体格式或模型错误未证实；任务 2 为校验异常误标属于**高概率待补证**，不能写成已证实的 Pydantic 失败或实际执行 30 次动作。

## autonomy 归因

20:07 的真实 audit 只保留 `error_code=tool_failed`，trace 保留 `error`，未保留 `device_file_denied` 原文。两条记录的 origin 分别为 `autonomy_loop` / UI 的 `autonomy`；与 chat 使用同一 owner/character 审计桶，同一重启后进程。

已核对的部署源码明确仅允许文件工具从 `assistant_loop` / `assistant_loop_relay` 进入，并拒绝 autonomy；拒绝发生在文件设备访问之前，所以不是“设备掉线”的证据。结合调用入口，**高概率为 `device_file_denied`**；原始错误码历史证据缺失，不能将源码推断冒充恢复结果。

另一个已证实的代码缺陷：这些工具没有 `live_owner_only` 标记，schema 构建和 autonomy policy 可以将它们暴露给自主模型，随后执行闸门拒绝。修复应收紧暴露面并保留执行拒绝，不能开放自主访问文件。

## web_search 归因

`api_calls-2026-11-14.jsonl`：11:45:45.385 / 11:45:51.715，provider=ddgs，`ok=false`，`output_hint=DDGSException`，耗时 3553 / 4063 ms。journal 对应 11:45:45.390 / 11:45:51.719，均包含 **`DDGSException` 与 `No results found`**。这些消息没有 timeout、proxy、429、403、DNS、TLS 或 curl 标记。

这是 **DDGS 抛异常**，并非库成功返回空列表，也不是已证实的外层 10 秒超时。无证据将其进一步归为代理、反爬、DNS 或上游限流。两次是独立工具调用，相隔约 6.3 秒；没有本工具内自动重试，但无法仅凭此证明模型为什么重复查询。

已证实的误映射：`search()` 捕获异常并返回普通中文字符串，dispatcher 将其包装为默认成功 ToolResult，最终 `tool_executed`；audit 因此记 success，action_trace 记 ok。文件工具则由 `format_result()` 只检查“有 code 且没有 status”判断失败，导致 `status=failed` 的查询仍视为成功。修复后必须让审计与实际返回一致，queued/running 只能说明请求已受理。

## 证据定位与限制

现场源文件均相对 `<部署目录>`：

- `DEPLOYED_COMMIT`；systemd `show presencekit`；journal 的上述 UTC 窗口。
- `data/runtime/tool_audit/<角色>/<owner>/2026-11-14.jsonl`（由部署版 `get_paths().tool_audit()` 定义）。
- `data/runtime/memory/<角色>/<owner>/action_trace.json`。
- `data/runtime/agent_runtime/reality/tasks/<角色>/<owner>/state.json`。
- 同根 `work_sessions/<角色>/<owner>/state.json` 和 `agent_tasks/<角色>/<owner>/<task_id>.json`。
- `data/runtime/observability/api_calls-2026-11-14.jsonl`。

缺失：原始 status 查询参数/响应、历史在线设备及 grant 快照、规划器结构化失败阶段、动作历史、桌面实际日志、源文件现状、当时完整出站链路与模型 preset。禁止用其他日期的记录补成当时事实。

## 最小变更建议

| 优先级 | 后端变更 | 桌面 / 管理面影响 |
|---|---|---|
| P1 | 文件失败/未知状态按明确业务状态映射；返回 request 与 task 两层事实 | 复用现有工具失败展示，无需变更桌面协议 |
| P1 | JSON、Action 校验、计划超时与真实动作预算分开；有界修复；持久化脱敏进度 | 复用 status 与已有只读观测端点 |
| P1 | 编辑能力没有写入回执不得完成，写入必须按实际目标路径 readback 验证 | 保留 epoch/grant/nonce/lease/ACK 规则 |
| P1 | 文件工具从 autonomy schema 隐藏，保留拒绝 | 无授权扩张，无配置开关变化 |
| P1 | web_search 异常返回失败 ToolResult，成功零结果独立标识 | 复用现有审计和活动状态 |

不为排查调整生产网络、代理或模型。没有请求重跑搜索或重放文件任务。没有读取其他功能的私有正文，没有新增清理/删除任务。

## 本地修复与验证

修复范围为后端：文件工具返回请求完成状态及任务业务状态，failed/paused 不再当作成功，未决写入向工具链返回 `outcome_unknown`；审计保留脱敏任务关联与业务错误码。JSON/Action 失败各自归因，有界修复不花费设备动作额度，真实额度耗尽单独覆盖。没有保存写入回执的编辑不能完成；已有按目标路径的 hash readback 规则保留。

状态及既有 `state.read` 观测端点增加当前/失败阶段、规划尝试数、设备动作完成数、最后动作时间与错误类别。autonomy 不再获得文件工具 schema，执行端仍拒绝；web_search 区分网络异常、超时与合法空结果。搜索异常仅记录异常类名；不再调用会携带当前 traceback 的统一错误日志入口，防止把私密查询或代理内容写入日志。

Windows / Python 3.12.13 验证命令（解释器使用项目支持的虚拟环境）：

- `python -m pytest tests/test_tool_incident_312.py tests/test_device_file_agent.py tests/test_tool_schema_portable.py -q --tb=short`：21 passed。
- `python -m pytest tests/test_tool_audit.py tests/test_tool_activity_display.py tests/test_action_trace.py tests/test_tool_exposure.py tests/test_tool_result_pins.py -q --tb=short`：48 passed。
- `python -m pytest tests/test_no_hardcoded_character.py tests/test_r3_scope_lint.py tests/test_line_endings.py -q --tb=short`：23 passed。
- `python -m pytest -m smoke -q --tb=short`：241 passed；此组与定向测试有交集，不能相加作独立用例数。

回归使用真实 dispatcher、HTTP poll/result 与只读观测入口；设备和 DDGS 只在外部边界替换。覆盖失败任务的 dispatch/audit/display 一致性、无效计划恢复、真正额度耗尽、无写入误完成、规划超时、未知写入不重放、owner/character/origin 拒绝，以及搜索异常/超时/空/成功结果。新增未知写入用例短暂撤销状态映射后，在 `tool_executed != outcome_unknown` 的业务断言上失败；恢复修复后通过。搜索日志脱敏用例先捕获到 traceback 中的合成私密文本，改用仅异常类名日志后通过。此前文件失败、计划和 autonomy 暴露用例也已先复现业务红灯。

本次未运行完整全量；相关回归与核心 smoke 已覆盖后端改动。未改管理面静态资源、桌面或手机的授权/传输协议及设置，因此无需客户端版本或静态缓存升级。浏览器可见效果、实际桌面文件操作和真实 DDGS 连通性未验证；自动化覆盖既有展示活动的状态字段，不能代替实机验收。历史缺失证据仍按上文列明，不由测试补成生产事实。

## 生产环境零变更声明

本次没有部署、修改配置/生产数据、重启服务、取消任务、重放动作或写入用户源 HTML；远程脚本通过 stdin 运行，未落盘远端脚本，Python 使用 `-B`。既有服务在取证期间自身正常活动不属于本次修改。

修复预计仅需后端发布；本单不自动部署。回滚以本次修复前源码为基准，只还原本次代码；新增脱敏进度字段应允许旧代码忽略，不能通过删除运行数据回滚。发布是否重启及对在途文件任务的影响由后续部署决定，先等待任务结束，未知写入不重放。
