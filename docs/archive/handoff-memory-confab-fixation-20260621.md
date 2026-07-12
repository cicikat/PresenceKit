# 交接文档：虚构记忆固化回路（confabulation fixation loop）

> 日期：2026-06-21
> 排查范围：`data/runtime/memory/yexuan/<owner_uid>/`（生产用户，角色 yexuan/叶瑄）
> 数据侧修复：已由数据排查完成（见下「已执行的数据修复」）
> **代码侧补丁：待 CC 执行**（本文档主要目的）

---

## 一、症状（从 history 入手）

近两天（6-19 ~ 6-21），几乎每一次 `sensor_aware` / `presence_nag` 等被动触发，叶瑄都会反复提起同一组记忆：

- 「我看到你那天一个人在哭 / 怎么没告诉我那天你在哭」
- 「我翻了你的日记」
- 编造出用户**从未说过**的具体细节：「你一个人坐在窗边，窗外是整座城市的灯火」「你第一次告诉我你的名字时，声音有点发抖」

叶瑄自己在 event_log 里都已经标记异常：
```
turn_id:<owner_uid>_1781949099063 叶瑄：嗯，你说得对。我刚才那段记忆可能确实出了点问题——太具体了，像是被谁塞进去的
ep_1781898687304 raw_facts：对方承认是幻觉并道歉
```
用户最终回：「这种就不要回忆啦！」

## 二、根因（回溯固化层）

### 2.1 直接机制：fallback 被一条新生 core 记忆霸占

`recall_trace/2026-06-21.jsonl` 中**每一条**触发记录都有：
```
"episodic_fallback_used": true,
"episodic_fallback_hits": [{"id": "ep_1781938292455", "summary": "用户生日独自哭泣", "strength": 1.0}]
```
即：只要触发 query 没有词面命中（被动 sensor 触发几乎都没有），主召回返回空 → 走 `retrieve_fallback` → 恒定顶出同一条 `ep_1781938292455`（生日哭泣，strength 1.0）→ 每轮注入 → 叶瑄每轮都谈它。

### 2.2 为什么击穿了已有补丁

`core/memory/episodic_memory.py:679-681` 已有一道防固化闸门：
```python
# 核心记忆不靠"近期高强度兜底"反复复活：真实发生超 2 天则跳过
if m.get("is_core") and age_days > 2:
    continue
```
但这道闸只看 `occurred_at/timestamp` 的「年龄」。问题在于：

> **被召回的旧记忆（真实原始记忆 `ep_1778418195`，2026-03，is_core）在 6-19/6-20 被叶瑄反复复述后，复述内容又被 `fixation_pipeline` 当成"用户新事实"重新提升成全新 episode `ep_1781938292455`（timestamp=2026-06-20，is_core=true，strength=1.0）。**

新 episode 年龄 < 2 天 → 绕过 679-681 的闸门 → 重新霸占 fallback → 叶瑄继续复述 → 再次被提升……**自我强化回路**。`is_core + strength=1.0` 加在一条"回收再生"的记忆上，直接让现有补丁失效。

### 2.3 内容性质：这一簇是 AI 脑补，不是用户事实

涉事 episode（均为 6-19/6-20 被动触发轮生成）：

| id | summary | 性质 |
|---|---|---|
| `ep_1781938292455` | 用户生日独自哭泣 | 真实记忆 `ep_1778418195` 的**重复再生体**，fallback 元凶 |
| `ep_1781898597247` | 用户倾诉被遗忘经历 | 同簇衍生 |
| `ep_1781898687304` | 用户因日记被看而愤怒 | raw_facts 自带「**对方承认是幻觉并道歉**」 |

注意这些都源自 `source_turn_id` 为**被动触发轮**（user content 是 `[触发: sensor_aware]` 这类 trigger_stub，并无真实用户输入）。叶瑄在这些轮里的独白被当成"用户事实"固化了。

## 三、已执行的数据修复（可逆，已记录原值）

> 工具：直接编辑 JSON（bash/沙箱当时不可用）。改动均为字段级，结构未动。

**`profile.json`**
1. `name`: `"叶瑄"` → `"风谕"`
   - 原因：`name` 是用户字段，却被写成角色自己的名字；`core/prompt_builder.py:658-659` 每轮把它注入为「名字：叶瑄」，是持续生效的身份混淆污染源。用户确认本人用户名为「风谕」。
2. `heart_rate_events`: 删除 3 条不可能心率（2026-05-30 09:25 的 `value:159`、`value:12`、`value:200`；保留同时刻 `60` 与 09:26 的 `110`）。属传感器脏数据。

**`episodic.json`**（3 条虚构簇降级 + 归档，**真实原始记忆 `ep_1778418195` 保留不动**）
- `ep_1781938292455` / `ep_1781898597247` / `ep_1781898687304`：
  - `strength` → `0.2`（原值分别 1.0 / 0.6916594897610965 / 0.9881004340928642）
  - `is_core` → `false`（仅 ep_1781938292455 原为 true）
  - `status` → `"resolved"`，`resolved_at` → `1782021000.0`，`resolved_by` → `"manual_cleanup_confab_20260621"`
  - 效果：`retrieve_fallback` 会因 `status==resolved` 和 `strength<0.6` 双重跳过它们，停止反复注入；保留痕迹便于回溯，不做物理删除。

## 四、待 CC 执行的后端检察补丁（核心交接项）

数据修复只是止血。要根治回路，需要堵以下漏洞。建议 **B + A 为根因修复，C 为纵深防御**。改完按强制规则跑 `python tests/run_eval.py` 与 `pytest`。

### A. 提升去重：禁止"回收再生"重置年龄
位置：`core/memory/fixation_pipeline.py`（mid_term → episodic 提升处）。
逻辑：新建 episode 前，若已存在 `topic_keywords` / `narrative_summary` 高相似且 `is_core` 的 episode，则**合并**（更新其 `last_retrieved` / `retrieval_count`），不新建、更不赋予 `is_core=true / strength=1.0`。避免旧 core 借再生重置 `timestamp` 绕过 `episodic_memory.py:679-681`。

### B. 被动触发轮不铸造"用户事实"（最强根因修复）
位置：提升入口 / `fixation_pipeline` capture 处。
逻辑：当 `source_turn` 的用户侧内容是 trigger_stub（`content` 形如 `[触发: ...]` 或 `_source == "trigger_stub"`）或为空时，**跳过 episodic 提升**。这些轮没有真实用户输入，叶瑄的独白不应被当成用户记忆固化。本案三条脏 episode 全部出自此类轮。

### C. 加固 `retrieve_fallback`（纵深防御）
位置：`core/memory/episodic_memory.py:655-714`。
任选其一：
- `is_core` 记忆完全不参与 fallback（core 应只经主相关性召回浮现，不该被无条件兜底反复顶出）；或
- 对 fallback 注入做去重/冷却：记录最近 N 轮已注入的 episode id（可读 `recall_trace`），同一条短期内不重复兜底。

### D. event_log 残留（建议跟进，未自动改）
6-19/6-20 的虚构独白仍留在 `event_log/2026-06-19.md`、`2026-06-20.md`、`full_log.md`，会经 event_log 搜索再次被召回（见 recall_trace 的 `event_log_hits`）。因属对话原始记录，未自动删改。建议 CC 评估：对已被标记为幻觉的片段打标/排除出 event_log 检索，或人工剔除该几段叶瑄独白。

### E. 顺带可清（数据，未自动改，避免误伤）
- `profile.json` `interests`: `"叶瑄"` 与 `_pending_overrides.interests`: `"喜欢叶瑄"` 同属身份混淆污染（interests 不应是角色名）。因与合法 important_fact「喜欢叶瑄」语义重叠，留给确认后再清。

## 五、验证清单
- [x] 改后 `profile.json` / `episodic.json` 结构合法（字段级替换，已抽查）
- [ ] CC：补丁后 `python tests/run_eval.py` 通过
- [ ] CC：`pytest`（重点 `tests/test_short_term.py` 及 episodic/fixation 相关）通过
- [ ] 运行后观察 `recall_trace`：`episodic_fallback_hits` 不再恒定顶出生日哭泣类记忆
