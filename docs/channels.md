# docs/channels.md — 通道与桌宠通信

---

## 定位

通道层只负责**把已经生成好的回复送到用户能看到的地方**。QQ、桌宠、调度器广播共用同一个 `Pipeline`，区别只在入口和发送方式。

```
QQ 收消息 → main.handle_message → Pipeline → text_output / QQChannel
桌宠发消息 → POST /desktop/chat → Pipeline → DesktopChannel
调度器主动消息 → scheduler._pipeline_send → channels.registry.broadcast()
```

---

## 输出通道

| 通道 | 文件 | 激活方式 | 发送方式 |
|---|---|---|---|
| QQ | `channels/qq.py` | `standalone_mode=false` 时由 `main.py` 注册 | `core/qq_adapter.send_message()` → NapCat |
| 桌宠 | `channels/desktop.py` | 总是注册；WS 连接或 `set_active(True)` 后活跃 | 优先 WebSocket，失败降级到 `data/channel_queue.json` |

`channels/registry.py` 维护通道注册表：
- `register(channel)`：启动时注册通道。
- `get_active()`：返回 `is_active=True` 的通道。
- `broadcast(content, user_id)`：调度器主动消息会广播到所有活跃通道。

---

## 桌宠 WebSocket

文件：`channels/desktop_ws.py`

端点由管理面板服务提供：`ws://127.0.0.1:8080/ws/desktop`

行为：
- 单连接：新桌宠连接会替换旧连接。
- 普通消息：`push_message()` 发送 `channel_message`，不等 ack。
- 桌面动作：`push_action_and_wait()` 发送 `action`，最多等 5 秒 ack。
- 心跳：服务端每 30 秒发 `ping`，超过约 70 秒没有 `pong` 会断开。

桌宠上线时会把 `DesktopChannel` 设为活跃；断开时取消文件 fallback 活跃标志。

---

## 文件降级

当 WebSocket 不在线或发送失败时：

| 文件 | 用途 | 写入方 | 读取方 |
|---|---|---|---|
| `data/channel_queue.json` | 普通消息队列 | `DesktopChannel._write_to_queue()` | 桌宠端轮询 |
| `data/agent_actions.json` | 桌面动作队列 | `tool_dispatcher._push_desktop_action()` | 桌宠端轮询 |
| `data/pending_perception/` | 动作失败后的下轮感知 | `pipeline._parse_and_execute_intent()` | `pipeline.build_prompt()` |

所有上述路径都通过 `core/sandbox.get_paths()` 获取，测试模式会切到 `data/test_sandbox/{session}/`。

---

## 跨通道接续

`Pipeline.build_prompt(..., channel="qq"|"desktop")` 会记录上一轮通道：
- 如果本轮通道和上轮不同，会在层 1 的 `perception_block` 注入一句接续提示。
- 这只影响本轮 prompt，不写入长期记忆。
- 工具结果不走 `perception_block`，只走层 10 `tool_result`。

---

## 启动模式

| 模式 | 行为 |
|---|---|
| 正常模式 | 注册桌宠通道和 QQ 通道，连接 NapCat，启动管理面板（如配置开启） |
| `standalone_mode=true` | 不连接 NapCat，不启动 QQ 消息队列；桌宠通道直接设为活跃 |

---

## 维护要点

1. 新增输出通道时，实现 `channels.base.BaseChannel`，在 `main.py` 启动阶段注册。
2. 不要在业务模块里直接写 `channel_queue.json` 或 `agent_actions.json`，统一走 `DesktopChannel` / `tool_dispatcher`。
3. 桌面动作优先走 WebSocket ack；只有失败或离线时才降级到文件队列。
4. 如果改跨通道感知，检查 `Pipeline._last_channel` 和 `perception_block`，避免把工具结果再次注入层 1。
