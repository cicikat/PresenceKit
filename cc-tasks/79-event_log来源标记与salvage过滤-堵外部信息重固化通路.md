# Brief 79 · event_log 来源标记 + salvage 过滤：堵外部信息重固化通路

> 来源：00d 备忘复核时发现的现存洞，独立于 storyline 决策、今天就该修。
> 前置于任何「直接消费 event_log 的聚合器」（storyline / 未来的 reflection 类改造）。

## 问题

来源隔离设计只覆盖了固化链的一半：

- **固化链（已隔离）**：`handler_summarize_to_midterm` 见 `dream_echo` / `web_echo` /
  `coplay_echo` payload 标记即跳过 mid_term → episodic → identity 写入
  （`fixation_pipeline.py:1414-1415`），外部信息（web 搜索结果、梦境印象回流、coplay 演出）
  不会被固化为角色记忆。✅
- **event_log（未隔离）**：`capture_turn` 在 `post_process_critical` 里无条件写
  short_term + event_log，此时不带任何 echo 标记；`event_log.append` 的 meta 行
  （`> emotion:... turn_id:...`）**没有来源字段**。❌
- **重固化通路（现存）**：`event_log_salvage`（每日扫 age 27-29 天日志、LLM 提取持久事实 →
  `important_facts` 冲突裁决入口）**无任何 echo 过滤**——被固化链刻意排除的 web/梦境内容，
  27 天后有一条绕过整个隔离设计、直达 `user_profile.important_facts` 的通路。

即：「web 与梦境来源同等隔离，不固化」（AGENTS.md 速查表）这条契约，目前只在逐轮链上成立，
在日志→抢救链上是破的。

## 1. 🟡 event_log 条目加来源标记

- `event_log.append()` 加可选参数 `source: str = ""`（受控值：`web` / `dream_echo` /
  `coplay`；空 = 普通轮）。非空时 meta 行追加 ` source:{source}`（assistant 与 user 行同步，
  与现有 `turn_id:` / `trigger:` 字段并列，格式向后兼容——老日志无此字段即普通轮）。
- `capture_turn()` 加同名参数透传。
- **标记从哪来**：
  - `web_echo` / `coplay_echo`：已经是 `post_process()` / `post_process_critical` 调用链的
    入参，直接透传即可。
  - `dream_echo`：现在的判定在 `post_process_slow`（`_load_imp_for_echo`，本地文件读 + tag
    匹配，毫秒级、无 LLM），晚于 critical 的 capture_turn。两个可选实现（落地者择一）：
    (a) 把 echo 判定抽成纯函数提前到 critical 段调用（注意别动 `consume_forced_impression_round`
    的消费时机）；(b) build_prompt 注入 D4.5 层时已知本轮带梦境印象，把标记随
    prompt 结果向下传。**禁止**第三种：salvage 侧事后用 LLM 猜哪段是梦境内容。

## 2. 🟡 event_log_salvage 过滤

- `_split_blocks` 解析出的块，meta 含 `source:` 非空值 → **整块跳过**，不进 LLM 抢救输入。
- 过滤发生在拼 LLM prompt 之前（省 token 也防漏）。
- 统计埋点：跳过块数记入现有 `_log_fixation` 式日志（或 salvage 自己的 logger），可观测。

## 3. 🟢 消费侧约定（文档，不写码）

- `docs/memory.md` 来源隔离一节补一句契约：**任何直接读 event_log 做聚合/固化的新代码，
  必须过滤 `source:` 非空块**（storyline、未来 reflection 类均适用），并引用本单。
- `event_log.search()` / `get_recent_days()`（注入侧召回）**不过滤**——注入侧本来就允许
  引用外部信息（web_recall 层自己就在注入），隔离的是「固化为长期记忆」，不是「短期可见」。
  这条边界写清楚，防止落地者顺手把召回也滤了。

## 验收

- 带 `web_echo=True` 的轮次 → event_log 当日文件对应块 meta 含 `source:web`（dream/coplay 同理）。
- 老格式日志（无 source 字段）→ salvage 行为不变（回归）。
- 构造含 `source:web` 块的 27 天前日志 → salvage 的 LLM 输入不含该块内容；无 source 块正常抢救。
- `python tests/run_eval.py` 不受影响（本单不动 tag_rules，跑一遍确认零波及即可）。
- 文档：`docs/memory.md` 来源隔离节 + AGENTS.md 速查表「web 与梦境来源同等隔离」行补 event_log
  标记与 salvage 过滤的指针。
- 独立 commit：1（append/capture_turn 标记）→ 2（salvage 过滤）→ 3（文档），1 前置于 2。

## 与 storyline（00d）的关系

storyline 聚合器读 event_log 时复用同一 `source:` 标记做过滤，不另发明机制。本单不依赖
storyline 决策，先行落地。
