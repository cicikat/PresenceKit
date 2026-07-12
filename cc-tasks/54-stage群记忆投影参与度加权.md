# Brief 54 · Stage 群记忆投影：参与度加权 + 说话人归属

> 依赖：**52**（用其呼格/mention 枚举算被点名数；52 未合入时退化用现有 `_addressed` bool，
> 不阻塞本单）。碰 `core/stage/projection.py`，与 50–53 无文件冲突。
> 现状问题：整个 roster 无差别 `memory_strength=0.7` 投影。被点名吵了一架的角色和全程
> 没说话的角色记忆强度一样；投影摘要若丢失说话人归属，会重演 1v1 曾经的"谁说的"混淆
> （P0-1/P1-1 为此做过 event_log speaker 字段化）。

## 1. 参与度加权（core/stage/projection.py）

对投影切片（projection_cursor 之后的 transcript 段）逐角色统计：

- `spoke`：该角色发言条数
- `addressed`：被呼格点名次数（52 枚举；退化时用 `_addressed` 命中数）

```
memory_strength = clamp(0.4 + 0.15 * spoke + 0.1 * addressed, 0.4, 0.9)
```

- 沉默且未被点名 → 0.4（旁听者也记一点，但弱）。
- 常量命名放模块顶部；`summarize_to_midterm` payload 的 `memory_strength` 字段现成，只改传值。

## 2. 说话人归属

1. 投影给 `summarize_to_midterm` 的输入文本逐行带说话人前缀（`{说话人名}：{内容}`），
   owner 用 `user_name` 插值（Hard Rule 8，不写死名字）。
2. 群投影的压缩 prompt 明确要求产出第三人称、带名字归属的摘要
   （"X 说了…，Y 对此…"），禁止合并不同说话人的话为无主语句。
3. 摘要写入 mid_term 时 `source="group:{group_id}"` 不变。

## 3. 拍板

- 加权只作用于群投影，1v1 路径 `memory_strength=1.0` 默认值不动。
- 不按情绪加权（群轮次没有 per-character detect_emotion，加了就得多付 LLM 往返，不值）。
- `projection_cursor` 幂等契约保持：裁剪回退逻辑不碰。

## 4. 测试

1. 三角色群：A 发言 2 次被点名 1 次 → 0.8；B 发言 1 次 → 0.55；C 沉默 → 0.4（clamp 边界另测 0.9 封顶）。
2. 投影输入逐行有说话人前缀；mock LLM 校验 prompt 含归属要求。
3. cursor 幂等：重复投影不重复入队（现有测试回归）。
4. 52 未合入的退化路径：`_addressed` bool 统计仍工作。
5. `pytest -n auto tests/test_stage*` + 新增测试。

## 5. 不做什么

- 不动 fixation 后续晋升逻辑（mid_term → episodic 阈值自然消化强度差异）。
- 不做 per-character 差异化摘要内容（同一份摘要、不同强度；差异化内容是 55 的关系层负责）。
