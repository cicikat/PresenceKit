# 工单 66：dream_echo 语义修复 —— 静音窗从 30 天收窄到"8h + 关键词命中"（方案 A）

> 与 65 / 67 / 68 **无依赖，可并行**。
> 改动前必读：`docs/dream.md`（D2 隔离墙段）、`docs/memory.md` 三点八（fixation_pipeline）。

## 问题（Desktop 已核实代码）

`core/pipeline.py:1094` 的 `_dream_echo = _has_active_imp(uid, char_id)` 是
**store 全量检查**：只要 impression store 有任何未过期条目就为 True。而
`core/dream/distill_impression.py` 的 `_DECAY_DAYS = 30`——结果是：

**做一场 sandbox 梦之后 ~30 天内，每一个现实回合都 `dream_echo=True`，
`handler_summarize_to_midterm`（`fixation_pipeline.py:1414`）直接 return。**
一场梦静音整条 mid_term → episodic → identity 固化链一个月。这远超"防止梦剧情
固化为现实事实"的原始意图，属于现实记忆链饥饿。

## 修复（方案 A，用户已拍板）

`_dream_echo` 判定改为两段：

1. **出梦后 8 小时内**（对齐 afterglow residue TTL）：全量静音照旧。
   判据用 `dream_state` 的 `last_exited_at`（该字段 `clear_local_state()` 不清，现成可读）。
2. **8 小时之后**：仅当**本轮文本确实谈到梦**才标记——user content 或 reply 命中
   梦境关键词（初版词表：`梦 / 梦里 / 梦见 / 梦到 / 做了个梦 / 昨晚的梦`，常量定义，
   留注释说明可扩）。

实现放独立纯函数（建议 `core/dream/echo_gate.py` 或 pipeline 内 `_should_dream_echo()`），
方便测试。`has_active_impressions` 保留（6g 注入判断仍用它，注入行为**不变**——
本单只改 echo 标记，不动 6g 体验）。

## 边界说明（写进注释）

泄漏面只剩"聊到梦但没命中关键词"的 case，落在 docs/dream.md 第十节 F1 已接受边界内
（6g 文案已显式框定非现实）。承重墙（store 物理隔离）不受影响，echo 本来就是纵深。

## 测试（反假绿：正反都要）

- 出梦 2h 内、无关键词 → echo=True（8h 窗生效）
- 出梦 3 天后、无关键词、store 有活跃 impression → **echo=False，mid_term 正常入队**（修复主张）
- 出梦 3 天后、user 说"我梦到你了" → echo=True（关键词生效）
- 无任何 impression、无梦史 → echo=False（基线）
- 回归：`pytest tests/test_dream_*.py -n auto` + 相关 fixation 测试

## 文档

`docs/dream.md` D2 隔离墙段与 `docs/memory.md` 对应段同步改写判定描述。
