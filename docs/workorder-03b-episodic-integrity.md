# 施工单 03b（热修）— episodic.json 截断抢救 + 写入硬化 + 加载报警

> 给 CC 的执行单。**优先级最高，先做这单再做 04**——当前 `episodic.json` 已截断，
> 在它修好前做任何召回调优都是在腐烂数据上跑。
> 目标文件：`core/memory/episodic_memory.py`、`core/safe_write.py`。

---

## 0. 现状（实测）
`data/runtime/memory/yexuan/1043484516/episodic.json` 末尾停在
`"status": "open",\r\n    "resolved_at":`——无值、无闭合括号、无数组结尾。
原始字节确认（164603 bytes 全读），不是读取截断，是文件本身截断。
- 194 条里 **193 条可正常解析**，只有最后一条（写到一半的）损坏。
- `_load_memories` 解析失败时**静默返回 `[]`**：若线上 bot 读到这个文件，叶瑄会**丢光全部情景记忆且无任何提示**。
- `safe_write_json` 本身是原子的（tmp→replace），正常保存不该截断 → 多半是重启打断写入 /
  有非原子写入方 / mount 视图不一致。无论成因，**加载与写入都要变得 fail-loud + 可回滚**。

---

## 1. 步骤 1：抢救当前文件（在真机上跑一次性脚本）
> 真机路径下的 `episodic.json` 是准绳。先备份再抢救。
```python
import json, shutil, re
from pathlib import Path

p = Path(r"D:\ai\qq-st-bot\data\runtime\memory\yexuan\1043484516\episodic.json")
shutil.copy(p, p.with_suffix(".json.corrupt.bak"))   # 先留证据

raw = p.read_text(encoding="utf-8", errors="replace")
# 裁到最后一个完整记录（顶层数组里每条记录以 "\n  }," 或 "\n  }" 结束）
idx = raw.rfind("\n  },")
salvaged = json.loads(raw[: idx + 4] + "\n]") if idx != -1 else []
print("salvaged:", len(salvaged))

# 用原子写回（与运行时同口径）
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(salvaged, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(p)
print("restored ok")
```
验收：`python -c "import json;print(len(json.load(open(r'...episodic.json',encoding='utf-8'))))"` 应打印 193。

## 2. 步骤 2：写入硬化（`core/safe_write.py`）
给 JSON 写入加「写后校验 + 旧档备份」，避免再产生不可解析的主文件。
新增一个带校验的 JSON 写函数（或给 `safe_write_json` 加 `verify_json=True` 参数）：
```python
def safe_write_json(path, data, *, keep_bak: bool = True) -> bool:
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        # 写后校验：tmp 必须能被解析回来，否则放弃替换
        json.loads(tmp.read_text(encoding="utf-8"))
        if keep_bak and path.exists():
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except Exception:
                pass
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"[safe_write] JSON 写入/校验失败，已保留原文件 {path}: {e}")
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        return False
```
要点：**校验通过才 replace**；replace 前把旧主文件挪成 `.bak`（崩了能手动回滚）。
> 注意 `safe_write_json` 是全仓通用函数，改签名要确认其它调用方（grep `safe_write_json(`）兼容
> ——只加可选关键字参数 `keep_bak`，默认行为安全，不破坏现有调用。

## 3. 步骤 3：加载 fail-loud + 防灾难覆写（`core/memory/episodic_memory.py`）
```python
def _load_memories(user_id, *, char_id="yexuan"):
    require_character_id(char_id)
    p = _mem_read_file(user_id, char_id=char_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        # 不再静默吞掉：损坏要喊出来，否则会被空列表覆写、记忆清零
        logger.error("[episodic] 加载失败（疑似损坏），拒绝按空处理 uid=%s path=%s err=%s",
                     user_id, p, e)
        raise EpisodicCorruptError(str(p)) from e
```
并在 `_save_memories` 加灾难覆写护栏：
```python
def _save_memories(user_id, memories, *, char_id="yexuan"):
    p = _mem_write_file(user_id, char_id=char_id)
    # 护栏：原文件本来很大却要写入空/极小，极可能是上游 load 失败导致，拒写。
    try:
        if (not memories) and p.exists() and p.stat().st_size > 1024:
            logger.error("[episodic] 拒绝用空列表覆写非空记忆文件 uid=%s", user_id)
            return
    except Exception:
        pass
    safe_write_json(p, memories)
```
> `EpisodicCorruptError` 定义一个轻量异常类即可。调用 `_load_memories` 的读路径
> （`retrieve` / `retrieve_fallback` / `fetch_context`）需 try 住它并降级为「本轮无 episodic」，
> **绝不**触发写回。写路径（consolidation）遇到它应中止本轮、保留原文件。

## 4. 验收
1. 抢救后 `episodic.json` 解析 = 193 条。
2. 单测：构造一个损坏 json，`_load_memories` 抛 `EpisodicCorruptError` 而非返回 `[]`；
   `_save_memories([], ...)` 对已有非空文件**不写**。
3. `safe_write_json` 写入一个含非法内容的对象（mock json.loads 失败）时，主文件**保持原样**、留下 `.bak`。
4. `pytest` 通过。
5. 顺带：`core/recall_trace.py` 的 jsonl 追加也确认带 flush/原子，杜绝之前看到的 `\x00` 脏行。

完成后再做 `docs/workorder-04-keyword-primary.md`。
