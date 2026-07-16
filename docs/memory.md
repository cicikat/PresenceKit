# docs/memory.md — 记忆子系统设计

多层记忆并行运作，各司其职，互不替代。

> **P0 写入边界（2026-06-02）**：当前已落地 Write Envelope v0，采用 fail-closed
> 准入。未 stamp 的事件默认不写 memory / mood；`is_test=true` 或 `is_debug=true`
> 强制不可写；sensor / watch 原始感知默认不写 profile。该边界不等于完整权限系统，
> 也不表示 `policy.py` 或完整字段契约已经接入。

---

## 多角色记忆隔离（P0 审计 2026-06-04）

### P0 不变量（已验收，所有测试绿）

以下为 P0 Final Gate 通过后的已落地契约：

| 不变量 | 验收来源 |
|---|---|
| `pipeline.fetch_context()` 所有读路径均透传 `active_character_id` 作为 `char_id` | T-01 / test_pipeline_read_scope.py |
| `pipeline.post_process_critical()` → `capture_turn()` 写 short_term + event_log 均使用 active char_id | T-02 / test_pipeline_write_scope.py |
| `slow_queue` payload 携带入队时的 char_id 快照；handler 透传至各 writer | T-03 / test_slow_queue_char_scope.py |
| `mood_state.update/get_current` 均通过 active char_id 隔离，path 不含 uid | T-04 / test_mood_state_char_scope.py |
| `impression_store` / `distill_impression` 读写均按 char_id 路由 | T-05 / test_impression_char_scope.py |
| `dream_pipeline` 入梦时冻结 `dream_state.char_id`；close/summary/impression/afterglow 均使用 session char_id，不读 active_character | T-05.5 / test_dream_session_char_scope.py |
| `hidden_state_store` / `afterglow_residue` / `integrate_afterglow_and_save` 均按 char_id 路由 | T-06 / test_hidden_state_char_scope.py |
| `_refresh_character_if_needed()` fail-loud：active_character 缺失/空/非法 → 抛 ValueError；character 保持原值；不写 short_term/event_log；不入队 slow_queue；不更新 mood | T-07 / test_active_character_fail_loud.py |
| 内容级隔离 A/B：yexuan 写入内容不出现在 character_b fetch_context 返回值，反之亦然 | P0 Final / test_memory_isolation_p0_final.py |
| 内容级隔离 C：yexuan afterglow 不污染 character_b hidden_state bucket | P0 Final / test_memory_isolation_p0_final.py |
| 内容级隔离 D：入梦 active=yexuan → 切 active=character_b → close → summary/impression 仍写 yexuan 桶 | P0 Final / test_memory_isolation_p0_final.py |

### P0 Final 审计调用点分类（2026-06-04，状态核对 2026-06-11）

| 调用点 | 文件 | 类别 | 说明 |
|---|---|---|---|
| `data_paths.py` 所有方法签名 `char_id: str = DEFAULT_CHAR_ID` | `core/data_paths.py` | **legacy/test 兼容层** | 签名默认值供旧代码 / 测试向后兼容；生产主链路调用方均显式传 char_id。`DEFAULT_CHAR_ID` 是 `_DEFAULT_CHAR_ID`（import 时冻结自 `character.default`）的公开导出别名——Brief 25 §3 P1 起，全仓其余 `char_id: str = "yexuan"` 默认参数已统一改为 `from core.data_paths import DEFAULT_CHAR_ID` + `char_id: str = DEFAULT_CHAR_ID`，不再各自硬编码字面量 |
| `_get_char_id_from_payload` fallback `"yexuan"` | `core/pipeline.py` / `core/memory/fixation_pipeline.py` | **DLQ 兼容层** | 仅在 DLQ 残留任务缺 char_id 时触发，WARN 日志可见，不静默 |
| `mood.py GET /state` fallback `"yexuan"` | `admin/routers/mood.py` | ~~admin/debug~~ **✅ 已修复 P1-0F.2** | `_active_char_id()` fail-loud，不再 fallback yexuan；无写路径 |
| `hidden_state_debug.py` 读 active_char_id | `admin/routers/hidden_state_debug.py` | **admin/debug，可接受** | fail-loud：active 空则 ValueError，不 fallback |
| `admin/routers/memory.py` `short_term.load/clear` | `admin/routers/memory.py` | ~~P1 TODO~~ **✅ 已修复 P1-0C** | `_resolve_char_id()` fail-loud：active 空则 503，char_id 非法则 422 |
| `admin/routers/users.py` `user_profile.load/save` | `admin/routers/users.py` | ~~P1 TODO~~ **✅ 已修复 P1-0E** | `_resolve_char_id()` fail-loud：active 空则 503，char_id 非法则 422 |
| `main.py` `_reply_with_tool_result` 读 short_term + user_profile | `main.py` | ~~P1 TODO~~ **✅ 已修复 P1-0A** | 使用 `frozen_scope.character_id`（N1 scope freeze）；fallback 到 `_active_character_id` |
| `core/garden/manager.py` `get_current()` 无 char_id | `core/garden/manager.py` | ~~P1 TODO~~ **✅ 已修复 P1-0F** | `get_current(char_id=char_id)` 显式传入 |

### 已落地基础设施（P0 后补齐，2026-06-11 核对）

以下曾为 P1 TODO，当前已实现：

- **`MemoryScope` dataclass**（`core/memory/scope.py`）：frozen dataclass，字段 `uid / domain / character_id / world_id`；`__post_init__` fail-loud 校验；`to_payload` / `from_payload` 供慢队列序列化；`require_character_id()` 守卫函数。
- **path_resolver**（`core/memory/path_resolver.py`）：统一路径构造入口；`test_memory_direct_path_lint.py` 覆盖 `user_memory_root(...)` 直调门禁。
- **慢队列 scope payload**：`summarize_to_midterm` 及后续 handler 均在入队时携带 `char_id` 快照，handler 透传至写入层。
- **`user_facts` 全局域拆分**：跨角色通用事实（姓名、生日等）已归入 global scope，不再混在 per-char 的 `user_profile` 内。
- **R3 CI 门禁**（2026-06-11）：`tests/test_r3_scope_lint.py` — core/ 不得新增 `char_id="yexuan"` 函数默认参数或裸 `data/` 路径构造；`tests/test_r3_memory_scope_cleanup_contract.py` — 迁移目标文件仍有违规时通过（文件清理后失败，提示移除 allowlist 条目）；admin/ 不得新增 char_id 默认参数。

### 残余工作（P0 范围外，2026-06-11 核对）

以下问题已知且已隔离，**不属于 P0 blocker**（不产生新串味存储写入）。编号保持原序以便 git blame 追溯。

1. ✅ **CI grep 门禁** — 已落地 `tests/test_r3_scope_lint.py`（R3-CI，2026-06-10）：core/ 不得新增 `char_id="yexuan"` 函数默认参数或裸 `data/` 路径构造；现有违规文件列入 allowlist（含原因注释）。`tests/test_r3_memory_scope_cleanup_contract.py`（R3-cleanup，2026-06-11）追踪 allowlist 清理进度并守卫 admin/ 不引入新违规。

2. ✅ **路径构造门禁** — 已落地 `tests/test_r3_scope_lint.py`（R3-CI，2026-06-10）：覆盖 `Path("data/...")` / `f"data/...` / `"data/" +` 三种模式；`core/data_paths.py`（路径权威）和 `core/dream/scenario_loader.py`（静态 authored-content）列入 allowlist。

3. ✅ **`main.py._reply_with_tool_result` reader bypass** — 已修复（P1-0A）：优先使用 `frozen_scope.character_id`（N1 scope freeze），无 frozen_scope 时 fallback 到 `_active_character_id`；两条路径均显式传 char_id。

4. ✅ **`core/garden/manager.py` mood 读取** — 已修复（P1-0F）：`auto_water_tick()` / `force_water()` 均调用 `get_current(char_id=char_id)`，char_id 由调用方传入。

5. ✅ **`admin/routers/memory.py` char_id 兼容** — 已修复（P1-0C）：`_resolve_char_id()` helper：char_id=None 时读 active_prompt_assets，非法 char_id 返回 422，active 空返回 503；永不 fallback yexuan。

6. ✅ **`admin/routers/users.py` char_id 兼容** — 已修复（P1-0E）：同上，`_resolve_char_id()` fail-loud。

7. ✅ **`hidden_state_decay` 仅处理 active 角色** — 已修复（P1-0G）：`_check_hidden_state_decay()` 遍历 `get_registry().list_all("character")` 所有注册角色，不依赖 active_character。

8. ✅ **`user_facts` global 拆分** — 已完成（P1-4）：跨角色通用事实归入 global scope；`pipeline.fetch_context` / `build_prompt` 已注入。

9. **旧 uid-only 数据迁移**（R3-followup）— 旧 `data/history/{uid}.json`、`data/event_log/{uid}/` 等 legacy 文件未自动迁移至 `data/runtime/memory/{char_id}/{uid}/`。干跑脚本见 `scripts/migrate_uid_only_memory_dry_run.py`；实际迁移待定。

10. ✅ **`dream_state` 物理路径 v1** — `_LAYOUT_DREAM="v1"` 已走 `runtime/dreams/{char_id}/state/...`，legacy 兼容期完成。

11. ✅ **`ShortTermMemory` 类方法默认值** — 已修复（Brief 25 §3 P1）：`ShortTermMemory.load/clear/append/get_history` 及模块级同名函数改为 `char_id: str = DEFAULT_CHAR_ID`（`core/data_paths.DEFAULT_CHAR_ID`），不再硬编码 `"yexuan"`；`test_r3_scope_lint.py` CHAR_ID_DEFAULT_ALLOWLIST 已相应清空（仅剩 `core/data_paths.py` 与 `core/dream/dream_pipeline.py` 的 `enter_dream()` 功能性网关）。

12. **轮级 scope freeze 尚未统一**（R3-followup）— `fetch_context / build_prompt / post_process` 各自接收独立 `char_id` 参数，未在轮入口处构造单一 `MemoryScope` 贯穿全程。极短窗口期内 active_character 切换时各步骤 char_id 可能不一致。建议：在 `Pipeline._run_turn()` 顶端一次性构造 `MemoryScope.reality_scope(uid, active_char_id)` 并向下传递。

> **最近核对**：2026-06-11。第 1–8、10 项已全部落地；第 9、11、12 项为已知 followup，不影响 P0 隔离结论。
> **2026-07-06 更新**：第 11 项已随 Brief 25 §3 P1 落地（`DEFAULT_CHAR_ID` 全仓迁移），CHAR_ID_DEFAULT_ALLOWLIST 从 ~25 个文件缩到仅 `core/data_paths.py` + `core/dream/dream_pipeline.py`。

---

## Reality 输出 Scrub 契约（R6-B，2026-06-10）

> **核心规则：任何写入 short_term / event_log 的现实 assistant 文本必须经过 `scrub_reality_output_text`。**
> 权威 scrub 点是 `capture_turn`（`core/memory/fixation_pipeline.py`）；上游预清洗（main.py、turn_sink）是 defense-in-depth。

| 路径 | 分类 | 处理 |
|---|---|---|
| QQ / desktop / mobile 用户可见输出 | REALITY_VISIBLE | 只用 `strip_render_tags`；**保留**动作描写 |
| short_term / event_log / mid_term / episodic | REALITY_MEMORY | `scrub_reality_output_text`（由 `capture_turn` 权威执行） |
| Dream 模式输出 | DREAM_VISIBLE | 不经过任何 reality scrub |

**不变量（由 `tests/test_r6b_reality_scrub_contract.py` 守卫）：**
- `short_term.append` / `event_log.append` 在 production 代码中只从 `capture_turn` 调用
- Dream 文件不导入 `reality_output_scrubber`
- `scrub_reality_output_text` 幂等（双重 scrub 安全）
- `turn_sink._fanout` 只用 `strip_render_tags`，不用 reality scrub

详见 [docs/assistant-turn-sink.md](assistant-turn-sink.md) §十。

---

## 注入层顺序（P1 骨架，prompt_builder.py）

| 层号 | 名称 | mode | 说明 |
|---|---|---|---|
| 1 | system_prompt | always | 角色系统提示 |
| 1.5 | fact_boundary | always | 事实边界声明 |
| 2 | char_desc / jailbreak | always | 角色描述 + 破限层2 |
| 2.5 | time | always | 当前时间 |
| 2.55 | last_seen | cond | 上次消息间隔（≥6h） |
| 2.6 | activity | cond | 角色此刻活动 |
| 3 | relation | always | 与该用户关系 |
| **3.5** | **period** | **tagged** | 生理期感知（`topic.body`等）；一行 |
| **3.6** | **watch** | **tagged** | 睡眠摘要（`topic.health`等）；一行 |
| **3.7** | **sensor** | **fresh** | 手机传感器今日摘要；一行 |
| **3.8** | **activity** | **tagged** | 桌宠活动快照（`query.what_doing`等）；一行 |
| **3.9** | **screen_awareness** | **tagged/fresh** | 桌面实时感知；一行；drop_priority=25 |
| 4 | group_context | cond | 群聊上下文 |
| 4.2 | stage_transcript | cond | 共享舞台对话；drop_priority=90 |
| 5 | profile | cond | 用户画像（稳定字段 + stable/misc 事实，100% 注入） |
| 5_profile_pref | profile_pref | cond | 偏好/习惯类事实（pref.\*/habit/health tag），recency 90天门控或 tag 命中时注入 |
| 5.1 | user_facts | cond | 跨角色客观信息 |
| 5.2 | reminders | cond | 待办备忘 |
| 5.5 | lore | cond | 世界书；drop_priority=80 |
| 6a | user_identity | cond | 用户稳定行为模式 |
| 6b | event_search | cond | 相关往事；drop_priority=30 |
| 6c | episodic | cond | 情景记忆；drop_priority=70 |
| mid_term | mid_term | cond | 中期记忆；drop_priority=40 |
| 6d | diary_context | cond | 用户日记摘要；drop_priority=50 |
| 6e | inner_diary | cond | 角色内心独白；drop_priority=60 |
| 6f | dream_afterglow | cond | 梦境余韵详情 |
| 6g | dream_impression | cond | 梦境印象 |
| 7 | mes_example | cond | 对话示例 |
| 8 | perception | cond | 感知块 |
| 9 | history | always | 短期对话历史 |
| 10 | tool_result | cond | 工具结果 |
| 11 | author_note + jailbreak | always | 作者注释 + 破限层11 |
| 11.5 | post_history | cond | 角色卡 post-history 约束 |
| **11.7** | **pinned_facts** | **cond** | **用户特意强调的高价值事实（不可裁，无 drop_priority）** |
| 12 | time_hint + user_message | always | 时间提示 + 用户消息 |

> 层 3.5–3.9 归拢于层 3 之后、层 4 之前（P1 骨架，2026-06-26）。
> 层 11.7 schema：`profile["pinned_facts"] = [{text, ts, source}]`，`source ∈ {"manual","auto"}`；写入接口待 G3/观察链补。
> 所有 system 层注入前经 `_normalize_injection` 统一称呼清洗（P5 已落地）：正文中的指代性「用户/user」→「她」，`<...>` 标签名原样保留，不触碰真实对话（history role）。

## 记忆层一览

| 记忆类型 | 文件（S6 新路径） | 更新时机 | prompt 位置 |
|---|---|---|---|
| 短期历史 | `data/runtime/memory/{char_id}/{uid}/history.json` | 每轮实时写 | 层9 |
| 事件流水账 | `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md` | 每轮实时写 | 层6b（搜索后） |
| 中期记忆 | `data/runtime/memory/{char_id}/{uid}/mid_term.json` | 每轮慢队列压缩 | `mid_term` |
| 情景记忆 | `data/runtime/memory/{char_id}/{uid}/episodic.json` | mid_term 显著情绪 eager 晋升，或 sweep 老化晋升 | 层6c |
| 用户稳定行为模式 | `data/runtime/memory/{char_id}/{uid}/identity.yaml` | fixation pipeline 达阈值后固化更新 | 层6a |
| 情绪状态 | `data/runtime/characters/{char_id}/inner/mood_state.json` | 每轮 post_process_slow（send 后异步）/ 工具触发 / 深夜调度（post_process_critical） | 层1内嵌软提示 |
| 用户隐性状态（Phase 6） | `data/runtime/memory/{char_id}/{uid}/hidden_state.json` | Reality-side integrator + WriteEnvelope；调度器 decay/consolidate tick；Dream exit afterglow 已接线（Phase 6：`wire_afterglow_from_summary()`） | Dream D4.5 tag-gated bucket 只读快照（body_intimate / physical_closeness；不含 float） |
| Afterglow 残差（Phase 6/7） | `data/runtime/memory/{char_id}/{uid}/afterglow_residue.json` | Dream exit 时 `wire_afterglow_from_summary()` 写入（`core/dream/dream_exit_afterglow.py`）；8h TTL | Phase 6 数值层：由 `integrate_afterglow_and_save()` 消费后影响 sensitivity.current / embodied_ease；Reality 文本层先由 summary 提供 0–5h 的 `6f_dream_afterglow`，随后由 residue 提供至 8h 的 `dream_afterglow_soft_hint`；两层互斥、只读、非现实事实；Dream 无直接写权限 |
| 梦境预构种子 | `data/runtime/memory/{char_id}/{uid}/dream_seed.json` | Dream Seed activity close 后写入；12h TTL；下一次入梦一次性消费 | 仅拼入 Dream `context_snapshot.entry_reason`，不进入 Reality prompt / 主记忆链 |
| episodic 淘汰归档（时期摘要） | `data/runtime/memory/{char_id}/{uid}/memory_digest.md` | episodic 上限裁剪淘汰批次时追加（`digest_evicted_episodes` slow_queue job，Brief 46 §1） | 不注入 prompt（v1 只归档观察） |

> **当前 v1 写布局**：per-user 主链统一写入 `get_paths().user_memory_root()`，即
> `data/runtime/memory/{char_id}/{uid}/`。迁移期 `for_read(new, old)` 仍保留在 event_log
> 相关读取；event_log 还保留近 30 天 union 读。其余主记忆 loader 已直接读新路径。

> **character_growth 已整体删除（Brief 35）**：
> `core/memory/character_growth.py` 模块与 `get_growth` 工具已于 Brief 35 一并删除——
> grep 确认 `character_growth.load()` 唯一读者就是 `get_growth` 工具本身，零其他读者。
> 当前长期认知写链是 `consolidate_to_identity`（→ `identity.yaml`）和 `trait_tracker_update` slow_queue task，
> 与本次删除无关（该写链早在 R8-E2 就已完全接管）。
> 磁盘上历史遗留的 `data/runtime/characters/{char_id}/character_growth/角色_{uid}.md` 文件
> 不会被自动清理，也不再被任何工具读取；`core/data_paths.py::DataPaths.character_growth()`
> 与 `core/memory/path_resolver.py` 的 `LEGACY_ARTIFACTS` 只读路径解析为审计/迁移脚本
> （`scripts/migrate_data_v1.py`）保留，**不要**在新代码里以此为由重新引入读写调用。

---

## 一、短期历史（short_term）

**文件**：`core/memory/short_term.py`

**存什么**：短期对话历史，不含工具结果。每条 entry 保留 OpenAI 兼容 `role`
以及真实发言人 `speaker_id`（owner 或 char_id）；单聊仍自然表现为 user/assistant 交替。磁盘保留上限优先读
`memory.short_term_disk_rounds`，没有则回退 `memory.short_term_rounds`。

**读写**：
- 写：`post_process_critical`（send 前的关键段）内，每轮 `short_term.append()`
- 读：`fetch_context` 里 `short_term.load_for_prompt()`，按 prompt 预算选择后传入层9

### prompt 选择策略（load_for_prompt）

当历史组数不超过 `memory.short_term_rounds` 时全部注入；超过时不是简单截尾：

- 先按 `_turn_id` 把同一轮所有发言人绑成 turn-group，旧数据回退到相邻 user+assistant 分组
- 固定保留最近 `NEAR_K=5` 组，保障近场连续性
- 更早的组按长度、实体、问句、数字/日期、tag 命中、情绪 tag 打分，择优补足预算
- `_ready_signal_bonus()` 目前固定 0，预留给未来按 turn_id join mid_term/episodic 就绪状态
- debug logger：`short_term_weight` 会记录每组分数和 selected 状态

### Reality 输出出口闸（scrub_reality_output_text）

`core/reality_output_scrubber.py` — 现实 Chat 的统一出口清洗器，作用于所有出口：
- `capture_turn` 写 short_term 前 + 写 event_log 前（不再保留原始动作描写）
- `turn_sink.record_assistant_turn` 的 memory_text 传 post_process_critical 前
- `_fanout` 向所有通道（含 desktop channel_message）发送前
- `admin/routers/chat.py` 的 HTTP 响应 reply 字段返回前
- `main.py` QQ 段落发送前

**清洗规则**（逐行，代码块豁免）：整行 `（…）`/`(…)` 删；整行 `*…*`/`_…_`/`> …` 删；以
他/她/动作/沉默/停顿/视线/目光/呼吸/微微/缓缓/轻轻/慢慢 开头的行删；含 抬起/低头/靠近/
尾巴/扫过/看你一眼/守着/趴/蹭/贴近/伸手/垂眸/眯眼/摸/抱(非抱歉/抱怨/抱负) 的行删。

**Segment 路径**：有 segments 时只保留 `"type": "say"` 文本，再走行级过滤。

清洗后为空 → 返回 `None`：写 short_term/event_log 时跳过该行；返回给前端时 fallback `"我在。"`。

**Dream 隔离**：`dream_pipeline.py` 永不调用此模块。Dream 的 do/feel/env/segment 渲染协议完全不受影响。

---

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

裁剪单位是 turn-group，不是 entry。按 _turn_id 分组：单聊正常对 = 同 id 的 {owner, char_id}；多人 turn 可包含同 id 的 {owner, char_a, char_b...}；触发消息（他主动发话）= 单条 {assistant}，是正常分支不是特例；legacy 无 _turn_id 的行退回按 role 序列贪心配对。整组增删，绝不拆出孤儿发言。同一 _turn_id 非相邻重复出现会记 warning 再按相邻兜底，不静默合并。
流程（load_for_prompt）：load()（已 sanitize）→ **入口剔除 `_source=="trigger_stub"` 条目** → _group_turns → 近场最后 NEAR_K(=5) 组无条件保留 → 远场逐组 _score_turn_group 打分、按总分降序填满剩余预算 → 选中集按原始时间顺序重排 flatten。len(groups) <= budget 时走快路径原样返回。trigger_stub（系统触发锚点，内容含内部 trigger_name 明文如 `[触发: sensor_aware]`）此前仅靠 _score_turn_group 评 0 分淘汰，但近场无条件保留与 ≤budget 快路径都绕过打分，导致触发器名被当成用户消息投影进 prompt、被 LLM 复述泄露；故在 load_for_prompt 入口统一过滤（覆盖三条路径），prompt_builder 层 9 另加防御性跳过。磁盘上的 stub 仍保留供血缘，get_history 不受影响。权重必须算在 sanitize 之后，否则话剧腔长动作描写会因"长度长"反得高分——这一步顺带淘汰话剧腔轮次，不用单独写规则。
打分信号（全部命名常量 + 独立打分函数，可解释，无魔数）：长度、具体名词/实体（结构化后缀类别 + 大写串，不枚举具体专名）、问句、数字/日期、tag_rules 命中、情绪（复用 get_tags 的 emotion.* 命中，不自建情绪词表）、assistant 发言人多样性。emotion.* 命中有意双算（既进 tag 分又进 emotion 分，情绪轮次双重重要），故单组总分 clamp 到 TURN_SCORE_CAP(=5.0) 防多信号叠加碾压；clamp 只截 total，parts 分项保真供观测。
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

**写入顺序**（Brief 37 之后，注意有先后）：
1. `post_process_critical`（send 前的关键段，只做毫秒级本地落盘）直接调用
   `fixation_pipeline.capture_turn(uid, ..., emotion="neutral", char_id=_active_character_id)`——
   此时 `detect_emotion()` 还没跑完，`emotion` 传的是占位值 `"neutral"`，事后不会
   回写修正。这是有意的取舍：event_log 的 `emotion` 字段只是标注，不参与任何下游
   判断（mid_term eager reflect 消费的是 `post_process_slow` 里 detect 出的真实
   emotion，不读这份占位值）。写入规则按 envelope 区分：
   - **用户对话**（`stamp_user_chat()`）：写 short_term user + assistant 两行，写 event_log user + assistant 两行（assistant 行含 emotion，所有行含 turn_id）。
   - **调度器触发器**（`stamp_trigger()`）：**不写 short_term**（trigger 不是用户说过的话，不得进入 history/prompt 上下文）；写 event_log assistant 行；另外追加 `trigger_audit_log` 一条（metadata + SHA256[:16] reply hash，不含完整回复文本）至 `data/event_log/{uid}/trigger_audit.jsonl`。
   - `envelope.can_write_memory=False`（默认 WriteEnvelope）：所有写入均跳过，用于无副作用 dry-run。
   - `char_id` 由 `post_process_critical` 显式传入，决定写入哪个角色桶。
2. send（channel fanout）完成后，`post_process_slow` 才异步跑 `detect_emotion()`
   并更新 `mood_state`（真实 emotion 只进 mood_state / mid_term，不回写 event_log）。

**写入格式（P1-1 speaker 字段化）**：每个对话块的各行之后加说话人元行：
- 用户行：`> speaker:user [turn_id:{turn_id}]`（turn_id 可选，speaker:user 恒写）
- 助手行：`> emotion:{emotion} intensity:{n} speaker:assistant [turn_id:{turn_id}] [trigger:{name}]`

**搜索**：`event_log.search(user_id, content, llm_client)` 异步执行，返回按行排列的第三人称事实卡。每张卡带明确的说话人归属和粗粒度时间标签，格式如下：

`search()` / `get_recent_days()` 均接受可选 `since_ts` / `until_ts`（Brief 48，查询侧
时间意图，`pipeline.fetch_context()` 解析出时间范围时透传）：非 None 时 `get_recent_days`
不再看 `days` 参数，只扫 `[since_ts, until_ts)` 范围内的日文件，顺带省 IO；块内容过滤/
评分逻辑不变。

```
（今天）她提到：最近睡不好
（前几天）叶瑄当时说：你要注意休息
```

**搜索评分**：
```
decay = 1 / (days_ago + 1)
score = intensity * decay + relevance
```

- 7 天外且 intensity < 1 的块直接跳过。
- 每行**按说话人单独成卡**（P0-1/P1-1），不跨说话人拼接、不截断在词中间。
  - **新格式 block**（含 `speaker:` 元字段）：按段归属——先收集正文行，遇到 `> speaker:X` 时将该段正文归属给 X；`>` 行全部跳过，不进召回正文。
  - **旧格式 block**（无 speaker 元行）：回退前缀匹配（P0-1 行为），跳过所有 `>` 行。
- 60 字以上按句末标点截断；无标点时加"…"。
- 时间粗粒度：今天 / 昨天 / 前几天 / 约N天前。
- 最多取前 5 张卡，`MIN_SCORE = 0.6`，低于阈值不注入。

**投毒防护（P0-2）**：assistant 回复写入 short_term/event_log 前，`reality_output_scrubber` 会删除以说话人标签开头的整行（如 `用户：…` / `叶瑄：…`），防止模型自写对白落库后被 search 误认为用户发言。

`get_highlights(user_id, days, max_lines)` 是独立函数，
从最近 N 天日志里提取有情感词的用户发言，供调度器碎碎念触发时参考，不走搜索路径。

**过期前抢救（Brief 46 §2）**：按天日文件超过 `day_archive_days`（默认 30 天）会被
`cleanup_event_log()` gzip 归档、退出 30 天搜索窗口——里面可能仍夹带"计划/承诺/生活
变化"这类低情绪但持久的信息，只靠 mid_term eager（sad/angry/happy）和 episodic_sweep
两条晋升路会漏掉。调度器 `event_log_salvage`（`core/scheduler/triggers/event_log_salvage.py`，
冷却 24h）在归档前的 age∈[27,29] 天窗口扫描尚未抢救的日文件，每文件一次 LLM 调用提取
"仍然为真的持久事实"（排除一次性事件/情绪表达），产出走下方 important_facts 冲突裁决
入口（`_apply_important_facts_ops`，op=add/update/noop）——不新建存储，已被 profile
覆盖的同义信息会被 noop 掉。每次调度 tick 最多处理 3 个到期文件（跨全部角色/用户合计，
防积压时一次打爆 LLM 配额）；已处理日期记 `fixation_state.json` 的 `salvaged_dates`
（滚动保留 60 个）。

**legacy 用途**：`character_growth.update()` 已于 R8-E2 删除；模块与 `get_growth` 工具本体已于 Brief 35 整体删除。

---

## 三、情景记忆（episodic_memory）

**文件**：`core/memory/episodic_memory.py`

### 数据结构

每条记忆的字段：

```json
{
  "id": "ep_1234567890",
  "timestamp": 1234567890.0,
  "occurred_at": 1234500000.0,
  "raw_facts": ["用户提到最近失眠严重", "用户说'睡不着'", "用户表达了疲惫"],
  "topic_keywords": ["失眠", "深夜", "陪伴"],
  "emotion_peak": "gentle",
  "emotion_texture": "像是被什么东西轻轻压着，说不清是担心还是不舍",
  "emotion_arc": "从担心到平静",
  "user_state": "tired_and_struggling",
  "narrative_summary": "用户说最近失眠严重，他陪他聊到很晚",
  "temporal_ref": "none",
  "strength": 0.85,
  "is_core": false,
  "status": "open",
  "resolved_at": null,
  "resolved_by": null,
  "retrieval_count": 2,
  "last_retrieved": 1234567890.0,
  "source_mid_ids": ["mt_123_1748000000000"],
  "consolidated_at": null
}
```

字段语义（P0-3 双时间戳）：

| 字段 | 语义 |
|---|---|
| `timestamp` | **recorded_at**：这条记忆被反思/写入的时刻。decay / index / MMR 排序用此字段。 |
| `occurred_at` | **事件真实发生时刻**的最佳估计。`format_for_prompt()` 渲染时间锚（"刚刚" / "N个月前"）一律读此字段；旧数据缺失时回退 `timestamp`。 |
| `temporal_ref` | `"future"` / `"past"` / `"none"`。`"past"` 时渲染层禁用刚刚/几小时前/今天等近期锚点，改为"之前" / "前几天" / "N个月前"，防止回顾型提及被渲染成刚发生的事。 |
| `event_time` | **未来**事件的预定时刻（TTL/expires_at 用）。与 `occurred_at` 含义不同，勿混用。 |

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
- is_closure：本批摘要是否明确完成、取消或更新了先前事件（bool）
- closure_keywords：完结事件所对应的关键词列表；非完结事件为空数组
- strength：0到1浮点数

晋升时，`reflect_to_episodic()`：
- 用 LLM 把一批 mid_term 摘要反思成一条记忆（JSON 格式）
- 若 LLM 标记 `is_closure=true`，先用 `closure_keywords` 关闭近 72 小时内匹配的非核心 open 记忆
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

**去重（P1-3 双防线）**：
1. **血缘 exact-dup**（全量扫）：新 episode 的 `source_mid_ids` 与**任意**存量 episode 有重叠 → 跳过；血缘精确，不漏不误。
2. **文本近似**（近场）：与最近 10 条做 `narrative_summary` / legacy `summary` 相似度检查，相似则跳过；覆盖"同一真实事件被新轮次重新提及"的血缘不同情形。
两道互补，不互相替代；血缘去重在前，文本去重在后。`source_mid_ids` 为空时跳过血缘检查，仅走文本近似。

**上限**：最多 200 条，超过时只从非核心记忆中按 strength 排序删掉最低的 20 条；
`is_core=True` 不参与自动上限裁剪。

**遗忘=降级而非删除（Brief 46 §1）**：被裁的 20 条不是直接丢弃——`write_episode` 会先把
条目全文快照 + `char_id` 入队 slow_queue 任务 `digest_evicted_episodes`，再从
episodic.json 删除。handler（`core/memory/fixation_pipeline.py::digest_evicted_episodes`）
一次 LLM 调用把这批条目压成 5-8 行"时期摘要"（保留时间跨度、反复出现的主题、用户状态
变化），追加写入 `data/runtime/memory/{char_id}/{uid}/memory_digest.md`（`memory_digest`
artifact key，path_resolver 已注册），每次追加带日期头 + 来源 ep_id 列表（血缘可追溯），
并记一条 `provenance_log.append(artifact="episodic", trigger_signal="evict_digest")`。
LLM 失败时原文以紧凑 JSON 追加到同文件的 `<!-- raw -->` 区块兜底，不丢数据，不重试
（fail-open——这里没有可重新触发的上游事件，与其余 fixation job 的重试策略不同）。
v1 **不**注入 prompt（`prompt_builder.py` 未接入），只归档观察，待内容质量观察 2-4 周
后再决定是否作为低优先级层接入。

### 事件完结 / 状态

新写入的 episode 默认 `status="open"`，并带有空的 `resolved_at` / `resolved_by`。
完结型 reflect 会在 neutral skip 之前执行关闭逻辑，因此“吃完了”“考完了”“不去了”
这类中性、低强度摘要即使自身不写入 episodic，也能关闭对应旧事件。

自动关闭只匹配近 72 小时内、非核心、尚未 resolved 的记忆；匹配文本来自
`topic_keywords`（兼容旧 `tags`）和 `raw_facts`。命中后写入完成时间与关闭来源 episode id，
并将 strength 压至不高于 0.2。核心记忆不会被自动关闭。

### 前瞻事件 TTL / 时间分辨率

reflect 输出可选 `temporal_ref`（future/past/none）与 `event_time_hint`。系统用本地时区
保守解析“明天 / 后天 / N 天后 / 这周末 / 下周末 / 下周 X / 常见具体日期”；解析失败不报错，
episode 的 `event_time` / `expires_at` 保持 `None`。可解析的 future 事件以
`event_time + 1 天` 作为 TTL。

召回时，超过 `expires_at` 的事件即时按 0.3 系数降权，无需等待用户明确说“结束了”；
格式化时把摘要中的“明天 / 后天 / 周末 / 下周 X”等相对未来锚点只读渲染为“那天”，
并追加“那时说要做的事应该已经发生了”。本档未增加持久化 elapsed 调度扫描，
因此磁盘 status 仍保持原值，由读路径即时判断兜底。近程时间锚点细分为“刚刚”
（不足 1 小时）、“几小时前”（不足 6 小时）以及同一日历日内的“今天上午 /
今天早些时候”；日历日判断使用系统本地时区。

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
`status="resolved"` 的记忆在主召回和 fallback 召回中均被排除；旧记忆缺少 status 时按 open 处理。

**查询侧时间过滤（Brief 48）**：`retrieve(..., since_ts=, until_ts=)` 可选参，默认
`None`=现行为不变。非 None 时按 `occurred_at`（缺失回退 `timestamp`）过滤候选，过滤
发生在关键词/语义候选之后、评分之前；过滤后为空则退化为"时间范围内全量记忆"参与
评分，让"上周聊了什么"这类没有关键词的 time-only 查询也能召回；时间范围内确实
没有任何记忆时按空结果 abstain，不越界兜底。`[since_ts, until_ts)` 为半开区间。
调用方是 `pipeline.fetch_context()`：每轮开头调一次 `core.memory.temporal_query.
parse_query_time_range(content, now)` 解析用户消息里的时间意图（纯规则，无 LLM；
"昨天/前天/N天前/上周/上周末/上个月/周X（最近一个）/具体日期M月D日"；模糊表述如
"之前/很久以前"保守返回 `None`，宁可不过滤也不误过滤），解析结果同时透传给
`event_log.search(since_ts=, until_ts=)`（只扫范围内的日文件）与 episodic 语义预取
`vector_store.query_async(..., since_ts=)`；不影响 `retrieve_fallback()`（本来就是
近 7 天兜底，不接时间过滤）；解析结果记入 `recall_trace` 的 `parsed_time_range`
字段（`null` 或 `[since, until]`），供盲区排查。

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
- 记忆的 `emotion_peak` == 他当前情绪（从 `mood_state.get_current()` 读）→ `+0.15 + intensity×0.15`
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
他脑海里浮现的片段：
- 【重要】今天，用户说最近失眠严重，他陪他聊到很晚，像是被什么东西轻轻压着
- 前几天，两人因误解吵了一架，后来又和好了（从争执到释然）
- 今天，用户在吃西瓜（这件事已经结束了）
```

若调用方显式格式化 resolved 记忆，会追加“这件事已经结束了”，避免模型将其当作进行时追问。

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

- **只看 `occurred_at`（事件真实时刻）7 天内的记忆**（P0-4）；旧数据缺失 `occurred_at` 时回退 `timestamp`
- `strength >= 0.6`
- 与最近 short_term 内容不相似
- **核心记忆（`is_core=True`）额外限制**：`occurred_at` 超过 2 天不允许通过 fallback 复活，防止"生日/纪念日"一再浮起（P0-4）
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
- `ts`：写入时的 unix 时间戳（recorded_at；过期判定动态算 `now - ts > 12h`）
- `occurred_at`：**事件真实发生时刻**（P1-2）；由 `summarize_to_midterm` 从 turn_id 解析写入；旧数据缺失时回退 `ts`。`reflect_to_episodic` 读此字段填充 episodic.occurred_at，不再末端解析。
- `summary`：LLM 压缩或 fallback 兜底产出的一句话摘要
- `tags`：`tag_rules.get_tags(content)` 命中的标签列表，未命中为空
- `mid_id`：形如 `mt_{uid}_{ts_ms}`，由 `fixation_pipeline.summarize_to_midterm` 写入（旧数据缺失按 None 处理）
- `source_turn_id`：来源 turn_id，形如 `{uid}_{ts_ms}`（旧数据缺失按 None 处理）
- `source`：可选来源标签；Stage 投影使用 `group:{group_id}`
- `memory_strength`：可选投影强度系数；普通私聊默认 `1.0`，群聊默认 `0.7`
- `promoted_to_episodic_id`：已晋升时填入对应 ep_id，否则为 None

上限：`MAX_EVENTS = 20`，过期阈值 `EXPIRE_SECONDS = 12 * 3600`。
追加前会先按 `ts` 过滤过期事件、再截断到 `MAX_EVENTS - 1`，最后 append 新事件。

### 写入

`post_process_slow`（send 后异步段）将 `summarize_to_midterm` 入慢队列，payload 中携带 `char_id`（入队时的角色快照），
handler 内调 `llm_client.summarize_turn()` 压缩本轮对话并写入对应角色桶。
payload 缺 `char_id` 时（DLQ 旧任务兼容）WARN fallback yexuan，不静默。
LLM 异常时降级 warning，不阻塞主流程。

`summarize_turn` 内部：当 `len(user_msg) + len(reply) < 8`（合计字数）才走 `_rule_fallback`，
否则一律调 LLM 压缩。fallback 也会同时利用 `user_msg` 和 `reply`，产出
"用户：xxx；他：yyy" 形式，避免把用户原话直接写成"摘要"。
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

整条 slow_queue 链路均携带 `char_id`（入队时的角色快照），确保即使用户在任务执行前切换角色，写入也只落入对应角色桶。每个 handler 读取 payload 中的 `char_id`；缺失时 WARN fallback yexuan（DLQ 兼容层）。

`consolidate_to_identity` 会读取旧 identity、未固化 episodic、user_profile，让 LLM 只固化跨多条
episode 反复出现的模式。它写入 YAML 前会备份旧文件为 `.yaml.bak`，写完后标记对应 episodic 的
`consolidated_at`，并重置 `fixation_state` 计数器。

---

## 四点五、角色认知（character_growth）——已于 Brief 35 删除

`core/memory/character_growth.py` 模块与 `get_growth` 工具已于 Brief 35 一并删除：
grep 确认 `character_growth.load()`（模块内唯一读接口）的唯一读者就是 `get_growth` 工具本身，
无其他调用方，按引用计数原则整体退役。

写入链早在 R8-E2 就已迁移完毕，与本次删除无关：
```
trait_state 写入   → trait_tracker_update slow_queue task（R8-B）
user identity 写入 → consolidate_to_identity（fixation_pipeline）
```

磁盘上历史遗留的 `data/runtime/characters/{char_id}/character_growth/角色_{uid}.md`
（及 `.fingerprint.txt` / `.felt.md` 派生文件）不会被自动清理，也不再被任何工具读取。
`core/data_paths.py::DataPaths.character_growth()` 与 `core/memory/path_resolver.py` 的
`LEGACY_ARTIFACTS` 只读路径解析为审计/迁移脚本（`scripts/migrate_data_v1.py`）保留，
不要以此为由重新引入读写调用。

---

### trait_tracker 联动

文件：`core/memory/trait_tracker.py`

trait 统计逻辑（R8-B 起）由独立的 `trait_tracker_update` slow_queue task 承接：每个 `can_write_memory=True` 的有效 assistant turn 后入队，handler 直接写入 `data/runtime/characters/{char_id}/inner/trait_state.json`。统计最近40条对话里各性格特质的关键词命中次数，维护最近5次的滑动窗口，累计命中≤2次的特质标记为 `underrepresented`。

legacy `character_growth.update()` 内的 trait 写路径已于 R8-E2 随函数删除一并消除；
`character_growth` 模块本体已于 Brief 35 整体删除。

author_note_rotator 每次选 note 时读取此文件，命中 underrepresented 特质的 note 权重×2，让他近期较少展现的性格侧面更容易出现。

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

每轮 `post_process_slow`（Brief 37：send 之后异步跑的慢段）里，LLM 检测他回复的
情绪后调用 `update()`：

- `update(emotion, source="detect")` — source 可选值：`detect`（post_process_slow 检测）、`trigger`（关键词触发，如 yandere）、`schedule`（时间触发，如深夜 sleepy）

```
新强度 = 旧强度 × 0.7 + 新情绪强度 × 0.3
```

**情绪标签切换需同时满足**：
1. 新情绪强度 > 0.4
2. 连续两轮检测到同一新情绪（`pending` 字段记录上轮候选）

### 对外接口

- `get_current()` → 当前情绪字符串
- `get_intensity()` → 当前强度 float
- `update(emotion, force=False)` → 漂移更新；`force=True` 跳过切换门槛/pending，但仍保留强度漂移
- `nudge_from_memory(emotion, strength)` → 记忆召回时的微调（episodic 调）

### ⚠️ 当前状态

### prompt 注入形态

mood_text 输出**不是独立的 prompt 层**，而是直接拼入层 1（system_prompt）的 `## 当前感知` 区块之前。格式：
他此刻：{情绪描述}。[pending 时追加：但有什么东西好像在悄悄变得不一样。]

每个情绪 × 3档强度（<0.4 / 0.4-0.7 / >0.7）对应不同描述，例：
- `gentle` 低：淡淡的平静 / 中：平静，带一点轻盈 / 高：很平静，像静水
- `sad` 低：有点沉 / 中：沉着，像压着什么 / 高：很沉，有什么东西在

`yandere` 情绪不在 MOOD_TEXT 里，走 `get_mood_text` 的 fallback 降级为 neutral 描述。

mood_state 目前影响：
1. episodic_memory 召回时的 emotion_bonus 加分
2. nudge_from_memory 的情绪强度微调
3. 三路触发写入：detect（每轮 post_process_slow，保留强度门槛）、trigger（yandere 关键词；工具 thinking 通过 helper 强制置位）、schedule（深夜 sleepy，post_process_critical 里调用 helper 强制置位——sleepy 本身不含 LLM/网络往返，留在关键段不影响 send 延迟）
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

post_process_critical()  ← send 前必须走完的关键段，不 await 任何 LLM/网络往返
    ├─ 检查 profile 更新条件（读 short_term 估算长度）
    ├─ maybe_mark_sleepy_from_time（深夜 sleepy，无 LLM，留在关键段不影响延迟）
    └─ capture_turn(uid, content, reply, emotion="neutral", char_id=_active_character_id)
         [用户对话] → 写 short_term（user + assistant，含 turn_id）
         [用户对话] → 写 event_log（user + assistant，含 turn_id；emotion 是占位值 "neutral"）
         [触发器]  → 跳过 short_term（trigger 不入 history）
         [触发器]  → 写 event_log（assistant 行）+ trigger_audit.jsonl（metadata+hash）

record_assistant_turn() → channel fanout（send，用户在这里看到回复）

post_process_slow()  ← send 之后 asyncio.create_task 调度，不 await
    ├─ detect_emotion(reply) → 写 mood_state  ← 本轮真实情绪写入（只影响下一轮）
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

**关键时序（Brief 37 更新）**：`post_process` 原来是一整段同步流程，`detect_emotion`
（一次 LLM 往返，最长 8s 超时）挡在 send 之前，每条消息都多付一次 LLM 延迟。现在拆成
`post_process_critical`（send 前，只做毫秒级本地落盘）与 `post_process_slow`（send 后
异步执行 detect_emotion / mood_state / slow_queue 入队等）。event_log 里这一轮的
`emotion` 字段永远是 critical 段写入时的占位值 `"neutral"`，事后不会回写修正——这是
可接受的取舍，因为 event_log 的 emotion 只是标注字段，真正消费 emotion 做判断的
mid_term eager reflect 触发用的是 `post_process_slow` 里 detect 出的真实值。本轮真实
情绪只在 `post_process_slow` 完成后才写入 mood_state，所以只影响**下一轮**的记忆召回
——这一点本身没变，只是触发时机从"send 之前"挪到了"send 之后"，语义上更清楚地体现
"本轮情绪不影响本轮 prompt"。

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

自动阈值出口为 `consolidate_to_identity`（写 `user_identity.yaml`）。
`consolidate_to_growth` 是 pre-S5 遗留名称，从未注册 handler，R8-E1 已从当时的
`LEGACY_TASK_TYPES` 移除；`LEGACY_TASK_TYPES` frozenset 本体已于 Brief 35 随
`mid_term_append` / `episodic_compress` legacy handler 一并删除（见 §改动溯源 TD-3）。
`character_growth.md` 不再被任何工具读取——`get_growth` 工具与 `character_growth` 模块
已于 Brief 35 一并删除。

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
| `salvaged_dates` | event_log_salvage 已处理的 `YYYY-MM-DD` 列表，滚动保留 60 个（Brief 46 §2） |

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

### 来源隔离（web / dream / coplay，Brief 79）

「web 与梦境来源同等隔离，不固化」（见 AGENTS.md 速查表）覆盖固化链 + event_log 两段：

- **固化链**：`handler_summarize_to_midterm` 见 payload 的 `dream_echo` / `web_echo` /
  `coplay_echo` 标记即跳过 mid_term → episodic → identity 写入。
- **event_log**：`event_log.append(source=)` / `capture_turn(source=)` 把同一次判定
  写进 meta 行（`> ... source:web|dream_echo|coplay`），`dream_echo` 由
  `core/pipeline.py` `_detect_dream_echo()` 只读判定（不消费
  `forced_impression_rounds_left`，那个计数器仍只在 `post_process_slow` 消费一次）。
- **event_log_salvage 抢救链**：`_split_blocks` 出的块若 meta 带非空 `source:`，
  拼 LLM 输入前整块跳过（`_filter_salvageable_text`）——否则 27 天后这些外部信息会
  绕过固化隔离，经抢救直达 `important_facts`。

**契约**：任何直接读 event_log 做聚合/固化的新代码（storyline、未来 reflection 类），
必须过滤 `source:` 非空块。`event_log.search()` / `get_recent_days()`（注入侧召回）
**不过滤**——隔离的是「固化为长期记忆」，不是「短期可见」，注入侧本来就允许引用外部信息
（如 web_recall 层）。


## 六、他日记（yexuan_inner_diary）

**触发**：调度器每日 23:00，由 `_check_daily_journal()` 生成

**生成方式**：两次 LLM 调用，合并写入同一个 `.md` 文件

**文件格式**：
YYYY-MM-DD
今日事件

HH:MM 发生了什么（客观事实，分析器视角）
HH:MM 发生了什么

今日感受
他第一人称的心理活动和感受……

**注入方式**（prompt 层 6e，读昨天的文件）：
- 事件层：必注入，取前 200 字
- 感受层：命中 `emotion.down / emotion.indirect / emotion.deep / topic.relation` 时注入，取前 150 字；**低信息准入闸**：用户消息为 backchannel 时跳过（`suppress_emotional_recall=True`）

**规则纠察**：事件层写入前跑 `check_diary_facts()`，不合规则清空事件层，感受层仍正常写入

### 用户日记上下文（diary_context，prompt 层 6d）

`core/memory/diary_context.py`：每 6 小时由调度器 `_check_diary_inject()` 读取最近 2 天日记写入独立快照。

**新鲜度闸**（P0.5-1）：`save()` 现在无论内容是否为空都写入文件（clear-on-empty），同时写伴随元数据 `diary_context.meta.json`（字段：`captured_at`、`latest_entry_date`）。`fetch_context` 注入时检查 `latest_entry_date` 距今是否 ≤ `diary.context_max_age_days`（默认 4 天）；无 meta 或过旧 → 不注入，旧存量文件立即停止泄漏。

**低信息准入闸**（P0.5-2）：用户消息为 backchannel 时（`core/recall_gate.is_low_information()`），`diary_context` 在注入前被清空，无论新鲜度如何。

## 七、并发保护（locks）

**文件**：`core/memory/locks.py`

per-uid 锁（`uid_lock(uid)`）：保护关键写入和慢队列 handler 中按 uid 分文件的读-改-写操作，
包括 capture_turn、mid_term、user_profile、user_identity、episodic_memory 等。
`fetch_context()` 当前不加 uid_lock，因此用户极短时间连发时仍可能读到上一轮 post_process 尚未写完的旧状态；这是已知低概率竞态，见 `docs/known-issues.md`。

全局锁（`global_lock("mood_state")`）：保护跨 uid 共享的 mood_state 文件。

两种锁均为 asyncio.Lock，在单线程事件循环内安全。
`post_process_critical`（send 前的关键段）与 `post_process_slow`（send 后异步的慢段，
Brief 37）各自独立获取一次 uid 锁——不再是同一把锁贯穿整轮，capture_turn 落盘完就
释放，`global_lock("mood_state")` 要等 detect_emotion 跑完才在 `post_process_slow`
里获取。单用户连发时，critical 段的排队会拖慢 send（这正是它必须"只做毫秒级本地
落盘"的原因），slow 段的排队完全在后台，不影响任何用户可见的响应延迟。

### 路径 × 保护机制对照表（Brief 34 §6，2026-07-10）

此前审计已驳回“合并 uid_lock 与
message_queue"的提案：诊断（两套串行机制=认知负债）成立，但合并的回归风险大于收益。
降级为记录：下表把"哪条路径受哪套机制保护"钉成一页纸，供后续排查竞态时查表，
不代表要统一它们。

| 机制 | 粒度 | 保护什么 | 覆盖路径 |
|---|---|---|---|
| `message_queue`（`core/message_queue.py`） | session_key（私聊=uid，群聊=group_id） | 同一会话的多条**原始消息**严格串行处理，不同会话并行 | QQ 消息入口（`main.py` 收到消息 → `message_queue.enqueue`） |
| `conversation_lock`（`core/conversation_gate.py`） | uid（不区分 char_id） | 同一用户完整一轮对话（`fetch_context → build_prompt → run_llm → record_assistant_turn`）不被并行第二轮抢跑 | QQ `handle_message` / `_reply_with_tool_result` / `_handle_group_message`；desktop `admin/routers/chat.py`；scheduler `_pipeline_send`；`perceive_event`；stage `projection.py`/`runner.py`；admin dream 路由 |
| `uid_lock`（`core/memory/locks.py`） | uid | `post_process_critical`（send 前）内关键写入的读-改-写（capture_turn / profile 条件判断等）；`post_process_slow`（send 后异步）内 mood_state 更新的读-改-写；以及慢队列 handler 里按 uid 分文件的读-改-写 | `pipeline.post_process_critical` / `pipeline.post_process_slow` 各自独立的临界区；慢队列各 handler |
| `global_lock("mood_state")`（`core/memory/locks.py`） | 全局单键 | 跨 uid 共享的 mood_state 文件（角色级，不分用户） | `post_process_slow` 内 mood_state.update（send 后异步，Brief 37）；工具/调度器强制置位 mood 的 helper |
| vector_store 单 worker executor（Brief 34 §2；Brief 36 收尾 episodic 一处遗留同步调用，2026-07-10） | 进程级单线程 | 同一 sqlite 向量库文件的读写不被默认线程池的多路并发撞 lock（无 WAL，rollback-journal 默认模式） | `vector_store.query_async` / `query_with_preview_async` / `upsert`（`_upsert_sync` 在 executor 内跑）；`fetch_context` 语义召回、`event_log.search` 语义相似度、`episodic_memory.retrieve` 的 X2 语义候选扩展（`fetch_context` 预取 `sem_hits` 传入，`retrieve()` 自身不再直接查库） |

**层次关系**：`message_queue` 串行化的是"消息到达顺序"，`conversation_lock` 串行化的是
"同一 uid 的完整一轮处理"，二者当前都按 uid/session 分片、职责有重叠但不是同一把锁——
这正是裁定书认定的认知负债，本表是记录，不是修复。`uid_lock`/`global_lock` 粒度更细，
只包临界区，嵌套在 `conversation_lock` 内部。vector_store executor 是本次新增的第五种
机制，保护对象是 sqlite 文件而非内存状态，与前四种锁不是同一维度，不参与嵌套关系。

## 八、感知暂存（pending_perception）

**文件**：`core/memory/pending_perception.py`
**存储**：`data/runtime/pending_perception/` 时间戳命名 json 文件

### 两阶段提交（含竞态消除）

| 阶段 | 触发 | 操作 |
|---|---|---|
| 写入 | pipeline 桌面动作失败时 | 新建文件到根目录，consumed_at=null |
| 原子抢占 | build_prompt 调 read_and_mark() | os.rename 移到 processing/ 子目录，并发时只有一个 task 成功 |
| 删除 | post_process_critical 成功后 confirm_delivered() | 删除 processing/ 下的文件 |
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

**历史背景**：旧固化逻辑曾散落在 `post_process` 的频率触发中，三层之间无显式晋升关系；已删除的 `character_growth` 曾靠内存计数器驱动（重启清零）。

**改动**：把固化重写为四个具名 job 的显式 pipeline，每个 job 有触发条件、输入/输出契约、幂等保证和可观测日志。

**涉及文件**：

| 文件 | 改动 |
|------|------|
| `core/memory/fixation_pipeline.py` | 新增：四 job 实现 + fixation_state 读写 + 可观测日志 |
| `core/memory/mid_term.py` | 新增 `mid_id` / `source_turn_id` / `promoted_to_episodic_id` 血缘字段；新增 `mark_promoted()` |
| `core/memory/episodic_memory.py` | 新增 `source_mid_ids` / `consolidated_at` 血缘字段 |
| `core/memory/short_term.py` | `append()` 新增 `turn_id` 参数 |
| `core/memory/event_log.py` | `append()` 新增 `turn_id` 参数 |
| `core/memory/character_growth.py` | 历史迁移记录：`should_update()` 曾改为读 `fixation_state`；R8-E2 删除 `update()` / `should_update()` 后，模块及接口又于 Brief 35 整体删除。 |
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

---

## 语义向量库（X1）

阶段 A 已落地。见 [`docs/vector-store.md`](vector-store.md)。

- 存储：per-user `vector_store.db`（sqlite-vec，派生数据，gitignore）
- 接口：`embed()` in `core/memory/embedding.py`；`upsert/query/rebuild` in `core/memory/vector_store.py`
- 挂接：episodic 落盘后异步 upsert；event_log 每轮 post_process_critical 后异步 upsert；fetch_context 并联语义召回
- fail-open：embedding 不可达时回退关键词路径，主回复不受影响

---

## 工具已读指纹日志（P2，2026-06-26）

**文件**：`core/memory/tool_read_log.py`

**目的**：防止 `persist=True` 工具（日记读取、玩具文件、身体数据、日记搜索）在同一轮对话里对同一内容重复触发、重复占用上下文。

**存储路径**：`data/runtime/memory/{char_id}/{uid}/tool_read_log.json`
**格式**：`{"fingerprints": ["diary:2026-06-26", "toy:wishlist", ...]}`（最近 30 条，FIFO）

### 工具 persist 标记

在 `_TOOL_REGISTRY` 中，`persist=True` 的工具：

| 工具 | 指纹格式 |
|---|---|
| `read_diary` | `diary:{date}`（date 为空时取当日 YYYY-MM-DD） |
| `read_toy_file` | `toy:{file_key}` |
| `read_watch` | `watch:{today}:{query}` |
| `search_diary` | `search_diary:{query}` |

其余工具（`get_time`、`weather`、桌面控制等）为易失类，`persist` 字段缺省（False），结果只进本轮 prompt 层 10，不沉淀。

### 去重与回写流程

`tool_dispatcher.execute()` 的 `char_id` 为必传 kwarg（无默认值，调用方必须显式传入）：

1. **指纹检查**：persist 工具执行前调用 `build_fingerprint()` + `is_recently_read()`；命中 → 返回"刚读过这个，这次跳过"，不调用底层函数。
2. **执行**：未命中 → 正常执行。
3. **回写**：执行成功后调用 `record_read()` 记录指纹；并以 `format_read_memo()` 生成"把你今天的日记读了一遍。"这类角色视角一句，经 `_sanitize_assistant_message()` 脱敏后以 `role=assistant` 写入 `short_term`，让角色"记得"读过这个内容。

---

## 用户画像条目模型（P3，2026-06-26）

**文件**：`core/memory/user_profile.py`

### important_facts 条目格式

`important_facts` 元素升级为兼容 dict 格式：

```json
{"text": "喜欢听周杰伦", "tag": "pref.music", "ts": 1719360000}
```

旧 str 条目在读取时由 `_normalize_fact()` 归一化为 `{text, tag:"misc", ts:0}`，不强制迁移磁盘。

### 受控 tag 集合

| tag | 含义 | 层 5 行为 |
|---|---|---|
| `pref.music` | 音乐偏好 | recency 门控 |
| `pref.food` | 饮食偏好 | recency 门控 |
| `pref.media` | 影视/游戏偏好 | recency 门控 |
| `habit` | 日常习惯 | recency 门控 |
| `health` | 身体/精神状态 | recency 门控 |
| `stable` | 稳定客观事实 | 始终注入 |
| `misc` | 其他（旧数据默认） | 始终注入 |

### 层 5 注入规则（prompt_builder.py）

- **稳定段**（`name / location / pets / interests / occupation` + tag 为 `stable/misc` 的 facts）：维持平铺，100% 注入到层 `5_profile`。
- **偏好/习惯段**（`pref.* / habit / health` tag，层 `5_profile_pref`）：
  - `(now - ts) < 90天`：直接注入（recency 门控）
  - 当前轮次 tag 命中该偏好前缀（如 query tag 含 "music" 且 fact tag = "pref.music"）：无论新旧均注入
  - 两者都不满足：不注入，不占 context

### extract_and_update LLM 提示

新的提取 prompt 要求 LLM 为每条 important_fact 输出 `{text, tag, ts}` 格式，ts 填当前 Unix 时间戳。`_compress_facts()` 也已更新，压缩后保留 dict 格式。

---

## 改动溯源（provenance_log，G3）

**文件**：`core/memory/provenance_log.py`
**存储**：`data/runtime/memory/{char_id}/{uid}/provenance_log.jsonl`（append-only JSONL，上限 5 MB，滚动保留 3 份）

### 定位

记录"记忆写入侧"的改动轨迹——identity/mid_term/episodic 被更新/漂移时，写入一条带溯源信息的日志条目，回答"这条概括为什么变成这样"。历史上的改动无法回溯重建；日志从接入当日起前向积累。

与注入/回复溯源（`core/observe/prompt_capture.py`，内存态 ring buffer）**互补**，不替代。

### 记录 schema

```json
{
  "ts": 1234567890.0,
  "turn_id": "uid_1234567890000",
  "artifact": "identity",
  "field": "trust_pattern",
  "before_gist": "旧文本摘要（最多120字）",
  "after_gist": "新文本摘要（最多120字）",
  "trigger_signal": "触发本次改动的用户信号摘要（最多120字）",
  "origin": {"origin": "user"}
}
```

- `artifact`：写入的记忆层（`identity` / `episodic` / `mid_term` / `trait_state` / `author_note_state`）
- `field`：identity 时为维度 key（如 `trust_pattern`）；其他层为空串
- `before_gist` / `after_gist`：变更前后摘要，不存全文
- `origin`：直接复用 `prompt_capture._capture_origin`（user/proactive/desktop）

### 当前已接入的写入点

| 写入点 | artifact | field | 时机 |
|---|---|---|---|
| `fixation_pipeline.summarize_to_midterm()` | `mid_term` | `` | 每条 mid_term 追加后 |
| `fixation_pipeline.reflect_to_episodic()` | `episodic` | `` | 每条 episodic 写入后（非 core_dedup 合并路径） |
| `fixation_pipeline.consolidate_to_identity()` | `identity` | `{维度key}` | identity text 有实际变化的每个维度 |
| `fixation_pipeline.digest_evicted_episodes()` | `episodic` | `` | 每次上限裁剪淘汰批次归档后，`trigger_signal="evict_digest"`（Brief 46 §1） |

写入失败时只打 DEBUG 日志，不抛异常、不阻塞主写路径。

### 两个查询视图

**视图 A（改动溯源）**：按 `artifact` / `field` 查"这条概括什么时候、因为什么变的"。

```
GET /provenance/{uid}?artifact=identity&field=trust_pattern
```

**视图 B（yexuan-self）**：`scope=yexuan_self` 过滤 `artifact ∈ {trait_state, author_note_state}` 的条目，即"叶瑄自身被用户改变/漂移"的轨迹。是过滤视图，不是新库。

```
GET /provenance/{uid}?scope=yexuan_self
```

详见 `admin/routers/provenance.py`。

### 扩展写入点（G2 协同）

G2 显式遗忘（已落地，2026-06-28）每次删除/覆盖均记一条 `trigger_signal="explicit_forget" origin={"source":"admin"}` 的 provenance。

---

## G2 粒度删除 API（显式遗忘，2026-06-28）

各记忆层均已增加细粒度删除/覆盖入口，并调用 `provenance_log.append(trigger_signal="explicit_forget")`。

### 核心函数

| 层 | 函数 | 键 |
|---|---|---|
| episodic | `episodic_memory.delete_episode(uid, ep_id, char_id=)` | 条目 `id`；连带删向量 |
| profile.important_facts | `user_profile.delete_important_fact(uid, index, char_id=)` | list index |
| profile.important_facts | `user_profile.overwrite_important_fact(uid, index, text, char_id=, tag=)` | list index |
| user_identity | `user_identity.delete_dimension(uid, key, char_id=)` async | 维度 key |
| user_identity | `user_identity.overwrite_dimension(uid, key, text, char_id=, ...)` async | 维度 key |
| mid_term | `mid_term.delete_event(uid, mid_id, char_id=)` | `mid_id` |
| user_facts | `user_facts.delete_user_fact(uid, key)` | ALLOWED_FIELDS key（全局域，无 provenance） |
| event_log | `event_log.delete_day(uid, date_str, char_id=)` | `YYYY-MM-DD`（仅删新布局文件） |
| vector_store | `vector_store.delete(uid, char_id, source, source_id)` | `(source, source_id)` |

### 管理端端点

均挂在 `prefix=/memory`（`admin/routers/memory.py`）：

```
DELETE /memory/{uid}/episodic/{ep_id}
DELETE /memory/{uid}/profile/important-facts/{index}
PUT    /memory/{uid}/profile/important-facts/{index}   body: {text, tag}
DELETE /memory/{uid}/identity/{key}
PUT    /memory/{uid}/identity/{key}                    body: {text, confidence, evidence_count}
DELETE /memory/{uid}/mid-term/{mid_id}
DELETE /memory/{uid}/user-facts/{key}
DELETE /memory/{uid}/event-log/{YYYY-MM-DD}
```

所有端点均支持 `?char_id=` 参数（user-facts 除外）。404 = 条目不存在，422 = 参数非法，不崩。

### 重要约束

新增记忆写入点时，**必须同步调用 `provenance_log.append()`**（fail-open，wrap in try/except）。否则改动无法追溯。

---

## 角色成长状态（Brief 58-60）

- 兴趣唯一真值：`data/runtime/characters/{char_id}/inner/interest_state.json`，角色级、不分 uid。
- 练习作品：`data/runtime/characters/{char_id}/works/{interest_id}/`，正文与盲评永不进入对话记忆链。
- 技巧笔记：`data/runtime/characters/{char_id}/notes/{interest_id}.md`，只注入后台练习 prompt，不注入对话 prompt。
- 唯一回流面是 `action_trace` 的一行“练习发生过”事实；兴趣新增、状态迁移、升级、笔记学习和 MCP 解锁均写 provenance。
- `practice.enabled: false` 是 58-61 共用的默认关闭总闸。

