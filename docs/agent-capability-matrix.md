# Agent 能力矩阵（313 · PR-A 基线及 B/C 更新）

审计基线：后端 `9e1b30c`，远端 main `ca76514`（已包含 PR #10）；两者业务代码一致，差异为接口总账的既有文档条目。桌面 `3bee952dbb8ba2c1b75d8234ac6b162fbda5ec87`。下表的 enabled 是准入条件，不能据此推断生产开关、设备在线或实际执行成功。测试只使用合成身份、临时工作区和模拟设备边界。

状态：I=implemented（实现存在），W=wired（真实入口接线），E=enabled（需满足的开关/授权），T=tested（本轮证据），G=gaps。未实测的平台明确为 not_verified。

下表保留 PR-A 的审计基线；B/C 的实现与验证更新以文末「本轮更新」为准，不能把基线缺口当作当前结论。

| 注册能力 / 实现 | VPS 后端 | Windows 桌面 | macOS | Android | 聊天主链 / autonomy | I / W / E / T / G |
|---|---|---|---|---|---|---|
| `device_workspace_list`；`core/tools/device_file_tools.py`、`core/agent_runtime/device_file_agent.py` | 返回设备授予的工作区摘要，不列 VPS 目录 | Rust `device_file_agent/mod.rs` 上报 opaque grant，物理根留本机 | 同一 Rust 实现；not_verified | 可通过聊天请求在线桌面；无本机文件执行器 | owner Path C native/relay；autonomy 拒绝 | I/W=yes；E=`device_file_agent.enabled`、active owner/char、设备启用与 token；T=3 个真实 dispatcher/HTTP fixture；G=不是 list_dir |
| `device_file_task_start/status/control/cancel`；同上 | Task Manager + 同角色 Work Session；启动只 queued | poll/result，经 epoch/revision/lease/nonce/ACK 执行 | 同一实现；not_verified | 不承担文件动作 | 主链直接暴露；autonomy 拒绝 | I/W=yes；E=同上；T=read→patch→verify、重复 ACK、错误 token、格式恢复；G=仅编辑，0 写入会失败 |
| 设备 Action `find/read/patch/create/undo/verify`；桌面 `device_file_agent/files.rs` | 仅转发，不把 VPS 当本机 | cap-std nofollow、根 inode/device、hash、版本日志；UTF-8 ≤256KiB，单页 ≤8KiB/100行 | 有 Users/APFS 处理及测试源码；not_verified | 无对应执行器 | 只能经编辑规划器间接调用 | I/W=yes；E=工作区/read-write grant；T=Python 协议模拟；Rust 本轮尚未运行；G=find 混合名称/正文、无目录/stat/glob/grep 独立工具，分页缺 has_more |
| `fs_list/fs_read`；`core/tools/fs_browse.py`、dispatcher | backend 只读可用，受脱敏/owner/char 边界 | 仅后端在本机且 external allow_roots 授权时访问本机 | 后端所在机器；not_verified | 不访问手机文件系统 | info 分类发现；autonomy 仍检查普通权限和上下文 | I/W=yes；E=backend/external 授权；T=源码核验；G=remote_server 禁 external，不能读取桌面磁盘 |
| `workspace_list/read/create/update/delete/undo`；`core/agent_runtime/workspace.py` | remote_server 禁用 | 本机后端的工作区能力，不是远程桌面能力 | 本机后端；not_verified | 无执行器 | 主链/允许的自治工具共用 registry 与 grants | I/W=yes；E=workspace roots/operations/owner/char；T=workspace 定向回归通过；G=部署位置限制 |
| `process_run`；`core/agent_runtime/process_runner.py` | remote_server 明确禁用 | 本机后端运行工作区允许程序；不是桌面 poll 执行器 | not_verified | 无执行器 | 普通 dispatcher；受工具权限与 process/workspace manifest 约束 | I/W=yes；E=默认关闭；T=受限进程回归通过；G=VPS→Windows 无 headless 进程协议，不能以此宣称 Windows 无执行方案 |
| `start_agent_task/get_agent_task/cancel_agent_task`；`agent_tasks.py` | remote_server 禁用 | 本机后端 coding/inspect 工作会话可 read/create/update/run | not_verified | 经聊天发起后端任务，不在手机执行 | 主链及已准入 autonomy；同一 Task Manager | I/W=yes；E=agent_tasks 开关、manifest revision/expiry；T=agent_tasks 回归通过；G=启动 receipt 与任务结果必须区分 |
| `agent_session_start/status/control`；`agent_sessions.py` | 可编排在线设备 | 长任务持久会话，pause/resume/steer/cancel，承接 Computer Use | GUI 支持须查平台适配；not_verified | 不是手机自动化会话 | owner live chat；单角色主体 | I/W=yes；E=computer_use/device 授权；T=源码核验；G=未做真实窗口验收 |
| `computer_use_*`；`computer_use.py`、桌面 computer_use | 设备 broker，不操作 VPS GUI | 已有截图、设备动作、ACK 与取消链 | not_verified | 独立 phone control 能力，不视为同一执行器 | owner/chat 准入；不把 file 原语路由到截图 | I/W=yes；E=设备本地授权与连接；T=源码核验；G=真实 GUI 目标未验收 |
| `browser_automation`；`core/agent_runtime/browser.py` | 后端浏览器适配；不是桌面登录态 | 后端执行环境决定浏览器位置 | not_verified | 不代表 Android 浏览器 | dispatcher 的受限浏览器入口 | I/W=yes；E=browser 配置、域名/动作授权；T=browser 定向回归；G=真实联网/登录态未验收 |
| `web_search` / 网页抓取 | 搜索 provider 与文本获取各自有网络依赖 | 不依赖桌面截图 | 同后端 | 同后端 | info 工具与来源隔离 | I/W=yes；E=provider/网络；T=312 失败语义回归；G=DDGS 故障不是本地文件故障，备用 provider 是独立需求 |
| 动态 MCP；`core/mcp_client.py` | 由后端连接的外部工具 | 只有实际连接本机 MCP server 才拥有本机能力 | not_verified | 无自动继承 | 统一 registry/discovery，exposure/allow_tools/local policy/proficiency | I/W=yes；E=配置、连接、发现与策略；T=源码核验；G=源码有客户端不等于真实 server 已连接 |
| 分类工具发现；`core/tool_discovery.py` | 只加载 schema，无业务动作 | 不访问设备 | 同左 | 同左 | native/relay 共享轮内暴露面；autonomy 有独立上下文限制 | I/W=yes；E=tools/exposure；T=discovery 定向回归；G=加载成功不等于用户目标成功 |
| `self_*` / self DB / recipes；`core/character_self.py`、`core/self_tool_recipes.py` | 角色 self 桶可用 | 所在机器仍是后端 | 同后端 | 同后端 | owner/char/Reality grants；不提升至任意文件或 shell | I/W=yes；E=有效 self grant/工具权限；T=源码核验；G=不能代替用户本机工作区 |
| `list_artifacts/read_artifact/update_artifact` 等；`core/tools/chat_artifacts.py` | 聊天产物的服务端副本 | 下载副本与服务端副本不是同一文件 | 同左 | 同左 | artifacts 分类、owner/char 范围 | I/W=yes；E=artifact grants；T=源码核验；G=编辑服务端副本不证明桌面文件保存 |

## 路由与完成语义审计

`file_path_hints.prompt_hint()` 当前把桌面文件引到 workspace_list/task_start；`pipeline` 11.6 层已有同 task_id 跟踪和排队提示。`device_file_tools.register()` 正确把 workspace/status 标为 read，把 start/control/cancel 标为 write；缺口在独立只读设备入口。`format_result()` 固定 `request_status=completed`，虽然 `task_status` 保留 queued/running，仍有混淆。dispatcher `tool_executed` 和 tool audit `success` 表示调用成功，不能用作任务完成凭据。

`start_agent_task` 与 `agent_session_start` 也是异步接单，必须查询原 task/session；不能因为仍在运行重建任务。现有 Task Manager 重启将 running 转 outcome_unknown，禁止不确定写入重放。设备写 journal/ACK 恢复路径必须保留。

## 失败复现与验证（PR-A）

- 结构化复现路径：合法设备 grant → start → planner read → 设备 ok/ACK → planner done。`_worker` done 分支先判断 `changes`，所以任意这样的零写入任务都落 `failed/no_write_performed`；这个结论来自当前控制流，不是对历史用户目标的猜测。
- 历史任务 `7dd2604b…` 的单次读取和 ACK 仅证明设备读取链成功。一次 JSONDecodeError、三次规划、零写入/校验不足以认定格式错误是最终根因，也不足以恢复用户原始意图。
- Windows / Python 3.12.13：`.venv/Scripts/python.exe -m pytest tests/test_device_file_agent.py tests/test_tool_incident_312.py tests/test_agent_runtime_process_runner.py tests/test_agent_runtime_agent_tasks.py tests/test_agent_runtime_workspace.py tests/test_agent_runtime_browser.py tests/test_tool_discovery.py -q --tb=short` → **75 passed**。协议设备为 HTTP fixture；不是实机联调。
- Python 3.10、macOS、Android 本地动作、真实 Windows 授权目录联调：**not_verified / pending physical verification**。本轮不读取生产角色、私人文件或 VPS 数据。

## 后续独立交付

PR-B 补授权设备原子发现及双版本拒绝；PR-C 补 typed intent、严格计划解析、证据完成条件与一致状态。P1 文件成组操作和 Windows 受限进程 POC 保留独立工单；P2 聚合成功率/延迟与列表体验需独立验收。删除候选：在新发现入口和回归完全接替后，删除旧 find 混合扫描实现及其专用守卫/文档；本单未授权删除该兼容协议。

## 本轮更新（313 B/C）

| 能力 | I / W / E | 实际验证 | 仍有缺口 |
|---|---|---|---|
| `device_file_list_dir/stat/find_name/search_text/read` | implemented/wired；沿用 owner/char、总闸、设备 grant，effect=read，无规划模型 | Python 3.10/3.12 dispatcher + 鉴权 HTTP；Windows Rust 临时文件扫描、分页、搜索、保存回读 | 真实 Tauri→HTTP→主链联调 pending physical verification；macOS not_verified |
| typed inspect/search/read/edit/create | implemented/wired；固定意图、只读禁止写、默认 legacy edit | 零写成功、空匹配、读证据延迟查询及撤权、编辑保存核验、提前 done 恢复 | no-change 证明及显式复合目标清单未实现，保持 P0 遗留；不声称完整自然语言目标验证 |
| 严格 native tool / fenced JSON 规划 | implemented/wired；有限格式/完成条件修复和独立调用预算 | 重复键、混合动作、多调用、空响应、坏 schema、超时、不确定写入回归 | 真实低能力模型 not_verified |
| queued/running/paused 与最终结果 | implemented/wired；accepted / task_status / counters / next_action；审计 pending | 排队不冒充完成、同 task_id、失败/未知回执回归 | 三端主界面可见结果未验收 |
| 本地 process / browser / Computer Use | 复用现有执行器与权限；路由提示已区分 | PR-A 原有模拟回归 | Windows headless 受限进程、浏览器 provider POC 为独立单，不能声称本轮新增远程 shell |

Python 3.10/3.12 最终定向各 192 项通过，兼容失败组各 46 项通过；Windows Rust 6 项及两种功能构建检查、前端构建通过。管理面双语描述与缓存版本已同步，但浏览器实测未完成。扫描中逐条撤权/取消仍为 P0 遗留；真实设备与 macOS 验收未完成。完整命令、版本、提交组合、全量结果及未验证项见 [313 交付报告](agent-capabilities-313-delivery.md)。
