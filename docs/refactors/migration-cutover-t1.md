# T1：切断 Core→Router 与 Router 私有依赖

基线：`acd98895dc5b4ff4d502ec641d7f428b0a826144`，2026-11-22。
本单仅执行 T1；T2–T9、HINT、客户端协议变更及生产部署未开始。
按总工单，交付审查分支与证据后等待独立审查，不自动叠加下一项。

## 1. 实际修改

| 旧入口 / 真实调用者 | 唯一所有者与调用变化 |
|---|---|
| chat.run_owner_chat_turn；companion 两处、mobile、owner_turn，以及 chat 自身 | 全部直接使用 `core.owner_chat.service`；删除 chat 的同对象转出，chat 内部使用模块引用 |
| chat._session_grant / _run_session_request；chat、mobile、sensor、chat_log、observability、video_call | `core.session_scope` 继续拥有 grant/receipt；`admin.session_requests` 仅拥有 HTTP 错误映射，旧 Router 定义删除 |
| chat._owner_media_scope；chat、sensor、video_call | 原 owner/character 选择实现迁到 `core.session_scope.owner_media_scope`；HTTP 映射归 `admin.session_requests`，状态码保持原值 |
| chat._check_reality_not_in_dream；chat、mobile、owner_turn | `admin.reality_admission.require_reality_turn` 组合既有 Core Dream guard 并映射 409；不重复实现 Dream 状态机 |
| Core activity continuity/minecraft/minecraft_routing → _common.active_char_id | 严格读取与校验归 `core.character_context.active_character_id`，抛领域错误；现有 `_common` 仅映射 HTTP 错误。活动对话 HTTP 签名/准入/回执编排整体归 `admin.activity_dialogue`，Core continuity 仅保留活动投影；Minecraft 领域读取不再导入 HTTP Router |
| character._active_character_id / _reload_character；character、phone_control、settings_gmail/llm/misc | 编辑/配置选择归 `core.character_context.configured_character_id`；热重载归 `core.character_loader.reload_active_character`；旧 Router 定义删除 |
| settings_llm._active_character_id_for_routing_warning；settings_llm/thinking | `core.character_context.routing_character_id` 保留原诊断语义；旧 Router 定义删除 |
| users._resolve_char_id；users、jailbreak_entries、lorebook、period、relations | 请求角色验证归 `admin.profile_scope.resolve_profile_character_id`；所有调用者使用该 HTTP 支持模块 |
| provenance._resolve_char_id；provenance、observe、observability、watch | 观测请求选择归 `admin.observation_scope.resolve_observation_character_id`；保留显式参数与缺省错误行为 |
| settings_llm._looks_placeholder / _mask_key；LLM 与邮件设置 | `admin.secret_display` 统一拥有设置响应的凭据展示；遮罩与占位符规则不变 |
| Dream _valid_world_layer_values 私有转出/转调；solo/group | `core.dream.world_loader.valid_world_layer_values` 拥有世界集合；删除 dream/group 的私有包装与重导出 |
| Dream Router 模式/注入常量 | 直接使用 `core.dream.dream_state.VALID_DREAM_MODES` 与既有 `dream_settings.SCENARIO_INJECTION_MODES`；删除 Router 的重复定义 |
| dream_presets._reload_dream_preset_registry；presets/worlds | 删除转调包装，直接使用 `core.asset_registry.reload_registry` |
| screen_observation.identity；screen/computer_use/device_file_agent | 设备 profile HTTP 校验与 opaque producer hash 归 `admin.device_identity` |
| food_memory.scope；food_memory/quote_notebook | 食物/摘录读取的 HTTP owner/character 准入归 `admin.food_requests` |
| scheduler.owner_status；settings_llm | 配置验证与设置投影归 `core.owner_config`；不改 Scheduler loop 的通用活动状态（T2） |
| wechat.operational_state；wechat/settings_feature_flags | 运行状态投影归已有 `core.wechat_service`，只读返回内容不变 |
| wake_bridge.submit_garden_wake；wake_bridge/integrations | 删除无业务职责转调，两个 HTTP 入口直接调用已有 `core.wake_bridge.WakeBridge.submit_garden_mapping` |

删除文件：无。删除的是上述旧函数、私有转出、重复常量与无用导入；没有复制后保留旧实现。
测试中的旧 Router 补丁和导入同步迁往正式所有者，业务断言保持。
完整文件列表以本单提交的 Git diff 为准，新增领域/HTTP 支持模块均列于上表。

## 2. 依赖证据与剩余边界

T0 清单中 core-router 4 行、router-private 41 行共 45 行已逐项联删；其中 companion 一行计数为 2。
剩余 26 行全部属于 T2（scheduler-state 23、scheduler-owner 3），身份、次数、元数据均未扩大。
原已清零的 AR-02 边界保持；新增 T1 零容忍断言，即使旧目标提交仍允许这些历史债务，
同时恢复源码与 JSON 也不能重新启用 T1 路径。真实子进程故障注入验证该断言会失败。

当前调用图：

```text
HTTP routers → 明确职责的 HTTP 准入/参数验证/脱敏模块 → Core 领域所有者
companion / mobile / owner_turn / chat → core.owner_chat.service
activity HTTP 入口 → admin.activity_dialogue → HTTP 角色准入 / Core 活动与 turn_sink
Minecraft 领域 → core.character_context
Dream 组合 Router → 分域 HTTP 端点 → core.dream / asset_registry
```

HTTP 路由组合、没有注册端点的既有 HTTP helper 依赖仍合法；没有扩大 HTTP_HELPERS。
Core 中的旧 logger 分类字符串不构成模块导入，没有借机更改日志分类。

未发现旧私有 Python 函数的正式外部消费者；仓内真实调用者及测试已改直连。
外部 HTTP 消费者依赖的路由、权限和 Session 语义保持，OpenAPI 与路由快照核对见下。
桌面仍使用的无 Session 行为属于 T4 的正式客户端迁移，T1 不提前删除；没有新增该兼容范围。
角色严格准入、编辑缺省和诊断读取原有策略分别保留，没有将不同契约强行归一。

## 3. 测试与运行证据

Python 3.12.13 / Windows，使用公共 fixture 和可写沙盒；未用生产数据测试。

| 验证 | 结果 |
|---|---|
| 开工 6 文件相关基线 | 70 passed，0 failed/skipped |
| 会话/桌面回执/手机/owner-turn/Dream guard/companion/活动/Minecraft/角色/设置 14 文件 | 修复回调引用遗漏后同组 157 passed，0 failed/skipped |
| 仅 pytest 的独立架构门禁 | 115 passed，0 failed/skipped；含现有动态访问、Git 基线、多提交 push 和 T1 防恢复注入 |
| 独立债务 CLI | origin/main 基线 71→26，通过；只删除 T1 对应记录 |
| OpenAPI 与 HTTP 路由快照 | 501 个 OpenAPI path、614 条 HTTP 路由；迁移前后完整 JSON 值和注册顺序完全一致 |
| 首轮全量与修复组 | 8775 passed / 66 failed；53 项同一 Dream 常量导入遗漏，13 项旧测试入口/补丁；真实入口与相关修复组 212 passed |
| 本地第二轮全量 | 8843 passed / 1 failed，13 subtests；唯一失败为未修改 backup_state 的 Windows 临时目录 os.replace PermissionError，并非断言回归 |
| 最终 HTTP 活动所有者与备份复查组 | 136 passed，0 failed/skipped；活动、阅读、棋类、Minecraft、HTTP scope 和整个 backup_restore 文件；未修改备份业务代码 |
| 最终 Smoke | 369 passed，0 failed/skipped；新增 HTTP scope 四例已接入现有 Smoke security 清单 |
| 最终隔离 Admin 实际启动 | Application startup complete；GET /openapi.json = 200（501 path），未授权 POST /v1/sessions = 401；正常 shutdown，无循环导入或启动警告 |
| 差异与远端 CI | 提交前逐文件核对 LF、普通/ignore-CR stat 和 diff/check；当前 head 远端完整结果在审查 PR 中给出，不以旧提交替代 |

首轮 14 文件回归为 140 passed / 17 failed，同一根因是删除 chat 转出后，
`executor=run_owner_chat_turn` 作为参数的引用尚未迁移。改为领域所有者的方法引用后原组全过，
没有放宽回执、权限、隔离、失败或去重断言。
全量另外定位到 Dream 主链仍导入旧私有模式集合，已直接迁到公开领域常量。
仅保护 users Router 旧私有函数定义位置的源码断言已删除，其 503/422 行为由既有 profile 回归
和新增真实 HTTP scope 用例保护。Garden 手工唤醒旧测试中的包装函数/源码字符串断言已替换为
实际 HTTP 鉴权、持久 pending 记录与零 drain/生成/turn-sink 副作用断言。

本单无 Admin 静态资源、设置字段、默认值、scope 或跨端协议变化，无需缓存版本更新或客户端构建。
后端 API 的真实 HTTP 测试和隔离启动验证不等于真实模型、生产部署或手机/桌面设备验收。
临时快照、隔离运行配置及日志仅存 ignored `.tmp/`。

## 4. 架构逐项检查

| 项目 | 结论 |
|---|---|
| 新增 facade / adapter / bridge / wrapper | 无兼容转发层；HTTP 支持函数承担真实请求验证、异常映射或凭据投影，直接访问领域所有者；旧 Router 名称删除 |
| 新增 fallback | 无；T4 既有无 Session 协议未改，原角色编辑/诊断错误策略原样迁移 |
| 新增静默降级 | 无 |
| 新增白名单例外 | 无；删除 45 条债务，T1 进入零容忍 |
| 双份业务逻辑/运行状态 | 无；领域实现迁走后旧函数删除，没有第二份 grant、角色状态或 Dream 状态机 |
| 未迁移调用者 | T1 已识别内部调用者为 0；T2–T9 未执行，不能标记整轮完成 |

## 5. 未完成项与交付约束

T1 交付后等待用户协调的独立审查；不开始 T2，不自动合并本单。
真实模型/设备/生产验收未执行，本单没有部署或迁移用户资产/记忆。
T0.1.1 对 PR #34 的合并授权已完成，不自动扩展为对本单的独立审查通过。
