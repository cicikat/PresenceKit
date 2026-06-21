# 第五轮：阅读左右视图 + 聊天可全收起侧栏(三页统一) + 日记窗口闪烁

> 全部前端：`D:\ai\Emerald-client`。改动集中在
> `src/windows/activity/components/ReadingPage.tsx`、`ActivityCompanionPanel.tsx`、
> `GomokuPage.tsx`、`ChessPage.tsx`，以及 `src/windows/chat/components/SubDiary.tsx`。

---

## 任务 1：阅读改左右视图（左书页 / 右聊天）

**现状**：`ReadingPage.tsx` 的「阅读中」分支（约 397-463 行）是**纵向列**：会话信息 → 页面内容（`flex:1`）→ 翻页 → `ActivityCompanionPanel`（垫底）。外层容器 `flexDirection:'column'`，所以是上下布局，读着别扭。

**改法**：把阅读中视图拆成**横向两栏**：
- 外层加一个 `display:flex, gap, minHeight:0, flex:1` 的行容器。
- **左栏（书页区）** `flex:1, minWidth:0, display:flex, flexDirection:column`：放会话信息条 + 页面内容（`flex:1` 可滚动）+ 翻页按钮。
- **右栏（聊天）**：`ActivityCompanionPanel`，作为右侧边栏（宽度见任务 2 的统一规则，可收起）。
- 顶部「一起看书」标题 + error 保留在最外层不动；只有「阅读中」内容改成左右。

> 书页区翻页按钮建议固定在左栏底部（不随内容滚动）；页面内容区 `overflowY:auto`。

---

## 任务 2：聊天改「可完全收起的侧栏」，三页统一（仿 Claude）

**现状**：`ActivityCompanionPanel.tsx` 已有 collapse，但**收起后仍留一条横栏**「和叶瑄说说 «」（折叠分支约 191-217 行）。这不是你要的——你要的是像 Claude 那样**整条侧栏收回、横栏也不留**，只留一个小按钮可重新展开。三页（reading / gomoku / chess）都这样。

**改法（建议抽一个共享壳，避免三页重复）**：

新建 `CompanionSidebar`（包裹 `ActivityCompanionPanel`），统一行为：
1. **展开态**：右侧边栏，宽度 `clamp(260px, 30%, 340px)`，撑满父高（`alignSelf:stretch`）。面板顶栏右上角放收起按钮 `»`。
2. **收起态**：**完全不渲染侧栏**（宽度归 0、横栏也不留）。改为在内容区**右上角悬浮一个小展开按钮**（仿 Claude 打开边栏的图标按钮，如 `‹` 或聊天气泡图标），点它再展开。
3. **状态共享 + 记忆**：沿用 `uiPref('activity.companion.collapsed')`（三页同一把 key，已存在），收起状态跨页/重启保留。
4. 删掉 `ActivityCompanionPanel` 现在那个「留横栏」的折叠分支，折叠逻辑上移到 `CompanionSidebar`；面板本身只负责展开态内容（顶栏 `»` 收起按钮保留）。

**三页接入**：
- `ReadingPage`（任务 1 的右栏）、`GomokuPage`、`ChessPage` 都把 `ActivityCompanionPanel` 换成 `CompanionSidebar`，作为各自布局行的右栏。
- 收起后左侧棋盘/书页区自动占满剩余宽度。

**缩小适配（一并做）**：
- 各页主内容行 `display:flex, minWidth:0`，**不要 `flexWrap`**（防止窄窗换行到下方）。
- 棋盘/书页区 `flex:1, minWidth:0`；侧栏用上面的 `clamp` 宽度，窗口窄时侧栏跟着缩，棋盘/书页也缩。
- 棋盘响应式尺寸（若之前没做）：用 `ResizeObserver` 量容器，推导格子大小（五子棋 `CELL`、象棋 `SQUARE`），随窗口缩放。
- 收起态下内容区可用宽更大，自动放大。

**验收**：三页聊天都在右侧；点收起→整条侧栏消失、无残留横栏、只剩一个小展开按钮；再点展开恢复；窗口缩小时侧栏与棋盘/书页一起自适应缩放、聊天框不被遮蔽。

---

## 任务 3（bug）：日记窗口打开时白黑白闪两下

**现状**：点日记打开独立窗口时，先「白 → 黑 → 白」闪烁再显示内容。

**根因**：新建的 `diary-detail` WebviewWindow 加载是**全新一份 index.html**，而主题是**异步**应用的：
1. webview 刚创建、内容未渲染 → Tauri 默认背景**白**（闪 1）。
2. HTML/CSS 加载，但 `main.tsx` 的 `initTheme()` 是 `async`（`.catch` 后台跑），主题 CSS 变量（`--paper` 等）此刻还没注入 → `var(--paper)` 取不到值、背景塌成**黑/透明**（闪 2）。
3. `initTheme` 应用完 → 变量就位 → 显示正常内容。

（若在 `tauri dev`，`React.StrictMode` 双挂载会让 `DiaryDetailPane` 二次拉取，加重闪烁。）

**修法（推荐组合，治本）**：
1. **窗口先隐藏、就绪后再显示**：`SubDiary.tsx` 的 `openEntry` 创建窗口时加 `visible: false`；`DiaryDetailWindow.tsx` 在主题已应用 + 首屏内容渲染完后再 `getCurrentWindow().show()`。这样窗口只在「已主题化」后出现一次，零闪烁。
   - 时机：可在 `DiaryDetailWindow` 的 effect 里 `await initTheme()`（或等待主题 registry 就绪）后 `requestAnimationFrame(() => win.show())`。
2. **创建时设背景色兜底**：`new WebviewWindow(..., { backgroundColor: <当前主题 paper 色> })`，即使有极短未渲染期也不是刺眼白。
3. **（可选）首屏前置主题**：在 `index.html` 顶部内联一小段脚本，从 `localStorage` 读已存主题、立即给 `<html>` 设背景色，避免任何窗口的首帧闪白。这对 pet/presence-nag 也有益。

> 最小可行：只做第 1 项（hidden → show after themed）通常就能消除闪烁；2、3 作为加固。

**验收**：点日记 → 窗口直接以正确主题显示，无白黑白闪烁；`tauri dev` 下也不闪。

---

### 建议顺序
1. 任务 3（日记闪烁）— 改动小、体验立竿见影
2. 任务 2（CompanionSidebar 共享壳）— 先把收起逻辑做对
3. 任务 1（阅读左右）— 接入新侧栏即可，顺带缩放适配
