# docs/channels.md — 通道与桌宠通信

---

## 定位

通道层只负责**把已经生成好的回复送到用户能看到的地方**。QQ、桌宠、调度器广播共用同一个 `Pipeline`，区别只在入口和发送方式。

桌宠功能已并入新客户端，"desktop" channel 名义保留，实际承载新客户端。QQ 桌宠本体已废弃。

```
QQ 收消息 → main.handle_message → Pipeline → text_output.send() 直发 QQ
桌宠发消息 → POST /desktop/chat → Pipeline → HTTP 返回 reply + turn_sink fanout 到其他活跃端
手机发消息 → POST /mobile/chat → Pipeline → HTTP 返回 reply + turn_sink fanout 到其他活跃端
调度器主动消息 → scheduler._pipeline_send → turn_sink → channels.registry.broadcast()
                                          ├─ DesktopChannel
                                          └─ MobileChannel
```

注意：QQ 主入口的可见发送由 `_qq_reality_reply_adapter` 调用 `text_output.send()`，LLM reply
记忆写入统一走 `core.turn_sink.record_assistant_turn()`；冻结管理面板 `/chat` 已返回 410。
`/desktop/chat`、`/mobile/chat`、scheduler、sensor_aware 同样走 turn sink。legacy
`/desktop/trigger` 已确认零调用方并删除。

---

## 输出通道

| 通道 | 文件 | 激活方式 | 发送方式 |
|---|---|---|---|
| QQ | `channels/qq.py` | `standalone_mode=false` 且 `qq.enabled=true` 时由 `main.py` 注册 | `core/qq_adapter.send_message()` → NapCat |
| 桌宠 | `channels/desktop.py` | 总是注册；WS 连接或 `set_active(True)` 后活跃 | 主动下行优先 WebSocket，失败降级到 `data/runtime/channel_queue.json` |
| 手机 | `channels/mobile.py` | 总是注册；`POST /mobile/activate` 或 `GET /mobile/poll` 后短时活跃 | 写入 `data/runtime/mobile_queue.json`，手机端轮询读取 |

`channels/registry.py` 维护通道注册表：
- `register(channel)`：启动时注册通道。
- `get_active()`：返回 `is_active=True` 的通道。
- `broadcast(content, user_id, behavior=None)`：向所有活跃通道广播；`behavior` 会透传给支持动作包的通道，QQ 通道忽略它。

---

## 手机端轮询通道

文件：`channels/mobile.py`

接口由管理面板服务提供：

| 接口 | 用途 |
|---|---|
| `POST /mobile/activate` | 手机端上线，激活 mobile channel |
| `POST /mobile/deactivate` | 手机端下线，停用 mobile channel |
| `POST /mobile/chat` | 手机端发送用户消息，按 `channel="mobile"` 进入 pipeline，HTTP 返回本端 reply |
| `GET /mobile/poll?limit=20&wait=55` | 拉取并清空最多 20 条手机主动消息；`wait` 可选，0-60 秒，用于后台长轮询 |
| `POST /mobile/push` | 后端工具/调试入口：通过 `MobileChannel.send()` 写入一条主动消息 |

上述接口使用管理面板 Bearer token。手机端当前不连接 `/ws/desktop`，因此不会抢占桌宠 WebSocket。

MobileChannel 的活跃状态有 120 秒 TTL：手机端持续轮询时保持活跃；停止轮询后，调度器广播不会再写入手机队列。

手机和桌宠的 owner 对话入口共享 `core/conversation_gate.py` 的 per-user 锁：
同一用户的 `/desktop/chat` 与 `/mobile/chat` 不会并行进入 `fetch_context → LLM → post_process`。
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
| `POST /desktop/deactivate` | 停用 desktop channel；鉴权失败不执行通道操作 |
| `POST /desktop/wake` | 触发 LLM 轮（Path B）并写记忆；鉴权失败不触发 LLM，不写记忆 |

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

**客户端迁移（Emerald-client）已完成**：客户端通过 Tauri Rust native bridge 在连接头中发送
`Authorization: Bearer <secret>`；服务端不再读取或接受 URL query token。

行为：
- 单连接：新桌宠连接会替换旧连接。
- 普通消息：`push_message()` 发送 `channel_message`，不等 ack。
- 叙事分段：`turn_sink` 在普通消息之后并行发送 `message_segments`，不等 ack。
- 桌面动作：`push_action_and_wait()` 发送 `action`，最多等 5 秒 ack。
- 心跳：服务端每 20 秒发 `ping`，超过约 70 秒没有 `pong` 会断开。

桌宠上线时会把 `DesktopChannel` 设为活跃；断开时取消文件 fallback 活跃标志。

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
{"type":"channel_message","content":"<say>你好</say>","msg_id":"..."}
{"type":"message_segments","content":"你好","segments":[{"type":"say","text":"你好"}],"msg_id":"..."}
```

Emerald-client 的 `ChatPanel` 按 `msg_id` 关联两条消息；`message_segments` 先到时会暂存。
旧客户端忽略未知 `message_segments` 即可继续工作。QQ / mobile 输出会移除 `<say>` 等展示
标签；reality history / event_log 同样保存纯文本。desktop segments 保持原行为。

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
