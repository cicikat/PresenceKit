# 第六轮修复：日记又打不开 / 偏好页无法下滑 / 聊天背景无效

> 全部前端：`D:\ai\Emerald-client`。三个都是明确 bug。

---

## Fix 1：日记又点不开（上一轮 visible:false 的回归）

**根因**：上一轮为消除闪烁，把窗口改成 `visible:false` 创建、就绪后再 `win.show()`（`DiaryDetailWindow.tsx:19-27`）。但 **`win.show()` 被权限拦了**——`src-tauri/capabilities/diary-detail.json` 的 permissions 里**没有 `core:window:allow-show`**（只有 start-dragging/close/set-position/set-focus/set-title）。窗口创建出来是隐藏的，show 调用被拒 → 永远不显示 → 「点不出来」。

**另一个隐患**：`DiaryDetailWindow.tsx` 用 `requestAnimationFrame(() => win.show())`。隐藏窗口的文档可能被判为 hidden，**rAF 会被浏览器暂停**，回调不触发 → 即使有权限 show 也不执行。

**修法（两处都改，才稳）**：
1. `capabilities/diary-detail.json` 的 permissions 加上：
   ```
   "core:window:allow-show",
   "core:window:allow-hide"
   ```
2. `DiaryDetailWindow.tsx` 别只靠 rAF。改成 initTheme 完成后**直接 show**，并加一个超时兜底：
   ```ts
   initTheme().catch(()=>{}).finally(() => {
     win.show().catch(()=>{});                 // 直接调，不靠 rAF
   });
   // 再加保险：无论如何 300ms 后兜底 show 一次
   const t = setTimeout(() => win.show().catch(()=>{}), 300);
   return () => clearTimeout(t);
   ```

**验收**：点日记 → 窗口正常显示、无闪烁、可拖拽；重复点同一条聚焦已开窗口。

---

## Fix 2：偏好设置页无法下滑，底部被截断

**根因**：偏好弹窗（`ChatWindow.tsx`）结构缺少滚动：
- 弹窗框（:139-144）`margin:'auto', width:min(540px,92vw), overflow:'hidden'`，**没有 maxHeight**。
- 内容区（:170）`padding, display:'grid', gap:18`，**没有 overflowY、没有 maxHeight**。

内容（尤其「外观」tab 行数多）比视口高时，弹窗整体超出屏幕、`margin:auto` 上下都溢出，加上框 `overflow:hidden` 且内部不滚动 → 底部几行够不到。

**修法**：让弹窗框限高、内容区可滚：
- 弹窗框（:139）加 `maxHeight:'90vh', display:'flex', flexDirection:'column'`（保留 `overflow:hidden` 以裁圆角）。
- 头部（:145 标题栏）和 tab 栏（:155）保持固定。
- 内容区（:170）改为 `flex:1, minHeight:0, overflowY:'auto'`（其余 padding/grid 不变）。

**验收**：三个 tab 内容超长时都能滚动到底，最后几项可见可操作。

---

## Fix 3：聊天背景设了没用（不透明内容层盖住了）

**根因（你猜得对）**：背景图层 `.chat-ui__background`（`globals.css:112`）在 `z-index:0`，且 `.chat-ui__body > *:not(.chat-ui__background)` 被设为 `z-index:1`（:128）盖在其上。而 `ChatPanel` 根容器（`ChatPanel.tsx:1607-1609`）背景是**不透明的 `var(--paper)`**，正好把背景图整个盖住。梦境那边对应的内容层是透明的（所以梦境背景能透出来），聊天直接搬结构却保留了不透明 paper。

> 另外 `.chat-ui__background::after`（:121）已经叠了一层 `var(--paper)` 0.62 透明度的柔化遮罩，用于保证文字可读——这层是该留的；真正多余的是 ChatPanel 自己的不透明底。

**修法**：设了背景图时，让聊天内容层透明/半透明，把图透出来：
1. `ChatWindow.tsx` 把「是否有背景图」传给 `ChatPanel`（如 `hasChatBackground={!!chatBackground.dataUrl}`，:1014 附近）。
2. `ChatPanel.tsx:1609` 根背景改为条件式：有背景图 → `transparent`（靠 `::after` 遮罩保证可读）；无背景图 → 维持 `var(--paper)`。
3. 顺带检查内部仍为整片不透明 `var(--paper)` 的层（如头部 :1615），有背景图时一并改 `transparent` 或半透明 `oklch(... / 0.x)`，否则它们还是会挡住图。气泡/卡片那种局部背景（paper-2、ink 等）可保留，不影响整体透出。

> 若希望背景更明显，可把 `::after` 的 `opacity:0.62` 调低（如 0.4）做成可调，但非必须。

**验收**：在偏好里设置聊天背景图后，聊天区域能看到背景（带柔化遮罩），文字仍清晰；不设背景时维持原 paper 底，无异常。

---

### 建议顺序
1. Fix 1（日记 allow-show + 直接 show）— 先让日记恢复
2. Fix 2（弹窗限高 + 内容可滚）— 小改
3. Fix 3（ChatPanel 背景条件透明）— 注意把头部等不透明层一并处理
