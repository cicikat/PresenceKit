# docs/vector-store.md — 语义向量库

> **阶段 A（已落地）**：sqlite-vec 本地向量库 + OpenAI 兼容 embedding API。
> **阶段 B（预留）**：`provider_kind: self_hosted` 切自托管 embedding 端点，接口不变。

---

## 1. 架构定位

向量库是**派生数据**，与 `memory_index.json`（episodic 倒排索引）同哲学：

- **真相源**：`episodic.json` / `event_log/*.md`（JSON/MD 不受影响）
- **向量库**：`vector_store.db`（sqlite-vec 虚拟表），随时可删除并从真相源 `rebuild()`
- 向量库出错 → **fail-open**，静默回退到现有关键词召回，主回复不受影响

---

## 2. 文件布局

```
data/runtime/memory/{char_id}/{uid}/
├── episodic.json          ← 真相源（不变）
├── event_log/             ← 真相源（不变）
└── vector_store.db        ← 派生（gitignore 自动覆盖，不进 git）
```

DB 路径通过 `path_resolver.resolve_path(scope, "vector_store")` 获得，
artifact key = `"vector_store"` 已注册进 `REALITY_USER_ARTIFACTS`。

---

## 3. Embedding 边界

**唯一对外接口**：`core/memory/embedding.py`

```python
async def embed(texts: list[str]) -> list[list[float]]:
    """文本 → 向量。失败抛 EmbeddingUnavailable（调用方 fail-open）。"""
```

- Provider 由 `config.yaml` 的 `embedding:` 块决定。
- 上层模块只 import `embed`，禁止直接拼 HTTP/SDK 调用。
- 阶段 A：`provider_kind: openai_compat`（OpenAI 兼容 `/embeddings`，复用 `openai` SDK）
- 阶段 B：`provider_kind: self_hosted`（HTTP 调自托管端点）——接口已预留，本期未实现

**config.yaml 样例**（真 key 只进 `config.yaml`，已 gitignore）：

```yaml
embedding:
  provider_kind: openai_compat
  base_url: https://your-embedding-endpoint/v1
  api_key: YOUR_EMBEDDING_KEY
  model: bge-m3
  dim: 1024       # 必须与 model 输出维度一致
  batch_size: 32
```

---

## 4. Vector Store API

`core/memory/vector_store.py` 对外提供三个函数：

| 函数 | 说明 |
|---|---|
| `async upsert(uid, char_id, source, source_id, ts, text)` | embed + 写两表；同一 `(source, source_id)` 自动更新 |
| `query(uid, char_id, query_vec, k, *, sources, since_ts)` | KNN 检索，可按 source / recency 过滤；返回 `[(source_id, distance, ts)]` |
| `async rebuild(uid, char_id)` | 删表重建：从 episodic.json + event_log 批量回填 |

以及 X2 引入的评分工具函数：

```python
def dist_to_sim(dist: float) -> float:
    """将 L2 距离转换为相似度 ∈ (0, 1]。越小距离 → 越大相似度。"""
    return 1.0 / (1.0 + dist)

def score_recall(semantic_sim, keyword_relevance, strength=0.5, decay=1.0) -> float:
    """
    融合召回分 = w_sem*semantic_sim + w_kw*keyword_relevance + w_str*(strength*decay)
    权重从 config recall.weights.{sem,kw,strength} 读取（默认 0.4/0.3/0.3）。
    embedding 不可用时 semantic_sim=0.0，w_sem 项自动置零（fail-open）。
    """
```

**配置**（`config.yaml`）：
```yaml
recall:
  weights:
    sem: 0.4      # 语义相似度权重
    kw: 0.3       # 关键词相关度权重
    strength: 0.3 # 强度×衰减权重
```

---

## 5. DB Schema

```sql
-- sqlite-vec 虚拟表（KNN 索引）
CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[DIM]);

-- 元数据旁挂（回指真相源）
CREATE TABLE vec_meta (
    rowid       INTEGER PRIMARY KEY,  -- 对齐 vec_items.rowid
    source      TEXT,                 -- 'episodic' | 'event_log' | 'profile' | 'web'
    source_id   TEXT,                 -- 回指真相源条目 id / turn_id
    ts          REAL,                 -- 原始时间戳（供 recency 过滤）
    text_preview TEXT                 -- 仅用于调试/溯源，非真相
);
```

---

## 6. Fail-Open 契约

- embedding API 超时/报错 → `EmbeddingUnavailable` → `upsert` 静默返回，主回复不变
- DB 文件缺失/损坏 → `_open_db` 返回 `None` → `upsert`/`query` 静默返回
- DIM 校验失败（`len(vec) != config.dim`）→ 跳过写入/查询，打 WARNING 日志
- `rebuild` 出错 → 记录异常，返回已成功写入数量（可能为 0）

---

## 7. 挂接点

| 挂接位置 | 动作 |
|---|---|
| `fixation_pipeline.reflect_to_episodic()` → `write_episode()` 之后 | `asyncio.ensure_future(vector_store.upsert(..., source='episodic'))` |
| `TurnEffects.capture_critical() / run_deferred_effects()` → `capture_turn()` 之后 | `asyncio.create_task(vector_store.upsert(..., source='event_log'))` |
| `pipeline.context.fetch()` → 并发任务启动前 | `await embed([content])` → `vs.query(k=8)` 得到 `_query_vec` + `_semantic_hits`；`query_vec` 传入 `event_log.search` 和 `episodic.retrieve`，结果也存入 context `semantic_hits` / `query_vec`（供 X3 复用） |
| `memory_janitor._check_vector_consistency()`（Brief 49，每日深夜） | `list_entries()` 对照 episodic.json / 近30天event_log 真相源统计孤儿数；超阈值（>20 或占比>10%）调用 `rebuild()`，否则只记观测日志 |

---

## 8. 只读观测接口 `/observe/vector*`

三个只读接口注册在 `admin/routers/observe.py`，均需 token 认证（`Depends(verify_token)`）。

| 路由 | 说明 |
|---|---|
| `GET /observe/vector` | 列出所有已知 uid（复用 `_get_known_users()`） |
| `GET /observe/vector/{uid}` | 返回 `stats`（总数 + by_source + dim）+ `entries`（最新 N 条 vec_meta，支持 `source=` 过滤和 `limit=`） |
| `GET /observe/vector/{uid}/search` | 语义检索：`q=` 嵌入后 KNN，返回 `(source_id, preview, distance, similarity)`；embed 失败时返回 `error: embed_failed` |

后端 helper 在 `vector_store.py`：
- `stats(uid, char_id)` — GROUP BY source，fail-open → `{total:0, by_source:{}, dim:N}`
- `list_entries(uid, char_id, *, source, limit, offset)` — ORDER BY ts DESC，fail-open → `[]`

两函数均调用 `_ensure_tables()` 防空库报错，均遵循 fail-open 契约。

---

## 9. 解锁路径

| 后续任务 | 依赖本模块 |
|---|---|
| P3 — 画像语义召回 | `query(sources=['profile'], since_ts=...)` |
| X2 — 召回融合公式 | ✅ 已落地：`score_recall()` 融合三路信号，`dist_to_sim()` 统一符号，`context['semantic_hits']` / `context['query_vec']` 供 X3 复用 |
| X3 — 网页自检索库 | `upsert(source='web', ...)` 写入同一 DB，复用 `embed()` 和 `query_vec` |
