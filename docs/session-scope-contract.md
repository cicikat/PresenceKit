# 固定会话 scope 契约（拟议，未上线）

状态：`proposed-not-shipped`。日期：2026-09-17。施工单 B。
本文是给 C 与桌面/手机接入评审的合同，不是已发布协议，也不是 OpenAPI 现状。
落定前不得把下列字段、错误码、capability 或端点写成 current。
权威仍是运行中的 `/openapi.json`、[api-reference.md](api-reference.md) 与现有 v0.1 通道实现。

Dream settings 归属走工单 F，本文只映射 domain 名称，不决定 Dream 配置树。
桌面 wire 正文仍在 PresenceKit-desktop `docs/protocol-v0.md`；本仓不复制、不单边扩展 v0.1 消息全集。
手机通道正文仍在 Emerald-mobile `docs/protocols/mobile-channel.md`。本工作区无手机/桌面仓时，两端接入依赖只列在本文和接口总账。

现有 `tests/protocol_fixtures/v1/` 是 owner-turn / 桌面 HTTP-WS / 手机 poll-ack 的 **已冻结** 合同。
本拟议合同的机器可读副本在 `tests/protocol_fixtures/session-scope-proposed/`，不进入 `.github/protocol-matrix.json`。

---

## 1. 当前事实（B1 端点矩阵）

下表描述 HEAD 实现，不是拟议字段。scope 来源以 router / pipeline 代码为准。

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

Owner 来自进程配置 `scheduler.owner_id`。角色来自 live `active_character`（`active_prompt_assets.json` / `pipeline._active_character_id`）。
客户端 body 不能指定 owner。legacy chat 也不读 body `char_id`。可见列表 `GET /characters` 只是 persona 可读资产。

### 2.2 拟议（未上线）

1. **Owner** 仍只由进程配置解析，调用方不能覆盖。
2. **角色授权** 是服务端 grant，不是客户端本地选中，也不是 `GET /characters` 的可见列表。
3. 客户端若要固定会话角色，必须先发现 capability，再向服务端 **请求** 绑定；服务端校验 grant 后签发不透明 `session_id`。客户端不得把 `char_id` 塞进 legacy `/desktop/chat` 或 `/mobile/chat`：这两支今天把未知字段忽略，旧服务端会静默发给当时的 active。
4. **Capability / version**：`session_scope` = `"v1"`。只允许出现在会话发现投影，禁止混入 `GET /observability/deployment-capabilities`（那是部署工具策略）。
5. 发现入口拟议为扩展 `GET /auth/whoami` 的可选 `capabilities` 对象。缺字段 = 旧服务端 / 未落地。客户端必须提示「当前服务端不支持固定会话角色」，不得静默改走 active。
6. 角色不可用、已删除、撤权、会话过期：返回下列固定错误码，不 fallback 到 active，不换一个可见角色继续发。

拟议发现投影（未上线）：

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

拟议绑定（未上线，C 实现时再进 OpenAPI）：

```http
POST /v1/sessions
Authorization: Bearer <desktop-or-mobile-token>
```

```json
{ "char_id": "<requested-character-id>" }
```

成功：`201`/`200`，body 含服务端签发的 `session_id`、冻结后的 `owner_id`/`char_id`/`domain`、可选 `expires_at`。
`char_id` 在这里是 **请求**，不是权威；权威是返回的 session 与服务端 grant。
后续 versioned 写/读带 `session_id`（推荐 header `X-Presence-Session`，C 落定时与 OpenAPI 对齐）。缺失 session 的 legacy 路由保持今天的 active 语义。

C 落实时：chat / 上传 / wake / 历史 / reasoning / 媒体必须使用同一冻结会话；禁止靠改 active 实现隔离。

---

## 3. 请求开始冻结（B3）

拟议在请求入口冻结、并贯穿该请求及其异步后处理（未上线）：

| 字段 | 来源 | 说明 |
|---|---|---|
| `owner_id` | `scheduler.owner_id` | 禁止 body 覆盖 |
| `domain` | 由入口决定，见下表 | 客户端不能用 body 把 Reality 写成 Dream |
| `char_id` | 会话 grant / 冻结 session；legacy 无 session 时仍为当时 active | 冻结后本请求内不变 |
| `request_id` | 客户端可选提交不透明 ID，否则服务端 mint | 只关联这一次尝试，不是记忆身份 |
| `session_id` | 服务端签发 | 有则覆盖 live active；无则 legacy |

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
2. mint `_stream_msg_id`，`message_stream_start`（当前实现可不带 `char_id`）。
3. `delta` / `end` 复用同一 `msg_id`。
4. critical 落盘后得到 persisted `turn_id`（可空）。
5. `channel_message` + 可选 `message_segments` 使用同一 `msg_id`，此时带 `char_id`。
6. HTTP 返回 `turn_id` 与 `msg_id`（流式路径 `msg_id` = `_stream_msg_id`）。

拟议补充（未上线，不改 v0.1 消息全集直到桌面仓同步）：

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
| `request_id` | 一次 HTTP/WS 尝试的关联 | 客户端或服务端 | 可选观测；不进 event_log 身份 | **拟议** |
| `session_id` | 已授权的冻结会话 | 仅服务端 | 短 TTL 会话记录 | **拟议** |
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

### 4.2 拟议重试与错误（legacy chat 在 C 落地前仍无幂等）

| 情况 | 拟议行为 | 错误码 / HTTP |
|---|---|---|
| 同一 `request_id` + 同一规范化 payload 重试，结果仍未知 | 不新开一轮；返回进行中或 unknown | `202` `in_flight` 或 `503` `execution_outcome_unknown` |
| 同一 `request_id` 不同 payload | 拒绝，不覆盖 | `409` `request_payload_conflict` |
| 并发双飞同一 `request_id` | 一者执行，另一者 `in_flight` | `202` |
| 超时 / 断线 | 客户端用同一 ID 查询或重试；不得换 ID 当新回合 | 无新码 |
| 已完成且 retained | 投影同一 `turn_id`/`reply` | `200` |
| 副作用已发生但正文过期 | 不重跑 | `410` `completed_result_expired` |
| 进程中断无法证明副作用 | 停止自动重试 | `503` `execution_outcome_unknown` |
| 附件 `upload_id` 与会话 `char_id` 不一致 | 拒绝 | `409` `upload_scope_mismatch` |
| 重复提交同一附件字节到同一会话 | 可复用同一 `upload_id`；不双写副作用 | `200` 已有引用 |
| 旧服务端无 session_scope | 客户端禁止发 `session_id`/`char_id` | 客户端本地 `session_scope_unsupported`（旧服务端不会发此码） |
| 角色不存在 / 文件缺失 | 拒绝 | `404`/`422` `character_unavailable` |
| 角色已从 grant 撤销 | 拒绝 | `403` `character_revoked` |
| 会话过期或未知 | 拒绝 | `409`/`404` `session_not_found` |
| 请求角色无授权 | 拒绝 | `403` `character_not_authorized` |
| live active 损坏且无 session | 与今天一致中止 | `503` `active_character_unusable` |

`/v1/owner/turns` 保持现有：同 ID 同 payload 幂等、冲突 409、in_flight 202、expired 410、unknown 503 `execution_outcome_unknown`、`upload_id_not_available`。不要把 `client_turn_id` 改名为 `request_id`。

附件：C 落地前 owner-turn 仍不接受真实 upload。拟议 ingest 在冻结会话下签发 opaque `upload_id`，后续发送必须同一 `session_id`。

---

## 5. 主动消息与 queue seq（B5）

当前事实：

- 主动开口走 turn_sink → `registry.broadcast`；手机写入 **一条** origin+owner 队列。
- `char_id` 是信封上的发言人，不是游标维度。
- `poll(after=seq)` 返回 `seq > after` 的下一段；`ack(ack_seq)` 删除 `seq <= ack_seq` 的全部项。
- activate/deactivate 是通道在线，不是角色订阅。
- relay 不含 `char_id`；Android 必须回源 poll。接收设备 = 正在 poll/ack 的那台手机，后端不选择设备。

拟议规则（未上线，但 **现在** 客户端就不得违反共享 cursor）：

1. 主动消息所属角色 = 生成该 turn 时冻结的 `char_id`。切换 live active 不得改写已入队信封。
2. `seq` 作用域 = 该 owner 的 mobile 通道队列，全角色共用。
3. 客户端可以按 `char_id` **显示过滤**，但必须先持久化/去重看到的每一条，再 ack。
4. **禁止**：跳过其他角色的消息却把 `ack_seq` 推进到它们之后。那样后端会删掉未展示的消息，造成丢失。
5. 若某设备暂时不能展示角色 B，应保持 B 的 seq 未 ack，或仍持久化后再 ack；不能“过滤即消费”。
6. 前台 poll 与后台补偿 poll 共用 `lastAckedSeq` / `seenMobileMessageIds`。
7. 桌面 WS 没有这套 seq；桌面重连用 `msg_id` 去重，不用 mobile cursor。

---

## 6. 拟议字段表（未上线）

| 字段 | 出现位置 | 必填 | 语义 | 落定前 |
|---|---|---|---|---|
| `capabilities.session_scope` | whoami 发现 | 无则视为不支持 | `"v1"` | 未上线 |
| `session_id` | 绑定响应；后续请求 header/字段 | versioned 路径拟议必填 | 服务端不透明句柄 | 未上线 |
| `request_id` | 请求与响应/早期 stream | 可选 | 单次尝试 correlator | 未上线 |
| `owner_id` | 绑定响应；不进客户端请求权威 | 响应有 | 进程 owner | 响应可回显，请求不可覆盖 |
| `char_id` | 绑定请求（请求而非权威）；下行信封 | 绑定请求要 | 冻结角色 | legacy chat **禁止**当权威 |
| `domain` | 下行 WS/绑定响应 | 缺省 reality | 线域 | 不把 group 写成 MemoryScope |
| `msg_id` | HTTP/WS/queue | 传输要有 | 传输 correlator | 已上线 |
| `turn_id` | HTTP/历史/思考 | 可空 | persisted 身份 | 已上线 |
| `client_turn_id` | owner-turn | 该 API 必填 | 幂等键 | 已上线，范围不扩大 |
| `seq` / `ack_seq` | mobile poll/ack | 队列要 | 共享游标 | 已上线 |
| `round_id` | 群 WS | 群轮次要 | 群边界 | 已上线 |
| `upload_id` | ingest 响应 / 后续发送 | 有附件时 | 绑定到冻结会话的附件句柄 | 拟议；今日 owner-turn 非空即 409 |

客户端禁止作为权威提交：`uid`、`origin`、`source`、`trust`、`tool_categories`、`token`、本机路径。与现有 owner-turn / security fixture 一致。

---

## 7. 兼容矩阵

| 客户端 | 无 `session_scope` 的后端（含今日 HEAD） | 已广告 `session_scope=v1` 的后端 |
|---|---|---|
| 今日桌面 / 手机 | 继续 legacy：对话走 live active；上传/媒体/wake/历史省略 char 时跟 active。**不要**往 `/desktop/chat` `/mobile/chat` 塞 `char_id` | 未升级客户端仍走 legacy 缺省，行为与今日相同 |
| 升级后桌面 / 手机 | 发现失败 → 明确提示不支持固定会话；用户本地选了非 active 角色时 **禁止发送**，禁止静默发给 active | 先 `POST /v1/sessions`，再带 `session_id` 走 versioned 写/读 |
| owner-input 适配器 | 保持禁止 body `char_id`；角色仍为当时 active | 若 C 给 owner-turn 接 session，必须另开字段/header 与 grant，不能把 `client_turn_id` 当 session |

分类：对未升级客户端是 `backward-compatible`；对要固定角色的新客户端是 `consumer-update-required`。
本拟议 **不是** 现在的 breaking wire 变更，因为 C 落地前不会广告 capability。

手机 22 号工单依赖：发现、错误码、`request_id`、共享 seq 规则、本 fixtures。桌面沿原会话交接接入，本会话不改桌面仓。

---

## 8. 错误码汇总（拟议 + 已有）

| 码 | HTTP 建议 | 新/旧 | 含义 |
|---|---|---|---|
| `session_scope_unsupported` | 客户端本地 | 拟议 | 发现失败，不要发 |
| `character_not_authorized` | 403 | 拟议 | 无 grant |
| `character_revoked` | 403 | 拟议 | 曾授权已撤 |
| `character_unavailable` | 404/422 | 拟议 | 删除或无法加载 |
| `session_not_found` | 404/409 | 拟议 | 未知或过期 session |
| `request_payload_conflict` | 409 | 拟议 | 同 request_id 不同负载 |
| `upload_scope_mismatch` | 409 | 拟议 | 附件会话与发送会话不同 |
| `active_character_unusable` | 503 | 拟议名；今日 detail 为中文「active character 状态异常」 | 无 session 且 active 坏 |
| `in_flight` | 202 | 已有 owner-turn | 同幂等键执行中 |
| `client_turn_id payload conflict` | 409 | 已有 | owner-turn 负载冲突 |
| `upload_id_not_available` | 409 | 已有 | owner-turn 附件未落地 |
| `completed_result_expired` | 410 | 已有 | 副作用已发生 |
| `execution_outcome_unknown` | 503 | 已有 | 中断未知 |
| `wake_scope_unavailable` | 200 + `source` | 已有 Path B | 无 char 不入队 |

错误 `detail` 只含固定码或通用提示，不含 token、正文、Prompt、路径。

---

## 9. C 落地约束（本单不实现）

1. 复用 `core/owner_turn_service.py` 与已有 frozen scope，不另造全局角色切换锁。
2. 入口验证 session/grant 后冻结，贯穿 pipeline、工具、模型路由、turn_sink、媒体引用、slow post_process。
3. 历史 / reasoning / 媒体：授权的同一 owner/char；持有 `turn_id`/`sha256` 不够。
4. 新增 receipt/队列/trace 配只读观测；优先管理面，无正文无凭据；展示 capability/effective state 与拒绝原因。
5. 观测不得复用 deployment-capabilities 语义。
6. fixtures 在 C 广告 capability 之前保持 `proposed-not-shipped`；广告后才可迁入 v1 协议包并更新 protocol-matrix。

---

## 10. 两端接入依赖

| 仓 | 依赖本文之后才能做的 | 本会话 |
|---|---|---|
| Emerald-presence C | 实现发现、session、冻结贯穿、错误码、观测 | 未开始 |
| PresenceKit-desktop | 发现失败提示；禁止 legacy body `char_id`；stream 早期绑定；历史/思考/媒体走同一会话 | **不改代码** |
| Emerald-mobile 22 | 发现、错误码、request_id、poll/ack 不得按角色跳 seq、附件 scope | 本单不修改手机仓，只列依赖 |

F（Dream settings）、E（specialized LLM 路由）、G/H/I/J 不在本合同决定。
