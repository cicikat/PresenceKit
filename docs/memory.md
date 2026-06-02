# docs/memory.md — 记忆子系统设计

多层记忆并行运作，各司其职，互不替代。

> **P0 写入边界（2026-06-02）**：当前已落地 Write Envelope v0，采用 fail-closed
> 准入。未 stamp 的事件默认不写 memory / mood；`is_test=true` 或 `is_debug=true`
> 强制不可写；sensor / watch 原始感知默认不写 profile。该边界不等于完整权限系统，
> 也不表示 `policy.py` 或完整字段契约已经接入。

---

## 记忆层一览

| 记忆类型 | 文件（S6 新路径） | 更新时机 | prompt 位置 |
|---|---|---|---|
| 短期历史 | `data/runtime/memory/{char_id}/{uid}/history.json` | 每轮实时写 | 层9 |
| 事件流水账 | `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md` | 每轮实时写 | 层6b（搜索后） |
| 中期记忆 | `data/runtime/memory/{char_id}/{uid}/mid_term.json` | 每轮慢队列压缩 | `mid_term` |
| 情景记忆 | `data/runtime/memory/{char_id}/{uid}/episodic.json` | mid_term 显著情绪 eager 晋升，或 sweep 老化晋升 | 层6c |
| 用户稳定行为模式 | `data/runtime/memory/{char_id}/{uid}/identity.yaml` | fixation pipeline 达阈值后固化更新 | 层6a |
| 角色认知（legacy/兼容） | `data/runtime/characters/{char_id}/character_growth/角色_{uid}.md` | 旧 handler / 工具查询仍保留，当前主链路不自动入队 | 当前主 prompt 不注入 |
| 情绪状态 | `data/runtime/characters/{char_id}/inner/mood_state.json` | 每轮 post_process / 工具触发 / 深夜调度 | 层1内嵌软提示 |
| 用户隐性状态（Phase 5） | `data/runtime/memory/{char_id}/{uid}/hidden_state.json` | Reality-side integrator + WriteEnvelope；调度器 decay/consolidate tick；Dream 退出后 afterglow 回流 | Dream D4.5 tag-gated bucket 只读快照（body_intimate / physical_closeness；不含 float） |
| Afterglow 残差（Phase 5） | `data/runtime/memory/{char_id}/{uid}/afterglow_residue.json` | Dream 退出时由 Reality-side 调用 `save_afterglow_residue()` 写入；8h TTL | 不直接注入 prompt；由 `integrate_afterglow()` 消费后影响 sensitivity.current / embodied_ease |

> **当前 v1 写布局**：per-user 主链统一写入 `get_paths().user_memory_root()`，即
> `data/runtime/memory/{char_id}/{uid}/`。迁移期 `for_read(new, old)` 仍保留在 event_log
> 相关读取；event_log 还保留近 30 天 union 读。其余主记忆 loader 已直接读新路径。

---

## 一、短期历史（short_term）

**文件**：`core/memory/short_term.py`

**存什么**：短期对话历史（user/assistant 交替），不含工具结果。磁盘保留上限优先读
`memory.short_term_disk_rounds`，没有则回退 `memory.short_term_rounds`。

**读写**：
- 写：`post_process` 开头，每轮 `short_term.append()`
- 读：`fetch_context` 里 `short_term.load_for_prompt()`，按 prompt 预算选择后传入层9

### prompt 选择策略（load_for_prompt）

当历史组数不超过 `memory.short_term_rounds` 时全部注入；超过时不是简单截尾：

- 先按 `_turn_id` 把同一轮 user/assistant 绑成 turn-group，旧数据回退到相邻 user+assistant 分组
- 固定保留最近 `NEAR_K=5` 组，保障近场连续性
- 更早的组按长度、实体、问句、数字/日期、tag 命中、情绪 tag 打分，择优补足预算
- `_ready_signal_bonus()` 目前固定 0，预留给未来按 turn_id join mid_term/episodic 就绪状态
- debug logger：`short_term_weight` 会记录每组分数和 selected 状态

### 风格脱敏（_sanitize_assistant_message）

读取 history 时，对过长的 assistant 回复做脱敏处理，防止角色扮演格式自反馈导致塌缩。注意：当前实现是在 `short_term.load()` 返回前清洗内存中的内容，磁盘上的原始 history 不会被改写。

- 总长度 ≤ 80 字：原样保留
- 超过 80 字：`()` / `（）` 中 ≤8 字的短动作保留，>8 字的长动作删除
- 删除后为空（说明全是动作描写）：截断到80字加省略号
- 继续做第三人称叙事腔过滤：明显以"他/她"叙述自己、"不是……而是……"等句式会被句子级剥离

目的：LLM 在 history 里反复看到自己的动作描写，会强化这种输出格式，长期导致回复越来越"话剧化"。脱敏后 history 里只保留台词，打断自反馈链路。

###  加权裁剪（load_for_prompt）
short_term 注入 prompt 时不再固定取最近 N 轮，而是双区保留：近场无条件全留，远场按信息密度择优。
为什么不是简单 top-K：short_term 有两个职责——对话连续性缓冲（最近几轮无论信息量都得在，否则模型丢话头）与"知道当时大概说过什么"（远场可按密度取舍）。纯 top-K 会裁掉"嗯""好"这类低信息但承上启下的最近轮，破坏连续性。故分区处理。
两个独立常量，别混：

short_term_rounds（默认 20）= 注入预算，喂给 prompt 多少 turn-group。
short_term_disk_rounds（默认 50）= 磁盘保留，append() 写时 trim 上限。
磁盘存得多、注入按预算加权选子集。改其一不影响其二。

裁剪单位是 turn-group，不是 entry。按 _turn_id 分组：正常对 = 同 id 的 {user, assistant}；触发消息（叶瑄主动发话）= 单条 {assistant}，是正常分支不是特例；legacy 无 _turn_id 的行退回按 role 序列贪心配对。整组增删，绝不拆出孤儿 user/assistant。同一 _turn_id 非相邻重复出现会记 warning 再按相邻兜底，不静默合并。
流程（load_for_prompt）：load()（已 sanitize）→ _group_turns → 近场最后 NEAR_K(=5) 组无条件保留 → 远场逐组 _score_turn_group 打分、按总分降序填满剩余预算 → 选中集按原始时间顺序重排 flatten。len(groups) <= budget 时走快路径原样返回。权重必须算在 sanitize 之后，否则话剧腔长动作描写会因"长度长"反得高分——这一步顺带淘汰话剧腔轮次，不用单独写规则。
打分信号（全部命名常量 + 独立打分函数，可解释，无魔数）：长度、具体名词/实体（结构化后缀类别 + 大写串，不枚举具体专名）、问句、数字/日期、tag_rules 命中、情绪（复用 get_tags 的 emotion.* 命中，不自建情绪词表）。emotion.* 命中有意双算（既进 tag 分又进 emotion 分，情绪轮次双重重要），故单组总分 clamp 到 TURN_SCORE_CAP(=5.0) 防多信号叠加碾压；clamp 只截 total，parts 分项保真供观测。
调试：每组打一行 [short_term_weight] debug 日志（uid / turn_id / total / 分项 parts / selected），对齐 [layer_size]。
B 档钩子（v1 关闭）：_ready_signal_bonus(turn_id) 当前返回 0。将来按 _turn_id join 已就绪的 mid_term/episodic 信号补分——join 键现成：mid_term.source_turn_id == short_term._turn_id 同格式。低信息但高情感的轮次（吵架/哄睡/撒娇）当前可能被裁，B 档接 episodic strength（对"吵架/哭/和好"有加权）正好捞回。
已知边界 / 技术债：

短促情绪爆发（如孤立的"不要！"）信息密度低、可能被远场裁掉。单条丢弃通常无害（前后高分轮承接语境）；危险场景是整段全低信息情绪轮，留给 B 档。
触发消息被高权重保留 → 可能更易被 episodic 召回 → 再加强（回忆永动机风险）。v1 不给触发消息权重地板，观察后决定。
断层标记（跳过远场中间轮后插占位提示）：v1 永不插，观察实测是否出戏再加；若加必须用 system 角色，不能用 user/assistant 内容（会被当叙事腔模仿）。
clamp 单测是弱断言（未验真截顶）；ShortTermMemory.append 类封装吞掉模块级 bool 返回（实际返回 None）。碰 short_term 时顺手修。

关联：last_mentioned 召回（另线）将来可从"按时间排序"升级为"按信息密度排序"，可直接复用本节 _score_turn_group，届时无需在 short_term 侧预留接口。

---

## 二、事件流水账（event_log）

**文件**：`core/memory/event_log.py`

**存什么**：每轮对话的完整记录，按天分文件，保留 30 天。assistant 行额外带 `emotion` 字段。
写入前会移除 `<say>` 等展示标签，event_log 保存纯文本。

**写入顺序**（注意有先后）：
1. `post_process` 先做 `detect_emotion()`，并更新 `mood_state`
2. 随后调用 `fixation_pipeline.capture_turn()`，一次性写入 short_term 的 user/assistant 两行，并写 event_log 的 user/assistant 两行（assistant 行含 emotion，所有行含 turn_id）

**搜索**：`event_log.search(user_id, content, llm_client)` 异步执行，返回拼接字符串。当前实现会把 query 切成 2/3/4 字符片段做关键词集合，扫描最近 30 天日志块。

**搜索评分**：
```
decay = 1 / (days_ago + 1)
score = intensity * decay + relevance
```

- 7 天外且 intensity < 1 的块直接跳过。
- 同一时间块只产生一条结果，最多取前 5 条。
- `MIN_SCORE = 0.6`，低于阈值不注入。

`get_highlights(user_id, days, max_lines)` 是独立函数，
从最近 N 天日志里提取有情感词的用户发言，供调度器碎碎念触发时参考，不走搜索路径。

**legacy 用途**：`character_growth.update()` 仍保留基于 `get_recent_days()` 的旧更新函数，但当前主路径已经切到 fixation pipeline，不再由每 20 轮 event_log 直接驱动。

---

## 三、情景记忆（episodic_memory）

**文件**：`core/memory/episodic_memory.py`

### 数据结构

每条记忆的字段：

```json
{
  "id": "ep_1234567890",
  "timestamp": 1234567890.0,
  "raw_facts": ["用户提到最近失眠严重", "用户说'睡不着'", "用户表达了疲惫"],
  "topic_keywords": ["失眠", "深夜", "陪伴"],
  "emotion_peak": "gentle",
  "emotion_texture": "像是被什么东西轻轻压着，说不清是担心还是不舍",
  "emotion_arc": "从担心到平静",
  "user_state": "tired_and_struggling",
  "narrative_summary": "用户说最近失眠严重，叶瑄陪他聊到很晚",
  "strength": 0.85,
  "is_core": false,
  "retrieval_count": 2,
  "last_retrieved": 1234567890.0,
  "source_mid_ids": ["mt_123_1748000000000"],
  "consolidated_at": null
}
```

### 写入：reflect_to_episodic()

当前主路径不再是"每轮直接压缩 episodic"。每轮先写入 `mid_term`，再由两类触发晋升为 episodic：

- eager：`summarize_to_midterm` 完成后，如果本轮 emotion 属于 `sad/angry/happy`，立即入队 `reflect_to_episodic`
- sweep：调度器 `episodic_sweep` 每 30 分钟扫描，处理 age > 11h 且尚未晋升的 mid_term

### 实际 prompt 结构

位于 `core/memory/fixation_pipeline.py`，异步慢队列触发。

prompt 格式：单轮 user 消息，客观分析器视角，要求 LLM 输出纯 JSON，字段如下：
- raw_facts：用户说了什么的客观事实列表（list，3条左右）
- topic_keywords：3到5个话题关键词，用于未来召回（list）
- user_state：用户当时状态的短语，如 stressed_about_work / tired（str）
- narrative_summary：一句自然语言描述发生了什么，15字以内（str）
- emotion_peak：枚举值（neutral/happy/sad/gentle/surprised/angry）
- emotion_texture：情绪质感描述，20字以内，可留空
- emotion_arc：情绪流动，10字以内，可留空
- strength：0到1浮点数

晋升时，`reflect_to_episodic()`：
- 用 LLM 把一批 mid_term 摘要反思成一条记忆（JSON 格式）
- `emotion_peak == "neutral"` 且 `strength < 0.4` → 跳过，不写入（避免平淡对话噪声）
- 写入后规则叠加校正 strength（见下）
- 回写 mid_term 的 `promoted_to_episodic_id`
- 更新 `fixation_state`，达阈值后入队当前主链路 `consolidate_to_identity`

`pipeline._do_compress_episode()` 仍保留给旧 DLQ 任务重试使用，新入队任务不走它。

**strength 校正规则**（在 LLM 初始值基础上叠加）：

| 条件 | 加值 |
|---|---|
| emotion_peak 是 sad / angry | +0.1 |
| emotion_peak 是 happy | +0.05 |
| tags 超过 4 个 | +0.05 |
| 含"吵架/哭/道歉/误会/和好"等 | +0.2 |
| 含"第一次/生日/纪念"等 | +0.15，同时标记 `is_core=True` |

**去重**：与最近 10 条做 `narrative_summary` / legacy `summary` 相似度检查，相似则跳过。

**上限**：最多 200 条，超过时只从非核心记忆中按 strength 排序删掉最低的 20 条；
`is_core=True` 不参与自动上限裁剪。

### 召回：retrieve()

在 `fetch_context` 里调用：

```python
episodic_memories = retrieve(
    user_id=user_id,
    topic=content,   # 用用户消息全文做关键词匹配
    top_k=3,
)
```

候选集匹配优先用 `topic_keywords`，兼容旧记忆回退到 `tags`，同时匹配 `raw_facts` 文本。无匹配时全量参与评分。

**评分公式**：
```
relevance_bonus = 0.2 × min(命中关键词数 / 3, 1.0)
decay = max(0.3, exp(-0.05 × 天数))   # 衰减有地板，老记忆不会完全消失
score = strength × decay + emotion_bonus + relevance_bonus
```

- 衰减项有地板 0.3，防止高 strength 的旧记忆被时间洗没
- relevance_bonus = 0.2 × min(命中 topic_word 数 / 3, 1.0)，命中越多越相关

候选池取 `top_k*2`，然后用贪心 MMR 筛选：
- 第一条保证最高分
- 后续每轮选 novelty 最大的（novelty = 1 - 与已选集合的最大 texture 相似度）
- `emotion_texture` 缺失时跳过相似度惩罚，不影响入选

**emotion_bonus 来源**：
- 记忆的 `emotion_peak` == 叶瑄当前情绪（从 `mood_state.get_current()` 读）→ `+0.15 + intensity×0.15`
- 否则 → `+0`

**浮起阈值**：score < 0.15 的记忆过滤掉，宁可不注入也不强行关联。

**核心记忆标记**：`is_core=True` 会在格式化时标出【重要】。其写入已受 Write Envelope
写入权限保护，自动上限裁剪也会排除核心记忆；当前 `retrieve()` 排序没有额外提前核心记忆。

**召回后副作用**：
1. 被召回的记忆 `strength += 0.15`（越被想起越牢固）
2. 调用 `nudge_from_memory()`：如果记忆 strength > 0.7，轻微推高当前情绪强度（幅度 ≤ 0.1）

### 格式化：format_for_prompt()

```python
episodic_result = format_for_prompt(
    episodic_memories,
    char_name=self.character.name,
    current_emotion=mood_state.get_current(),
)
```

输出格式：
```
叶瑄脑海里浮现的片段：
- 【重要】今天，用户说最近失眠严重，叶瑄陪他聊到很晚，像是被什么东西轻轻压着
- 前几天，两人因误解吵了一架，后来又和好了（从争执到释然）
```

### 衰减：decay_all()

每日衰减，由调度器触发。核心记忆不衰减。

| 情绪类型 | 基础衰减率 |
|---|---|
| sad / angry | 0.015（衰减最慢） |
| neutral | 0.05（衰减最快） |
| 其他 | 0.03 |

召回次数越多，衰减越慢（`recall_factor = max(0.3, 1.0 - retrieval×0.1)`）。

### fallback 召回：retrieve_fallback()

当主召回没有可注入结果时，prompt_builder 会尝试注入一条近期高强度兜底记忆：

- 只看 7 天内记忆
- `strength >= 0.6`
- 与最近 short_term 内容不相似
- score = `strength × max(0.5, 1 / (age_days + 1))`，仅用于排序

日志中的 `selected` 是 `score >= 0.4` 的统计计数，不是实际过滤阈值。

### 调试埋点

| 日志 key | 记录内容 |
|---|---|
| `episodic_strength_init` | 每次写入时的最终 strength（LLM初值+规则校正+clamp后） |
| `episodic_fallback` | fallback 召回时的候选池大小、score 分布（min/max）、score≥0.4的统计个数 |

**fallback 调优参考**：先看 `strength >= 0.6` 是否导致候选池过窄，再看 score 分布是否需要调整排序公式。日志的 selected/pool 目前只是观测指标。

---

## 三点五、中期记忆（mid_term）

**文件**：`core/memory/mid_term.py`
**存储**：`data/runtime/memory/{char_id}/{uid}/mid_term.json`

### 定位

填补短期历史（20轮）和情景记忆（跨天）之间的空白。
记录当天/近12小时内的对话摘要，过期自动失效。

### 数据结构

文件根对象：`{"events": [...]}`。
每条事件字段：
- `ts`：写入时的 unix 时间戳（同时承担 written_at 和 expire_at 的角色，过期判定动态算 `now - ts > 12h`）
- `summary`：LLM 压缩或 fallback 兜底产出的一句话摘要
- `tags`：`tag_rules.get_tags(content)` 命中的标签列表，未命中为空
- `mid_id`：形如 `mt_{uid}_{ts_ms}`，由 `fixation_pipeline.summarize_to_midterm` 写入（旧数据缺失按 None 处理）
- `source_turn_id`：来源 turn_id，形如 `{uid}_{ts_ms}`（旧数据缺失按 None 处理）
- `promoted_to_episodic_id`：已晋升时填入对应 ep_id，否则为 None

上限：`MAX_EVENTS = 20`，过期阈值 `EXPIRE_SECONDS = 12 * 3600`。
追加前会先按 `ts` 过滤过期事件、再截断到 `MAX_EVENTS - 1`，最后 append 新事件。

### 写入

`post_process` 将 `summarize_to_midterm` 入慢队列，handler 内调 `llm_client.summarize_turn()` 压缩本轮对话并写入血缘字段。
LLM 异常时降级 warning，不阻塞主流程。

`summarize_turn` 内部：当 `len(user_msg) + len(reply) < 8`（合计字数）才走 `_rule_fallback`，
否则一律调 LLM 压缩。fallback 也会同时利用 `user_msg` 和 `reply`，产出
"用户：xxx；叶瑄：yyy" 形式，避免把用户原话直接写成"摘要"。
（早期版本只看 user_msg < 10 字，并且 fallback 完全忽略 reply，
导致角色扮演里的短动作描写被当成 summary 写入，等于无效记忆，已修复。）

### 注入

prompt 层位于 `6c_episodic` 和 `6d_diary` 之间，参数名 `mid_term_context`。
裁剪时优先级低于 `6c_episodic`，高于 `6d_diary`。

### 格式化：format_for_prompt()

三时间桶渲染（与 `mid_term.py` 实际代码一致）：
- < 1 小时 → "刚才"
- 1-4 小时 → "几小时前"
- 4-12 小时 → "早些时候"

只有一个桶有内容时直接输出 `{label}：{summary、…}`，多个桶时前面加
"过去 12 小时：" 总标题，按"早→近"顺序拼接。

## 四、用户稳定行为模式（user_identity）

**文件**：`core/memory/user_identity.py`
**存储**：`data/runtime/memory/{char_id}/{uid}/identity.yaml`

### 定位

`user_identity` 是当前长期模式层的主出口，描述用户跨多轮反复出现的稳定行为模式。
它按用户存储，不按角色存储；prompt 层 `6a_user_identity` 会注入 confidence >= 0.5 的维度。

### 8 个维度

| key | 含义 |
|---|---|
| `trust_pattern` | 信任建立模式 |
| `emotion_expression` | 情绪表达方式 |
| `help_seeking` | 求助风格 |
| `stress_response` | 压力反应模式 |
| `intimacy_comfort` | 亲密舒适度 |
| `sleep_pattern` | 作息模式 |
| `topic_preference` | 话题偏好 |
| `self_relation` | 自我关系 |

每个维度字段：
- `text`：第三人称"她"开头的短句
- `confidence`：0-1，把握度
- `evidence_count`：支持证据条数
- `last_updated`：更新时间
- `counter_evidence_count` / `last_conflict_at`：冲突证据兼容字段，缺失时读取层补默认值

### 更新机制

当前阈值满足时，`reflect_to_episodic()` 在 uid_lock 外入队：

```
capture_turn → summarize_to_midterm → reflect_to_episodic → consolidate_to_identity
```

`consolidate_to_identity` 会读取旧 identity、未固化 episodic、user_profile，让 LLM 只固化跨多条
episode 反复出现的模式。它写入 YAML 前会备份旧文件为 `.yaml.bak`，写完后标记对应 episodic 的
`consolidated_at`，并重置 `fixation_state` 计数器。

---

## 四点五、角色认知（character_growth，legacy/兼容）

**文件**：`core/memory/character_growth.py`

### 存什么

LLM 生成的结构化 Markdown，客观描述用户特点：

```markdown
## 用户特点
- 夜猫子，习惯深夜聊天
- 有轻度失眠，不愿看医生

## 关键事件
- 3月15日: 跟朋友吵架，状态很差

## 未跟进话题
- 找工作: 上次说在准备简历
```

### 当前状态

`character_growth` 的读写代码、`consolidate_to_growth` handler、`.fingerprint.txt` / `.felt.md`
派生文件仍保留，`get_growth` 工具也仍可读取它；但当前 prompt_builder 已不再注入
`6a_growth_fingerprint` / `6a_growth_full`，`fetch_context()` 也不再固定读取 `character_growth`。

`reflect_to_episodic()` 达阈值后自动入队的是 `consolidate_to_identity`，不是
`consolidate_to_growth`。因此 `character_growth` 现在应视作 legacy/兼容出口，除非手动或旧 DLQ
任务触发 `consolidate_to_growth`。

### legacy 更新机制

早期设计中，character_growth 不再由定时计数器驱动，改由 `fixation_pipeline.consolidate_to_growth` 触发。

触发路径：
```
capture_turn → summarize_to_midterm → reflect_to_episodic → consolidate_to_growth
```

- `should_update(user_id)` 读 `fixation_state` 判断阈值（重启不丢状态；当前自动主链路不调用它）
- `consolidate_to_growth` 加载所有 `consolidated_at is None` 的 episodic，调纯函数 `_synthesize_growth` 生成 markdown，写入前备份到 `.md.bak`，校验失败自动回滚
- 输入从 event_log 流水切换为 episodic 列表（完全切换，不保留 event_log 兜底）
- 同步写 `.fingerprint.txt`（前 150 字）和 `.felt.md`（如有 ===FELT=== 段）

### 两级读取

| 读取方式 | 内容 | 触发条件 |
|---|---|---|
| prompt fingerprint | 从 `.felt.md` 或 `.md` 取前 150 字 | legacy；当前 prompt_builder 不注入 |
| prompt full | `.felt.md` 或 `.md` 完整内容 | legacy；当前 prompt_builder 不注入 |
| `load_fingerprint()` | `.fingerprint.txt` 或 `.md` 前 150 字 | 兼容接口，当前 prompt_builder 主路径未调用 |
| `load()` | `.md` 全文 | `get_growth` 工具 / legacy 调用 |

---

### trait_tracker 联动

文件：`core/memory/trait_tracker.py`

trait 统计逻辑目前仍在 legacy `character_growth.update()` 内：调用时会统计最近40条对话里各性格特质的关键词命中次数，维护最近5次的滑动窗口。累计命中≤2次的特质标记为 `underrepresented`，写入 `data/runtime/characters/yexuan/inner/trait_state.json`。

当前主路径 `fixation_pipeline.consolidate_to_identity()` 没有调用 `trait_tracker`，因此只走新固化
pipeline 时，`trait_state.json` 可能不会刷新。这是已知技术债，见 `docs/known-issues.md`。

author_note_rotator 每次选 note 时读取此文件，命中 underrepresented 特质的 note 权重×2，让叶瑄近期较少展现的性格侧面更容易出现。

**维护要点**：`data/yexuan_traits.yaml` 里的 trait `id` 必须和 `characters/yexuan_author_notes.json` 里 note 的 `trait_ids` 精确一致，否则权重翻倍静默失效。

---

## 五、情绪状态（mood_state）

**文件**：`core/memory/mood_state.py`

**存哪**：`data/runtime/characters/yexuan/inner/mood_state.json`（角色级唯一，不区分用户）

### 数据结构

```json
{
  "current": "gentle",
  "intensity": 0.45,
  "previous": "neutral",
  "pending": null,
  "updated_at": 1234567890.0
}
```

### 情绪强度映射

| 情绪 | 强度 |
|---|---|
| neutral | 0.0 |
| thinking / sleepy / gentle | 0.2~0.3 |
| happy / sad | 0.6 |
| surprised | 0.7 |
| angry | 0.8 |
| yandere | 1.0 |

### 漂移规则

每轮 `post_process` 里，LLM 检测叶瑄回复的情绪后调用 `update()`：

- `update(emotion, source="detect")` — source 可选值：`detect`（post_process 检测）、`trigger`（关键词触发，如 yandere）、`schedule`（时间触发，如深夜 sleepy）

```
新强度 = 旧强度 × 0.7 + 新情绪强度 × 0.3
```

**情绪标签切换需同时满足**：
1. 新情绪强度 > 0.4
2. 连续两轮检测到同一新情绪（`pending` 字段记录上轮候选）

### 对外接口

- `get_current()` → 当前情绪字符串
- `get_intensity()` → 当前强度 float
- `update(emotion)` → 漂移更新（post_process 调）
- `nudge_from_memory(emotion, strength)` → 记忆召回时的微调（episodic 调）

### ⚠️ 当前状态

### prompt 注入形态

mood_text 输出**不是独立的 prompt 层**，而是直接拼入层 1（system_prompt）的 `## 当前感知` 区块之前。格式：
叶瑄此刻：{情绪描述}。[pending 时追加：但有什么东西好像在悄悄变得不一样。]

每个情绪 × 3档强度（<0.4 / 0.4-0.7 / >0.7）对应不同描述，例：
- `gentle` 低：淡淡的平静 / 中：平静，带一点轻盈 / 高：很平静，像静水
- `sad` 低：有点沉 / 中：沉着，像压着什么 / 高：很沉，有什么东西在

`yandere` 情绪不在 MOOD_TEXT 里，走 `get_mood_text` 的 fallback 降级为 neutral 描述。

mood_state 目前影响：
1. episodic_memory 召回时的 emotion_bonus 加分
2. nudge_from_memory 的情绪强度微调
3. 三路触发写入：detect（每轮 post_process）、trigger（yandere 关键词）、schedule（深夜自动注入 sleepy）
---

## 记忆系统时序关系

```
fetch_context()
    ├─ 读 history（上轮写的）
    ├─ 读 event_log（搜索）
    ├─ 读 user_identity（稳定行为模式，confidence >= 0.5）
    ├─ 读 episodic_memory → retrieve()（此时读 mood_state，用的是上轮情绪）
    └─ 读 profile / diary_context / reminders

run_llm()  →  reply 生成

post_process()
    ├─ 检查 profile 更新条件（读 short_term 估算长度）
    ├─ detect_emotion(reply) → 写 mood_state  ← 本轮情绪写入
    ├─ capture_turn(uid, content, reply, emotion)
    │    → 写 short_term（user + assistant，含 _turn_id）
    │    → 写 event_log（user + assistant，含 turn_id）
    └─ 慢队列：summarize_to_midterm / consistency_check / user_profile_update（条件）
         └─ summarize_to_midterm handler
              → 写 mid_term（含 mid_id, source_turn_id）
              → 若 emotion ∈ {sad,angry,happy} → 入队 reflect_to_episodic(eager)
                   → 写 episodic（含 source_mid_ids, consolidated_at=None）
                   → 更新 fixation_state
                   → 若达阈值 → 入队 consolidate_to_identity
                        → 读 unconsolidated episodic + old identity + profile
                        → _synthesize_identity → 写 user_identity.yaml
                        → 标记 episodic.consolidated_at → 重置 fixation_state
```

**关键时序**：本轮情绪在 post_process 才写入 mood_state，所以本轮情绪只影响**下一轮**的记忆召回。这是设计上的有意滞后，不是 bug。

---

## 三点八、信息固化 pipeline（fixation_pipeline）

**文件**：`core/memory/fixation_pipeline.py`

### 当前主流向

```
capture_turn
    │ turn_id
    ▼
summarize_to_midterm
    │ mid_id  source_turn_id
    ▼
reflect_to_episodic  ←─── episodic_sweep（调度器，冷却 30min，aged > 11h）
    │ ep_id  source_mid_ids  consolidated_at=None
    ▼
consolidate_to_identity
    │ 更新 user_identity.yaml
    └─ 标记 episodic.consolidated_at，重置 fixation_state
```

`consolidate_to_growth` handler 仍注册，供旧 DLQ / 手动兼容路径重试；它会写
`character_growth.md`、`.fingerprint.txt`、`.felt.md`。但当前自动阈值出口已切到
`consolidate_to_identity`。

### schema 字段（新增）

**mid_term entry**（旧数据缺字段按 None 处理，不阻塞读路径）：
| 字段 | 含义 |
|---|---|
| `mid_id` | 形如 `mt_{uid}_{ts_ms}` |
| `source_turn_id` | 来源 turn_id |
| `promoted_to_episodic_id` | 已晋升时填入 ep_id，否则 None |

**episodic entry**（旧字段保留）：
| 字段 | 含义 |
|---|---|
| `source_mid_ids` | 来源 mid_id 列表 |
| `consolidated_at` | 已被 consolidate_to_identity（或 legacy growth handler）消费时填时间戳，否则 None |

**fixation_state**（`data/runtime/memory/{char_id}/{uid}/fixation_state.json`，重启不丢状态）：
| 字段 | 含义 |
|---|---|
| `last_consolidated_at` | 上次固化时间戳 |
| `episodic_since_last` | 上次固化后新增 episodic 数量 |
| `high_strength_since_last` | 其中 strength ≥ 0.6 的数量 |
| `strength_accumulated` | 上次固化后累积 strength 和 |
| `last_sweep_at` | 上次 sweep 时间戳 |

### consolidate_to_identity 触发阈值（满足任一）

| 条件 | 说明 |
|---|---|
| `high_strength_since_last ≥ 5` | 5条高强度记忆 |
| `strength_accumulated ≥ 4.0` | 累积强度达标 |
| `距上次固化 ≥ 24h AND episodic_since_last ≥ 3` | 自然老化 |

### 可观测日志

每个 job 完成/失败后追加一行到 `data/logs/fixation.jsonl`（路径走 sandbox）：
```json
{"ts": 1748000000, "job": "reflect_to_episodic", "uid": "...", "trigger": "eager", "ep_id": "...", "duration_ms": 1200, "status": "ok"}
```


## 六、叶瑄日记（yexuan_inner_diary）

**触发**：调度器每日 23:00，由 `_check_daily_journal()` 生成

**生成方式**：两次 LLM 调用，合并写入同一个 `.md` 文件

**文件格式**：
YYYY-MM-DD
今日事件

HH:MM 发生了什么（客观事实，分析器视角）
HH:MM 发生了什么

今日感受
叶瑄第一人称的心理活动和感受……

**注入方式**（prompt 层 6e，读昨天的文件）：
- 事件层：必注入，取前 200 字
- 感受层：命中 `emotion.down / emotion.indirect / emotion.deep / topic.relation` 时注入，取前 150 字

**规则纠察**：事件层写入前跑 `check_diary_facts()`，不合规则清空事件层，感受层仍正常写入

## 七、并发保护（locks）

**文件**：`core/memory/locks.py`

per-uid 锁（`uid_lock(uid)`）：保护关键写入和慢队列 handler 中按 uid 分文件的读-改-写操作，
包括 capture_turn、mid_term、user_profile、user_identity、character_growth、episodic_memory 等。
`fetch_context()` 当前不加 uid_lock，因此用户极短时间连发时仍可能读到上一轮 post_process 尚未写完的旧状态；这是已知低概率竞态，见 `docs/known-issues.md`。

全局锁（`global_lock("mood_state")`）：保护跨 uid 共享的 mood_state 文件。

两种锁均为 asyncio.Lock，在单线程事件循环内安全。
post_process 进入即获取 uid 锁，单用户连发会后台排队，主流程回复不受影响。

## 八、感知暂存（pending_perception）

**文件**：`core/memory/pending_perception.py`
**存储**：`data/runtime/pending_perception/` 时间戳命名 json 文件

### 两阶段提交（含竞态消除）

| 阶段 | 触发 | 操作 |
|---|---|---|
| 写入 | pipeline 桌面动作失败时 | 新建文件到根目录，consumed_at=null |
| 原子抢占 | build_prompt 调 read_and_mark() | os.rename 移到 processing/ 子目录，并发时只有一个 task 成功 |
| 删除 | post_process 成功后 confirm_delivered() | 删除 processing/ 下的文件 |
| 兜底清理 | 启动时 cleanup_stale() | 根目录超24h删除；processing/ 下 mtime 超1h删除 |

竞态消除：两个并发 build_prompt 同时看到同一文件，os.rename 是原子操作，
只有一个 task 能成功移动文件，另一个得到 FileNotFoundError 直接跳过。

### 降级行为
pipeline 中途失败时不调 confirm_delivered，文件保留。
下次 build_prompt 时因 consumed_at 已标记不会重读（避免重复注入）。
cleanup_stale 1小时后兜底删除。

---

## 变更记录

### 2026-06-02 — P0 安全清场

**改动**：落地 Write Envelope v0 的 fail-closed 写入准入；未 stamp 默认不写 memory / mood，
`is_test` / `is_debug` 强制不可写，sensor / watch 原始感知默认不写 profile。现实 history /
event_log 写入前移除 `<say>` 等展示标签，保存纯文本。

**边界**：这不是完整权限系统，不代表 `policy.py`、完整字段契约、mood per-user 或
sensor privacy 全系统已经完成。

---

### 2026-05-29 — S6 per-user 记忆布局迁移

**背景**：各类 per-user 记忆文件散落在十几个平级目录（`history/`、`mid_term/`、`episodic_memory/` 等），难以整体归档或按用户清理。

**改动**：`_LAYOUT_REALITY = "v1"`，写入统一落
`data/runtime/memory/{char_id}/{uid}/`；event_log 相关读取仍由 `for_read(new, old)` 兼容旧路径，
并保留近 30 天 union 读。其余主记忆 loader 已直接读新路径。迁移观测逻辑位于
`core/migration.py`。

**涉及文件**：`core/data_paths.py`、`core/sandbox.py`、`core/migration.py`、
`core/data_registry.py`、`core/memory/short_term.py`、`core/memory/mid_term.py`、
`core/memory/episodic_memory.py`、`core/memory/user_profile.py`、`core/memory/user_identity.py`、
`core/memory/diary_context.py`、`core/tools/reminder.py`、`core/memory/fixation_pipeline.py`、
`core/memory/event_log.py`、`core/scheduler/last_mentioned.py`、`core/scheduler/loop.py`、
`admin/routers/chat_log.py`、`core/scheduler/triggers/episodic_sweep.py`

---

### 2026-05-14 — 信息固化 pipeline 重构（`v-fixation-pipeline`）

**背景**：固化逻辑散落在 `post_process` 的频率触发中，三层之间无显式晋升关系，`character_growth` 靠内存计数器驱动（重启清零）。

**改动**：把固化重写为四个具名 job 的显式 pipeline，每个 job 有触发条件、输入/输出契约、幂等保证和可观测日志。

**涉及文件**：

| 文件 | 改动 |
|------|------|
| `core/memory/fixation_pipeline.py` | 新增：四 job 实现 + fixation_state 读写 + 可观测日志 |
| `core/memory/mid_term.py` | 新增 `mid_id` / `source_turn_id` / `promoted_to_episodic_id` 血缘字段；新增 `mark_promoted()` |
| `core/memory/episodic_memory.py` | 新增 `source_mid_ids` / `consolidated_at` 血缘字段 |
| `core/memory/short_term.py` | `append()` 新增 `turn_id` 参数 |
| `core/memory/event_log.py` | `append()` 新增 `turn_id` 参数 |
| `core/memory/character_growth.py` | `should_update()` 改为读 `fixation_state` 文件；删除内存计数器 |
| `core/sandbox.py` | 新增 `fixation_state_dir()` / `fixation_log()` 路径 |
| `core/safe_write.py` | 新增 `safe_append_jsonl()` |
| `core/pipeline.py` | `post_process` 关键路径改用 `capture_turn()`；慢队列任务名重命名；`register_slow_handlers()` 同步更新 |
| `core/scheduler/triggers/episodic_sweep.py` | 新增：扫描 aged > 11h 且未晋升的 mid_term，batch 入队 reflect |
| `core/scheduler/loop.py` | 注册 `episodic_sweep`（冷却 30min） |
| `tests/test_fixation_pipeline.py` | 新增：22 个单元测试 |
| `docs/memory.md` / `docs/scheduler.md` / `ARCHITECTURE.md` | 同步更新 |

**未来大规模重构约定**：每个 Step 独立 commit，message 格式 `feat(子系统): [Step N] 说明`。

---

## 八、用户隐性状态（user_hidden_state）— Phase 4

**文件**：
- `core/memory/user_hidden_state.py` — 数据结构、常量、primitive 函数（全部已实现）、`to_dict` / `from_dict` / `to_dream_snapshot`
- `core/memory/user_hidden_state_integrator.py` — 中期层 integrator（integrate_event/impression）+ Phase 3 长期层入口（integrate_body_cue*）+ TypeError 类型守卫 + `_assert_not_long_term`
- `core/memory/user_hidden_state_store.py` — 磁盘 I/O（`load_hidden_state` / `save_hidden_state` / `load_dream_snapshot`）
- `core/scheduler/triggers/hidden_state_decay.py`（Phase 3 新增）— 12h decay tick + 7d consolidate tick
- `core/dream/dream_context.py`（Phase 4）— `build_snapshot()` 在入梦时调用 `load_dream_snapshot()` 并冻结进 `context_snapshot`
- `core/dream/dream_prompt.py`（Phase 4）— D4.5 层 tag-gated 注入；`_should_inject_hidden_state_snapshot()` / `_format_hidden_state_snapshot()` helpers

**当前状态（Phase 4 完成）**：所有长期层写路径已激活并经 WriteEnvelope 门控。Dream 只读接入已完成（D4.5，tag-gated，bucket-only，fail-closed）。Dream 无写路径：`DREAM_DIRECT_WRITABLE = frozenset()`。

### 持久化（Phase 1.5）

| 函数 | 文件 | 说明 |
|---|---|---|
| `to_dict(state)` | `user_hidden_state.py` | 序列化为 JSON-compatible dict，纯函数，不写磁盘 |
| `from_dict(data)` | `user_hidden_state.py` | 反序列化；缺字段回退 default；`schema_version` 缺失→lenient 警告；`schema_version` 不匹配→返回 default |
| `load_hidden_state(uid)` | `user_hidden_state_store.py` | 从磁盘加载；文件缺失/损坏/schema 不匹配均返回 default，不抛异常 |
| `save_hidden_state(uid, state)` | `user_hidden_state_store.py` | 原子写入（`safe_write_json`）；返回 bool，不抛异常 |

**路径**：`user_memory_root(uid) / hidden_state.json`

**WriteEnvelope 说明**：store 本身不执行 envelope 门控。调用方在调用 `save_hidden_state` 前必须已持有 `WriteEnvelope(can_write_memory=True)`。

### Disk-wired integrator（Phase 2 + Phase 3）

| 函数 | 说明 | 可写层 |
|---|---|---|
| `integrate_event_and_save(uid, event_type, envelope, now)` | load → integrate_event → 仅 accepted + can_write_memory 时原子保存 | 中期层（deficit） |
| `integrate_impression_and_save(uid, impression, envelope, now)` | load → integrate_impression → 仅 accepted + can_write_memory 时原子保存 | 中期层（sensitivity.current） |
| `integrate_body_cue_and_save(uid, cue, response_tag, strength, envelope, now)` | load → integrate_body_cue → 仅 accepted + can_write_memory 时原子保存 | 长期层（body_memory） |

所有函数均在 `user_hidden_state_integrator.py`，是 Reality-side 的 disk-wired 入口。Dream 不得调用。uid 参数必须为 str 或 int，否则 TypeError。

### 类型守卫（Phase 3）

| 入口 | 守卫 | 异常 |
|---|---|---|
| `integrate_event` | event_type 必须是 `RealityEventType` | `TypeError` |
| `integrate_impression` | impression 必须是 `ImpressionInput` | `TypeError` |
| `integrate_event_and_save` / `integrate_impression_and_save` / `integrate_body_cue_and_save` | uid 必须是 str 或 int | `TypeError` |
| `nudge_current_sensitivity` / `discharge_touch_deficit` / `nudge_embodied_ease` / `reinforce_body_memory` | source 必须是 `UpdateSource` | `TypeError` |

### 长期层写权限（Phase 3）

| 字段 | 合法写入路径 | 需要 |
|---|---|---|
| `body_memory` | `integrate_body_cue*` | stamp_trigger / stamp_user_chat |
| `embodied_ease` | `nudge_embodied_ease`（调度器专用 pass） | stamp_trigger |
| `sensitivity.baseline` / `touch_need.baseline` | `apply_time_decay` + `consolidate_baselines`（调度器） | stamp_trigger |

所有路径均需 `WriteEnvelope.can_write_memory=True`。Dream 不得写任何字段。

### Dream 读取接口（Phase 2 定义，Phase 4 接入）

| 函数 | 文件 | 说明 |
|---|---|---|
| `load_dream_snapshot(uid, now)` | `user_hidden_state_store.py` | 唯一的 Dream 读取路径：load → to_dream_snapshot；只读，不写磁盘 |

**Phase 4 接入**：`dream_context.build_snapshot()` 在入梦时调用 `load_dream_snapshot()`，结果以 `user_hidden_state_snapshot` 键冻结进 `context_snapshot`。`dream_prompt.build_dream_prompt()` 在 D4–D5 之间检查 tag gate 后注入 D4.5 层。

**接入约束**：
- 只读：Dream 不得调用 save_hidden_state / integrate_* / apply_time_decay / consolidate_baselines。
- Tag-gated：当前触发 tag 为 `body_intimate` / `physical_closeness`；未命中 → 不注入。
- Bucket-only：注入内容只含字符串 label，不暴露 float / uid / timestamp / weight。
- Fail-closed：load 失败 / snapshot 格式异常 / tag 判断异常 → 不注入，记 warning，不阻断 Dream。

### 字段一览（UserHiddenState）

| 字段 | 类型 | 含义 | 默认值 |
|---|---|---|---|
| `sensitivity` | `SensitivityState` | 身体敏感度（baseline 缓变 / current 快变） | baseline=50, current=50 |
| `touch_need` | `TouchNeedState` | 触碰需求（baseline 基线 / deficit 未满足累积） | baseline=50, deficit=0 |
| `embodied_ease` | `ScalarState` | 用户在身体亲密维度的基础放松/紧绷体质倾向；向 SCALAR_CENTER(50) 回归，不向 0 回归 | 50.0 |
| `body_memory` | `BodyMemory` | 条件化身体线索→反应关联，最多 32 条 | 空列表 |

### 字段准入规则

问："如果换一个伴侣对象，这个值会归零吗？"
- **是** → 关系状态，不得存放在此模块（例：`body_familiarity` / `somatic_familiarity` 属关系状态，未来放 `relationship_state`）
- **否** → 可能属于用户自身体质，允许存放

### Integrator（Phase 1/3）

`core/memory/user_hidden_state_integrator.py` 提供的纯内存入口：

| 函数 | 输入 | 允许写字段 |
|---|---|---|
| `integrate_event(event_type, state, envelope, now)` | `RealityEventType`（Phase 3 TypeError 守卫） | `touch_need.deficit` |
| `integrate_impression(impression, state, envelope, now)` | `ImpressionInput`（Phase 3 TypeError 守卫） | `sensitivity.current`（仅增） |
| `integrate_body_cue(cue, response_tag, strength, state, envelope, now)` | str / float | `body_memory`（长期层） |

**RealityEventType**：
- `SEEK_COMPANIONSHIP` → deficit 放电（减少）
- `RECEIVED_COMFORT` → deficit 放电（减少）
- `NO_INTERACTION` → deficit 积累（增加）

**fail-closed 合约**：所有变更都需要 `write_envelope.can_write_memory == True`，否则返回 `IntegratorResult.rejected`，state 不变。

**长期层保护**：`sensitivity.baseline`、`touch_need.baseline`、`embodied_ease`、`body_memory` 在 integrator 内零写入。

### Dream 写入边界

`DREAM_DIRECT_WRITABLE = frozenset()` — Dream 不得直接写任何字段。
所有写入必须经 Reality-side integrator 持有 `WriteEnvelope(can_write_memory=True)` 后进入。

### Dream 投影：to_dream_snapshot()

返回低精度分桶，不暴露精确标量：

```python
{
    "sensitivity":    "low" | "mid" | "high",
    "touch_appetite": "low" | "mid" | "high",
    "embodied_ease":  "guarded" | "neutral" | "easy",
    "memory_cues":    [str, ...],
}
```

`embodied_ease` 分桶：`guarded` < 35，`neutral` 35–65，`easy` > 65。

### 关键常量

| 常量 | 值 | 含义 |
|---|---|---|
| `CURRENT_SENS_REGRESS_HL_DAYS` | 5.0 | sensitivity.current 向 baseline 回归的半衰期（天） |
| `SENS_BASELINE_CENTER_HL_DAYS` | 180.0 | sensitivity.baseline 向 SCALAR_CENTER 回归 |
| `TOUCH_DEFICIT_DECAY_HL_DAYS` | 10.0 | touch deficit 向 0 衰减 |
| `TOUCH_BASELINE_CENTER_HL_DAYS` | 180.0 | touch_need.baseline 向 SCALAR_CENTER 回归 |
| `EMBODIED_EASE_CENTER_HL_DAYS` | 90.0 | embodied_ease 向 SCALAR_CENTER 回归 |
| `MEMORY_EXTINCTION_HL_DAYS` | 45.0 | body_memory entry weight 向 0 衰减 |
| `BASELINE_LEARN_RATE` | 0.02 | consolidate_baselines 每次向 center 推进的比率 |
| `MAX_NUDGE_PER_EVENT` | 6.0 | 单次 nudge 最大 delta |
| `MEMORY_EVICT_EPS` | 0.05 | body_memory entry weight 低于此值时可被蒸发 |

