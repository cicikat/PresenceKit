# 施工单 02 — 第一跳质量加固（去噪 + 修兜底）

> 给 CC 的执行单。**前置**：施工单 01（n-gram）已落地并验证有效
> （post-fix trace A/B：n-gram 命中 107 vs split 命中 3）。
> 本单**不做二跳**——二跳改到施工单 03，必须等第一跳去噪后再做（否则在噪声种子上扩散）。
> 目标文件：`core/text_match.py`、`core/memory/episodic_memory.py`、`core/pipeline.py`（传角色名）。

---

## 0. 为什么是这单（fresh trace 实测证据）
2026-06-20 trace，18 轮，407 条 episodic 命中：
- 匹配 gram 频次 top：`叶瑄`×84、`记得`×10、`情绪`×8、`问叶`×3、`什么`×3、`叶瑄。`×2、`瑄。`×2…
- **`叶瑄`（角色自己的名字）压倒一切**：用户几乎每句都喊名字 → 每句都命中一大票含"叶瑄"的记忆 → 拉回同样的高强度记忆（质问/表白/梦境）。例：「叶瑄咪。这样太像工具人了」只命中`叶瑄`，召回质问/表白/梦境，**完全错过"工具人"这个真话题**。
- **5/18 轮无真实命中 → 仍走"全量按 strength"兜底 → 又是那同 3 条**。例：「我以为你会帮我浇水」召回 日记被看/质问/表白。
- 标点/虚词 gram 误命中：`好。`、`什么`。

结论：01 让第一跳"开始匹配"，但匹配到的 ~90% 是名字噪声，加上兜底仍在倒同 3 条。这两点不修，二跳只会放大。

---

## 1. 改动总览（两处核心 + 一处传参）
1. **去噪**：n-gram 前剔除角色名 + 标点切段 + 虚词停用 gram。
2. **修兜底**：无真实命中时**不再全量按 strength 倒同 3 条**，主路径返回空，由 `retrieve_fallback` 兜底续上。
3. 把角色名传进 `retrieve()` 用于去噪。

---

## 2. 具体改法

### 步骤 1：升级 `core/text_match.py`
按标点切段（gram 不跨标点/空格），并支持停用集：
```python
import re

# 中文虚词 / 高频无信息 2-gram，可据 trace 增删
STOP_GRAMS = {
    "什么", "这么", "那么", "怎么", "这样", "那样", "这个", "那个",
    "觉得", "可以", "不是", "就是", "但是", "然后", "的话", "一下",
    "知道", "没有", "我们", "你们", "他们", "自己", "现在", "时候",
}

_SEG = re.compile(r"[^0-9A-Za-z一-鿿]+")  # 非 中/英/数 即为切段边界

def ngram_tokens(text: str, lengths=(2, 3, 4), *, stopwords: set | None = None) -> set[str]:
    """中文友好 n-gram：按标点切段（不跨段），过滤停用 gram。"""
    out: set[str] = set()
    stop = (stopwords or set()) | STOP_GRAMS
    for seg in _SEG.split(text or ""):
        if not seg:
            continue
        for n in lengths:
            for i in range(len(seg) - n + 1):
                g = seg[i:i+n]
                if g and g not in stop:
                    out.add(g)
    return out
```
这一步顺手干掉 `好。`、`叶瑄。`、`瑄。`、`，我` 这类标点噪声 gram。
**event_log.search 也调 ngram_tokens**，确认它仍正常（标点切段对它只会更干净，不影响评分）。

### 步骤 2：`retrieve()` 去角色名 + 传停用集
`core/memory/episodic_memory.py`，candidate 匹配处（约 276–286 行）：
```python
if topic:
    from core.text_match import ngram_tokens
    # 关键：先把角色名从 query 里抠掉，再切 n-gram，避免“叶瑄”+名字碎片刷屏
    _clean = topic
    if char_name:
        _clean = _clean.replace(char_name, "  ")
    query_grams = ngram_tokens(_clean, stopwords={char_name} if char_name else None)
    for mem in memories:
        ...
```
`retrieve()` 签名加参数 `char_name: str = ""`；`return_trace` 段里算 `query_grams` 的地方
（约 383 行 `_clean`）也用同一份 `query_grams`，保证 trace 反映真实命中。

### 步骤 3：`fetch_context` 把角色名传下去
`core/pipeline.py` 调 `retrieve(...)` 处（约 240、258 行两处）加
`char_name=scoped_character.name`。`retrieve_fallback` 不需要（它不按 query）。

### 步骤 4：修"无匹配兜底"——别再倒同 3 条
`core/memory/episodic_memory.py` 约 289–291：
```python
# 无匹配时全量参与评分   ← 删掉这个分支
if not candidate_ids:
    candidate_ids = {m["id"] for m in memories}
```
改为：
```python
# 无真实词面命中：主路径不强行倒高强度记忆（那会每轮重复同几条）。
# 留空 → 本轮 episodic 主召回为空，由 retrieve_fallback（近期高强度）兜底续上。
if topic and not candidate_ids:
    logger.debug("[episodic.retrieve] 无词面命中，主召回返回空，交给 fallback uid=%s", user_id)
    return ([], []) if return_trace else []
```
注意：`topic` 为空（某些主动触发不带 query）时**保留**原全量行为，避免误伤无 query 场景——
所以判断写成 `if topic and not candidate_ids`。

---

## 3. 风险与护栏
- **会不会召回变少？** 是，且这是目标。原来每轮硬塞 3 条（多数离题）→ 现在只在真有词面相关时注入，
  其余交给 `retrieve_fallback`（已 100% 触发，足够维持"角色还记得点什么"的连续性）。
- **fallback 100% 触发**这条本单先不动，但改完步骤 4 后它的角色更吃重，**用 trace 复评**它给的是否合适；
  若它也总倒同一条，下一单再收紧（7天/strength≥0.6 阈值）。
- **STOP_GRAMS 是种子集**，不要追求一次到位。上线后看 trace 的 `kw` 频次榜，把新冒出来的高频虚词加进去。
- **不要动**：`MIN_SCORE`、emotion_texture 去重、N2-A `allow_strengthen=False`、relevance_bonus 公式。

## 4. 验收（trace 自验）
重启 bot（本模块不热重载）后跑若干轮，看当天 `recall_trace/*.jsonl`：
1. `kw` 命中里 `叶瑄` 及其碎片应**基本消失**；`什么/好。` 类噪声消失。
2. 之前"无命中→同 3 条"的句子（如"我以为你会帮我浇水"）：`episodic_hits` 主召回应为空或只剩 fallback，
   **不再出现 日记被看/质问/表白 那三件套**。
3. 有真实话题词的句子（如"不安""依赖""记得"）应继续命中对应记忆——别把有用的也滤没了。
4. `pytest`（episodic 相关用例）通过；没动 `tag_rules.py`，跳过 `run_eval.py`。
5. 复评 OK 后，删 `kw_legacy` 字段（A/B 使命完成）。

## 5. 数据卫生（顺手）
当前 `2026-06-20.jsonl` 里有一行 **null 字节（\x00）损坏行**（解析时报错被跳过）。
多半是写入时进程被杀/未 flush。`core/recall_trace.py` 的 append 建议用 `safe_write`/原子追加并确保 flush，
避免后续分析脚本被脏行绊倒。

## 6. 下一步
第一跳去噪验收通过、trace 里 `kw` 是干净的话题词后，再开 **施工单 03：关键词二跳**
（复用 `_load_index` 倒排索引 + 审计文档四条护栏）。届时二跳的种子是干净的，扩散才有意义。
