# 管理面板样式规则与实现（2026-09-12）

规范来源：用户在独立讨论稿导出的 `admin-style-decisions.json`，以批注优先于单选项。

## 已确定规则

- 灰绿仅表示当前状态；标题、结构和普通操作保持中性。标题 20px / 600，副标题 12px / 400，间距 6px。
- 普通设置区用分隔线；主导航跳转入口用表面层次。复杂表单展开/收起，普通开关不独立成卡。
- 页面标题对应导航分类；功能标题解释用途，副标题显示参数摘要，字段说明补充前置条件和默认行为。
- 表单内边距 24px，控件与导航入口圆角 6px；操作放底部，按钮保持一行，窄屏操作组可横向滚动。
- 高级参数与 JSON 默认收起；JSON 先填入可视化表单，核对后保存，普通开关不用 JSON。
- 未配置或受阻时显示提示与设置链接，恢复后提示消失；配置、实际生效、阻断和对应函数合并为表格。

创作页「现实资产启用与头像」按设置行拆开：当前角色、世界书、提示词、头像各自成组，操作放底部，开关不成独立卡。世界书 UI label 优先 title/name，否则条目关键词，再回退 stem；破限优先条目标题。PATCH `/settings/prompt-assets` 仍只提交 id。

## 当前实现与边界

共享样式在 `admin/static/style.css`、`admin-design.css`；交互在 `js/admin-design.js`。
复杂设置页复用原字段 ID、监听器及保存函数，原地移动节点形成原生 details；可按功能、参数、保存函数查找并展开。
现有图像连接保持列表及原地编辑，保存按钮移到编辑区底部。无第二份配置存储。
有后端配置状态节点的表单保留其节点；没有明确配置完整性契约的模块不根据非空输入捏造“已配置”。

观测入口与工具页显示同一链路表；功能总览的旧状态段落改用该表。
读取 `GET /admin/control-center/effective-state`（`state.read`）已有十类状态：
多步工具、MCP、自主管理、自主活动、调度器、消息通道、模型路由、语义检索、语音、硬件。
展示 configured/effective/runtime_status、来源、阻断、重启需求和 runtime_consumer，设置链接复用后端 edit_page。
绿色只表示该项后端判断通过，不替代上游连通性测试；无数据用 —，失败清除旧表而不显示为关闭。

模型连接 JSON 接受顶层对象或 connection 对象，识别 base_url/baseURL、api_key/apiKey、model、
provider_kind、api_protocol、tool_call_mode、anthropic_auth_mode 与标量 params。
字段类型、URL、枚举及冲突别名验证通过后才填表；嵌套 params 拒绝，避免现有表单把对象转成字符串。
未知字段不导入；不执行 JSON，不发请求，不存 localStorage。导入成功清除粘贴区；关闭弹窗清除粘贴区与密钥输入。

## 三面检查

管理面是配置编辑入口；桌面 `ConnectionSettingsPage` → `openAdminPanel` → 本地原生 bridge 继续加载同一静态资源。
手机 SettingsPage 保留连接和设备设置，消费原后端配置；本次不新增原生开关或管理 token 暴露。
保存仍走原路由与原鉴权，模型连接和图像用途独立提交；队列、WS/poll、ack、TTL、角色覆盖与 fallback 未改变。
无新增状态落盘或观测 API。客户端设置审计同步本轮入口事实。

## 验证

Chromium 在隔离本地静态服务中清除缓存、禁用缓存并重新加载，使用合成 API 响应。
`tests/admin_design_browser.cjs` 覆盖配置折叠、查找展开、输入保留、JSON 原子校验、零提交、
阻断提示恢复隐藏、读取失败清旧表、英文及 390px 窄屏。
`tests/admin_settings_browser.cjs` 覆盖原图像保存、协议联动、用途独立保存、探针、调度保存和自主活动排布。
已实际检查链路表与设置页面截图；不把合成数据验证当作真实服务连通验收。
三组浏览器回归通过（新样式、原设置、导航）；定向 pytest 10 项通过。

open：既有 `test_admin_form_controls.py` 两项失败：旧 setup.js 缓存版本断言、IME 输入框缺 type；HEAD 中已存在。
roadmap：十类全局状态尚非逐请求全链路追踪，视觉/OCR 探针、每个工具的权限闸门、队列/发送/ack/TTL
仍使用原明细页；需要各模块提供关联数据后才能合并，不能凭前端补出状态。
roadmap：含复杂动态子编辑器的页面保留原展开/弹窗交互，逐模块补明确配置完整性状态后再统一右侧标记。
observe：桌面原生 bridge 容器、手机真机和真实 provider 连通性本轮未联调。
