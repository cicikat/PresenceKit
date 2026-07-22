# docs/stage.md — Multi-Character Stage

> 状态：P3 reality Stage 已实现；Dream Stage（群聊梦境，Brief 100）v1 已实现，见 §六——
> 仅 sandbox 模式，scenario / mirror 硬禁用，零回流，hard_exit 绝对。

Stage 是多角色群聊的一等 session 实体。它持有在场角色、群设置和共享 transcript，并负责在 owner
发言后串行决定谁说话。

---

## 一、当前实现

| 能力 | 文件 |
|---|---|
| Stage / Settings / TranscriptEntry 数据模型 | `core/stage/models.py` |
| Stage 与共享 transcript 持久化 | `core/stage/store.py` |
| 纯规则 want-to-speak 仲裁 | `core/stage/arbiter.py` |
| 一整轮 Phase A + Phase B 编排 | `core/stage/runner.py` |
| per-character 只读生成视图 | `core/stage/views.py` |
| 群在场感 / transcript prompt 渲染 | `core/stage/context.py` |
| reality 群记忆投影 | `core/stage/projection.py` |
| reality Stage 运行入口 | `core/stage/runtime.py` → `run_reality_stage_turn()` |
| 沙盒路径 | `core/data_paths.py` → `stage_group_dir()` / `stage_meta()` / `stage_transcript()` |
| Dream Stage 运行入口（Brief 100，§六） | `core/stage/dream_runtime.py` → `run_dream_stage_turn()` |
| Dream Stage per-character 生成视图 | `core/stage/dream_views.py` → `DreamStageCharacterView`（独立类，不与 reality `StageCharacterView` 共用分支） |
| Dream Stage 共享状态 / transcript / 设置 | `core/stage/dream_state.py` / `core/stage/dream_store.py` / `core/stage/dream_settings.py` |
| Dream Stage 沙盒路径 | `core/data_paths.py` → `dream_group_dir()` 系列（`data/runtime/dreams/_stage/{group_id}/`，与 `stage_group_dir()` 物理隔离） |

持久化布局：

```text
data/runtime/groups/{group_id}/
├── meta.json
├── transcript.json
└── arbiter_trace.jsonl
```

虽然物理路径位于 `runtime/`，两份文件是当前 Stage session 的共享真值，数据治理登记为
`canonical / shared / per_group`。所有路径必须通过 `get_paths()` 获取。

---

## 二、运行合同

`run_owner_turn()` 是 P2 的最小可调用入口。一整轮在同一个回波窗口（Phase B）内跑完
四个阶段，全部同步于一次 `conversation_lock(stage.owner_uid)`，零后台自发调用（Brief 85）：

1. 读取 active Stage，追加 owner 发言。
2. **Phase A**（直接回应）：对未发言角色逐条重算，产生 `min_responders..max_responders`
   条直接回应，走全量 `fetch_context()`——对 owner 的回应值得全套记忆。
   空白或校验失败的生成不计入 responder；若第一轮全部落空而仍未达到 `min_responders`，会按最新
   排名对最佳候选额外重试一次（`triggered_by="user_retry"`），避免瞬态 provider/validator 故障
   静默吞掉一整轮。
3. **Phase B**（自主续聊）：每条后重算，最多产生 `max_ai_chain_depth` 条续聊；使用
   `StageCharacterView` 的**轻量 prompt 视图**（只带角色卡核心层 + 群在场感 + transcript
   尾部 ≤12 条，跳过 episodic/mid_term/diary/lore/history/relation/profile 检索），并注入
   **定向接话块**（"你在回应 {speaker} 刚才那句：「quote」" + 该 pair 的关系印象/往事）。
4. **Phase R**（短反应，可选）：Phase A+B 之后一次性扫描未发言、评分落在
   `[react_threshold, speak_threshold)` 区间的候选，最多 `max_reactions` 条 ≤15 字迷你回应
   （专用迷你 prompt，`max_tokens≈40`），不占用 `max_ai_chain_depth` 名额，也不重新计分链式
   续聊。调用方不传 `generate_reaction` 时此阶段整体跳过（向后兼容）。
5. **Phase T**（话题引子，可选）：轮末条件触发——本轮 Phase A+B 实际发言 <2 条，或最后一条
   无问句且无 vocative——以概率 `topic_seed_prob` 选本轮未发言、talkativeness 最高的角色抛一个
   新话头（素材：`activity_manager` 当前动向、`scheduler_user_state.followed_topics`、
   `char_relations.recent_moments`、当前时间，零新检索）。`triggered_by="topic_seed"`；这是
   本轮的最后一步，不会再触发新的 Phase B 链，天然无递归。
6. 每条有效回复（含 Phase R/T）先追加共享 transcript，再调用可选 delivery callback。

一轮总 LLM 调用数上限 = `max_responders + max_ai_chain_depth + max_reactions + 1`（Phase T
至多一条）——硬预算，`tests/test_stage_85.py::test_round_llm_call_budget_hard_cap` 断言。

生成由调用方提供 `generate_reply(stage, speaker_id, transcript, turn_id, triggered_by)` callback
（Phase A/B/T 共用）；Phase R 走独立可选的 `generate_reaction` callback，签名相同。
两者都可以同步或异步返回文本。delivery callback 同样支持同步或异步。

共享 transcript 每条包含：

```text
speaker_id / content / timestamp / _turn_id / triggered_by
```

`speaker_id` 只能是 `owner` 或 Stage roster 内的 `char_id`。同一 Stage turn 的所有发言共用
`_turn_id`。

---

## 三、P3 Reality 接入

`run_reality_stage_turn()` 串起 P2 runner、per-character view、跨通道发送与回合后投影：

- 每个 `speaker_id` 对应一个缓存的 `StageCharacterView`，显式加载自己的角色卡、LoreEngine 和
  `MemoryScope.reality_scope(owner_uid, speaker_id)`，不热切换全局 active character。
- 角色自己的长期/近期记忆仍由现有 `Pipeline.fetch_context()` 读取；共享 transcript 通过
  `2.2_stage_presence` 与 `4.2_stage_transcript` 两个独立 prompt 层注入。
- Stage 回复通过 `channels.registry.broadcast(..., char_id=speaker_id)` 发送，旧客户端仍可忽略字段。
- 每轮完成后，未投影 transcript 按 roster 逐角色入 `summarize_to_midterm` slow queue；
  输入保留说话人名前缀，且按本段的发言/被点名次数计算 `memory_strength`（0.4–0.9）。
  mid-term 记录仍携带 `source="group:{group_id}"`，后续由原 fixation pipeline 晋升。
- 同轮 AI↔AI 的直接接话会异步更新全局双向关系记录；冷却 6h，同一次 LLM 调用顺带产出
  `recent_moments`（滚动 ≤5 条定性事实，如"上次他帮我调琴"）。关系除了作为 presence 提示与
  定向接话块的素材外，也参与仲裁打分（Brief 85 §5）：arbiter 的 `peer_reply` 项按
  `(1 + 0.2 × valence)`（valence ∈ [-1,1] → 系数 ∈ [0.8,1.2]）调制——互有好感的角色更爱接
  对方的话，但关系**只微调 eagerness，不决定能否发言**；仍绝不存 owner↔角色关系。
- `projection_cursor` 保证投影幂等；transcript 裁剪时游标同步回退，避免漏掉后续新消息。
- scheduler cooldown 支持显式 `char_id` 键。统一执行层双写角色键与旧全局键，旧触发器兼容，
  新的多角色 proposal 可按角色隔离。

## 四、HTTP API（`/group/*`）

挂载于 `admin/admin_server.py`，prefix=`/group`，全部要求 Bearer token 鉴权。

| 方法 | 路径 | 描述 |
|---|---|---|
| `GET` | `/group/list` | 列出所有 Stage 群 |
| `POST` | `/group/create` | 建群（roster + domain + settings）；settings 缺省时从 `group_defaults` config 读 |
| `GET` | `/group/{id}` | 取群详情（roster + settings + 近 50 条 transcript） |
| `DELETE` | `/group/{id}` | 硬删除群 + transcript；正在跑的轮次下轮 load 不到自然结束 |
| `POST` | `/group/{id}/send` | 触发 arbiter 一轮，立即返回 `{round_id, status:"accepted"}`，整轮经 WS 异步推送 |
| `GET` | `/group/{id}/history?before=` | 分页 transcript，`before` 为 Unix 时间戳上界（可选） |
| `GET` | `/group/{id}/arbiter-trace?limit=` | 倒序读取最近仲裁分项与选择记录 |
| `GET` | `/group/{id}/settings` | 读群设置 |
| `PATCH` | `/group/{id}/settings` | 部分更新群设置（min/max_responders 等） |
| `PATCH` | `/group/{id}/roster` | 改群成员（`{roster:[char_id,…]}`）；max_responders 自动夹紧到新 roster 长度 |

`POST /group/{id}/send` 触发 `run_reality_stage_turn()` 作为异步 task，不阻塞 HTTP 返回。

### 群设置字段（`GET`/`PATCH /group/{id}/settings`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `min_responders` / `max_responders` | 1 / 2 | Phase A 直接回应条数区间 |
| `max_ai_chain_depth` | 2 | Phase B 自主续聊上限 |
| `respond_threshold` | 0.5 | Phase A/B "是否接话" 的仲裁分数门槛 |
| `spontaneous_threshold` | 0.7 | 预留（主动群触发未接入，v1 不消费） |
| `addressed_exclusive` | false | 命中 vocative 时是否只留被点名角色候选 |
| `allow_silent_rounds` | true | 允许整轮沉默（仅在消息本身是 backchannel/低信息量时生效，需与 `is_low_information` 同时成立；有实质内容的消息始终触发 `min_responders` 保底） |
| `transcript_limit` | 200 | 共享 transcript 滚动上限 |
| `memory_strength.group` | 0.7 | 群聊摘要投影写 mid_term 时的记忆强度 |
| `debug_token_log` | true | 是否记录每条 Phase prompt 的 token 估算 |
| `talkativeness` / `keywords` | `{}` | 逐角色话痨度 / 话题关键词，供 arbiter 打分 |
| `speak_threshold` | 0.5 | Phase R 分档上界：总分 ≥ 此值走正常接话（非短反应） |
| `react_threshold` | 0.25 | Phase R 分档下界：`[react_threshold, speak_threshold)` 触发短反应 |
| `max_reactions` | 2 | Phase R 每轮短反应条数上限，0 关闭该阶段 |
| `topic_seed_prob` | 0.25 | Phase T 轮末条件满足时，抛新话头的概率 |

`speak_threshold`/`react_threshold` 与 Phase A/B 自身的 `respond_threshold` 是两套独立阈值——
决定"是否说话"仍只看 `respond_threshold`；`speak_threshold`/`react_threshold` 只用来给 Phase A/B
之外、原本会沉默的候选分档决定是否给一句短反应。

### WS 群聊帧（`channels/desktop_ws.py`）

| 帧 | 字段 | 触发时机 |
|---|---|---|
| `group_round_start` | `round_id, group_id, domain?` | 每轮 Phase A 开始前；`domain: "reality"\|"dream"` 可选，缺省 reality（Brief 100） |
| `group_round_end` | `round_id, group_id, domain?` | 整轮（含 Phase R 短反应/Phase T 话题引子）完成后；`domain` 语义同上 |
| `message_stream_start` | `msg_id, char_id?, round_id?` | 每条角色回复的伪流式回放开始（Brief 84） |
| `message_stream_delta` | `msg_id, delta` | 伪流式打字机分块（按标点/句内 2-6 字切块） |
| `message_stream_end` | `msg_id` | 伪流式回放结束，随后是 canonical 替换 |
| `channel_message` | `content, msg_id, char_id, round_id` | 每条角色回复的 canonical 替换（同一 `msg_id`） |

群聊 deliver 路径绕过 `channels.registry.broadcast`：
- **伪流式**（Brief 84）：deliver 先调 `ui_push.pseudo_stream_push(content, msg_id=..., char_id=speaker_id, round_id=...)`
  做打字机回放，复用上面三种 `message_stream_*` 帧；fail-open，不影响下面的 canonical 推送。
- **desktop WS**：直接调用 `push_message(content, msg_id=..., char_id=..., round_id=...)`——`msg_id`
  与伪流式帧共享，供前端替换同一个临时气泡。
- **其他通道**（mobile、QQ）：经 `registry.get_active()` 轮询发送，无 `round_id`（v1 无群聊 UI）。
- **device 通道**：同样经 `registry.get_active()` 发送，但显式传入与伪流式帧相同的 `msg_id`——
  ESP32 firmware（`firmware/presence-device/src/ws_client.cpp`）按 `msg_id` 匹配 `message_stream_*`
  帧与 `channel_message`，两者不一致会导致设备端流式气泡卡在打字状态、永不收口。
  详见 `docs/channels.md` §伪流式。

## 五、当前边界

- Stage 不热切换全局 active character；角色 view 只复用 Pipeline 的读与生成步骤，不调用
  `post_process()`。
- 原始共享 transcript 不写 short_term / mid_term / episodic / identity；只有摘要投影通过
  fixation pipeline 入链。
- Stage 不进入 `perceive_event`；它是由入口显式创建、关闭和驱动的 session。
- Stage `domain="dream"` 仍拒绝进入 reality view / projection（`views.py` 两处 `RuntimeError`、
  `runtime.py` 的 domain 断言）——这条守卫本身不变；群聊梦境走独立的 §六 Dream Stage
  （`core/stage/dream_runtime.py`），不是把 reality 适配器伪装成梦境群聊，两条路径物理分离。
- proactive 群触发与 think-delay 尚未接入；v1 主动群触发按规格保持关闭。Phase T（话题引子）
  不是例外——它仍在 owner 触发的回波窗口内同步产出，config 位 `stage.idle_theater`
  （后台自发群聊/小剧场）默认 false 且未实现，留待未来若开必须走 ProactiveLedger + 每日上限。
- LLM speaker selection（AutoGen manager 式）不做，arbiter 保持纯规则；Phase R/T 的候选选择
  同样是规则打分/talkativeness 排序，没有引入额外的选择调用。
- **角色间私聊（Brief 86，DESIGN.md 决策 9.5 修订版）**：`owner` 不可见的 char↔char 对话
  在受限形态下**可以做**，但不是 Stage session——它走独立的调度触发器
  `core/scheduler/triggers/private_exchange.py`（深夜/闲时窗口，同 `memory_janitor` 时段判断），
  每日 ≤1 对、单次会话 ≤ `max_turns`（默认 6）次轻量 LLM 调用，pair 选择纯规则零 LLM。
  会话生成复用本文档 §Phase B 的 lightweight 视图（`StageCharacterView.generate_private()`），
  但用私下语域框定层取代 Stage 的"群聊在场感"框定：`render_private_presence()`
  产出**身份段 + 语域段**两部分（Brief 106 §1 修订）。身份段在前——
  `你是{viewer}，现在深夜和{other}单独说上了话`，有既有印象则复用
  `char_relations.viewer_summary(viewer, other)` 追加一句"你对 ta 的印象：…"；
  没有这段身份锚点，角色卡里唯一的亲密关系模板是对用户的，模型会把
  "私下+亲昵语域"错套成恋人关系（真机观察到的根因）。语域段是原有的两条
  强制系统提示（授权私下语气 + 防漂移锚"不隐瞒、不结盟"），原样保留。
  产物**只回流关系层**——`char_relations` 摘要投影
  （经既有 6h 冷却更新路径）+ 12h presence 提示；transcript 全文按决策 3
  （自产内容不固化）永不进入 short_term / mid_term / episodic / identity / event_log / 向量库，
  只落 `data/runtime/groups/_private/{char_a}__{char_b}/transcript.jsonl`（管理面板只读端点
  `GET /relations/private-log`，在「群聊仲裁」页按角色 pair 折叠展示 transcript 尾部；
  同页另有一节直接展示 `prompt_capture` 环形缓冲里 `origin.origin=="private_exchange"`
  的原始构建 prompt——按 pair 双方是否都在当前群 roster 内过滤，供核对身份/语域
  注入是否生效，Brief 106 §5）。任意一方生成失败 → 整段放弃，
  不落盘、不回流（fail-open，当日额度不返还）。

  私聊复用 Stage 的 4.2 transcript 槽位与 9_history 短期历史槽位时，两处此前都
  固定按"群聊"措辞注入，角色容易把私下对话或私聊历史误读成群聊场合（Brief 106
  §2/§4）：`build_prompt()` 新增 `stage_transcript_private` 标志（私聊会话调用方
  显式置真），4.2 层头据此从"当前群聊共享对话"换成"你们俩的私下对话（{user}不
  在场）"；9_history 层则仅在**真正的多人 group Stage**（历史非空 且
  `stage_transcript` 非空非私聊）时把 note 从"以下是与用户真实发生的对话"换成
  "以下是你和{user}的私聊历史，不是这场群聊里发生的内容"——私下往来的 history
  恒为空（lightweight_context），不受影响。此外 `generate_private()` 的
  instruction（"接着刚才的话往下聊…"）走 build_prompt 的 user_message 槽位
  （层12），前缀"（旁白指引，不是任何人的发言。）"防止被读成有人在场发话。

接入现有 Pipeline 前，必须先构造显式的 per-character view，保证角色卡、prompt scope 和记忆 scope
都由 `speaker_id` 决定，不能依赖全局 active character。

## 六、Dream Stage · 群聊梦境（Brief 100 v1）

群聊梦境是把单人梦境系统（`docs/dream.md`）的三轴模型（身份/世界/身体）与本文档的多角色
Stage 编排（arbiter/runner/transcript）叠合出的第三种模式——不是"reality Stage 加个梦境开关"，
也不是"单人梦境加多角色"，而是各自的核心机制原样复用、各自的写入路径完全分离。

### 已拍板的 v1 边界（不再讨论）

- 仅 sandbox 模式；scenario / mirror / D4.5（用户隐性状态快照）**硬禁用**（`build_dream_prompt()`
  的 `dream_domain="group"` 守卫，见下）。
- **零回流**：不写 afterglow / impression / hidden_state / char_relations / mid_term / episodic /
  identity / event_log；出梦只留 archive，供人工复盘。
- `body_state` 全群共享一份（用户身体唯一）；情绪张力 `char_tension` per-char。
- 世界 + lorebook 全局配置，入梦冻结，整场不可切；不做 per-char 世界。
- `memory_access` 固定 `card_only`，v1 不开放设置（`admin/routers/group_dream.py::_force_card_only()`
  在 `build_snapshot()` 返回后二次剥离，不信任其按 owner 单人设置选出的档位）。
- `hard_exit` 绝对（Invariant D）；v1 无软挽留——多角色场景下"挽留该由谁开口"没有干净答案，故不做。
- Phase R（短反应）/ Phase T（话题引子）强制关闭：`core.stage.dream_runtime._load_dream_stage()`
  把 `max_reactions` / `topic_seed_prob` 覆盖为 0，不经群设置暴露。

### 架构：两条编排管线共用同一个 runner

`core/stage/runner.py::run_owner_turn()` 是 Phase A/B/R/T 的唯一编排实现，reality 和 dream 共用
同一份算法，不复制。区别只在于它读写哪套存储——Brief 100 把存储层做成四个可注入的关键字参数：

```python
async def run_owner_turn(
    group_id, owner_content, *,
    generate_reply, deliver_reply=None, turn_id=None,
    derived_keywords=None, generate_reaction=None,
    load_stage_fn=load_stage,               # 默认：core.stage.store（reality）
    load_transcript_fn=load_transcript,      # 同上
    append_transcript_fn=append_transcript,  # 同上
    trace_path_fn=_default_trace_path,       # 同上
) -> StageTurnResult
```

- `core/stage/runtime.py::run_reality_stage_turn()`：不传这四个参数，走默认（reality 存储），
  行为与 Brief 85 之前完全一致。
- `core/stage/dream_runtime.py::run_dream_stage_turn()`：注入 dream 专属实现——
  `_load_dream_stage()` 读**已有 reality 群**的 `meta.json`（复用其 roster / owner_uid /
  仲裁调参），投影出 `domain="dream"` 且 `max_reactions=0`/`topic_seed_prob=0` 的视图；
  `load_dream_transcript()` / `append_dream_transcript()`（`core/stage/dream_store.py`）读写
  `data/runtime/dreams/_stage/{group_id}/tmp/current_dream.jsonl`（append-only jsonl，speaker
  前缀，不是 `transcript.json` 数组）；`trace_path_fn` 落 dream 树自己的
  `arbiter_trace.jsonl`，不写进 reality 群目录。
- 收尾职责天然分离：`run_reality_stage_turn()` 在 `run_owner_turn()` 之后调用
  `enqueue_reality_projection()` + `enqueue_relation_updates()`；`run_dream_stage_turn()`
  两者都不调用（也不 import），零回流靠"没接线"，不是靠 if 分支过滤——
  `tests/test_dream_isolation_guard.py` 的反向静态扫描断言
  `core/stage/dream_runtime.py` / `dream_views.py` 不出现这两个符号。

一个群要先经 `POST /group/create`（`domain="reality"`）建群，`POST /group/{id}/dream/enter`
只是在这个已有群上层叠一场梦——梦醒后群依然是同一个 reality 群，`meta.json` 从未被 dream 路径
改写过。

### `DreamStageCharacterView`（`core/stage/dream_views.py`）

独立类，不在 `StageCharacterView` 上加 dream 分支（那两处 `RuntimeError` 保持原样守 reality
侧）。`generate()` 不调用 `Pipeline.fetch_context()` / `build_prompt()`，直接调用
`core.dream.dream_prompt.build_dream_prompt()` + `core.llm_client.chat()`，与单人 dream
pipeline 同构：

- **D0** 破限：`core.dream.dream_pipeline._load_presets_text()` 原样复用；预设名列表由
  `core.stage.dream_settings.resolve_jailbreak_presets()` 按回退链
  `per_char[char_id].jailbreak_presets → 群级 jailbreak_presets → default.md` 解出前两档，
  最后一档（named preset 缺失 → `default.md` → disabled）仍在 `_load_presets_text()` 内部。
- **D1** 身份核心：发言角色自己的角色卡，逐角色独立加载。
- **新增 DG 层**（`_render_dg_layer()`）：紧跟 D1 之后注入，在场角色名单 + 单侧人称契约扩展——
  "只演你自己这一轮，不替其他角色和用户配台词"；`dream_domain != "group"` 时永远不注入。
- **D2/D3/lorebook**：全局 `frozen_world`（入梦时从群设置冻结，整场不可切）；
  `match_dream_lore()` 的匹配窗口是共享 dream transcript 尾部，而不是单人 dream_history。
- **D4** 冻结现实背景：`per_char_snapshots[char_id]`，入梦时逐角色各跑一次
  `core.dream.dream_context.build_snapshot(memory_access=card_only)`，梦中不再刷新。
- **D4.5 / DS / DM**：`build_dream_prompt()` 的 `dream_domain="group"` 守卫硬禁用——
  即使调用方误传 `dream_mode="scenario"` 或塞了 `scenario_core`，也不会注入（对齐 D4.5 原有的
  scenario 式守卫写法，见 `tests/test_dream_stage.py::test_build_dream_prompt_group_domain_hard_disables_scenario_even_if_forced`）。
- **D5/D7** 身体投影与张力：`body_state` 读共享一份，`project_body_for_yexuan()` 按
  该发言角色自己的 `char_tension[char_id]` 单独计算一次——函数本身不变，调用方式从"每轮一次"
  变成"每个发言角色各一次"。
- **D9** 共享梦内 transcript：`_render_group_dream_transcript()` 渲染成单个 speaker 前缀文本块
  折进 system 消息（不是 D9 的逐条 user/assistant messages——多个不同角色的台词没法用
  OpenAI 的 user/assistant 两种 role 表达），且不过 `_sanitize_assistant_message()`
  （现实反话剧化 sanitizer）。

`arbiter.score_candidates()` 本身零写盘、可直接复用；其内部 `_peer_valence()` 在群梦场景下
仍会去读实时 `char_relations`（Brief 100 §2 的"冻结进 frozen_relations，梦中不再读盘"这条
在 v1 落地为：`enter_dream` 把当时的关系快照写入 `dream_state["frozen_relations"]`，供未来
需要时替换 arbiter 的读取源；v1 arbiter 本身尚未接这根线，仲裁打分暂时仍读实时值——纯读不写，
不违反"梦内不回流"，但严格对齐设计稿留作后续工单）。梦内 AI↔AI 接话确定不更新 char_relations，
因为 `run_dream_stage_turn()` 根本不调用 `enqueue_relation_updates()`。

### 张力 / 身体结算（`core/stage/dream_runtime.py::_update_shared_state_after_round()`）

一轮结束后，对本轮 `result.replies` 中每一条按顺序调用
`core.dream.body_tracker.analyze_turn(owner_content, entry.content, body)`，同一份共享
`body` 被依次滚动更新；每个发言角色各自从更新后的共享 body 投影出自己的
`char_tension[char_id]`（`project_body_for_yexuan()`，单轮增量封顶、梦关即清的既有不变量照搬）。
失败仅记警告，绝不让已经发出的回复回滚。

### HTTP 端点（`/group/{id}/dream/*`，`admin/routers/group_dream.py`）

契约已在 Brief 100 §3 冻结，desktop Brief 38 依赖此契约；改动前先读该工单。

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/group/{id}/dream/enter` | 冻结世界/lore/逐角色快照/relations；冲突 409：本群已有活跃梦、owner 单人梦 ACTIVE/CLOSING、`conversation_lock(owner_uid)` 已被占用（视为"本群 reality 轮进行中"） |
| `POST` | `/group/{id}/dream/send` | `{content}` → `{round_id, status:"accepted"}`，异步起 `run_dream_stage_turn()`，WS 推送 |
| `POST` | `/group/{id}/dream/exit` | 无条件硬退（Invariant D）；transcript 归档、dream-local 状态清空、状态直接回 `REALITY_CHAT`（v1 无 afterglow，不经 `REALITY_AFTERGLOW`） |

Dream Stage 每组同一时间只允许一个 in-flight round。运行态会写入 `active_round_id` / `round_status`；第二条发送返回 409 而不是排队并制造第二个 typing 气泡。整轮受运行时 timeout 保护；超时或异常总会发送既有 `group_round_end` 解锁前端，并在状态端点返回 `round_status`（`timed_out` / `failed`）与 `last_round_error`，下一条消息可以正常处理。
| `GET` | `/group/{id}/dream/state` | 对齐单人 `/dream/state` shape；差异：`char_tension` 是 `{char_id: float}` 映射、新增 `roster`；`derive_dream_state_projection()` 与 `get_reality_guard_status()` 分别调用，语义不合并 |
| `GET`/`PATCH` | `/group/{id}/dream/settings` | schema 见 `core/stage/dream_settings.py`；枚举校验对齐单人 `/dream/settings`；`per_char` 的 key 必须是本群 roster 成员 |
| `GET` | `/dream/presets` | 挂在 `admin/routers/dream.py`（不是 `/group/*` 前缀）：列出 `characters/dream_presets/` 经 asset registry 登记的预设 `{id, label}`，供客户端 per-char 选择器；只读，无正文 |

**现实窗互斥（双向）**：`core.dream.dream_state.get_reality_guard_status(uid)` 扩展为除检查
该 uid 的单人 `dream_state.json` 外，还调用
`core.stage.dream_state.has_active_group_dream_for_owner(uid)` 扫描
`data/runtime/dreams/_stage/*/state/dream_state.json`，任一方向活跃都判 `BLOCK_ACTIVE`；反向地，
`core.dream.dream_pipeline.enter_dream()` 在放行前也调用同一个函数拒绝单人入梦。两个方向共用
一个扫描函数，不是各自各写一份判断。

### 隔离守卫

`tests/test_dream_isolation_guard.py` 新增反向扫描：断言
`core/stage/dream_runtime.py` / `dream_views.py` 不出现
`core.stage.projection` / `enqueue_reality_projection` / `summarize_to_midterm` /
`impression_loader` / `afterglow` / `hidden_state` 字样（精确符号而非裸词——`projection`
若用裸词会与合法的 `body_projection` / `project_body_for_yexuan` 冲突，故收窄为精确
标识符）；配反假绿正样本，断言 reality `core/stage/runtime.py` 确实含 `projection` 引用，
证明扫描没有退化成对空集的无效断言。`tests/test_dream_stage.py` 覆盖生命周期（enter 冲突 409
三态、hard_exit 幂等与归档、send 契约）、`memory_access` 强制 card_only、D0 回退链、
prompt 层硬禁用、一轮 LLM 调用数硬上限（= `max_responders + max_ai_chain_depth`，
Phase R/T 恒零贡献）、张力/body 结算，以及端到端隔离断言
（配 `enqueue_reality_projection()` 确实入队 `summarize_to_midterm` 的正样本对照）。
