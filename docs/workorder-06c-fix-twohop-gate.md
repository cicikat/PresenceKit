# 施工单 06c（修正）— 二跳关键词门用错了集合,导致 hop-2 恒为空

> 给 CC 的小修单。这是 05 设计里的我的 bug,不是你的改动问题。
> 06b(索引重建)已正确落地、索引在线上是好的;二跳还是 0 命中,根因在下面这一行。
> 目标文件:`core/memory/episodic_memory.py`(hop-2 块,约 433–436 行)。

---

## 0. Bug
hop-2 用 `if kw not in specific: continue` 过滤种子关键词,但:
- `specific` = **query 派生的 n-gram**(用户这句话的 2–4 字碎片);
- `kw` = 种子记忆的**完整 topic_keyword**(如 `依赖`/`梦境不安`)。

完整关键词几乎不可能是 query 碎片集合的成员 → 永远 `continue` → 二跳一条都收不到。
(索引 `index.get(kw)` 本身是对的——index 就是按完整 topic_keyword 建键的。)

## 1. 修法:改用"关键词自身的稀有度"作门(用索引的 df)
把这段:
```python
for kw in (seed.get("topic_keywords") or []):
    if kw not in specific:
        continue
    for mid in index.get(kw, []):
```
改成:
```python
_N = max(1, len(memories))
_KW_SHARE_CAP = max(2, int(0.10 * _N))   # 关键词出现在 ≤10% 记忆里才用于扩散
for kw in (seed.get("topic_keywords") or []):
    linked = index.get(kw, [])
    # 只从“有共享但不泛滥”的关键词扩散:
    #   len<=1 → 没有别的记忆共享它,无可扩;  len>cap → 太通用,会主题漂移
    if not (1 < len(linked) <= _KW_SHARE_CAP):
        continue
    for mid in linked:
```
其余(排除已选/resolved/strength<0.5、按 `seed_rel*HOP2_DECAY*strength` 累加、过 MIN_SCORE、
emotion_texture 去重、最多 2 条)都不动。

> 直觉:种子是"依赖",它的关键词若有别的记忆也挂着(共享),就把那些记忆作为联想补进来;
> 但若某关键词挂了一大半记忆(太泛),不扩,免得又把通用情绪记忆铺开。

## 2. 验收
1. `two_hop_enabled: true` 重启后跑几轮:trace 出现 `hop=2` 条目;
   看种子(hop1 selected)与补进来的 hop-2 是否**同簇**(如 依赖→依赖并担忧/被遗忘),不是漂移。
2. A/B:flag 关 vs 开,对比 hop-2 补的是有用联想还是噪声;漂移多就调小 `_KW_SHARE_CAP`(如 0.05*N)。
3. `pytest` 通过。

修完二跳的真 A/B 才跑得起来(此前一直是空转)。
