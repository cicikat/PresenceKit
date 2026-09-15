# 管理面设置整理（2026-09-12）

## 当前实现

- 英文技术名称不再经过含占位符的反向翻译；保留显式 i18n key 和完整短语翻译。
  例如 Tool Loop 不会被套入“已……”句型；相应未使用的反向正则构建代码已删除。
- 标题说明统一为常规字重、次级颜色和独立间距。自主活动三个触发源始终纵向排列，
  时间字段分行，并说明前置条件、活动时段和机会不等于发言。
- 顶部提供全局所有者资料入口；唯一编辑字段仍在首次配置页。
  调度页保存不再提交 owner_id 或 signatures；签名 textarea 已删除。
  不迁移身份数据、不更改存量配置值，QQ/desktop/mobile 均沿用同一个后端 owner_id。
- MCP 页折叠/展开只改当前卡片 DOM，不整页 `loadMcpPage()`；已有内容刷新不再先刷「加载中」。总开关写盘后信号入队即返回。
- 思考卡开关（生成思考 / 角色心声 / 应用于主动消息 / 气泡优先显示前置独白）与功能开关总览相同，用 `admin-toolbar` + `checkbox-row` 分组；方式与独白预算单独一组 `field`，仍一次保存。
- 图像连接和用途分离：Presets 列表可新建/编辑/删除命名连接（vision 或 OCR），名称不锁死；
  用途表每行自选连接（聊天图、生活记录饮食/购物车/账单、手机自动化）。无 `image_presets`
  块时从 `vision:` / `image_recognition:` / `phone_control_vision:` 合成，语义不变。
  空密钥保留、OCR 协议决定 URL 字段、删除被引用连接返回 409。配置就绪不宣称服务可用。
- 图像连接测试：POST /image-recognition/test/{connection}，接受连接名及 general/ocr/phone 别名，admin-only，
  使用已保存设置与本地生成的 TEST 123 图片，25 秒总超时；视觉 SDK 零重试。
  不用用户图片、不执行手机动作、不修改配置。响应仅包含 ok/connection/duration_ms/
  error_category；不会回显 provider 正文、密钥或异常文本。现有 API 调用账本记录探针结果；
  诊断测试不计入角色对话统计。客户端不需要调用这个 admin 诊断接口。

## 三面核对与验收

管理面保持配置真值与测试入口；桌面由现有管理面桥接编辑这些设置，手机使用相同
后端图片识别/自动化路由。原调用链鉴权、字段、配置热重载、上传缓存签名、图片顺序、
phone inheritance、WS/poll/ack/TTL/通知不变；没有额外配置或状态存储。

Chromium 已在本地隔离静态服务上清缓存并禁用缓存，实测桌面和窄屏：连接编辑展开、
自定义地址不被 provider 覆盖、OCR 协议字段切换、连接与 mode 分开保存、诊断状态显示、
调度保存不带 owner/signatures、自主活动纵向三行、Tool Loop 不被误译。测试 API 使用
合成响应，不接触生产配置；真实模型连通性与原生容器仍为 observe。

定向后端/UI 回归见 tests/test_admin_image_connections.py、test_admin_i18n_assets.py、
test_admin_model_preset_ui.py、test_admin_phone_control_vision_ui.py、test_image_recognition.py。
浏览器回归 tests/admin_settings_browser.cjs。全站 i18n 两项扫描仍被既有的 IME、生活记录、
设备与小红书页面未翻译文本阻断；本次三个修改 fragment 的独立翻译覆盖通过，不顺手修改其他页面。

根目录 admin-style-review.html 为独立讨论稿，不是管理面功能。包含现有色彩变量和组件样本、
候选排版、用途批注、localStorage 保存与 JSON 导入导出，供用户决定后续统一规范。

## 管理面样式定稿实施（2026-09-12）

灰绿状态色、中性按钮、分隔线设置区、复杂表单折叠及 JSON 填表已接入；观测与工具页复用现有 state.read 全局状态表。原保存 API、客户端管理面 bridge 和手机消费路径不变。
current / open / roadmap / observe 与验证证据见 [admin-design-implementation.md](admin-design-implementation.md)。
roadmap：逐请求的视觉、权限、队列、发送、ack/TTL 尚未合并入十类全局状态表，不能据此宣称端到端链路全部可观测。observe：原生容器与真实服务未联调。
