# 后端 API 参考

`admin/admin_server.py` 是路由注册真值；各端点的请求/响应 schema 以运行中的
`/openapi.json` 为准。本页是跨端协作的稳定入口：新增或移除路由时，须同步更新本页和
客户端调用点，避免各客户端各自维护一份端点表。

桌面 WebSocket 消息与 action 契约不由 OpenAPI 描述，统一见 [desktop-client-protocol.md](desktop-client-protocol.md)。

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
| POST/GET | `/mobile/activate`、`/mobile/deactivate`、`/mobile/push`、`/mobile/ack`、`/mobile/poll` | mobile | Emerald-mobile |
| POST | `/upload/ingest`、`/transcribe` | desktop / admin | 桌宠、手机端 |
| GET | `/system/health`、`/system/status` | read/admin | 客户端状态页、运维 |

## 端点目录

下表以 router 前缀为单位列出完整端点族；同一族中每个操作及其精确 schema 可在
`/openapi.json` 或对应的 `admin/routers/*.py` 查看。`admin` 表示管理 token 或其满足的细分
scope；只读端点通常允许对应的 read scope。

| 方法 | 路径 | scope | 消费方 / 用途 |
|---|---|---|---|
| GET/PUT/POST | `/characters*` | characters | 管理面角色卡 |
| GET/POST/PUT/DELETE | `/memory/{user_id}/*` | memory | 管理面记忆浏览与删除 |
| GET/PUT/PATCH/DELETE | `/users/*`、`/relations/*`、`/relationship-facts/*` | users / relations | 管理面用户与关系 |
| GET/POST/PUT/DELETE | `/lorebook*`、`/jailbreak-entries*` | prompt_assets | 管理面 Prompt 资产 |
| GET/POST/PUT/DELETE | `/scheduler/*`、`/garden/*`、`/mood/*` | scheduler | 管理面状态和手动触发 |
| GET/POST/PATCH | `/dream/*` | dream | PresenceKit-desktop 梦境界面 |
| GET/POST | `/sensor/*`、`/watch/*` | sensor | Emerald-mobile、桌宠 |
| GET/POST | `/activity/*`（reading/gomoku/chess/dream_seed） | activity | PresenceKit-desktop 活动界面 |
| GET/POST | `/coplay/*` | coplay | PresenceKit-desktop 陪玩控制 |
| GET | `/diary/*`、`/chat-log/*` | read | 管理面历史浏览 |
| GET/POST/PUT/PATCH | `/llm-params`、`/vision-params`、`/model-presets/*`、`/context-config`、`/chat-*`、`/proxy`、`/settings/*` | settings | 管理面设置 |
| GET/POST/PUT/PATCH/DELETE | `/system/*`、`/hardware/*` | admin / hardware | 管理面运维、设备控制 |
| GET | `/observe/*`、`/provenance/*`、`/hidden-state/*` | observe | 管理面诊断 |
| GET/POST/PATCH/DELETE | `/auth/*` | auth | Token 管理页 |
| GET/POST/PATCH/DELETE | `/group/*` | group | Stage 群聊管理 |

## 维护约定

1. 后端先改 router，再更新本页的端点族、scope 和消费方。
2. 客户端仓不得再维护独立的后端端点清单；应链接到本页，并以 OpenAPI schema 生成或校验调用。
3. 改鉴权时同时更新 [security.md](security.md) 与本页的“鉴权与连接”。
