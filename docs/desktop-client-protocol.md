# Desktop client protocol

PresenceKit-desktop 与本后端当前正式使用的桌面协议是 **v0.1（legacy 冻结版）**。

协议正文的唯一权威位于 PresenceKit-desktop 仓库：

```text
docs/protocol-v0.md
```

ChatPanel 的 HTTP/WS 回复对账契约位于同一仓库：

```text
docs/chat-correlation.md
```

本仓不复制协议正文，避免客户端与后端各维护一份而发生漂移。修改桌面消息类型、字段、ack 语义或 action allowlist 前，必须先在双方工单中明确升级范围；v0.1 不允许任一端单边扩展。