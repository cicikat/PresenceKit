# 三仓库真实互动链路审计

> 审计日期：2026-05-19  
> 范围：`D:\ai\Emerald-client`、`D:\ai\yexuan_memery`、`D:\ai\qq-st-bot`  
> 限制：本文只记录代码和文档事实；未在代码中找到的步骤标为“未找到明确实现 / 待确认”。

## 1. 总览

三个仓库的当前边界比较清楚：

| 仓库 | 当前职责 | 证据 |
|---|---|---|
| `qq-st-bot` | 后端和核心真相源：QQ/HTTP/WS 入口、Pipeline、LLM、记忆、情绪、调度器、花园、通道广播。 | `D:\ai\qq-st-bot\ARCHITECTURE.md`；`D:\ai\qq-st-bot\main.py:82`；`D:\ai\qq-st-bot\admin\routers\chat.py:29` |
| `Emerald-client` | Tauri + React 桌面客户端：聊天 UI、Tauri HTTP 桥、legacy desktop WS 订阅、桌面 sensor 发布、只读花园/日记/状态面板。 | `D:\ai\Emerald-client\ARCHITECTURE.md`；`D:\ai\Emerald-client\src-tauri\src\lib.rs:26`；`D:\ai\Emerald-client\src\shared\api\ws.ts:9` |
| `yexuan_memery` | Flutter/Android 手机薄客户端：`mobile channel` 对话、主动消息轮询、后台通知、无障碍屏幕上下文、移动端行为 metadata 消费。 | `D:\ai\yexuan_memery\README.md`；`D:\ai\yexuan_memery\lib\main.dart:1563`；`D:\ai\yexuan_memery\android\app\src\main\kotlin\com\example\yexuan_memery\MobileNotificationService.kt:19` |

当前事实不是“三端都走同一出口”。`/desktop/chat`、`/mobile/chat`、普通 scheduler 和 `sensor_aware` 已经大量使用 `core.turn_sink.record_assistant_turn()`，但 QQ 主入口、管理面板冻结入口、`/desktop/trigger` 和重复 `/chat` 仍有绕过路径。

## 2. 当前项目真实术语表

### 消息入口名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `handle_message(message)` | QQ 消息主入口 | `D:\ai\qq-st-bot\main.py:82` |
| `message_queue.enqueue()` / `_process_session()` | QQ 会话串行队列 | `D:\ai\qq-st-bot\core\message_queue.py:47` / `D:\ai\qq-st-bot\core\message_queue.py:68` |
| `POST /desktop/chat` / `desktop_chat()` | 桌面客户端 HTTP 对话入口，无鉴权 | `D:\ai\qq-st-bot\admin\routers\chat.py:189` |
| `POST /mobile/chat` / `mobile_chat()` | 手机端 HTTP 对话入口，Bearer 鉴权 | `D:\ai\qq-st-bot\admin\routers\mobile.py:40` |
| `run_owner_chat_turn(message, channel_name)` | desktop/mobile 共用 owner 对话函数 | `D:\ai\qq-st-bot\admin\routers\chat.py:29` |
| `POST /upload/ingest` | 三端统一文件上传入口，无鉴权 | `D:\ai\qq-st-bot\admin\routers\chat.py:207` |
| `POST /desktop/trigger` | 桌宠触发 QQ 回复的旧入口，无鉴权 | `D:\ai\qq-st-bot\admin\routers\chat.py:280` |
| `POST /chat` / `frontend_chat()` | 管理面板专用对话，Bearer 鉴权，冻结 | `D:\ai\qq-st-bot\admin\routers\chat.py:140` |
| `POST /chat` / `unified_chat()` | 重复注册的对话入口，无显式鉴权 | `D:\ai\qq-st-bot\admin\routers\chat.py:322` |
| `POST /watch/event` | Watch/快捷指令事件入口，query secret | `D:\ai\qq-st-bot\admin\routers\watch.py:174` |
| `POST /sensor/push` | 低频手机传感器入口，无鉴权，写 user_profile | `D:\ai\qq-st-bot\admin\routers\sensor.py:81` |
| `POST /sensor/realtime` | 实时键鼠/屏幕上下文入口，Bearer 鉴权 | `D:\ai\qq-st-bot\admin\routers\sensor.py:182` |
| `POST /sensor/activity` | 桌宠活动快照入口，无鉴权，写 activity_snapshot | `D:\ai\qq-st-bot\admin\routers\sensor.py:248` |
| `ws://127.0.0.1:8080/ws/desktop` | 桌面 WS 入口 | `D:\ai\qq-st-bot\admin\admin_server.py:69`；`D:\ai\Emerald-client\src\shared\api\ws.ts:60` |

### 触发器/主动行为名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `_pipeline_send()` | scheduler 生成主动发言的核心函数 | `D:\ai\qq-st-bot\core\scheduler\loop.py:179` |
| `_COOLDOWNS` | scheduler 触发器冷却注册表 | `D:\ai\qq-st-bot\core\scheduler\loop.py:24` |
| `_HIGH_PRIORITY_TRIGGERS` | 高优先级触发器集合 | `D:\ai\qq-st-bot\core\scheduler\loop.py:83` |
| `morning_greeting` / `night_reminder` / `random_message` / `weather_alert` / `daily_journal` / `spontaneous_recall` | 时间类主动触发器 | `D:\ai\qq-st-bot\core\scheduler\triggers\time_based.py`；`D:\ai\qq-st-bot\docs\scheduler.md` |
| `period_reminder` | 生理期主动触发器 | `D:\ai\qq-st-bot\core\scheduler\triggers\period.py` |
| `topic_followup` | 未完结话题追问 | `D:\ai\qq-st-bot\core\scheduler\triggers\memory.py` |
| `hr_high` / `hr_critical` / `sleep_end` | Watch 健康触发器 | `D:\ai\qq-st-bot\core\scheduler\triggers\watch.py:8` |
| `sensor_aware` | 实时状态主动开口触发器 | `D:\ai\qq-st-bot\core\scheduler\triggers\sensor_aware.py:286` |
| `PRESENCE_LEFT` / `PRESENCE_RETURNED` / `LONG_FOCUS` / `FOCUS_SCATTERED` / `SILENT_TOGETHER` / `APP_CATEGORY_CHANGED` / `LATE_NIGHT_ACTIVE` / `LONG_AT_DESK` | sensor 候选事件常量 | `D:\ai\qq-st-bot\core\scheduler\sensor_events.py:15` |
| `garden_water` / `garden_daily` / `garden_bloom` / `garden_handle_*` / `garden_vase_wilted` | 花园主动/伴生触发器 | `D:\ai\qq-st-bot\core\scheduler\loop.py:61`；`D:\ai\qq-st-bot\core\scheduler\triggers\garden_water.py`；`D:\ai\qq-st-bot\core\scheduler\triggers\garden_daily.py` |
| `manual_trigger(name)` | 管理面板手动触发 | `D:\ai\qq-st-bot\core\scheduler\loop.py:341` |

### 记忆模块名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `short_term` | 短期历史 | `D:\ai\qq-st-bot\core\memory\short_term.py`；`D:\ai\qq-st-bot\docs\memory.md` |
| `event_log` | 每日事件流水账 | `D:\ai\qq-st-bot\core\memory\event_log.py`；`D:\ai\qq-st-bot\docs\memory.md` |
| `mid_term` | 12 小时中期摘要 | `D:\ai\qq-st-bot\core\memory\mid_term.py`；`D:\ai\qq-st-bot\core\memory\fixation_pipeline.py:283` |
| `episodic_memory` | 情景记忆 | `D:\ai\qq-st-bot\core\memory\episodic_memory.py`；`D:\ai\qq-st-bot\core\memory\fixation_pipeline.py:343` |
| `character_growth` | 角色对用户的长期认知 | `D:\ai\qq-st-bot\core\memory\character_growth.py`；`D:\ai\qq-st-bot\core\memory\fixation_pipeline.py:540` |
| `capture_turn` / `summarize_to_midterm` / `reflect_to_episodic` / `consolidate_to_growth` | 信息固化四 job | `D:\ai\qq-st-bot\core\memory\fixation_pipeline.py:240` / `:283` / `:343` / `:540` |
| `user_profile` | 用户画像、低频传感器摘要、心率/睡眠片段 | `D:\ai\qq-st-bot\core\memory\user_profile.py`；`D:\ai\qq-st-bot\admin\routers\sensor.py:27`；`D:\ai\qq-st-bot\admin\routers\watch.py:14` |
| `pending_perception` | 桌面动作失败感知暂存 | `D:\ai\qq-st-bot\core\memory\pending_perception.py`；`D:\ai\qq-st-bot\core\pipeline.py:216` |
| `realtime_state` | 实时 sensor 内存快照，不持久化 | `D:\ai\qq-st-bot\core\memory\realtime_state.py:18` |

### 情绪状态模块名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `mood_state.update()` / `get_current()` / `nudge_from_memory()` | 全局情绪状态读写和召回推动 | `D:\ai\qq-st-bot\core\memory\mood_state.py`；`D:\ai\qq-st-bot\docs\memory.md` |
| `detect_emotion()` | 每轮回复情绪检测 | `D:\ai\qq-st-bot\core\llm_client.py:325`；`D:\ai\qq-st-bot\core\pipeline.py:321` |
| `thinking` source=`trigger` | 工具探针命中时的情绪触发 | `D:\ai\qq-st-bot\main.py:239` |
| `sleepy` source=`schedule` | 深夜 fetch_context 时的情绪触发 | `D:\ai\qq-st-bot\core\pipeline.py:148` |
| `yandere` source=`trigger` | 关键词 + 关系阈值触发 | `D:\ai\qq-st-bot\core\pipeline.py:19` / `:337` |

### 花园/植物模块名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `core.garden.manager` | 花园核心逻辑 | `D:\ai\qq-st-bot\core\garden\manager.py` |
| `water(slot_key, reason)` | 给指定花槽浇水 | `D:\ai\qq-st-bot\core\garden\manager.py:127` |
| `auto_water_tick()` | 根据当前 mood 自动浇水 | `D:\ai\qq-st-bot\core\garden\manager.py:170` |
| `force_water()` | 被动浇水工具入口 | `D:\ai\qq-st-bot\core\garden\manager.py:191`；`D:\ai\qq-st-bot\core\tools\garden_tools.py:21` |
| `daily_check()` | harvest/vase 每日生命周期扫描 | `D:\ai\qq-st-bot\core\garden\manager.py:203` |
| `GET /garden/state` | 花园只读状态接口 | `D:\ai\qq-st-bot\admin\routers\garden.py`；`D:\ai\Emerald-client\src-tauri\src\lib.rs:64` |

### 广播/推送/客户端通信名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `record_assistant_turn()` | assistant turn 写入 + fanout 汇聚点 | `D:\ai\qq-st-bot\core\turn_sink.py:123` |
| `channels.registry.broadcast()` | 活跃通道广播 | `D:\ai\qq-st-bot\channels\registry.py:28` |
| `DesktopChannel.send()` | desktop 下行，WS 优先，文件降级 | `D:\ai\qq-st-bot\channels\desktop.py:40` |
| `MobileChannel.send()` / `poll()` | mobile 下行队列写入与轮询读取 | `D:\ai\qq-st-bot\channels\mobile.py:45` / `:54` |
| `QQChannel.send()` | QQ 下行通道 | `D:\ai\qq-st-bot\channels\qq.py:28` |
| `desktop_ws.push_message()` / `push_action_and_wait()` | desktop legacy WS 下行 | `D:\ai\qq-st-bot\channels\desktop_ws.py:47` / `:57` |
| `text_output.send()` | QQ 直接发送出口 | `D:\ai\qq-st-bot\core\output\text_output.py:17` |
| `channel_message` / `action` / `ack` / `ping` / `pong` | Emerald-client 当前 legacy WS 消息类型 | `D:\ai\Emerald-client\src\shared\api\ws.ts:87` / `:91` |
| `MobileNotificationService` | Android 后台 mobile poll + 通知服务 | `D:\ai\yexuan_memery\android\app\src\main\kotlin\com\example\yexuan_memery\MobileNotificationService.kt:19` |

### 鉴权/安全检查名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `verify_token()` | FastAPI Bearer token 校验 | `D:\ai\qq-st-bot\admin\auth.py:14` |
| `admin.secret_key` | 管理 token 配置字段 | `D:\ai\qq-st-bot\admin\auth.py:17` |
| `watch_secret` / `_watch_secret()` | Watch query secret；未配置则不校验 | `D:\ai\qq-st-bot\admin\routers\watch.py:164` |
| `HTTPBearer(auto_error=False)` | 管理面板鉴权机制 | `D:\ai\qq-st-bot\admin\auth.py:11` |
| `reqwest::Client::builder().no_proxy()` | Emerald-client Rust HTTP 代理绕过 | `D:\ai\Emerald-client\src-tauri\src\lib.rs:27` |
| `token = "Emerald1231"` | Android 后台服务硬编码 token | `D:\ai\yexuan_memery\android\app\src\main\kotlin\com\example\yexuan_memery\MobileNotificationService.kt:26` |
| `_adminToken = "Emerald1231"` | Flutter 前台硬编码 token | `D:\ai\yexuan_memery\lib\main.dart:1927` |
| `ADMIN_TOKEN = "Emerald1231"` | Emerald-client 前端硬编码 token | `D:\ai\Emerald-client\src\shared\api\backend.ts:5` |

### 测试/调试标记名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `run_test.py` | 后端测试模式启动，初始化 `mode="test"` sandbox | `D:\ai\qq-st-bot\run_test.py:1` / `:18` |
| `data/test_sandbox/{session_id}` | 测试数据根 | `D:\ai\qq-st-bot\core\sandbox.py:33` |
| `debugBackgroundDelivery` | Android 本地后台投递调试 | `D:\ai\yexuan_memery\android\app\src\main\kotlin\com\example\yexuan_memery\MainActivity.kt:139` / `:385` |
| `pushMobileBehaviorTest()` | Flutter 写 `/mobile/push` 的主动行为测试 | `D:\ai\yexuan_memery\lib\main.dart:1703` |
| `prompt_kind: debug_test` / `cooldown_key: debug:*` | mobile behavior test metadata | `D:\ai\yexuan_memery\lib\main.dart:1725` |
| `sensor_aware_audit` | sensor_aware 决策 ring buffer | `D:\ai\qq-st-bot\core\scheduler\triggers\sensor_aware_audit.py` |
| `data/debug/llm_output` | LLM 异常输出 debug 目录，当前代码直接 `Path("data/...")` | `D:\ai\qq-st-bot\core\llm_output_validator.py:8` |

### 队列/异步任务名称

| 术语 | 类型 | 来源 |
|---|---|---|
| `core.message_queue` | QQ 会话队列 | `D:\ai\qq-st-bot\core\message_queue.py:24` / `:47` |
| `slow_queue` | 后处理慢任务队列，单 worker，不持久化 | `D:\ai\qq-st-bot\core\post_process\slow_queue.py:31` / `:91` |
| `data/channel_queue.json` | desktop 普通消息文件降级队列 | `D:\ai\qq-st-bot\channels\desktop.py:61` |
| `data/mobile_queue.json` | mobile 主动消息轮询队列 | `D:\ai\qq-st-bot\channels\mobile.py:78` |
| `data/agent_actions.json` | 桌面 action 文件队列 | `D:\ai\qq-st-bot\channels\desktop.py:79`；`D:\ai\qq-st-bot\core\tool_dispatcher.py:183` |
| `pending_perception/processing` | 动作失败两阶段提交目录 | `D:\ai\qq-st-bot\core\memory\pending_perception.py` |
| `_sleep_buffer` / `_sleep_flush_task` | Watch sleep_end 合并缓冲 | `D:\ai\qq-st-bot\admin\routers\watch.py:37` / `:38` |
| `_queue_condition` | mobile queue 长轮询唤醒条件 | `D:\ai\qq-st-bot\channels\mobile.py:19` |

## 3. 三仓库职责边界表

| 项目 | 主要职责 | 不应承担的职责 | 关键文件/模块 | 当前风险 |
|---|---|---|---|---|
| `qq-st-bot` | 角色核心、LLM、prompt、记忆、情绪、花园、scheduler、HTTP/WS 服务、通道 fanout。 | 不应把客户端 UI 状态当真相；不应让未鉴权客户端写真实关系记忆；不应让测试/调试事件默认进入真实记忆。 | `main.py`、`admin/routers/*.py`、`core/pipeline.py`、`core/turn_sink.py`、`core/memory/*`、`core/scheduler/*`、`channels/*` | 仍有多个绕过 `turn_sink` 的写入/发送路径；若 `watch_secret` 为空或无鉴权端口暴露，事件可写真实 profile/记忆/情绪。 |
| `Emerald-client` | 桌面 UI、Tauri HTTP 桥、desktop WS 连接、桌面 sensor 发布、状态展示。 | 不应拥有 mood/activity/presence 的业务真值；不应伪造 action 成功；不应硬编码长期 token。 | `src/shared/api/backend.ts`、`src/shared/api/ws.ts`、`src-tauri/src/lib.rs`、`src-tauri/src/sensor/*` | `action` 立即成功 ack 但未执行；v1 协议未落地；token 仍硬编码。 |
| `yexuan_memery` | 手机 UI、mobile chat/poll、后台通知、无障碍屏幕上下文、悬浮/锁屏确认层。 | 不应定义后端主动行为规则；不应自动升级普通消息为高危动作；不应把屏幕敏感文本默认长期化。 | `lib/main.dart`、`MobileNotificationService.kt`、`YexuanAccessibilityService.kt`、`FloatingBubbleService.kt` | 后台服务和 Flutter 均硬编码 token；debug 行为测试会写入真实 mobile queue；屏幕上下文会进入后端实时状态并参与主动发言裁决。 |

## 4. 真实互动链路

### 用户主动发消息（desktop / mobile owner 入口）

触发来源 → `Emerald-client` `sendChat()` 或 Flutter `BackendClient.sendChat()`。  
入口文件/函数 → desktop：`D:\ai\Emerald-client\src-tauri\src\lib.rs:26` → `POST /desktop/chat` → `D:\ai\qq-st-bot\admin\routers\chat.py:189`；mobile：`D:\ai\yexuan_memery\lib\main.dart:1563` → `POST /mobile/chat` → `D:\ai\qq-st-bot\admin\routers\mobile.py:40`。  
鉴权/安全检查 → desktop 无鉴权；mobile 使用 `verify_token`。  
事件是否被标准化 → 两者都进入 `run_owner_chat_turn(message, channel_name)`，但没有统一 envelope。  
是否生成 AI 回复 → 是，`fetch_context()` → `build_prompt()` → `run_llm()`。  
回复如何落库 → `record_assistant_turn(source=USER_CHAT, user_text=message)` → `pipeline.post_process()` → `capture_turn()`。  
是否写入记忆 → 是，写 short_term + event_log；慢队列写 mid_term，可能晋升 episodic/growth。  
是否影响情绪 → 是，`detect_emotion()` 后 `mood_state.update(source="detect")`，yandere 关键词另触发。  
是否影响花园 → 普通消息不直接影响；若探针命中 `water_garden`，会通过工具调用 `force_water()`。  
是否进入异步总结/反思/成长 → 是，`slow_queue.enqueue("summarize_to_midterm")`，显著情绪可 eager 反思。  
如何广播到客户端 → `record_assistant_turn(fanout="all")` 向所有活跃通道发送。  
当前风险 → desktop 无鉴权但写真实记忆；`fanout="all"` 可能导致用户在一个端输入，多个活跃端都收到回复。  
证据路径 → `D:\ai\qq-st-bot\admin\routers\chat.py:29`、`:68`；`D:\ai\qq-st-bot\core\turn_sink.py:123`；`D:\ai\qq-st-bot\core\pipeline.py:287`。

### QQ 收到消息

触发来源 → NapCat OneBot WS。  
入口文件/函数 → `qq_adapter._parse_event()` → `message_queue.enqueue()` → `main.handle_message()`。  
鉴权/安全检查 → QQ 侧有黑名单和群聊 at 过滤；没有 admin Bearer。  
事件是否被标准化 → `_parse_event()` 统一为 `{user_id, group_id, content, sender_name, timestamp, image_urls, file_info}`。  
是否生成 AI 回复 → 是，主流程内探针、context、prompt、LLM。  
回复如何落库 → 当前先 `text_output.send()` 直接发 QQ，再 `asyncio.create_task(_pipeline.post_process(...))`。  
是否写入记忆 → 是，但 post_process 异步，不通过 `record_assistant_turn()`。  
是否影响情绪 → 是，post_process 内检测情绪；工具探针命中会先 `mood_state.update("thinking", source="trigger")`。  
是否影响花园 → 仅当工具探针命中 `water_garden`。  
是否进入异步总结/反思/成长 → 是，由 post_process 入 slow_queue。  
如何广播到客户端 → 只发送 QQ；不走 `channels.registry.broadcast()`，不会自动同步 desktop/mobile。  
当前风险 → QQ 是主要未统一出口：发送、落库和广播顺序与 owner/scheduler 不一致，post_process 不 await，触发来源元数据不进入 `turn_sink`。  
证据路径 → `D:\ai\qq-st-bot\main.py:82`、`:290`、`:308`；`D:\ai\qq-st-bot\core\output\text_output.py:17`。

### mobile 端触发事件

触发来源 → Flutter 前台发送消息、前台轮询、后台 `MobileNotificationService` 长轮询、能力页调试。  
入口文件/函数 → `POST /mobile/chat`、`GET /mobile/poll`、`POST /mobile/activate`、`POST /mobile/push`。  
鉴权/安全检查 → 后端 mobile 路由全部 `verify_token`；客户端 token 当前硬编码。  
事件是否被标准化 → 对话只传 `message`；主动消息读取 `mobile_queue` item：`id/content/user_id/timestamp/behavior?`。未发现统一 envelope。  
是否生成 AI 回复 → `/mobile/chat` 是；`/mobile/poll` 否；`/mobile/push` 否。  
回复如何落库 → `/mobile/chat` 走 `record_assistant_turn(USER_CHAT)`；`/mobile/push` 只写 mobile queue，不写记忆。  
是否写入记忆 → `/mobile/chat` 是；`/mobile/push` 否。  
是否影响情绪 → `/mobile/chat` 是；`/mobile/push` 否。  
是否影响花园 → `/mobile/chat` 中工具触发才可能；`/mobile/push` 否。  
是否进入异步总结/反思/成长 → `/mobile/chat` 是。  
如何广播到客户端 → `/mobile/chat` fanout all；scheduler 或 mobile push 写 `mobile_queue.json`，手机轮询读取。  
当前风险 → mobile queue item 缺少 `trigger/priority/is_test` 等字段；行为测试写入真实队列，靠 content/behavior 约定区分。  
证据路径 → `D:\ai\qq-st-bot\admin\routers\mobile.py:40`、`:55`、`:68`；`D:\ai\qq-st-bot\channels\mobile.py:75`；`D:\ai\yexuan_memery\lib\main.dart:1703`。

### desktop 端触发事件

触发来源 → Emerald-client HTTP、legacy WS 连接、桌面 sensor runner、文件上传。  
入口文件/函数 → `send_chat()` → `/desktop/chat`；`wsClient.connect()` → `/ws/desktop`；`upload_document()` → `/upload/ingest`；sensor runner → `/sensor/realtime`。  
鉴权/安全检查 → `/desktop/chat` 无鉴权；desktop WS 无 token；`/sensor/realtime` 使用 Bearer；上传接口后端无鉴权，Emerald-client 传 token 但后端不校验。  
事件是否被标准化 → chat/message 没有 envelope；WS legacy 为 `hello/channel_message/action/ack/ping/pong`。  
是否生成 AI 回复 → `/desktop/chat` 和上传是；WS 连接本身不是。  
回复如何落库 → `/desktop/chat` 经 `turn_sink`；上传调用 `run_owner_chat_turn()`；WS 主动消息来自后端，不直接生成回复。  
是否写入记忆 → chat/上传是。  
是否影响情绪 → chat/上传是；sensor realtime 本身不更新 mood，但 `sensor_aware` 发言后会通过 post_process 更新 mood。  
是否影响花园 → chat 工具触发才可能；只读 `GET /garden/state` 不影响。  
是否进入异步总结/反思/成长 → chat/上传是。  
如何广播到客户端 → `DesktopChannel.send()` 优先 `desktop_ws.push_message()`，失败写 `channel_queue.json`。  
当前风险 → desktop action 当前客户端会 ack 成功但没有执行器；WS 没有 token；文件 fallback 当前 Emerald-client 不读。  
证据路径 → `D:\ai\Emerald-client\src-tauri\src\lib.rs:26`、`:266`；`D:\ai\Emerald-client\src\shared\api\ws.ts:91`；`D:\ai\qq-st-bot\channels\desktop.py:40`。

### 自动/主动触发器触发

触发来源 → `core.scheduler.loop._loop()` 每 60 秒 gather。  
入口文件/函数 → `_pipeline_send(prompt, trigger_name=...)`。  
鉴权/安全检查 → 内部任务，无外部鉴权；低优先级受 `_user_active_recently(120)`；高优先级绕过活跃窗口。  
事件是否被标准化 → 以 `trigger_name` 表示来源，没有统一 event envelope。  
是否生成 AI 回复 → 是，除 `_pipeline is None` 时降级直接发送 prompt 原文。  
回复如何落库 → 当前普通触发器经 `record_assistant_turn(source=TRIGGER/WATCH/SENSOR, trigger_name=...)`。  
是否写入记忆 → 是，`capture_turn()` 写 assistant only；慢队列会总结 assistant turn。  
是否影响情绪 → 是，post_process 对主动回复做 detect/update。  
是否影响花园 → `garden_water` / `garden_daily` 会改花园；普通触发器不直接改。  
是否进入异步总结/反思/成长 → 是，assistant only 也会进入 mid_term，可能进一步晋升，取决于 LLM 输出和强度。  
如何广播到客户端 → 默认 `fanout="all"`；`sensor_aware` 二段式特殊路径见下。  
当前风险 → 触发来源只有 `trigger_name`，缺少 `is_system_initiated/can_write_memory/priority` 等显式策略字段；`_pipeline is None` 降级直接发送时不写记忆。  
证据路径 → `D:\ai\qq-st-bot\core\scheduler\loop.py:179`、`:226`。

### 早安/睡眠/日程类触发

触发来源 → `time_based.py`、`watch.py`、reminders。  
入口文件/函数 → `morning_greeting/night_reminder/daily_journal/reminders` 经 `_pipeline_send()`；`sleep_end` 由 `/watch/event` 缓冲后 `scheduler.on_watch_event("sleep_end")`。  
鉴权/安全检查 → scheduler 内部无外部鉴权；Watch 入口取决于 `watch_secret` 是否配置。  
事件是否被标准化 → trigger_name；Watch sleep_end 合并为 `merged` dict + prompt。  
是否生成 AI 回复 → 是。  
回复如何落库 → 经 `record_assistant_turn()`。  
是否写入记忆 → 是，assistant only。  
是否影响情绪 → 是。  
是否影响花园 → 否，除非回复后意图解析触发桌面 action，不涉及花园。  
是否进入异步总结/反思/成长 → 是。  
如何广播到客户端 → 默认 all。  
当前风险 → Watch secret 未配置时 `/watch/event` 不校验；健康事件可能被外部误触发并写入 profile/主动记忆。  
证据路径 → `D:\ai\qq-st-bot\admin\routers\watch.py:164`、`:174`；`D:\ai\qq-st-bot\core\scheduler\triggers\watch.py:8`。

### 心率/健康数据触发

触发来源 → `POST /watch/event` heart_rate/sleep_end。  
入口文件/函数 → `receive_watch_event()`；heart_rate `asyncio.create_task(scheduler.on_watch_event(...))`；sleep_end `_sleep_buffer` 合并后调用 scheduler。  
鉴权/安全检查 → query `secret`；若 config 未设置 watch_secret 则 `_watch_secret()` 返回空字符串且不校验。  
事件是否被标准化 → heart_rate 只保留 `value`；sleep_end 合并为 `sleep_start/sleep_end_time/duration_minutes/prompt`。  
是否生成 AI 回复 → heart_rate 超阈值时是；sleep_end 是。  
回复如何落库 → 经 `_pipeline_send()` → `record_assistant_turn(source=WATCH)`。  
是否写入记忆 → 是，assistant only；心率事件还写 user_profile。  
是否影响情绪 → 是，回复后 detect/update。  
是否影响花园 → 间接：后续 `garden_water` 会读取当前 mood。  
是否进入异步总结/反思/成长 → 是。  
如何广播到客户端 → 默认 all。  
当前风险 → 健康数据入口认证弱于 mobile/desktop Bearer；`triggered` 字段写心率 profile 时初始为 False，未见后续回写为 True（待确认）。  
证据路径 → `D:\ai\qq-st-bot\admin\routers\watch.py:14`、`:174`、`:255`；`D:\ai\qq-st-bot\core\scheduler\triggers\watch.py:8`。

### 后台状态/文件/日记类事件

触发来源 → 文件上传、Obsidian/日记接口、desktop/mobile sensor、activity snapshot。  
入口文件/函数 → `/upload/ingest`、`/sensor/push`、`/sensor/realtime`、`/sensor/activity`、日记工具/日记调度器。  
鉴权/安全检查 → 上传无鉴权；sensor push/activity 无鉴权；sensor realtime Bearer；日记只读接口 Bearer；日记工具通过内部工具调用。  
事件是否被标准化 → 上传被拼成 `media_context` 用户消息；sensor realtime 是固定 Pydantic model；sensor push 是松散 dict。  
是否生成 AI 回复 → 上传是；sensor push/activity/realtime 否，但 realtime 可被 `sensor_aware` 消费后生成主动回复。  
回复如何落库 → 上传走 `run_owner_chat_turn()`；sensor_aware 走 `record_assistant_turn(source=SENSOR)`。  
是否写入记忆 → 上传是；sensor push 写 user_profile；sensor realtime 仅内存，但触发出的 assistant reply 会写记忆。  
是否影响情绪 → 上传和 sensor_aware 回复会影响；sensor push/activity 本身不直接影响。  
是否影响花园 → sensor_aware 改 mood 后可能影响后续自动浇水；sensor push 不直接影响。  
是否进入异步总结/反思/成长 → 上传和 sensor_aware 回复会进入。  
如何广播到客户端 → 上传 fanout all；sensor_aware fanout desktop/mobile。  
当前风险 → sensor realtime 的 `screen_text_hint` 会进入 sensor_judge 和 LLM prompt；若回复复述敏感屏幕内容，可能被写进 assistant 记忆链。  
证据路径 → `D:\ai\qq-st-bot\admin\routers\sensor.py:182`；`D:\ai\qq-st-bot\core\scheduler\sensor_judge.py:88`；`D:\ai\qq-st-bot\core\scheduler\triggers\sensor_aware.py:471`。

### debug/test/dry-run 事件

触发来源 → 后端 `run_test.py`、pytest sandbox、Flutter 能力页、Android 本地 debug delivery、mobile behavior test。  
入口文件/函数 → `init_paths(mode="test")`；`debugBackgroundDelivery`；`pushMobileBehaviorTest()` → `/mobile/push`。  
鉴权/安全检查 → `run_test.py` 本地；`/mobile/push` Bearer；Android debug delivery 本机 MethodChannel，不打后端。  
事件是否被标准化 → mobile behavior test 带 `prompt_kind=debug_test/cooldown_key=debug:*`，但 queue item 没有顶层 `is_test`。  
是否生成 AI 回复 → behavior test 否；debug delivery 否；run_test 取决于测试启动后的真实交互。  
回复如何落库 → behavior test 只写 mobile queue，不落库；run_test 落入 test sandbox。  
是否写入记忆 → behavior test 否；但如果用户在生产后端用调试 UI 发送真实 `/mobile/chat`，会写生产记忆。  
是否影响情绪 → behavior test 否。  
是否影响花园 → behavior test 否。  
是否进入异步总结/反思/成长 → behavior test 否。  
如何广播到客户端 → `/mobile/push` 只进 mobile queue。  
当前风险 → debug/test 标记只在 behavior payload 内，不是后端统一策略字段；生产能力页可向真实队列投递测试通知。  
证据路径 → `D:\ai\qq-st-bot\run_test.py:18`；`D:\ai\yexuan_memery\lib\main.dart:1703`；`D:\ai\yexuan_memery\android\app\src\main\kotlin\com\example\yexuan_memery\MobileNotificationService.kt:58`。

## 5. 当前统一出口/核心收口点

| 问题 | 当前代码事实 |
|---|---|
| AI 回复最终在哪里落库 | 新主路径是 `record_assistant_turn()` → `Pipeline.post_process()` → `fixation_pipeline.capture_turn()`。QQ 主入口和若干旧 HTTP 入口仍直接 `pipeline.post_process()`。 |
| 消息最终在哪里广播 | 新主路径是 `record_assistant_turn()` 内 `_fanout()` 调通道；也存在 `channels.registry.broadcast()`；QQ 主入口和 `/desktop/trigger` 使用 `text_output.send()` 直接发 QQ。 |
| 触发来源在哪里记录 | `capture_turn(trigger_name=...)` 在 scheduler assistant meta 写 `trigger`；普通用户消息无统一 source 字段；sensor/mobile behavior metadata 不会统一写入 event_log。 |
| 并发/锁在哪里处理 | owner desktop/mobile 由 `conversation_lock(uid)` 包 `fetch_context → LLM → post_process`；关键记忆写入由 `uid_lock(uid)`；mood 由 `global_lock("mood_state")`；QQ 由 `message_queue` 按 session 串行，但不走 owner `conversation_lock`。 |
| 慢任务在哪里调度 | `core.post_process.slow_queue`，单 worker，不持久化，失败写 DLQ。 |

结论：**当前没有发现完全统一出口**。`turn_sink` 已经是新核心收口点，但 QQ 主消息、冻结管理面板 `/chat`、`/desktop/trigger`、重复 `unified_chat` 仍绕过它。缺口主要在：发送出口不统一、source/priority/test/debug 策略未统一、部分入口鉴权不一致。

## 6. Interaction Contract 草案

以下是基于当前代码事实提出的文档草案，不要求立即实现：

| 字段 | 建议含义 |
|---|---|
| `source` | `qq` / `desktop` / `mobile` / `scheduler` / `watch` / `sensor` / `debug` |
| `trigger_name` | 现有 `trigger_name` 或客户端事件名，如 `morning_greeting`、`sensor_aware`、`desktop_chat` |
| `user_id` | owner 或 QQ user id |
| `event_type` | `user_message` / `assistant_turn` / `sensor_snapshot` / `health_event` / `file_upload` / `manual_trigger` |
| `is_user_initiated` | 用户主动输入或上传 |
| `is_system_initiated` | scheduler/sensor/watch 主动触发 |
| `is_test` | 测试事件，不得写生产记忆 |
| `is_debug` | 调试事件，可以投递 UI，但默认不得影响关系记忆 |
| `auth_required` | 入口是否要求 Bearer/secret/本机白名单 |
| `can_write_memory` | 是否允许写 short_term/event_log/mid_term/episodic/growth |
| `can_affect_mood` | 是否允许更新 `mood_state` |
| `can_affect_garden` | 是否允许浇水或修改 harvest/vase |
| `can_trigger_reply` | 是否允许生成并发送 AI 回复 |
| `priority` | `low` / `normal` / `high` / `critical` |
| `fanout` | `all` / `qq` / `desktop` / `mobile` / 指定列表 |
| `behavior` | 下发给客户端的 action/overlay/notify metadata |
| `created_at` | 事件产生时间 |
| `observed_at` | 传感器观测时间，区别于后端接收时间 |
| `evidence_source` | 产生该事件的文件/函数/客户端版本 |

关键建议：

- `is_test=true` 或 `is_debug=true` 时，默认 `can_write_memory=false`、`can_affect_mood=false`、`can_affect_garden=false`。
- `sensor_snapshot` 默认只允许短期实时使用，除非明确给出 `privacy.allow_memory=true`。
- `watch/health` 事件应明确 `auth_required=true`，并在 secret 未配置时拒绝或限制为本机。
- 所有 assistant turn 最终都应有 `source + trigger_name + fanout + can_write_memory`，避免靠内容推断。
