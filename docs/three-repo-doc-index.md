# PresenceKit 三仓文档总索引

> 最后核对：2026-09-16
> 适用仓库：`Emerald-presence`（后端）、`Emerald-client`（桌面）、`Emerald-mobile`（手机）

这份文档是三仓文档的第一入口。先按功能直达；如果要了解某个仓库的全部文档，再看后面的逐仓清单。
路径使用当前工作区的相对链接，跨仓链接默认指向三个同级仓库。

## 先记住这 6 条权威边界

| 要查的真值 | 唯一优先文档 | 说明 |
|---|---|---|
| 后端 API 精确 schema | 运行中的 `/openapi.json`、后端 `docs/api-reference.md` | 文档描述调用关系；字段最终以代码和 OpenAPI 为准。 |
| Desktop wire protocol | `Emerald-client/docs/protocol-v0.md` | 后端 `docs/desktop-client-protocol.md` 只是实现指针，不复制正文。 |
| Mobile channel / poll / ack / behavior | `Emerald-mobile/docs/protocols/mobile-channel.md` | Mobile backend integration 只做调用矩阵和接入说明。 |
| 后端开关、effective state、scope、观测 | `Emerald-presence/docs/feature-control-surface.md` | 客户端文档只记录设置入口和本地能力，不拥有后端真值。 |
| 三仓接口、调用链、跨端闭环 | 本文 + `docs/three-repo-interface-catalog.md` | 本文负责找文档；接口总账负责字段、链路和缺口。 |
| 当前问题和未完成验收 | 各仓 `docs/known-issues.md` | 只看 open/observe/roadmap；历史背景不要当完成证据。 |

状态标记：`authority` = 该主题权威；`current` = 当前说明；`supporting` = 支撑/调用方说明；`pointer` = 只负责指路；`historical` = 历史快照、提案或已关闭记录；`task` = 施工接力记录。

## 按功能直达

| 想查什么 | 直接去这里 | 还需要看什么 |
|---|---|---|
| 系统全貌、启动边界、运行生命周期 | [`ARCHITECTURE.md`](../ARCHITECTURE.md)、[`runtime-lifecycle.md`](runtime-lifecycle.md) | 设计准入看 [`DESIGN.md`](../DESIGN.md)，事件边界看 [`interaction-event-model.md`](interaction-event-model.md)。 |
| Windows 开发、pytest、构建和沙箱 | [`dev-environment.md`](dev-environment.md)、[`testing-matrix.md`](testing-matrix.md) | Desktop 看 [`Emerald-client/docs/testing.md`](../../Emerald-client/docs/testing.md)，Mobile 看 [`Emerald-mobile/docs/quality/testing-and-dev.md`](../../Emerald-mobile/docs/quality/testing-and-dev.md)。 |
| 后端 HTTP/WS 端点 | [`api-reference.md`](api-reference.md)、[`channels.md`](channels.md) | 跨仓字段和调用方看 [`three-repo-interface-catalog.md`](three-repo-interface-catalog.md)。 |
| Desktop 消息格式、WS、Tauri IPC | [`Emerald-client/docs/protocol-v0.md`](../../Emerald-client/docs/protocol-v0.md) | 后端落点看 [`desktop-client-protocol.md`](desktop-client-protocol.md)，关联/重试看 [`Emerald-client/docs/chat-correlation.md`](../../Emerald-client/docs/chat-correlation.md)。 |
| Mobile 聊天、后台主动消息、poll/ack | [`Emerald-mobile/docs/protocols/mobile-channel.md`](../../Emerald-mobile/docs/protocols/mobile-channel.md) | 后端路由看 [`Emerald-mobile/docs/backend/integration.md`](../../Emerald-mobile/docs/backend/integration.md)，Android 服务看 [`Emerald-mobile/docs/mobile/background-notification-design.md`](../../Emerald-mobile/docs/mobile/background-notification-design.md)。 |
| relay / ntfy 发布和信号语义 | [`Emerald-mobile/docs/protocols/relay-publish-contract.md`](../../Emerald-mobile/docs/protocols/relay-publish-contract.md) | Android fallback 看 [`Emerald-mobile/docs/mobile/background-notification-design.md`](../../Emerald-mobile/docs/mobile/background-notification-design.md)。现行为是断开 1 分钟后补偿、之后每 15 分钟一次。 |
| 鉴权、token、scope、轮换 | [`security.md`](security.md)、[`token-rotation.md`](token-rotation.md) | 威胁模型看 [`security_model.md`](security_model.md)，客户端接入看各仓 backend integration。 |
| 设置页、功能开关、effective state | [`feature-control-surface.md`](feature-control-surface.md) | Desktop 设置归属看 [`Emerald-client/docs/settings-control-audit.md`](../../Emerald-client/docs/settings-control-audit.md)。历史重组讨论看 [`settings-reorganization-audit-2026-09-10.md`](settings-reorganization-audit-2026-09-10.md)，不要把它当当前实现。 |
| Agent Runtime、工具循环、MCP | [`agent-runtime-architecture.md`](agent-runtime-architecture.md)、[`tools.md`](tools.md) | Agent Runtime 是同一角色的 durable/specialized 副链，不是第二个 Agent。路由/能力看 [`model-presets.md`](model-presets.md)，浏览器 worker 看 [`agent-runtime-browser-route-matrix.md`](agent-runtime-browser-route-matrix.md)。 |
| Prompt、tag、token 裁剪、风格 | [`prompt-layers.md`](prompt-layers.md)、[`prompt-unification-audit.md`](prompt-unification-audit.md) | 角色模型方案看 [`model-presets.md`](model-presets.md)。后者是审计记录，规则以代码和 `prompt-layers.md` 为准。 |
| 记忆、事件、向量、数据路径 | [`memory.md`](memory.md)、[`data-taxonomy.md`](data-taxonomy.md)、[`vector-store.md`](vector-store.md) | 迁移/资产盘点看 [`authored-root-migration.md`](authored-root-migration.md)、[`c1-root-asset-inventory.md`](c1-root-asset-inventory.md)。 |
| 对话写入、短期记忆、turn sink | [`assistant-turn-sink.md`](assistant-turn-sink.md) | 通道链路看 [`channels.md`](channels.md)，客户端展示边界看 [`Emerald-client/docs/memory.md`](../../Emerald-client/docs/memory.md)。 |
| 主动性、scheduler、触发器 | [`autonomy.md`](autonomy.md)、[`scheduler.md`](scheduler.md) | 当前开关看 [`feature-control-surface.md`](feature-control-surface.md)，未完成项看 [`known-issues.md`](known-issues.md)。 |
| Dream / Dream Stage / RPG Dream | [`dream.md`](dream.md)、[`stage.md`](stage.md)、[`rpg-dream-api.md`](rpg-dream-api.md) | Desktop 展示看 [`Emerald-client/docs/dream-hud.md`](../../Emerald-client/docs/dream-hud.md)；RPG 客户端恢复看 [`rpg-dream-client-guide.md`](rpg-dream-client-guide.md)。 |
| 花园、活动、棋类、阅读、共玩 | [`garden.md`](garden.md)、[`activity-session.md`](activity-session.md)、[`reading-activity.md`](reading-activity.md) | 棋类看 [`chess-activity.md`](chess-activity.md)/[`gomoku-activity.md`](gomoku-activity.md)，共玩看 [`coplay.md`](coplay.md)。 |
| 视觉观察、按需截图、传感器 | [`visual-perception.md`](visual-perception.md)、[`screen-observation-2026-09-12.md`](screen-observation-2026-09-12.md) | Desktop 看 [`Emerald-client/docs/screen-observation-2026-09-12.md`](../../Emerald-client/docs/screen-observation-2026-09-12.md)，Mobile 看 [`Emerald-mobile/docs/screen-observation-2026-09-12.md`](../../Emerald-mobile/docs/screen-observation-2026-09-12.md)。 |
| ESP32 / Intiface / 具身设备 | [`presence-device-firmware.md`](presence-device-firmware.md)、[`perform-mapping.md`](perform-mapping.md) | 传感器输入还看 [`manual-sensor-evidence.md`](manual-sensor-evidence.md) 和 Mobile [`protocols/sensor-event-protocol.md`](../../Emerald-mobile/docs/protocols/sensor-event-protocol.md)。 |
| 生活记录、资料接续、聊天日历 | [`life-records.md`](life-records.md)、[`conversation-calendar.md`](conversation-calendar.md) | 手机接入看 [`Emerald-mobile/docs/backend/integration.md`](../../Emerald-mobile/docs/backend/integration.md)，手机 UI 看 [`Emerald-mobile/docs/mobile/conversation-calendar.md`](../../Emerald-mobile/docs/mobile/conversation-calendar.md)。 |
| 媒体连续性、语音、思考旁白 | [`media-continuity-2026-09-13.md`](media-continuity-2026-09-13.md)、[`thinking-voice.md`](thinking-voice.md) | Desktop 展示看 [`Emerald-client/docs/brief-244-reasoning.md`](../../Emerald-client/docs/brief-244-reasoning.md)，Mobile 行为看 [`Emerald-mobile/docs/backend/integration.md`](../../Emerald-mobile/docs/backend/integration.md)。 |
| 发布、首次配置、备份恢复 | [`v1-release-contract.md`](v1-release-contract.md)、[`release-guide.md`](release-guide.md)、[`fresh-clone-testing.md`](fresh-clone-testing.md) | 数据备份看 [`offline-state-backup.md`](offline-state-backup.md)，Mobile 签名状态看 [`Emerald-mobile/docs/v1-release-readiness.md`](../../Emerald-mobile/docs/v1-release-readiness.md)。 |
| Mobile 正式签名、升级 lineage | [`Emerald-mobile/docs/v1-release-readiness.md`](../../Emerald-mobile/docs/v1-release-readiness.md)、[`Emerald-mobile/docs/android/release-signing-and-upgrade.md`](../../Emerald-mobile/docs/android/release-signing-and-upgrade.md) | 当前仍需正式候选包、keystore 和真机升级证据；不要以旧 `known-issues` 段落代替。 |
| 当前 bug、open、observe、roadmap | 后端 [`known-issues.md`](known-issues.md)、桌面 [`Emerald-client/docs/known-issues.md`](../../Emerald-client/docs/known-issues.md)、手机 [`Emerald-mobile/docs/known-issues.md`](../../Emerald-mobile/docs/known-issues.md) | 三仓交叉状态再登记到 [`three-repo-interface-catalog.md`](three-repo-interface-catalog.md)。 |

## 后端仓库：`Emerald-presence/docs/`

### 导航、架构与开发

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`README.md`](README.md) | current | 后端文档入口；本索引是三仓入口。 |
| [`three-repo-doc-index.md`](three-repo-doc-index.md) | authority | 三仓文档总入口、按功能直达和逐仓全量清单。 |
| [`agent-runtime-architecture.md`](agent-runtime-architecture.md) | authority | Agent Runtime 分层、同一角色主链/副链语义、身份/realm 和迁移边界。 |
| [`agent-runtime-browser-route-matrix.md`](agent-runtime-browser-route-matrix.md) | current | Agent Runtime 浏览器路线、路由和验收矩阵。 |
| [`api-reference.md`](api-reference.md) | authority | 后端 HTTP/WS API 目录和调用方。 |
| [`channels.md`](channels.md) | authority | QQ、Desktop、Mobile、WebSocket、广播和跨通道接续。 |
| [`dev-environment.md`](dev-environment.md) | authority | Windows Agent、pytest、构建、沙箱和跨仓验证约束。 |
| [`design-constraints.md`](design-constraints.md) | authority | 后端设计约束、准入标准和禁止行为的补充说明。 |
| [`external-companion-contract.md`](external-companion-contract.md) | supporting | 外部 companion / relay 接入边界。 |
| [`fresh-clone-testing.md`](fresh-clone-testing.md) | authority | 新克隆安装、配置、认证和最小可运行验证。 |
| [`interaction-event-model.md`](interaction-event-model.md) | authority | realm/kind/lifecycle 三维事件 envelope、stimulus 和 v0.1 边界。 |
| [`known-issues.md`](known-issues.md) | current | 后端当前 open、observe、roadmap 和技术债。 |
| [`protocol-compatibility-workflow.md`](protocol-compatibility-workflow.md) | current | 协议变更、兼容性核对和跨端验证流程。 |
| [`runtime-lifecycle.md`](runtime-lifecycle.md) | authority | 启动、运行、停止、资源和生命周期边界。 |
| [`testing-matrix.md`](testing-matrix.md) | authority | 后端自动化测试、评测脚本、CI 和发布验收范围。 |

### API、通道、设置与跨仓

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`backend-integration.md`](backend-integration.md) | supporting | Desktop/Mobile 共用请求字段和接入语义；不承载客户端完整 UI。 |
| [`backend-upgrade-recovery.md`](backend-upgrade-recovery.md) | supporting | 后端升级、回滚、备份和恢复操作说明。 |
| [`desktop-client-protocol.md`](desktop-client-protocol.md) | pointer | 指向 Desktop `protocol-v0.md`，补充后端实现落点、wake HTTP 和工具状态。 |
| [`feature-control-surface.md`](feature-control-surface.md) | authority | 功能开关、配置态/effective state、权限、设置入口和观测。 |
| [`ime-ingest.md`](ime-ingest.md) | authority | IME ingest、活动理解、隐私和主动关心边界。 |
| [`life-records.md`](life-records.md) | authority | 生活记录后端模型、权限、同步和资料接续。 |
| [`owner-turn-api.md`](owner-turn-api.md) | authority | owner turn HTTP 入口和 scoped 调用边界。 |
| [`three-repo-interface-catalog.md`](three-repo-interface-catalog.md) | authority | 三仓 HTTP、WS、Tauri IPC、Android channel、relay、设置/观测闭环总账。 |
| [`wake-bridge.md`](wake-bridge.md) | current | 主动消息 wake bridge 和通知唤醒链路。 |

### 运行时与 Agent 能力

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`assistant-turn-sink.md`](assistant-turn-sink.md) | authority | 统一 assistant turn sink、关键/慢速后处理和写入顺序。 |
| [`autonomy.md`](autonomy.md) | authority | 主动评估、工具调用、熔断和主动消息出口。 |
| [`intent-grounding.md`](intent-grounding.md) | authority | 用户意图 grounding、trusted text 和探针约束。 |
| [`mcp-server-authoring-template.md`](mcp-server-authoring-template.md) | template | 独立 MCP Server 的设计、数据契约和 Emerald 接入模板。 |
| [`model-presets.md`](model-presets.md) | authority | 多模型 preset、路由、参数合并和 prompt style。 |
| [`prompt-layers.md`](prompt-layers.md) | authority | Prompt 层、tag 激活、裁剪和注入顺序。 |
| [`scheduler.md`](scheduler.md) | authority | scheduler 主循环、trigger、gating、主动调度。 |
| [`thinking-voice.md`](thinking-voice.md) | current | 思考旁白、语音和跨端展示契约。 |
| [`tool-discovery.md`](tool-discovery.md) | current | 工具探针、发现、能力暴露和工具分类。 |
| [`tools.md`](tools.md) | authority | 工具注册、调度、探针、execute origin 和 tool loop。 |

### 记忆、数据与内容

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`authored-root-migration.md`](authored-root-migration.md) | supporting | authored 资产根目录迁移和兼容 fallback。 |
| [`authored-root-truth-audit.md`](authored-root-truth-audit.md) | audit | authored 根目录实际状态与代码交叉核对。 |
| [`c1-root-asset-inventory.md`](c1-root-asset-inventory.md) | audit | C1 资产根和文件分类盘点。 |
| [`data-taxonomy.md`](data-taxonomy.md) | authority | data/userdata/seed/history 的分类、路径和保留边界。 |
| [`memory-event-baseline.md`](memory-event-baseline.md) | audit | Memory Event 基线和 shadow 评估。 |
| [`memory-storage-architecture-assessment.md`](memory-storage-architecture-assessment.md) | assessment | 记忆存储架构风险、迁移和一致性评估。 |
| [`memory.md`](memory.md) | authority | episodic、identity、event_log、mood、hidden state 和并发保护。 |
| [`private-content-manifest.md`](private-content-manifest.md) | authority | 用户私有 authored 内容清单和发布边界。 |
| [`vector-store.md`](vector-store.md) | authority | 向量写入、召回、来源隔离和一致性。 |

### Dream、Stage、Activity、Garden 与设备

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`activity-session.md`](activity-session.md) | authority | Activity session、回合、状态和恢复。 |
| [`chess-activity.md`](chess-activity.md) | current | Chess 活动协议、回合和观测。 |
| [`coplay.md`](coplay.md) | authority | 共玩模式、现场控制和跨端边界。 |
| [`dream-seed-activity.md`](dream-seed-activity.md) | current | Dream seed 与 Activity 的接入关系。 |
| [`dream.md`](dream.md) | authority | Dream domain、world/preset/scenario、状态和隔离。 |
| [`dream-system.html`](dream-system.html) | historical | Dream 概念 brief 和视觉草案，不是实现真值。 |
| [`garden.md`](garden.md) | authority | 花园、情绪花槽、浇水、采后处理和管理面板。 |
| [`gomoku-activity.md`](gomoku-activity.md) | current | Gomoku 活动协议、回合和观测。 |
| [`perform-mapping.md`](perform-mapping.md) | current | 表演动作到硬件/具身输出的映射。 |
| [`presence-device-firmware.md`](presence-device-firmware.md) | authority | ESP32 具身硬件固件、WS 鉴权和显示协议。 |
| [`reading-activity.md`](reading-activity.md) | current | 阅读 Activity、进度和内容边界。 |
| [`rpg-dream-api.md`](rpg-dream-api.md) | authority | RPG Dream 后端 API、状态、回合和恢复。 |
| [`rpg-dream-client-guide.md`](rpg-dream-client-guide.md) | supporting | RPG Dream 客户端展示、lane 选择和恢复接入。 |
| [`rpg-dream-mode-design.md`](rpg-dream-mode-design.md) | historical | RPG Dream 设计提案和未承诺方案。 |
| [`stage.md`](stage.md) | authority | 多角色 Stage session、transcript 和回合仲裁。 |

### 视觉、媒体、发布与历史审计

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`conversation-calendar.md`](conversation-calendar.md) | authority | 后端聊天日历、统计和查询契约。 |
| [`legacy-backlog-audit.md`](legacy-backlog-audit.md) | audit | legacy backlog 清理、保留和删除候选。 |
| [`manual-sensor-evidence.md`](manual-sensor-evidence.md) | current | 手动传感器证据和验证记录。 |
| [`media-continuity-2026-09-13.md`](media-continuity-2026-09-13.md) | audit | 媒体连续性、附件和跨端恢复状态。 |
| [`offline-state-backup.md`](offline-state-backup.md) | authority | 离线状态备份、恢复和升级前保护。 |
| [`proactivity-2026-09-12.md`](proactivity-2026-09-12.md) | audit | 主动性排查、运行证据和当前 open 项。跨仓完整报告保留在后端。 |
| [`prompt-unification-audit.md`](prompt-unification-audit.md) | audit | Prompt 统一审计记录；规则仍以 prompt 层文档和代码为准。 |
| [`release-guide.md`](release-guide.md) | authority | CI、发布、签名材料和版本交接。 |
| [`screen-observation-2026-09-12.md`](screen-observation-2026-09-12.md) | audit | Desktop/Mobile 按需截图共同协议、隐私、TTL、回执和验收状态。 |
| [`admin-settings-visual-review.md`](admin-settings-visual-review.md) | audit | 管理面板设置页视觉审计和已知视觉问题。 |
| [`security.md`](security.md) | authority | 鉴权、token registry、scope 和管理面安全实现。 |
| [`security_model.md`](security_model.md) | authority | 威胁模型、部署假设和安全边界。 |
| [`settings-reorganization-audit-2026-09-10.md`](settings-reorganization-audit-2026-09-10.md) | historical | Brief 242 设置重组的决策和初始审计；当前状态看 feature-control-surface。 |
| [`system-readiness.md`](system-readiness.md) | historical | 旧 readiness 快照；不与当前 release contract/known issues 竞争。 |
| [`test_record.md`](test_record.md) | template | 手动测试记录模板。 |
| [`token-rotation.md`](token-rotation.md) | authority | token 签发、轮换、禁用和恢复操作。 |
| [`v1-cold-start-single-user-deployment.md`](v1-cold-start-single-user-deployment.md) | authority | v1 首次启动、迁移、备份恢复和单用户部署。 |
| [`v1-release-contract.md`](v1-release-contract.md) | authority | v1 产品发布承诺和必需能力。 |
| [`v1-release-readiness.md`](v1-release-readiness.md) | current | 后端发布就绪度和未完成证据。 |
| [`visual-perception.md`](visual-perception.md) | authority | 视觉感知、输入、隐私过滤和模型边界。 |
| [`xiaohongshu-reader.md`](xiaohongshu-reader.md) | current | 小红书资料读取、部署和来源隔离。 |

### 后端历史/人类教学文档

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`docs-truth-census.md`](docs-truth-census.md) | historical | 2026-08-10 的旧三仓盘点；仅作历史证据，不能当实时库存。 |
| [`user-teach/README.md`](user-teach/README.md) | historical | 一次性审计、部署和交接文档目录说明。 |
| [`user-teach/配置改进候选.md`](user-teach/配置改进候选.md) | historical | 配置改进候选和待议事项。 |
| [`user-teach/cross_project_interaction_flow.md`](user-teach/cross_project_interaction_flow.md) | historical | 跨项目交互流程快照。 |
| [`user-teach/handoff-memory-confab-fixation-20260621.md`](user-teach/handoff-memory-confab-fixation-20260621.md) | historical | 记忆 confab/fixation 交接记录。 |
| [`user-teach/interaction_issues_dedup.md`](user-teach/interaction_issues_dedup.md) | historical | 交互问题去重记录。 |
| [`user-teach/memory-recall-audit.md`](user-teach/memory-recall-audit.md) | historical | 记忆召回审计快照。 |
| [`user-teach/opensource-v0.1-checklist.md`](user-teach/opensource-v0.1-checklist.md) | historical | 开源 v0.1 检查清单和旧环境记录。 |
| [`user-teach/proactive-trigger-audit.md`](user-teach/proactive-trigger-audit.md) | historical | 主动触发器审计快照。 |
| [`user-teach/server-backup-and-upgrade-runbook.md`](user-teach/server-backup-and-upgrade-runbook.md) | supporting | Ubuntu 单机备份、恢复和源码升级 runbook。 |
| [`user-teach/ubuntu-single-user-deployment.md`](user-teach/ubuntu-single-user-deployment.md) | supporting | Ubuntu + systemd + Tailscale 部署示例。 |
| [`user-teach/vbox-hyperv-vtx-troubleshooting.md`](user-teach/vbox-hyperv-vtx-troubleshooting.md) | historical | VirtualBox/Hyper-V/VTX 排障记录。 |
| [`inspiration/playability_and_extensibility.md`](inspiration/playability_and_extensibility.md) | inspiration | 活动可玩性和可扩展性灵感，不是协议。 |

## Desktop 仓库：`Emerald-client/docs/`

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`backend-integration.md`](../../Emerald-client/docs/backend-integration.md) | supporting | Desktop 调用后端的 endpoint、字段、错误和 fallback；不复制后端物理路径。 |
| [`brief-174-runtime-baseline.md`](../../Emerald-client/docs/brief-174-runtime-baseline.md) | historical | Brief 174 桌面生命周期/性能基线和验收快照。 |
| [`brief-244-reasoning.md`](../../Emerald-client/docs/brief-244-reasoning.md) | current | Desktop 思考旁白的接收、渲染和降级。 |
| [`brief-60-runtime-baseline.md`](../../Emerald-client/docs/brief-60-runtime-baseline.md) | historical | 桌面多窗口/runtime 性能基线。 |
| [`brief-63-freeform-primitives.md`](../../Emerald-client/docs/brief-63-freeform-primitives.md) | current | Freeform primitives、舞台和布局约束。 |
| [`brief-67-koke-niwa-integrated-chat-shell.md`](../../Emerald-client/docs/brief-67-koke-niwa-integrated-chat-shell.md) | historical | Koke/Niwa 集成聊天壳设计和实施记录。 |
| [`brief-71-acceptance-record.md`](../../Emerald-client/docs/brief-71-acceptance-record.md) | historical | Brief 71 接受记录和测试证据。 |
| [`chat-correlation.md`](../../Emerald-client/docs/chat-correlation.md) | authority | Desktop chat history、turn id、重试和消息关联。 |
| [`chat-usability-2026-09-10.md`](../../Emerald-client/docs/chat-usability-2026-09-10.md) | audit | 聊天可用性排查和未完成体验项。 |
| [`client-fixes-2026-09-11.md`](../../Emerald-client/docs/client-fixes-2026-09-11.md) | audit | Desktop 修复批次的验证和遗留项。 |
| [`design-constraints.md`](../../Emerald-client/docs/design-constraints.md) | authority | Desktop UI/交互设计约束。 |
| [`design-mod-authoring.md`](../../Emerald-client/docs/design-mod-authoring.md) | authority | Desktop 设计 mod 的制作、加载和契约。 |
| [`design-mods.md`](../../Emerald-client/docs/design-mods.md) | current | 设计 mod、主题和视觉扩展。 |
| [`dream-hud.md`](../../Emerald-client/docs/dream-hud.md) | current | Dream HUD、状态、回放和交互展示。 |
| [`dream-isolation-2026-09-13.md`](../../Emerald-client/docs/dream-isolation-2026-09-13.md) | audit | Desktop Dream 广播隔离和真实联调状态。 |
| [`frontend-structure.md`](../../Emerald-client/docs/frontend-structure.md) | authority | React/Tauri 窗口、组件、controller 和本地存储结构。 |
| [`known-issues.md`](../../Emerald-client/docs/known-issues.md) | current | Desktop 当前问题、open/observe 和历史背景。 |
| [`layout-mods.md`](../../Emerald-client/docs/layout-mods.md) | current | 布局 mod、尺寸和组合规则。 |
| [`memory.md`](../../Emerald-client/docs/memory.md) | supporting | Desktop 记忆展示、读取和客户端边界。 |
| [`pet-window-reference.md`](../../Emerald-client/docs/pet-window-reference.md) | historical | 旧桌宠窗口参考资料，不是当前实现 authority。 |
| [`proactivity-2026-09-12.md`](../../Emerald-client/docs/proactivity-2026-09-12.md) | pointer | 指向后端完整主动性报告；只追加 Desktop 专属证据。 |
| [`protocol-v0.md`](../../Emerald-client/docs/protocol-v0.md) | authority | Desktop v0.1 wire protocol、消息字段和 WS 行为。 |
| [`release-v0.1.md`](../../Emerald-client/docs/release-v0.1.md) | current | Desktop v0.1 发布、构建和验收边界。 |
| [`runtime-acceptance-matrix.md`](../../Emerald-client/docs/runtime-acceptance-matrix.md) | authority | Windows/Tauri runtime、窗口生命周期和真实验收矩阵。 |
| [`screen-observation-2026-09-12.md`](../../Emerald-client/docs/screen-observation-2026-09-12.md) | pointer | 指向后端共同截图报告；只保留 Desktop/Tauri 差异。 |
| [`settings-control-audit.md`](../../Emerald-client/docs/settings-control-audit.md) | authority | Desktop 设置归属、后端设置入口、本地能力和未完成联调。 |
| [`testing.md`](../../Emerald-client/docs/testing.md) | authority | Desktop 测试、TypeScript、Vite、Cargo 和手工验收。 |
| [`tool-activity-2026-09-12.md`](../../Emerald-client/docs/tool-activity-2026-09-12.md) | audit | 工具活动显示和旁白排查记录。 |
| [`ui-mods.md`](../../Emerald-client/docs/ui-mods.md) | current | UI mod、视觉变量和应用方式。 |
| [`ui-polish-2026-09-11.md`](../../Emerald-client/docs/ui-polish-2026-09-11.md) | audit | UI polish 批次、截图/夹具验证和遗留项。 |
| [`ui-refinements-2026-09-11.md`](../../Emerald-client/docs/ui-refinements-2026-09-11.md) | audit | UI refinement 批次和视觉修整记录。 |
| [`人类说明书/live2d-model-import-guide.md`](../../Emerald-client/docs/人类说明书/live2d-model-import-guide.md) | supporting | Live2D 模型导入和资源目录操作。 |
| [`人类说明书/room-model-import-guide.md`](../../Emerald-client/docs/人类说明书/room-model-import-guide.md) | supporting | Room 模型导入和资源目录操作。 |
| [`inspiration/ui_design_ideas.md`](../../Emerald-client/docs/inspiration/ui_design_ideas.md) | inspiration | UI 设计灵感，不是实现契约。 |

## Mobile 仓库：`Emerald-mobile/docs/`

| 文档 | 状态 | 负责什么 |
|---|---|---|
| [`README.md`](../../Emerald-mobile/docs/README.md) | current | Mobile 文档入口、维护规则和当前边界。 |
| [`android/device-lifecycle-matrix.md`](../../Emerald-mobile/docs/android/device-lifecycle-matrix.md) | authority | Android 真机、后台、Doze、OEM 和生命周期验收矩阵。 |
| [`android/instrumented-testing.md`](../../Emerald-mobile/docs/android/instrumented-testing.md) | authority | Android instrumented test、安装和运行方式。 |
| [`android/native-capabilities.md`](../../Emerald-mobile/docs/android/native-capabilities.md) | authority | Android 权限、MethodChannel、后台服务、安全和原生能力。 |
| [`android/release-signing-and-upgrade.md`](../../Emerald-mobile/docs/android/release-signing-and-upgrade.md) | authority | 正式签名、keystore、版本升级和 lineage gate。 |
| [`archive/main-dart-split-plan.md`](../../Emerald-mobile/docs/archive/main-dart-split-plan.md) | historical | 已完成的 `main.dart` 拆分计划；当前结构看 `mobile/flutter-structure.md`。 |
| [`backend/integration.md`](../../Emerald-mobile/docs/backend/integration.md) | supporting | Mobile endpoint、鉴权、调用矩阵、生活记录和数据流。 |
| [`known-issues.md`](../../Emerald-mobile/docs/known-issues.md) | current | Mobile 当前 bug、observe、发布风险和历史背景。 |
| [`mobile/background-notification-design.md`](../../Emerald-mobile/docs/mobile/background-notification-design.md) | authority | 前后台通知、ntfy SSE、AlarmManager 和用户可见行为。 |
| [`mobile/chat-history-and-tools.md`](../../Emerald-mobile/docs/mobile/chat-history-and-tools.md) | current | Mobile 聊天历史、工具 receipt 和恢复行为。 |
| [`mobile/color-mods.md`](../../Emerald-mobile/docs/mobile/color-mods.md) | current | Mobile 颜色 mod、导出和打包契约。 |
| [`mobile/conversation-calendar.md`](../../Emerald-mobile/docs/mobile/conversation-calendar.md) | current | Mobile 日历 UI、分页和空状态。后端统计契约在 Presence。 |
| [`mobile/dream-wake-and-reveal.md`](../../Emerald-mobile/docs/mobile/dream-wake-and-reveal.md) | current | Mobile Dream wake、reveal 和恢复展示。 |
| [`mobile/flutter-structure.md`](../../Emerald-mobile/docs/mobile/flutter-structure.md) | authority | Flutter 页面、状态、组件和拆分后的当前结构。 |
| [`mobile/localization.md`](../../Emerald-mobile/docs/mobile/localization.md) | authority | ARB、本地化键、语言选择和持久化。 |
| [`overview/project-snapshot.md`](../../Emerald-mobile/docs/overview/project-snapshot.md) | current | Mobile 当前项目快照、目录和功能边界。 |
| [`protocols/mobile-channel.md`](../../Emerald-mobile/docs/protocols/mobile-channel.md) | authority | Mobile channel、poll/ack、payload、behavior 和后台消费。 |
| [`protocols/phone-control-protocol.md`](../../Emerald-mobile/docs/protocols/phone-control-protocol.md) | authority | 手机控制动作、确认和安全白名单。 |
| [`protocols/relay-publish-contract.md`](../../Emerald-mobile/docs/protocols/relay-publish-contract.md) | authority | relay signal-only 发布、topic、鉴权和去重语义。 |
| [`protocols/sensor-event-protocol.md`](../../Emerald-mobile/docs/protocols/sensor-event-protocol.md) | authority | Mobile/硬件传感器事件字段、隐私和接收端点。 |
| [`quality/testing-and-dev.md`](../../Emerald-mobile/docs/quality/testing-and-dev.md) | authority | Flutter/Android 分析、测试、构建、安装和验证边界。 |
| [`reference/push-relay-spike.md`](../../Emerald-mobile/docs/reference/push-relay-spike.md) | historical | 已封存的 push relay spike 结论。 |
| [`roadmap-notes.md`](../../Emerald-mobile/docs/roadmap-notes.md) | historical | 未归档路线和后续议题，不是 current implementation authority。 |
| [`screen-observation-2026-09-12.md`](../../Emerald-mobile/docs/screen-observation-2026-09-12.md) | pointer | 指向后端共同截图报告；只保留 Android 差异。 |
| [`v1-release-readiness.md`](../../Emerald-mobile/docs/v1-release-readiness.md) | authority | Mobile v1 发布证据、签名 lineage 和真机升级 gate。 |
| [`reference/desktop-jsx/Yexuan Companion App.html`](../../Emerald-mobile/docs/reference/desktop-jsx/Yexuan%20Companion%20App.html) | reference | Desktop JSX 视觉参考；不得当作 Flutter/Android 实现结构。 |

## 仓库根目录与附属文档

以下文件不在 `docs/` 目录，但属于三仓的人类文档入口或协作记录，也纳入导航范围。

### Presence 根目录 / 任务记录

| 路径 | 状态 | 负责什么 |
|---|---|---|
| [`AGENTS.md`](../AGENTS.md) | authority | 本仓任务入口、强制规则和专题文档路由。 |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | authority | 后端系统总览和主 pipeline。 |
| [`CLAUDE.md`](../CLAUDE.md) / [`CODEX.md`](../CODEX.md) | compatibility | 协作工具兼容入口；不与 AGENTS 竞争。 |
| [`DESIGN.md`](../DESIGN.md) | authority | 产品设计意图、准入标准和禁止行为。 |
| [`README.md`](../README.md) / [`README.zh-CN.md`](../README.zh-CN.md) | current | 项目安装、运行和对外介绍入口。 |
| [`cc-tasks/00c-研究备忘-prompt对模型输出的影响.md`](../cc-tasks/00c-研究备忘-prompt对模型输出的影响.md) | task | Prompt 研究备忘。 |
| [`cc-tasks/00d-研究备忘-长期记忆Fragment-Event聚合评估.md`](../cc-tasks/00d-研究备忘-长期记忆Fragment-Event聚合评估.md) | task | 长期记忆聚合评估备忘。 |
| [`cc-tasks/00f-讨论备忘-主体性方向与架构现状核对.md`](../cc-tasks/00f-讨论备忘-主体性方向与架构现状核对.md) | task | 主体性方向和架构核对备忘。 |
| [`cc-tasks/217-memory-event-context-identity-soak.md`](../cc-tasks/217-memory-event-context-identity-soak.md) | task | Memory Event/context/identity soak 工单。 |
| [`cc-tasks/218-memory-event-repository-boundary.md`](../cc-tasks/218-memory-event-repository-boundary.md) | task | Memory Event repository boundary 工单。 |
| [`cc-tasks/226-支出能力重审与受限支付适配.md`](../cc-tasks/226-支出能力重审与受限支付适配.md) | task | 支出能力和受限支付风险重审。 |
| [`cc-tasks/227-视觉观察人工审计与习惯固化准入.md`](../cc-tasks/227-视觉观察人工审计与习惯固化准入.md) | task | 视觉观察人工审计和准入。 |
| [`cc-tasks/250-admin-prompt-followups.md`](../cc-tasks/250-admin-prompt-followups.md) | task | Admin prompt follow-up 接力记录。 |
| [`cc-tasks/253-voice-drink-files-protocol-thinking.md`](../cc-tasks/253-voice-drink-files-protocol-thinking.md) | task | 语音、饮品文件协议和思考接力。 |
| [`cc-tasks/临时工单.md`](../cc-tasks/临时工单.md) | task | 临时施工记录；完成后应回填正式文档或归档。 |

### Desktop 根目录 / 任务记录

| 路径 | 状态 | 负责什么 |
|---|---|---|
| [`AGENTS.md`](../../Emerald-client/AGENTS.md) | authority | Desktop 协作入口和本仓约束。 |
| [`ARCHITECTURE.md`](../../Emerald-client/ARCHITECTURE.md) | authority | Desktop 结构、窗口和运行边界总览。 |
| [`CLAUDE.md`](../../Emerald-client/CLAUDE.md) | compatibility | 协作工具兼容入口。 |
| [`README.md`](../../Emerald-client/README.md) / [`README.zh-CN.md`](../../Emerald-client/README.zh-CN.md) | current | Desktop 安装、运行和产品入口。 |
| [`cc-tasks/2026-09-16-chat-send-retry-render.md`](../../Emerald-client/cc-tasks/2026-09-16-chat-send-retry-render.md) | task | 聊天发送、重试和渲染接力。 |
| [`cc-tasks/244-history-turn-id-backend-handoff.md`](../../Emerald-client/cc-tasks/244-history-turn-id-backend-handoff.md) | task | 历史 turn id 与后端交接。 |
| [`cc-tasks/72-agent-runtime-browser-client-retirement-audit.md`](../../Emerald-client/cc-tasks/72-agent-runtime-browser-client-retirement-audit.md) | task | Agent Runtime browser client retirement 审计。 |
| [`public/room/character/README.md`](../../Emerald-client/public/room/character/README.md) | supporting | Room character 资源放置说明。 |
| [`public/room/scene/README.md`](../../Emerald-client/public/room/scene/README.md) | supporting | Room scene 资源放置说明。 |
| [`public/themes/README.md`](../../Emerald-client/public/themes/README.md) | supporting | Desktop theme 资源说明。 |
| [`src/features/dream/README.md`](../../Emerald-client/src/features/dream/README.md) | supporting | Dream feature 目录和组件说明。 |

### Mobile 根目录 / 任务记录

| 路径 | 状态 | 负责什么 |
|---|---|---|
| [`AGENTS.md`](../../Emerald-mobile/AGENTS.md) | authority | Mobile 协作入口和验证规则。 |
| [`ARCHITECTURE.md`](../../Emerald-mobile/ARCHITECTURE.md) | authority | Flutter/Android、后台通道和生命周期总览。 |
| [`CLAUDE.md`](../../Emerald-mobile/CLAUDE.md) | compatibility | 协作工具兼容入口。 |
| [`README.md`](../../Emerald-mobile/README.md) / [`README.zh-CN.md`](../../Emerald-mobile/README.zh-CN.md) | current | Mobile 安装、运行和产品入口。 |
| [`cc-tasks/20-mobile-character-refresh-fixes.md`](../../Emerald-mobile/cc-tasks/20-mobile-character-refresh-fixes.md) | task | Mobile 角色刷新和缓存修复接力。 |
| [`ios/Runner/Assets.xcassets/LaunchImage.imageset/README.md`](../../Emerald-mobile/ios/Runner/Assets.xcassets/LaunchImage.imageset/README.md) | supporting | iOS 启动图资源说明。 |
| [`mods/README.md`](../../Emerald-mobile/mods/README.md) | supporting | Mobile mods 资源目录说明。 |
| [`spike/push_relay_ntfy/README.md`](../../Emerald-mobile/spike/push_relay_ntfy/README.md) | historical | ntfy push relay spike 入口。 |
| [`spike/push_relay_ntfy/android/test_log_template.md`](../../Emerald-mobile/spike/push_relay_ntfy/android/test_log_template.md) | template | Push relay Android 手测记录模板。 |

## 不纳入“人类文档”导航的 Markdown/HTML

以下文件是运行时资源、测试 fixture 或管理面板页面，不应和产品/工程文档混在同一索引中：

- `Emerald-presence/bundled/` 下的 seed、world、postcard 模板。
- `Emerald-presence/tests/fixtures/` 下的 Dream fixture。
- `Emerald-presence/admin/static/` 下的管理面板 HTML 页面；页面入口和职责查 `admin-navigation-guide.md`、`admin-design-implementation.md`。
- 三仓构建产物、依赖目录和生成文件。

如果这些资源的格式或字段发生变化，应在对应 authority 文档中补充“资源契约”或“测试 fixture”说明，而不是把资源本身当作设计文档。

## 文档维护规则

1. 新增跨仓字段、endpoint、IPC、MethodChannel、relay、设置或观测时，先更新对应 authority，再更新 `three-repo-interface-catalog.md` 和本索引。
2. dated audit、施工交接和已关闭问题只保留一份完整记录；其他仓库用 pointer，不复制全文。
3. `known-issues.md` 只维护当前 open/observe/roadmap；历史证据放在折叠历史或归档文档，并明确标注旧实现。
4. 修改协议或调用链后，检查本索引的“按功能直达”表、三仓 README 和相对链接。
5. 任何“已完成”结论都必须能回到代码、测试、构建或真实设备证据；夹具、旧快照和接口存在本身不算完成。
