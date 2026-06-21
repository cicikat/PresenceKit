# 第四轮：书库开读超时 + 书库管理（删除/改名/分类）

> 仓库：后端 `D:\ai\qq-st-bot`，前端 `D:\ai\Emerald-client`。

---

## Fix 1（明确 bug）：点书报 `error sending request for url (.../start_from_library)`

**这不是连不上后端**（书库列表能加载、后端在线）。`error sending request for url (...)` 是 reqwest 的 **send 失败/超时** 的报错文案。

**根因**：`/activity/reading/start_from_library`（`admin/routers/reading.py:377`）在请求里**同步**做整本 PDF 文本提取（pypdf 跑完 219 页）+ `save_pages` 写 219 个 .txt 文件，耗时 >15s。而 Rust 端 `activity_reading_start_from_library`（`src-tauri/src/lib.rs:1297`）走 `activity_post` → `http_client()`，**超时只有 15s**（`lib.rs:22-29`）。超时后 reqwest 就抛出这句 `error sending request for url`。

同理，上传开读的 `activity_reading_start`（multipart，也用 `http_client()` 15s）对大 PDF 一样会超时。

**修法**：让这两个「开读」命令改用长超时客户端。
- 已有 `llm_http_client()`（`lib.rs:31`，120s）可直接复用，或新增一个 `reading_http_client()`（如 60–120s）。
- `activity_reading_start_from_library`：不要走 `activity_post`，改成内联用长超时 client POST（带 `authorized_request` + `.json(body)` + `.no_proxy()` 已含在 llm_http_client）。
- `activity_reading_start`：把里面的 `http_client()?` 换成长超时 client。

**（建议但非必须）后端优化**：开读时只提取并保存「当前页 + 前后几页」，其余页**懒加载**（翻到时再提取/缓存），把开读从「整本提取」降为「秒开」。这样既治本又顺带改善大书体验。若做了懒加载，timeout 问题也基本消失。

**验收**：点书库里 219 页的书 → 正常开读、不再报 error sending request。

---

## Fix 2（设计）：书库支持删除 / 改名 / 分类

**现状**：后端只有 `GET /reading/library`（列目录）、`POST /reading/library/add`（存文件）、`POST /reading/start_from_library`。**没有删除/改名/分类**。且 `book_id = make_file_id(filename)`——**book_id 由文件名派生**，一改名 book_id 就变，会让 `insights/{book_id}/` 感悟孤立、正在进行的 session 失配。所以不能简单地"重命名文件"。

**设计：引入书库清单（manifest），让 book_id 与文件名解耦**

1. **`data/library/manifest.json`**（新增，走 `core/data_paths.py` 加 `reading_library_manifest()`）：
   ```json
   {
     "books": [
       {
         "book_id": "uuid-或-内容hash（稳定，不随改名变）",
         "title": "用户可改的显示名",
         "category": "未分类",
         "filename": "磁盘真实文件名.pdf",
         "added_at": "ISO时间",
         "total_pages": 219
       }
     ]
   }
   ```
   - `book_id` 在 **add 时生成一次**（uuid4 或文件内容 sha256），此后永不变 → 改名/分类都不影响 insights、session、缓存。
   - `title` 是显示名（改名只改这里，磁盘文件名可不动）；`category` 用于分类分组。

2. **后端端点**（`admin/routers/reading.py`）：
   - `GET /reading/library` 改为**读 manifest**返回（含 title/category/分组），不再裸列目录。
   - `POST /reading/library/add`：生成稳定 book_id、存文件、追加 manifest 条目（title 默认取原文件名去扩展名、category 默认「未分类」）。
   - `POST /reading/library/delete` `{book_id}`：删 manifest 条目 + 删磁盘文件（insights 可保留或一并删，给个 `with_insights` 开关）。
   - `POST /reading/library/rename` `{book_id, title}`：只改 manifest 的 title。
   - `POST /reading/library/categorize` `{book_id, category}`：改 manifest 的 category。
   - `POST /reading/start_from_library`：改为**按 manifest 的 book_id 查 filename**，不再扫目录 + `make_file_id` 比对（更稳更快）。

3. **迁移**：首次加载时，若 `manifest.json` 不存在，扫描 `books/` 现有 PDF 自动生成 manifest（book_id 沿用 `make_file_id(filename)` 以兼容已存在的 insights；title=文件名、category=未分类）。这样你之前加的书不会丢。

4. **前端**（`src/windows/activity/components/ReadingPage.tsx` + `shared/api/activity-api.ts` + `src-tauri/src/lib.rs`）：
   - `BookListItem` 加一个「⋯」菜单：**改名 / 分类 / 删除**（删除带二次确认）。
   - 书库列表**按 category 分组**展示，顶部可加分类筛选。
   - `activity-api.ts` 加 `renameBook` / `deleteBook` / `categorizeBook`；`lib.rs` 加对应命令并在 `invoke_handler` 注册；`ReadingLibraryBook` 类型补 `title` / `category` 字段。

**验收**：书库每本书可改名、可归类、可删除；改名/分类后感悟与历史不丢失（book_id 稳定）；列表按分类分组；删除有确认。

---

### 建议顺序
1. Fix 1（超时换长 client）— 几行，先让书能点开
2. Fix 2（manifest + 增删改分类）— 较大，建议先后端 manifest，再前端菜单
