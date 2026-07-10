# Brief 53 · Stage 沉默是合法回合

> 依赖：**50**（沉默轮命中率要看 trace）、**52**（用它调整后的分值做阈值判断）。
> 与 52 同碰 `arbiter.py`/`runner.py`，排在 52 后串行。
> 参考：RESPOND（arXiv 2603.21682）——when-to-speak 与 who-speaks 同等重要，
> 不回应是合法动作；你 1v1 的 `recall_gate.is_low_information()` 是同一哲学。
> 现状问题：`min_responders` 强制每轮必有人回。owner 在群里自言自语一句"困了"，
> 三个角色排队接话，尬聊填充比沉默更出戏。

## 1. 改法（core/stage/runner.py Phase A 入口）

满足**全部**条件时本轮零响应（Phase A 产出空集，Phase B 不启动）：

1. 无人被 `vocative` 点名（52 的枚举）；
2. 仲裁最高分 < `SILENCE_THRESHOLD`（常量，初值 0.35）；
3. owner 消息命中 `recall_gate.is_low_information()`（backchannel 硬名单，复用现有函数）
   **或** 群设置 `allow_silent_rounds=true` 且条件 1、2 已满足。

沉默轮仍然：追加 owner 发言进 transcript、发 `group_round_start`/`group_round_end` WS 帧
（前端不悬空）、trace 记 `"silent_round": true` 与当轮全体分数。

## 2. 群设置

`Settings` 新增 `allow_silent_rounds: bool = true`；`PATCH /group/{id}/settings` 自动支持
（现有部分更新机制）。设为 false 时恢复现行为（min_responders 强制兜底）。

## 3. 拍板

- 被 vocative 点名**永不沉默**（条件 1 是硬闸）——点了名不理人是 bug 不是自然。
- 沉默轮的 owner 发言照常进 transcript 与后续投影（她说过的话角色们"听见了"，
  只是没接话；下轮完全可以被提起）。
- 阈值 0.35 拍死初值，命名常量，依 trace 调。

## 4. 测试

1. "困了"（backchannel、无点名、低分）→ 零响应，transcript 有 owner 条目，WS 两帧齐发，trace 记 silent_round。
2. "叶瑄，困了吗" → vocative 硬闸，正常响应（min_responders 生效）。
3. `allow_silent_rounds=false` → 与现行为逐字节一致（回归保护）。
4. 沉默轮后的下一轮：投影包含上一轮 owner 发言（没被吞）。
5. `pytest -n auto tests/test_stage*` + 新增测试。

## 5. 不做什么

- 不做"延迟响应"（想了一会儿才说话的 think-delay）——那是 stage.md 已标注的
  独立 backlog，不混进本单。
- 不做主动群触发（proactive 群发言按规格保持关闭）。
