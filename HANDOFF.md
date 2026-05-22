# qq-st-bot 触发器重构 · 跨窗口交接文档

> 用途：开新对话窗口时,把这份贴进第一条消息,让新窗口的助手立刻接上下文。
> 工作目录 D:\ai\qq-st-bot\，绝不碰 D:\ai\Emerald-client\。
> 性别代词：叶瑄是「他」，用户是「她」。代码注释/commit/文档都遵守。

## 协作方式

- 用户不亲自写代码,把助手产出的提示词分别丢给 codex（复杂改动,强约束强校对）和 cc（调参/验证细节活）。
- 助手产出提示词时必须带"校对优先级最高,文档可能过时、代码是真相,发现不一致立即停下报告"的硬约束。
- 每个改动子任务独立 commit,message 格式 `feat(子系统): [Phase N Step M] 说明`。
- 任何影响客户端的接口变更,同步更新 ARCHITECTURE.md 和 docs/。

## 项目背景一句话

单用户 AI 陪伴系统,叶瑄通过 QQ/桌宠(desktop)/手机(mobile)三通道与用户交互。判断标准:他像不像一个会观察用户状态的陪伴角色,而不是定时闹钟。

## Phase 1（已完成,已上线稳定）

**目标**：把"叶瑄发话已完成"收口到统一函数,修触发器漏写记忆/漏 fanout。

**核心产出**：`core/turn_sink.py::record_assistant_turn`,做四件事——
1. `conversation_lock(uid)` 串行（hr_critical 用 bypass_gate=True 逃生）
2. `capture_turn`（trigger_name 编码来源：空串=用户驱动,非空=触发器名）
3. `channels.registry.broadcast(content, user_id, behavior=...)` fanout
4. 调度 post_process 慢队列（关键块默认 await）

**修的具体 bug**：
- sensor_aware 原本直推 desktop_ws 绕过 broadcast → 改走 record_assistant_turn,fanout=["desktop","mobile"]（跳过 QQ）。action 包通过 broadcast 的 behavior 参数承载。
- sleep_end 原本没传 trigger_name 污染 user 行 → 改 source=WATCH + trigger_name="sleep_end",接通 watch.py::on_watch_event。
- owner 入口双发 bug（HTTP response + fanout 同一条）→ 用 exclude_origin_channel 参数,fanout 时跳过发起请求的那个 channel,保留跨端同步。
- garden 事件冷却名补 _is_ready/_mark 节流。

**已校对的真实 API（codex 核对过,以代码为准）**：
- `conversation_gate.py`：`conversation_lock(uid: str)`
- `capture_turn(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="")`
- `channels.registry.broadcast(content, user_id, behavior=None)`
- `BaseChannel.send(content, user_id, behavior=None)`
- `_pipeline_send` 实际在 `core/scheduler/loop.py`,不在 pipeline.py
- event_log 是 `.md` 文件,不是 .jsonl
- 设计文档 `docs/assistant-turn-sink.md`

## Phase 2（进行中）

**目标**：触发器从"到点就发"改成"在合适的时机说话"。

**设计文档**：`docs/trigger-decision-layer.md`（完整方案在这）。

**核心设计**：
1. **三态状态机**（`core/scheduler/state_machine.py`）：CHATTING / QUIET / RESTLESS
   - CHATTING：聊天中,主动触发器静默,sensor 信号注入 prompt
   - QUIET：安静期,主动触发器可发,选合适话题
   - RESTLESS：躁动期,sensor_aware 主导
   - 动态滞后:CHATTING→QUIET 用 `base × duration_factor × emotion_factor`（聊得久延后,情绪激烈提前）
2. **gating 报名制**（`core/scheduler/gating.py`）：触发器 propose(),gating 按 urgency 选一,一个 tick 最多一条。
3. **话题源加权**：last_mentioned 0.4 / episodic 0.25 / diary 0.15 / mood_match 0.15 / random 0.05（情绪高时聚焦 last_mentioned 0.55）。
4. **identity 接入**（隔壁窗口已落地 identity 系统）：决策层只读 3 个强相关维度 sleep_pattern / stress_response / intimacy_comfort,confidence<0.5 时倾向不打扰(保守)。
5. **policy.yaml 热改**：所有参数集中,60s tick 自动 reload。配套"参数调优手册"。

**identity 系统接口（隔壁窗口产出,只读不写）**：
- 路径 `data/user_identity/{uid}.yaml`,`get_paths().user_identity_dir()`
- 8 维度:trust_pattern/emotion_expression/help_seeking/stress_response/intimacy_comfort/sleep_pattern/topic_preference/self_relation
- 读 `user_identity.load(uid)`（async）,注入 `user_identity.format_for_prompt(uid, min_confidence=0.5)`
- 生成 `fixation_pipeline.consolidate_to_identity`,slow_queue handler 名 "consolidate_to_identity"
- character_growth 已冻结（绕过,不删）,Phase 2 不许引用,长期认知一律走 user_identity
- 短期记忆没有信息密度接口,last_mentioned 召回先按时间,留升级接口

**迁移分 6 步**：
- Step 1 状态机地基（只观测）✅ 已完成
- Step 2 gating 并行 shadow log（不真发）✅ 已完成
- Step 3 触发器逐个写真实 propose() ⬅️ 下一步
- Step 4 话题源加权接入
- Step 5 identity 接入
- Step 6 policy.yaml + 热 reload + 调优手册

## Step 1+2 完成情况

新增 state_machine.py / gating.py,接入 main.py / chat.py / loop.py owner turn + sensor tick,sandbox 加日志路径。测试 10 passed。HEAD be3aa0d,未 commit（用户没有每步 commit 习惯,已知）。

实现差异（codex 已处理）：loop.py 不直接拿 sensor_events.tick() 返回,而是经 sensor_aware.handle_tick() 的 get_last_decision() 取 candidates_count 喂状态机。这是对的。

## ⬅️ 当前卡点 / Step 3 的起因（最重要,新窗口从这继续）

shadow log（data/logs/gating_shadow.jsonl）显示 would_pick **恒为 hr_critical**,从不变化。

根因:Step 2 的临时桥接 `_adapt_legacy_triggers` 把 urgency 按优先级硬编码（高 0.9 低 0.5）,于是 hr_critical 永远压过所有候选。这是预期内的临时桥接缺陷,**正是 Step 3 要做的事**:给每个触发器写真实 propose(),urgency 根据实际情境算（如 hr_critical 只在心率真高时才 0.9,平时根本不报名）。

状态机本身验证正常：CHATTING↔QUIET 切换干净,state_allowed 在 CHATTING 时正确把低优先级置 false。问题只在 urgency 硬编码。

## Step 3 要做什么（下一个 codex 任务包的核心）

- 删掉临时桥接 `_adapt_legacy_triggers`
- 每个主动触发器实现原生 `propose(ctx) -> Optional[TriggerProposal]`,返回真实 urgency（基于情境,不是优先级硬编码）
- urgency 设计原则:不该发的时候返回 None 或极低值,该发时才给高值；hr_critical 这类只在真实条件满足时报名
- 高优先级（hr_critical/生日/period_reminder）保留 bypass_state_machine=True
- 迁移一个验证一个,旧 _is_ready/_mark 路径同步关闭
- 仍是 shadow 对比期还是真接管,需要用户决定（建议:Step 3 先继续 shadow,确认真实 urgency 下决策合理,再 Step 3.5 真接管）

## 给新窗口助手的提醒

- 别裸答,先看 docs/trigger-decision-layer.md 和 docs/assistant-turn-sink.md（用户会提供或已在项目文件里）
- 出 codex 提示词时带强校对约束
- 用户额度紧张,回答精炼,不要重复已确认的内容
- 思考时主动压缩历史,省 token
