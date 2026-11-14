# 工单 #310｜PresenceKit-mobile 聊天连续性、传感器权限入口与中继状态降噪

> 类型：Bugfix + 交互一致性 + 最小协议补充  
> 优先级：P1（历史阅读位置、发送后恢复、生成中状态）；P2（设置页可发现性、中继通知降噪）  
> 涉及仓库：`cicikat/PresenceKit-mobile`（主）与 `cicikat/PresenceKit`（必要的只读请求状态接口）  
> 建议分支：`fix/mobile-experience-310`；PR 标题：`fix(mobile): preserve chat viewport and recover turn presence`  
> 审查基础：2026-11-14 GitHub `main` 源码静态审查 + 用户真机截图及复现描述。**不是已完成的真机日志复现**；实际安装 APK/当前本地未提交改动可能与 GitHub main 不同，执行前须核对版本、commit 和差异。  
> 不与 #309 Kotlin 编译缓存修复耦合；不改角色卡、Prompt、记忆、Dream 和现有业务数据。

## 0. 用户感知的完成标准

1. 用户正在看几屏甚至几天之前的历史消息时，即使新回复、工具最终结果、历史补页、发送状态、断线重连、进出后台同时发生，**屏幕上原本正在看的同一条消息及其屏幕内坐标应保持稳定**，绝不能向下滑动一截。新消息通过一个轻量的「新消息」提示呈现；用户点击或自己滚到底才进入跟随模式。
2. 发送消息后切后台/退出页面，再回前台：服务端若**可确证**请求仍在执行，角色气泡有连续、低调的 `…` / 三点动态输入状态。结束后直接过渡为正式回复，不闪「系统处理中」文本。若状态**不可判定**，不伪装成仍在生成：在用户自己的消息旁低调提示「等待确认」，保留人工核对动作，不自动重发。
3. 「查询投递结果」「移出本机队列」不能以大块醒目的工程化 UI 打断沉浸；仍必须保留安全投递语义与失败恢复能力，避免重复发送、误删、丢消息。
4. 设置页存在明确可找到的「设备感知与权限」入口，列出**实际已经实现**的传感器/权限/上报能力，展示真实状态、授权跳转、开关及失败原因；不支持的项目说明原因，不能整块消失。
5. 「中继已连接」不因短暂重连或前后台切换反复弹出/刷新常驻通知。真正收到角色新消息时，仍正常通知；不能通过关掉重要通知或后台保活“修复”。

## 1. 源码核验发现（区分事实与待验证）

### 1.1 历史聊天轻微下移（P1）

已确认的相关代码：

- [`lib/controllers/chat_controller.dart` 1360–1427](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/chat_controller.dart#L1360-L1427)：`loadOlderHistory()` 在插入历史后用 `newMaxScrollExtent - oldMaxScrollExtent + oldPixels` 补偿。
- [同文件 2336–2359](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/chat_controller.dart#L2336-L2359)：`_revealOlder()` 对隐藏旧消息展开也使用同一总高度差算法。
- [同文件 1937–1966](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/chat_controller.dart#L1937-L1966)：`_followIncoming()` 通过 `extentAfter <= 32` 判断是否跟随，帧回调再 `jumpTo(maxScrollExtent)`；有保护但不是统一的用户滚动意图管理。
- [`lib/widgets/chat_scene.dart` 162–205](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/chat_scene.dart#L162-L205)：聊天列表顶部的 `metaItems` 随 sending/history/error/mobile 状态增减；这些行本身就会改变列表高度。`ListView.builder` 当前主要按 index 取条目，虽有部分 `ValueKey`，但缺乏全局稳定可视锚点补偿。
- [`lib/widgets/edge_refresh.dart`](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/edge_refresh.dart) 存在首尾下拉刷新层；截图中底部「正在刷新新消息」/ spinner 应作为刷新状态对待，而不是角色生成状态。

**高可信根因候选**：只用全列表最大滚动高度差补偿 *顶部补页*，会把同时追加的底部消息/动态 meta 高度也计入，导致阅读位置产生可见位移；加之多条入口独立执行滚动，保护存在竞态。必须通过交错时序 widget/integration test 证实具体是哪一条触发用户的“下移一截”，不要只调整 32px 阈值。

### 1.2 退出回来时三点动画消失、Outbox 技术提示出戏（P1）

已确认：

- [`lib/widgets/chat_widgets.dart` 985–1077](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/chat_widgets.dart#L985-L1077) 已有 `TypingHimMessage` + `JumpingDots`；并非缺失 UI 组件。
- [`lib/widgets/chat_scene.dart` 121–205、298–310](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/chat_scene.dart#L121-L310) 仅 `controller.himTyping` 为 true 才放入角色输入气泡，同时 `sending` 在列表顶部展示 `chatWaitingReply`（现文本「已送到后端 · 正在等他回话」）。
- [`lib/controllers/chat_controller.dart` 960–1078](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/chat_controller.dart#L960-L1078)：`_executeOutgoing()` 在当前 HTTP 生命周期中设 `himTyping=true`，`finally` 中置 false；`_restoreOutbox()` 868–885 会把此前 `sending` 的未完成本机记录恢复为 `outcomeUnknown`。
- [`lib/services/backend_client.dart`](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/services/backend_client.dart) `sendChat()` 直接等待 `/mobile/chat`，HTTP timeout 为 120 秒。长工具调用可能超过 HTTP 等待期限，但**这不等同于后端停止执行**。
- [`lib/widgets/chat_scene.dart` 87–119、435–455](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/chat_scene.dart#L87-L119) 把 `outcomeUnknown` 显示为 `chatOutboxUnknown`；[`lib/l10n/app_zh.arb`](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/l10n/app_zh.arb) 定义了「投递结果未知」「查询投递结果」「移出本机队列」，截图确实对应这些安全状态，而非角色台词。
- 后端 [`core/session_scope.py` 202–270](https://github.com/cicikat/PresenceKit/blob/main/core/session_scope.py#L202-L270)：`execute_request()` 管理 `_RUNNING`、`_REQUESTS`（running/completed/failed/retryable_failed）并以 `asyncio.shield()` 等待 task，HTTP 客户端结束不能直接证明执行已停止。**当前手机 API 没有与该请求台账配套的只读实时状态查询接口。**
- 后端 [`docs/mobile-realtime-r2.md`](https://github.com/cicikat/PresenceKit/blob/main/docs/mobile-realtime-r2.md) 与手机 [`lib/controllers/mobile_realtime_controller.dart`](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/mobile_realtime_controller.dart) 明确 `final_only`：只传持久化完成的 final，没有「正在执行工具/正在生成」帧；不能直接把 WS 连通误判为生成中。

**根因**：角色生成指示绑定当前前台 HTTP/临时内存状态；App 恢复后只拿到本地不确定投递记录和最终完成事件，缺中间执行态的可靠证据。大块调试性 Outbox 操作虽有安全价值，但直接出现在对话视觉主轴上。

### 1.3 设置页面功能入口松散（P2）

已确认：

- [`lib/widgets/settings_widgets.dart` 226–311](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/settings_widgets.dart#L226-L311)：「权限与功能」只用单条 ListTile 导向 CapabilitySheet；旁边有 `ScreenObservationSettings`，没有统一「传感器/设备感知」总览。
- [`lib/pages/app_shell.dart` 1074–1075、1134–1144](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/pages/app_shell.dart#L1074-L1144) 权限入口 `onOpenSystemControls` 进入 `controlsOnly=true`，另有普通能力检查入口；两种模式 UI 不一致。
- [`lib/widgets/capability_widgets.dart` 285–385](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/capability_widgets.dart#L285-L385)：多项授权动作只在 `controlsOnly` 时可点；`AmbientPermissions` 也只在 `controlsOnly && onAmbientChanged != null` 展示；另一模式下屏幕观察只读。
- [`lib/widgets/ambient_permissions.dart` 40–75](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/widgets/ambient_permissions.dart#L40-L75)：平台通道不可用或读取失败时，`_settings == null` 就 `SizedBox.shrink()`，用户无法知道为何传感器消失。
- [`lib/controllers/device_controller.dart`](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/controllers/device_controller.dart) 已有电量、步数、环境采样、屏幕上下文上报逻辑；[`android/app/src/main/AndroidManifest.xml`](https://github.com/cicikat/PresenceKit-mobile/blob/main/android/app/src/main/AndroidManifest.xml) 已声明 `ACTIVITY_RECOGNITION`、`PACKAGE_USAGE_STATS` 等权限。**权限存在不代表已经授权、启用或者后端实际收到数据。**

### 1.4 中继已连接反复跳（P2）

已确认：

- [`android/.../MobileNotificationService.kt` 229–275](https://github.com/cicikat/PresenceKit-mobile/blob/main/android/app/src/main/kotlin/com/presencekit/mobile/MobileNotificationService.kt#L229-L275)：每次 SSE 建立并执行 `activateRelayConsumer()` 都调用 `updateForegroundNotification("中继已连接")`。
- 同文件 `updateForegroundNotification()` 每次调用都会 `NotificationManager.notify(foregroundId, buildForegroundNotification(text))`，未见状态相同不更新的短路去重。
- [`lib/models/background_status.dart` 32–97](https://github.com/cicikat/PresenceKit-mobile/blob/main/lib/models/background_status.dart#L32-L97) 已有展示层 20 秒 reconnect debounce / 3 分钟 heartbeat freshness，但这个展示去抖**不覆盖 Android 原生通知更新路径**。
- [`android/.../NotificationChannels.kt`](https://github.com/cicikat/PresenceKit-mobile/blob/main/android/app/src/main/kotlin/com/presencekit/mobile/NotificationChannels.kt) 的常驻服务频道已是 `IMPORTANCE_MIN`、无声/无振动；用户依然感知反复跳，具体是系统通知更新、厂商 ROM 展示还是实际重连频繁，还需真机证据。
- [`android/.../MainActivity.kt` onResume/onStop](https://github.com/cicikat/PresenceKit-mobile/blob/main/android/app/src/main/kotlin/com/presencekit/mobile/MainActivity.kt#L65-L103) 前台停止 native 服务，退后台在未启用 FCM 且满足条件时才启动原生中继；**目前未证明存在两条同时消费的旧逻辑**。请记录 FCM、SSE、后台补偿三者的实际启停后再决定是否删除旧代码。

## 2. 实施任务（须在同一工单里落地）

### A. 稳定历史阅读锚点（P1）

- 在 `ChatScene` / `ChatController` 的历史分页、隐藏消息展开、final 接入、HTTP/WS/原生补读、头部 meta 状态变化间建立**统一滚动策略**，按「是否由用户主动进入跟随底部模式」区分，避免仅依据一次 `extentAfter` 猜测阅读意图。
- 非跟随模式：保存当前视口第一条实际可见**稳定消息 ID/turn ID + 相对屏幕偏移**，变更布局后恢复该锚点；禁止直接用 `newMax-oldMax` 混算前后插入。包括前部增删 meta、字体/换行、历史补页、消息去重/回填后的锚点失效回退。
- 跟随模式：用户发消息、明确点击「最新」、主动滚到底、首次空列表定位时才随新消息滚到底；新消息的角色三点气泡自身展示不可把历史读者拖离锚点。
- 将 `scrollToBottom`、`_followIncoming`、`_revealOlder`、`loadOlderHistory`、`_readHistory`、`EdgeRefresh` 统一到同一 coordinator 或具名可测试策略；防止 post-frame 旧任务跨代生效，保留离散化去抖与 cancel/epoch 处理。
- 视觉层的 sending/history/network 状态不要插入导致 scroll index 扰动的动态顶部行；迁移到独立、不参与历史项索引的状态区域，或给这些行做显式锚点补偿。
- 大量消息性能必须可接受；不要简单把全量聊天改成 `SingleChildScrollView`。

### B. 恢复可确证的 Agent 在场/输入中状态（P1；可跨两仓）

- 调查现有 `core/session_scope.py` 的请求台账与 `mobile_finals` 最终投影，优先最小化复用，不新增第二套会导致状态分叉的业务真相源。
- 必要时后端新增**只读** `GET /mobile/chat/requests/{request_id}`（或等价的现有 scoped endpoint 扩展），要求有效 Bearer + `X-Presence-Session`，严格绑定 owner、char、domain、session、request_id；响应只包含安全枚举如 `running/completed/failed/retryable_failed/unknown`、可选已有 turn_id/时间，不返回 prompt、工具参数、thinking、模型 token、他人会话、隐私数据。未匹配严格返回 unknown/404，不提供按请求 ID 枚举。
- 后端执行重试、重启及内存台账失效必须有清晰状态语义；只有服务端确定 running 时展示三点动画。若支持从持久最终投影找回完成，必须以 `request_id` 与 scope 对账，避免跨角色误匹配。禁止借查询接口触发重跑或第二次工具执行。
- 客户端应用恢复时按当前 owner/角色/session 读取属于本端的未决 request_id 并轻量查询，`running` => `TypingHimMessage` 三点（或角色一致的轻量动画）；收到 WS/HTTP/历史 final => 原子关闭 typing、移除不确定投递、呈现回复。`completed` 但结果未到则调用现有只读补读，不再次发送。
- `unknown` 不等于 `running`；超时/断网/离线以低调中性的未确证状态表示，后台不轮询，前台退避与预算有限（如 2s→5s→10s，过期停止）；请求已失败应能安全人工重试，但须沿原 request_id/幂等协议且判断 retry-safe。
- 同时有后台主动消息、其他设备消息和自己的未决发送时，绝不能把无关 final 当成本端发送结束；WS generation、identity、scope 及 `request_id` 都需核对。
- `final_only` 协议如果仍不传中间执行事件，保持原设计即可；本单优先可验证的只读状态。不要为了三点效果引入原始 LLM token streaming 或暴露工具内部日志。

### C. 对话层去技术化，保留可恢复性（P1）

- 对话只显示「我」与角色真正发言、属于角色的低调三点状态和必要轻量新消息提示。系统技术状态、网络错误、后台补读进度、工具/Outbox 调试信息迁至可折叠的状态详情/设置诊断，不污染对话时间线。默认不要在顶部追加 `chatWaitingReply`、`chatMobileReceived`、`chatBackendStatus` 这类行导致跳动。
- 本机消息不确定、失败与排队状态只能在**对应用户消息局部**显示含义准确的极简图标/文案，例如「待确认」「发送失败」。点开详情才露出「查询投递结果」「移出本机队列」等高级操作，并说明删除仅清本机队列，不代表撤回服务器请求；不可静默删除或自动重发。
- 不把 `reasoning`、`toolActivity` 当作角色说话；允许用户自行开启现有调试可视化，但默认不反复弹工程提示。不要更改或伪造原始角色文本、系统证据。
- `EdgeRefresh` 的「正在刷新消息」应是短暂轻量且明确的主动刷新回馈，不占据大面积聊天正文；退后台/回来自动补读不冒充用户手势刷新。刷新反馈不要遮挡输入框，错误不能无限转圈。
- 三点气泡应只属于角色，和其头像/主题/聊天气泡一致，不能出现一个笼统的系统 loading 文本；动画勿导致列表高度每帧变化。

### D. 设置→设备感知与权限闭环（P2）

- 在现有系统设置内提供一级可见「设备感知与权限」入口，能看到至少：电池、运动/步数、环境光、运动状态、使用情况访问、屏幕上下文、按需屏幕观察（及其无障碍依赖）、通知/后台接收。按已实现能力逐项检索真实状态；不要凭空增加 GPS、麦克风常驻监听等未实现功能。
- 每项明确区分：**硬件/系统是否支持 → OS 权限 → 本机启用 → 后端允许及可达 → 最近一次成功上报/错误**。无权限时显示按钮发起系统授权或打开对应设置；跳回前台刷新状态。开关启用不代表授权成功，不能显示假阳性。
- `AmbientPermissions` 原生通道不可用时禁止 `SizedBox.shrink()` 静默消失：展示可用性/失败原因/重试或系统设置路径；`ScreenObservationSettings` 同理显示 `supported` 与 accessibility 实态，明确本地授权与后端全局策略的关系。
- 审计 `controlsOnly=true/false` 两种 CapabilitySheet，消除「普通页只读、控制页可点」造成的误解：至少从设置入口可达所有可授权选项；诊断模式可更详细，但不能变成唯一能操作的隐藏入口。
- 用户必须主动授权/开启敏感感知，默认遵循现有安全策略；不做后台偷偷请求权限、自动截图或将原始传感器内容写入角色记忆。

### E. 中继连接状态去抖和通知责任梳理（P2）

- 使用 `adb logcat` + native service 诊断跟踪：SSE connect/disconnect/retry 计数、每次 `NotificationManager.notify` 原因、前后台事件、FCM enabled、ntfy SSE 服务、fallback poll。注意日志不输出 token、topic 密钥、正文、设备私有信息。对比实际是否发生重连或仅通知被重复刷新。
- 对 native 常驻通知建立 `lastRenderedState / lastRenderedText` 去重；同样文字/无意义 SSE 瞬断时不重复 `notify`，保留短暂重连宽限并防止已连接⇄连接中频繁切换；若连接确实长期失效，应如实显示而非永远假装已连接。
- `NotificationChannels` 已将 keepalive 设为 `IMPORTANCE_MIN` 且静音，不要再误改消息频道的 `IMPORTANCE_HIGH`：**角色真正主动发消息的可见通知必须保留**。尊重 Android 8+ 通道重要性由用户控制的规则，不为了自认为“降噪”强行删除重建用户自定义频道。
- 审查原生 `MainActivity` 前后台启动停止、FCM 与 ntfy 的接收所有权、补偿轮询边界。只删除确实无调用且覆盖在 CI 的旧路径；不能根据 UI 重复通知推断已存在重复消费。
- 稳定性展示要区分「实时前台 WS」「Android 后台 ntfy/FCM」「回源 HTTP 补读」的职责；不要把某条通道断开误报为全局离线。提供可点开的技术详情即可，不要频繁顶层弹出「中继已连接」。

## 3. 约束 / 不可做

- 禁止更改后端 Chat / Dream 核心生成逻辑、人格设定、记忆格式、酒馆数据与用户数据。**不要清空 Outbox、本地会话、ACK cursor 或历史记录来消除症状。**
- 不以统一延长 `/mobile/chat` HTTP timeout 作为主要修复；工具可以长时间运行，正确方案是跨生命周期识别真实状态。
- 不改动现有 `request_id` 幂等、`critical_written` 判定、final-only message identity、mobile inbox ACK 和持久化顺序；不允许 WS 重连导致重复回复/重复工具调用。
- 没有服务端证据，不准一直显示「角色正在思考」；未知状态需明确、可恢复、不过度技术化。
- Android 后台常驻 notification 是服务的系统约束，不能简单删掉常驻通知；新增自动权限授权、绕过系统权限、敏感原文遥测一律禁止。
- 不引入大体积新库去替换已有成熟滚动/状态/传感器实现，除非有基准和必要性说明；优先局部收敛旧逻辑。
- 若发现 GitHub main 和本地发行版不一致，先出差异清单并在该基础上实施，不覆盖用户未提交变更；密钥、签名、Keystore 不进入提交。

## 4. 验收矩阵（要求测试、实机证据，不接受仅编译通过）

| 编号 | 操作 / 故障注入 | 必须通过 |
|---|---|---|
| 310-A1 | 聊天停在 3 天前任意气泡中部，后台新消息 1/10/50 条到达 | 原气泡相对屏幕位置无可见下移；有未读提示；未自动跳底 |
| 310-A2 | `loadOlderHistory()` 异步进行时同时收到 final、meta 增删、历史去重 | 同一锚点保持，时间顺序正确，无跳屏/重复/漏行 |
| 310-A3 | 用户已主动滚至底部，发送或收到新消息 | 合理跟随、动画稳定；用户一旦往上滑则立即锁住阅读位置 |
| 310-A4 | 开始下拉刷新、中途新消息进入、结束刷新、切前后台 | 不自动跳屏，不误触发多次刷新，不无限旋转 |
| 310-B1 | 发送后工具执行超过 120 秒，退出页面/APP 再回 | 服务端 running 可证实时有角色三点；完成后取消三点并显示唯一最终回复 |
| 310-B2 | HTTP 超时但后端继续执行、WS 延迟/断连、HTTP late failure | 不展示假的失败结果，不自动重发，最终状态正确合并 |
| 310-B3 | 后端重启/失去请求记录/权限撤销 | unknown 不能显示正在思考；过期状态有合理恢复入口，无转圈永驻 |
| 310-B4 | 两个角色/账号/会话交替、其他设备主动消息 | 不串请求、不泄露别人的执行状态、不取消不相关 typing |
| 310-C1 | 失败/不确定发送截图 + 正常聊天截图 | 默认看不到大块「查询投递结果」「移出本机队列」；详情里保留操作与安全说明 |
| 310-D1 | 各种权限未授权/已授权/设备不支持/平台通道失败 | 设置页入口可见，按钮可用/禁用合理；状态准确，不静默消失 |
| 310-D2 | Android 系统设置授权后返回，禁用授权，切换服务端开关 | 权限、开关、有效状态独立且可刷新；不偷偷开启感知 |
| 310-E1 | Wi-Fi↔蜂窝、SSE 每 5s 断开再重连 10 次、前后台 10 次 | 中继常驻通知不因相同文案反复打扰，真实连接恢复有日志证据 |
| 310-E2 | 主动通知抵达、FCM 有/无配置、ntfy 故障回退 | 不漏新消息、不重复通知、不破坏已有保活/补偿策略 |
| 310-X1 | flutter analyze/test + Android 原生单测/真机打包 + 后端协议测试 | 均通过；明确列出设备/Android 版本/commit/截图/关键脱敏日志 |

## 5. Codex 交付物

1. 首先输出**根因报告**：每个问题对应确定代码路径、触发时序与测试/真机日志；把“代码可确认”和“仍需设备验证”分开。特别核对用户截图对应的 `EdgeRefresh` 与 Outbox 两条独立状态路径。
2. 尽量用一组小而明确的提交/PR：滚动锚点；Agent 状态恢复 + 如有必要的后端只读接口；聊天视觉清理；权限入口；native 通知降噪。
3. 新增回归测试和最小 fixture，截图展示正常聊天、工具长等待返回、历史阅读收到新消息、权限未授权状态。提供具备可复现性的 `adb`/Flutter 集成脚本。
4. 更新手机端协议/架构文档，标清前台 WS、HTTP 补读、后台 SSE/FCM 的职责边界；更新设置项文案与中文/英文 l10n。
5. 完成后附 `commit SHA`、PR 地址、通过的测试统计、仍未验证的平台与风险。**不得声称已经真机验证，除非实际提供设备证据。**

### 实施顺序建议

`A 滚动锚点 → B Agent 状态读取与恢复 → C 聊天去技术化 → D 传感器入口 → E 中继原生通知降噪`。

其中 A/B/C 同为 P1，可独立提交以减小回归；D/E 不应拖延阻塞 P1 的交付。任何只读后端状态协议变更，需加兼容探测与旧版降级，让旧版手机/旧版后端仍能互通。
