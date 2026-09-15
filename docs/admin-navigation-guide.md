# 管理面板导航与使用文档

管理面板侧边栏按「功能与行为、模型与服务、角色与创作、记录与状态、排错与管理」
分组，保留概览与各分类首页。点击分类标题展开或收起，小入口直接进入细分页；
底部固定「使用文档与页面指南」，提供常见任务路线、术语说明及 62 个页面的用途与快捷跳转。
用户文档正文在 `admin/static/pages/guide.html`，中英文文案在 `admin/static/i18n.js`。

分类状态沿用浏览器 `navGroupsCollapsed`，进入细页自动展开所属分类。
「返回上一页」仅记录本标签页内导航，最多 100 条，只保存页面标识和滚动位置；
不保存表单值、密钥或响应数据。刷新后保留访问顺序，退出登录清空。
返回已挂载页面保留当前筛选及输入，不撤销保存，也不自动保存表单；调度页恢复轮询，
不重新加载设置表单。刷新浏览器后不承诺恢复未保存输入。

所有入口沿用 `goto` 与原 lazy fragment / loader；冻结的 integrations 仍只保留兼容
深链，不加入目录。未知页面不移除当前页面；fragment 请求失败显示重试说明；过期
异步导航不再启动 loader 或覆盖访问位置。分类按钮支持键盘及 `aria-expanded`，
当前页面链接标记 `aria-current`，窄屏沿用抽屉导航。

## 维护与验证

新增页面时同步侧边栏、文档目录、双语词条和 loader。修改静态资源必须更新
`index.html` 的对应版本；fragment 同步更新 `ADMIN_UI_FRAGMENT_VERSION`。
`/` 与 `/static/*` 的 `Cache-Control` 是 `no-cache, must-revalidate`：普通刷新会向服务器再验证，
不是面板自己会变。改了 JS/CSS/fragment 仍必须 bump `?v=`，否则对照的是再验证后仍命中的旧内容。
`test_admin_navigation_ui.py` 校验导航与文档覆盖所有当前页面，避免遗漏细入口。

浏览器回归：启动或复用本地 admin 服务，安装 Playwright 后运行
`node tests/admin_navigation_browser.cjs`。可通过 `NODE_PATH` 指向既有依赖，
`ADMIN_TEST_URL` 指定服务，`ADMIN_TEST_BROWSER` 指定已有浏览器可执行文件。
测试清除缓存后刷新真实静态资源，在独立浏览器上下文中显示页面，拦截全部业务 API，
不读取生产数据或使用真实 token。覆盖桌面与窄屏、键盘展开、快捷跳转、返回、
输入与滚动保留、刷新、语言切换、请求竞态、失败重试、调度轮询恢复、坏存储及退出。
截图输出到 ignored 的 `.tmp/admin-navigation/`。

三面边界：仅调整管理面页面发现和导航，不新增服务端配置、台账、权限或控制接口。
桌面仍经 `open_admin_panel` 打开管理面；手机原有 Flutter 设置、鉴权与中继保持原契约。
真实桌面原生窗口、手机 WebView 及登录/业务请求联调不属于上述静态浏览器回归。
