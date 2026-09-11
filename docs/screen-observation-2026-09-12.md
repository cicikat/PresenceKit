# 角色按需截图（2026-09-12，partial）

用户已确认桌面、后端、手机共同施工，并同意工具/旁白事件契约扩展。
实现已完成，真实 Windows/Android 设备和视觉模型联合验收仍为 open。

## 实现和使用

- 后端功能开关：管理面 → 功能开关 →「角色按需截图（电脑与手机）」；默认关闭。
  同时需要「视觉感知」和有效视觉模型配置。管理面同处可查看设备和截图请求回执。
- 电脑：设置 → 视觉观察 →「允许角色按需截图」。独立于原有周期采样，默认关闭。
- 手机：设置 → 系统配置 →「允许角色按需截图」。需要 Android 11+、无障碍服务，默认关闭。
- 更新并重启后端和两端应用后，角色可调用 `observe_user_screen`。全局开关开启且未显式配置
  自主工具策略时，主动循环继承该工具的启用状态；已有显式禁用、自我能力限制、工具暴露规则仍有效。
- 角色根据过滤后的视觉概括决定 `talk_owner` 或保持沉默，现有消息通知通道负责提醒。
  没有新增绕过免打扰的强制弹窗工具，通知权限和原有投递策略继续生效。

## 请求链

两端每 5 秒通过 `POST /perception/screen/poll` 上报可用性和真实输入空闲秒数。
后端仅在心跳小于 30 秒、输入空闲小于 300 秒的已授权设备中选择最近操作的一个。
无合格设备时返回状态，不读取旧图；不把“最近上线”当成“最近使用”。

`observe_user_screen` → UUID 请求（20 秒 TTL）→ 所选设备单次领取 → 本机锁屏/授权复查
→ 新截图（最长边 1280，JPEG）→ `POST /perception/screen/result` → VLM 隐私过滤
→ 当前工具轮的事实概括。整体最多约 28 秒，适配现有 30 秒自主工具预算。
同一后端同时最多一个请求，60 秒冷却；超时不换设备、不复用旧图、不重试截图。

Windows 复用 GDI 截图，GetLastInputInfo 判定输入；Android 复用 Accessibility takeScreenshot，
点击、滚动和文本编辑事件更新输入时间。手机在截图前拒绝密码节点、敏感应用与敏感关键词；
两端均在截图前和上传前检查本地授权与锁屏，撤销或服务退出后丢弃旧请求结果。
截图只在内存中传输到配置的视觉模型，敏感画面不返回角色。行为痕迹仅保存工具元数据。
角色可见的非敏感概括可能按现有对话/自主 prompt 观测策略保存，不承诺概括永不落盘。

## 契约与观测

- poll：`{device: desktop|mobile, available: bool, idle_seconds: number}`；返回
  `{enabled, request: null|{request_id, ttl_seconds}}`。
- result：`{device, request_id, status, image_base64}`；status 为
  `ok|locked|disabled|unsupported|sensitive|failed`，仅 ok 可携带 JPEG。
- `sensor.write` + 对应 desktop/mobile token profile（admin 兼容）；请求同时绑定 token 摘要。
  不同设备/凭据、重复、过期请求返回 409；非法图片返回 422；跨设备 profile 返回 403。
- `GET /perception/screen/status` 使用 `state.read`，返回有效开关、活跃设备、心跳年龄、
  空闲时间、待处理请求和最近 30 条无正文回执。均在内存中，重启清空，不新增磁盘台账。
- HTTP 禁用代理/重定向，不改原有 WS、mobile poll/ack 或 relay 协议。
- 桌面 `update_visual_perception_settings` 新增可选 `onDemandEnabled`，旧调用保留原值；
  `get_visual_perception_settings` 返回该值，本地配置 `visualPerceptionConfig.onDemandEnabled`。
- 手机新增专用 `presence_mobile/screen_observation` 通道，get/set 管理本机授权和能力；
  AccessibilityService 持有 worker 生命周期，未新增前台服务或 app_shell 领域状态。

## 验证与未完成项

- 后端截图、既有视觉观察、工具事件及 XML 自主循环相关测试共 43 项通过。
- 桌面 `npm run build`、`cargo check` 通过；Rust visual 相关测试 8 项通过。
- Edge + 模拟 IPC 验证独立授权/撤销、管理面设备与回执查询通过。
- 手机 gen-l10n、analyze、授权组件 3 项测试、dev debug APK 构建通过。
- 管理面旧国际化测试 3 项未通过：已有缓存版本断言及无关 IME/生活记录等页面翻译缺口。
- 后端全量测试尝试：421 通过、5 失败，达到 `--maxfail=5` 后停止；除上述 3 项外，
  还有已有 IME 输入框缺少 type、setup 缓存版本断言失败。未声称全量通过。
- open：真实双设备争用、Windows 锁屏/多屏、Android OEM/Doze/无障碍被杀、切换凭据、
  视觉模型实际响应与角色后续消息投递。未启动真实截图、未调用真实模型、未安装 APK。
- observe：手机实际输入事件覆盖依赖应用无障碍事件；当前仅主显示器截图；无障碍服务不可用时不降级为文字截图。

与桌宠分段、工具展示及自主模型修复独立交付；截图两端依赖后端新增 HTTP 契约。
