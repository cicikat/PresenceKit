# Admin 新旧 UI 迁移验收

初次迁移覆盖 73 个页面，以下字段/action 数量为当时的验收快照。2026-10-10 后续重组加入多模态模型路由、角色行为及数据管理；三个聊天观测页面已删除，当前归属见 `feature-control-surface.md`。默认旧版，顶栏选择写入 localStorage；切换不重启、不写配置、不同时挂载两份业务页面。旧版及切换入口等待用户验收后单独评估删除。

## 组件与共享边界

`admin-v2.css` 提供统一字体与 Design Tokens；`admin-components-v2.css` 在 `.pk-admin` 下提供 Card、Accordion、Button、Input、Select、Switch、StatusBadge、FormField、表格、弹窗、结果与日志样式，后续页面沿用现有语义结构即可复用。

`ui-version.js` 是共享呈现适配器，标题左侧 ▾/▸ 操作折叠；既有 details 和 MCP 三角控件继续使用自身交互。折叠只隐藏内容，保留表单节点、草稿与播放器。动态内容通过页面范围 MutationObserver 适配；旧版回退移除新版标记与控件，保留原 inline 样式。信号颜色映射仅处理已知旧调色板，不修改媒体或用户颜色输入。

全部页面共用现有 API Client、鉴权、业务 handler、数据加载及事件委托。剩余呈现重复：吃食查询的旧 JSON / 新卡片 renderer；旧样式与新 scoped 样式；旧设置 details 装饰与通用卡片适配。没有第二套业务逻辑。后端、桌面与手机协议未改动，无跨端迁移。

## 验证证据

- Chromium 实际加载当前静态资源：73 页在 1440px / 480px 遍历，字段和 action 数量、节点身份、草稿保留；切换/折叠无 API 请求；无页面横向溢出；控制台无未捕获异常。缓存已更新。
- 真实共享 handler + 明确 API fixture：新旧代理、图像连接、MCP 测试/导入请求完全相同且单次提交；403 错误可见；动态资产表单、隐藏 OCR 和播放器节点保留。
- prompt 观测真实 handler 动态刷新、信号颜色、键盘折叠、刷新保持折叠及 inline 样式回退通过。同时修复既有总览显示问题：清空 display 后旧隐藏 class 仍生效，现显式显示 block，新旧版均可见。
- 吃食读取、设置保存、查询、重试、更正、error/empty 与窄窗口 fixture 回归通过。
- 最近观测相关 pytest：11 passed / 2 baseline failures，均为旧 fragment 缓存常量硬编码；设置批次：15 passed / 3 baseline failures（旧字段及缓存断言）。未把这些旧断言改成迁移证据。LF 与差异检查通过。

浏览器脚本：`tests/admin_ui_versions_browser.cjs`（`ADMIN_UI_TEST_PHASE=all`）、`tests/admin_ui_business_browser.cjs`、`tests/admin_food_versions_browser.cjs`。截图与逐页字段/action/overflow 数据位于 ignored `.tmp/admin-all/`；包含 overview 新旧对比以及设置、观测 desktop/narrow。fixture 数据只在测试中，产品继续读取真实 API。

这是全页呈现验收与代表性交互回归，未执行全部生产服务、凭据、真实设备或每个功能的生产端到端操作；没有对生产配置进行测试写入。仍需用户使用真实管理面验收，不能将 73 页静态遍历视为 73 个业务全部实测。

## 全页覆盖清单

| Page | Fields | Actions | 1440/480 |
| --- | ---: | ---: | --- |
| agent-runtime-browser | 11 | 6 | pass |
| auth-tokens | 0 | 4 | pass |
| autonomy-settings | 26 | 9 | pass |
| call-records | 2 | 1 | pass |
| character | 22 | 16 | pass |
| client-settings | 28 | 17 | pass |
| conversation-settings | 8 | 6 | pass |
| coplay-config | 1 | 2 | pass |
| creation-center | 0 | 6 | pass |
| device-policy | 3 | 4 | pass |
| diary-config | 2 | 1 | pass |
| dream-settings | 8 | 21 | pass |
| embedding-config | 5 | 1 | pass |
| feature-center | 1 | 3 | pass |
| generation-debug | 3 | 6 | pass |
| guide | 0 | 86 | pass |
| integrations | 0 | 0 | pass |
| listening-player | 5 | 12 | pass |
| local-model-runtime | 14 | 5 | pass |
| logs | 5 | 5 | pass |
| lorebook | 8 | 13 | pass |
| mail-config | 12 | 1 | pass |
| mcp | 15 | 14 | pass |
| memory-consolidation | 20 | 19 | pass |
| minecraft | 26 | 16 | pass |
| model-routing | 20 | 17 | pass |
| network-config | 5 | 3 | pass |
| observation-center | 1 | 31 | pass |
| observe-api-contract | 0 | 1 | pass |
| observe-autonomy | 0 | 1 | pass |
| observe-char-permissions | 2 | 1 | pass |
| observe-dream-operations | 0 | 1 | pass |
| observe-dream-prompt | 2 | 5 | pass |
| observe-dream | 0 | 1 | pass |
| observe-existence | 0 | 6 | pass |
| observe-group-arbiter | 1 | 1 | pass |
| observe-growth | 1 | 1 | pass |
| observe-hidden | 0 | 1 | pass |
| observe-listening | 1 | 6 | pass |
| observe-memory-events | 6 | 9 | pass |
| observe-memory-summary | 2 | 1 | pass |
| observe-memory | 1 | 1 | pass |
| observe-mood | 0 | 1 | pass |
| observe-prompt | 4 | 4 | pass |
| observe-provenance | 4 | 1 | pass |
| observe-resource-completeness | 0 | 1 | pass |
| observe-runtime-signals | 0 | 1 | pass |
| observe-runtime | 0 | 1 | pass |
| observe-spend | 0 | 1 | pass |
| observe-system-diagnosis | 0 | 0 | pass |
| observe-tools | 3 | 2 | pass |
| observe-trigger-catalog | 0 | 1 | pass |
| observe-vector | 3 | 2 | pass |
| observe-visual | 1 | 1 | pass |
| operations-center | 0 | 12 | pass |
| output-settings | 3 | 2 | pass |
| overview | 0 | 9 | pass |
| owner-turn-api | 4 | 15 | pass |
| personal-settings | 2 | 3 | pass |
| relationship-facts | 2 | 5 | pass |
| role-bindings | 1 | 0 | pass |
| runtime-config | 13 | 11 | pass |
| scheduler | 11 | 17 | pass |
| service-center | 4 | 3 | pass |
| setup | 5 | 6 | pass |
| status | 0 | 15 | pass |
| tools | 18 | 19 | pass |
| tts-config | 24 | 13 | pass |
| user-data | 0 | 0 | pass |
| users | 4 | 4 | pass |
