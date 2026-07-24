# Brief 119 · 给 Codex：admin/static/index.html 分块

写于 2026-07-24，指定交给 Codex（额度恢复后）。这是纯体力活/杂活，不涉及设计决策，
适合丢给额度充足但不需要深判断的执行者。

## 背景

`admin/static/index.html` 现在是一个 3000+ 行的单文件，CSS + 全部页面 HTML +
全部 JS 逻辑都挤在一起。这几轮排查 MCP/视觉观测问题时，每次定位一个函数/一张卡片
都要先 grep 行号再 offset 读取，维护成本很高，以后新增页面只会更糟。

## 目标

按现有的"页面"边界（`<div class="page" id="page-xxx">`）和职责，把这一个文件拆成
多个文件，**不改变任何行为、任何 DOM 结构、任何 API 调用**，纯粹是物理拆分 + 引用
关系整理。

## 建议的拆法（接手时再核实，不强制照抄）

1. **CSS 抽出**：`<style>` 块整体挪到 `admin/static/style.css`，`index.html` 里
   换成 `<link rel="stylesheet" href="style.css">`。
2. **JS 按页面拆**：现在的大 `<script>` 块按功能域拆成多个文件，比如
   `admin/static/js/mcp.js`、`admin/static/js/dream-settings.js`、
   `admin/static/js/visual-perception.js`、`admin/static/js/feature-flags.js`、
   `admin/static/js/core.js`（`api()`、`t()`、`goto()`、`escapeHtml()` 这类全局
   共用 helper，别的文件都依赖它，必须最先加载）……具体切几块、怎么分组，接手时
   看实际函数聚集程度决定，不用严格对应本单列的名字。
3. **`index.html` 只保留骨架**：nav + 每个 `<div class="page" id="page-xxx">` 的
   HTML 结构，末尾按依赖顺序 `<script src="...">` 引入拆出去的 JS 文件。

## 硬约束（不能破的红线）

- 所有 `onclick="xxx()"` 引用的函数名必须保持**全局可访问**（这些函数现在都是
  内联 `<script>` 里定义的顶层函数，天然挂在 `window` 上；拆成多文件后普通
  `<script src=...>`（非 module）里定义的顶层函数依然会挂上 `window`，不需要显式
  导出——但如果哪个文件改用了 `type="module"` 或者用了立即执行函数包裹，就会
  破坏这一点，必须避免）。
- `data-i18n` / `data-i18n-placeholder` 属性和 `i18n.js` 的对应关系不能动。
- 不改变任何 DOM id、class、data-* 属性——`tests/test_admin_mcp_ui.py` 这类测试
  是拿字符串在整个文件里做 marker 匹配的，拆分后这些字符串必须原样能在**某一个**
  拆出去的文件里找到（如果测试写死读 `index.html` 单文件，可能需要同步把测试改成
  读多个文件拼起来做判断，具体看接手时这类测试还剩几个）。
- 拆分过程本身不产生任何功能性 diff——拆完之后每个页面手动点一遍（或者至少挑
  几个刚修过的：视觉观测设置、MCP 管理、梦境设定）确认渲染和交互跟拆分前一致。

## 验收

1. `pytest -n auto tests/test_admin_mcp_ui.py`（以及其他任何断言 `index.html`
   内容的测试）全过，需要的话同步改测试读取路径。
2. 管理面板启动后，nav 里每个页面都能正常 `goto()` 切换、加载数据不报 JS 报错
   （浏览器 console 里过一遍）。
3. 不需要新增任何后端改动，纯前端静态文件拆分。

## 备注

这个仓库的协作偏好和红线写在 `AGENTS.md`（Codex 默认读取的入口）；`CODEX.md` 是
`CLAUDE.md` 的兼容镜像，两者冲突时以 `AGENTS.md` 为准。
