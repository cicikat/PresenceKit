# presence-device 固件（ESP32 具身硬件）

> 后端侧协议 / WS 通道文档见 `docs/channels.md` → 「设备 WebSocket（ESP32 等具身硬件）」。
> 本文档只讲固件本身：板子、代码结构、鉴权配置、消息渲染。
>
> **不要与 `hardware/Emerald-hello` 混淆**：那是早期纯硬件例程测试项目（仅验证 OLED 初始化，
> 无 WiFi、无 WS、无鉴权），已废弃改名为 `hardware/_achieve_Emerald-hello`。本文档描述的
> `firmware/presence-device` 才是接入后端的真正具身硬件固件。

## 硬件 / 构建

- 板型：`esp32-s3-devkitc-1`（`platformio.ini`，framework=arduino，板型/framework 照抄
  `Emerald-hello` 已跑通的配置，未臆造）。
- 屏幕：SSD1306 128x64 I2C（SDA=5 / SCL=6，addr 0x3C），库 `olikraus/U8g2`。
- 依赖：`links2004/WebSockets`（WS 客户端）、`bblanchon/ArduinoJson`（消息帧解析）。
- 构建工具：PlatformIO（`pio run` / VS Code PlatformIO 插件）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/main.cpp` | `setup()`/`loop()`：初始化 display + ws_client，主循环驱动两者 tick |
| `include/ws_client.h` / `src/ws_client.cpp` | WiFi STA 连接（指数退避 1s→30s）+ `/ws/device` WebSocket 客户端（同样退避），解析消息帧并分发给 display |
| `include/display.h` / `src/display.cpp` | U8g2 中文渲染（`u8g2_font_wqy12_t_gb2312`）：分段自动翻页、流式增量显示、爱心动作、离线状态渲染 |
| `include/secrets.h`（gitignored，本地设备凭据）/ `include/secrets.example.h`（模板） | WiFi SSID/密码、后端局域网 IP、`/ws/device` 鉴权 token |

## 鉴权（SEC-AUTH-2）

`ws_client.cpp::beginWsConnection()` 用 `Authorization: Bearer <AUTH_TOKEN>` 连接
`/ws/device`，与桌宠端 `/ws/desktop` 走同一套 `admin.auth.authenticate_ws()` 校验逻辑
（详见 `docs/security.md`）。

`AUTH_TOKEN` 应填后端 `POST /auth/tokens {"label": "...", "profile": "device"}` 签发的
**`esp32-device` profile token**（仅 `ws.device` scope），不要填 owner 的 legacy admin
secret——虽然目前仍兼容，但一旦 legacy secret 轮换（见 `docs/security.md` P4），继续用它的
设备会立刻断线。改 `AUTH_TOKEN` 后必须重新烧录固件才会生效（没有远程热更新）。

`authHeader` 缓冲区 `char authHeader[96]`：`"Authorization: Bearer "`（22 字节）+
`emt_` 前缀 token（约 47 字节）+ 结尾符，留有余量，无需因换新 token 格式而扩容。

## 消息协议

帧格式与 `/ws/desktop` 完全一致：`hello`/`hello_ack`/`ping`/`pong`/`channel_message`/
`message_segments`/`message_stream_start|delta|end`/`action`/`ack`。`onWsEvent()`
分发到 `handleTextMessage()`，未知 `action.type` 安全忽略、不回 ack（呼应桌宠端对未知
action 的降级策略）。

## 连接状态机

`ConnState`：`WIFI_CONNECTING` → `WS_CONNECTING` → `ONLINE`。WiFi 和 WS 断线各自独立
指数退避重连（1s 翻倍到 30s 封顶），互不干扰；display 层据此渲染离线/重连中状态。
