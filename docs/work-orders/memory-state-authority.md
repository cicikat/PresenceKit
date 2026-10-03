# 工单批次：状态编排 / 召回兜底 / 解释权收拢 / 遗忘级联 / 隐性状态证据化

> 状态：待施工。行号为写单时快照（2026-10-03），施工前先 Grep 复核；对不上时以函数名为准。
> 每张工单验收后独立提交（见 AGENTS.md）。只暂存本单改动。
> 本批次所有新增状态/trace 都必须 **只记 ID 和指标，不记正文**（与 `event_shadow_recall` 一致）。

## 依赖与并行

| 波次 | 工单 | 说明 |
|---|---|---|
| 第一波（可并行） | S1、S4a、S5a | 三者改动文件不重叠 |
| 第一波（串行） | S1 → S2 | 都改 `core/pipeline.py` 与 recall_trace，S2 在 S1 提交后开工 |
| 第二波 | S3（依赖 S2）、S4b（依赖 S4a）、S5b（依赖 S5a） | S3 也改 `pipeline.py`，需在 S2 之后 |

总原则（每张单都适用）：
1. 新行为一律先有开关和观测，默认值保持现状，除非工单明确写「默认开启」。
2. 新 config 字段同步 `config.example.yaml` 与 `docs/feature-control-surface.md`。
3. send 前路径只允许毫秒级本地计算，不加 LLM/网络调用（AGENTS.md 规则 10）。
4. 新记忆写入点调用 `provenance_log.append()`（规则 6）；新持久 trace 提供只读观测（规则 7）。
5. 路径一律走 `core/sandbox.get_paths()` / `path_resolver.resolve_path(scope, ...)`。

---

## 工单 S1：普通聊天去掉「不看 query 的兜底回忆」，主动回忆保留

### 现象
用户问「Python 为什么报这个错？」，episodic 主检索无命中，但 prompt 里仍出现一条与问题无关的长期/修复记忆（标题【你最近印象深的事】）。角色会顺着这条记忆跑题。

### 根因
- `core/memory/episodic_memory.py:1126-1188` `retrieve_fallback()` 完全不看 query：从 long/repair 桶按 `strength * recency` 挑 top-k。
- `core/pipeline.py:432-451` 每轮都调用它（`top_k=1`），只在 `_skip_recall` 或 `_long_term_query` 时跳过；`core/prompt_builder.py:1053-1079` 在主检索为空时注入，`_report_layer=6c_episodic_fallback`。
- 普通聊天与调度器主动开口共用同一个 `fetch_context()`，没有区分「用户在问问题」和「角色主动想起一件事」。
- provenance 文案 `"(fallback: recent high-strength)"` 已过时（实际选的是 long/repair 桶）。

### 施工要求
1. `fetch_context()` 增加关键字参数 `query_free_fallback: bool = False`。
   - 为 `False` 时不调用 `retrieve_fallback()`，`episodic_fallback_result` 为空。
   - 调度器路径显式传 `True`：`core/scheduler/loop.py` 中 `_pipeline_send`（约 :562）与约 :845 的调用。其余调用点（`main.py:478`、`admin/routers/chat.py:242,633`、`core/stage/views.py:87`）保持默认 `False`。施工时 Grep `fetch_context(` 列出全部调用点，在提交说明里逐个写明取值。
2. 以下「有意图依据」的补位**保留不动**，但要在 trace 里标清来源：
   - `retrieve_mixed(long_term=True)` 的 `long_term_fill`（`episodic_memory.py:1081-1097`）：仅在 `query.relationship_longterm` 命中时触发，属于问题本身要求回顾长期关系。
   - 时间窗兜底（`episodic_memory.py:416-430`）：query 里有「上周/前天」等时间意图。
3. 语义候选没有相似度下限（`episodic_memory.py:402-411`，top-10 向量命中无论距离都进候选池）：
   - 新增 `recall.semantic_min_similarity`，**默认 0.0（不改变现状）**。大于 0 时，相似度低于阈值的纯语义候选（无关键词命中）不进候选池。
   - recall_trace 的 episodic 命中项补记 `sim`（已有就复用）与 `match: keyword|semantic|time|long_term_fill`，供后续用真实分布定阈值。
4. recall_trace 增加 `episodic_fallback_mode`：`disabled_for_chat` / `used` / `empty` / `skipped_low_info` / `skipped_long_term`。观测走现有 `GET /observe/recall/{uid}`，无需新端点。
5. 修正 provenance 文案为 `"(fallback: long/repair high-strength, query-independent)"`。
6. memeval / `tests/run_eval.py` 若依赖层名 `6c_episodic_fallback`（见 `prompt_builder.py:1066-1068` 注释），Grep 确认并同步期望：普通聊天场景期望该层**不出现**。

### 测试
- 复用并按需更新：`tests/test_episodic_fallback_cooldown.py`、`tests/test_recall_trace.py`（:183-222）、`tests/test_fetch_context_lowinfo_gate.py`、`tests/test_relationship_longterm_m5.py`、`tests/test_pipeline_read_scope.py`、`tests/test_episodic_temporal.py`。
- 新增：普通聊天 + 主检索为空 → 不调用 `retrieve_fallback`、prompt 无 `6c_episodic_fallback`；`query_free_fallback=True` → 行为与现状一致；`semantic_min_similarity>0` 时低相似纯语义候选被过滤、关键词命中不受影响。
- `python tests/run_eval.py` 跑一次，提交说明附层激活变化。

### 文档
`docs/memory.md` 召回章节、`docs/prompt-layers.md` 的 6c 段落写明：fallback 仅限主动回忆；保留的意图型补位清单。

### 验收
普通聊天主检索为空时，prompt 中不再出现与 query 无关的 episodic；调度器 `spontaneous_recall` 等主动开口行为不变。

---

## 工单 S2：State Composer / StatePacket 影子模式

### 目标
在不改变 prompt 的前提下，每轮生成一份「这一轮各模块给了什么、为什么给、谁说了算」的结构化清单（StatePacket），用来回答：哪些层与 query 无关、哪些层互相重复、哪些来源完全没有 trace。这是纯编排/观测层，**不是新的记忆库**，不持久化任何记忆正文。第二阶段（不在本单）才考虑由它驱动 `prompt_builder.build()`。

### 现状
- `fetch_context()`（`core/pipeline.py:231-755`）拉取约 20 个来源；`build_prompt()`（:761-883）把它们作为 kwargs 传给 `prompt_builder.build()`。storyline、realtime、inner diary、afterglow 等在 `build()` 内部计算。
- 已有观测：`core/observe/prompt_capture.py`（内存环形缓冲，含每层字符数、drop_priority、`_provenance`）、`core/recall_trace.py`（每轮 JSONL，但**不记录** dossier、relationship_facts、diary、dream、storyline、action_trace）。
- 可直接照抄的影子模式范本：`core/memory/event_shadow_recall.py`（开关 + uid/char 灰度、fail-open、只写 ID 与指标进 recall_trace、`GET /observability/memory-event-shadow-recall`）。

### 施工要求
1. 新建 `core/memory/authority.py`：一个**纯常量**登记表 `LAYER_AUTHORITY: dict[str, dict]`，键为 `_layer` 名（以 `prompt_builder.KNOWN_LAYERS` 为全集，约 :1890-1948），值包含：
   - `source`：来源模块名（如 `episodic`、`memory_dossiers`、`user_identity`、`profile`、`relationship_facts`、`event_log`、`storyline`……）。
   - `authority`：取值固定为以下之一：
     `canonical_evidence`（事件账本）/ `topic_authority`（dossier）/ `identity_authority`（user_identity）/ `user_stated_fact`（profile important_facts、user_facts、pinned_facts、relationship_facts）/ `derived_compat`（episodic、mid_term、event_search）/ `narrative`（storyline）/ `ambient`（realtime、diary、dream、afterglow、coplay、web）/ `character_self`（traits、author note、growth_self、self_agent_md）/ `system`（system prompt、history、tool grounding、user message）。
   - `lineage`：`event_ids` / `days_only` / `none`（据实填写：episodic、mid_term、storyline、dossier 有 event ids；identity 只有 evidence_days；important_facts、relationship_facts 无）。
   - 提供 `authority_of(layer) -> dict`，未登记的层返回 `{"authority": "unregistered"}`。新增测试断言 `KNOWN_LAYERS` 全部已登记（防止以后加层漏登记）。
2. 新建 `core/state_composer.py`，入口 `compose_shadow_packet(ctx: dict, build_result: dict, *, query: str) -> dict`，纯函数、无 IO：
   - 每个激活层一项：`layer`、`source`、`authority`、`chars`、`dropped`（是否被裁剪/消融）、`reason`、`item_ids`（能拿到的 episode id / event id / dossier id，最多 10 个）。
   - `reason` 取值：`query_match` / `time_intent` / `long_term_intent` / `always`（每轮固定注入）/ `tag_gated` / `query_free`（S1 后只应出现在主动回忆轮次）/ `unknown`。来源：recall_trace 已有字段（hop、match）、ctx 中的 `_parsed_time_range`、`_long_term_query`、层的 `_provenance.mode`。
   - 汇总指标：`layers_total`、`query_free_layers`、`duplicate_event_ids`（同一 event id 出现在 ≥2 层的数量，来源于 episodic `source_event_ids`、dossier 引用证据、event_search 命中）、`untraced_sources`（激活了但拿不到任何 item_id 的层名列表）、`by_authority`（各 authority 字符数）、`conflict_candidates`（同一轮同时激活 ≥2 个 `user_stated_fact`/`identity_authority`/`topic_authority` 层时列出层名，**只列不判**）。
3. 接线：`pipeline.build_prompt()` 在 `prompt_capture` 调用处（约 :877-881）之后调用；结果写入当轮 recall_trace 的新键 `state_packet`。
   - 若 recall_trace 写入发生在 build 之前（:665-694），改为在 build 后补写同一条记录，或把 `state_packet` 作为独立 JSONL 写到 `resolve_path(scope, "recall_trace")` 同目录的 `state_packet/{date}.jsonl`——二选一，以改动最小者为准，提交说明写明。
   - 整体 `try/except` fail-open；超过 5ms 记 warning（不中断）。
4. 开关：`state_composer.shadow`（`enabled: false`、`uids: []`、`char_ids: []`），语义与 `event_shadow_recall.enabled_for()` 相同（`core/memory/event_shadow_recall.py:83-96`）。关闭时不计算。
5. 观测：`GET /observability/state-packet?uid&char_id&date&limit`，scope `state.read`，照抄 `admin/routers/observability.py:519-589` 的扫描方式；另给一个聚合视图：最近 N 轮 `query_free_layers`、`duplicate_event_ids`、`untraced_sources` 的出现频次。
6. **不改** `prompt_builder.build()` 的任何输出；加一条测试断言开启影子模式前后 messages 完全相同。

### 测试
新增 `tests/test_state_composer_shadow.py`：登记表全覆盖；reason 推断；重复 event id 计数；开关与灰度；开启前后 prompt 相同；compose 抛异常不影响回复。复用 `tests/test_memory_event_shadow_recall.py` 的结构。

### 文档
`docs/prompt-layers.md` 新增「StatePacket 影子模式」一节，含 authority 分类表；`docs/memory.md` 观测章节加端点；`docs/feature-control-surface.md` 加开关。

### 验收
灰度开启自己的 uid 聊 20 轮后，端点能回答：哪些层与 query 无关、哪些 event 在多层重复、哪些来源无 trace。prompt 与开启前逐字节一致。

---

## 工单 S3：Dossier 解释权收拢（第一步：按证据重叠抑制 + 解释权地图）

### 现状与问题
- dossier 文档自称「当前主题理解的唯一权威」（`docs/character-memory-dossiers.md:9-19`），但同时存在 user_identity、important_facts、user_facts、pinned_facts、relationship_facts、episodic 核心记忆、storyline 等多个对「用户是谁/我们是什么关系」下结论的存储，**跨存储没有任何冲突裁决**。
- 现有唯一的跨存储规则过于粗暴：只要 dossier 有任何文本，`event_search_result`、`episodic_result`、`episodic_fallback_result` **全部清空**（`core/pipeline.py:728,732-733`），而文档写的是只抑制「重叠」内容（`docs/character-memory-dossiers.md:159-163`、`docs/prompt-layers.md:991-999`）。实现与文档不一致。
- dossier 注入没有 config 开关：sqlite 存在就注入（`dossiers.build_recall_context`，`core/memory/dossiers.py:703-731`）。
- `dossiers.search()` 用 `LIKE %整条消息%` 匹配（:593-599），实际几乎只有长期关系问题（空 needle → 最近 3 条）会命中。

本单不做存储迁移，只做三件事：把解释权地图写清楚、让抑制规则与文档一致、补开关。

### 施工要求
1. **解释权地图**：扩写 `docs/character-memory-dossiers.md` 顶部的普查表，覆盖下列全部存储，每行写：持有什么结论、写入者、注入层、lineage 能力（复用 S2 `core/memory/authority.py` 的分类）、与 dossier 的重叠面、冲突时谁优先。
   优先级规则（写进文档并在后续单落实）：
   `canonical_evidence` > 用户本人明确陈述（`user_stated_fact`）> `topic_authority`（dossier）> `identity_authority` > `derived_compat`；`narrative`/`ambient`/`character_self` 不参与事实裁决。
   涉及存储：user_identity、profile important_facts、pinned_facts、user_facts（全局）、relationship_facts、user_relation、episodic（含核心记忆）、mid_term、storyline、memory event store、user_hidden_state、traits/author note。
2. **按证据重叠抑制**：新增 `memory_dossiers.suppression: global | overlap`，**默认 `global`（现状）**。
   - `overlap` 模式：只移除与本轮注入 dossier 引用证据（`occurrence_evidence.source_id`，即 event id）有交集的 episodic 项（按 episode 的 `source_event_ids`）与 event_search 项；无交集的保留。
   - 需要 `build_recall_context` 额外返回本轮引用的 event id 集合（改返回值或新增一个并行函数，保持旧调用兼容）。
   - event_search 命中若拿不到 event id，该来源在 `overlap` 模式下维持现状（整体抑制），并在 trace 中标记 `suppression_fallback: true`。
   - recall_trace 记录 `dossier_suppression`：`mode`、`removed_episode_ids`、`removed_event_ids`、`kept_count`。S2 的 StatePacket 如已上线，`reason` 增加 `suppressed_by_dossier`。
3. **注入开关**：新增 `memory_dossiers.prompt_injection: true`（默认 true，现状）。false 时 `6b_memory_dossiers` 不注入、也不触发抑制；dossier 工具与夜间 worker 不受影响。
4. 修正 `docs/prompt-layers.md:991-999` 描述，使其与实际模式一致。

### 测试
新增 `overlap` 模式：有交集被移除、无交集保留；`global` 模式行为与现状一致；`prompt_injection=false`。复用现有 dossier 相关测试（Grep `build_recall_context`）。

### 验收
`global` 下行为零变化；切 `overlap` 后，dossier 与无关 episodic 可以同时出现。提交说明附一段：建议用 S2 端点观察一周后，再由维护者决定是否把默认切到 `overlap`（本单不切）。

### 后续（不在本单，写进文档 roadmap）
- identity 与 dossier understanding 的冲突检测（S2 `conflict_candidates` 已提供候选）。
- important_facts / relationship_facts 补 lineage 字段。

---

## 工单 S4a：遗忘与墓碑的现成漏洞（S4b 前置）

### 问题
1. **墓碑事件仍可被 dossier 引用**：`core/memory/dossiers.py:287-291` `_validate_events` 与 :762-796 `maintenance_candidates` 不排除已墓碑事件；`event_query.get_event`（`core/memory/event_query.py:99-122`）对墓碑事件仍返回（`tombstoned: true`）。结果：新 occurrence 可以引用用户已删除的事件。
2. **用户要求遗忘的内容被送进 storyline**：`episodic_memory.forget_episodes`（:683-767）在 :737-747 把被遗忘的 episode 送进 `storyline_evicted_input`，周频聚合会把它重新写成叙事弧。遗忘 ≠ 容量淘汰。
3. **janitor 合并丢失 lineage**：`core/scheduler/triggers/memory_janitor.py:206-208` 合并近似重复时只合并 `source_mid_ids`，不合并 `source_event_ids`，被合并掉的那条的证据链断开，S4b 将无法级联。
4. **管理端返回值不实**：`DELETE /memory-events/{event_id}`（`admin/routers/event_memory.py:201-232`）返回 `"derived_memories": "manual_review_required"`，但代码中没有任何地方标记待复核。

### 施工要求
1. `_validate_events` 拒绝墓碑事件（返回明确错误）；`maintenance_candidates` 过滤墓碑事件。
2. `forget_episodes` 不再把被遗忘项送进 `storyline_evicted_input`；容量淘汰路径（`handler_storyline_evicted_input` 的正常来源）不受影响。Grep 确认没有别处依赖该行为。
3. janitor 合并时 `source_event_ids` 取并集去重，provenance 记录合并来源。
4. `DELETE /memory-events/{id}` 返回值暂改为如实描述：`{"derived_memories": {"dossiers": "invalidated", "others": "not_cascaded"}}`；S4b 完成后再改为实际级联摘要。同步 `docs/three-repo-interface-catalog.md` 若该端点在其中登记。

### 测试
每条各一条回归；复用 `tests/` 中 dossier、`forget_episodes`、memory_janitor、event_memory 路由相关测试（Grep 定位）。

### 验收
墓碑事件不可再被 dossier 引用；遗忘的 episode 不再出现在 storyline inbox。

---

## 工单 S4b：删除根事实 → 派生结论失效级联

### 目标
用户删除/墓碑一个事件后，凡是以它为证据推导出的结论，不再进入 prompt 或被当作事实。采用**可逆失效（标记）而非物理删除**，与事件墓碑的设计一致。

### 现状
唯一的级联：`event_store.tombstone_event` → `dossiers.invalidate_source(reason="event_tombstoned")`（`core/memory/event_store.py:724-731`、`dossiers.py:562-585`）。mid_term、episodic、storyline、向量库、identity、important_facts、user_facts 均不级联。
可用的 lineage：mid_term `source_event_ids`；episode `source_event_ids` / `source_mid_ids`；storyline node `source_ids`；event store 边 `derived_from` / `correction_of`（`event_store.py:29,237-250`）；解析器 `core/memory/lineage.py:59-90`。

### 施工要求
1. 新建 `core/memory/invalidation.py`，入口 `invalidate_by_events(scope, event_ids: list[str], *, reason: str, actor: str) -> dict`：
   - **dossier**：调用现有 `dossiers.invalidate_source`（把 `event_store` 里的直接调用改为经此入口）。
   - **mid_term**：`source_event_ids` 与之有交集的条目加 `invalidated: {reason, at, event_ids}`；`format_for_prompt` 跳过。
   - **episodic**：
     - 全部 `source_event_ids` 都已失效 → `status = "invalidated"`（检索、fallback、`9.5_episodic_top` 全部排除），同时删除其向量（复用 `delete_episode` 中的向量清理逻辑，不删 episode 本体）。
     - 部分失效 → 记 `invalid_source_event_ids`，强度上限压到 0.3，不排除；trace 可见。
     - 无 `source_event_ids` 的旧 episode 不处理，计入摘要 `unlinked`。
   - **storyline**：node 的 `source_ids` 与之有交集 → node 标记失效，`list_recallable_arcs` 跳过；arc 全部 node 失效则 arc 不可召回。
   - **event store 派生边**：沿 `derived_from` / `correction_of` 找到的下游事件，只记入摘要（不自动墓碑下游事件，避免误伤）。
   - **无 lineage 的存储**（user_identity、important_facts、user_facts、relationship_facts）：无法自动定位，在摘要中列为 `manual_review`，附「该存储无事件级 lineage」说明。不做模糊匹配删除。
   - 返回摘要：各存储失效数、`partial`、`unlinked`、`manual_review`。
2. **台账**：`resolve_path(scope, "memory_invalidations")` → `memory_invalidations.jsonl`，每次一条（ts、reason、actor、event_ids、摘要，**不含正文**）；在 `path_resolver` 注册新 key，并登记 `docs/data-taxonomy.md`。每个被改动的存储各调用一次 `provenance_log.append()`（`trigger_signal="invalidation:<reason>"`，`source_event_ids` 填入）。
3. **可逆**：提供 `revert_invalidation(scope, invalidation_id)`，按台账恢复上述标记（dossier 的 recompute 不回滚，由 worker 重新计算即可）。管理端暂不暴露 revert，只提供函数和测试。
4. **接线**：
   - `event_store.tombstone_event` 调用 `invalidate_by_events`。
   - `episodic.delete_episode` 与 `forget_episodes`：对 storyline 中引用相同 `source_event_ids` 的 node **不级联**（episode 删除不等于证据删除），只在摘要里提示。本单不改这两条路径的行为。
   - `DELETE /memory-events/{id}` 返回值改为真实摘要（替换 S4a 的临时值）。
5. **观测**：`GET /observability/memory-invalidations?uid&char_id&limit`，scope `memory.read`。

### 测试
新增 `tests/test_memory_invalidation_cascade.py`：全失效/部分失效/无 lineage 三类 episode；mid_term 与 storyline 跳过；向量被清；台账与 provenance 写入；revert 恢复；重复调用幂等。复用 tombstone 与 dossier invalidate 现有测试。

### 文档
`docs/memory.md` 新增「遗忘级联」一节（覆盖范围、可逆性、无 lineage 存储的限制）；`docs/security_model.md` 若有删除/隐私语义章节同步一句。

### 验收
墓碑一条事件后，以它为唯一证据的 episode / mid_term / storyline node 不再进入 prompt（用 `GET /observe/prompt-layers/{uid}` 验证）；台账与端点可见；revert 后恢复。

---

## 工单 S5a：隐性状态写入安全（S5b 前置）

### 问题
1. **丢写**：现实侧写入 `process_reality_turn`（`core/pipeline.py:2119-2138`）在 `uid_lock` 之外执行；12h 衰减 tick（`core/scheduler/triggers/hidden_state_decay.py:104-147`）同样 load→改→save 不加锁。两者并发会互相覆盖。
2. **版本不匹配会抹掉真实数据**：`user_hidden_state.from_dict` 遇到 `schema_version` 不匹配时记 warning 并返回**默认状态**（`core/memory/user_hidden_state.py:720-726`），下一次 save 用默认值覆盖真实文件。S5b 要升 schema，必须先修。
3. `nudge_current_sensitivity`（:468）不受 `MAX_NUDGE_PER_EVENT` 限制，与 `nudge_embodied_ease`（:552）不一致。

### 施工要求
1. 在 `core/memory/locks.py` 的锁池中新增（或复用）按 `(char_id, uid)` 的 hidden_state 锁；`user_hidden_state_store` 提供 `async with hidden_state_lock(scope)` 或同步等价物，所有 load→modify→save 路径（reality signals、decay、consolidate、afterglow、body cue）都在锁内完成。注意 decay tick 遍历所有角色/uid，逐个加锁，不持全局锁。
2. 版本迁移机制：
   - `from_dict` 改为：版本 < 当前 → 依次调用 `_MIGRATIONS[v]` 升级；版本 > 当前（未来版本）→ 抛 `HiddenStateVersionError`。
   - `load_hidden_state` 捕获该异常时返回一个**只读**状态对象（或带 `read_only=True` 标记），`save` 遇到只读拒绝写入并记 error，绝不覆盖文件。
   - 解析失败（损坏 JSON）时同样不得覆盖：先把原文件复制为 `hidden_state.json.corrupt-<ts>` 再回落默认。
3. `nudge_current_sensitivity` 加 `MAX_NUDGE_PER_EVENT` 上限。
4. 修正 `scripts/audit_hidden_state.py:197-198` 中「integrator 无调用者」的过时提示。

### 测试
复用 `tests/test_user_hidden_state_store.py`、`test_user_hidden_state_decay_consolidation.py`、`test_hidden_state_reality_signals_brief88.py`；新增：并发两次写入都保留；未来版本只读且不覆盖；损坏文件被备份。

### 验收
上述三类问题各有回归测试；现有 hidden state 测试全绿。

---

## 工单 S5b：隐性状态证据化（只改真正影响决策的字段）

### 范围
只改会影响 autonomy / prompt / dream 的字段：
- `sensitivity.current`、`touch_need.deficit`：overflow `hidden_need_score`（`core/scheduler/overflow_bucket.py:115-129`）→ autonomy runner（`core/autonomy/runner.py:1310-1336`）、letter_writer `_hidden_state_reason`（`core/scheduler/triggers/letter_writer.py:181-194`）、dream 快照（`user_hidden_state.py:961-1054` → `dream_prompt.py` D4.5、mirror）。
- `sensitivity.baseline`、`touch_need.baseline`：只作分母/偏移，本单只补证据，不加置信度门控。
- `embodied_ease`、`body_memory`：dream-only 分桶，本单不改。

### 现状
每个 `ScalarState` 只有 `value`、`last_updated`、`last_update_source`（:128-138），且 12h 衰减每次都覆盖 `last_update_source=time_decay`，真实来源被抹掉。`IntegratorResult` / `FieldDelta`（:157-188）算出来就丢了。没有任何更新历史。

### 施工要求
1. **schema v2**（依赖 S5a 的迁移机制）：`ScalarState` 新增
   - `evidence: list[dict]`：环形缓冲，最多 12 条，每条 `{at, source, event_type, delta, ref}`；`ref` 为 turn_id / event id / dream session id（能拿到就填，拿不到为空）。**不存原文、不存命中词**。
   - `last_confirmed_at`：最近一次**非衰减、非合并**的更新时间。
   - `last_decay_at`：衰减单独记录，`apply_time_decay` 不再覆盖 `last_update_source` 与 `last_confirmed_at`。
   - v1 → v2 迁移：evidence 为空，`last_confirmed_at` 取原 `last_updated`（若原 source 不是 time_decay/consolidation）否则为空。
2. **写入证据**：`integrate_event`、`integrate_afterglow`、`integrate_body_cue` 把 `FieldDelta` 追加到对应字段的 evidence。`process_reality_turn` 需要把 turn_id 传进来（Grep 确认 pipeline 在 :2119 附近可拿到的标识）。
3. **派生量（读时计算，不持久化）**：在 `user_hidden_state.py` 新增 `scalar_view(state, field) -> dict`：
   - `value`
   - `confidence`：`min(1, n_recent / 5) * exp(-days_since_confirmed / 7)`，`n_recent` 为 14 天内的 evidence 条数；无 `last_confirmed_at` 为 0。常量放模块顶部，写清含义。
   - `evidence_refs`：与当前偏离方向（相对中性值：sensitivity 以 baseline 为中性，deficit 以 0 为中性）同号的 evidence 的 ref。
   - `counterevidence_refs`：反号的 evidence 的 ref。
   - `last_confirmed_at`、`update_source`（最后一条非衰减 evidence 的 source）。
4. **消费端门控（开关，默认关）**：新增 `hidden_state.confidence_gating: false`、`hidden_state.min_confidence: 0.3`。
   - 开启后：`hidden_need_score` 乘以对应字段 confidence，低于 `min_confidence` 视为 0；letter_writer 的 hidden reason 在 confidence 不足时不触发；dream 快照中 confidence 不足的字段输出 `unknown`，`_format_hidden_state_snapshot` 与 mirror 对 `unknown` 不渲染该行。
   - 关闭时行为与现状完全一致，但 autonomy Signal evidence 中同时记录 `hidden_need_raw` 与 `hidden_need_gated`、各字段 confidence，供观察后再决定是否开启。
5. **观测**：`GET /debug/user-hidden-state`（`admin/routers/hidden_state_debug.py:59-128`）增加每个字段的 `scalar_view` 输出与 evidence 列表。

### 测试
复用 hidden state 全部测试（文件清单见 `tests/test_user_hidden_state_*.py`、`test_hidden_state_*.py`、`test_scheduler_overflow.py`、`test_letter_email.py`、`test_dream_mirror_v0.py`）；新增：v1→v2 迁移；衰减不覆盖来源；evidence 环形上限；confidence 计算边界；gating 关闭时 score 与现状相同、开启时低置信归零；dream `unknown` 不渲染。

### 文档
`docs/memory.md` 隐性状态章节写明 v2 字段、confidence 公式与含义（「估计的可信度」而非「强度」）、gating 开关；`docs/feature-control-surface.md` 加开关。

### 验收
debug 端点能看到「这个数为什么是这个值、依据是哪几轮」；gating 关闭时所有消费端行为零变化。
