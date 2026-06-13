# Spec #3 — 梦境记忆随时间模糊（Dream Clarity Decay）

> 状态：待实现  
> 难度：小-中  
> 改动范围：`core/dream/dream_afterglow.py`、`core/prompt_builder.py`、`core/memory/user_hidden_state.py`（可选 turns 追踪）

---

## 目标行为

刚退梦时角色对梦境记忆清晰，可以具体引用情节和意象；随着时间（或对话轮数）推移逐渐模糊，最终只剩情绪余温。

| 阶段 | 时间窗口 | 注入内容 |
|---|---|---|
| **清醒** | 0 – 2h | 情绪摘要 + 情绪色调 + 残留意象（全量注入） |
| **模糊** | 2 – 5h | 情绪色调 + 部分摘要（去掉 symbolic_fragments）|
| **余温** | 5 – 8h | 仅情绪色调软提示（当前行为，不变） |
| **消散** | > 8h | 不注入（当前行为，不变） |

---

## 实现步骤

### Step 1：修改 `core/dream/dream_afterglow.py`

**目标**：`_format_afterglow` 按 `age_hours` 分阶段返回不同深度的内容。

在 `_find_best_summary()` 返回时已经有 `created_at`，但没有传 `age_hours` 下去。需要把 `age_hours` 算出来传给 `_format_afterglow`。

修改 `load_afterglow()` → `_find_best_summary()` → `_format_afterglow()` 调用链：

```python
def load_afterglow(uid: str, *, char_id: str = "yexuan") -> str:
    best, age_hours = _find_best_summary(uid, char_id=char_id)
    if best is None:
        return ""
    return _format_afterglow(best, age_hours=age_hours)
```

修改 `_find_best_summary` 返回值为 `tuple[dict | None, float]`：

```python
def _find_best_summary(uid: str, *, char_id: str = "yexuan") -> tuple[dict | None, float]:
    ...
    # 在选中 best 时一并算出 age_hours
    if best is None:
        return None, 0.0
    age_hours = (now - best_ts) / 3600.0
    return best, age_hours
```

修改 `_format_afterglow` 增加 `age_hours` 参数，按阶段控制内容深度：

```python
_PHASE_CLEAR_HOURS  = 2.0   # 0~2h：清醒，全量
_PHASE_FADE_HOURS   = 5.0   # 2~5h：模糊，去掉意象
# 5~8h：余温，只注一行（由 _format_afterglow_soft_hint 负责，本层降级）
# >8h：_AFTERGLOW_TTL_SECONDS 硬截止

def _format_afterglow(summary: dict[str, Any], *, age_hours: float = 0.0) -> str:
    afterglow_type = summary.get("afterglow", "gentle_residue")
    frame = _HURT_FRAME if afterglow_type == "hurt_reluctance" else _GENTLE_FRAME
    ...
    parts: list[str] = [frame]

    if s := summary.get("summary"):
        parts.append(f"情绪摘要：{_sv(s)}")

    if tags := summary.get("emotional_tags"):
        if isinstance(tags, list) and tags:
            parts.append("情绪色调：" + "、".join(_sv(str(t)) for t in tags[:4]))

    # 清醒阶段才注入具体意象；模糊及之后跳过
    if age_hours < _PHASE_CLEAR_HOURS:
        if frags := summary.get("symbolic_fragments"):
            if isinstance(frags, list) and frags:
                parts.append("残留意象：" + "、".join(_sv(str(f)) for f in frags[:3]))

    # 模糊阶段：主摘要也降级，只保留前 30 字
    if age_hours >= _PHASE_CLEAR_HOURS and s:
        # 替换已加入 parts 的完整摘要为截断版
        for i, p in enumerate(parts):
            if p.startswith("情绪摘要："):
                parts[i] = f"情绪摘要（模糊）：{_sv(s)[:30]}……"
                break

    # 余温阶段（>5h）：本层只保留 frame + 一行情绪色调，其余不注
    if age_hours >= _PHASE_FADE_HOURS:
        tone_line = next((p for p in parts if p.startswith("情绪色调：")), "")
        parts = [frame, tone_line] if tone_line else [frame]

    parts.append(_PROHIBIT_DREAM_RP)
    return "\n".join(p for p in parts if p)
```

### Step 2（可选）：基于对话轮数的衰减

如果想叠加"对话轮数越多越模糊"的效果，需要在 afterglow_residue.json 里追踪退梦后的对话次数。

在 `prompt_builder.py` 的 `_format_afterglow_soft_hint()` 每次被调用（即每轮现实对话）时，给残留记录加一。

具体：

1. 在 `user_hidden_state_store.py` 的 `save_afterglow_residue()` 中，往 JSON 里写 `"reality_turns_since_exit": 0`。
2. 在 `_format_afterglow_soft_hint()` 每次成功读取 residue 时，调用一个新函数 `increment_afterglow_turns(uid, char_id=char_id)` 原子 +1。
3. `load_afterglow()` 也读这个计数，叠加到 age_hours 的计算里（比如每 5 轮等效 1h）。

**如果觉得 Step 2 复杂度不值得，可以先不做，纯 time-based 已经够好。**

### Step 3：调整 `_format_afterglow_soft_hint()` 在 `prompt_builder.py`

当前 `_format_afterglow_soft_hint()` 读的是 `afterglow_residue.json`（only tone/tags）。在余温阶段（5-8h），这个软提示层接管。

现有行为已经正确，不需要修改。但要确认 `dream_afterglow.py` 的 load_afterglow 和 `_format_afterglow_soft_hint` 不会在同一轮 both 注入内容（会重叠）。

检查点：两者在 prompt_builder 里对应不同 layer（`6f` vs `dream_afterglow_soft_hint`），但在余温阶段 `dream_afterglow.py` 的 `load_afterglow` 仍会返回非空（只返回 frame + tone_line）。

**建议**：在余温阶段（age_hours >= 5）让 `load_afterglow()` 直接返回空字符串，让 `_format_afterglow_soft_hint` 独自接管，避免重叠。

```python
def load_afterglow(uid: str, *, char_id: str = "yexuan") -> str:
    best, age_hours = _find_best_summary(uid, char_id=char_id)
    if best is None:
        return ""
    # 余温阶段交给 _format_afterglow_soft_hint（prompt_builder layer）接管
    if age_hours >= _PHASE_FADE_HOURS:
        return ""
    return _format_afterglow(best, age_hours=age_hours)
```

---

## 验证方式

手动测试（无需跑 pytest，直接调函数）：

```python
from core.dream.dream_afterglow import _format_afterglow

fake_summary = {
    "uid": "xxx",
    "afterglow": "gentle_residue",
    "summary": "我们在某个海边的场景里，你在找什么，我一直跟着你",
    "emotional_tags": ["warm", "longing"],
    "symbolic_fragments": ["海边", "你在找的东西", "黄昏"],
}

print("=== 清醒阶段 (0.5h) ===")
print(_format_afterglow(fake_summary, age_hours=0.5))
print("=== 模糊阶段 (3h) ===")
print(_format_afterglow(fake_summary, age_hours=3.0))
print("=== 余温阶段 (6h) ===")
print(_format_afterglow(fake_summary, age_hours=6.0))
```

---

## 注意事项

- `_find_best_summary` 返回值签名改变，确认没有其他调用方（全文搜 `_find_best_summary`，目前只有 `load_afterglow` 调用，safe）。
- `_PHASE_CLEAR_HOURS` 和 `_PHASE_FADE_HOURS` 可以提到文件顶部作为配置常量，方便调整。
- 不要修改 `_AFTERGLOW_TTL_SECONDS`（8h 是好的硬截止）。
