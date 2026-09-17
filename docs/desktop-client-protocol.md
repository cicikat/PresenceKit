# Desktop client protocol

PresenceKit-desktop 与本后端当前正式使用的桌面协议是 **v0.1（legacy 冻结版）**。
MCP is backend-only and is not part of the desktop/mobile client transport contract.

协议正文的唯一权威位于 PresenceKit-desktop 仓库：

```text
docs/protocol-v0.md
```

ChatPanel 的 HTTP/WS 回复对账契约位于同一仓库：

```text
docs/chat-correlation.md
```

后端实现锚点：

- `channels/desktop_ws.py`：WS 帧、ack 等待与心跳（20 秒 ping，超过 70 秒无 pong 断开）。
- `admin/admin_server.py`：`/ws/desktop` Bearer header 鉴权。
- `admin/routers/chat.py`：正式聊天路径 `POST /desktop/chat`，以及重开入口
  `POST /desktop/wake`。

本仓不复制协议正文，避免客户端与后端各维护一份而发生漂移。修改桌面消息类型、字段、ack 语义或 action allowlist 前，必须先在双方工单中明确升级范围；v0.1 不允许任一端单边扩展。

拟议固定会话 scope（发现、`session_id`、早期 stream 绑定）见
[session-scope-contract.md](session-scope-contract.md)。未与桌面仓同步前，不得把这些字段
写进 v0.1 消息全集，也不得在本仓复制 `protocol-v0.md`。

Tool Ephemeral Status P0 已作为配对的后端与 PresenceKit-desktop 改动交付：`tool_status` 是
S→C、无 ack、无持久 fallback 的瞬态帧，仅覆盖桌面“动向”NOW 区域，不产生聊天气泡或历史。
字段与 TTL 语义以客户端仓的 `docs/protocol-v0.md` 为准；旧客户端可忽略未知类型，但后端不得向
移动端或文件队列投递该事件。

## Desktop wake HTTP contract

`POST /desktop/wake` has two deliberately different outcomes:

- Path A replays at most one persisted assistant trigger turn after `last_seen`.
  The response uses `source="pending_trigger"`, includes `reply`, and preserves
  the canonical identity `turn_id == msg_id`. The wake-delivery ledger makes
  this replay one-shot, including concurrent retries.
- Path B does not run an LLM or create an assistant turn in the HTTP request. It
  queues one expiring `desktop_wake` autonomy signal and normally returns
  `{"reply": null, "source": "queued_autonomy_signal", "correlation_id":
  "...", "expires_at": ...}`. This acknowledgement does not promise a later
  message: silent evaluation, tools-only work, user activity, DND, budget and
  unanswered-message limits are all valid outcomes.

After a queued Path B response, the client must keep listening on the existing
desktop WebSocket. Only if autonomy later chooses `talk_owner` will the normal
`channel_message` and optional `message_segments` frames arrive. The HTTP
response itself never carries a new `turn_id` or `msg_id`. Duplicate,
Dream-Guard-blocked, autonomy-disabled and queue-error outcomes are also
non-text responses and must not be rendered as chat messages.
