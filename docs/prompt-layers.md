# docs/prompt-layers.md — Prompt 层结构

---

## 层总览（实际执行顺序）

> 注意：层编号和实际插入顺序不完全一致（3.5~3.8 在 4 之后插入）。以下是代码实际执行顺序。

| 层标识 | 内容 | 触发条件 | 数据来源 |
|---|---|---|---|
| `0_jailbreak` | 破限预设 layer=0 | 文件存在且 enabled | stems（`jailbreaks/{stem}.json`，受 `enabled_jailbreaks` 控制）+ `characters/reality/jailbreak_entries.json`，按内容去重合并 |
| `1_system_prompt` | 角色存在性定义 + 情绪软提示 + `{perception_block}` 槽位 | always | `characters/yexuan.json` + `core/mood_text.py` |
| `1.5_fact_boundary` | 数据驱动单句：有实时感知数据时给出数据 + "仅以上为已确认，其余未知"；无数据时注入禁令句（物品/食物/天气等一律未知） | always | `_format_realtime_awareness()` 结果条件注入（`core/prompt_builder.py`） |
| `2_char_desc` | 角色描述 + 性格 + 情境 | always | 角色卡 |
| `2.2_stage_presence` | 群聊在场成员与公开发言提醒 | reality Stage 角色生成时 | `core/stage/context.py` |
| `2_jailbreak` | 破限预设 layer=2 | 文件存在且 enabled | stems + `characters/reality/jailbreak_entries.json`，按内容去重合并 |
| `2.5_time` | 当前时间（年月日 时:分 星期X） | always | 实时生成 |
| `2.55_last_seen` | 用户上一条消息距现在的精确时间差（如"约3小时12分钟"） | 非静默时段 且 gap ≥ 6 小时 | `core/presence.py` → `get_gap_from_history()` + `format_gap_text()` |
| `2.6_activity` | 他此刻的状态 | 对话开头（history 为空）或沉默超10分钟 | `activity_manager.get_prompt_fragment(char_id=char_id)`（CC 任务 24 · 3 起按角色隔离，此前全角色共用 yexuan 状态），每15-45分钟随机切换，部分活动会从 episodic_memory 按 strength 加权抽一条记忆作为"他在想什么"注入 |
| `3_relation` | 与该用户的关系 + 称呼 | always | `user_relation` |
| `4_group_context` | 群聊最近动态 | 群聊时 | `group_context.get_recent()` |
| `4.2_stage_transcript` | 带真实 speaker 标签的共享 Stage transcript | reality Stage 角色生成时 | `core/stage/context.py` |
| `3.5_period` | 生理期感知（第N天） | tagged（见下） | `user_profile.get_period_info()` |
| `3.6_watch` | 最近一次睡眠数据（以角色第三人称旁白注入，无方括号标签） | tagged（见下） | `user_profile` sleep_segments |
| `3.7_sensor` | 手机传感器（步数/电量/位置/亮屏次数，以角色旁白注入，无方括号标签/时间戳/数据来源描述） | 当天有数据即注（无 tag 门控） | `user_profile.phone_sensor_today` |
| `3.8_activity` | 屏幕活动快照（以角色旁白注入，无方括号标签；内容来自 activity_snapshot 的类别字段，不含原始应用名） | tagged（见下） | `data/runtime/characters/{char_id}/inner/activity_snapshot.json`（TTL 5分钟） |
| `3.9_screen_awareness` | 桌面实时感知摘要（粗粒度应用/活动类别 + 模糊编辑状态；不注入窗口标题或屏幕原文） | 活动相关 tagged 快照 5 分钟内，或用户活跃且快照 3 分钟内 | `core.memory.realtime_state`（纯内存，重启清零） |
| `5_profile` | 用户画像（名字/位置/宠物/兴趣/职业）+ stable/misc 标签事实 | 有内容即注 | `user_profile.load()` |
| `5_profile_pref` | 用户偏好/习惯类事实（pref.*/habit/health tag） | recency 90天窗口内 OR 当前轮 tag 命中 | `user_profile.load()` → `_is_recency_tag()` |
| `5.1_user_facts` | 跨角色全局用户事实（uid-only，与角色主观记忆无关，标题明确区分不是角色记忆） | `user_facts_text` 非空 | `core/memory/user_facts.py` → `format_for_prompt()` |
| `5.2_reminders` | 待办备忘录列表 | 有待办即注 | `get_reminders()` |
| `5.5_lore` | 世界书条目 | LoreEngine 命中时 | `lore_engine.match()` |
| `6a_user_identity` | 用户稳定行为模式 | `user_identity_text` 非空 | `core/memory/user_identity.py`，confidence >= 0.5 的维度 |
| `6b_event_search` | 相关往事（event_log 搜索结果） | 搜索结果非空；`fetch_context(recall_policy="none")` 时整层跳过（CC 任务 19 · C） | `event_log.search()` |
| `6c_episodic` | 情景记忆片段 | episodic_result 非空；`fetch_context(recall_policy="none")` 时整层跳过（CC 任务 19 · C） | `episodic_memory.retrieve()` + `format_for_prompt()` |
| `6c_episodic_fallback` | 近期高强度记忆兜底 | episodic_result 为空且 fallback 非空；`recall_policy="none"` 时同样跳过 | `episodic_memory.retrieve_fallback()`；实际消息 `_layer` 仍写 `6c_episodic`，便于统一裁剪 |
| `mid_term` | 过去 12 小时对话压缩视图 | mid_term_context 非空 | `mid_term.format_for_prompt()`（12h 过期，最多 20 条，三时间桶渲染） |
| `6d_diary_context` | 用户近期日记 | 有内容且命中 `emotion.down` / `emotion.indirect`；**新鲜度闸**：`diary_context.meta.json` 中 `latest_entry_date` 距今 >4 天（可配置 `diary.context_max_age_days`）或无 meta 时不注入；**低信息准入闸**：用户消息为 backchannel 时不注入 | `diary_context.load()` + `diary_context.load_meta()` |
| `6e_inner_diary_facts` | 他昨天的记录（事件层，取前200字） | 昨日日记文件存在且含事件层 | `data/runtime/characters/{char_id}/inner/diary/` |
| `6e_inner_diary_feeling` | 他昨天的心情（感受层，取前150字） | 昨日日记存在且命中 `emotion.down/indirect/deep` 或 `topic.relation`；**低信息准入闸**：`suppress_emotional_recall=True` 时跳过 | `data/runtime/characters/{char_id}/inner/diary/` |
| `web_recall` | X3 向量库语义召回的相关网络资料（外部事实，非记忆/经历，标注来源） | `web_recall_result` 非空（`vs.query_with_preview(sources=["web"], k=3)` 命中） | `core/pipeline.py` fetch_context X3 块 → `prompt_builder.build(web_recall_result=)` |
| `6f_dream_afterglow` | 梦境余韵详细层（只读，非现实事实）：0–2h 完整摘要/色调/意象；2–5h 模糊摘要/色调 | 5h 内存在有效 dream summary | `core/dream/dream_afterglow.load_afterglow()`；5h 后返回空并交接给软提示层 |
| `dream_afterglow_soft_hint` | 梦境余韵软提示（只读，非事实，TTL 8h，`may/可能` 限定语气，`neutral+空tags` 不注入） | 详细 afterglow 层为空，且 afterglow_residue.json 存在、TTL 未过期、tone≠neutral 或 tags 非空 | `core/prompt_builder._format_afterglow_soft_hint()` → `core/memory/user_hidden_state.read_afterglow_residue()` → `data/runtime/memory/{char_id}/{uid}/afterglow_residue.json`（S6 路径，详见 docs/memory.md §记忆层一览） |
| `6g_dream_impression` | 梦境印象回流（ambient，≤3条，非事实框定，他自述"我好像在梦里……"） | 有未过期印象时注入 | `core/dream/impression_loader.load_impression_text()` → `data/runtime/dreams/{char_id}/impressions/{uid}.json` |
| `coplay_context` | 陪玩模式游戏名 + 进度 + 最近3条动态 + 剧透压制硬约束（`<陪玩状态>`定界） | `CoplaySession.status == active` | `core/coplay/game_state.py::build_coplay_context_text()`，读 `core.coplay.session` + `game_state` + `observer.peek_moments()` |
| `coplay_residue_soft_hint` | "刚陪她打完《X》，还有点意犹未尽"体裁软提示 | session 收尾后 4h TTL 内，且非 active（与 `coplay_context` 互斥） | `core/coplay/afterglow.py::load_afterglow_text()`，fail-closed |
| `coplay_recall` | 聊天提到玩过的游戏名/别名时，回忆上次游玩摘要（`<陪玩回忆>`定界） | 命中 `game_state` 里任一已玩游戏的 `game_name`/`aliases` 子串，且非 active | `core/coplay/game_state.py::build_game_log_recall_text()` |
| `7_mes_example_item` | 对话示例（few-shot），前后各有 `<语气示例>` / `</语气示例>` 定界标签，明示"仅作风格参考，非真实对话" | always（有内容） | 角色卡 mes_example |
| `9_history` | 短期对话历史，前后各有 `<对话记录>` / `</对话记录>` 定界标签，明示"以下是真实发生的对话"；近场保留 + 远场加权择优；投影时跳过 `_source=="trigger_stub"` 防触发器名泄露 | always | `short_term.load_for_prompt()` |
| `9_anti_repeat` | 跨轮开头去同质：取最近 2–3 条 assistant 回复的起手（首 8 字），以软约束告知模型别用相同开头/句式；fail-open，无历史时不注入 | 有近期 assistant 回复时 | `_recent_openings()` 从 history 提取 |
| `9.5_episodic_top` | 最相关情景记忆1条（attention sweet spot） | episodic_result 非空 | 从已召回结果取第一条，不重复召回 |
| `10_tool_result` | 本轮工具执行结果 | 有工具调用时 | `tool_dispatcher.execute()` 裸输出经 `core/tools/tool_result.py` 截断+定界框定后注入（`safe_summary`） |
| `10.5_action_trace` | 工具动作痕迹：你最近做过的操作（跨轮回忆，供角色记得"刚才做了什么"，不要求逐条复述） | `action_trace_entries` 非空（`recent()` 过滤后仍有条目） | `core/memory/action_trace.py` → `recent()` + `format_trace_block()`；`fetch_context()` 拉取，`build_prompt()` 透传 |
| `anti_collapse_hint` | 反坍缩提示（长度坍缩 + 分段坍缩合并，按触发维度拼装文案），per-uid 持久化倒计时 `hint_rounds` 轮（默认3），不可裁 | `anti_collapse.enabled` 且长度/分段任一维度倒计时未归零 | `core/memory/short_term.py::get_anti_collapse_hint()`（长度维度沿用 `detect_reply_length_collapse()`；分段维度由 `note_segment_collapse_signal()` 在 `capture_turn()` 落盘时写入） |
| `11_author_note` | 人设核心提醒 + 输出格式规则 + 风格补充 | always | 硬编码 + `author_note_rotator` + consistency_check |
| `11_jailbreak` | 破限预设 layer=11 | 文件存在且 enabled | stems + `characters/reality/jailbreak_entries.json`，按内容去重合并 |
| `11.5_post_history` | 酒馆卡「历史之后」约束层：`post_history_instructions` + `post_history_extra`（常驻 after 型世界书）；核心约束，永不被自动裁剪 | `character.post_history_instructions` 或 `post_history_extra` 非空时 | `core/character_loader.py` Character 字段（由 `scripts/import_st_card.py` 导入填入） |
| `11.7_pinned_facts` | 用户主动强调过、要求记住的高价值事实（如生日），与泛化画像分离；不可裁 | `profile["pinned_facts"]` 非空且未与 5_profile 内容重复 | `profile["pinned_facts"] = [{text, ts, source}]` |
| `12_time_hint` | 时间提示（`<时间提示>距上一条消息已过去约X</时间提示>`） | gap ≥ 10 分钟 | `core/presence.py` → `get_gap_from_history()` + `format_gap_text()` |
| `12_user_message` | 用户当前消息 | always | 用户输入 |

> 层 10 注入安全：工具裸输出经 `ToolResult.safe_summary`（截断上限 2000 字符）包裹后，以定界标记 `<<<TOOL_DATA_START>>>` / `<<<TOOL_DATA_END>>>` 加反注入指令框定，防止外部工具/搜索结果中的不可信文本被模型当作指令执行。原始数据仅落 debug 日志，永不进 prompt/memory。
>
> 层 10.5（Brief 27）：`tool_dispatcher.execute()` 每次 return（origin 闸门拒绝除外）都调 `action_trace.record()` 落一条精简痕迹（`data/runtime/memory/{char_id}/{uid}/action_trace.json`，环形上限 30 条）；Path B（`pipeline._parse_and_execute_intent`）不经 `execute()`，单独补记。`result_digest` 只消费 `ToolResult.safe_summary`，`peek_screen_content` 特判只留 title_hint。**当轮去重**：本轮已有 `tool_result` 且其工具名与痕迹最新一条相同时跳过该条，避免层10/10.5 重复同一件事。不进 `_drop_priority` 裁剪链（够小且时效性强），全层预算截断 400 字。`action_trace.enabled: false` 时零行为变化。可选 `event_log_echo` 配置项：`status=ok` 时经 `fixation_pipeline.capture_turn(trigger_name="action_trace")` 回流一条到 event_log（**不得**直接调用底层写入函数，见 `tests/test_r6b_reality_scrub_contract.py` C2 契约）；回流文案刻意不整行包在中文括号里，否则会被 `scrub_reality_output_text` 当整行动作旁白丢弃。
>
> `6f_dream_afterglow` 与 `dream_afterglow_soft_hint` 为互斥层：前者在退梦后 0–5h 注入逐渐模糊的摘要，后者在详细层为空后接管至 8h TTL。两层均只读、非现实事实、读取异常 fail-closed，不写 memory / mood / profile / hidden state。
>
> 破限层双来源+去重（CC 任务 24 · 1）：`_load_jailbreak(layer)`（`core/prompt_builder.py`）合并两套并行存储——stems 源（`characters/reality/jailbreaks/{stem}.json`，受 `active_prompt_assets.json` 的 `enabled_jailbreaks` 控制）与 entries 源（`characters/reality/jailbreak_entries.json`，前端「偏好→世界→破限条目」EntryManager 管理）。两源均按各自的 `enabled` + `layer` 过滤后合并，`content.strip()` 相同的条目跨源只注入一次（先出现的保留，通常是 stems 源）。entries 源读取失败时 fail-open（不影响 stems 源正常注入）。

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
Brief 35 已移除的 `character_growth` 全文/指纹层不再可切换。当前长期人格模式入口是 always-if-present 的
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
# 跨通道切换时追加中性接续提示（不含通道名/UI实现名）
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
2. 记忆/人格约束单句（旧记忆≠当前事实，保持角色边界；原四块协议已压缩为此句）
3. `author_note_extra`（consistency_check 临时纠偏；`（...）` 包裹；用完即清）
4. S2 防句式坍缩软提示（句首同质性检测，命中时注入；`core/memory/short_term.py::detect_reply_homogeneity_prefix()`，与层9历史投影去同质复用同一份检测结果 `_s2_prefix`；填充词前缀「嗯/啊/呃/哦/唔/哈」等命中时用不复读字面的文案，避免再次 prime 同一个词，其余前缀沿用引用式文案，见下方「反坍缩治理」）
5. 【输出格式】（`chat` 或 `roleplay`，由 config.yaml `chat.style` 决定；两种模式都常态要求正文至少两段、段间一个空行；`chat` 分段不依赖句号）
6. 【词级强调】每条回复在情绪/语义焦点处用一次 `<hl>`；需要时再用 `<big>/<sm>`，每条 1–3 处
7. 条件工具规则（R5）：
   - 有 `tool_result`：`【工具结果已提供】`，提示层10已注入，禁止再声称调用
   - 无 `tool_result`：`【无工具结果】`，禁止声称调用工具，禁止编造日记/实时数据
8. 表达规则（禁止复用对话示例原句）
9. `style_hint`（从 observations.jsonl 读取，深夜/压力状态提示词；直接追加，不加方括号）
10. 破限预设 layer=11

注意：S3 防字数坍缩（长度维度）与 Brief 54-B 新增的分段坍缩（S4）**不再拼进 Author's Note**，
已独立为 `anti_collapse_hint` 层（见上方层总览与下方「反坍缩治理」），带自己的 per-uid 持久化
倒计时，不与 Author's Note 的裁剪/组装逻辑耦合。

注意：正式主 LLM 调用没有接入任何 tools schema；`get_time` 等 info/desktop 工具
由 pre-pipeline 探针触发，结果以 `tool_result` 参数进入层10。memory 类工具
（`read_diary` / `search_diary` 等）需用户明确触发，主 LLM 不能自行调用。
R5 修复了旧版 Author's Note 里"必须调用 read_diary"的工具幻觉风险。

---

## 反坍缩治理（CC 任务 24 · 2，Brief 54-B 增强）

「嗯。」句首坍缩、字数坍缩与分段坍缩的治理，按软→硬排列：

### S2 句首同质化：检测 + 历史投影去同质 + 输出端重试

1. **检测**（`core/memory/short_term.py::detect_reply_homogeneity_prefix()`）：近 6 条 assistant
   回复前 2 字有 ≥3 条相同 → 返回原始前缀 P；`is_filler_prefix(P)` 判断 P 是否属于填充词白名单
   （`嗯 啊 呃 哦 唔 哈`，可跟 `。，、！？…～~,.!?` 标点）。`build()` 顶部只检测一次（`_s2_prefix`），
   层9历史投影与层11软提示复用同一结果，避免两处判断不一致。
2. **历史投影去同质**（`build()` 组装层9历史时，`_dedupe_filler_prefix_history()`）：仅当 P 是填充词时
   生效——保留最早一条完整的 P，其余各条剥掉开头的 P，避免模型从上下文里"学到"每句都要 P 开头。
   **只改注入 prompt 的历史副本，绝不写回 short_term 存储**；被剥的消息额外带内部字段 `_raw_content`
   记录原文，供输出端重试复原检测所需的原始文本，与 `_layer` 一样在 `sanitize_messages()` 出口被剥离。
3. **层11软提示文案**：P 是填充词 → 不复读字面的文案（"这次第一个字直接进正文"），避免再次 prime 该
   token；非填充词前缀（如"现在，"）→ 保留引用式文案（"开头连续用了『P』"）。
4. **输出端校验重试（硬止血）**（`core/pipeline.py::Pipeline._anti_collapse_prefix_retry()`，`run_llm()`
   内调用）：LLM 输出仍以 P 开头 → 追加一条强 system 指令重试 1 次；重试仍命中且 P 为填充词 → 剥掉开头
   P 后接受，非填充词前缀只接受重试结果不做硬剥离。仅覆盖 `run_llm()`（非流式）路径，`run_llm_stream()`
   暂不接入（流式已实时吐出的 token 无法撤回，需要独立设计，见 `docs/known-issues.md`）。开关
   `anti_collapse.prefix_retry`（默认 `true`）；重试计入一次额外 LLM 调用成本，日志 `[anti_collapse] prefix retry`。

### S3 字数坍缩：长/短两挡非对称触发 + 持久化倒计时（Brief 54-B）

`detect_reply_length_collapse(history, *, short_max=60, recent_n_long=4, recent_n_short=7)`：字符数
`< short_max` 为短句，`>= short_max` 为长句（单边界，只分两挡，替代旧版 5 挡 `thresholds`）。这个
检测函数本身**无状态**——每轮都重新用 history 窗口判断。

- 近 `recent_n_long` 条全为长句 → 触发（**易触发**）：`（最近几条都挺长，这次收短——去掉铺垫和水词，捡最有劲的一两句说。）`
- 否则近 `recent_n_short` 条全为短句 → 触发（**难触发**）：沿用原通用打破惯性文案。
- 设计意图：模型爱往注水长句坍缩，短句反而更像活人；5 挡太密会限制模型表达。

**旧问题**：无状态检测导致"当轮命中→注入提示→下轮 history 窗口一变就撤"，模型下一轮立刻弹回长文。
**Brief 54-B 修复**：`core/memory/short_term.py::get_anti_collapse_hint()` 在检测结果外面套了一层
per (char_id, uid) 内存倒计时——命中后计数器设为 `hint_rounds`（默认3，含触发当轮），此后每轮调用
衰减 1，倒计时未归零期间沿用同一份命中文案继续注入；期间再次命中 → 计数器**重置**为 `hint_rounds`
（不是叠加，也不提前清零，避免来回振荡）。重启进程后计数器丢失，可接受。

### S4 分段坍缩：连续 2 轮无换行 + 超长（Brief 54-B 新增）

角色回复堆成一整段不分行，同样是长期对话质量劣化的一种表现，与字数坍缩共用同一套持久化倒计时
机制，但检测点不同：

- **信号**：`core/memory/short_term.py::note_segment_collapse_signal()`——文本不含 `\n` 且长度
  超过 `segment_min_len`（默认 40 字）视为一次"未分段"命中；连续 `segment_recent_n`（默认 2）轮
  命中才把分段维度的倒计时设为 `hint_rounds`，未连续命中只清零 streak。
- **判定点必须是原始文本**：`_sanitize_assistant_message`（`load()` 时才生效）和
  `scrub_reality_output_text`（`capture_turn()` 内部）都可能按行/按段过滤内容，破坏"是否有换行"
  这个信号——history 里读到的 assistant 内容已经是二次加工过的，不能反过来拿它判断分段。因此
  `note_segment_collapse_signal()` 在 `core/memory/fixation_pipeline.py::capture_turn()` 收到
  `reply` 参数（两次 scrub 之前）时就立即调用，而不是在 `build()` 里用 `history` 重新判断。
- **提示文案**：`（最近几条回复都挤成一大段没有换行，超过两句就空一行分段，别把话都堆在一起。）`
- **与长度维度的合并**：两个维度各自独立倒计时，`get_anti_collapse_hint()` 在同一次调用里把两边
  仍处于倒计时中的文案拼接成一个 `anti_collapse_hint` 层——都触发时两句话一起注入，只触发一个就只
  注入那一个。

**预防 vs 兜底**：`anti_collapse_hint` 层（S3+S4）是生成前的**预防**——在 prompt 里提前劝模型；
S2 的输出端重试（下方）是生成后的**兜底**——已经生成了还硬拦一次。两者并存，互不替代。日志上也
刻意用不同文案区分来源：预防命中打 `[anti_collapse] hint_injected`（`core/prompt_builder.py`），
S2 兜底重试打 `[anti_collapse] prefix retry`（`core/pipeline.py`），grep 关键字互不重叠。

配置 `config.yaml` `anti_collapse:` 块：

```yaml
anti_collapse:
  enabled: true
  short_max: 60
  recent_n_long: 4
  recent_n_short: 7
  prefix_retry: true
  hint_rounds: 3         # 长度/分段提示触发后连续注入的轮数
  segment_min_len: 40    # 分段坍缩阈值：无换行且字数超过此值才计一次"未分段"命中
  segment_recent_n: 2    # 连续几轮命中"未分段"才触发分段提示
```

旧键 `thresholds` / `recent_n` 读到时忽略并打 `[anti_collapse] 配置键 thresholds/recent_n 已废弃...` warning，不做自动迁移。
`hint_rounds` / `segment_min_len` / `segment_recent_n` 缺省时分别回退到
`core/memory/short_term.py` 的 `DEFAULT_HINT_ROUNDS` / `DEFAULT_SEGMENT_MIN_LEN` / `DEFAULT_SEGMENT_RECENT_N`
硬编码默认值（3 / 40 / 2），不报错。

### S4 生成后兜底：segment_enforcer（Brief 72）

`core/output/segment_enforcer.py::enforce_paragraph_breaks()` 是 S4 预防之外的发送前硬兜底：
当前段落长度超过有效 `min_len` 后，在下一个 `。！？…` 句末插入一个空行；已有换行会重置段长，
不改写标点，不增删字词。QQ 路径在
`core/response_processor.py::process()` 完成清理后、`_split_message()` 前调用；桌面/手机 Reality 路径
在 `core/reality_output_guard.py::clean_reality_reply_text()` 出口调用。Dream 不在本机制覆盖范围。

桌面流式路径使用同模块的 `ParagraphStreamEnforcer` 增量处理发送副本：达到阈值的句末出现后，
在下一句第一个可见字符到达时立即发出 `\n\n` delta。状态机会先接纳右引号，并追踪 XML/NMP
标签边界，避免把 `。”` 或 `<say>…</say>` 拆跨两个气泡；流结束后的 canonical 文本再用同一规则
校正，前端按同一 `msg_id` 替换临时气泡。开关关闭或处理异常时 delta 原样透传。

该兜底由 `output.segment_enforce.enabled` 控制，默认关闭；`output.segment_enforce.min_len` 缺省时回退
到 `anti_collapse.segment_min_len`，再缺省回退 S4 的 `DEFAULT_SEGMENT_MIN_LEN=40`。运行时或文本处理
异常均 fail-open，直接返回原文。`GET/PUT /output-segment-enforce` 可热切换，管理面板「Prompt 层检视」
和桌面客户端「偏好 → 系统设置」均提供入口。

**存储红线**：enforcer 只能处理发送副本（包含流式 delta 与最终 canonical），结果绝不写回
`short_term` / `event_log`。S4 的
`note_segment_collapse_signal()` 必须继续读取模型原始回复，否则开启兜底后会掩盖模型真实的分段坍缩，
令生成前预防错误失效。

```yaml
output:
  segment_enforce:
    enabled: false
    min_len: 40
```

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
| 12 | `coplay_residue_soft_hint` | 陪玩结束后软提示，同 dream 软提示量级，早丢 |
| 15 | `9_anti_repeat` | 跨轮开头去同质软约束，fail-open 只读，无近期历史时不注入 |
| 20 | `6g_dream_impression` | 梦境印象回流，ambient 非事实框定，次先丢 |
| 25 | `3.9_screen_awareness` | 桌面实时感知摘要，短 TTL 辅助氛围层 |
| 30 | `6b_event_search` | 关键词 + 评分搜索结果，质量较低 |
| 35 | `web_recall` | X3 向量库 web 资料召回，外部事实非记忆，早于 mid_term 丢 |
| 40 | `mid_term` | 过去 12 小时压缩视图 |
| 45 | `coplay_recall` | game_log 摘要回忆（tag 门控命中游戏名/别名） |
| 50 | `6d_diary_context` | 用户近期日记，tag 门控注入 |
| 60 | `6e_inner_diary` | 角色昨天日记（事件层 + 感受层，同 priority 整批丢） |
| 70 | `6c_episodic` | LLM 压缩 + MMR 筛选的情景记忆，高质量，靠后丢 |
| 80 | `5.5_lore` | 世界书设定，最后丢 |
| 85 | `coplay_context` | 陪玩模式游戏进度/动态 + 剧透压制约束，内容很小，比 lore 更晚丢 |

不在裁剪表里（无 `_drop_priority`）：`6a_user_identity`、`5_profile`、`5_profile_pref`、`5.1_user_facts`、`9_history`、`11_author_note`、`11.5_post_history`、`10.5_action_trace`（够小且时效性强，不参与裁剪）、`anti_collapse_hint`（触发时才存在，内容是纠偏软提示，裁掉即失去纠偏效果，够小不参与裁剪）等核心层。

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
- 分组：优先按 `_turn_id` 把同一轮所有 speaker 连续消息绑在一起，旧数据按相邻 user+assistant 分组
- 存储 entry 带 `speaker_id`；当前单聊层9投影为标准 `{role, content}`，不把元数据发送给 LLM
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

- **剥离**：任何以 `_` 开头的键（`_layer`、`_debug`、`_drop_priority` 等），以及本地 transcript 元数据 `speaker_id` / `timestamp`。
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

---

## 结构定界约定（XML 成对标签）

所有**注入外部内容**的层（记忆片段、日记、关系、世界书等）content 均以成对中文 XML 标签包裹，格式：

```
<标签名>
【内部小标题】（可选）
...实际内容...
</标签名>
```

已包裹的层和对应标签名：

| 层 | 标签 |
|---|---|
| `3_relation` | `<与用户关系>` |
| `4_group_context` | `<群聊上下文>` |
| `4.2_stage_transcript` | `<群聊对话>` |
| `5_profile` | `<用户概况>` |
| `5_profile_pref` | `<用户偏好>` |
| `5.1_user_facts` | `<用户客观信息>` |
| `5.2_reminders` | `<待办备忘>` |
| `5.5_lore` | `<世界书>` |
| `6b_event_search` | `<相关往事>` |
| `6c_episodic` | `<情景记忆>` |
| `mid_term` | `<近12小时摘要>` |
| `6d_diary_context` | `<近期日记>` |
| `6e_inner_diary_facts` | `<昨日记录>` |
| `6e_inner_diary_feeling` | `<昨日心情>` |
| `web_recall` | `<查到的资料>` |
| `coplay_context` | `<陪玩状态>` |
| `coplay_recall` | `<陪玩回忆>` |
| `7_mes_example_item`（包裹整组） | `<语气示例 note="...">` |
| `9_history`（包裹整组） | `<对话记录 note="...">` |
| `9_anti_repeat` | `<避免复读>` |
| `12_time_hint` | `<时间提示>` |

**配套完整性检查**：`build()` 末尾在 token 估算前会对所有 content 以 `<非斜线` 开头的消息做配平检查，不配平打 `WARNING [prompt_integrity]`。

**规则**：新增外部内容层时必须用成对标签包裹，并在上方表格补充登记。标签名用中文保持可读，内部可保留 `【小标题】` 抬头。

---

## 来源维度（origin）

每一轮快照的顶层字段 `origin` 标识这轮 `build_prompt()` 的调用来源：

| `origin.origin` | 管理面板徽章 | 含义 |
|---|---|---|
| `"user"` | 灰色「用户」 | QQ / 外部消息触发（默认；现有调用方无需改动） |
| `"desktop"` | 蓝色「桌宠」 | 桌宠 chat 入口（`admin/routers/chat.py::run_owner_chat_turn`） |
| `"proactive"` | 绿色「主动 · trigger_name」 | 调度器触发（`_pipeline_send`）；额外字段 `trigger_name`、`seed_prompt`、`search_query`、`recall_policy` |

实现：`core/observe/prompt_capture.py` 的 `ContextVar _capture_origin`（默认 `{"origin":"user"}`）；
各调用方在 `build_prompt()` 前调用 `set_capture_origin(info)` 写入。
ContextVar 保证同一 asyncio task 内隔离，不同并发请求互不影响。

**主动轮额外字段**（仅 `"proactive"`）：

| 字段 | 含义 |
|---|---|
| `trigger_name` | 触发器名（如 `"daily_journal"`） |
| `seed_prompt` | 喂给第 12 层的内容（触发器组装的"用户位"消息） |
| `search_query` | fetch_context 用的 RAG 锚点；空 = 与 seed_prompt 相同 |
| `recall_policy` | CC 任务 19 · C：`"none"`（跳过 6b_event_search/6c_episodic/web_recall 三层检索，只保留状态层）/ `"anchored"`（检索开启，锚点是触发器自带具体话题）/ `"seed"`（检索开启，锚点是 search_query 或 prompt 全文，默认值）。详见 `docs/scheduler.md`「召回锚点治理：recall_policy」 |

---

## 固定/召回 标注（provenance）

每一轮 prompt 快照（`GET /observe/prompt-layers/{uid}`）里，每个层现在带一个 `provenance` 字段，指示它当轮是如何进入 prompt 的：

| mode | 管理面板徽章 | 含义 |
|---|---|---|
| `always` | 灰色「常驻」 | 无条件注入（如 `1_system_prompt`、`11_author_note`） |
| `tagged` | 蓝色「标签召回」 | 满足 tag 门控才注入；`triggers_checked` 列出检查了哪些 tag，`matched_tags` 是命中的 |
| `scored` | 紫色「打分召回」 | RAG 相似度/评分机制召回；`rag_query` 是用于检索的原始查询串 |

### 已标注的条件层

| 层 | mode | 备注 |
|---|---|---|
| `3.5_period` | `tagged` | triggers: `topic.body`、`emotion.physical_discomfort` 等 |
| `3.6_watch` | `tagged` | triggers: `topic.energy`、`topic.health` 等 |
| `3.8_activity` | `tagged` | triggers: `topic.activity`、`query.what_doing` 等 |
| `6b_event_search` | `scored` | rag_query = 用户消息前 200 字符 |
| `6c_episodic` | `scored` | rag_query = 用户消息前 200 字符 |
| `6c_episodic_fallback` | `scored` | rag_query = `"(fallback: recent high-strength)"` |
| `6d_diary_context` | `tagged` | triggers: `emotion.down`、`emotion.indirect` |
| `web_recall` | `scored` | rag_query = 用户消息前 200 字符；额外带 `source`（固定 `"vector_store:web"`）与 `hits`（`[(url, dist), ...]`，来自 `vs.query_with_preview(sources=["web"])`） |
| `6e_inner_diary_feeling` | `tagged` | triggers: `emotion.down`、`emotion.indirect`、`emotion.deep`、`topic.relation` |

### 实现说明

- `core/prompt_builder.py`：条件层在 `messages.append()` 时附带内部字段 `_provenance: dict`（mode + triggers/rag_query，`web_recall` 额外带 `source`/`hits`），always 层不写（`capture()` 默认推断为 `always`）。
- `core/observe/prompt_capture.py`：`capture()` 从每条消息提取 `_provenance`，生成快照 `layers[]` 中的 `provenance` 字段（白名单浅拷贝，含 `source`/`hits`）。
- `_provenance` 与 `_layer`、`_drop_priority` 性质相同——均由 `sanitize_messages()` 在 `llm_client.chat()` 入口统一剥离，**不会发送给供应商**。

---

## 探针观测（Probe Capture）

`info`/`desktop` 工具调用的完整记录，逐轮存入内存环形缓冲区，供管理面板"探针观测"页查阅。

### 架构

| 组件 | 位置 | 说明 |
|---|---|---|
| 环形缓冲区 | `core/observe/probe_capture.py` | `deque(maxlen=5)` per uid，纯内存，重启清零 |
| 捕获入口 | `main.py` → `handle_message()` | 在探针分支（快速路径 / LLM 探针）末尾调用 `capture_probe()` |
| API | `admin/routers/observe.py` | `GET /observe/probe`（uid 列表）、`GET /observe/probe/{uid}?n=0`（第 n 条，0=最新） |

### 快速路径快照字段

```json
{
  "is_fast_path": true,
  "matched_tool": "get_time",
  "matched_keyword": "几点",
  "fast_path_risk": false,
  "user_message": "...",
  "tool_calls": [{"name": "get_time", "arguments": {}}],
  "tool_results": [{"name": "get_time", "result": "...", "has_side_effect": false}],
  "captured_at": "2026-06-20T..."
}
```

### LLM 探针快照字段

```json
{
  "is_fast_path": false,
  "probe_system": "（系统 prompt，可展开查看）",
  "probe_context": "（注入 LLM 的上下文，可展开）",
  "user_message": "...",
  "tools_available": ["get_time", "water_garden", "..."],
  "probe_response_raw": "（LLM 原始输出，可展开）",
  "tool_calls": [...],
  "tool_results": [...],
  "captured_at": "2026-06-20T..."
}
```

### 设计约束

- **只读观测**：capture 不影响任何生成逻辑，capture 异常会被静默 catch，不中断主流程。
- **不持久化**：重启后环形缓冲区清空，没有磁盘写入。
- **隔离于 reality prompt 快照**：`probe_capture` 与 `prompt_capture` 是独立模块，不共享 ring。

---

## 梦境 Prompt 检视器（Dream Prompt Inspector）

梦境生成（`dream_turn()`）的完整 prompt 结构快照，与 reality pipeline 完全隔离。

### 架构

| 组件 | 位置 | 说明 |
|---|---|---|
| 环形缓冲区 | `core/observe/dream_capture.py` | `deque(maxlen=5)` per uid，纯内存 |
| 捕获钩子 | `core/dream/dream_prompt.py` → `build_dream_prompt()` | 可选参数 `_capture_hook: Any \| None = None`，默认 None（现有调用方无感） |
| 捕获入口 | `core/dream/dream_pipeline.py` → `dream_turn()` | 构造 hook → 传入 `build_dream_prompt()` → LLM 后调 `update_dream_llm_output()` |
| API | `admin/routers/observe.py` | `GET /observe/dream-prompt`（uid 列表）、`GET /observe/dream-prompt/{uid}?n=0` |

### 快照字段

```json
{
  "world_id": "prison_demo",
  "lucid_mode": false,
  "dream_mode": "SCENARIO",
  "scene_tags": ["confined", "emotional_pressure"],
  "total_tokens": 3200,
  "history_turns": 6,
  "layers": [
    {"label": "D0 dream_system", "chars": 512, "tokens": 256, "flags": [], "note": "", "injected": true},
    {"label": "D4.5 scenario_lore", "chars": 0,   "tokens": 0,   "flags": ["skipped"], "note": "tag not matched", "injected": false},
    ...
  ],
  "user_message": "...",
  "dream_id": "...",
  "llm_output": "（LLM 梦境回复，捕获时配对写入）",
  "captured_at": "2026-06-20T..."
}
```

### 与 reality 快照的隔离保证

| 项目 | reality prompt 快照 | dream prompt 快照 |
|---|---|---|
| 模块 | `core/observe/prompt_capture.py` | `core/observe/dream_capture.py` |
| API 路由 | `/observe/prompt-layers/{uid}` | `/observe/dream-prompt/{uid}` |
| 捕获来源 | `pipeline.build_prompt()` | `dream_prompt.build_dream_prompt()` |
| 层编号前缀 | `1_`、`6c_`… | `D0`–`D10` |
| 混入风险 | ✗ 无，两条路径完全不交叉 | ✗ 无 |

### 设计约束

- **`_capture_hook` 向后兼容**：参数可选，默认 `None`；所有现有测试不传 hook，不受影响。
- **Hook 异常静默**：`build_dream_prompt()` 内部 try/except，hook 报错只写 debug 日志，不中断梦境生成。
- **不写 hidden_state / impression / afterglow**：检视器仅旁观，不向任何持久化路径写入。

### D1/D8 模板插值约定（Brief 25 §3 P0）

`core/dream/dream_prompt.py` 的 `_D1_LUCID_AWARENESS`、`_D1_NON_LUCID_AWARENESS`、
`_D8_DREAM_DIRECTOR`、`_D8_DREAM_DIRECTOR_NON_LUCID` 四个模板字符串不写死角色名/用户名，
统一用 `.format()` 占位符：

- `{name}` — 角色显示名，`build_dream_prompt()` 里从 `character.name` 取。
- `{pronoun}` — 角色代词（他/她/ta），从 `character.gender` 经
  `core/character_name_provider._PRONOUN_MAP` 推导。
- `{user_clause}` — "梦里与你同在的人是……"分句，由 `_format_user_clause(user_name)`
  生成；`user_name` 来自 `core.config_loader.get_user_display_name()`
  （读 `config.yaml → user.display_name`），空值时退化为不含具体名字的写法。

新增模板字符串同样禁止写死具体角色名/用户名，一律走上述占位符 + `.format()`；
不要再用 `.replace("叶瑄", char_name)` 式事后补丁（旧补丁只覆盖了调用它的两处，
docstring 与新增模板漏网，已在 Brief 25 §3 P0 收敛掉）。

---

## 层级消融开关（CC 任务 23 · B）

对比 / 消融测试用：可按层临时关闭注入，观察去掉某层后回复质量的变化，不用改代码。

### 机制

- **单一改动点**：不在 40 多个 if 块里各加判断，而是在 `build()` 组装完 `12_user_message` 之后、
  token 估算与裁剪之前，统一按 `_layer` 过滤一遍 `messages`。字符估算、裁剪、`prompt_capture` 快照
  全部反映消融后的真实 prompt。
- **只过滤注入，不短路检索**：`fetch_context()` 的所有检索（episodic/event_log/web_recall 等）照常
  执行，消融只影响 `build_prompt()` 组装阶段。改动面最小，且消融结论一样准——LLM 看到的 prompt 是
  唯一变量。
- **全局开关**（单用户系统），进程内热生效，无需重启；下一轮 `build_prompt()` 调用即生效。
- **fail-open**：开关文件缺失或损坏 → `get_state()` 返回全启用默认值，绝不 raise。

### 存储

`core/data_paths.py::prompt_layer_ablation()` → `data/runtime/prompt_layer_ablation.json`：

```json
{
  "disabled_layers": ["5.5_lore", "6b_event_search"],
  "perception_block_disabled": false,
  "updated_at": "2026-07-04T12:00:00+00:00"
}
```

### `core/prompt_ablation.py`

- `ALWAYS_ON = {"1_system_prompt", "12_user_message"}` — 不可消融，`set_state()` 校验命中即 raise
  `ValueError`（路由层转 422）。
- `get_state()` → `{"disabled_layers": set(), "perception_block_disabled": bool}`；进程内缓存 + 文件
  mtime 失效检查，mtime 未变直接返回缓存。
- `set_state(disabled, perception_block_disabled)` → 原子写（tmp + `os.replace`，经
  `core/safe_write.safe_write_json`），写后立即刷新缓存。

### ALWAYS_ON 与已知联动

- `1_system_prompt` / `12_user_message` 硬编码不可消融——去掉即无角色身份或无用户输入，对话崩坏。
- `6c_episodic_fallback` 的消息 `_layer` 写的是 `6c_episodic`（与 `6c_episodic` 共用层名，便于统一
  裁剪）→ 关闭 `6c_episodic` 会连 fallback 一起关闭，**预期行为**，不是 bug。
- `9_history` 允许关闭（消融场景需要观察"完全没有对话历史"时的行为），但前端会红字警示"关闭短期
  历史将严重改变行为"。
- 破限层（`0_jailbreak`/`2_jailbreak`/`11_jailbreak`）已有 `jailbreak_entries.json` 的 `enabled` 字段
  管理单条目启停；消融开关是第二道闸（双闸设计），两者任一关闭该条目都不会注入。

### perception_block 子开关

`perception_block` 不是独立消息——它嵌在 `1_system_prompt` 的 `{perception_block}` 槽位里
（`core/prompt_builder.py` 约 :363-382），没有自己的 `_layer`，所以不能用统一过滤点处理，需要独立
字段 `perception_block_disabled` 在拼接槽位前判断：

```python
perception = perception_block.strip() if perception_block else ""
if _ab["perception_block_disabled"]:
    perception = ""
```

`_ab`（`get_state()` 的结果）在 `build()` 函数体前部一次性读取，统一过滤点复用同一份结果，避免重复
读文件。

### 快照标注

`core/observe/prompt_capture.py::capture()` 在快照顶层写入 `"ablated_layers": meta.get("ablated_layers", [])`。
被消融的消息已经不在 `messages` 里，所以只做顶层列表展示，不逐层打标——与红色「被裁层」
（`removed_layers`，token 裁剪产生）并列，紫色徽标区分二者语义不同（消融=人工关闭，裁剪=自动降级）。

### API

`admin/routers/settings_misc.py`（scope 均为 `admin`）：

```
GET /prompt-ablation    → {"known_layers": [{"layer","desc"}, ...], "always_on": [...],
                            "disabled_layers": [...], "perception_block_disabled": bool}
PUT /prompt-ablation    body: {"disabled_layers": [...], "perception_block_disabled": bool}
                        → 未知层名或含 ALWAYS_ON 层：422
```

`known_layers` 来源：`core/prompt_builder.py::KNOWN_LAYERS`（`[(层名, 一句话说明), ...]`），覆盖全部
`build()` 中出现的 `_layer` 字面量；`perception_block` 不在此表内，由独立字段表达。

### per-char 合并（Brief 29 · 3.1）

`prompt_ablation.get_state()` 返回的 `disabled_layers` 是**全局开关文件 ∪ 活跃角色卡
`presence_ext.disabled_layers`**。角色卡部分不缓存——每次调用都读当前活跃角色（走
`pipeline_registry`），随角色切换即时生效，无需额外失效逻辑；`ALWAYS_ON`
（`1_system_prompt` / `12_user_message`）对角色卡来源同样生效，不可被 per-char 配置消融。

管理面板「层级开关」页展示/编辑的仍然是全局开关文件那一份，不包含角色卡贡献的部分——
角色卡的 `disabled_layers` 只在角色 JSON 里手改，不经过这个 API。

### 与 tool loop 的 `11.5_tool_nudge` 层的区别

`core/pipeline.py::run_agentic_loop()` 注入的 `11.5_tool_nudge`（工具意愿软提示，见
`docs/tools.md`）**不在这套消融机制的管辖范围内**：它不经过 `prompt_builder.build()`，
只存在于 loop 的一次性 `loop_msgs` 副本里，因此故意不登记进 `KNOWN_LAYERS`——登记了也不会
有任何过滤效果，属于两条独立链路。控制它的开关是 `config.tool_loop.nudge_hint`。

### `11.7_inner_monologue` 层（Brief 32 · 内部思考链，前置独白路线）

`core/thinking.py::maybe_apply()` 在 `llm_client.chat()` / `chat_stream()` 以及
`pipeline.run_agentic_loop()` 进入循环前注入，同样**不经过 `prompt_builder.build()`**，
不登记进 `KNOWN_LAYERS`，与 `11.5_tool_nudge` 是同一类"消融机制管辖范围外"的层。

内容是一次轻量 LLM 调用（`call_category="monologue"`）产出的角色内心活动，以
`（你此刻的内心活动，不要直接复述：{monologue}）` 的 system 消息形式插在 messages
尾部、用户消息之前。只在 `config.thinking.enabled=true` 且解析到 monologue 路线
（`mode: monologue`，或 `mode: auto` 且 chat preset 未声明 `reasoning_native`）时注入；
tool loop 内通过"messages 中已存在该 `_layer` 则跳过"的判断确保多步循环只独白一次
（首步前），不会每步重复调用。

**铁律**：这条内容永不写入 `short_term` history、不广播、不落 `event_log`——它只存在于
当轮调用方持有的 messages 副本里，函数返回后随对象一起被丢弃，下一轮 `build_prompt()`
产出全新的 messages 时不会带着它。

### 前端

管理面板「Prompt 层检视」页新增「层级开关（消融测试）」卡片：勾选即关闭对应层注入，`ALWAYS_ON` 灰
置不可点，`9_history` 行内红字警示；「保存」调 `PUT /prompt-ablation`，成功 toast「已生效，下一轮
对话起作用」。快照总览区 `ablated_layers` 非空时显示紫色徽标「已消融层：…」，与红色「被裁层」并列。

---

## 待评估：Prompt 层配置化 + 面板调参（Phase 2）

**现状**：层顺序、`_drop_priority`、裁剪阈值（15k/20k/18k）均硬编码于 `core/prompt_builder.py`。

**Phase 2 方向**：引入 `prompt_layers` 配置块（`config.yaml`），每层支持 `enabled`/`order`/`drop_priority`/`budget` 可配，再配套管理面板编辑器。

**为什么压后**：改的是每轮生成热路径，改错直接影响所有对话输出；且在 Phase 1 检视器（`GET /observe/prompt-layers/{uid}`）上线前无法判断哪层确实需要调。**先用 round4 检视器观察真实数据 1-2 周，再决定是否值得承担热路径风险。** 届时单开工单，且必须带 `python tests/run_eval.py` 回归。
