# 一起看书 — Reading Activity (P0)

## 定位

**"一起看书"是 Reality-side Activity，不是 Trigger/Stimulus。**

| 分类 | 说明 |
|---|---|
| 类型 | Activity（阅读活动） |
| 触发方式 | **仅由用户显式 API 调用启动**，禁止自动触发 |
| 是否接 perceive_event | **否** |
| 是否接 Dream/Scenario/Mirror | **否** |
| 是否接 trigger/stimulus | **否** |
| 是否接 scheduler | **否** |

PDF 解析是 **Tool 能力**（`core/activity/pdf_reader.py`），阅读过程是 **Activity Session**（`core/activity/reading_session.py`）。两者职责不混用。

---

## P0 支持范围

| 功能 | P0 状态 |
|---|---|
| 文本型 PDF（可提取文本） | ✅ 支持 |
| 扫描版 / 图片 PDF | ❌ 明确报错（不支持 OCR） |
| 多用户共享阅读 | ❌ P0 单用户 |
| 批注 / 高亮 | ❌ 后续版本 |
| 嵌入式图片提取 | ❌ 后续版本 |
| OCR | ❌ 后续版本 |
| 长期记忆写入 | ❌ 默认不写（见隔离规则） |
| RAG / 向量检索 | ❌ 后续版本 |

---

## 存储路径

```
data/runtime/activity/reading/{char_id}/{uid}/{session_id}/
  metadata.json        — ReadingSession 元数据
  pages/
    1.txt              — 第 1 页文本
    2.txt              — 第 2 页文本
    ...
```

路径通过 `core/data_paths.DataPaths.reading_session_dir()` 获取，**全路径经过 `_p()` 沙盒检查**，禁止硬编码。

### char_id 隔离

- `yexuan` 的 session 存在 `reading/yexuan/{uid}/...`
- `character_b` 的 session 存在 `reading/character_b/{uid}/...`
- 两角色路径完全独立，`find_active_session` / `load_session_by_id` 均以 char_id 为边界。

### 文件名安全

上传文件名经 `_sanitize_filename()` 处理：
1. `Path(raw).name` 提取最终文件名部分（去掉路径前缀）
2. 非 ASCII / 特殊字符替换为下划线
3. 长度截断到 128 字符

session_id 为 `uuid4().hex`（32 位十六进制），存储前经 `safe_user_id()` 校验。

---

## HTTP API

所有端点挂载在 `/activity/reading/` 下，需要 Bearer token 鉴权。

### POST `/activity/reading/start`

上传 PDF 并创建阅读 session。

**Form 字段**

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | UploadFile | PDF 文件，≤ 50 MB |
| `start_page` | int | 起始页（1-indexed），默认 1 |
| `uid` | str | 用户 id，留空取 `default_user_id` |

**返回**（ReadingSession 全部字段）

```json
{
  "session_id": "a3f1...",
  "uid": "owner",
  "char_id": "yexuan",
  "file_id": "f_abc123...",
  "filename": "novel.pdf",
  "total_pages": 42,
  "current_page": 1,
  "created_at": "2026-06-09T10:00:00+00:00",
  "updated_at": "2026-06-09T10:00:00+00:00",
  "status": "active",
  "mode": "reading"
}
```

**错误**

| 状态码 | 触发条件 |
|---|---|
| 413 | 文件超过 50 MB |
| 422 | 扫描版 PDF / start_page 越界 / 解析失败 |

---

### GET `/activity/reading/state`

返回当前 active session，无则 `{"active": false}`。

**Query**：`uid`（可选）

---

### GET `/activity/reading/page`

读取某一页文本。

**Query**：`session_id`、`page`（1-indexed）

**返回**

```json
{
  "page": 3,
  "total_pages": 42,
  "text": "...",
  "text_length": 1234
}
```

**错误**：`422` 页码越界，`409` session 已关闭，`404` session 不存在

---

### POST `/activity/reading/turn_page`

翻页并返回新页文本。

**Body**

```json
{
  "session_id": "...",
  "direction": "next",   // 或 "prev"
  "page": null           // 或 直接指定目标页码
}
```

`direction` 和 `page` 二选一；`page` 优先级高于 `direction`。

---

### POST `/activity/reading/close`

关闭 session，不写长期记忆。

**Body**

```json
{
  "session_id": "...",
  "brief_summary": "(可选，P0 不持久化到长期记忆)"
}
```

**返回**

```json
{
  "status": "closed",
  "session_id": "...",
  "filename": "novel.pdf",
  "total_pages": 42,
  "last_page": 15,
  "closed_at": "..."
}
```

---

### GET `/activity/reading/library`

列出书库（从 manifest.json 读取，含 title / category / total_pages）。

**返回**

```json
{
  "books": [
    {
      "book_id": "550e8400-...",
      "title": "基督山伯爵",
      "category": "小说",
      "filename": "基督山伯爵.pdf",
      "added_at": "2026-06-19T10:00:00+00:00",
      "total_pages": 219,
      "size_bytes": 2048000
    }
  ]
}
```

---

### POST `/activity/reading/library/add`

上传 PDF 到书库，写入 manifest.json。同名文件覆盖时保留 book_id。

**Body**：multipart，字段 `file`（PDF）

**返回**：manifest 条目 + `size_bytes`

---

### POST `/activity/reading/start_from_library`

从书库开始阅读（按 manifest book_id 查文件名，file_id 使用 book_id）。

**Body**

```json
{ "book_id": "550e8400-...", "start_page": 1, "uid": "" }
```

**返回**：与 `/reading/start` 相同的 ReadingSession 结构。

---

### POST `/activity/reading/library/delete`

从书库删除一本书（移除 manifest 条目 + 删除磁盘文件）。

**Body**

```json
{ "book_id": "550e8400-...", "with_insights": false }
```

`with_insights: true` 时同时删除 `insights/{book_id}/` 目录。

**返回**：`{ "deleted": true, "book_id": "..." }`

---

### POST `/activity/reading/library/rename`

修改书的显示名称（只改 manifest title，磁盘文件名不变，book_id 不变）。

**Body**

```json
{ "book_id": "550e8400-...", "title": "新名称" }
```

**返回**：更新后的 manifest 条目

---

### POST `/activity/reading/library/categorize`

设置书的分类。留空则恢复「未分类」。

**Body**

```json
{ "book_id": "550e8400-...", "category": "小说" }
```

**返回**：更新后的 manifest 条目

---

## 书库 manifest 设计

`data/library/manifest.json` 是书库元数据注册表，book_id 与文件名解耦：

- **book_id**：新增时生成一次（uuid4），此后永不变更，改名/分类均不影响 insights 路径和已有 session
- **迁移**：manifest 不存在时，自动扫描 `books/` 目录生成，沿用 `make_file_id(filename)` 作为 book_id 以兼容已有 insights
- **改名**：只改 manifest 的 `title` 字段，`filename` 与磁盘文件名保持原样
- **re-upload**：同名文件重传时保留原有 book_id（按 filename 匹配），只更新 `total_pages`

---

## 内存隔离规则

以下内容禁止写入，适用于 P0 全部操作：

| 禁止写入目标 | 原因 |
|---|---|
| `short_term` / `history` | 页面内容不是对话历史 |
| `event_log` 全文 | 避免污染事件流 |
| `user_hidden_state` | 阅读行为不触发隐性状态变化 |
| `afterglow` / `impression` | 无梦境关联 |
| `episodic_memory` / `mid_term` | 默认不写长期记忆 |

---

## LLM 接入边界（未来版本）

P0 不接 LLM。未来注入他 prompt 时，只允许：

- 当前书名
- 当前页码
- **当前页文本片段**（不超过 `MAX_PAGE_TEXT_CHARS` = 8000 字）
- 最近 1–2 页的轻量摘要
- 用户明确提问

**禁止**把整本 PDF 注入 prompt。

---

## 文件变更列表

| 文件 | 变更 |
|---|---|
| `requirements.txt` | 新增 `pypdf` |
| `core/data_paths.py` | 新增 `reading_char_root` / `reading_sessions_root` / `reading_session_dir` |
| `core/activity/__init__.py` | 新建（空） |
| `core/activity/pdf_reader.py` | 新建：PDF 文本提取工具 |
| `core/activity/reading_session.py` | 新建：ReadingSession dataclass + 工厂函数 |
| `core/activity/activity_store.py` | 新建：session 持久化层 |
| `admin/routers/reading.py` | 新建：HTTP API（5 个端点）；扩展书库 manifest 管理（+3 端点） |
| `core/data_paths.py` | 新增 `reading_library_manifest()` |
| `admin/admin_server.py` | 注册 `reading.router` 到 `/activity` |
| `tests/test_reading_activity.py` | 新建：18 个测试用例 |
| `docs/reading-activity.md` | 新建：本文档 |
