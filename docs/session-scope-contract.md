## T4 当前实现与验收边界（2026-11-25，partial）

本检查点已删除相关 HTTP 入口的无 Session → active-character 分支；缺少 Header 返回
422 `session_scope_required`，未知/过期返回 404 `session_not_found`，跨 token/角色返回明确拒绝。
产物下载/预览与转写也要求同一 grant；桌面持久回执加入角色命名空间。
桌面按连接和角色持有内存 grant；过期或拒绝不自动新建并重放请求，重新打开窗口才重新绑定。
手机同步停止失效后的自动重绑重发。mobile poll/ACK 仍为 owner 共享游标。

实现已推送：后端 PR #38（基线 `3d24c0e077698db6e48a106146e1fdcb107b2302`）、
桌面 PR #14（`c497124a8876204ba1c08d2d53af137084e61ff1`）、手机配套基线
`24b8867b11e43e14f130a3f63c417c3420a9275a`。没有生产部署授权。
最终三仓 matrix、独立审查及完整设备验收是独立门禁；自动测试通过不等于设备通过。
见 [T4 施工记录](refactors/migration-cutover-t4.md)。下方明确标为历史的 B 阶段表格
仅记录切断前实现，不能据此接入当前后端。

### 当前会话生命周期

| 操作 | 实际实现 |
|---|---|
| discover | `GET /auth/whoami` 的 `capabilities.session_scope=v1`；缺失时客户端停止发送 |
| create | `POST /v1/sessions`，`chat` scope；服务器校验角色资产后冻结 token label、owner_id、char_id、domain=reality |
| hold | 客户端内存持有 grant；桌面按连接及角色缓存，手机按当前连接/角色持有；请求 Header 为 `X-Presence-Session` |
| refresh | 没有 refresh API；过期不能自动签发后重放旧请求。用户明确重新打开桌面窗口或切换角色/连接，可走 create |
| invalidate | 24 小时 TTL、后端进程重启、owner 变化、角色不可用或 token 不匹配都会拒绝；每次 ingress 重新核验 |
| destroy | 没有独立 destroy API；本地替换/窗口进程退出丢弃引用，服务端 TTL/有界缓存回收 |

手机和通用 Session request_id 与冻结 scope 共同参与 30 分钟进程内 receipt 键；换同 scope
grant 不新开执行命名空间。桌面正式客户端另用 `X-Presence-Chat-Request` 的 `dc-...`
ID，复用既有持久 owner-turn receipt，键含 caller label 与 owner/char/reality 摘要，
新请求准入窗口为 24 小时；不能把这条持久路径说成仅有 30 分钟内存去重。
stream msg_id 关联临时气泡，persisted turn_id 关联历史与思考，两者不能互代。
媒体、产物预览/下载、历史、calendar、reasoning 与转写都核验同一 grant 的角色；
query char_id 必须与 grant 一致（否则 403），持有 artifact/media/turn ID 不授予跨角色读取权。
mobile poll/ACK 仍是 owner 共享游标，不能按当前角色过滤后推进 ACK。

2026-11-26 媒体归属修补：全局 inbox/cache 中存在字节不代表当前 grant 有权读取，
还须同 owner/角色的未墓碑 Reality 媒体引用或正式上传资料索引；没有授权证据与字节
不存在同为 410。生成图作为工具参考也按调用者 owner/角色读取产物，不以全局 ID lookup
结果替换调用者身份。隔离回归见 [媒体作用域修补](refactors/t4-media-scope-followup.md)。

## 安全失败重试（2026-10-08，271）

mobile/desktop chat 在工具执行与 turn sink 前失败时标记 retryable_failed，同 request_id/payload 可以重跑；已开始工具/回合写入、未知入口和 upload 保留 execution_outcome_unknown。context/probe 并发失败会取消并等待另一任务，避免重试与残留工具交错。completed/in_flight/409、token+owner+char+domain 隔离和 30 分钟进程内保留不变；旧 failed 无阶段证据不重跑。既有 /observability/session-scope 可见 request_failed 原因 retryable_failed/failed 及 request_retry，无新持久状态或开关。chat-log plain/display 均取 ledger visible_text；记忆存储与原文不改。真机/真实模型观察仍 open。
## 同作用域重绑与回执（2026-10-07）

request receipt 内部键改为 token_label+owner_id+char_id+domain+request_id，session_id 仍是服务端授权 grant，必须经过现有 ingress 校验。仅更换同作用域 grant 不会重新执行同一 request_id；不同 token/owner/char/domain 仍隔离，同 ID 不同 payload 仍 409。completed 回放返回调用者当前 session_id。现有 30 分钟/进程内保留边界不变，字段不删不改。

# 固定会话 scope 契约

状态：后端 `current`（`session_scope=v1`）；手机消费者已接入发现/绑定/角色归属/
失败气泡 `request_id` 复用（2026-09-19 自动化验收见
[runtime-recovery-257.md](runtime-recovery-257.md)）；桌面消费者代码已接入，完整设备验收仍 open。
日期：2026-09-19。
本文是给 C 与桌面/手机接入评审的合同，不是已发布协议，也不是 OpenAPI 现状。
本页第 1 节保留 B 阶段的上线前基线，后续章节保留设计理由；当前实现差异以紧随其后的清单为准。
权威仍是运行中的 `/openapi.json`、[api-reference.md](api-reference.md) 与现有 v0.1 通道实现。

Dream settings 归属走工单 F，本文只映射 domain 名称，不决定 Dream 配置树。
桌面 wire 正文仍在 PresenceKit-desktop `docs/protocol-v0.md`；本仓不复制、不单边扩展 v0.1 消息全集。
手机通道正文仍在 Emerald-mobile `docs/protocols/mobile-channel.md`。本工作区无手机/桌面仓时，两端接入依赖只列在本文和接口总账。

现有 `tests/protocol_fixtures/v1/` 是 owner-turn / 桌面 HTTP-WS / 手机 poll-ack 的 **已冻结** 合同。
当前 provider fixture 为 `tests/protocol_fixtures/v1/session_scope.json`。B 阶段评审夹具仍保留在
`tests/protocol_fixtures/session-scope-proposed/` 作为决策记录。三仓基线已升级；
`.github/protocol-matrix.json` 只对明确记录并实际测试的 SHA 提供证据，
不把旧组合兼容结果用作 T4 设备验收。

## 0. 后端 current（工单 C）

- `GET /auth/whoami` 广告 `capabilities.session_scope="v1"`；`POST /v1/sessions`
  按有效 chat token、服务端 owner 与已安装非隐藏角色签发 24 小时不透明 session。
- 后续请求用 `X-Presence-Session`。`/desktop/chat`、`/mobile/chat`、`/upload/ingest`、
  `/desktop/wake`、媒体、历史、calendar 与 reasoning 均从同一 grant 解析 owner/char；
  缺失 Header 的旧客户端返回 422 `session_scope_required`，没有 live-active fallback。
- chat/upload 接受 `request_id`，30 分钟有界 receipt 实现 completed replay、并发
  `in_flight`、payload conflict 与保守 unknown-result；不承诺 exactly-once。
- HTTP、WS stream start/canonical/segments 与 mobile durable envelope 携带可用的
  `request_id`、`char_id`、`domain`，persisted `turn_id` 仍独立。
- `GET /observability/session-scope`（`state.read`）只返回 capability、计数、TTL、
  request/session ID 和拒绝原因等元数据，不返回正文、token、Prompt 或路径。
- session 与通用 Session receipt 为进程内有界状态；桌面 `dc-...` receipt 独立持久化。
  服务重启后 session 失效，客户端停止请求，明确重新打开窗口后才重新绑定。
  Dream settings 归属仍走工单 F。

---

## 1. 历史事实（B1 切断前端点矩阵，不是当前协议）

下表保留 B1 设计审计时的实现；T4 当前受影响入口以第 0 节和页面顶部为准。

| 端点 | 当前 scope 来源 | 权限 | 缺口 | 消费者 |
|---|---|---|---|---|
| `POST /desktop/chat` | body 不读 `char_id`；`legacy_desktop_context` → `run_owner_chat_turn` 在入口 `pipeline._current_reality_scope(owner_id)` 冻一次 memory scope | `chat` | 无 `request_id`；无会话授权句柄；HTTP 边仍把 live active 当本轮角色 | PresenceKit-desktop |
| `POST /mobile/chat` | 同上，`legacy_mobile_context` | `chat` | 同桌面 | Emerald-mobile |
| `POST /v1/owner/turns` | 禁止 body `uid`/`char_id`/`source`/`origin`/tool capability；内部同样冻 active；幂等键是 `client_turn_id` | `owner-input` 或 `integration` + `chat` | 无 session-scope capability；`upload_ids` 非空固定 409 `upload_id_not_available` | 外部适配器 |
| `GET /v1/owner/turns/{client_turn_id}` | 仅当前 token caller 的 receipt | 同上 | receipt 不含会话冻结投影 | 适配器 |
| `POST /upload/ingest` | `scheduler.owner_id` + 当时 `pipeline._active_character_id`（或 `DEFAULT_CHAR_ID`）写入媒体；随后另一次 `run_owner_chat_turn` 再冻 scope | `chat` | 上传桶与随后对话冻结可能不是同一角色；无 session 绑定 | 桌面、手机 |
| `GET /chat/media/{sha256}` | `_owner_media_scope()` = owner + **live** active | `chat` | 持有 sha256 ≠ 读权；闸是 live active，不是授权会话 | 桌面、手机 |
| `GET /chat/artifacts/{id}` 及 `/preview` | 产物记录里的 `uid`+`char_id` | `chat` | 知道 id 仍受 chat token；不是请求会话冻结 | 桌面 |
| `POST /desktop/wake` Path A | `active_prompt_assets.json` 的 `active_character`，缺则 fail-loud 落到 Path B | `chat` | live active；回放 `turn_id == msg_id` | 桌面 |
| `POST /desktop/wake` Path B | `perceive_event` 返回的 `char_id`（gate 时的 active）；gate 后禁止第二次 active 查找 | `chat` | 不生成新 turn_id/msg_id | 桌面 |
| `POST /desktop/activate` | 通道级，无角色 | `chat` | 非会话 | 桌面 |
| `GET /chat-log/dates`、`GET /chat-log/{date}` | query `char_id` 或 live active；uid-only 旧日志仅冻结历史默认角色可 union | `memory.read` | query 角色不是会话冻结；省略则跟 active 走 | 桌面、手机、管理面 |
| `GET /diary/list`、`GET /diary/{date}`、`GET /garden/state`、`GET /mood/state`、`GET /activity/current` | 可选 query `char_id`；未知/隐藏 `422 character_unavailable`；省略则 live active | `memory.read` / `state.read` | 不是 Reality session 冻结；手机按本机所选角色带 query | 桌面、手机、管理面 |
| `GET /dream/state`、`GET /dream/settings`、`GET /dream/stats`、`POST /dream/enter` | 可选 query/body `char_id`；未知/隐藏 `422 character_unavailable`；省略则当前 Dream 角色 | `activity` | Dream 独立域，不套用 Reality `X-Presence-Session` | 桌面、手机、管理面 |
| `GET /chat-log/stats/calendar` | 同上解析，只扫 canonical 桶 | `memory.read` + `state.read` | 无 session 句柄 | 管理面 |
| `GET /chat/turns/{turn_id}/reasoning` | 只按 persisted `turn_id` | `memory.read` | 持有 turn_id ≠ 角色读权；无 char/session 闸 | 桌面、手机 |
| WS `/ws/desktop` `message_stream_*` / `channel_message` / `message_segments` | 可选 `char_id`/`domain`/`round_id`；桌面流式先 mint `_stream_msg_id`，start 当前可不带 char_id；canonical 后补 char_id | `ws.desktop` | 早期无 turn_id；迟到/乱序/重连无显式 bind 事件 | 桌面 |
| `POST /mobile/activate`、`/mobile/deactivate` | 通道级 TTL，无角色 | `chat` | 非 per-char | 手机 |
| `GET /mobile/poll`、`POST /mobile/ack` | origin+owner **共享** `seq` 游标；`char_id` 只是入队信封 | `chat` | 按角色过滤却 ack 共享 cursor 会丢其他角色的消息 | 手机前台/后台 |
| `POST /mobile/push` | 可选 body `char_id` 写入信封 | `chat` | 调试/工具入口，不是会话冻结 | 管理面/工具 |
| relay 信号 | `id`/`seq`/`user_id`/`timestamp`/`signal`；无 char_id、无正文 | 独立 relay token | 设备选择在手机本地；后端不点名设备 | Android |
| `PATCH /settings/prompt-assets` `active_character`、`PUT /characters/active` | 进程级 live active，并热写 `pipeline._active_character_id` | `persona` | 这是全局切换，不是 per-request 授权 | 管理面、桌面/手机设置 |
| `GET /characters`、`GET /characters/active-info` | 可见角色列表 / 当前 active 展示 | `persona` | **列表不是授权** | 桌面、手机 |
| `GET /auth/whoami` | `{label, scopes}` | 任意有效 token | 无角色 grant、无 session_scope capability | 全端 |
| `GET /observability/deployment-capabilities` | 本机 vs 远程工具策略 | `state.read` | **不是** 会话 capability | 管理面 |
| `GET /observability/owner-turns` | 脱敏 receipt | `state.read` | 无会话冻结观测 | 管理面 |

内部已有、且拟议合同必须复用的事实：

- `run_owner_chat_turn()` 已冻 `MemoryScope` 与 `EventContext`；中途管理面切角色不得拆读写。C 禁止用临时改 active 冒充隔离。
- 桌面流式 `_stream_msg_id` 可与 persisted `turn_id` 不同；手机非流式且 critical 已落盘时 `msg_id == turn_id`，无 turn_id 时仍 mint 不透明 `msg_id`。
- 主动消息 `char_id` 随 turn_sink fanout 可选附带；队列身份是 `id`/`turn_id`（传输 correlator），顺序是 `seq`。

---

## 2. 本机固定角色的授权来源（B2）

### 2.1 当前

Owner 来自进程配置 `scheduler.owner_id`。当前角色来自服务端 grant；旧 B 阶段角色来自 live active。
客户端 body 不能指定 owner。legacy chat 也不读 body `char_id`。可见列表 `GET /characters` 只是 persona 可读资产。

### 2.2 已落地的授权模型

1. **Owner** 仍只由进程配置解析，调用方不能覆盖。
2. **角色授权** 是服务端 grant，不是客户端本地选中，也不是 `GET /characters` 的可见列表。
3. 客户端若要固定会话角色，必须先发现 capability，再向服务端 **请求** 绑定；服务端校验 grant 后签发不透明 `session_id`。客户端不得把 `char_id` 塞进 legacy `/desktop/chat` 或 `/mobile/chat`：这两支今天把未知字段忽略，旧服务端会静默发给当时的 active。
4. **Capability / version**：`session_scope` = `"v1"`。只允许出现在会话发现投影，禁止混入 `GET /observability/deployment-capabilities`（那是部署工具策略）。
5. 发现入口是扩展 `GET /auth/whoami` 的可选 `capabilities` 对象。缺字段 = 旧服务端 / 未落地。客户端必须提示「当前服务端不支持固定会话角色」，不得静默改走 active。
6. 角色不可用、已删除、撤权、会话过期：返回下列固定错误码，不 fallback 到 active，不换一个可见角色继续发。

发现投影：

```json
{
  "label": "<token-label>",
  "scopes": ["chat", "state.read", "memory.read"],
  "capabilities": {
    "session_scope": "v1"
  }
}
```

无 `capabilities` 或无 `session_scope` 都视为不支持。不要把 `scopes` 里出现 `chat` 当成会话能力。

绑定：

```http
POST /v1/sessions
Authorization: Bearer <desktop-or-mobile-token>
```

```json
{ "char_id": "<requested-character-id>" }
```

成功：`201`/`200`，body 含服务端签发的 `session_id`、冻结后的 `owner_id`/`char_id`/`domain`、可选 `expires_at`。
`char_id` 在这里是 **请求**，不是权威；权威是返回的 session 与服务端 grant。
后续写/读以 `X-Presence-Session` header 带 `session_id`。缺失 session 返回 422，不使用 active。

chat / 上传 / wake / 历史 / reasoning / 媒体使用同一冻结会话；实现不修改 active。

---

## 3. 请求开始冻结（B3）

当前在请求入口冻结、并贯穿该请求及其异步后处理：

| 字段 | 来源 | 说明 |
|---|---|---|
| `owner_id` | `scheduler.owner_id` | 禁止 body 覆盖 |
| `domain` | 由入口决定，见下表 | 客户端不能用 body 把 Reality 写成 Dream |
| `char_id` | 会话 grant / 冻结 session | 冻结后本请求内不变 |
| `request_id` | 客户端可选提交不透明 ID，否则服务端 mint | 只关联这一次尝试，不是记忆身份 |
| `session_id` | 服务端签发 | 当前相关入口强制要求；缺失即拒绝 |

### 3.1 domain 与内部 realm / MemoryScope

| 线域 `domain` | 内部 realm | `MemoryScope.domain` | 备注 |
|---|---|---|---|
| `reality`（缺省） | `reality` | `reality` | owner 对话、wake、主动开口 |
| `dream` | `dream` | `dream` | 需要 `world_id`；Dream **设置树** 归 F，不在本单决定 |
| `group` | 视 Stage：普通群聊 `reality`，群梦 `dream` | 对应 scope | 线域 `group` **不是** MemoryScope.domain；关联键是 `round_id` |

`EventContext` 今天只允许 reality scope。群聊/群梦继续用现有 Stage/Dream runtime，不把它们塞进 Reality EventContext。

### 3.2 早期 stream 与 canonical turn_id 绑定顺序

当前桌面流式顺序（事实）：

1. 冻 memory scope 与 EventContext（尚无 turn_id）。
2. mint `_stream_msg_id`，`message_stream_start`（session 请求带 `char_id` / `domain` / `request_id`）。
3. `delta` / `end` 复用同一 `msg_id`。
4. critical 落盘后得到 persisted `turn_id`（可空）。
5. `channel_message` + 可选 `message_segments` 使用同一 `msg_id`，此时带 `char_id`。
6. HTTP 返回 `turn_id` 与 `msg_id`（流式路径 `msg_id` = `_stream_msg_id`）。

session-scope 加法字段（旧客户端可忽略）：

- `message_stream_start` 即携带冻结 `char_id` 与 `request_id`（或等价 correlator）；允许无 `turn_id`。
- 落盘后用 **同一** `msg_id` 的 canonical `channel_message` 收敛。客户端把早期临时气泡绑到该 `msg_id`，不得用后来的 `turn_id` 去认领别人的流。
- 若需显式 bind 事件，必须与桌面仓同步升级；在此之前 canonical `channel_message` 就是绑定。
- 迟到、重复、乱序、重连、Dream/Activity/group 并发：只认自己的 `msg_id` + 冻结 `char_id`/`round_id`/`domain`。不得用正文、时间戳或 seq 认领。
- `group_round_start/end` 的 `round_id` 是群轮次边界，不是 `request_id`，也不是 `turn_id`。

---

## 4. ID 分类与幂等（B4）

不承诺 exactly-once。

| 名称 | 作用 | 谁生成 | 持久化 | 当前状态 |
|---|---|---|---|---|
| `request_id` | 一次请求的关联；桌面 `dc-...` 由 `X-Presence-Chat-Request` 传递 | 客户端或服务端 | 通用 Session 路径为进程内 30 分钟；桌面正式请求复用持久 owner-turn receipt | 已实现；不是 persisted turn_id |
| `session_id` | 已授权的冻结会话 | 仅服务端 | 进程内 24 小时 session | **已上线** |
| `client_turn_id` | owner-turn 幂等键 | 调用方，按 caller label 隔离 | receipt 30 天 / 每 caller 1000 | **已上线**，仅 `/v1/owner/turns` |
| `msg_id` | 传输 correlator（HTTP/WS/queue `id`） | 服务端；流式可预 mint | 不作为记忆身份 | **已上线** |
| `turn_id` | persisted assistant 身份 | 服务端 critical 落盘 | event_log / short_term footer | **已上线**；可空，禁止用 `msg_id` 填 |
| `seq` | 手机 durable 队列游标 | 服务端单调 | `mobile_queue` + `mobile_queue_seq` | **已上线**，origin+owner 共享 |
| `round_id` | 群轮次 | 服务端 | Stage | **已上线** |
| `sha256` | 媒体内容身份 | 内容哈希 | 媒体桶按 owner+char | **已上线**；读权另论 |

### 4.1 不得混用

- `request_id` ≠ `msg_id` ≠ `turn_id` ≠ `client_turn_id` ≠ `seq` ≠ `round_id`。
- 思考归档、历史入口只认 persisted `turn_id`。无 footer 的旧日志不补造。
- 传输 `msg_id` 不得填充缺失的 canonical `turn_id`。
- owner-turn 的幂等窗口与语义不自动套到 legacy `/desktop/chat` `/mobile/chat`。

### 4.2 当前 session request 重试与错误

| 情况 | 拟议行为 | 错误码 / HTTP |
|---|---|---|
| 同一 `request_id` + 同一规范化 payload 重试，结果仍未知 | 不新开一轮；返回进行中或 unknown | `202` `in_flight` 或 `503` `execution_outcome_unknown` |
| 同一 `request_id` 不同 payload | 拒绝，不覆盖 | `409` `request_payload_conflict` |
| 并发双飞同一 `request_id` | 一者执行，另一者 `in_flight` | `202` |
| 超时 / 断线 | 客户端用同一 ID 查询或重试；不得换 ID 当新回合 | 无新码 |
| 已完成且 retained | 投影同一 `turn_id`/`reply` | `200` |
| 进程中断或失败后无法证明副作用 | 停止自动重试 | `503` `execution_outcome_unknown` |
| 相同上传 request 重试 | 复用 receipt，不产生第二个 owner turn | `200` retained result |
| 旧服务端无 session_scope | 客户端禁止发 `session_id`/`char_id` | 客户端本地 `session_scope_unsupported`（旧服务端不会发此码） |
| 角色不存在 / 文件缺失 | 拒绝 | `404`/`422` `character_unavailable` |
| 角色已从 grant 撤销 | 拒绝 | `403` `character_revoked` |
| 会话过期或未知（包括后端重启） | 拒绝，客户端显示会话不可用且不自动重绑重发 | `404` `session_not_found` |
| 请求角色无授权 | 拒绝 | `403` `character_not_authorized` |
| 缺少 Session Header | ingress 处中止，不检查 active 来授权 | `422` `session_scope_required` |

`/v1/owner/turns` 保持现有：同 ID 同 payload 幂等、冲突 409、in_flight 202、expired 410、unknown 503 `execution_outcome_unknown`、`upload_id_not_available`。不要把 `client_turn_id` 改名为 `request_id`。

附件：`/upload/ingest` 是上传并发送的一阶段入口，使用 session 与 request receipt；没有另行签发
`upload_id`。`/v1/owner/turns` 仍不接受真实 upload，非空 `upload_ids` 保持原有 409。

---

## 5. 主动消息与 queue seq（B5）

当前事实：

- 主动开口走 turn_sink → `registry.broadcast`；手机写入 **一条** origin+owner 队列。
- `char_id` 是信封上的发言人，不是游标维度。
- `poll(after=seq)` 返回 `seq > after` 的下一段；`ack(ack_seq)` 删除 `seq <= ack_seq` 的全部项。
- activate/deactivate 是通道在线，不是角色订阅。
- relay 不含 `char_id`；Android 必须回源 poll。接收设备 = 正在 poll/ack 的那台手机，后端不选择设备。

共享 cursor 规则（当前）：

1. 主动消息所属角色 = 生成该 turn 时冻结的 `char_id`。切换 live active 不得改写已入队信封。
2. `seq` 作用域 = 该 owner 的 mobile 通道队列，全角色共用。
3. 客户端可以按 `char_id` **显示过滤**，但必须先持久化/去重看到的每一条，再 ack。
4. **禁止**：跳过其他角色的消息却把 `ack_seq` 推进到它们之后。那样后端会删掉未展示的消息，造成丢失。
5. 若某设备暂时不能展示角色 B，应保持 B 的 seq 未 ack，或仍持久化后再 ack；不能“过滤即消费”。
6. 前台 poll 与后台补偿 poll 共用 `lastAckedSeq` / `seenMobileMessageIds`。
7. 桌面 WS 没有这套 seq；桌面重连用 `msg_id` 去重，不用 mobile cursor。

---

## 6. 字段表

| 字段 | 出现位置 | 必填 | 语义 | 状态 |
|---|---|---|---|---|
| `capabilities.session_scope` | whoami 发现 | 无则视为不支持 | `"v1"` | 已上线 |
| `session_id` | 绑定响应；后续请求 header | scoped 请求必填 | 服务端不透明句柄 | 已上线 |
| `request_id` | 请求与响应/早期 stream | scoped chat/upload 可选 | 单次尝试 correlator | 已上线 |
| `owner_id` | 绑定响应；不进客户端请求权威 | 响应有 | 进程 owner | 响应可回显，请求不可覆盖 |
| `char_id` | 绑定请求（请求而非权威）；下行信封 | 绑定请求要 | 冻结角色 | legacy chat **禁止**当权威 |
| `domain` | 下行 WS/绑定响应 | 缺省 reality | 线域 | 不把 group 写成 MemoryScope |
| `msg_id` | HTTP/WS/queue | 传输要有 | 传输 correlator | 已上线 |
| `turn_id` | HTTP/历史/思考 | 可空 | persisted 身份 | 已上线 |
| `client_turn_id` | owner-turn | 该 API 必填 | 幂等键 | 已上线，范围不扩大 |
| `seq` / `ack_seq` | mobile poll/ack | 队列要 | 共享游标 | 已上线 |
| `round_id` | 群 WS | 群轮次要 | 群边界 | 已上线 |

客户端禁止作为权威提交：`uid`、`origin`、`source`、`trust`、`tool_categories`、`token`、本机路径。与现有 owner-turn / security fixture 一致。

---

## 7. 当前兼容矩阵

| 客户端 | 无 `session_scope` 的旧后端 | 当前 T4 后端 |
|---|---|---|
| 未升级的无 Session 桌面 / 手机 | 历史行为：跟随 live active | 相关入口 422 拒绝，必须协调升级 |
| 已接入 Session 的桌面 / 手机 | 发现失败 → 明确停止发送，禁止静默发给 active | 先 `POST /v1/sessions`，后续以 Header 携带 grant |
| owner-input 适配器 | 保持禁止 body `char_id`；角色仍为当时 active | 若 C 给 owner-turn 接 session，必须另开字段/header 与 grant，不能把 `client_turn_id` 当 session |

分类：T4 是 `consumer-update-required`，已经切断早期加法兼容路径；不能滚动到只升级后端的组合。

手机 22 号工单依赖：发现、错误码、`request_id`、共享 seq 规则、本 fixtures。桌面沿原会话交接接入，本会话不改桌面仓。

---

## 8. 当前错误码汇总

| 码 | HTTP 建议 | 新/旧 | 含义 |
|---|---|---|---|
| `session_scope_unsupported` | 客户端本地 | 拟议 | 发现失败，不要发 |
| `character_not_authorized` | 403 | 新 | session 不属于当前 token |
| `character_revoked` | 403 | 新 | owner grant 已变化 |
| `character_unavailable` | 404/422 | 新 | 删除或无法加载 |
| `session_not_found` | 404 | 新 | 未知或过期 session |
| `request_payload_conflict` | 409 | 新 | 同 request_id 不同负载 |
| `session_scope_required` | 422 | T4 | 未携带 Session Header，入业务前拒绝 |
| `in_flight` | 202 | 已有 owner-turn | 同幂等键执行中 |
| `client_turn_id payload conflict` | 409 | 已有 | owner-turn 负载冲突 |
| `upload_id_not_available` | 409 | 已有 | owner-turn 附件未落地 |
| `completed_result_expired` | 410 | 已有 | 副作用已发生 |
| `execution_outcome_unknown` | 503 | 已有 | 中断未知 |
| `wake_scope_unavailable` | 200 + `source` | 已有 Path B | 无 char 不入队 |

错误 `detail` 只含固定码或通用提示，不含 token、正文、Prompt、路径。

---

## 9. C 落地结果

1. 复用 `core/owner_turn_service.py` 与已有 frozen scope，不另造全局角色切换锁。
2. 入口验证 session/grant 后冻结，贯穿 pipeline、工具、模型路由、turn_sink、媒体引用、slow post_process。
3. 历史 / reasoning / 媒体：授权的同一 owner/char；持有 `turn_id`/`sha256` 不够。
4. 新增 receipt/队列/trace 配只读观测；优先管理面，无正文无凭据；展示 capability/effective state 与拒绝原因。
5. 观测不得复用 deployment-capabilities 语义。
6. provider fixture 已进入 v1；固定 SHA matrix 只在三仓精确组合实际测试后更新，设备验收另外记录。

---

## 10. 两端接入依赖

| 仓 | 依赖本文之后才能做的 | 本会话 |
|---|---|---|
| Emerald-presence C | 实现发现、session、冻结贯穿、错误码、观测 | backend current |
| PresenceKit-desktop | 发现失败提示；stream 早期绑定；历史/思考/媒体走同一会话 | PR #14 已实现，完整 UI 验收 open |
| Emerald-mobile | 发现、错误码、request_id、poll/ack 不得按角色跳 seq、附件与转写 scope | 配套已实现，完整真机验收 open |

F（Dream settings）、E（specialized LLM 路由）、G/H/I/J 不在本合同决定。
