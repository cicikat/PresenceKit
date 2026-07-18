# Brief 85 · Stage 群聊互动升级：回波窗口内定向接话 + 轻量续聊 + 关系记忆参与仲裁

> 裁决依据：DESIGN.md §十一 决策 9（群聊互动边界）。核心原则：**所有角色互动发生在
> owner 触发的轮内「回波窗口」（Phase B）**，零后台自发 LLM 调用；互动的丰富来自
> 内容侧（定向接话/短反应/话题引子）而非新增调用轮次。
>
> 业界对照（写单时调研）：AutoGen 系用 GroupChatManager（LLM 选下一个发言人）——每轮
> 多付一次选择调用；近期研究方向反而是**agent 自主 bid want-to-speak**（Murder Mystery
> Agents 的会话规范/邻接对、When2Speak/RESPOND 的参与时机建模）。本项目的纯规则 arbiter
> 正是 bid 模型、零选择成本——**机制不换**，升级全部在打分项与 prompt 内容上。

## 现状（省摸路）

- `core/stage/arbiter.py`：纯规则打分（vocative 0.9 / mention 0.3 / 问句 bonus /
  recency penalty / keywords / talkativeness / PEER_REPLY_BASE 0.4）。
- Phase A（直接回应 owner）+ Phase B（AI 链，`max_ai_chain_depth` 默认 3）已存在——
  「一问N答」的观感来自：接话不定向（prompt 里没有"你在回应谁的哪句"）、无短反应形态、
  角色从不抛新话题、关系不影响谁接谁。
- **每条回复都是全量 pipeline**（`views.py:53-105` fetch_context → build_prompt → run_llm），
  Phase B 续聊同价——这是 token 主要成本点。
- `char_relations.py`：AI↔AI 直接接话后 LLM 更新双向 `{summary, valence}`（6h 冷却），
  只做 presence 提示，arbiter 不读。

## 1. 🟡 Phase B 轻量生成视图（先做，token 闸门）

Phase B 续聊改用**削减 context**：不跑全量 `fetch_context`，只带——角色卡核心层 +
`2.2_stage_presence` + `4.2_stage_transcript`（尾部 ≤12 条）+ 对被回应角色的
char_relation hint + author_note。跳过 episodic/mid_term/diary/lore 检索（续聊回应的是
眼前对话，不是长期记忆场景）。Phase A 保持全量（对 owner 的回应值得全套记忆）。
实现：`StageCharacterView` 加 `lightweight=True` 生成路径。
预期：Phase B 单条 prompt 从 ~15k 字降到 ~4k，链深 3 的一轮总输入成本近似减半。

## 2. 🟡 定向接话（Phase B 内容侧）

- Phase B 生成 prompt 注入定向块：`你在回应 {speaker_name} 刚才那句：「{quote ≤60字}」`
  + 该 pair 的 `char_relations.a_of_b.summary`（有则注，无则略）+ 一句软引导
  「可以直接称呼对方，可以同意、反驳、追问或岔开」。
- transcript 渲染已带说话人前缀，此块是**指向性**补强，不重复贴上下文。

## 3. 🟡 短反应形态（杂音感，token 极小）

arbiter 打分分档消费：`total ≥ speak_threshold` → 正常接话；
`react_threshold ≤ total < speak_threshold` → **短反应**（专用迷你 prompt：角色卡摘要 +
最后 2 条 transcript，要求 ≤15 字的附和/吐槽/一个动作，max_tokens 硬限 ~40）；
低于 react_threshold → 沉默。短反应不占 `max_ai_chain_depth` 名额，但每轮上限 2 条
（settings 新键 `max_reactions`，默认 2，0=关闭）。阈值进 settings，默认从现有分布回测取
（arbiter_trace.jsonl 里有历史分项，CC 可直接统计定初值）。

## 4. 🟡 话题引子（轮末，不跨轮）

轮末条件触发：本轮 Phase A+B 实际发言 < 2 条，或最后一条无问句且无 vocative（对话自然
falls flat）→ 以概率 `topic_seed_prob`（默认 0.25）选**本轮未发言、talkativeness 最高**的
角色抛一个新话头。素材注入（全部现成数据，零新检索）：该角色 `activity_manager` 当前动向、
`scheduler_user_state.followed_topics` 未完结话题、与在场某角色的 `char_relations`
recent 摘要、时间节点/节日。引子也走轻量视图（§1）。**仍在同一轮的回波窗口内**，
不产生轮外调用；引子后不再触发新的 Phase B 链（防递归，`triggered_by="topic_seed"`
不计入 peer-reply 候选源）。

## 5. 🟡 关系记忆参与仲裁 + 定性瞬间

- `char_relations` schema 加 `recent_moments: list[str]`（滚动 ≤5 条，"上次他帮我调琴"
  量级的定性事实）；由**现有** 6h 冷却 LLM 更新顺带产出（同一次调用多要一个字段，
  零新增调用）。schema 向后兼容（缺键按空）。
- arbiter 的 `PEER_REPLY` 项乘以 `(1 + 0.2 × valence)`（valence ∈ [-1,1] → 系数 0.8~1.2）：
  互有好感的角色更爱接对方的话，纯规则、可从 arbiter_trace 观测。关系仍**不决定**
  能否发言（只微调 eagerness），owner↔char 永不入此库（现有铁律不动）。
- `recent_moments` 进 §2 定向块与 presence hint——这就是「角色间内部梗」的沉淀与露出，
  趣味功能的最小实现，不另做系统。

## 6. 🟢 settings 与观测

- 新 settings 键：`max_reactions` / `speak_threshold` / `react_threshold` /
  `topic_seed_prob`，`PATCH /group/{id}/settings` 自然支持；`group_defaults` config 同步。
- arbiter_trace 记录分档结果（speak/react/silent/topic_seed），现有
  `GET /group/{id}/arbiter-trace` 免费获得观测（Hard Rule 7 满足）。

## 明确不做（防 token 洞与失控）

- **后台自发群聊/小剧场**：不做。留 config 位 `stage.idle_theater`（默认 false、
  实现留空），未来若开必须走 ProactiveLedger + 每日上限。
- **LLM speaker selection**（AutoGen manager 式）：不做，纯规则 arbiter 保持。
- **角色间私聊**（owner 不可见的 char↔char 对话）：不做——不可观测的 token 洞 +
  违背「owner 是唯一现实锚点」。
- 群 transcript 直接进个人记忆：不做，摘要投影链（mid_term→fixation）保持现状。

## 验收

- Phase B 轻量视图 prompt 字数显著低于 Phase A（阈值断言）；Phase A 全量不回归。
- 定向块出现在 Phase B prompt（存在性断言）；短反应 ≤15 字上限生效、每轮 ≤ max_reactions。
- 话题引子只在轮末条件下触发、不引发新 Phase B 链（递归防护断言）。
- valence 调制系数范围 [0.8, 1.2]；char_relations 旧文件（无 recent_moments）兼容读。
- 一轮总 LLM 调用数 ≤ max_responders + max_ai_chain_depth + max_reactions + 1（引子），
  硬预算断言。
- `pytest -n auto`；文档：`docs/stage.md`（运行合同 + settings 表 + 边界更新）、
  known-issues 若有相关 observe 项同步。

## Commit 划分

1（§1 轻量视图）→ 2（§2 定向接话）→ 3（§3 短反应）→ 4（§4 话题引子）→
5（§5 关系仲裁 + moments）→ 6（§6 settings/docs）。1 前置于 2-4；5 可与 2-4 并行。
