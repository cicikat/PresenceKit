# docs/prompt-layers.md — Prompt 层结构

---

## 层总览（实际执行顺序）

> 注意：层编号和实际插入顺序不完全一致（3.5~3.8 在 4 之后插入）。以下是代码实际执行顺序。

| 层标识 | 内容 | 触发条件 | 数据来源 |
|---|---|---|---|
| `0_jailbreak` | 破限预设 layer=0 | 文件存在且 enabled | `characters/reality/jailbreak_entries.json` |
| `1_system_prompt` | 角色存在性定义 + 情绪软提示 + `{perception_block}` 槽位 | always | `characters/yexuan.json` + `core/mood_text.py` |
| `2_char_desc` | 角色描述 + 性格 + 情境 | always | 角色卡 |
| `2_jailbreak` | 破限预设 layer=2 | 文件存在且 enabled | `characters/reality/jailbreak_entries.json` |
| `2.5_time` | 当前时间（年月日 时:分 星期X） | always | 实时生成 |
| `2.55_last_seen` | 用户上次说话时间 | 距上次说话 ≥ 6 小时 | `core/presence.py` → `get_last_seen_text()` |
| `2.6_activity` | 他此刻的状态 | 对话开头（history 为空）或沉默超10分钟 | `activity_manager.get_prompt_fragment()`，每15-45分钟随机切换，部分活动会从 episodic_memory 按 strength 加权抽一条记忆作为"他在想什么"注入 |
| `3_relation` | 与该用户的关系 + 称呼 | always | `user_relation` |
| `4_group_context` | 群聊最近动态 | 群聊时 | `group_context.get_recent()` |
| `3.5_period` | 生理期感知（第N天） | tagged（见下） | `user_profile.get_period_info()` |
| `3.6_watch` | 最近一次睡眠数据 | tagged（见下） | `user_profile` sleep_segments |
| `3.7_sensor` | 手机传感器（步数/电量/位置/亮屏次数） | 当天有数据即注（无 tag 门控） | `user_profile.phone_sensor_today` |
| `3.8_activity` | 桌宠屏幕活动快照 | tagged（见下） | `data/runtime/characters/{char_id}/inner/activity_snapshot.json`（TTL 5分钟） |
| `5_profile` | 用户画像（名字/位置/宠物/兴趣/职业） | 有内容即注 | `user_profile.load()` |
| `5.2_reminders` | 待办备忘录列表 | 有待办即注 | `get_reminders()` |
| `5.5_lore` | 世界书条目 | LoreEngine 命中时 | `lore_engine.match()` |
| `6a_user_identity` | 用户稳定行为模式 | `user_identity_text` 非空 | `core/memory/user_identity.py`，confidence >= 0.5 的维度 |
| `6b_event_search` | 相关往事（event_log 搜索结果） | 搜索结果非空 | `event_log.search()` |
| `6c_episodic` | 情景记忆片段 | episodic_result 非空 | `episodic_memory.retrieve()` + `format_for_prompt()` |
| `6c_episodic_fallback` | 近期高强度记忆兜底 | episodic_result 为空且 fallback 非空 | `episodic_memory.retrieve_fallback()`；实际消息 `_layer` 仍写 `6c_episodic`，便于统一裁剪 |
| `mid_term` | 过去 12 小时对话压缩视图 | mid_term_context 非空 | `mid_term.format_for_prompt()`（12h 过期，最多 20 条，三时间桶渲染） |
| `6d_diary_context` | 用户近期日记 | 有内容且命中 `emotion.down` / `emotion.indirect` | `diary_context.load()` |
| `6e_inner_diary_facts` | 他昨天的记录（事件层，取前200字） | 昨日日记文件存在且含事件层 | `data/runtime/characters/{char_id}/inner/diary/` |
| `6e_inner_diary_feeling` | 他昨天的心情（感受层，取前150字） | 昨日日记存在且命中 `emotion.down/indirect/deep` 或 `topic.relation` | `data/runtime/characters/{char_id}/inner/diary/` |
| `6f_dream_afterglow` | 梦境余韵详细层（只读，非现实事实）：0–2h 完整摘要/色调/意象；2–5h 模糊摘要/色调 | 5h 内存在有效 dream summary | `core/dream/dream_afterglow.load_afterglow()`；5h 后返回空并交接给软提示层 |
| `dream_afterglow_soft_hint` | 梦境余韵软提示（只读，非事实，TTL 8h，`may/可能` 限定语气，`neutral+空tags` 不注入） | 详细 afterglow 层为空，且 afterglow_residue.json 存在、TTL 未过期、tone≠neutral 或 tags 非空 | `core/prompt_builder._format_afterglow_soft_hint()` → `core/memory/user_hidden_state.read_afterglow_residue()` → `data/runtime/memory/{char_id}/{uid}/afterglow_residue.json`（S6 路径，详见 docs/memory.md §记忆层一览） |
| `6g_dream_impression` | 梦境印象回流（ambient，≤3条，非事实框定，他自述"我好像在梦里……"） | 有未过期印象时注入 | `core/dream/impression_loader.load_impression_text()` → `data/runtime/dreams/{char_id}/impressions/{uid}.json` |
| `7_mes_example_item` | 对话示例（few-shot） | always（有内容） | 角色卡 mes_example |
| `9_history` | 短期对话历史（近场保留 + 远场加权择优） | always | `short_term.load_for_prompt()` |
| `9.5_episodic_top` | 最相关情景记忆1条（attention sweet spot） | episodic_result 非空 | 从已召回结果取第一条，不重复召回 |
| `10_tool_result` | 本轮工具执行结果 | 有工具调用时 | `tool_dispatcher.execute()` 裸输出经 `core/tools/tool_result.py` 截断+定界框定后注入（`safe_summary`） |
| `11_author_note` | 人设核心提醒 + 输出格式规则 + 纠偏 | always | 硬编码 + `author_note_rotator` + consistency_check |
| `11_jailbreak` | 破限预设 layer=11 | 文件存在且 enabled | `characters/reality/jailbreak_entries.json` |
| `12_user_message` | 用户当前消息 | always | 用户输入 |

> 层 10 注入安全：工具裸输出经 `ToolResult.safe_summary`（截断上限 2000 字符）包裹后，以定界标记 `<<<TOOL_DATA_START>>>` / `<<<TOOL_DATA_END>>>` 加反注入指令框定，防止外部工具/搜索结果中的不可信文本被模型当作指令执行。原始数据仅落 debug 日志，永不进 prompt/memory。
>
> `6f_dream_afterglow` 与 `dream_afterglow_soft_hint` 为互斥层：前者在退梦后 0–5h 注入逐渐模糊的摘要，后者在详细层为空后接管至 8h TTL。两层均只读、非现实事实、读取异常 fail-closed，不写 memory / mood / profile / hidden state。

---

## Tag 门控详细说明

### `get_tags()` 的工作方式

文件：`core/tag_rules.py`

对用户消息做简单字符串包含检查（不是正则）：

```python
if any(p in text for p in rule.patterns):
    tags.add(rule.tag)
```

### 完整 Tag 规则

| Tag | 触发词 | 解锁的层 |
|---|---|---|
| `topic.energy` | 累、困、没精神、熬夜、睡不着、睡眠、疲 | 3.6 watch |
| `topic.health` | 身体、头疼、发烧、不舒服、生病、医院 | 3.6 watch |
| `topic.activity` | 运动、跑步、健身、走路、步数 | 3.6 watch + 3.8 activity |
| `query.body_state` | 今天状态、最近怎么样、身体怎么 | 3.5 period + 3.6 watch |
| `query.what_doing` | 你看到我在干嘛、你知道我在做什么、我在干嘛、我在做什么 | 3.8 activity |
| `topic.body` | 肚子、痛、生理期、例假、姨妈 | 3.5 period |
| `emotion.physical_discomfort` | 难受、不舒服、很疼 | 3.5 period |
| `topic.relation` | 我们、你还记得、之前、那次、上次 | 6e感受层 |
| `topic.history` | 那时候、以前、当时、记得吗 | 当前不直接解锁额外层 |
| `emotion.deep` | 其实、说真的、一直、从来、没人 | 6e感受层 |
| `meta.identity` | 你是谁、你是什么、你了解我吗 | 当前不直接解锁额外层 |
| `emotion.down` | 难过、想哭、想吐、恶心、痛苦、呃呃、呕呕、想似 | 3.5 period + 3.6 watch + 6d日记 + 6e感受层 |
| `emotion.positive` | 好耶、噢噢噢、喵喵喵 | 3.8 activity |
| `emotion.indirect` | 咪、好累、不想动、没胃口、吃不下、今天又没 | 3.5 period + 3.6 watch + 6d日记 + 6e感受层 |

`topic.history` / `meta.identity` 目前仍会被 `tag_rules` 记录到 debug，但 `prompt_builder`
不再用它们切换 `character_growth` 全文/指纹层。当前长期人格模式入口是 always-if-present 的
`6a_user_identity`。

### Tag 覆盖率已知盲区

- **孤独/失落**：无专属 tag，`emotion.deep` 靠"没人"兜底，覆盖窄
- **高兴/庆祝**：已有 `emotion.positive`，但触发词很窄，生日/成功话题仍可能不触发
- **间接表达**：如"最近不太好"不命中任何 tag

---

## perception_block 槽位

层1 的 system_prompt 末尾有一个占位符 `{perception_block}`，由 pipeline.build_prompt 填入。
**只承载 pending_perception**（上轮失败的桌面动作感知）和跨通道接续提示，不含工具结果——
工具结果走层10的 `tool_result` 参数，唯一出口。

情绪软提示也在层1内完成：`prompt_builder` 会读取 `mood_state.json`，调用
`get_mood_text()`，并把"他此刻：..."插到 `## 当前感知（实时，非记忆）`
之前。它没有独立 `_layer`，所以 debug layers 里不会出现 `2.7_mood_state`。

```python
_perception = ""
_pending, _pending_paths = pending_perception.read_and_mark()  # 两阶段提交
if _pending:
    _perception = _pending.strip()
# 跨通道切换时追加 "刚才在QQ那边/桌宠这边" 提示
# post_process 成功后调用 confirm_delivered(_pending_paths) 删除文件
```

感知内容格式（由 pipeline 拼接时间前缀）：
[刚刚] 桌面动作 minimize_window 执行失败（重试2次）

```

当前实现中，`tool_result` 不进入 `perception_block`，只作为层10注入。

---

## 层11 Author's Note 内容构成

Author's Note 放在历史之后、用户消息之前，对模型影响最大，由以下部分拼接：

1. `author_note_rotator.get_current_note()`（轮转的人设提醒）
2. 硬编码的反问频率规则
3. 硬编码的情感稳定性规则
4. 硬编码的动作格式规则
5. 输出风格指令（`chat` 或 `roleplay`，由 config.yaml `chat.style` 决定）
6. 条件工具规则（R5）：
   - 有 `tool_result`：`【工具结果已提供】`，提示层10已注入，禁止再声称调用
   - 无 `tool_result`：`【无工具结果】`，禁止声称调用工具，禁止编造日记/实时数据
7. 表达规则（禁止复用对话示例原句）
8. `style_hint`（从 observations.jsonl 读取，深夜/压力状态提示词）
9. `author_note_extra`（consistency_check 发现问题时的纠偏，用完即清）
10. 破限预设 layer=11

注意：正式主 LLM 调用没有接入任何 tools schema；`get_time` 等 info/desktop 工具
由 pre-pipeline 探针触发，结果以 `tool_result` 参数进入层10。memory 类工具
（`read_diary` / `search_diary` 等）需用户明确触发，主 LLM 不能自行调用。
R5 修复了旧版 Author's Note 里"必须调用 read_diary"的工具幻觉风险。

---

## token 裁剪

### 估算方式

```python
token_estimate = sum(len(m["content"]) for m in messages)
# 字符数，不是真实 token 数
# 汉字 1字符 ≈ 0.5~0.7 token
```

### 阈值

| 字符数 | 行为 |
|---|---|
| > 15000 | warning 日志 |
| > 20000 | 强制裁剪，目标降到 ≤18000 |

### _drop_priority 裁剪元数据（R4-B）

每条可裁剪消息在 `messages.append()` 时附带内部字段 `_drop_priority: int`：

- **数字越小越先丢**（lower = dropped first）。
- `None` / 未声明 = 不可裁，裁剪器不得自动删除。
- `sanitize_messages()` 会在 LLM API 出口剥离此字段，不会泄漏给供应商。
- `_DROPPABLE` 中心列表已在 R4-B 退役（`_DROPPABLE` no longer exists in production code）。

### 当前裁剪顺序

| `_drop_priority` | 层 | 说明 |
|---|---|---|
| 10 | `6f_dream_afterglow` | 梦境余韵详细层，只读非事实，与软提示互斥 |
| 10 | `dream_afterglow_soft_hint` | 梦境余韵软提示，只读非事实，最先丢 |
| 20 | `6g_dream_impression` | 梦境印象回流，ambient 非事实框定，次先丢 |
| 30 | `6b_event_search` | 关键词 + 评分搜索结果，质量较低 |
| 40 | `mid_term` | 过去 12 小时压缩视图 |
| 50 | `6d_diary_context` | 用户近期日记，tag 门控注入 |
| 60 | `6e_inner_diary` | 角色昨天日记（事件层 + 感受层，同 priority 整批丢） |
| 70 | `6c_episodic` | LLM 压缩 + MMR 筛选的情景记忆，高质量，靠后丢 |
| 80 | `5.5_lore` | 世界书设定，最后丢 |

不在裁剪表里（无 `_drop_priority`）：`6a_user_identity`、`5_profile`、`5.1_user_facts`、`9_history`、`11_author_note` 等核心层。

### 裁剪算法

1. 收集所有 `_drop_priority is not None` 的消息，按 priority 升序排列，同 priority 按原始顺序（稳定排序）。
2. 按 priority 分组，整批原子性丢弃，丢完后检查预算是否达标。
3. 预算已满（≤18000）即停止，更高 priority 的层保留。
4. 无 `_drop_priority` 的层永不被裁剪器触碰。
5. 裁剪结果写入 `debug_info["removed_layers"]`，与实际删除严格一致。

### 新增可裁剪层规范

新层如果可裁，必须在 `messages.append()` 时声明 `_drop_priority`：

```python
messages.append({
    "role": "system",
    "content": some_text,
    "_layer": "Nx_new_layer",
    "_drop_priority": 35,   # 插入已有层之间，不需要改中心列表
})
```

不再需要修改任何中心列表。`sanitize_messages()` 保证此字段不出 LLM 边界。

`_layer` 和 `_drop_priority` 在 R4-A/R4-B 之后已在 `llm_client.chat()` 入口统一剥离，不再透传给供应商（见下方 **PromptLayer 与 LLM 边界** 章节）。

---

## 层9短期历史选择

`fetch_context()` 调 `short_term.load_for_prompt()`，不是简单读取磁盘末尾 20 轮。

- 磁盘保留上限：`memory.short_term_disk_rounds`，没有则回退 `memory.short_term_rounds`
- prompt 预算：`memory.short_term_rounds`
- 分组：优先按 `_turn_id` 把同一轮 user/assistant 连续消息绑在一起，旧数据按相邻 user+assistant 分组
- 选择：固定保留最近 `NEAR_K=5` 组；更早的组按长度、实体、问句、数字/日期、tag、情绪信号打分择优补足预算
- 日志：`short_term_weight` debug 会记录每组分数和是否入选

---

## PromptLayer 与 LLM 边界（R4-A）

### PromptLayer 结构

`core/prompt_layer.py` 中定义了轻量结构体：

```python
@dataclass(frozen=True)
class PromptLayer:
    name: str              # 层标识，如 "6c_episodic"
    content: str           # 发送给 LLM 的文本
    role: str = "system"   # system / user / assistant
    drop_priority: int | None = None  # 越小越先丢；None = 永不自动丢
```

`PromptLayer` 是项目内部结构，不得直接序列化给供应商。

- `prompt_layer_to_message(layer)` → 返回含 `_layer` 字段的 dict，供 prompt_builder 使用（保留裁剪元数据）。
- `sanitize_messages(messages)` → 返回新列表，剥离所有 `_` 前缀内部字段，用于 LLM API 调用。

### LLM API 边界规则

`llm_client.chat()` 在进入任何分支（function_calling / xml_fallback / plain）前，统一调用 `sanitize_messages()` 清洗入参：

- **剥离**：任何以 `_` 开头的键（`_layer`、`_debug`、`_drop_priority` 等）。
- **保留**：`role`、`content`、`name`、`tool_calls`、`tool_call_id` 等标准 OpenAI 字段。
- **不修改原始对象**：返回新 list + 新 dict，调用方持有的 messages 不受影响。

这意味着：
- `_layer` 可以在 prompt_builder → 裁剪 → 日志 整个内部路径中存在。
- 但出 `llm_client.chat()` 边界后，供应商收到的 messages 里不会有任何内部字段。

### R4-B（已完成）

- 所有可裁剪层在 `messages.append` 时声明 `_drop_priority`；`prompt_layer_to_message()` 也在 `drop_priority is not None` 时写入 `_drop_priority`。
- 裁剪逻辑已迁至按 `_drop_priority` 动态排序，`_DROPPABLE` 中心列表已退役。
- `debug_info["removed_layers"]` 反映实际删除层，不依赖静态列表。

---

## 新增层的规范

新增一层时必须：

1. 在 messages.append() 时加 `"_layer": "N_name"` 字段
2. 如果是可裁剪的非核心层，加 `"_drop_priority": N` 字段（数字越小越先丢）；无需修改任何中心列表
3. 如果是 tagged 层，在 `tag_rules.py` 里确认有对应 tag 规则
4. 在此文档的层总览表格和裁剪顺序表里补充说明

---

## 新增 layer checklist（R4-C 门禁）

每次新增一个 prompt 层时，必须逐项回答以下问题。测试文件 `tests/test_r4c_prompt_layer_contract.py` 会在 CI 中自动验证标有 ⚙️ 的项。

### 1. 这个 layer 是否可裁？

| 判断依据 | 结论 |
|---|---|
| 是辅助/上下文增强层（记忆片段、日记、梦境、世界书等），去掉后不破坏对话基本能力 | **可裁** |
| 是核心身份层（system_prompt、角色描述、关系、author_note、用户消息等），去掉后对话崩坏 | **不可裁** |

### 2. ⚙️ 可裁层：必须声明 `_drop_priority`

```python
messages.append({
    "role": "system",
    "content": some_text,
    "_layer": "Nx_new_layer",
    "_drop_priority": 35,   # 插入已有层之间即可，不需要改任何中心列表
})
```

- 数字越小越先丢（lower = dropped first）
- 同 priority 的消息整批原子性丢弃
- **不得恢复 `_DROPPABLE` 中心表** — R4-B 已退役

### 3. ⚙️ 不可裁层：说明理由，加入 allowlist

若层名包含以下关键词之一（`dream`、`diary`、`episodic`、`event`、`lore`、`afterglow`、`impression`、`mid_term`），但确实不需要 drop_priority，必须在测试文件的 `NON_DROPPABLE_ALLOWLIST` 中加入理由：

```python
# 在 tests/test_r4c_prompt_layer_contract.py 中
NON_DROPPABLE_ALLOWLIST: dict[str, str] = {
    "9.5_episodic_top": "Single top memory placed after history for recency ...",
    "Nx_my_new_layer":  "原因：...",   # ← 新增
}
```

不含上述关键词的非核心层不需要 allowlist，但建议在此文档中备注不可裁原因。

### 4. ⚙️ `_drop_priority` 只能是 `int`

```python
"_drop_priority": 35     # ✓ 正确
"_drop_priority": "35"   # ✗ 禁止字符串
"_drop_priority": None   # ✗ 不写此字段即等效；显式 None 无意义
```

### 5. 内部字段会在 LLM 边界剥离

`_layer`、`_drop_priority` 等 `_` 前缀字段由 `sanitize_messages()` 在 `llm_client.chat()` 入口统一剥离，不会发送给供应商。可以放心在内部使用，无需手动清理。

### 6. 不得恢复 `_DROPPABLE` 中心表

R4-B 已完全退役 `_DROPPABLE`。新层的可裁性由 `_drop_priority` 字段自描述，不需要修改任何中心列表。`tests/test_r4c_prompt_layer_contract.py` 的 Rule 1 会持续检测 `_DROPPABLE` 是否重新出现。

### 快速决策树

```
新增 prompt 层
    │
    ├─ 层名含 dream/diary/episodic/event/lore/afterglow/impression/mid_term？
    │       │
    │       ├─ 是 → 是否可裁？
    │       │           ├─ 可裁 → 加 _drop_priority（选合适数字）
    │       │           └─ 不可裁 → 加入 NON_DROPPABLE_ALLOWLIST + 理由
    │       │
    │       └─ 否 → 是否可裁？
    │                   ├─ 可裁 → 加 _drop_priority
    │                   └─ 不可裁 → 不加（无需 allowlist）
    │
    └─ 在此文档层总览和裁剪顺序表里补充说明
```
