# 施工单 03 — 第一跳精度加固（IDF 降权 + 证据门槛 + 相关性闸门）

> 给 CC 的执行单。**前置**：施工单 01（n-gram）、02（去名/标点噪声 + 修无匹配兜底）已落地并验证。
> 本单仍**不做二跳**（二跳=施工单 04，等本单精度达标后再做）。
> 目标文件：`core/memory/episodic_memory.py`（`retrieve()` 的候选 + 评分两段）。

---

## 0. 为什么是这单（02 后 trace 证据）
2026-06-20 trace（20 轮，42 命中）：去名彻底见效（`叶瑄` 84→0），无意义短句正确返回空（10/20 selected=0）。
但**单个常见词命中就能拽出离题记忆**：
- 「今天叶瑄难得说了很多情话」→ 命中 `今天` → **用户被威胁强迫服从**（离题、基调糟）
- 「我靠在床头」→ 命中 `在床` → 又是 **用户被威胁强迫服从**
- 「为什么叶瑄总喜欢说接住…出戏」→ 命中 `喜欢` → 表白/看法/AI情感三件套，**错过真话题"接住"**
- `喜欢`×15 成了新的高频噪声词，一命中就盖过更具体的词。

根因：① 常见词（`喜欢/今天/方式`）与具体词（`接住/依赖/撒娇`）**同等权重**；
② **单个 gram 命中即入选**，之后 `strength`（上限1.0）把它顶上去，而 `relevance_bonus` 才 0.2，压不住。

修法三件：IDF 给常见词降权 → 单个常见词不够格入选 → 相关性弱时压低高强度记忆。

---

## 1. 改法（替换 `retrieve()` 的两段）

### 步骤 1：候选段 —— 加 IDF + 证据门槛
把现有候选块（约 274–290，从 `candidate_ids = set()` 到无匹配 `return` 之前）替换为：
```python
# 候选集：n-gram 命中 topic_keywords + raw_facts；IDF 降权 + 证据门槛
candidate_ids = set()
matched_map: dict = {}   # mem_id -> set(命中 gram)
df: dict = {}            # gram -> 文档频次（多少条记忆含它）
query_grams: set = set()
idf: dict = {}
if topic:
    from core.text_match import ngram_tokens
    _clean = topic.replace(char_name, "  ") if char_name else topic
    query_grams = ngram_tokens(_clean, stopwords={char_name} if char_name else None)
    for mem in memories:
        keywords_text = " ".join(mem.get("topic_keywords") or mem.get("tags", []))
        facts_text = " ".join(mem.get("raw_facts", []))
        haystack = keywords_text + " " + facts_text
        matched = {g for g in query_grams if g in haystack}
        if matched:
            matched_map[mem["id"]] = matched
            for g in matched:
                df[g] = df.get(g, 0) + 1

    N = max(1, len(memories))
    # IDF：出现在越多记忆里的 gram 权重越低
    idf = {g: math.log((N + 1) / (c + 1)) + 1.0 for g, c in df.items()}
    # “具体词”：只出现在少数记忆里（默认 ≤10%）的 gram，如 接住/依赖/撒娇
    SPECIFIC_DF_FRAC = 0.10
    _specific_cap = max(1, int(SPECIFIC_DF_FRAC * N))
    specific = {g for g, c in df.items() if c <= _specific_cap}

    # 入选证据门槛：至少命中一个具体词，或至少命中两个不同词。
    # 单个常见词（今天/喜欢/方式）不足以单独入选。
    for mid, matched in matched_map.items():
        if (matched & specific) or (len(matched) >= 2):
            candidate_ids.add(mid)

# 无真实词面命中（或证据不足）：主路径不强行倒高强度记忆，交给 fallback。
if topic and not candidate_ids:
    logger.debug("[episodic.retrieve] 无足够词面证据，主召回返回空，交给 fallback uid=%s", user_id)
    return ([], []) if return_trace else []
```
> 注意：`hit_counts` 不再需要（被 `matched_map`/`idf` 取代），同时删除旧的
> `_gram_total = max(1, len(query_grams))` 那行（评分段会改）。

### 步骤 2：评分段 —— IDF 加权相关性 + 相关性闸门 strength
把评分循环里这两行：
```python
relevance_bonus = 0.2 * min(hit_counts.get(mem["id"], 0) / _gram_total, 1.0)
score = strength * decay + emotion_bonus + relevance_bonus
```
替换为：
```python
REL_SCALE = 5.0   # idf_sum 归一化尺度（约“一个稀有词≈满分”），据 trace 可调
idf_sum = sum(idf.get(g, 0.0) for g in matched_map.get(mem["id"], ()))
relevance_norm = min(1.0, idf_sum / REL_SCALE)
# 相关性闸门：弱相关时把高强度记忆压低（×0.4），强相关时放行（×1.0），
# 防止“单词命中 + 高 strength”把离题记忆顶上来。
score = strength * decay * (0.4 + 0.6 * relevance_norm) + emotion_bonus + 0.3 * relevance_norm
```

### 步骤 3：trace 加 `rel`，删 `kw_legacy`
`return_trace` 段（约 383）：把 `kw_legacy` 字段删掉（01/02 的 A/B 已完成），
每条 trace item 加 `"rel": round(relevance_norm, 3)`（需在评分时把 relevance_norm 存进一个
`rel_map[mem["id"]]` 供 trace 段读取），方便你按相关性强弱核对命中质量。

---

## 2. 预期效果（对照上面 4 个坏例）
- 「今天…情话」：只命中 `今天`（常见、单个）→ **不入选** → 不再吐 被威胁服从。
- 「靠在床头」：只命中 `在床`（单个）→ 不入选。
- 「喜欢撒娇」：`撒娇` 具体 → 撒娇记忆入选并排前；`喜欢` 单常见 → 三件套**不再入选**。
- 「总喜欢说接住出戏」：`接住` 具体 → 接住记忆排前，**命中真话题**；`喜欢` 三件套被挤掉。

## 3. 风险与护栏
- **会不会把对的也滤掉？** 关键看 `SPECIFIC_DF_FRAC`（0.10）和 `REL_SCALE`（5.0）。先用默认值，
  **看 trace 的 `rel` 分布**：若发现该召回的具体词被判成"不具体"（比如某词其实只在 3 条里但 N 小导致 cap=1），
  调高 `SPECIFIC_DF_FRAC` 到 0.15；若相关的整体 `rel` 偏低导致被 MIN_SCORE 砍，调高 `REL_SCALE`→更宽松。
- `MIN_SCORE=0.15` 暂不动；新公式下弱相关分数会自然走低，观察是否需要微调到 0.2。
- **不要动**：emotion_texture 去重、N2-A `allow_strengthen=False`、emotion_bonus、decay 地板。
- `math` 已在文件顶部导入（`math.exp` 在用），无需新增 import。

## 4. 验收（trace 自验）
重启后端后跑若干轮，看当天 `recall_trace/*.jsonl`：
1. 上面 4 个坏例（或同类）：离题三件套 / 被威胁服从 **不再因单常见词被选中**。
2. 真话题词（接住/依赖/撒娇/不安）：仍稳定命中对应记忆，且其 `rel` 明显高于残余噪声。
3. `kw` 命中频次榜上不再有"单个常见词霸榜"（`喜欢` 不再 ×15 拉同一批）。
4. `pytest`（episodic 用例）通过；未动 `tag_rules.py`，跳过 `run_eval.py`。

## 5. 下一步
精度达标后 → **施工单 04：关键词二跳**。此时种子（hop-1 命中）已是干净的具体词，
二跳经 `_load_index` 倒排索引扩到共享具体关键词的记忆才有意义；护栏沿用
`docs/memory-recall-audit.md` 的四条（分数衰减、只补1–2条、过 MIN_SCORE、避免拉回 resolved/低强度）。
