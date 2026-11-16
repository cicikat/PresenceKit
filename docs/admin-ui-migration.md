# Admin 新旧 UI 迁移验收

初次迁移覆盖 73 个页面，以下字段/action 数量为当时的验收快照。2026-10-10 后续重组加入多模态模型路由、角色行为及数据管理；三个聊天观测页面已删除，当前归属见 `feature-control-surface.md`。2026-10-18 起首次访问默认新版，已保存的旧版选择仍保留；顶栏选择写入 localStorage；切换不重启、不写配置、不同时挂载两份业务页面。旧版及切换入口等待用户验收后单独评估删除。

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

## AR-01 页面与依赖索引（325）

328 更新：[AR-03 责任账本](refactors/ar03-admin-components.md) 记录现行 shared
服务、原生组件、聚合数据 controller 和 Token 权威。以下 AR-01 表为迁移基线，
当前精确 loader/fragment/owner 以唯一 Registry 的 descriptors 为准。

全部页面的初始化入口在 `js/admin-page-registry.js`；原功能脚本继续拥有各自的
DOM、表单事件、渲染、请求和可见错误。`core.js` 唯一持有 TOKEN/BASE、api/toast、
fragment 加载与竞态取消、页面状态/历史和 data-action 委托。注册表只保存延迟回调，
不在装载时发请求、不复制公共状态。`logs` 的 offset 仍引用原全局状态。

同步装载关系：theme/ui boot → defer 字典 `i18n.js` → `js/i18n-runtime.js`
（原 t/AdminI18n/Legacy DOM、Observer 和原生弹窗包装）→ 页面注册表 → core
→ 原有业务脚本（顺序不变）→ DOMContentLoaded。`window.AdminI18nMessages`
与 `AdminI18n.I18N` 指向同一份字典；既有 key、fallback、语言切换与初始化时机不变。

公共呈现：`style.css`、`admin-design.css`、`admin-v2.css`、
`admin-components-v2.css`、`admin-theme.css`，本次未移动/修改 CSS。
DOM 列是原 fragment 容器；新版由 `AdminPageLayout.resolve/prepare` 将**同一个节点**
移动到角色行为/模型/数据/观测聚合页，`ui-version.js` 只适配呈现，旧版恢复原位置。
多个业务域共用这些 CSS，未引入第二份页面 DOM 或业务 loader。

| 页面/fragment | 业务 JS（注册回调引用） | 原 DOM 容器 |
|---|---|---|
| `agent-runtime-browser` | `agent-runtime-browser.js` | `#page-agent-runtime-browser` |
| `auth-tokens` | `admin-page-layout.js` | `#page-auth-tokens` |
| `autonomy-settings` | `observability.js`, `scheduler.js`, `video-call-presence.js` | `#page-autonomy-settings` |
| `call-records` | `settings-center.js` | `#page-call-records` |
| `character-behavior` | 无首屏请求；表单/显式交互驱动 | `#page-character-behavior` |
| `character-cleanup` | `character-cleanup.js` | `#page-character-cleanup` |
| `character` | `character.js` | `#page-character` |
| `client-settings` | `client-settings.js`, `settings-center.js` | `#page-client-settings` |
| `conversation-settings` | `settings-center.js` | `#page-conversation-settings` |
| `coplay-config` | `setup.js` | `#page-coplay-config` |
| `data-management` | `admin-page-layout.js` | `#page-data-management` |
| `device-policy` | `relationship.js` | `#page-device-policy` |
| `diary-config` | `setup.js` | `#page-diary-config` |
| `dream-settings` | `dream-settings.js` | `#page-dream-settings` |
| `embedding-config` | `setup.js` | `#page-embedding-config` |
| `feature-center` | `admin-page-layout.js` | `#page-feature-center` |
| `frozen-features` | `settings-center.js` | `#page-frozen-features` |
| `generation-debug` | `mcp.js`, `observability.js` | `#page-generation-debug` |
| `guide` | 无首屏请求；表单/显式交互驱动 | `#page-guide` |
| `integrations` | 冻结兼容 fragment | `#page-integrations` |
| `listening-player` | `listening-player.js` | `#page-listening-player` |
| `local-model-runtime` | `local-runtime.js` | `#page-local-model-runtime` |
| `logs` | `status-users.js` | `#page-logs` |
| `lorebook` | `lorebook.js` | `#page-lorebook` |
| `mail-config` | `setup.js` | `#page-mail-config` |
| `mcp` | `mcp.js` | `#page-mcp` |
| `memory-consolidation` | `observability.js` | `#page-memory-consolidation` |
| `minecraft` | `minecraft.js` | `#page-minecraft` |
| `model-routing` | `admin-page-layout.js`, `settings.js` | `#page-model-routing` |
| `network-config` | `settings.js` | `#page-network-config` |
| `observation-center` | `settings-center.js` | `#page-observation-center` |
| `observe-api-contract` | `observability.js` | `#page-observe-api-contract` |
| `observe-autonomy` | `observability.js` | `#page-observe-autonomy` |
| `observe-char-permissions` | `observability.js` | `#page-observe-char-permissions` |
| `observe-dream-operations` | `dream-operations.js` | `#page-observe-dream-operations` |
| `observe-dream-prompt` | `observability.js` | `#page-observe-dream-prompt` |
| `observe-dream` | `admin-page-layout.js` | `#page-observe-dream` |
| `observe-group-arbiter` | `observability.js` | `#page-observe-group-arbiter` |
| `observe-hidden` | `observability.js` | `#page-observe-hidden` |
| `observe-listening` | `observability.js` | `#page-observe-listening` |
| `observe-memory-events` | 无首屏请求；表单/显式交互驱动 | `#page-observe-memory-events` |
| `observe-memory-summary` | `observability.js` | `#page-observe-memory-summary` |
| `observe-memory` | 无首屏请求；表单/显式交互驱动 | `#page-observe-memory` |
| `observe-mood` | `observability.js` | `#page-observe-mood` |
| `observe-prompt` | `admin-page-layout.js` | `#page-observe-prompt` |
| `observe-provenance` | `provenance-vector.js` | `#page-observe-provenance` |
| `observe-resource-completeness` | `observability.js` | `#page-observe-resource-completeness` |
| `observe-runtime-signals` | `observability.js` | `#page-observe-runtime-signals` |
| `observe-runtime` | `observability.js` | `#page-observe-runtime` |
| `observe-spend` | `observability.js` | `#page-observe-spend` |
| `observe-system-diagnosis` | `observability.js` | `#page-observe-system-diagnosis` |
| `observe-tools` | `observability.js` | `#page-observe-tools` |
| `observe-vector` | `admin-page-layout.js` | `#page-observe-vector` |
| `observe-visual` | `observability.js` | `#page-observe-visual` |
| `output-settings` | `character.js` | `#page-output-settings` |
| `owner-turn-api` | `owner-turn-api.js` | `#page-owner-turn-api` |
| `personal-settings` | `setup.js` | `#page-personal-settings` |
| `relationship-facts` | `relationship.js` | `#page-relationship-facts` |
| `role-bindings` | `settings-center.js` | `#page-role-bindings` |
| `runtime-config` | `runtime-config.js` | `#page-runtime-config` |
| `scheduler` | `scheduler.js` | `#page-scheduler` |
| `service-center` | `settings-center.js` | `#page-service-center` |
| `setup` | `admin-page-layout.js`, `setup.js` | `#page-setup` |
| `status` | `admin-page-layout.js` | `#page-status` |
| `tools` | `tools.js` | `#page-tools` |
| `tts-config` | `character.js` | `#page-tts-config` |
| `user-data` | `user-data.js` | `#page-user-data` |
