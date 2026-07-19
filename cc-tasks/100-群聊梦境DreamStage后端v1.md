# Brief 100 · 群聊梦境（Dream Stage）后端 v1

> 背景：解封 docs/stage.md §五 的 dream fail-closed。方案已评审定案（20260719）。
> 施工顺序：§1 → §2 → §3 串行；§4 可与 §3 并行起步，验收前合流。
> 客户端对应 desktop Brief 38，依赖本单 §3 契约冻结（契约已写死在 §3，可先行照做）。
> 注意：仓库有其他 agent 并行施工，只改本单相关文件；测试失败先判断是否本单引入。

## 0. 已拍板的设计决策（不再讨论）

- v1 仅 sandbox；scenario / mirror / D4.5 在群梦**硬禁用**（scenario 式守卫写法）。
- **零回流**：不写 afterglow / impression / hidden_state / char_relations / mid_term /
  episodic / identity / event_log；出梦只留 archive。
- body_state 全群共享一份（用户身体唯一）；情绪张力 per-char。
- 世界 + lorebook 全局配置，入梦冻结，整场不可切；不做 per-char 世界。
- hard_exit 绝对（Invariant D），v1 无软挽留（多角色挽留让谁开口是泥坑，不碰）。
- Phase R（短反应）/ Phase T（话题引子）在群梦强制关闭。
- 群梦 memory_access 固定 `card_only`，v1 不开放设置。

## 1. 🟡 数据与设置层

- 路径全部经 `core/sandbox.get_paths()`，新增 helper 到 `core/data_paths.py`：

  ```text
  data/runtime/dreams/_stage/{group_id}/
  ├── tmp/current_dream.jsonl        梦内共享 transcript（speaker 前缀）
  ├── archive/dream_*.jsonl          归档，永不进任何 loader
  ├── state/dream_state.json         共享梦境状态
  └── settings.json                  群梦设置
  ```

- `dream_state.json`：共享字段沿用单人 schema（`status / dream_id / dream_started_at /
  frozen_world / context_snapshot(共享部分) / body_state / scene_state /
  symbolic_anchors / flow_entries / emotional_tension 移除`），新增：
  - `char_tension: {char_id: float}` —— 替代单人版单值张力；
  - `per_char_snapshots: {char_id: {...}}` —— 逐角色入梦冻结快照（card_only 内容）；
  - `frozen_relations`（可选）—— arbiter 打分用的关系快照，见 §2。
- `settings.json` schema 与回退链见 §3 PATCH 契约。破限回退链：
  `per_char[char_id].jailbreak_presets → 群级 jailbreak_presets → default.md`。
- 所有产物带 sentinel：`never_retrieve / not_memory_source / reality_boundary: dream_only`。
- `docs/data-taxonomy.md` 登记新目录（canonical / shared / per_group / dream_only）。

## 2. 🟡 dream_runtime + prompt 适配

- 新建 `core/stage/dream_runtime.py` → `run_dream_stage_turn()`：复用
  `store / arbiter / runner`（`run_owner_turn()` 注入 dream 生成 callback），
  **绝不 import** `projection` / `summarize_to_midterm` / fixation 任何入口。
- 新建 `DreamStageCharacterView`（独立类，**不**在 reality `StageCharacterView`
  上长 dream 分支；`views.py` 两处 RuntimeError 保持原样守 reality 侧）。
- `build_dream_prompt()` 复用 + 增量（逐发言角色组装一次）：
  - **D0**：按回退链取 preset 列表，复用现成 `_load_presets_text()`（已支持多预设拼接）。
  - **D1**：发言角色自己的角色卡身份核心。
  - **D2/D3/lorebook**：全局 `frozen_world`；lore 匹配窗口 = 共享 dream transcript 尾部。
  - **新增 DG 层（梦内在场感）**：在场角色名单 + 单侧人称契约扩展
    「只演你自己这一轮，不替其他角色和用户配台词」；层带可观测标识（进 token 统计）。
  - **D4**：该角色自己的 `per_char_snapshots[char_id]`（card_only，入梦冻结）。
  - **D5/D7**：body_state 读共享一份；projection 与张力分桶按 `char_tension[char_id]` 计算。
  - **D9**：共享梦内 transcript（speaker 前缀），不过现实 sanitizer。
  - **D4.5 / DS / DM**：硬守卫禁注入（对齐 scenario 守卫写法）。
- arbiter：打分若需 char_relations，入梦时冻结进 `frozen_relations`，梦中不再读盘；
  梦内 AI↔AI 接话**不更新** char_relations（自产内容不固化，DESIGN.md 决策 3 同源）。
- body_tracker：分析共享 transcript 更新共享 body_state；张力耦合只写
  `char_tension[speaker]`，单轮封顶与梦关即清的既有不变量照搬。
- flow_entries：复用 `dream_flow` 规则产出（模板本就不含角色名）。
- 群设置强制位：`max_reactions=0`、`topic_seed_prob=0`（settings 层写死，不暴露）。
- Hard Rule 9：全程 `char_id` 参数化，不写字面角色名。

## 3. 🟡 端点 + WS + 现实窗锁定（契约冻结，desktop Brief 38 依赖）

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/group/{id}/dream/enter` | 冻结世界/lore/逐角色快照/relations；冲突 409：本群已有活跃梦、owner 单人梦 ACTIVE/CLOSING、本群 reality 轮进行中 |
| `POST` | `/group/{id}/dream/send` | `{content}` → `{round_id, status:"accepted"}`，异步整轮，WS 推送 |
| `POST` | `/group/{id}/dream/exit` | 无条件硬退（Invariant D）；dream-local 状态清空，transcript 转 archive |
| `GET` | `/group/{id}/dream/state` | 对齐单人 `/dream/state` shape；差异：`char_tension` 为映射、新增 `roster`；含 `dream_state/since/expected_end/blocks_chat` 投影（复用 `derive_dream_state_projection` 逻辑，guard 单独调用，语义分离不合并） |
| `GET/PATCH` | `/group/{id}/dream/settings` | 见下方 schema，枚举校验对齐单人 settings；`world_layer` 合法值复用现有并集逻辑 |
| `GET` | `/dream/presets` | **新增**：列出 `characters/dream_presets/` 可用预设名（经 asset registry，供客户端 per-char 选择器；只读，无正文） |

```json
// GET/PATCH /group/{id}/dream/settings
{
  "world_layer": "…",
  "enable_dream_lorebook": true,
  "boundary_level": "…",
  "jailbreak_presets": ["default"],
  "per_char": { "<char_id>": { "jailbreak_presets": ["…"] } }
}
```

- **WS**：复用 `group_round_start/end`、`message_stream_*`、`channel_message`
  （后两者已带 `char_id`/`round_id`）；`group_round_start/end` 增加可选字段
  `domain: "reality" | "dream"`（缺省 reality，旧客户端忽略，零破坏）。
- **现实窗锁定**：群梦 ACTIVE/CLOSING 期间，owner 的 `/desktop/chat`、`/mobile/chat`、
  QQ owner 消息、`POST /group/{id}/send`（reality）全部硬拒（409）；实现上扩展
  `get_reality_guard_status()` 读群梦活跃态，fail-closed 语义与单人梦一致；
  反向同理：群梦活跃时单人 `/dream/enter` 拒绝。
- **scheduler**：确认 `dream_exit` proposer 不因群梦触发（群梦不写单人
  dream_state 的 `last_*` 字段；加断言测试固化）。

## 4. 🟡 测试与守卫（可与 §3 并行起步）

- `test_dream_isolation_guard`：扫描范围加 `core/stage/dream_runtime.py`
  （断言无 projection / summarize / impression / afterglow / hidden_state 引用）
  + 反假绿正样本（断言 reality `runtime.py` 含 projection 引用，证明扫描有效）。
- 隔离契约端到端：整轮群梦后 short_term / mid_term / episodic / identity /
  event_log / char_relations / hidden_state **零变化**；正样本对照：reality stage
  同轮会入 summarize 队列（反假绿铁律）。
- hard_exit：任意状态（含轮次进行中）exit 必成功且状态清空。
- LLM 预算：一轮调用数 ≤ `max_responders + max_ai_chain_depth`（R/T 关闭生效）。
- D0 回退链三档单测：per_char 命中 / 回退群默认 / 双缺失 disabled。
- 无硬编码角色名守卫通过（`tests/test_no_hardcoded_character.py`）。
- 文档同步：`docs/dream.md` 新增「群梦」节、`docs/stage.md` §五 解封说明、
  `AGENTS.md` 速查表补行、`docs/channels.md` WS domain 字段。

## 验收

- 双角色以上群：enter → 多轮 send（Phase A/B 正常仲裁、逐角色 prompt 正确挂各自
  D0/D1/D4）→ exit 全链路通；`GET state` 投影字段齐全。
- 隔离契约测试、hard_exit、预算、回退链、isolation guard 全绿。
- 群梦期间现实回合（单聊/群聊/QQ）硬拒，出梦后恢复。
- `pytest -n auto` smoke 通过；文档同步完成。
