# 迁移遗留切断：T0 事实清单与执行报告

日期：2026-11-22（T0 原始快照）；2026-11-26 校准：T1、T2/T2.1、T3.1/T3.1.1 已合并；
T3.2 C1/C2 已修补，C3–C5 未完成。T4 三仓 PR 已合并，最终设备验收 partial；T5–T9 与 HINT 未开始。
后续状态：用户已允许 T1 施工；T1 将旧入口与调用者迁走后联删 45 条债务，T2 将剩余 26 条活动时钟、角色/owner/配置读取债务全部联删，机器清单已清零，见 [T2 报告](migration-cutover-t2.md)。
下文保留 T0 原始事实快照；实际 T1 所有者、删除项与验证见 [T1 报告](migration-cutover-t1.md)。
门禁的单向收敛与动态访问加固见 [T0.1 报告](migration-cutover-gate-hardening.md)。
本单最后一节的“现在只执行 T0”为原始授权快照；后续 T3.1/T3.1.1 合并为 PR #37
`d2fe511200b90f55e6dbead72e2a62608b238829`。T4 当前状态见 [后续验收](t4-02-final-acceptance.md)。
下表是 T0 调用者快照，不能用其“桌面未迁移”等历史文字判断当前消费者；T3.2、T5–T9 未被本轮关闭。

## 当前阶段状态（2026-11-26）

| 阶段 | 当前事实 | 仍待处理 |
|---|---|---|
| T0/T0.1、T1、T2/T2.1 | 门禁及调用者迁移已合并 | 不重做；相关守门仍须通过 |
| T3.1/T3.1.1 | PR #37 已合并 | 不等于 T3 全部完成 |
| T3.2 | C1 TurnSink 身份、C2 LoreEngine 生命周期已修补 | C3 Schema 间接 Registry、C4 TypeError 兼容、C5 Pipeline 属性代理 |
| T4 | 后端 #38、桌面 #14、手机 #21 均已合并；后端 #41 媒体修补已合并 | 验收 partial：手机真机十项 NOT RUN，独立审查未记录；桌面细分人工缺口见 T4-02 |
| T5/T6/T7/T8/T9、HINT | 未开始 | 保留原依赖、数据迁移和独立授权条件 |

用户默认提交合并规则已覆盖旧的测试/设备/审查合并门禁；验收状态及部署授权仍独立记录。

## 1. 基线和审计范围

三仓均在原有 main 上检查；执行 `git fetch origin main` 后 HEAD 与 origin/main 完全相同。

| 仓库 | 核实的完整 SHA | 开工工作区 |
|---|---|---|
| PresenceKit | `4aeeef30c623f0d4e1e26b7cb07a93d8b82d4991` | 仅用户提供的本工单未跟踪；不纳入施工提交 |
| PresenceKit-desktop | `af6c4d9368ff61aca09988440a2ee89247a7b275` | clean |
| PresenceKit-mobile | `94d25875474da3b127610f58b7208ebd37f3b6a0` | clean |

检查了 core、admin、channels、main、脚本、配置示例、bundled 模板和两端实际 HTTP 传输代码及测试。
这三个 SHA 只代表源码核实组合，跨端运行验收为 `not_verified`，不是协议兼容性通过声明。
未读取远端生产日志或用户私有记忆，未修改配置、生产状态、客户端或数据文件。
本地私有配置及真实安装资产的逐项迁移 manifest 尚待 T5/T6，不能据此宣布受管理数据已迁完。

## 2. 可执行边界与债务记录

扩展现有 `tests/test_ar02_dependency_directions.py`，没有另起重复门禁。
原 AR-02 已清零的边界保持零容忍，不能使用本次遗留记录豁免。
新增的全仓扫描检查：

- core 对 admin.routers 的依赖，包含业务服务和局部导入。
- Router 对其他 Router 私有成员的依赖，包含模块别名及属性访问。
- Scheduler 对通用活动 API 的旧所有权，以及外部业务模块对其活动/身份/配置入口的依赖。
- 依赖记录精确绑定源文件、所属函数、目标、访问方式、出现次数；增加文件、函数、目标或次数均失败；删除后未删账也失败。

机器清单为 [`migration-cutover-dependencies.json`](migration-cutover-dependencies.json)。
它记录实际尚未修复的内部债务，**不是批准长期保留兼容实现的白名单**。
开工基线共 71 行：core-router 4、router-private 41、scheduler-state 23、scheduler-owner 3；
同一函数中重复位置以 count 记录，所以行数不等于物理 import 数。
负责人：Codex 施工、用户协调独立审查；2026-11-29 为建议复核日期，不是已批准的生产删除日期。
退出条件：T1/T2 各迁走对应调用者、删除旧入口及其测试后，同步删除精确记录；不得重新生成基线吸收新增违规。
没有外部正式协议白名单，也没有依据“可能有人使用”批准保留任何路径。

HTTP 组合层导入公开 router 合法；没有自身 HTTP 路由注册的辅助模块不按独立 Router 处理。
这依据源码 AST 判断，而不是按路径加入人工例外。core 仍禁止依赖这些 HTTP 辅助模块。
静态分析不执行任意动态表达式，不是完整 Python 数据流/类型检查；原 AR-02 的窄域动态加载禁令仍保留。
Scheduler 门禁保护已识别的活动/身份 API，不能自动判定任意新业务状态的语义；新状态所有权仍须代码审查。

独立 CI job `architecture` 直接执行此文件，不依赖运行配置或生产凭据，覆盖 push、PR、定时和手动事件；
原 Smoke 及全量 collection 也继续包含本文件。CI 配置已更新，远端结果待实际推送运行。

## 3. 逐项遗留、目标所有者及切断条件

以下为按真实调用关系归并的清单。T1/T2 每个文件、函数、成员和次数另见机器清单；
目标为后续施工设计要求，不能把“已有目标模块”写成“已迁移”。

| 项目 / 旧入口 | 当前真实调用者 | 唯一目标所有者 | 外部消费者 / 切断条件 | 联删代码、测试、文档 / 验收入口 |
|---|---|---|---|---|
| T1 `chat.run_owner_chat_turn` 转出入口 | companion.service 的 handle_event 两处；mobile_chat、owner_turn | 已有 `core.owner_chat.service.run_owner_chat_turn` | 内部 Python 调用；HTTP 对外消费者仍是桌面/手机；迁移调用不改变 REST | 删除 chat 冗余转出及仅补丁该入口的测试；AR-02 文档；owner-turn/mobile/companion 回归 |
| T1 chat `_session_grant`、`_run_session_request` | mobile、sensor、chat_log、observability、video_call | grant/receipt 归已有 `core.session_scope`；HTTP 错误映射归有明确职责的 HTTP 会话入口 | 内部私有调用，非正式外部 API；所有调用者改直连，保留鉴权和错误语义 | 删除旧 Router 帮助实现及补丁 fixture；`test_session_scope.py`、`test_mobile_chat_channel.py` |
| T1 chat `_owner_media_scope`、`_check_reality_not_in_dream` | sensor、video_call、owner_turn、mobile | scope 归会话所有者；Reality/Dream 准入归现有 Dream domain gate，HTTP 层仅映射异常 | 内部调用；不能以提取 HTTPException 到 Core 代替分层 | scope/media/owner-turn 回归；同步 channels、三仓接口总账相关条目 |
| T1 activity → `_common.active_char_id` | continuity.reality_dialogue、MinecraftService._principal、minecraft_routing.route_view | Core 角色/MemoryScope 所有者，HTTP 边界传入可信 scope | 新发现，内部业务反查 HTTP；迁移全部 3 处 | 删除反向导入及 Router patch 测试；activity/minecraft 相关回归 |
| T1 users/provenance/character 私有实现 | jailbreak_entries、lorebook、period、relations；observe、observability、watch；phone_control、settings_gmail/llm/misc | 角色请求解析与 HTTP 校验留 HTTP 适配；角色加载/重载归 Core 现有所有者 | 新发现，精确函数清单见 JSON；没有外部 Python 私有契约证据 | 联删各私有转出；相关 router/settings 回归；不可建立 admin.common 杂物箱 |
| T1 settings_llm 私有实现 | settings_misc 的 key masking；settings_thinking 的角色路由提示解析 | 凭据展示归明确的 HTTP 脱敏工具；路由有效状态归现有 model/control-center 所有者 | 新发现，内部；不能改变密钥遮罩或 effective state | 删除私有跨 Router 导入；相关 settings 安全回归 |
| T1 Dream 私有重导出与跨 Router 常量/重载 | dream.py、group_dream、dream_sessions/observability/worlds 对 dream_presets | 校验常量归领域合同，registry 重载归实际 registry；HTTP composition 仅挂载 router | 现存内部消费者；dream_common 本身是 HTTP helper，不误认作对外业务服务 | 删除 `_valid_world_layer_values` 重导出；Dream/Stage 回归；AR-02 兼容表更新 |
| T2 活动状态包装（已切断，待审查） | main.handle_message、owner_chat.service、scheduler.gating 已直连活动所有者；loop 三个包装已删除 | `core.conversation_activity` | 内部；进程内唯一时间戳，默认 120 秒严格小于边界不变 | 六条债务联删；时钟/gating 边界、相关入口回归与不可恢复门禁见 [T2 报告](migration-cutover-t2.md) |
| T2 Scheduler 身份/配置读取（已切断，待审查） | audio_perception、autonomy/control_center、ime_awareness、owner_turn_service、wake_bridge；各 admin scope 帮助函数，见 JSON | 配置归 config_loader；角色归明确 Core scope/角色所有者 | 跨业务调用已直连 config_loader、owner_config、character_context；Scheduler 内部仍可消费这些同一实现的函数引用，不持有第二份状态或解析实现 | 跨业务私有读取与 20 条债务已联删；相关 scope/autonomy/scheduler 回归见 T2 报告 |
| T3 `Pipeline.fetch_context/run_agentic_loop/post_process*` 门面 | main、owner_chat prepare/generation、scheduler.loop、stage.views、dream.reality_continuation、turn_sink | 已有 reality_context / owner_chat.tool_loop / turn_effects；prompt 归 prompt_builder、模型传输归 llm_client；生成防护和会话持有需明确生命周期设计 | 内部消费者确实存在，不能现在删；turn_sink 仍调用组合 post_process，不能误判无调用者 | 逐方法迁移并联删测试 patch；`test_pipeline_*scope*.py`、tool loop、scheduler、owner chat 集成；ARCHITECTURE/prompt/memory 文档 |
| T3 `pipeline_registry` | main 注册；character_loader/name_provider/cleanup、model_registry、资源完整性、tool policy/context、owner chat、autonomy、dream、stage 周边、scheduler、channels.qq、admin settings/character 等读取 | main 生命周期持有运行实例；是否显式注入须在 T3 对每类读者确定，T0 不盲改 | 内部 Service Locator 仍实际使用；recovery_validation 和 stage.views 还独立构造 Pipeline | 保留/删除结论须附完整注册/读取扫描；`test_r7b_scheduler_pipeline_registry.py`、启动/热切换/恢复回归 |
| T4 无 Session → active character | 后端 chat/history/media、sensor、video-call 的隐式 scope；桌面 Rust send_chat/get_chat_request/computer_use 等 HTTP 调用 | core.session_scope 签发的固定 grant；客户端按角色/连接持有 | 桌面是真实未迁移消费者；手机已有 session 支持但也有无 session 分支测试，不能写成已强制 | 客户端先改再删服务端 fallback；session/receipt/media/history 回归；桌面 build/UI、手机合同；三仓协议文档 |
| T5 authored legacy read roots / resolver | character_loader/asset_registry、Dream world/preset/scenario、lorebook/jailbreak、voice、stickers、traits/activity/author notes、letter_samples/knowledge 消费者 | 正式 userdata authored root；bundled 公共发行素材独立保留 | 真实私有资产是否迁移未核验，不能删除；需 dry-run manifest、冲突决策、inventory 备份、hash/apply/恢复演练 | data_paths/data_registry/resolver/asset_registry 旧读分支及旧 fixture 联删；`test_authored_root_migration_dry_run.py`、`test_authored_resource_resolver.py`、`test_authored_legacy_writer_gate.py`；authored-root 文档 |
| T6 tool_loop.categories/exclude_tools、presence_ext.tool_categories、info 扩展 | core.tool_exposure 的路径选择与 owner_chat.tool_loop 的分类扩展；config.example.yaml；bundled/examples 两张角色卡；真实配置尚待单独私有审计 | `tool_exposure.path_a/path_c`，角色分路字段遵循正式校验 schema | 有受管模板实际使用，不是假想消费者；先迁移配置、验证分类等价，再拒绝旧字段 | 删除 fallback 和 expand_legacy_categories 的所有调用；`test_tool_exposure.py`、权限/schema 回归；model-presets/tools/控制面文档 |
| T7 旧主召回 episodic/event-log/vector 与 shadow 比较 | reality_context.fetch_reality_context 的正式召回；event_shadow_recall 比较 | Event Store 证据 + 具备全部正式能力的新主召回所有者（尚未完成） | 用户私有历史及生产主路径；pending_approval、used_as_gate=False 仍属现状；必须观测、迁移、恢复及用户明确批准 | 仅达到门槛后删除旧主召回与已无用途 shadow；保留离线恢复/原始证据；event migration/retirement、scope/source/ranking/token 回归；memory 文档 |
| T8 根 test_garden_injector.py → scripts.garden_injector_smoke | 文件仅转调 main；仓内文档 round2-p2-delivery 明确旧命令保留至 v1.3 系列结束 | `scripts/garden_injector_smoke.py` | 未发现其他仓内执行调用；历史发布承诺不是外部使用证据，外部使用情况未核实，不批准永久保留 | 后续独立审查给出删除日期再联删包装/旧命令文档；保留正式脚本的隔离 smoke |
| T8 其他薄入口候选 | CharacterLoader.load 方法只是调用模块 load；dispatcher register_send_callback 等曾记为兼容，需后续逐消费者扫描 | 实际领域所有者；不能仅按 wrapper 名称删除正式 API | 目前未证明可删，仍为待核对候选；四个 AA*.bat/run_test.py 有现有工作流记载，不能按薄脚本统一删除 | 对应迁移完成后逐项查调用、联删守卫/fixture/文档；不得提前宣称无外部消费者 |
| T9 最终审计 | T1–T8、三仓正式入口和真实安装 | 更新后的唯一所有者与协议 | 依赖上述独立审查、数据安全及设备验收；当前不具备完成条件 | 三仓架构/功能地图/测试矩阵、启动/构建/真实核心链路证据 |

### 源码搜索与后续验收命令

在各仓根执行命令；扫描命令不等于运行验收通过。

```text
git status --short
git fetch origin main
git rev-parse HEAD origin/main
rg -n 'admin\.routers|core.scheduler.loop' core admin main.py channels --glob '*.py'
rg -n 'Pipeline|pipeline_registry|fetch_context\(|run_agentic_loop\(|post_process\(' core admin main.py channels --glob '*.py'
rg -n 'legacy|fallback' core/data_paths.py core/authored_asset_resolver.py core/asset_registry.py
rg -n 'categories|tool_categories|expand_legacy_categories' core/tool_exposure.py core/owner_chat/tool_loop.py config.example.yaml bundled
rg -n 'pending_approval|used_as_gate' core/memory/event_retirement.py
rg -n 'X-Presence-Session|/v1/sessions|desktop/chat' src src-tauri
rg -n 'X-Presence-Session|createPresenceSession|sessionId' lib test
python -m pytest --noconftest -o addopts= -q tests/test_ar02_dependency_directions.py
```

后续 T4：桌面 `npm.cmd run build`（Rust 变更再 cargo check），手机相关 Flutter session/backend-client 测试；
必须另做 UI/真机链路验收。T5 使用现有 `python scripts/authored_root_migration_dry_run.py --help` 约定；
目前该命令**没有 apply**，不得把 dry-run 当成备份/迁移/恢复实现。
T7 使用 `scripts/migrate_memory_events.py` 的离线工具，正式切换不是本单授权。

## 4. T0 实际修改与依赖证据

- 修改文件：现有架构测试、Tests workflow、架构总览与测试矩阵；新增本清单和精确 JSON 债务记录。
- 删除文件 / 旧入口：无；T0 不进行 T1–T9 切断。
- 迁移后的唯一所有者：本轮仅门禁归现有 AR-02 测试；业务目标见上表，未假称已经迁移。
- 调用方变更：无；全部现有调用者按实际源函数记录。
- 所有调用者是否已迁移：否，必须在对应 T 项施工并审查。
- 外部消费者：桌面/手机 REST、已安装用户数据与配置必须迁移；没有证明内部私有 Python 入口是外部正式协议。
- 保留原因和退出条件：本次仅获准 T0，现有债务冻结后交由 T1–T8 切断；这不是运行时兼容范围扩张授权。

## 5. 测试证据

Python 3.12.13 / Windows。结果如下。

| 命令 / 验证 | 结果与边界 |
|---|---|
| 仅安装 pytest 的全新 venv；`python -m pytest --noconftest -c <空pytest配置> -q tests/test_ar02_dependency_directions.py` | 73 passed，零失败/跳过；独立门禁无需后端依赖和私有配置。CI 在 Linux 用 `/dev/null` 作空配置，避开项目 pytest.ini 的运行时 warning 类型依赖 |
| 实际仓库故障注入 | 临时新增 core 模块分别反查 chat Router、导入 Scheduler 活动入口，真实 repository gate 均失败（退出码 1、命中债务断言）；finally 删除探针后同一入口通过。没有用例收集或环境错误冒充捕获回归 |
| `python -m pytest -q tests/test_ar02_dependency_directions.py tests/test_ar02_extension_boundaries.py tests/test_line_endings.py --tb=short` | 最终 76 passed，零失败/跳过（12.60s）；覆盖原边界、跨函数同名 alias 隔离、合法工具/挂载 Router 扩展集成与换行 |
| YAML 解析、JSON 与差异检查 | workflow 可解析，独立 job 命令已核对；债务与扫描相等，71 行；提交前执行 diff/check 和 LF 核对 |
| 隔离 Admin 实际启动与 HTTP 操作 | 公共示例配置、临时 data/authored 根、loopback 随机端口；Application startup complete；GET /openapi.json = 200（501 个 path，包含 desktop/chat 和 v1/sessions）；未授权 POST /v1/sessions = 401；正常 shutdown，未发现循环导入或启动警告 |
| 构建 / 真实模型 / UI / 手机设备 | 未执行；本单无运行时代码、UI 或客户端协议改动。Admin 启动不是完整 main/model/设备验收，不据此标记 T3/T4/T9 通过 |
| 远端 CI | 未推送，`not_verified`；本地新 venv 验证不冒充 GitHub Actions 结果 |

临时验证材料位于 ignored `.tmp/cutover-t0/`；运行配置和数据均隔离。
未运行全量回归；T0 的静态合同与 CI 接线不要求扩大到全仓业务、客户端构建或生产迁移。

## 6. 架构检查与未完成项

| 问题 | 结果 |
|---|---|
| 新增 facade / adapter / bridge / wrapper | 否；没有新增运行时代码 |
| 新增 fallback | 否 |
| 新增静默降级 | 否 |
| 新增白名单例外 | 无外部协议白名单；新增精确内部债务快照供 T1/T2 联删，待独立审查；原 AR-02 边界不可豁免 |
| 双份业务逻辑 | 未增加；既有 Pipeline 门面和旧配置/路径仍待后续切断 |
| 未迁移调用者 | 有，上表及 JSON 逐项列出，未标为完成 |

T0 需独立审查门禁与事实清单，特别是新增发现的目标所有者和债务冻结范围。
T1–T9 业务修改、生产迁移/备份/恢复、客户端完整链路、正式 Memory Event 切换均未执行。
本轮没有 push/PR/合并/部署授权，不代表远端 CI 已通过。后续施工不得跳过工单指定的逐项审查。

## 7. Pipeline registry import snapshot

Baseline source references (including local imports); main is the sole production register caller. These are inspection evidence, not a runtime acceptance result.

| Source | Line | Imported names |
|---|---:|---|
| `admin/routers/character.py` | 113 | `get` |
| `admin/routers/chat.py` | 103 | `pipeline_registry` |
| `admin/routers/chat.py` | 539 | `pipeline_registry` |
| `admin/routers/chat.py` | 593 | `pipeline_registry` |
| `admin/routers/coplay.py` | 41 | `get` |
| `admin/routers/dream_common.py` | 29 | `get` |
| `admin/routers/dream_rpg.py` | 79 | `get` |
| `admin/routers/dream_sessions.py` | 36 | `get` |
| `admin/routers/dream_sessions.py` | 107 | `get` |
| `admin/routers/lorebook.py` | 76 | `get` |
| `admin/routers/observability.py` | 25 | `get` |
| `admin/routers/observability.py` | 383 | `get` |
| `admin/routers/settings_mcp.py` | 392 | `pipeline_registry` |
| `admin/routers/settings_prompt_assets.py` | 100 | `get` |
| `admin/routers/settings_prompt_assets.py` | 310 | `get` |
| `channels/qq.py` | 61 | `get` |
| `core/agent_runtime/computer_use.py` | 11 | `config_loader, image_presets, pipeline_registry` |
| `core/autonomy/effective_state.py` | 217 | `pipeline_registry` |
| `core/autonomy/runner.py` | 1205 | `pipeline_registry` |
| `core/autonomy/talk_gate.py` | 76 | `pipeline_registry` |
| `core/character_cleanup.py` | 71 | `get` |
| `core/character_loader.py` | 36 | `pipeline_registry` |
| `core/character_loader.py` | 202 | `pipeline_registry` |
| `core/character_name_provider.py` | 26 | `pipeline_registry` |
| `core/character_name_provider.py` | 59 | `pipeline_registry` |
| `core/companion/service.py` | 77 | `get` |
| `core/companion/service.py` | 347 | `get` |
| `core/control_center/effective_state.py` | 86 | `pipeline_registry` |
| `core/coplay/device_collection.py` | 48 | `get` |
| `core/dream/dream_pipeline.py` | 437 | `get` |
| `core/dream/dream_pipeline.py` | 1068 | `get` |
| `core/dream/dream_pipeline.py` | 1497 | `get` |
| `core/dream/reality_continuation.py` | 252 | `get` |
| `core/memory/health_state.py` | 92 | `get` |
| `core/model_registry.py` | 143 | `pipeline_registry` |
| `core/owner_chat/service.py` | 67 | `get` |
| `core/perception/phone_ambient.py` | 125 | `get` |
| `core/pipeline.py` | 1030 | `get` |
| `core/pipeline.py` | 70 | `get` |
| `core/prompt_ablation.py` | 51 | `pipeline_registry` |
| `core/resource_completeness.py` | 242 | `pipeline_registry` |
| `core/scheduler/loop.py` | 332 | `pipeline_registry` |
| `core/scheduler/loop.py` | 460 | `get` |
| `core/scheduler/loop.py` | 818 | `get` |
| `core/scheduler/triggers/coplay_watch.py` | 35 | `get` |
| `core/scheduler/triggers/festival.py` | 19 | `pipeline_registry` |
| `core/thinking_voice.py` | 104 | `get` |
| `core/tool_runtime/context.py` | 8 | `get` |
| `core/tool_runtime/policy.py` | 231 | `pipeline_registry` |
| `core/turn_sink.py` | 56 | `get` |
| `main.py` | 186 | `register` |
