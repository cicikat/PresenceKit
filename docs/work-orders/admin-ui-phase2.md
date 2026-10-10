# Admin 第二阶段：双版本与吃食试点

范围：原生 HTML/JS 管理面；导航、后端、权限及其他模块不改。
方案：保留唯一业务 DOM 与共享 API/action，通过可逆节点移动和模块级 CSS 呈现新版；localStorage 默认 legacy。

- [x] 建立顶层版本选择，持久化选择，切换零网络、零写入。
- [x] 抽取 scoped tokens 与公共 Card/Accordion/Button/Input/Select/Switch/StatusBadge/FormField 样式。
- [x] 迁移吃食试点：设置、查询、重试、更正、状态与提交互斥。
- [x] 浏览器核对桌面/窄窗口、草稿保留、重复请求、错误/空态；记录截图。
- [x] 相关回归、换行/差异核对，独立 commit。

验收边界：真实后端写操作使用隔离数据验证，不修改生产配置或真实口味。浏览器 fixture 验证必须标注模拟 API。完成后等待用户验收。
删除候选：全部迁移验收后单独删除旧呈现与开关；本轮不执行。

## 交付与验收证据

- 顶层「界面」选择旧版/新版（吃食试点）。默认 legacy；localStorage key 为 presencekit_admin_ui。切换不刷新页面、无 API 请求或配置写入。
- 单一页面、单一表单 DOM、同一 api/authHeaders、同一 data-action 绑定和 foodOperation 控制器。设置 PUT 直接消费返回的 effective state；重试与更正沿用原端点、scope 和校验。
- 新版基于选定 refined 样机的本地字体、紫灰 tokens、8px 圆角、38px 控件和原生 details 卡片。未迁移模块继续旧版，不增加导航或假页面。
- 公共组件为原生语义元素与 .pk-ui 范围内的 pk-card / pk-accordion / pk-button / pk-input / pk-select / pk-switch / pk-status / pk-field，入口映射见 AdminUI.components。后续模块可复用样式，适配器只处理呈现。
- 保留的重复仅为记录结果的旧版 JSON 与新版卡片渲染；API、状态、提交、校验均未复制。旧版继续已有 settings-disclosure 包装，节点原位还原。
- 浏览器：实际静态资源，清缓存的独立 Chromium context；桌面 1440px 与窄窗口 480px。通过默认旧版、刷新记忆、切换零请求、唯一节点、折叠/切换草稿保留、保存/重试/更正、程序化双调用只提交一次、错误/空态、窄窗口无横向溢出、无 pageerror。API 响应是明确标注的 fixture，不代表生产 API 浏览器端到端验收。
- 真实后端：隔离 sandbox 下 TestClient 调用原 router，验证配置写入/effective state、更正落盘与查询、角色隔离、422 校验、state.read 禁止写入、retry 排队。生产配置和真实记录未改。
- pytest：32 passed / 3 existing failures。失败为已有 scheduler inline style、admin-inline-103/131 孤立规则、工具双语说明缺失；相关文件及注册表未修改，前两项已核对 HEAD。未为本轮扩展修复。
- 截图在本地 ignored 的 .tmp/admin-food-legacy.png、admin-food-new.png、admin-food-narrow.png，全部为测试数据。JS syntax 与 diff/换行检查通过。
- 本轮仅影响 backend Admin 前端，不涉及 desktop/mobile 协议或业务合同；不修改其他仓库。
- 用户尚未进行视觉与生产联调验收；默认保持旧版。到此停止后续迁移，全部完成后另行评估删除旧呈现和开关。
