# 记忆回流审计 & 优化方案

> 给 CC 的执行交接文档。基于对读路径代码的实读（2026-06-19）。
> 关键文件：`core/pipeline.py`(fetch_context)、`core/prompt_builder.py`、
> `core/memory/episodic_memory.py`、`core/memory/event_log.py`、`core/memory/mood_state.py`、
> `core/tag_rules.py`、`core/lore_engine.py`、`core/data_paths.py`。

---

## 一、叶瑄"能读到什么、什么时候读到"（完整回流地图）

所有回流在 `fetch_context()`（并发拉取）+ `prompt_builder.build()`（按层注入、tag 门控、超 20k 裁剪）里发生。

| 层 | 来源函数 | 召回机制 | 注入条件 | 中文匹配是否OK |
|---|---|---|---|---|
| `9_history` 短期 | `short_term.load_for_prompt` | 最近 N 轮原样 | 总是 | — |
| `mid_term` 中期 | `mid_term.format_for_prompt` | 12h 内压缩桶 | 有内容时 | — |
| `6c_episodic` 情景 | `episodic.retrieve(topic=content, top_k=3)` | `strength×decay + emotion_bonus + relevance_bonus`，再做 emotion_texture 去重选 top_k | 总是跑，`MIN_SCORE=0.15` 过滤 | ❌ 见发现 A |
| `6c_episodic_fallback` 兜底 | `retrieve_fallback` | 7天内 `strength≥0.6` 高强记忆，排除近期已说过的 | 主召回弱/空时兜底 | — |
| `9.5_episodic_top` | episodic 结果第一条 | 挪到 history 后吃 recency 红利 | episodic 有结果时 | — |
| `6b_event_search` 事件日志 | `event_log.search` | 查询切 2/3/4 字 **n-gram** × intensity × 时间衰减 | 30 天窗口 | ✅ |
| `6a_user_identity` | `user_identity.format_for_prompt` | 用户稳定行为模式（per char） | 有内容 | — |
| `5.1_user_facts` | `user_facts.format_for_prompt` | 跨角色全局事实（uid-only） | 有内容 | — |
| `5_profile` | `user_profile.load` | 用户画像 | 有内容 | — |
| `5.5_lore` 世界书 | `lore_engine.match` | 关键词 substring（含最近5条历史扩面） | 命中时 | ✅(看条目写法) |
| 情绪 | `mood_state.get_current/get_intensity` | 给 episodic 加 `emotion_bonus`；召回强记忆后 `nudge_from_memory` 反向漂移当前情绪 | 总是参与评分 | — |
| `3.5_period`/`3.6_watch`/`3.7_sensor`/`3.8_activity`/`6d_diary`/`6e_feeling` | 各源 | **`tag_rules` 门控** | `_tags` 命中对应触发集才注入 | ✅ |
| `6f_dream_afterglow`/`6g_dream_impression` | dream | 梦境余韵/印象 | 有内容 | — |

**情绪召回 ≠ 一个独立检索层**，而是两条线：
1. 当前情绪给情景记忆评分加成（`emotion_peak == current_mood` 时 `+0.15 + intensity×0.15`）；
2. 召回到的强情绪记忆（strength>0.7 且是当前情绪的邻居）会反过来把当前情绪强度往上推。
这是一个**情绪⇄记忆的小反馈环**，注意别和二跳/网状叠加后失控（见下）。

---

## 二、关于"先搜一下目前的关键词对应什么"——结论：你有**两套互不相通**的关键词系统

### 系统 1：`tag_rules.py` 的 TAG_RULES（14 条手写规则）
- substring 匹配用户消息 → 产出 `topic.energy` / `emotion.down` / `topic.relation` 这类 tag。
- 用途：**只用来门控 prompt 层**（period/watch/sensor/activity/diary/feeling 要不要注入）。
- **不参与情景记忆召回。** 中文 substring，工作正常。

### 系统 2：episodic 的 `topic_keywords`（LLM 逐条抽取，存在每条记忆上）
- 写入时 LLM 给每条记忆抽 `topic_keywords`，并建倒排索引 `memory_index`（`tag -> [memory_id,...]`，见 `_load_index` / `_rebuild_index`）。
- 用途：本应驱动情景召回。**这就是你"二跳"要用的图结构。**

### ⚠️ 三个关键发现（直接决定你三个功能怎么做）

**发现 A：情景召回的"第一跳"对中文基本失效。**
`retrieve()` 里候选匹配是 `topic_words = topic.split()`，而 `topic` = 用户原始消息。
中文没有空格 → 整句变成**一个 token** → 拿整句去 `topic_keywords + raw_facts` 里做 substring →
几乎永不命中 → `candidate_ids` 落空 → 退化成"**全量记忆按 strength 评分**"。
也就是说：当前情景召回**几乎不是按内容相关性**，而是按"强度+情绪+时间"在挑。
对比之下 `event_log.search` 用的是 n-gram，中文是对的。**两套不一致，episodic 是坏的那个。**

**发现 B：倒排索引已经存在，但读路径没用它。**
`retrieve()` 里 `index = _load_index(...)` 被加载了，但候选选择用的是全量 for 循环，
`index` 实际**没参与**。等于二跳需要的图结构已经建好了，却闲置着。

**发现 C：没有任何统一的"这轮召回命中了哪条"的记录文件。**
`build()` 返回的 `debug_info` 只有 `layers_activated / token_estimate / tags / pending_paths`，
而且**只在内存里返回、不落盘**，也**不含命中的具体 episodic id / event_log 块 / lore 条目 / 各自分数**。
日志里有零散的 `logger.info`（fallback、tag debug、layer_size），但没有一处能让你回看单轮全貌。
→ 你想要的检查文件确实**目前没有**，是真需求。

---

## 三、对你三个功能的判断

### 功能 1：关键词二跳召回 —— 可做，但**必须先修第一跳**
- **先决条件（发现 A）**：把 `retrieve()` 的候选匹配从 `topic.split()` 换成中文 n-gram
  （直接复用 `event_log.search` 的 2/3/4-gram 切法），或对 `topic_keywords` 做包含匹配。
  否则二跳建在一个本就不命中的第一跳上，等于放大噪声。
- **二跳实现**：hop1 = query→命中记忆集 M1；hop2 = 取 M1 里每条的 `topic_keywords`，
  经倒排索引 `_load_index`（发现 B，已现成）找到共享关键词的其他记忆 M2。成本很低。
- **必须加的护栏**（否则主题漂移）：
  - 二跳分数显式衰减（如 `score_hop2 = score_hop1_source × 0.4 × 共享关键词数/总数`）；
  - 二跳最多补 1–2 条，且要过 `MIN_SCORE`；
  - 二跳不拉回 `resolved/elapsed` 或低 strength 记忆；
  - 二跳结果参与现有 emotion_texture 去重，避免和 hop1 重复。

### 功能 2：网状数据库（给数据之间加关联权重）—— **判断：先别单独建，它和二跳是同一条路的轻重两档**
- 本质：把"共享关键词"升级成**显式带权边** `memory_i ↔ memory_j (weight)`。
  你现在做的二跳（经倒排索引找共享关键词）**就是一张隐式图的深度=2 BFS**，权重默认=共享关键词数。
- **建议顺序**：先上功能 1 的二跳（隐式图，零新存储），观察召回质量。
  只有当你确实需要下面任一项时，才值得把边**物化**成显式权重：
  1. 记忆间**共现/被一起召回**的统计权重（"每次提 A 都会想到 B"）；
  2. **非关键词**关联（情绪相似、时间邻近、因果链），关键词图表达不了；
  3. 多跳（>2）联想。
- **物化方案（真要做时）**：一个 `memory_edges.json` 邻接表（`{mem_id: [{to, weight, type}]}`），
  在 `post_process` 写记忆时增量更新边权，读路径做带权 BFS。**不需要引 neo4j 这类外部图库**——
  规模（≤200 条记忆）下 JSON 邻接表完全够，也符合项目"data 走 sandbox、轻量文件"的风格。
- 一句话：**二跳 = MVP，网状 = 二跳验证有效后的升级**，不要并行造两套。

### 功能 3：召回检查文件 —— **强需求，现在没有，建议立刻做（且应排在 1、2 之前）**
没有它，你改第一跳/二跳/网状全是盲调。建议：

- **让召回函数返回命中明细**：`retrieve()` / `event_log.search()` / `lore_engine.match()` 目前只返回内容，
  需要额外回传 `[{id/block, score, 命中关键词, why}]`（可加一个 `return_trace=True` 参数，避免破坏现有签名）。
- **每轮落一条 trace**：在 `pipeline` 里把 `fetch_context` 的命中明细 + `build` 的 `debug_info`
  合并，写到 `data/.../recall_trace/{date}.jsonl`（路径走 `core/sandbox.get_paths()`，遵守 Hard Rule 1）。
  建议每行 schema：
  ```json
  {
    "ts": "...", "uid": "...", "char_id": "yexuan", "trigger": "user|scheduler:xxx",
    "query": "用户原话",
    "tags": ["topic.relation"],
    "episodic_hits": [{"id":"e_42","score":0.62,"hop":1,"kw":["吵架"],"summary":"..."}],
    "episodic_fallback_used": false,
    "event_log_hits": [{"date":"...","score":0.4,"kw":["实习"]}],
    "lore_hits": ["..."],
    "mood": {"current":"calm","intensity":0.3},
    "layers_activated": ["6c_episodic","6b_event_search",...],
    "token_estimate": 7321,
    "pruned_layers": []
  }
  ```
- **加个 admin 只读接口**（仿 `admin/routers/memory.py`）：`GET /debug/recall?date=...&limit=...`
  直接看最近 N 轮命中了什么、分数多少、为什么没命中——这就是你要的"专门检查文件"。

---

## 四、建议落地顺序

1. **功能 3 先做**（召回 trace + 只读接口）：给后续所有调优装上仪表盘。
2. **修第一跳**（发现 A：episodic 候选匹配换 n-gram）：这一步可能单独就显著提升相关性，先量一量。
3. **功能 1 二跳**（复用倒排索引 + 四条护栏），用 trace 对比开/关效果。
4. 视情况再决定 **功能 2 网状物化**（只有当二跳证明"关联召回有价值但关键词表达不够"时）。
5. 任何动 `tag_rules.py` 的改动后跑 `python tests/run_eval.py`（Hard Rule 4）；
   动 episodic 写回/strength 逻辑注意 N2-A 的"读路径不写回"约定（`allow_strengthen=False`）别破坏。

---

## 五、几个要顺手注意的债
- **倒排索引闲置**（发现 B）：要么在二跳里真正用起来，要么标记清楚，别让它继续是"建了不用"的半成品。
- **情绪⇄记忆反馈环**：二跳/网状会拉回更多强情绪旧记忆 → `nudge_from_memory` 会更频繁推动情绪。
  上二跳后留意叶瑄情绪是否更容易被旧记忆带跑，必要时给二跳召回的记忆**不触发** `nudge`（只读不推）。
- **episodic vs event_log 双匹配口径不一**：长期建议统一到一套中文 n-gram 工具函数，两处共用。
