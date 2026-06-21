# 第三轮修复：日记点击 / 阅读显示 / 主题切换

> 仓库：后端 `D:\ai\qq-st-bot`，前端 `D:\ai\Emerald-client`。
> 三个都是上一轮改动带出的回归 / 设计缺口。Fix 1、Fix 3 是明确 bug，改动小；Fix 2 含一处设计。

---

## Fix 1（明确 bug）：日记点击无反应

**根因**：`Emerald-client/src/windows/chat/components/SubDiary.tsx` 的 `openEntry()` 里：
```ts
const existing = WebviewWindow.getByLabel(label);
if (existing) { existing.then(w => w?.setFocus()).catch(()=>{}); return; }
new WebviewWindow(label, {...});
```
`WebviewWindow.getByLabel()` 返回的是 **Promise**，`if (existing)` 对 Promise 恒为真 → 每次都进「已存在」分支、`w` 实为 `null`、`setFocus` 空操作后 `return`，**`new WebviewWindow` 永远不执行**，所以点击毫无反应。窗口路由（`main.tsx` `diary-detail`）、`DiaryDetailWindow.tsx`、capability（`diary-detail.json` 的 `diary-detail-*`）都没问题。

**修法**：把 `openEntry` 改成 `async`，await 后再判：
```ts
async function openEntry(item: DiaryListItem) {
  const charId = activeCharId || '';
  const label = `diary-detail-${charId}-${item.date}`;
  const existing = await WebviewWindow.getByLabel(label);   // 关键：await
  if (existing) { await existing.setFocus(); return; }
  const params = new URLSearchParams({ window: 'diary-detail', date: item.date, char: charId });
  const w = new WebviewWindow(label, { url: `index.html?${params.toString()}`, /* …其余不变 */ });
  w.once('tauri://error', e => console.error('[diary] 窗口创建失败', e));  // 加错误日志便于排查
}
```
> 注意 label 含中文 char_id/日期一般 OK，但 Tauri 窗口 label 仅允许 `a-zA-Z0-9-/:_`。若 `charId` 可能含中文/特殊字符，需先 slug 化（如 hash 或替换非法字符），否则创建会再次静默失败。建议顺手处理。

**验收**：点日记条目 → 弹出独立小窗、可拖出主窗口；重复点同一条 → 聚焦已开窗口而非新建。

---

## Fix 3（明确 bug）：活动页日/夜切换闪一下还原

**根因**：`Emerald-client/src/windows/activity/ActivityWindow.tsx` 的 `handleThemeToggle` 自成一套「遍历所有主题 `(index+1)%themes.length`」逻辑，绕开了标准的日/夜槽位系统。聊天窗用的是 `shared/theme/registry.ts` 的 `toggleDayNight()`（见 `windows/chat/components/Ribbon.tsx:146`），它会更新 `chat.theme.active` 并在 auto 模式下切回 manual。活动页那套不更新 active/slot/mode，于是 auto 模式的 `applyByMode` 定时器（每 10 分钟）或其它重应用会按 active 槽位把主题刷回去 → **闪一下还原**。

**修法**：让活动页复用标准入口，和聊天窗一致：
1. `ActivityWindow.tsx`：`handleThemeToggle` 改为直接调 `toggleDayNight()`（从 `shared/theme/registry` 引入），删掉 `listThemes()` 那套循环。
2. ribbon 的图标/文案改成反映 `getDayNight().active`（`day`/`night`），而不是 `theme === 'dark'`。订阅 `subscribe` 时用 `getDayNight().active` 更新（照搬 `Ribbon.tsx:78-80`）。

**验收**：活动页点左下角日/夜 → 立即切换且**不回弹**；与聊天窗切换状态保持一致。

---

## Fix 2：阅读「书名乱码」+「页面空白」

### 2a（明确 bug）中文书名变下划线

**根因**：`qq-st-bot/admin/routers/reading.py:54` `_UNSAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._\-]")` 把所有中文字符替换成 `_`，于是中文书名整串变 `____.pdf`。

**修法**：保留 Unicode 文字，只清掉路径分隔符和真正危险的字符。`Path(raw).name` 已去目录前缀，正则改为保留 CJK：
```python
# 替换 Windows/路径非法字符与控制字符，保留中文等 Unicode 文字
_UNSAFE_CHAR_RE = re.compile(r'[\\/:\*\?"<>\|\x00-\x1f]')
```
（或 `re.compile(r"[^\w.\-]", re.UNICODE)`——Python3 `\w` 默认匹配 CJK。）两处调用（:121 / :344）不变。

> 附带：前端会话栏标题恒显示「—」，因为后端 `ReadingSession.to_dict()` 返回的是 `filename`，而前端 `ReadingState.title` 取的是 `title` 字段（不存在）。顺手统一：后端补 `title` 字段，或前端改读 `filename`。

### 2b（设计）页面空白 → 文字为主 + 按需看原页 + 手动开关

**根因**：当前「一起看书」是**纯文本提取**（pypdf 抽文字），**不渲染 PDF 视觉页面**。扫描版/图片型 PDF 提不出文字 → 显示空白。`pdf_reader.extract_pages` 仅在「全书无文字」时报 OCRRequired，单页空文本会被静默存成空串。

**已确认的产品方向（用户拍板）**：**能提文字就显示文字（默认）；提不出就显示该页原始图像；并提供手动开关在「文字 / 原页」之间切换。**

**实现设计**：

1. **保留源 PDF**（当前没存）。渲染原页需要原始字节：
   - 若已做书库方案（round2 任务 D）：源文件就在 `data/library/books/`，直接用。
   - 若没做：在 `start_reading` 落 session 时，把上传的 PDF 原字节存一份到 session 目录（如 `…/{session_id}/source.pdf`），供渲染端点读取。

2. **后端加渲染端点**：`GET /activity/reading/page_image?session_id=&page=` → 用 **PyMuPDF（fitz）** 把指定页渲染成 PNG 返回（`page.get_pixmap()` → PNG bytes，`Response(media_type="image/png")`）。
   - `requirements.txt` 加 `PyMuPDF`。fitz 渲染质量/速度都好，也能顺带做文本提取（可不替换现有 pypdf，仅用于渲染）。
   - 渲染结果可按 `…/{session_id}/page_img/{n}.png` 缓存，避免重复渲染。

3. **`extract_pages` 标注空页**：单页 `text.strip()` 为空时，在 session/page 元数据里标 `needs_image=true`（或前端拿到空 text 即视为需图），用于自动回退。

4. **前端 `ReadingPage.tsx`**：
   - 每页：`text` 非空 → 默认显示文字；`text` 为空 → 自动显示原页图像（调 `page_image`）。
   - 顶部加「文字 / 原页」手动切换开关（toggle），用户可随时强制看原页或回文字。开关状态可用 `uiPreferences`（如 `activity.reading.viewMode`）记忆。
   - `shared/api/activity-api.ts` + `src-tauri/src/lib.rs` 加取图命令（返回 PNG bytes / base64 或本地临时文件 URL 给 `<img>`）。

**验收**：文本型 PDF 显示文字；扫描版 PDF 自动显示原页图像而非空白；任意页可手动在「文字/原页」间切换并记忆选择；中文书名正常显示。

---

### 建议顺序
1. Fix 1（日记 await）— 一行级修复
2. Fix 3（主题 toggleDayNight）— 小改
3. Fix 2a（文件名正则）— 小改
4. Fix 2b（原页渲染）— 含新依赖+端点+前端开关，单独提交
