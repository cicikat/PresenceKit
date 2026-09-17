# 后端 API 参考

`admin/admin_server.py` 是路由注册真值；各端点的请求/响应 schema 以运行中的
`/openapi.json` 为准。本页是跨端协作的稳定入口：新增或移除路由时，须同步更新本页和
客户端调用点，避免各客户端各自维护一份端点表。

桌面 WebSocket 消息与 action 契约不由 OpenAPI 描述，跨仓总览见
[three-repo-interface-catalog.md](three-repo-interface-catalog.md)，桌面字段细节见
`Emerald-client/docs/protocol-v0.md`。
desktop / mobile 共用的聊天请求体可选字段（如 reply_to 引用回复）见 [backend-integration.md](backend-integration.md)。

## 鉴权与连接

除根路径和明确标为禁用的旧 `/chat` 外，HTTP 管理接口均需
`Authorization: Bearer <token>`。具体 token scope 由对应 router 的依赖声明决定；scope
常量及 profile 映射见 [security.md](security.md)。WebSocket 也只接受这个 header，不接受
query token。

| 方法 | 路径 | 最低 scope 类别 | 消费方 |
|---|---|---|---|
| WS | `/ws/desktop` | desktop | PresenceKit-desktop 桌宠 |
| WS | `/ws/device` | device | presence-device 固件 |
| POST | `/desktop/chat`、`/desktop/activate`、`/desktop/wake` | desktop | PresenceKit-desktop |
| POST | `/mobile/chat` | mobile | PresenceKit-mobile 手机前台主对话 |
| POST/GET | `/mobile/activate`、`/mobile/deactivate`、`/mobile/push`、`/mobile/ack`、`/mobile/poll` | mobile | Emerald-mobile |
| POST | `/upload/ingest`、`/transcribe` | desktop / admin | 桌宠、手机端 |
| GET | `/system/health`、`/system/status` | read/admin | 客户端状态页、运维 |
| POST/GET | `/v1/owner/turns`、`/v1/owner/turns/{client_turn_id}` | owner-input (`chat`) | 外部硬件、脚本和本地适配器；行为合同见 [owner-turn-api.md](owner-turn-api.md) |

## 端点目录

下表以 router 前缀为单位列出完整端点族；同一族中每个操作及其精确 schema 可在
`/openapi.json` 或对应的 `admin/routers/*.py` 查看。`admin` 表示管理 token 或其满足的细分
scope；只读端点通常允许对应的 read scope。

| 方法 | 路径 | scope | 消费方 / 用途 |
|---|---|---|---|
| GET/PUT/POST | `/characters*` | characters | 管理面角色卡 |
| GET/POST/PUT/DELETE | `/memory/{user_id}/*` | memory | 管理面记忆浏览与删除 |
| GET | `/memory-events/search`、`/memory-events/{event_id}`、`/memory-events/{event_id}/window`、`/memory-events/{event_id}/related`、`/memory-events/query-trace`、`/memory-events/lineage/*` | `memory.read` | 管理面 scoped Memory Event 证据检索与派生记忆血缘；显式 `uid + char_id + realm=reality`，仅只读，lineage 只按已存 `source_event_ids` 回溯，旧数据或已删除事件返回 `legacy_unknown`，dry-run 不写回 |
| GET | `/observability/memory-event-shadow-recall` | `state.read` | Memory Event 09 shadow recall 的只读灰度指标；不返回查询正文或事件证据 |
| GET | `/observability/event-context` | `state.read` | Brief 217 入口、turn 和证据身份链的旁路聚合；仅状态、计数和延迟桶，不返回原文、完整 ID、用户 ID、媒体或路径 |
| GET/PUT | `/settings/event-context-observer` | `admin` | 后端身份链旁路观测的热切换；S1 soak 前只允许 `disabled` / `observe`，不新增客户端协议 |
| DELETE | `/memory-events/{event_id}` | `admin` | 可逆墓碑：清空事件正文和媒体引用，保留 event ID、证据关系边和派生血缘；不提供物理删除 |
| GET | `/observability/memory-event-migration` | `state.read` | 内容无关的历史 Markdown 迁移状态、批次位置和计数；不返回正文、媒体或本地路径 |
| GET | `/observability/chat-identity` | `state.read` | 进程级聊天身份覆盖率：attempted / persisted_turn_id / generated_transport_id / empty_transport_id 及覆盖率；不含正文 |
| GET | `/chat/media/{sha256}` | `chat` | 按 sha256 读取仍可恢复的聊天原图/原文件；owner+活跃角色闸；410 不可恢复；不返回磁盘路径 |
| GET | `/observability/chat-media` | `state.read` | 聊天媒体引用计数、inbox/image_cache 保留策略与 live-ref 守卫；不含正文、路径或文件名 |
| GET/PUT/PATCH/DELETE | `/users/*`、`/relations/*`、`/relationship-facts/*` | users / relations | 管理面用户与关系 |
| GET/POST/PUT/DELETE | `/lorebook*`、`/jailbreak-entries*` | prompt_assets | 管理面 Prompt 资产 |
| GET/POST/PUT/DELETE | `/scheduler/*`、`/garden/*`、`/mood/*` | scheduler | 管理面状态和手动触发 |
| GET/POST/PATCH | `/dream/*` | dream | PresenceKit-desktop 梦境界面 |
| GET/POST | `/dream/rpg/*` | activity | RPG Dream 双栏客户端；见 [rpg-dream-client-guide.md](rpg-dream-client-guide.md) |
| GET/POST | `/sensor/*`、`/watch/*` | sensor | Emerald-mobile、桌宠 |
| GET/POST | `/activity/*`（reading/gomoku/chess/dream_seed） | activity | PresenceKit-desktop 活动界面 |
| GET/POST | `/coplay/*` | coplay | PresenceKit-desktop 陪玩控制 |
| GET | `/diary/*`、`/chat-log/*` | read | 管理面历史浏览 |
| GET/POST/PUT/PATCH | `/llm-params`、`/vision-params`、`/model-presets/*`、`/context-config`、`/chat-*`、`/proxy`、`/settings/*` | settings | 管理面设置 |
| GET/POST/PUT/PATCH/DELETE | `/system/*`、`/hardware/*` | admin / hardware | 管理面运维、设备控制 |
| GET/POST | `/spend/ledger`、`/spend/budget`、`/spend/mandates`、`/spend/check` | admin | 只读支出余额观测、人工检查与台账浏览 |
| GET | `/observe/*`、`/debug/recall`、`/provenance/*`、`/debug/user-hidden-state` | observe | 管理面与桌面客户端诊断；`/debug/recall?uid=` 是 recall trace 兼容入口 |
| GET | `/observability/api-calls`、`/observability/perceive-events`、`/observability/runtime-signals`、`/observability/owner-turns` | state.read | 外部 API 调用总账、reality stimulus 审计、运行信号和脱敏 owner-turn receipt 观测；均为只读查询。 |
| GET | `/perception/visual-trace` | state.read | 本地 VLM shadow 观察（不含原图，不进入 prompt/记忆） |
| GET/PUT/POST | `/tts-config`、`/tts-config/test` | admin | TTS provider 安全配置与已就绪 provider 的试听 |
| GET/POST/PATCH/DELETE | `/auth/*` | auth | Token 管理页。`GET /auth/whoami` 当前只回 `label`/`scopes`。拟议 `capabilities.session_scope` 见 [session-scope-contract.md](session-scope-contract.md)，未上线 |
| GET/POST/PATCH/DELETE | `/group/*` | group | Stage 群聊管理 |
| GET/PUT/POST | `/settings/agent-runtime-browser`、`/settings/agent-runtime-browser/tasks*` | admin | 后端唯一浏览器 allowlist、worker 和任务控制面；任务 receipt/观测仅返回脱敏 metadata |

## 维护约定

1. 后端先改 router，再更新本页的端点族、scope 和消费方。
2. 客户端仓不得再维护独立的后端端点清单；应链接到本页，并以 OpenAPI schema 生成或校验调用。
3. 改鉴权时同时更新 [security.md](security.md) 与本页的“鉴权与连接”。
| GET | `/observability/event-context` | `state.read` | Brief 217 persistent ingress/turn/evidence aggregation with startup readiness, chain counts, and latency percentiles; no bodies, complete IDs, user IDs, media, or paths |
| GET/PUT | `/settings/event-context-observer` | `admin` | Backend-only `disabled`/`observe`; enabling reruns ledger readiness and returns 503 without changing config when not ready |
