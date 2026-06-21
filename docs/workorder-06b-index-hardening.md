# 施工单 06b（热修）— 倒排索引损坏导致二跳静默失效

> 给 CC 的执行单。**先做这单,否则二跳永远是空转。**
> 目标文件:`core/memory/episodic_memory.py`(`_load_index`)、`core/safe_write.py`(fsync)。

---

## 0. 现状(实测)
- `config.yaml` `two_hop_enabled: true`、05 hop-2 代码已落地、trace 里 16/32 轮有 hop-1 种子,
  但 **hop=2 命中 = 0**。
- 根因:`memory_index.json` 损坏(null 填充,解析报 `Extra data`),`_load_index` 解析失败**静默返回 `{}`**
  → 二跳拿到空索引 → 找不到任何共享关键词记忆 → 一条都不补。`.bak` 索引是好的(398 键)。
- 这是和 episodic.json 同款的 null 填充崩溃产物;03b 的 fail-loud 只加到了 `_load_memories`,
  **漏了 `_load_index`**。

## 1. 改法

### 步骤 1:`_load_index` 损坏即重建(索引是派生数据,可再生)
`core/memory/episodic_memory.py` 的 `_load_index`:
```python
def _load_index(user_id, *, char_id="yexuan"):
    p = _index_read_file(user_id, char_id=char_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        memories = _load_memories(user_id, char_id=char_id)
        _rebuild_index(user_id, memories, char_id=char_id)
        return _load_index_raw(user_id, char_id=char_id)
    except Exception as e:
        # 不再静默吞掉:索引是 episodic 的派生数据,损坏就从记忆重建,别返回空让二跳空转
        logger.error("[episodic] 索引损坏,从记忆重建 uid=%s path=%s err=%s", user_id, p, e)
        memories = _load_memories(user_id, char_id=char_id)
        _rebuild_index(user_id, memories, char_id=char_id)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
```
> `_load_index_raw` 只是直接读文件那一行;若不想加辅助函数,把重建后再 `read_text+loads` 内联即可。
> 注意:`_load_memories` 现在会对损坏抛 `EpisodicCorruptError`(03b),这里要 try 住——
> 记忆也坏时索引无从重建,返回 `{}` 并让上层降级(二跳本就 flag 可关,空索引=退回纯 hop-1)。

### 步骤 2:写入加 fsync(根治 null 填充)
null 填充来自“rename 落了盘、数据块没落盘”的非干净退出。`core/safe_write.py` 的
`safe_write_text` 在 `tmp.replace(path)` **之前**加 fsync:
```python
import os
with open(tmp, "r+b") as _fd:
    _fd.flush()
    os.fsync(_fd.fileno())
# 然后再 keep_bak / replace
```
(可选)替换后对父目录 fd 再 fsync 一次,确保目录项持久化。这一步让原子写真正“原子+持久”。

### 步骤 3:修当前这个坏索引(一次性,真机)
不用手修——步骤 1 上线后,下次 `_load_index` 命中损坏会自动从记忆重建。
想立刻修:删掉损坏的 `memory_index.json`(保留 `.bak`),下次召回自动重建。

## 2. 验收
1. `two_hop_enabled: true` 重启后跑几轮:trace 里出现 `hop=2` 条目(种子有具体关键词的轮次)。
2. 构造损坏索引文件 → `_load_index` 记一条 error 日志并返回非空(重建成功),不再静默 `{}`。
3. `memory_index.json` 写入后 `xxd` 看尾部无 `\x00` 填充。
4. `pytest` 通过。

完成后,二跳的 A/B 才真正可测(见 05 验收)。顺带把 `recall_trace` 的 jsonl 追加也补上 flush
(之前那行 `\x00` 脏行同源)。
