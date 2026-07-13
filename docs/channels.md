# docs/channels.md — 通道与桌宠通信

---

## 协议权威

桌面端当前正式协议为 **v0.1（legacy 冻结版）**。本仓只维护实现说明，协议消息全集、ack/nack 语义、9 类 desktop action allowlist 与 HTTP/WS 对账契约统一从 [desktop-client-protocol.md](desktop-client-protocol.md) 跳转到 PresenceKit-desktop 权威正文。v1 未排期，双方均未实现；不得在本仓单边增加消息类型或 desktop action。

实现真值：`channels/desktop_ws.py`（帧与心跳）、`admin/admin_server.py`（Bearer WS 鉴权）、`admin/routers/chat.py`（`POST /desktop/chat`）。

## 定位

通道层只负责**把已经生成好的回复送到用户能看到的地方**。QQ、桌宠、调度器广播共用同一个 `Pipeline`，区别只在入口和发送方式。

桌宠功能已并入新客户端，"desktop" channel 名义保留，实际承载新客户端。QQ 桌宠本体已废弃。

```
QQ 收消息 → main.handle_message → Pipeline → text_output.send() 直发 QQ
桌宠/手机发消息 → POST /desktop/chat → Pipeline → HTTP 返回 reply + turn_sink fanout 到其他活跃端
调度器主动消息 → scheduler._pipeline_send → turn_sink → channels.registry.broadcast()
                                          ├─ DesktopChannel
                                          ├─ MobileChannel
                                          └─ DeviceChannel
```

桌宠（`/ws/desktop`）和设备（`/ws/device`，ESP32 等具身硬件）是两条独立的 WS 单例，互不踢线、可同时在线。
交互式流式/分段推送（`message_stream_*` / `message_segments`）不走 `registry.broadcast()`，
由 `channels/ui_push.py` 直接 fan 到所有已连的 UI 客户端（桌宠 + 设备），见下方「设备 WebSocket」一节。

注意：QQ 主入口的可见发送由 `_qq_reality_reply_adapter` 调用 `text_output.send()`，LLM reply
记忆写入统一走 `core.turn_sink.record_assistant_turn()`；冻结管理面板 `/chat` 已返回 410。
`/desktop/chat`、scheduler、sensor_aware 同样走 turn sink。legacy
`/desktop/trigger` 已确认零调用方并删除。手机端发消息也调用 `/desktop/chat`
（`channel_name="desktop"` 硬编码），`POST /mobile/chat` 端点未被三端任一实际调用，
已作为 legacy 删除（cc-tasks round-接口盘点，2026-07-11）；手机侧的独立收发路径仅剩
`/mobile/poll` `/mobile/ack` `/mobile/push` `/mobile/activate` `/mobile/deactivate`。

---

## 输出通道

| 通道 | 文件 | 激活方式 | 发送方式 |
|---|---|---|---|
| QQ | `channels/qq.py` | `standalone_mode=false` 且 `qq.enabled=true` 时由 `main.py` 注册 | `core/qq_adapter.send_message()` → NapCat |
| 桌宠 | `channels/desktop.py` | 总是注册；WS 连接或 `set_active(True)` 后活跃 | 主动下行优先 WebSocket，失败降级到 `data/runtime/channel_queue.json` |
| 手机 | `channels/mobile.py` | 总是注册；`POST /mobile/activate` 或 `GET /mobile/poll` 后短时活跃 | 写入 `data/runtime/mobile_queue.json`，手机端轮询读取 |
| 设备 | `channels/device.py` | 总是注册；仅 `/ws/device` 连接后活跃 | 只走 WebSocket（MVP 无文件降级），WS 未连接时 `send()` 直接返回 |

`channels/registry.py` 维护通道注册表：
- `register(channel)`：启动时注册通道。
- `get_active()`：返回 `is_active=True` 的通道。
- `broadcast(content, user_id, behavior=None, *, char_id=None)`：向所有活跃通道广播；`behavior` 会透传给支持动作包的通道，QQ 通道忽略它；`char_id` 是可选发言人字段。

---

## 手机端轮询通道

文件：`channels/mobile.py`

接口由管理面板服务提供：

| 接口 | 用途 |
|---|---|
| `POST /mobile/activate` | 手机端上线，激活 mobile channel |
| `POST /mobile/deactivate` | 手机端下线，停用 mobile channel |
| `GET /mobile/poll?after=<seq>&limit=20&wait=55` | 非销毁拉取 `seq > after` 的最多 20 条手机主动消息；响应含 `cursor`；`wait` 可选，0-60 秒，用于后台长轮询 |
| `POST /mobile/ack` | 传入 `{ack_seq}`，删除 `seq <= ack_seq` 的已持久化消息 |
| `POST /mobile/push` | 后端工具/调试入口：通过 `MobileChannel.send()` 写入一条主动消息 |

上述接口使用管理面板 Bearer token。手机端当前不连接 `/ws/desktop`，因此不会抢占桌宠 WebSocket。
`POST /mobile/push` 可选接收 `char_id`，写入主动消息信封供新客户端渲染发言人。

MobileChannel 的活跃状态有 120 秒 TTL：手机端持续轮询时保持活跃；停止轮询后，调度器广播不会再写入手机队列。

主动消息队列采用单调 `seq` 游标并保留到 `/mobile/ack`；未 ack 项最多保留 500 条或 24 小时，避免离线客户端令队列无限增长。
配置 `relay_base_url` / `relay_topic` / `relay_token` 后，每条主动消息写盘成功会异步发布一条
signal-only 中继唤醒（仅含 `id` / `seq` / `user_id` / `timestamp` / `signal`）；正文和
`behavior` 只保留在 `/mobile/poll` 队列。三项任一缺失时静默跳过中继发布。

手机和桌宠共用同一个 `/desktop/chat` 入口（手机端目前没有独立的 chat 端点），
经 `core/conversation_gate.py` 的 per-user 锁：同一用户的并发请求不会并行进入
`fetch_context → LLM → post_process_critical`（Brief 37：只锁到落盘的关键段；`post_process_slow` 里的
`detect_emotion` / mood_state 更新是 send 后异步 `asyncio.create_task` 出去的，不占这把锁，
下一条消息不需要等它跑完）。
本端 reply 通过 HTTP response 返回；`record_assistant_turn(fanout="all", exclude_origin_channel=...)`
会把同一回复同步到其他活跃端，避免本端重复收到一份队列消息。

HTTP assistant reply 保留 `turn_id`，并同时返回兼容字段 `msg_id`；两者相等。该 canonical ID
也用于同一 assistant turn 的 WS `channel_message.msg_id` / `message_segments.msg_id`。
`/desktop/wake` 在实际返回 assistant reply 时同样返回相等的 `turn_id` / `msg_id`。

---

## 桌宠控制端点（SEC-AUTH-1，2026-06-11 已收口）

以下端点均需 `Authorization: Bearer <YEXUAN_ADMIN_SECRET>` header，无 token 或 token 错误返回 401/403：

| 端点 | 影响 |
|---|---|
| `POST /desktop/activate` | 激活 desktop channel；鉴权失败不执行通道操作 |
| `POST /desktop/wake` | 触发 LLM 轮（Path B）并写记忆；鉴权失败不触发 LLM，不写记忆 |

`POST /desktop/deactivate` 已作为 legacy 删除（下线由 `/ws/desktop` 断连触发
`set_active(False)` 处理，HTTP 版从未被调用；cc-tasks round-接口盘点，2026-07-11）。

---

## 桌宠 WebSocket

文件：`channels/desktop_ws.py`

端点：`ws://127.0.0.1:8080/ws/desktop`

### 鉴权方式（R9 / SEC-WS-1 final，2026-06-11）

服务端鉴权集中在 `admin/auth.authenticate_ws()`，只接受安全的 header 鉴权：

| 方式 | Header / Param | 状态 |
|---|---|---|
| Authorization header | `Authorization: Bearer <secret>` | **唯一支持方式** |
| query param | `?token=<secret>` | **已移除；请求会被拒绝** |

- token 值在任何情况下均不出现在日志输出或错误响应中。
- uvicorn access log 的 `QuerySanitizeFilter`（`admin/log_filter.py`）仍就位，覆盖其他 query 参数泄漏风险。
- 失败（无 token / 错 token / 未配置 secret）时以 code `1008` 关闭连接。

**客户端迁移（PresenceKit-desktop）已完成**：客户端通过 Tauri Rust native bridge 在连接头中发送
`Authorization: Bearer <secret>`；服务端不再读取或接受 URL query token。

行为：
- 单连接：新桌宠连接会替换旧连接。
- 普通消息：`push_message()` 发送 `channel_message`，不等 ack。
- 叙事分段：`turn_sink` 在普通消息之后并行发送 `message_segments`，不等 ack。
- 桌面动作：`push_action_and_wait()` 发送 `action`，最多等 5 秒 ack。
- 梦境邀请：Path B 推送 `dream_invite` action；PresenceKit-desktop 收到并 ack 后打开 Dream 窗口。
- 玩耍邀请：Path B 推送 `toy_invite` action；PresenceKit-desktop 在「玩耍模式」开关开启时打开 ToyWindow，关闭时忽略并正常回 ack。
- 心跳：服务端每 20 秒发 `ping`，超过约 70 秒没有 `pong` 会断开。

桌宠上线时会把 `DesktopChannel` 设为活跃；断开时取消文件 fallback 活跃标志。

---

## 设备 WebSocket（ESP32 等具身硬件）

文件：`channels/device_ws.py`

端点：`ws://<后端局域网IP>:8080/ws/device`

独立于 `/ws/desktop` 的模块级单例（独立的 `_current_ws` / `_lock` / `_pending_acks` / `_last_pong` /
`_connect_time` / `_heartbeat_task`），设备和桌宠可同时在线、互不踢线。鉴权方式、心跳规则、
消息帧格式（`hello_ack` / `ping` / `pong` / `channel_message` / `message_segments` /
`message_stream_*` / `action` / `ack`）与 `/ws/desktop` **完全一致**，板子和 PC 客户端解析逻辑一致。

设备上线时会把 `DeviceChannel` 设为活跃；断开时设为不活跃（MVP 无文件 fallback，`DeviceChannel.send()`
在 WS 未连接时直接返回）。

### 出站队列 + 单 writer 任务（CC-18，设备通道解耦）

`device_ws` 的所有 `push_message` / `push_segments` / `push_stream_start/delta/end` 不再逐帧
`await send_text`，而是先 `enqueue_json()` 非阻塞入队，真正的网络发送收敛到连接建立时启动的
单个 `_writer_loop()` 协程。动机：ESP32 走 WiFi 且渲染时不读 socket，TCP 缓冲一满 `send_text`
就阻塞——旧实现下整条流会被最慢的客户端限速，桌宠端跟着变慢。

- 出站队列（`maxsize=64`）随连接建立创建、断开或被新连接顶替时清空并取消 writer。
- 队满时：新帧若与队尾同为 `message_stream_delta` 且 `msg_id` 相同，原地合并 delta 字符串（无损）；
  其余情况丢弃并 WARN——`stream_start/end`、`channel_message`、`segments` 不该在 64 深度下丢，
  真丢了说明设备端已经死了，心跳会在 70s 内踢掉。
- writer 对连续同 `msg_id` 的 delta 帧做 ~100ms 聚合再发送（单帧不超过 512 字节），设备屏幕
  不需要逐 token 刷新。
- `push_action_and_wait`（action + 等 ack）和心跳 ping/pong 不走队列，保持直发。
- 桌宠通道（`desktop_ws`）不受影响，仍是直接 `await send_text`（本机回环，快且保序习惯不变）。

### `channels/ui_push.py` — 交互式推送 fanout

`admin/routers/chat.py` 的流式推送（`message_stream_start/delta/end`）与 `core/turn_sink.py` 的
`message_segments` 推送不走 `channels.registry.broadcast()`，而是通过 `channels/ui_push.py` 直接
fan 到所有已连接的 UI 客户端（桌宠 + 设备）：

- `ui_push.any_connected()`：桌宠或设备任一在线即为 `True`，作为流式路径的启用 gate。
- `ui_push.push_stream_start/delta/end(msg_id, ...)`：向所有已连客户端广播流式帧。
- `ui_push.push_segments(...)`：`turn_sink` 内部按「该 channel 是否确实在本轮 fanout targets 内」
  精确门控，避免向从未收到过对应 `channel_message` 的客户端推送孤立的 `message_segments`
  （破坏单一展示路径的不变量）。

### 设备动作 fanout

`core/tool_dispatcher._push_desktop_action()` 会向所有已连接的 `desktop_ws` / `device_ws` 推送
动作并等 ack，**任一 ack 成功即返回 "ok"**（例如 `show_heart` 的实际执行方是设备）。全部离线或全部
ack 失败时降级到 `data/runtime/agent_actions.json` 文件队列。

---

## 桌宠流式输出协议（Spec #9，2026-06-13）

`/desktop/chat` 且 WS 已连接时，主生成回复通过 WS 流式推送；记忆写入和 scrub 依赖完整文本，不受影响。

```
HTTP /desktop/chat 触发 turn
        ↓ conversation_lock 内
  probe ⇉ fetch_context（CC-18：asyncio.gather 并行，互不依赖）→ build_prompt
        ↓
  run_llm_stream() ── 逐 token ──→ push_stream_delta()   ← 前端实时渲染
        ↓（流结束，拿到完整 reply）
  scrub / clean_reality_reply_text
        ↓
  record_assistant_turn(exclude_origin_channel="desktop")  ← 不通过 fanout 推 desktop
        ↓
  push_message(canonical, msg_id=_stream_msg_id)           ← 前端用干净版替换临时气泡
        ↓
  HTTP response 返回（msg_id=_stream_msg_id，与 WS 帧共享）← 3s fallback 计时器凭此 dedup
```

`run_owner_chat_turn` 每轮结束会打一行 `[owner_chat/timing]` INFO 日志（`probe` / `ctx` /
`prompt` / 流式分支的 `first_delta` + `stream` 或非流式分支的 `llm` / `chars` + `c/s` / `post` /
`total`），纯 logging 不改变行为，用于定位"感觉慢"是否有真实机制支撑。

### 流式帧类型

三种帧共享同一个 `msg_id`，前端凭此关联到同一条消息：

| 帧类型 | 方向 | 含义 |
|---|---|---|
| `message_stream_start` | server→client | 流开始，前端创建空临时气泡；同时停 loading 指示器 |
| `message_stream_delta` | server→client | 增量 token（`delta` 字段），前端追加到临时气泡文本 |
| `message_stream_end` | server→client | 流结束，前端关闭打字光标，等 canonical 替换 |
| `channel_message`（同 msg_id）| server→client | scrub 后干净版，前端用此内容替换临时气泡 |

**约束：**
- QQ / mobile 链路不走流式，只收完整 `channel_message`。
- Dream pipeline 不经 `run_owner_chat_turn`，不受影响。
- 工具探测（probe）本身不走流式，只有主生成那一步流式推送。
- WS 断流时，`run_llm_stream` 累积已产出的 token 作为 reply，`record_assistant_turn` 用完整文本。

### 前端 dedup 与 fallback

- HTTP response 的 `msg_id` 在流式路径下等于 `_stream_msg_id`（与 WS 帧一致）。
- 前端 3s fallback 计时器发现 `wsMsgIdToLocalIdsRef.has(msgId)` = true 时取消（canonical 已替换）。
- WS 流中途断开时，fallback 计时器检测到 `streamingLocalIdRef.has(msgId)` → 用 HTTP 全文替换临时气泡，避免双气泡。

### 一轮 assistant turn 的完整事件契约（CC-17）

跨 desktop / device / mobile / QQ 所有通道，一轮 assistant turn 在传输层遵守同一契约：

1. 一轮 turn = 可选的 `stream_start → stream_delta×N → stream_end`，之后**恰好一条**
   同 `msg_id` 的 `channel_message`（canonical，`strip_render_tags` 剥标签后的文本），
   之后**可选一条**同 `msg_id` 的 `message_segments`。
2. `channel_message` 是流式路径的 **finalizer**：客户端不得把它当成另一条独立消息叠加渲染
   （前端应按 `msg_id` 替换/关联同一气泡，而非追加新气泡）。`message_segments` 是可选增强，
   客户端不得依赖其存在——不认识该帧类型或帧未到达时都要能安全降级为只显示
   `channel_message` 的纯文本。
3. `stream_end` 与 `channel_message` 之间存在毫秒级间隔（Brief 37 之前曾达秒级——
   `detect_emotion` 一次 LLM 往返曾挡在这里，最长 8s 超时；Brief 37 后已挪到 send
   之后异步执行）：两者之间只会 `await pipeline.post_process_critical(...)`
   （`record_assistant_turn` 的关键段，只做本地落盘写短期/事件记忆），这是正常
   时序，不代表连接异常或消息丢失。`channel_message` 发出（send）之后，
   `record_assistant_turn` 才用 `asyncio.create_task` 调度
   `pipeline.post_process_slow(...)`（detect_emotion / mood_state / mid_term
   等），不阻塞任何通道的收发。
4. 触发器 / 主动路径（scheduler、sensor、`desktop_wake` Path B 等 `TurnSource.TRIGGER`）没有
   流式帧，直接发 `channel_message`（+ 可选 `message_segments`），前端应把它当作一条完整
   新消息追加。
5. 各路径实际发帧情况：

   | 路径 | 流式帧 | `channel_message` | `message_segments` |
   |---|---|---|---|
   | 聊天·流式（`/desktop/chat`，desktop 或 device 已连接） | 有（仅发往 desktop） | 有：`push_message()` 直发 desktop（不经 `exclude_origin_channel` 排除的 fanout） | 有（可选）：`push_message` 之后同 `msg_id` 直发 desktop；其余活跃通道仍走 `record_assistant_turn` 的常规 fanout，按「该通道是否在本轮 targets 内」判断是否收到 |
   | 聊天·非流式（mobile / QQ 入口，或 desktop 无 UI 连接） | 无 | 有：`record_assistant_turn` 常规 fanout 广播给除发起通道外的活跃通道；发起通道本身只通过 HTTP response 拿到 reply，不额外收 WS 帧 | 有（可选）：随常规 fanout 发给 targets 中的 desktop / device |
   | 触发器 / 主动路径（scheduler、sensor、`desktop_wake` Path B） | 无 | 有：`record_assistant_turn` 常规 fanout | 有（可选）：同上 |
   | `desktop_wake` Path A（历史 pending trigger turn，HTTP 直接返回） | 无 | 无——完全不经 WS，`reply`/`msg_id` 只出现在 HTTP response 里 | 无 |

---

## Narrative Message 双轨协议

`core/turn_sink.py` 通过 `core/narrative_parser.py` 把 LLM 回复解析为只读 segments 视图：

| segment type | 含义 |
|---|---|
| `say` | 台词 |
| `do` | 动作 |
| `env` | 环境 |
| `feel` | 感受 |
| `narration` | 未标记文本或容错降级 |

桌面在线时，原始 tagged 回复仍用于 desktop 双轨展示，同一个 `msg_id` 会收到两条消息：

```json
{"type":"channel_message","content":"你好","msg_id":"..."}
{"type":"message_segments","content":"你<hl>好</hl>","segments":[{"type":"say","text":"你<hl>好</hl>"}],"msg_id":"..."}
```

`channel_message.content` 已全剥标签（纯文本）；`message_segments.content` 保留段内样式标签供 PresenceKit-desktop 渲染。

PresenceKit-desktop 的 `ChatPanel` 按 `msg_id` 关联两条消息；`message_segments` 先到时会暂存。
旧客户端忽略未知 `message_segments` 即可继续工作。QQ / mobile 输出会移除所有展示
标签（含 `<hl>/<big>/<sm>`）；reality history / event_log 同样保存纯文本。

### 段内 Inline 样式标签（CC-06）

`INLINE_STYLE_TAGS = {"hl", "big", "sm"}` — 这三种标签由 `narrative_parser.py` 在 `segment.text` 里保留，
不作为段类型、不切分 segment；`content` 字段全剥（`_ALL_TAG_RE`）。

- `<hl>词</hl>` — 强调/重音（桌面渲染为主题色 + 粗体）
- `<big>词</big>` — 放大（`font-size: 1.18em`）
- `<sm>词</sm>` — 缩小（`font-size: 0.85em, opacity: 0.8`）

QQ / mobile / memory / hidden_state 路径无需任何修改 — `strip_render_tags` / `_ALL_TAG_RE`
作为通用正则已覆盖这三个标签的剥除。桌面 `inlineStyle.tsx` 的 `renderInlineStyled()` 负责解析渲染。

两种信封均可携带可选 `char_id` 发言人字段；旧客户端忽略未知字段即可。mobile 主动消息队列
和 desktop 文件降级队列同样按需写入 `char_id`，QQ 当前只接受参数、不改变文本渲染。

### 句级表演 spec（perform，Brief 20）

`message_segments.segments[]` 的 `say` 段可以额外携带可选 `perform` 键，驱动桌面端 3D/Live2D
逐句表演；整体缺失 = 无表演标注，字段为 `null` = 不覆盖该通道（客户端回落 mood 基调层）：

```jsonc
{
  "type": "say",
  "text": "才、才没有等你很久呢",
  "perform": {                  // 可选
    "expression": "happy",      // neutral|gentle|thinking|happy|sad|surprised|angry|sleepy|yandere | null
    "intensity": 0.7,           // 0~1，缺省 0.6
    "head": "tilt_r",           // nod|shake|tilt_l|tilt_r|dip|null
    "posture": "lean_in",       // lean_in|lean_back|shrink|straighten|null
    "gaze": "away",             // user|away|down|wander|null
    "energy": 0.4               // 0~1，缺省 0.5
  }
}
```

由 `core/perform_mapper.py`（`enrich_say_segments`）在 `build_say_segments()` 之后、`push_segments`
之前挂上；fail-open——任何内部异常/超时都原样返回未标注的 segments，绝不影响主流程。
模块职责、rules/llm 两个 provider、词典维护方式见 `docs/perform-mapping.md`。
perform 字段属于 v0.1 `message_segments` 契约；权威入口见 [desktop-client-protocol.md](desktop-client-protocol.md)，映射细节见 `docs/perform-mapping.md`。

---

## 文件降级

当 WebSocket 不在线或发送失败时：

| 文件 | 用途 | 写入方 | 读取方 |
|---|---|---|---|
| `data/runtime/channel_queue.json` | 普通消息队列 | `DesktopChannel._write_to_queue()` | 桌宠端轮询 |
| `data/runtime/mobile_queue.json` | 手机主动消息队列 | `MobileChannel._write_to_queue()` | 手机端 `/mobile/poll` |
| `data/runtime/agent_actions.json` | 桌面动作队列 | `tool_dispatcher._push_desktop_action()` / `DesktopChannel.send(..., behavior=...)` | 桌宠端轮询 |
| `data/runtime/pending_perception/` | 动作失败后的下轮感知 | `pipeline._parse_and_execute_intent()` | `pipeline.build_prompt()` |

所有上述路径都通过 `core/sandbox.get_paths()` 获取，测试模式会切到 `data/test_sandbox/{session}/`。

---

## 文件 / 图片上传

三端统一走 `POST /upload/ingest`。该接口需 Bearer token 鉴权（SEC-AUTH-1，2026-06-11 已收口）；
接口同时兼容旧单文件字段 `file`，以及新多文件字段 `files`：

| 参数 | 说明 |
|---|---|
| `file` | 单文件上传字段，向后兼容旧客户端 |
| `files` | 多文件上传字段，图片可多传 |
| `message` | 用户附言（可选，默认空） |
| `channel` | 来源通道标记（默认 `desktop`） |

- QQ 路径仍由 NapCat 推 `[CQ:file]` 触发，内部走同一个 `media_processor.ingest_file_bytes`
- QQ 图片路径仍由 NapCat 图片 URL 触发，内部走同一个 `media_processor.ingest_image_bytes`
- 文档落 `data/inbox/{ts}_{原文件名}`；图片新图落 `data/inbox/{ts}_{sha8}_{原文件名}`
- 文档支持类型：`.txt` / `.md` / `.docx`
- 图片支持类型：`.jpg` / `.jpeg` / `.png` / `.gif` / `.webp` / `.heic` / `.heif` / `.bmp`
- 文档只能单个上传；图片可以通过 `files` 多张上传；文档和图片不能混传
- 图片按原始 bytes 计算 sha256，描述缓存写入 `data/cache/image_cache/{sha256}.json`；命中缓存时不再落盘同一张图，也不再调用 vision
- 文档大小上限 5MB，图片大小上限 10MB（单张）；超出返回 413
- 不支持类型（`.pdf` / `.zip` / `.exe` 等）上传返回 415；QQ 文件路径返回"看不懂"提示
- 422 表示请求形态或处理失败，如空文件列表、文档多传、文档图片混传、图片识别失败、文件读取失败
- 空文档可正常处理，由 LLM 自然回应

---

## 跨通道接续

`Pipeline.build_prompt(..., channel="qq"|"desktop"|"mobile")` 会记录上一轮通道：
- 如果本轮通道和上轮不同，会在层 1 的 `perception_block` 注入一句接续提示。
- 这只影响本轮 prompt，不写入长期记忆。
- 工具结果不走 `perception_block`，只走层 10 `tool_result`。

当前 channel name 显示映射只内置了 `qq`→QQ、`desktop`→桌宠；`mobile` 会直接显示为
`mobile`。

---

## 启动模式

| 模式 | 行为 |
|---|---|
| 正常模式 | 注册桌宠通道；`qq.enabled=true` 时注册 QQ 通道并连接 NapCat；启动管理面板（如配置开启） |
| `qq.enabled=false` | 不连接 NapCat，不启动 QQ 消息队列；桌宠、管理面板和调度器照常运行 |
| `standalone_mode=true` | 不连接 NapCat，不启动 QQ 消息队列；桌宠通道直接设为活跃 |

---

## 维护要点

1. 新增输出通道时，实现 `channels.base.BaseChannel`，在 `main.py` 启动阶段注册。
2. 不要在业务模块里直接写 `runtime/channel_queue.json`、`runtime/mobile_queue.json` 或 `runtime/agent_actions.json`，统一走 `DesktopChannel` / `MobileChannel` / `tool_dispatcher`。
3. 桌面动作优先走 WebSocket ack；只有失败或离线时才降级到文件队列。
4. 如果改跨通道感知，检查 `Pipeline._last_channel` 和 `perception_block`，避免把工具结果再次注入层 1。
