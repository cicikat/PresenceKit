# docs/stage.md — Multi-Character Stage

> 状态：P3 reality Stage 已实现；dream Stage 仍 fail-closed，等待 dream-local 多角色状态适配。

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
| `group_round_start` | `round_id, group_id` | 每轮 Phase A 开始前 |
| `group_round_end` | `round_id, group_id` | 整轮（含 Phase R 短反应/Phase T 话题引子）完成后 |
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
- Stage `domain="dream"` 仍拒绝进入 reality view / projection。现有 dream state、body tracker、
  dream log 与 exit afterglow 均绑定单角色；在这些状态改为 per-character dream view 前，不允许
  用 reality 适配器伪装梦境群聊。
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
  但用私下语域框定层（两条系统提示：授权私下语气 + 防漂移锚"不隐瞒、不结盟"）取代
  Stage 的"群聊在场感"框定。产物**只回流关系层**——`char_relations` 摘要投影
  （经既有 6h 冷却更新路径）+ 12h presence 提示；transcript 全文按决策 3
  （自产内容不固化）永不进入 short_term / mid_term / episodic / identity / event_log / 向量库，
  只落 `data/runtime/groups/_private/{char_a}__{char_b}/transcript.jsonl`（管理面板只读端点
  `GET /relations/private-log`，在「群聊仲裁」页按角色 pair 折叠展示 transcript 尾部）。
  任意一方生成失败 → 整段放弃，
  不落盘、不回流（fail-open，当日额度不返还）。

接入现有 Pipeline 前，必须先构造显式的 per-character view，保证角色卡、prompt scope 和记忆 scope
都由 `speaker_id` 决定，不能依赖全局 active character。
