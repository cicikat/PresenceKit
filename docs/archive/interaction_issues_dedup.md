# 三仓库互动链路 Issue 去重整理

> 审计日期：2026-05-19  
> 范围：`<desktop-client-root>`、`<mobile-client-root>`、`<repo-root>`
> 说明：本文件只做文档级去重，不修复代码。若同一根因在多个仓库出现，合并成一个主 issue，并把其他记录列入“重复来源”。
>
> **2026-05-31 更新说明**：本文保留 2026-05-19 的审计快照，不作为当前事实清单。此后已落地 WebSocket query token 与 access log 脱敏、Emerald-client Rust 端本地配置读取、桌面动作分发、叙事分段 `message_segments` 和 Dream 正式界面。当前通道实现以 `docs/channels.md` 为准，仍待处理的问题以 `docs/known-issues.md` 为准。
>
> **2026-06-02 P0 安全清场校准**：以下 issue 正文保留历史证据，但当前结论按本段覆盖：
> - Write Envelope v0 已落地：fail-closed；未 stamp 默认不写 memory / mood；`is_test` /
>   `is_debug` 强制不可写；sensor / watch 原始感知默认不写 profile。
> - QQ Dream Guard 已落地：`DREAM_ACTIVE` / `DREAM_CLOSING` 时 QQ owner 消息被拒，不进入
>   现实 pipeline，不写 runtime / memory。
> - Render Tag 已收口：QQ / mobile 输出移除 `<say>` 等展示标签；reality memory /
>   event_log 保存纯文本；desktop segments 保持原行为。
> - legacy `POST /desktop/trigger` 已确认零调用方并删除。
> - `data/chars` 生产代码字面量引用已归零，S6 layout 测试已通过。
> - 以上不等于完整权限系统、NMP、mood per-user、Dream 三模式、`policy.py` 接线或
>   sensor privacy 全系统已经完成。

## ISSUE-001：assistant turn 尚未完全统一出口

状态：已关闭主阻断；legacy `/desktop/trigger` 已删除，QQ LLM reply 已接入 turn_sink，冻结 `/chat` 已返回 410

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
`record_assistant_turn()` 已承接 QQ、desktop/mobile/scheduler/sensor_aware 的 LLM reply 写入；
QQ 可见发送仍由通道 adapter 负责，冻结管理面板 `/chat` 已返回 410。剩余差异主要是
QQ 是否 fanout 到 desktop/mobile，不再是记忆写入绕过。

证据：
- `<repo-root>\core\turn_sink.py:123`：新统一写入 + fanout 函数。
- `<repo-root>\admin\routers\chat.py:68`：desktop/mobile owner 入口使用 `record_assistant_turn()`。
- `<repo-root>\core\scheduler\loop.py:226`：scheduler 使用 `record_assistant_turn()`。
- `<repo-root>\main.py:290` / `:308`：QQ 主入口直接 `text_output.send()` 后 `create_task(post_process)`。
- `<repo-root>\docs\assistant-turn-sink.md`：已有“统一写入与广播”设计背景。

可能根因：  
Phase 1 turn sink 已部分落地，但 legacy/冻结入口没有全部迁移，QQ 主入口仍保留原始 pipeline 流程。

影响范围：
- 记忆
- 情绪
- 主动触发
- mobile
- desktop
- QQ
- broadcast

是否已有记录：  
重复来源：`<repo-root>\docs\assistant-turn-sink.md` 关于历史出口不统一的记录；主阻断已收口，保留 fanout 等后续架构差异。

建议处理方式：  
受控重构；需要人工拍板 QQ 是否同步广播到 desktop/mobile；
鉴权或迁移。`/desktop/trigger` 已删除。

推荐处理顺序：立即处理

## ISSUE-002：多个可写生产状态入口无鉴权或弱鉴权

状态：降级；Write Envelope v0 已阻断未 stamp 写 memory / mood，入口鉴权债仍保留

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
后端存在多个会写入真实记忆、profile、队列或活动快照的入口无 Bearer 鉴权。若管理服务监听到局域网或被转发，外部请求可触发真实对话、上传文件、写用户画像、写活动快照或发 QQ。

证据：
- `<repo-root>\admin\routers\chat.py:189`：`POST /desktop/chat` 无鉴权，走正常 pipeline 并写记忆。
- `<repo-root>\admin\routers\chat.py:207`：`POST /upload/ingest` 无鉴权，上传内容会拼入用户消息并写记忆。
- `<repo-root>\admin\routers\sensor.py:81`：`POST /sensor/push` 无鉴权，写 `user_profile` 的手机传感器摘要。
- `<repo-root>\admin\routers\sensor.py:248`：`POST /sensor/activity` 无鉴权，写 `activity_snapshot`。
- `<repo-root>\admin\routers\watch.py:164`：`watch_secret` 未配置时不校验。

可能根因：  
早期“本机/内网个人系统”假设被多个端复用后没有统一入口策略；desktop 被视为可信本机，但 mobile 和局域网调试让边界变宽。

影响范围：
- 记忆
- 情绪
- 花园
- 主动触发
- mobile
- desktop
- QQ
- 后台健康数据
- 鉴权

是否已有记录：  
重复来源：`<repo-root>\docs\security_model.md` 的未来 WebSocket/Mobile 安全风险；`<desktop-client-root>\docs\known-issues.md` 的 token 风险。本轮新增具体无鉴权入口列表。

建议处理方式：  
需要人工拍板。至少先给写记忆/写 profile/发 QQ 的入口补 Bearer 或本机白名单；`watch_secret` 未配置时不要默认放行。

推荐处理顺序：立即处理

## ISSUE-003：sensor/push 低频手机传感器会直接写真实 user_profile

状态：P0 写入污染已缓解；sensor 原始感知默认不写 profile，入口鉴权债仍保留

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
历史问题是 `POST /sensor/push` 接收 steps/battery/location/screen_sessions 后直接写
`phone_sensor_log` 和 `phone_sensor_today` 到真实用户画像。当前 Write Envelope v0 已使
sensor 原始感知默认不写 profile；无鉴权、设备可信度和完整 privacy 字段契约仍需另行处理。

证据：
- `<repo-root>\admin\routers\sensor.py:27`：`_save_sensor_to_profile(data)`。
- `<repo-root>\admin\routers\sensor.py:81`：`receive_sensor_data(body)` 无 `Depends(verify_token)`。
- `<repo-root>\admin\routers\sensor.py:118`：调用 `_save_sensor_to_profile(data)`。

可能根因：  
把低频手机传感器作为可信内网数据处理，没有 interaction contract 和测试隔离字段。

影响范围：
- 记忆
- mobile
- 后台健康数据
- debug/test
- 鉴权

是否已有记录：  
未在 `known-issues.md` 看到直接等价记录；
`<mobile-client-root>\docs\protocols\sensor-event-protocol.md` 已提出 `privacy.allow_memory`
草案，但未落地。

建议处理方式：  
小 patch + 文档补充。入口应鉴权；数据应带 source/device_id/is_test/privacy；profile 写入应受 `can_write_memory` 或 `allow_memory` 控制。

推荐处理顺序：立即处理

## ISSUE-004：实时 sensor 快照为全局 last-writer-wins，desktop/mobile 会互相覆盖

状态：仍需处理；与 Write Envelope v0 无关，仍是多端实时状态覆盖风险

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
`realtime_state` 只存一个 `_snapshot`，`POST /sensor/realtime` 来自桌面 Rust sensor 或 Android 无障碍上下文时都会整体替换。`sensor_events.tick()` 只读这一份全局快照，所以桌面键鼠和手机屏幕上下文可能互相覆盖，导致主动触发误判来源。

证据：
- `<repo-root>\core\memory\realtime_state.py:14`：单个 `_snapshot`。
- `<repo-root>\core\memory\realtime_state.py:18`：`update(payload)` 整体替换。
- `<repo-root>\core\scheduler\sensor_events.py:168`：`tick()` 读取 `realtime_state.get()`。
- `<desktop-client-root>\src-tauri\src\sensor\publisher.rs:43`：桌面 sensor 发布。
- `<mobile-client-root>\android\app\src\main\kotlin\com\example\mobile-client\MobileNotificationService.kt:173`：Android 后台推屏幕上下文到同一接口。

可能根因：  
实时状态从单客户端设计扩展到多端后，没有按 source/device 分桶。

影响范围：
- 主动触发
- mobile
- desktop
- 后台健康数据
- broadcast

是否已有记录：  
`<desktop-client-root>\docs\backend-integration.md` 已记录 `/sensor/realtime` 是“单字典内存覆盖，最后写入者赢”。本 issue 合并为跨仓库风险。

建议处理方式：  
受控重构。按 `source.device_type/client` 分桶，裁决时显式选择或合并快照；短期至少在文档标注 mobile/desktop 互斥风险。

推荐处理顺序：本轮重构处理

## ISSUE-005：屏幕上下文可能经 sensor_aware 回复进入长期记忆

状态：仍需处理；Write Envelope v0 已缩小写入面，但生成回复的 sensor privacy 边界未关闭

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
Android 无障碍会采集可见文字/可点击文字，后端将其传给 `sensor_judge` 和 `sensor_aware` prompt。虽然原始 realtime snapshot 不持久化，但如果 LLM 回复复述敏感屏幕内容，`record_assistant_turn(source=SENSOR)` 会写 assistant turn，并进入 mid_term/episodic/growth 链路。

证据：
- `<mobile-client-root>\android\app\src\main\kotlin\com\example\mobile-client\YexuanAccessibilityService.kt:43`：采集屏幕快照。
- `<repo-root>\admin\routers\sensor.py:182`：接收 `screen.visible_text/clickable_text`。
- `<repo-root>\core\scheduler\sensor_events.py:103`：构造 `screen_text_hint` / `screen_click_hint`。
- `<repo-root>\core\scheduler\sensor_judge.py:88`：将屏幕文本摘要写入裁决 prompt。
- `<repo-root>\core\scheduler\triggers\sensor_aware.py:471`：主动回复经 `record_assistant_turn()` 写入。
- `<repo-root>\core\pipeline.py:362`：所有 post_process 都 enqueue `summarize_to_midterm`。

可能根因：  
缺少 `privacy.tier`、`allow_memory` 和“敏感上下文不得复述/不得写记忆”的硬门。

影响范围：
- 记忆
- 主动触发
- mobile
- desktop
- debug/test

是否已有记录：  
`<mobile-client-root>\docs\protocols\sensor-event-protocol.md` 已建议 `ephemeral` 默认不进记忆；
当前代码未实现。

建议处理方式：  
需要人工拍板 + 小 patch。先在 sensor_aware prompt 和 turn sink 上引入 `can_write_memory=false` 或只写非敏感摘要；敏感 app/text 默认不送 LLM 或不允许回复复述。

推荐处理顺序：立即处理

## ISSUE-006：调试/测试主动行为没有顶层 is_test/is_debug 隔离

状态：降级；`is_test` / `is_debug` 强制不可写 memory / mood，mobile queue 展示隔离仍待核

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
手机端能力页可以通过 `/mobile/push` 投递行为测试消息，payload 内带 `prompt_kind=debug_test` 和 `cooldown_key=debug:*`，但后端 mobile queue item 没有顶层 `is_test/is_debug`。这些消息不会写记忆，但会进入真实 mobile queue，被前台/后台当普通主动消息消费。

证据：
- `<mobile-client-root>\lib\main.dart:1703`：`pushMobileBehaviorTest()`。
- `<mobile-client-root>\lib\main.dart:1725`：写 `prompt_kind: debug_test`。
- `<repo-root>\admin\routers\mobile.py:68`：`POST /mobile/push`。
- `<repo-root>\channels\mobile.py:75`：queue item 只写 `id/content/user_id/timestamp/behavior`。

可能根因：  
debug metadata 属于客户端私约定，没有提升为后端统一策略字段。

影响范围：
- mobile
- debug/test
- broadcast

是否已有记录：  
`<mobile-client-root>\README.md` 记录能力检查页测试能力；未见隔离风险记录。

建议处理方式：  
文档补充 + 小 patch。`/mobile/push` 接收并保存 `is_test/is_debug`，客户端 UI 明确标注；测试消息默认不参与普通通知冷却统计或有专用调试通道。

推荐处理顺序：本轮重构处理

## ISSUE-007：Watch 健康入口 secret 可为空导致不鉴权

状态：降级；Write Envelope v0 已阻止原始感知默认写 profile，入口鉴权风险仍保留

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
`_watch_secret()` 未配置时返回空字符串，`receive_watch_event()` 只有在 expected 非空且不匹配时才拒绝。结果是 watch_secret 缺省时，任何请求都可提交心率/睡眠事件并触发高优先级主动消息。Write Envelope v0 已使 watch 原始感知默认不写 profile；鉴权和生成回复边界仍需单独处理。

证据：
- `<repo-root>\admin\routers\watch.py:164`：未配置返回空字符串。
- `<repo-root>\admin\routers\watch.py:190`：只有 `expected and secret != expected` 时拒绝。
- `<repo-root>\admin\routers\watch.py:251`：心率事件写 profile。
- `<repo-root>\core\scheduler\triggers\watch.py:8`：watch 事件可触发主动发言。

可能根因：  
iPhone 捷径易用性优先，安全默认值偏宽。

影响范围：
- 记忆
- 情绪
- 主动触发
- 后台健康数据
- 鉴权

是否已有记录：  
`<repo-root>\AAWatch配置指南.md` 记录配置方式；未见“未配置即开放”的风险记录。

建议处理方式：  
小 patch。未配置 secret 时拒绝外部写入，或只允许 localhost；文档明确 watch_secret 必填。

推荐处理顺序：立即处理

## ISSUE-008：客户端硬编码 admin token

状态：**已解决**（SEC-AUTH-2 token 注册表 + 各端连接设置页上线后）

严重度（历史）：P1：多端状态不一致、重复写入、触发误判

问题描述（历史）：  
Emerald-client、Flutter 前台和 Android 后台服务曾均硬编码 `Emerald1231`。token 改动会导致多端失联；源码暴露也不适合长期使用。旧字符串仍留存于 desktop/mobile 的 **git 历史**中（工作区源码已清除），开源前需确认该值在当前部署上已不再是有效的 admin secret。

现状：  
后端改为 `data/runtime/auth/tokens.yaml` token 注册表（label/hash/scopes，见 `<repo-root>\admin\token_registry.py`）。各客户端不再硬编码 token：
- desktop：连接设置页 → `client.local.json`（`<desktop-client-root>\src\shared\api\connectionSettings.ts`）。
- mobile Flutter：设置页 → 本地存储（`<mobile-client-root>\lib\pages\app_shell.dart` `_settingsStore.loadAdminToken()`）。
- mobile Android 后台服务：`SharedPreferences`（`<mobile-client-root>\android\app\src\main\kotlin\com\example\yexuan_memery\BackendSecurityPolicy.kt`）。

影响范围：
- mobile
- desktop
- 鉴权

重复来源：`<desktop-client-root>\docs\known-issues.md` 的 “P2：admin token 硬编码”；`<desktop-client-root>\docs\backend-integration.md`（这两处开源前也需同步标注为已解决）。

## ISSUE-009：桌面 WS action 会成功 ack 但未执行

状态：已有记录

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
Emerald-client 收到 `action` 后立刻回 `ok:true`，但没有真正执行 `open_url/minimize_window/notify/pet_emote/execute`。后端会认为桌面动作成功，角色可能在下一轮基于“已执行”继续互动。

证据：
- `<desktop-client-root>\src\shared\api\ws.ts:91`：收到 `action`。
- `<desktop-client-root>\src\shared\api\ws.ts:93`：立即 ack `ok:true`。
- `<repo-root>\channels\desktop.py:47`：后端等待 action ack。
- `<repo-root>\core\tool_dispatcher.py:176`：桌面工具等待 ack 成功即返回 ok。

可能根因：  
legacy WS 协议先接通，action executor 未迁入新 Tauri 客户端。

影响范围：
- desktop
- broadcast
- 主动触发

是否已有记录：  
重复来源：`<desktop-client-root>\docs\known-issues.md` “P1：WebSocket action 会回成功 ack，但没有执行动作”。

建议处理方式：  
小 patch。未实现 executor 前 ack `ok:false`，或按 capabilities 告诉后端 action 不可用。

推荐处理顺序：立即处理

## ISSUE-010：desktop/mobile/QQ 客户端协议与字段不统一

状态：已有记录 / 新发现合并

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
desktop WS 使用 `channel_message/action` legacy 协议；mobile queue 使用 `id/content/user_id/timestamp/behavior`；HTTP chat 返回 `reply/emotion/affection/level/turn_id?`；QQ 直接发文本，不消费 channel envelope。缺少统一字段导致去重、来源、priority、trigger、behavior、test/debug 等信息在不同端表现不一致。

证据：
- `<desktop-client-root>\src\shared\api\ws.ts:87`：desktop `channel_message`。
- `<repo-root>\channels\mobile.py:82`：mobile queue item schema。
- `<repo-root>\admin\routers\chat.py:80`：desktop/mobile chat response 增加 `turn_id/critical_written`。
- `<repo-root>\core\output\text_output.py:17`：QQ 直接文本发送。

可能根因：  
三端分阶段接入，后端已有 turn sink 但尚未形成统一 Interaction Contract。

影响范围：
- mobile
- desktop
- QQ
- broadcast
- debug/test

是否已有记录：  
重复来源：`<desktop-client-root>\docs\known-issues.md` 的 v1 WS 协议不一致、HTTP `/desktop/chat` 仍在使用；本轮补充 mobile/QQ 字段不统一。

建议处理方式：  
需要人工拍板。先文档确认 legacy 过渡期字段，再设计 v1 envelope。

推荐处理顺序：本轮重构处理

## ISSUE-011：mobile_queue 缺少 trigger/priority/ttl，后台通知无法区分紧急程度

状态：已有记录 / 新发现合并

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
后端写入 mobile queue 时只保存基础字段和可选 behavior，普通 scheduler 主动消息没有 `trigger`、`priority`、`ttl`。手机端 README 已指出当前主动消息最高只按普通通知处理，无法根据心率/生日/普通碎碎念等来源决定通知强度。

证据：
- `<repo-root>\channels\mobile.py:82`：queue item 字段。
- `<mobile-client-root>\README.md` “当前 `mobile_queue` 只有 `content/user_id/timestamp`，没有优先级或 trigger 元数据”。
- `<mobile-client-root>\android\app\src\main\kotlin\com\example\mobile-client\MobileNotificationService.kt:274`：后台仅按 behavior/通知闸门处理。

可能根因：  
mobile channel 先实现主动消息文本投递，未同步后端 trigger 元数据。

影响范围：
- mobile
- 主动触发
- 后台健康数据
- broadcast

是否已有记录：  
重复来源：`<mobile-client-root>\README.md` 通知与主动消息部分。

建议处理方式：  
小 patch。`MobileChannel.send()` 支持 trigger/priority/ttl；`turn_sink` fanout 时透传。

推荐处理顺序：本轮重构处理

## ISSUE-012：slow_queue 不持久化，进程退出会丢失总结/反思/成长任务

状态：已有记录 / 待确认

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
关键写入已同步，但 mid_term、episodic、growth、consistency_check、profile_update 等慢任务在内存队列中。进程退出会丢失未执行慢任务，导致下一轮上下文缺少中期/长期归纳。文档称“不持久化队列（进程退出丢失，有意设计）”，但在跨端主动触发增多后风险升高。

证据：
- `<repo-root>\core\post_process\slow_queue.py:31`：`enqueue()` 只进内存队列。
- `<repo-root>\core\post_process\slow_queue.py:91`：单 worker。
- `<repo-root>\ARCHITECTURE.md` Pipeline post_process 段记录“不持久化队列（进程退出丢失，有意设计）”。

可能根因：  
关键路径响应速度优先，慢任务可靠性未升级为持久队列。

影响范围：
- 记忆
- 主动触发
- debug/test

是否已有记录：  
架构文档已有行为记录，但未作为 issue；本轮作为风险项收敛。

建议处理方式：  
需要人工拍板。短期保留设计但加入观测；中期可把 slow_queue task 持久化或在 event_log 上做补偿扫尾。

推荐处理顺序：后续处理

## ISSUE-013：花园写入缺少 safe_write 和专用锁（已修复）

状态：已修复

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
历史问题是花园 `plants.json/storage.json` 存在多条写路径，并发时可能覆盖。当前
`manager.py` 已使用 `threading.RLock()` 与 `safe_write_json()`。

证据：
- `<repo-root>\core\garden\manager.py:30`：`_save()` 使用 `path.write_text()`。
- `<repo-root>\core\garden\manager.py:170`：自动浇水。
- `<repo-root>\core\garden\manager.py:191`：被动浇水。
- `<repo-root>\core\garden\manager.py:203`：每日扫描。

可能根因：  
花园先作为伴生状态快速落地，尚未接入记忆系统同级别的锁和原子写。

影响范围：
- 花园
- 主动触发

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` G2；`<repo-root>\docs\garden.md` 当前边界。

建议处理方式：  
已完成，无需继续作为当前 issue 排期。

推荐处理顺序：已完成

## ISSUE-014：花园 dry/gift/ask 分支仍留在 harvest，可能二次过期

状态：已有记录

严重度：P2：文档不一致、维护困难、轻微行为异常

问题描述：  
`daily_check()` 中 `vase` 会把花移出 harvest，但 `dry/gift/ask` 只标记状态或 note，仍留在 harvest。之后过期扫描可能再把同一朵花按 `harvest_expired` 处理。

证据：
- `<repo-root>\core\garden\manager.py:224`：遍历 harvest handle。
- `<repo-root>\core\garden\manager.py:238`：`dry` 仅改 `status=dried`。
- `<repo-root>\core\garden\manager.py:249`：`gift` 仅写 `gifted_note`。
- `<repo-root>\core\garden\manager.py:257`：只有 `harvest_to_remove` 被移除。

可能根因：  
harvest 生命周期容器设计未定。

影响范围：
- 花园
- 主动触发

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` G4；`<repo-root>\docs\garden.md` 当前边界。

建议处理方式：  
需要人工拍板。明确 dry/gift/ask 最终状态容器。

推荐处理顺序：后续处理

## ISSUE-015：fetch_context 读写竞态仍存在，跨端输入增多会放大

状态：已有记录

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
`fetch_context()` 不持 `uid_lock`，已知用户极短时间连发时可能读到上一轮尚未 capture 的旧状态。desktop/mobile owner 入口现在用 `conversation_lock` 包住整个 turn，但 QQ 主入口只靠 message_queue 按 QQ session 串行；scheduler/trigger 和 QQ/user 输入之间仍需看 `turn_sink` 覆盖情况。

证据：
- `<repo-root>\core\pipeline.py:73`：`fetch_context()` 读取多层记忆。
- `<repo-root>\docs\known-issues.md` B11：明确记录 fetch_context 读写竞态。
- `<repo-root>\admin\routers\chat.py:45`：owner 入口使用 `conversation_lock`。
- `<repo-root>\main.py:308`：QQ post_process 异步。

可能根因：  
为了响应速度，没有给 fetch 阶段加 uid_lock；新旧入口串行策略不一致。

影响范围：
- 记忆
- 情绪
- mobile
- desktop
- QQ

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` B11。

建议处理方式：  
受控重构。先迁移所有入口到 `turn_sink`/conversation gate，再评估是否需要 fetch 阶段锁或 per-uid 输入队列。

推荐处理顺序：本轮重构处理

## ISSUE-016：核心情景记忆上限裁剪未保护 is_core（已修复）

状态：已修复

严重度：P0：可能污染生产记忆、安全鉴权、真实关系状态

问题描述：  
`write_episode()` 自动写入超过上限时只从非核心记忆删除低 strength 条目；
`is_core=True` 已排除在自动上限裁剪之外。其写入也已受 Write Envelope 写入权限保护。

证据：
- `<repo-root>\docs\known-issues.md` B12。
- `<repo-root>\docs\memory.md` 情景记忆上限段说明当前裁剪保护。

可能根因：  
核心记忆保护只在部分清理路径实现，自动写入裁剪路径未同步。

影响范围：
- 记忆
- 主动触发

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` B12。

建议处理方式：  
已完成，无需继续作为当前 issue 排期。

推荐处理顺序：已完成

## ISSUE-017：部分 data/debug 路径仍绕过 sandbox（已修复）

状态：已修复

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
历史问题是少量运行模块仍使用 legacy `Path("data/...")`。当前已核对关闭：列出的运行模块
已走 `get_paths()`；剩余硬编码是 DataPaths 内明确的 authored-content fallback。

证据：
- `<repo-root>\docs\known-issues.md` S1。
- `<repo-root>\core\llm_output_validator.py:8`：`_DEBUG_DIR = Path("data/debug/llm_output")`。
- `<repo-root>\core\sandbox.py:33`：test 模式应进入 `data/test_sandbox/{session}`。

可能根因：  
早期 debug/配置类路径未纳入 DataPaths。

影响范围：
- debug/test
- 记忆

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` S1；本轮补充具体 debug 路径。

建议处理方式：  
已完成，无需继续作为当前 issue 排期。

推荐处理顺序：已完成

## ISSUE-018：memory 类工具已注册但正式主 LLM 不能自动调用

状态：已有记录

严重度：P2：文档不一致、维护困难、轻微行为异常

问题描述：  
`read_diary/read_watch/search_diary/get_profile/get_episodic` 已注册（`get_growth` 已随 Brief 35 删除），但探针只覆盖 `info + desktop`，主 LLM 没有工具调用回合。Author's Note 中若要求调用 diary/watch 工具，当前没有真实执行通道。

证据：
- `<repo-root>\docs\known-issues.md` F11。
- `<repo-root>\docs\tools.md` memory 类工具说明。
- `<repo-root>\main.py:229`：探针只取 `categories=["info", "desktop"]`。

可能根因：  
工具注册表先扩展，正式工具回合未接入。

影响范围：
- 记忆
- 后台健康数据
- QQ
- mobile
- desktop

是否已有记录：  
重复来源：`<repo-root>\docs\known-issues.md` F11。

建议处理方式：  
需要人工拍板。二选一：正式 LLM 工具回合，或把允许的 memory 工具加入 pre-pipeline 探针。

推荐处理顺序：后续处理

## ISSUE-019：客户端侧高危动作边界依赖行为字符串推断

状态：新发现 / 待确认

严重度：P1：多端状态不一致、重复写入、触发误判

问题描述：  
手机端后台通知会根据 `kind/delivery/level/behavior_id` 字符串推断 lock/order/overlay。虽然当前锁屏和外卖都保留确认，但后端没有统一 `allowed_actions/blocked_actions/risk/requires_confirmation` 的强 schema，普通 behavior 字段只要包含 lock/order 等词就会进入高强度 UI。

证据：
- `<mobile-client-root>\android\app\src\main\kotlin\com\example\mobile-client\MobileNotificationService.kt:298`：`overlayRequestFor()`。
- `<mobile-client-root>\lib\main.dart:2288`：前台 `_handleForegroundBehavior()` 也按 kind/behaviorId 推断。
- `<repo-root>\core\scheduler\triggers\sensor_aware.py:263`：后端 action packet 仅 `{action_type, params}`。

可能根因：  
行为 metadata 仍处于过渡版和阶段 6/7 版并存，没有固定 contract。

影响范围：
- mobile
- 主动触发
- 鉴权

是否已有记录：  
`<mobile-client-root>\README.md` 记录手机端执行边界；未见作为 issue 去重。

建议处理方式：  
需要人工拍板。定义 behavior schema，强制 `requires_confirmation/risk/allowed_actions`，客户端不得仅靠字符串升级能力。

推荐处理顺序：本轮重构处理

## ISSUE-020：重复 `/chat` 路由存在鉴权语义混乱

状态：新发现 / 待确认

严重度：P2：文档不一致、维护困难、轻微行为异常

问题描述：  
`admin/routers/chat.py` 中有两个 `@router.post("/chat")`：`frontend_chat()` 带 `verify_token`，`unified_chat()` 没有显式鉴权。FastAPI 对重复 path/method 的实际匹配顺序需要代码运行确认，但文档和维护层面已经存在明显歧义。

证据：
- `<repo-root>\admin\routers\chat.py:140`：`frontend_chat(body, auth=Depends(verify_token))`。
- `<repo-root>\admin\routers\chat.py:322`：`unified_chat(request, body=Body(...))`。

可能根因：  
旧入口叠加，新旧接口没有清理或改名。

影响范围：
- desktop
- mobile
- QQ
- 鉴权
- 文档一致性

是否已有记录：  
未在 known-issues 中看到直接记录。

建议处理方式：  
小 patch，但需人工确认哪个 `/chat` 是权威入口。至少文档标注冻结/废弃，或改路径/补鉴权。

推荐处理顺序：本轮重构处理

## 去重摘要

本轮共整理 20 个主 issue。

新发现：
- ISSUE-002、003、004、005、006、007、019、020

已有记录合并：
- ISSUE-001、008、009、010、011、013、014、015、016、017、018

待确认/设计取舍：
- ISSUE-012、019、020

2026-06-02 P0 清场后的当前摘要：

1. ISSUE-002：Write Envelope v0 已缓解未 stamp 写入污染；无鉴权/弱鉴权入口仍需单独收口。
2. ISSUE-005：未 stamp 默认不写 memory / mood，原始感知默认不写 profile；生成回复的
   sensor privacy 边界仍需补齐。
3. ISSUE-001：`/desktop/trigger` 已删除；QQ 与冻结 `/chat` 的统一出口债仍在。
4. ISSUE-007：Watch secret 可为空的鉴权风险仍需单独处理。
5. ISSUE-003：sensor 原始感知默认不写 profile；入口鉴权和完整 privacy 字段契约仍未完成。

不建议 Codex 自动修改：
- ISSUE-001：涉及 QQ 是否多端广播、legacy 入口去留，需要 Claude/用户定策略。
- ISSUE-002/007：安全默认值会影响现有手机、桌面、Watch 调试方式，需要先定兼容策略。
- ISSUE-005/019：涉及隐私边界和主动行为产品定义，需要人工拍板。
- ISSUE-020：重复 `/chat` 可能有历史依赖，删除或改名前要确认调用方。
