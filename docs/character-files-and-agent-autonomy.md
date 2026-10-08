# 单角色文件、自有空间与 Agent 自主能力（现行合同）

272 A：提醒新增对象 `user|self`，到期分流、失败重试与权限边界见 [reminder-target.md](reminder-target.md)。

状态：**H current**。工单 [256](../cc-tasks/256-character-files-and-agent-autonomy.md) A–H 已落地。
日期：2026-09-18。基线 SHA：`b1ebe0d`（工单源码表对照 `e35d0ec`，差异见 §0）。

权限限制能做什么，不规定角色应该追求什么。仍是同一角色的聊天主链与持久工作副链，不引入第二个人格。

历史权威：[agent-runtime-architecture.md](agent-runtime-architecture.md)。Brief 229 当时不新增端点、设置或客户端字段，那是 229 自身的历史事实；256 续篇若落地 durable 状态，必须同单提供只读观测。不得把 229 的“不新增端点”读成禁止后续 brief 增加观测。

---

## 0. 前单复核与基线差异（A1）

前单 [255](../cc-tasks/255-backend-audit-session-scope-work-order.md) 状态：A/B/C/E/F/G/H/I/J 已提交。未完成项只剩运行联调：D3 真实部署、D4 跨端/真机、I 退场阈值未批准。本单不替前单勾选这些运行项。

与工单现状表（对照 `e35d0ec`）在当前 HEAD `b1ebe0d` 的剩余缺口：

| 落点 | `e35d0ec` 工单记录 | `b1ebe0d` 仍真 |
|---|---|---|
| `fs_browse.py` | allow_roots 强制；`data/` 整类拒绝；`token` 子串 deny；无统一脱敏 | **B current**：backend/external 独立开关；allow_roots 为发现提示；统一脱敏；`remote_server` 可读本进程 backend，拒外部本机路径 |
| `toybox.py` | 三枚举、4000 字、无自由建/删 | **H current**：`file_key` 接受 self 相对路径；三个旧 key 仅作兼容别名；容量由统一 self 配额控制 |
| `very_formal_project_dir()` | 无 `char_id`，共享目录 | 仅供离线迁移/回滚读取；live 读写者已删除 |
| `toy_autogrow.py` | 直写旧目录并裁头 | **D current**：统一 self writer append，不裁头；`enabled: false` 停用习惯 |
| `reminder.py` | add 走 Runtime；get/mark_done/prune 仍 legacy JSON；add 用 `DEFAULT_CHAR_ID` | **H current**：list/get/add/update/cancel/restore 全部走 Runtime；legacy reader、mark_done/prune 旁路及 scheduler 回退已删除 |
| `_TOOL_REGISTRY` | reminder 仅 `add_reminder` | **E current**：六件套已注册；模型不能指定 principal |
| work sessions | digest/长度，有限 artifact kind | **F current**：增加 `agent_task_result`；私有 payload 保存有期输入/结果，receipt 仍无正文 |
| process_runner | 受限 interpreter/program/args，无 shell | **F current**：worker 复用受限 workspace program adapter；仍无 shell |

255 已改变、工单表未写的相关事实：session scope v1 可冻结 owner/char；workspace 突变 `causation_ref.kind=tool_request`；tuple `execute()` 已删；autonomy 决策矩阵统一 `allowed+decision_source`。本单 grant 必须吃冻结 principal，不得再让模型或 wrapper 默认 `DEFAULT_CHAR_ID`。

---

## 1. 空间与能力合同（A3 / A4）

| 空间 | 目标 | 读 | 写/删 | 授权来源 | 部署 |
|---|---|---|---|---|---|
| **backend** | 任意时刻可读本进程系统代码、配置、日志及内部文件；主动循环须明确告知可用 | 默认开；配置可关 | 否。core / 鉴权 / 安全策略不可改 | 服务端 frozen principal；配置 `backend_read` | remote_server 可读**自身**后端资料，不等于操作用户电脑 |
| **external** | OS 权限允许的本机普通文件 | 用户提供路径是通常用法；allow_roots 降为常用目录/发现提示，不是唯一准入 | 否 | 同上；配置 `external_read` | 仅本机。`remote_server` 拒绝。UNC/网络认证不当作普通本机文件 |
| **self** | Reality `owner+char` 持久自主文件空间 | 本角色默认可读 | 默认可自主 CRUD/move/可逆删除；管理员可撤销。不依赖 danger 模式 | 默认 grant，管理员可撤；self 权限不能向外传播到 workspace/core | 本机后端。remote_server 可写**自身** self |
| **workspace** | 用户/项目授权工作目录 | 操作级 grant | 操作级 grant + 确认 + manifest + 预算 | 显式 roots/permissions；与 self 隔离 | 延续 Brief 233；`remote_server` 关 |
| **core** | 系统代码、鉴权、安全策略、授权来源 | 走 backend 只读+脱敏 | 角色及其任务均不可自行修改；与 workspace 路径重叠时保护规则优先 | 管理员/部署，不是角色工具 | 全部署 |

隔离不因“后端可读”取消：共享系统资料可读；其他角色私有桶与 Dream 隔离资料仍按既有合同保护。禁止通过外部绝对路径、junction、symlink、hardlink、ADS 或路径替换别名绕过 owner/char/realm 边界。

手机/桌面提供的路径是客户端资料，不伪装成服务器同名文件。

### 1.1 Principal 与授权

- grant 由服务端从冻结 session / `execute_structured(user_id=, char_id=, origin=)` / TaskPrincipal 取得。
- 模型参数不得指定 `owner` / `uid` / `char_id` / `realm`，不得自签授权，不得把 `confirmed=true` 当票据。
- 读取脱敏与写入授权分开：能读 backend 不等于能写 workspace；能写 self 不等于能跑 process。
- 自主创建可执行文本 ≠ 获准执行；`process_run` 必须再次走 Runtime capability。
- **角色自制工具 = 声明式配方（T3），不是代码**：`self_tool_define/list/run`，配方是 self 空间里的 `tools/<name>.json`
  （`{name, description, params, steps[{tool,args}]}`，`args` 里 `{参数名}` 只替换已声明参数，值不二次展开）。
  步骤工具必须是本轮该角色实际暴露的（`tool_exposure.resolve` + 调用方 allowlist），不得属于 `system`/`browser`/`phone_control`/
  `self_management`、不得是 `self_tool_*`（禁递归）、不得需要确认（`dangerous`）、不得带 `user_id/char_id` 等身份参数；≤5 步。
  define 时校验，run 时**重新校验**（配方可被 `self_update` 改写）；每步走 `execute_structured(origin="assistant_loop")`，既有闸门/审计照常，
  任一步失败即停并返回已完成步骤与失败原因。autonomy 与群聊不可 run。观测：`/observability/character-self` 的 `self_tools`（配方名，不含步骤）。

### 1.2 稳定拒绝码

所有角色可见失败返回稳定 `code`（snake_case），不回原文秘密、不回绝对内部路径（self 相对路径与 workspace 已授权相对路径除外）。

| 码 | 含义 |
|---|---|
| `backend_read_disabled` | 配置关闭 backend 读 |
| `external_read_disabled` | 配置关闭外部读 |
| `disabled_remote_server_local_capability` | 远程部署拒绝本机外部/workspace/process/browser |
| `path_not_found` / `not_a_file` / `not_a_directory` | 目标不存在或类型不符 |
| `unsupported_file_type` | 非文本且无安全解析器 |
| `file_size_limit_exceeded` / `read_budget_exceeded` / `list_limit_exceeded` | 配额 |
| `sensitive_redaction_failed` | 脱敏失败；**拒绝，不退回原文** |
| `high_risk_secret_denied` | 私钥/密码库/keystore 等整体拒绝或隐藏 |
| `credential_store_denied` | 浏览器凭据库等 |
| `cross_owner_denied` / `cross_char_denied` / `realm_forbidden` / `dream_isolation_denied` | 隔离 |
| `unc_network_denied` / `ads_denied` / `device_path_denied` / `reparse_denied` | 路径形态 |
| `grant_principal_mismatch` | 模型试图指定 principal |
| `self_revoked` / `self_path_denied` / `self_escape_denied` | self 撤权或逃逸 |
| `revision_conflict` | `expected_revision` 不匹配 |
| `quota_exhausted` | 字节/数量/历史耗尽；可查询，不静默删角色记录 |
| `audit_store_denied` | 试图经 self 工具改审计/版本库 |
| `confirmation_required` / `confirmation_ticket_invalid` / `confirmation_replay_denied` | 真实用户确认 |
| `shell_unsupported` | 不提供无限 shell |
| `task_unauthorized` / `workspace_grant_required` / `budget_exhausted` | Agent task |
| `os_permission_denied` | OS 权限不足，明确报错 |

---

## 2. 角色可达能力矩阵（A2）

管理 API 存在 ≠ 角色可调用。下表“角色可达”指 `_TOOL_REGISTRY` + origin 闸门 + schema 暴露后，角色在聊天或 autonomy 中可 `execute_structured()`。

### 2.1 执行闸门（现状，本单不改语义）

`_EXECUTE_ALLOWED_ORIGINS`：`user_live`、`assistant_loop`、`assistant_loop_relay`、`autonomy_loop`、`admin_console`、`assistant_self_management`、`autonomy_self_management`。未知 origin fail-closed。self-management origin 只允许 `manage_self_capability`。

`char_id`/`user_id` 由 dispatcher 注入 wrapper，不来自模型 JSON。群聊禁止 memory/event/artifact 若干读工具。Intiface 工具另需 opt-in。danger 模式约束 `desktop`/`system`/`phone_control`。

### 2.2 `_TOOL_REGISTRY`（角色工具）

| 工具 | category | 效果 | 确认 | 角色来源 | 物理路径 | 回滚 | 观测 |
|---|---|---|---|---|---|---|---|
| `get_time` | info | read | 无 | 实时 | 无 | 无 | 无独立端点 |
| `weather` | info | read | 无 | 网络 | 无 | 无 | api-calls |
| `web_search` | info | read（结果可入 vector `source=web`） | 无 | 网络 | vector_store | 不固化 identity | api-calls |
| `list_reminders` / `get_reminder` / `add_reminder` / `update_reminder` / `cancel_reminder` / `restore_reminder` | info | read/write | 无 | frozen 会话 uid+char | Runtime `agent_runtime_schedule_state` + Task Manager lease | cancel 可恢复；完成 lease 不可复活 | `GET /observability/character-reminders` |
| `water_garden` | info | write | 无 | 会话 char | garden 角色树 | 花园自身状态 | garden 管理面 |
| `drink_with_user` | info | write | 无 | 会话 char | `drinking_state` | 自然衰减 | `/observability/drinking` |
| `read_life_records` | info/memory | read | 无 | frozen uid+char | life records | 无 | life-records observability |
| `read_xiaohongshu` | info | read | 无 | 用户 URL | 无持久私有桶 | 无 | api-calls |
| `read_diary` / `search_diary` | info | read | 无 | 配置日记根 + char | 日记文件 | 无 | 无正文观测 |
| `backfill_diary` | info | write | 无 | frozen 会话 uid+char；仅 owner 私聊 | 角色日记文件 | 失败可重试，已有文件不覆盖 | Agent Runtime task / work-session 只读端点 |
| `read_watch` | info | read | 无 | owner 健康 | health_state | 无 | watch/sensor |
| `get_profile` / `get_episodic` | memory | read | 无 | Reality scope | `user_memory_root` | 无 | memory 管理面 |
| `search_documents` / `read_document` / `search_character_notes` / `reread_image` | memory | read | 无 | uid+char library | character_library | 无 | `/observability/character-library` |
| `search_events` / `expand_event_window` / `get_related_events` | memory | read | 无 | Reality ledger | event_store | 无 | memory-event-*（无证据正文进 prompt） |
| `revise_memory` / `forget_episodic` / `clear_midterm` / `revise_user_profile` | memory | write | 既有 provenance | uid+char | memory 树 | 修订/墓碑，非物理清证据 | provenance / memory |
| `desktop_*` / `play_song` / `toy_invite` / `dream_invite` | desktop | actuate | danger 模式 | 通道 | 无服务端文件（降级 `agent_actions.json`） | 无 | deployment-capabilities |
| `peek_screen_content` / `observe_user_screen` | desktop/perception | read | 开关+冷却/授权 | 本机设备 | realtime / perception | 无 | screen status |
| `device_shutdown` / `device_sleep` / `exit_yandere` | system | actuate | danger + 确认 | 本机 | 无 | 无 | meta-mode |
| `phone_control_start` | phone_control | write | 既有 | 手机 adapter | `phone_control_tasks` | 任务终态 | phone 任务文件（管理） |
| `toy_vibrate` / `toy_stop` / `toy_pattern` / `toy_job_status` | desktop | actuate/read | Intiface opt-in + owner 私聊 | owner | `hardware_jobs` | stop | `/hardware/jobs` |
| `read_toy_file` / `write_toy_file` | info | read/write | 无 | frozen uid+char；self 相对路径，兼容三个旧 key | `character_self_root` | self revision/trash | character-self |
| `fs_list` / `fs_read` | fs | read | 无（不受 danger） | frozen uid+char；backend 默认开；external 跟随 `external_read`/`enabled` | 仓库/`data`/config（脱敏）+ 本机普通文件 | 无 | `GET /observability/backend-read` |
| `workspace_list/read` | fs | read | grant | workspace roots | 用户目录；版本在 runtime | 无 | `/observability/agent-runtime-workspace` |
| `workspace_create/update/delete/undo` | fs | write | update/delete/undo 需 `confirmed` | 同上 + Task receipt | 同上 | undo 最近版本 | 同上 + tasks |
| `process_run` | system | execute | dangerous | workspace 内程序 | workspace | 无通用撤销 | `/observability/agent-runtime-processes` |
| `browser_automation` | browser | write | 高风险 one-shot | 域名 allowlist | 私有 profile | 无 | 设置面；旧 observability 路由已 retired |
| `manage_self_capability` | self_management | write | expected_revision + action_id | overlay grant | self_management state | revision CAS | character-permissions |
| `self_db_tables` / `self_db_create_table` / `self_db_insert` / `self_db_query` / `self_db_update` / `self_db_delete` / `self_db_drop_table`（T2） | self | read/write | where 必填；无原始 SQL | uid+char，同一 self grant | `data/runtime/self_db/{char}/{uid}/self.db`（self 根之外） | drop 进回收区，TTL 同 self trash | `/observability/character-self`.`self_db` |
| `write_artifact` / `update_artifact` / `read_artifact` / `list_artifacts` | artifacts | write/read | 无 | uid+char | `chat_artifacts` | 非 workspace | `/observability/chat-artifacts` |
| 动态 `mcp__*` | mcp | 按 local policy | 按 policy | MCP session | 外部 | 未知结果 fail-closed | MCP 设置 |

**C current：** `self_list` / `self_read` / `self_create` / `self_update` / `self_move` / `self_delete` / `self_restore`（category `self`，T1 起从 `info` 拆出；写工具默认 grant、不经 danger 闸，autonomy 沙盒白名单含 self 写）。**E current：** reminder `list/get/add/update/cancel/restore`。**F current：** `start_agent_task` / `get_agent_task` / `cancel_agent_task`。

### 2.3 Autonomy 可用集合

`core.autonomy.policy.tool_eligibility()`：危险/需确认工具一律不准入。write 沙盒白名单 **`water_garden`、self 写工具、reminder 写工具与 Agent task start/cancel**。显式 autonomy allowlist 或只读 MCP `global_read_inheritance` 才能进入 schema。backend 读已随 B 进入聊天/autonomy 发现面；**F current** 对 `start_agent_task` 做了单独准入，仍需角色 capability、显式 autonomy allowlist、deployment、workspace manifest 与每步实际操作权限的交集，不会因为聊天 Path C 可见就自动无人值守写 workspace/process。self 默认可整理自己的笔记；workspace 写与 process 仍要 grant。

### 2.4 post_process writers（非角色工具）

| handler | 写什么 | accessor | 本单 |
|---|---|---|---|
| `toy_autogrow` | self 笔记 + owner/char scoped 冷却；不裁头 | `append_self_text` / `character_self_meta_root()` | **H current** |
| `capture_turn_retry` | 重试 capture_turn | `user_memory_root` | 不改 |
| `summarize_to_midterm` / `reflect_to_episodic` / `consolidate_to_identity` / `storyline_evicted_input` | 记忆 | `user_memory_root` | self 内容不自动固化 |
| `user_profile_update` / `trait_tracker_update` / `update_char_relations` / `practice_session` / `consistency_check` | 各域 | 既有 | 不改 |

### 2.5 Scheduler writers

migrated 触发器产 signal，不直发。maintenance 工人写各自状态（garden、hidden_state、storyline、event_log_salvage、memory_janitor、inner_diary_write、interest/practice 等）。**E current：** 到期投递由 `_check_reminders()` 读 Runtime `due_across_owner()`，`begin_delivery` / `finish_delivery` 复核 revision/取消；shadow `reminders` 提案 `execute=None` 且 `observation_only`，不入 autonomy 发言队列、不写 legacy JSON。`agent_runtime_schedule_state` 已登记 DataPaths/registry。

### 2.6 Runtime adapters

| adapter | origin/char | realm | 部署 | 读写执行 | 路径 | 确认 | 回滚 | 观测 |
|---|---|---|---|---|---|---|---|---|
| task_manager | 调用方 principal | reality | 全 | 生命周期元数据 | `agent_runtime/reality/tasks/{char}/{uid}` | 无 | cancel；orphan→`outcome_unknown` | GET tasks；DELETE 取消为 **admin** |
| work_sessions | 同上 | reality | 全 | 会话+artifact 元数据 | `work_sessions/{char}/{uid}` | manifest kinds | 无内容回滚 | GET work-sessions |
| workspace | tool_request fingerprint | reality | 本地 | 分操作 grant | 配置 roots；versions per_char_user | 写/删/undo | 最近版本 undo | GET workspace |
| process_runner | lease | reality | 本地 | 受限执行 | workspace 内程序 | dangerous 工具 | 不宣称可撤销副作用 | GET processes |
| browser | 指纹 | reality | 本地 | 隔离 profile | 非 data 根 | 高风险 one-shot | 无 | 设置面 |
| scheduler_capability | frozen principal | reality | 全 | create/get/list/update/cancel/restore；交付 begin/finish；list 含安全正文投影 | `agent_runtime_schedule_state` | 无 danger | cancel 可恢复；完成 lease 不可复活；重复轮次新 task | `GET /observability/character-reminders` |

管理面 `DELETE /observability/agent-runtime-tasks/{id}` 是 admin 取消，不是角色工具。

---

## 3. Self 持久空间（A4）

Reality 的 owner+char 桶，经 `get_paths()` accessor 与 `data_registry` 登记。

建议物理布局（测试沙盒自动偏移）：

```text
data/runtime/self/{char_id}/{uid}/           # 用户内容 canonical
  AGENT.md
  notes/  habits/  ledger/                   # 示例，无业务枚举限制
data/runtime/self_meta/{char_id}/{uid}/      # 系统维护，self 工具不可写
  revisions/  trash/  audit.jsonl  quota.json
```

| accessor（C current） | durability | domain | scope | git |
|---|---|---|---|---|
| `character_self_root(uid, char_id=)` | canonical | reality | per_char_user | ignore |
| `character_self_meta_root(uid, char_id=)` | runtime | reality | per_char_user | ignore |
| `character_self_audit(uid, char_id=)` | forensic | reality | per_char_user | ignore |

`AGENT.md`、`notes/`、`habits/`、`ledger/` 只是示例目录，无业务枚举。角色可自由组织相对路径。禁止 self 指向系统文件、其他角色桶、workspace roots、Dream 树或 meta 库。

旧 `data/very_formal_project/` 不会复制给每个角色。H current：首次盘点冻结配置默认角色+owner；导入 `self/notes/` 可重入，冲突源保存在 `self/notes/legacy/` 的哈希命名文件，不覆盖较新 self 文件；autogrow 冷却迁入 scoped self meta。生产 live 源已在备份及恢复校验后清理，备份保留 90 天；回滚只恢复档案备份，不覆盖 self 文件。

Never Prompt Load：`self_meta` 的 revisions/trash/audit 默认不进 prompt；角色读 self 用户文件须走 B 单脱敏。

---

## 4. 配额与预算（A5）

耗尽返回 `quota_exhausted` 及可查询余量；管理员可调。禁止无声裁掉角色记录（autogrow 裁头在 D 单删除）。

| 项 | 默认 | 硬上限（配置不可超） | 调整 |
|---|---|---|---|
| self 单文件 | 256 KiB | 2 MiB | admin |
| self 总量/角色桶 | 8 MiB | 64 MiB | admin |
| self 文件数 | 200 | 2000 | admin |
| self 每文件 revision | 20 或 14 天（先到） | 100 / 90 天 | admin；超限拒绝新写或拒绝新版本，不删未入 trash 的当前文件 |
| self trash | 50 项 / 30 天 | 200 / 90 天 | 可逆恢复；不可逆清空另策略 |
| backend/external 单次读 | 12_000 字符；5 MiB 文件 | 32_000 / 8 MiB | 分页；脱敏先于截断 |
| 列目录 | 100 条、深度 2 | 200 / 3 | 不默认枚举全盘 |
| 读耗时 | 5 s | 15 s | 超时 `read_budget_exceeded` |
| AGENT.md 注入 | 2_000 字符 | 4_000 | 裁剪/消融；低于系统安全与用户指令 |
| Agent task 并发/角色 | 1 | 2 | admin |
| 递归派生 | 0 | 1 | 默认禁止 |
| 任务步骤 | 12 | 32 | 与 tool_loop 独立 |
| 任务墙钟 | 900 s | 1800 s | process 上限取更严者 |
| 任务 token/费用 | 跟随角色 chat preset | spend 账本若启用则拦截 | 耗尽可查，不盲跑 |

workspace/process 限额延续现状（单文件 5 MiB、总量 50 MiB、并发 2；process 15 min / 512 MiB / 1 MiB 输出）。self 权限不能放宽 workspace 限额。

---

## 5. 读取、脱敏与 Agent task（B/F current）

- **B current**：backend 与 external 独立解析。显式关闭仍生效；旧 `fs_access.enabled`/`allow_roots` 迁移为 effective state：allow_roots 变为发现提示，不是外部普通文件唯一准入。
- **B current**：统一 sensitive-redaction：先于截断、分页、模型、缓存。失败拒绝。禁止 `token` 子串误杀源码。workspace_read、toy 读、artifact 读、process stdout/stderr 复用同一服务。
- **B current**：高风险凭据库按类型/内容判定，不只靠扩展名。
- **F current**：`start_agent_task` 立即返回 `task_id`；coding/inspect worker 复用 Task Manager + Work Session + workspace + 受限 process，不新造任务库，不新增无限 shell。
- workspace manifest 绑定 frozen principal、workspace ID、操作集合、payload digest、有效期与 revision；每步重检，模型参数不是授权。当前 worker 对 update 使用 workspace 版本库；delete/undo 不在 worker manifest 中。

---

## 6. 观测与控制面（G 预告）

同单落地只读投影，scope `state.read`，不含凭据与私有正文：

| 拟议端点 | 内容 |
|---|---|
| `GET /observability/backend-read` | **current**：configured/effective、关闭原因、脱敏版本与计数 |
| `GET /observability/character-self` | **D current**：配额余量、grant revision、文件计数、最近操作元数据、AGENT.md 注入状态（无正文）、legacy toy 归属/迁移计数；不含私有正文 |
| `GET /observability/character-reminders` | **E current**：状态计数、revision、到期、重复种类、legacy 迁移计数；不含提醒正文 |
| 复用 `GET /observability/agent-runtime-tasks` | **F current**：Agent task 生命周期；角色查询走工具，不把 admin DELETE 暴露给模型 |
| `GET /observability/character-file-autonomy` | **G current**：集中展示 backend/external read、self 与 Agent task 的 configured/effective、阻断原因、配额、grant revision 与脱敏版本；不含正文、任务目标、凭据或绝对路径 |

Brief 229 自身仍“不新增端点”；这是历史范围，不约束后续施工单。backend-read 属于 256 B，character-self 属于 256 C，character-reminders 属于 256 E，Agent-task 生命周期属于 256 F；G 增加上述集中只读投影。

---

## 7. 非目标

- 第二人格、万能 EventBus、无限 shell
- 把 self 笔记自动写入 identity/episodic
- 桌面/手机新设置 UI（除非出现新客户端字段；本单默认管理面+角色工具）
