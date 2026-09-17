# 后端集成契约（跨仓共用字段）

本页收录不适合放进 [api-reference.md](api-reference.md) 端点表（那是端点/scope 目录，不
是字段 schema）、又需要 desktop / mobile 两端客户端与后端保持一致理解的请求体字段
契约。新增字段前先确认三端（本仓 + PresenceKit-desktop + Emerald-mobile）对齐范围，
避免任一端单边扩展导致字段语义漂移。

固定会话 scope（拟议、未上线）见 [session-scope-contract.md](session-scope-contract.md)。
legacy `/desktop/chat` 与 `/mobile/chat` 今天忽略未知字段并使用 live active；客户端不得把
`char_id`/`session_id` 塞进这两支，以免旧服务端静默发给当时的活跃角色。

## Versioned owner turns and diary sync (Brief 171)

Trusted owner adapters may use `POST /v1/owner/turns` with an
`owner-input` token. The request is deliberately smaller than the legacy chat
body: `client_turn_id`, `message`, optional `reply_to`, and bounded opaque
`upload_ids`. The server resolves owner and active-character scope from its
process configuration and preserves the existing Reality pipeline.

The `(caller label, client_turn_id)` receipt is durable metadata. Same-payload
retries project the canonical retained turn; payload conflicts are `409`, and
an old receipt whose canonical history has expired is
`completed_result_expired` instead of a second LLM run. Use
`GET /v1/owner/turns/{client_turn_id}` for the caller-scoped redacted status
projection.

Obsidian synchronization is separate from the character inner-diary display
API. `POST /integrations/diary/sync` (scope `diary.sync`) accepts only bounded
`YYYY-MM-DD.md` entries plus hash/revision/generation metadata. Remote diary
tools read the server mirror; local mode retains the existing configured
Obsidian path and fallback behavior.

## 引用回复（reply_to）

**适用端点**：`POST /desktop/chat`（`admin/routers/chat.py` → `run_owner_chat_turn()`）。
mobile 端聊天发送入口复用同一套 `run_owner_chat_turn(reply_to=...)` 参数，接入时直接
透传即可，无需单独适配。

**请求体**，在现有聊天请求体上追加的可选字段：

```jsonc
{
  "message": "……",
  "reply_to": {          // 可选；不带时行为与现状完全一致
    "text": "被回复的角色气泡原文",   // 客户端截断至 ~200 字；服务端也会二次截断兜底
    "ts": 1752900000.0               // 该气泡消息的时间戳（epoch seconds）
  }
}
```

v0.1 不建消息 ID 体系，只用「文本 + 时间戳」定位被回复的历史气泡——足够用且零迁移
成本。

**服务端行为**：`core/reply_context.py::apply_reply_prefix()` 在 `run_owner_chat_turn()`
入口处校验并拼接前缀：

```
用户回复了你{相对时间}发送的这条消息「{text}」：{原始 message}
```

相对时间格式化规则（`format_relative_time()`，按自然日边界判定）：

| 与当前时间的自然日差 | 格式 |
|---|---|
| 当天（差 0） | `今天 HH:MM` |
| 1–6 天 | `N 天前` |
| ≥7 天 | `M月D日` |

拼接后的 message 整体作为「本轮用户消息」进入 pipeline（`fetch_context` /
`build_prompt` / `capture_turn`），short_term / mid_term / event_log 因此自然捕获这条
引用上下文，不需要任何记忆层改造，也不新增 prompt 层。

**校验与降级**：以下情况一律**静默降级为普通消息**（不带前缀，不报错）——引用回复
是体验增强，客户端传参异常不应打断整轮对话：

- `reply_to` 不是对象，或 `text` 为空/非字符串
- `ts` 非数字、为负数，或明显是未来时间（服务端允许 ~5 秒时钟误差容忍）
- `text` 超过约 200 字时，服务端二次截断而非拒绝

**探针隔离**：前缀只影响进入 pipeline 的 message，不影响 pre-pipeline 工具探针
（`_probe_text`）——探针始终读取拼接前的原始用户文本，避免被引用原文里的操作性短语
（例如引用了一条包含"打开音乐"的历史消息）误判为当轮指令。

**单测**：`tests/test_reply_context.py`（相对时间三档边界、非法输入降级、超长截断）。
