# Spec #10 — 多角色群聊（Multi-Character Group Chat / Stage）

> 状态：设计中（地基审计 + 方案）
> 难度：高（唯一会击穿地基的项目）
> 最近核对：2026-06-13
> 改动范围：`core/pipeline.py`、`core/memory/short_term.py`、`channels/`（三端协议）、`core/scheduler/loop.py`、`core/character_name_provider.py`，新增 `core/stage/`（Stage/Conversation 实体）

---

## 0. 一句话

存储层（S5/S6 的 `{char_id}/{uid}` 布局、per-char mood/garden/diary/记忆五层）已经为多角色准备好了；**运行时仍是二人世界假设**。群聊不是"加功能"，是把"单活跃角色热切换"换成"N 个角色同时在场"。在没准备好之前贸然接入，会在 short_term 配对、通道协议、调度冷却三处同时炸。

---

## 1. 地基审计（对照上一轮讨论的现状核对）

> 结论：上一轮的架构判断**大体仍然成立**，只有一处已被修掉（import 期角色名冻结）。

| 子项 | 上一轮判断 | 现状（2026-06） | 是否过时 |
|---|---|---|---|
| Pipeline 单活跃角色 | 单例持 `self.character`，靠 `active_prompt_assets.json` 热切换 | `pipeline.py:84 self.character`、`:92 _refresh_character_if_needed`、`:90/:345 _last_channel` 单实例变量 —— 不变 | **仍成立** |
| short_term 二元 schema | `role ∈ {user, assistant}`，turn-group 配对建立在一问一答上 | `short_term.py` entry 仍只有 `role`（无 speaker 维度）；`_group_turns:164` 仍按 user+assistant 邻接配对（或按 `_turn_id` 分组）；`_score_turn_group` / `_sanitize_assistant_message` / `load_for_prompt` 近场加权全建立在 pair 模型上 | **仍成立——最深的一刀** |
| `_char_name()` / import 期 `_CHAR` | 88 处调用、27 文件；`tool_dispatcher`/`user_profile` import 期把名字烤死成模块级常量 | **import 期冻结已消除**：改用 `core/character_name_provider.get_active_char_name()` 运行时从 `pipeline.character` 取。`tool_dispatcher.py:18`、`user_profile.py:13` 已接入。但调用面反而扩大（≈172 处 / 43 文件），且**仍是"单活跃角色"解析**，名字尚未按 speaker 流动 | **部分过时**：地雷已拆，单活跃耦合仍在 |
| 通道协议无发言人字段 | `BaseChannel.send(content, user_id)`、WS `channel_message` 只有 `source` | `channels/base.py` 签名仍是 `send(content, user_id, behavior)`；`desktop_ws` 的 `channel_message`/`message_segments` 无 `char_id`/`speaker` | **仍成立**（防护建议②未做） |
| 调度冷却无 char 维度 | `_COOLDOWNS`/`_last_trigger` 按 trigger 名全局记账 | `loop.py:35/:77` 仍按 trigger 名为 key；`_mark(name)`/`_is_ready(name)` 全局。`_pipeline_send` 已把 `char_id` 透传给 perceive_event，但**冷却记账本身仍 char-blind** | **仍成立** |

**净变化**：唯一真正动过的是 `character_name_provider` 的引入——它把"防护建议①"的前半（杀掉 import 期冻结）做掉了，并给"名字按 scope 流动"留好了唯一接缝。其余四点原样保留。

---

## 2. 设计原则

**不要做成"N 条 pipeline 各自跑然后广播"。** 那会把单活跃假设复制 N 份，short_term/调度/通道的耦合一个都解不掉，还多出 N 倍状态同步。

引入 **Stage / Conversation 作为新的第一类实体**：

1. **花名册（roster）**：当前在场的角色集合（`list[char_id]`）。二人对话 = roster 长度为 1 的退化特例。
2. **回合仲裁器（turn arbiter）**：决定"下一个谁说话"，持有 `conversation_lock`——**一个 stage turn = 一次锁**。复用现有 `core/conversation_gate.conversation_lock(uid)`（已是 per-uid 串行锁），无需新锁原语。
3. **共享 transcript（带 `speaker_id`）**：一份对话流，每条发言标注是谁说的（`user` / `char_id`）。这是群聊唯一的新数据结构。

**记忆按投影喂入（projection）**：每个角色的记忆链（mid_term / episodic / identity）**格式一律不改**。"角色 A 听到了什么 / 记住了什么"是一次从共享 transcript 到 A 的私有视图的**投影计算**。二人对话退化为"投影 = 全量"的特例。这样五层记忆纪律（per-char scope、envelope 准入、fixation pipeline 入链）原样保留，Stage 只负责"谁听到什么"，不碰记忆格式。

> 这套形状照抄你们自己已经验证过的 Dream / Activity 模式：**独立 session 对象 + 受控回流**，而不是在主 pipeline 里内联展开。

---

## 3. 防护性前置（与群聊解耦，现在就能做，纯收益）

这两项不依赖群聊落地，做完群聊做不做都是赚的，且把最贵的协议变更提前摊销。

### 防① 名字按 scope 流动，彻底拆掉"单活跃角色名"耦合

- 接缝已就位：`character_name_provider.get_active_char_name()`。
- 动作：给它加可选 `char_id` 参数（`get_char_name(char_id: str | None = None)`），传入时按指定角色解析，不传时退化为当前活跃角色（向后兼容）。调用点逐步从"隐式活跃角色"迁到"显式 scope.character_id"。
- 收益：群聊里"这句兜底文案/工具描述是谁的"立刻可表达；非群聊时也消除了最后一处单活跃隐式依赖。
- 风险：低。≈172 处调用，但绝大多数当前语义就是"活跃角色"，迁移可分批、默认行为不变。

### 防② 通道消息信封加可选 `char_id`（发言人位）

- 动作：`BaseChannel.send(content, user_id, behavior, *, char_id: str | None = None)`；WS `channel_message` / `message_segments` 增加可选 `char_id` 字段。
- **旧客户端忽略未知字段 → 零成本向后兼容**。先把协议位留出来，不要求前端立刻渲染。
- 触达面：`channels/base.py`、`channels/desktop_ws.py`、`channels/qq.py`、`channels/mobile.py`，以及前端 Emerald-client 的 Rust 层 / mobile 轮询 / QQ 适配三端的反序列化。
- 收益：协议一旦能表达"谁说的"，Stage 落地时就不必再做一次破坏性协议升级。

---

## 4. 分期实现

| 阶段 | 目标 | 关键改动 | 可独立交付 |
|---|---|---|---|
| **P0（前置）** | 拆耦合、留协议位 | 防①（名字按 scope）+ 防②（通道 `char_id` 字段） | ✓（群聊无关也该做） |
| **P1** | short_term 支持发言人 | entry 加 `speaker_id`（assistant 条目标注 char_id）；`_group_turns`/`_score_turn_group` 改为 speaker-aware；二人对话保持默认行为 | ✓ |
| **P2** | Stage/Conversation 实体 | 新增 `core/stage/`：roster + turn arbiter（建于 conversation_lock）+ 共享 transcript；pipeline 不再"单活跃热切换"，由 Stage 持有 N 个角色视图 | 群聊 MVP |
| **P3** | 记忆投影 + 调度多角色 | transcript→各角色记忆链的投影；`_COOLDOWNS`/`_last_trigger` 加 char 维度；跨通道发言人渲染 | 完整群聊 |

**落地顺序铁律**：P1 必须在 P2 之前——schema 不带 speaker 就上 Stage，多个 assistant 进同一 history 会让配对逻辑直接错乱（最深的一刀）。

---

## 5. 不变量（必须守住）

1. **记忆五层格式不改**：Stage 只决定"谁听到什么"（投影），不碰 mid_term/episodic/identity 的写入格式与准入（envelope + fixation pipeline）。
2. **一个 stage turn = 一次 `conversation_lock`**：不引入新锁原语，不在 `run_llm()` 里加 while 循环。
3. **per-char scope 不串味**：投影读写一律经 `MemoryScope.reality_scope(uid, char_id)`（`core/memory/scope.py`），禁止默认桶。
4. **协议向后兼容**：`char_id` 是可选字段，旧客户端忽略；不得做破坏性协议升级。

---

## 附：本 spec 的现状引用锚点

- `core/pipeline.py:84,90,92,345` — 单活跃角色 + `_last_channel`
- `core/memory/short_term.py:150-184`（`_group_turns`）、`:302`（`load_for_prompt`）、`:99`（`_sanitize_assistant_message`）— 二元配对
- `core/character_name_provider.py` — 名字解析唯一接缝（防①）
- `channels/base.py` — `send` 签名（防②）
- `core/scheduler/loop.py:35,77,205-213` — char-blind 冷却
- `core/conversation_gate.py` — turn arbiter 的锁基座
- `core/memory/scope.py:50` — `MemoryScope.reality_scope`（投影读写口）
