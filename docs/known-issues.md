# docs/known-issues.md — 已知问题与技术债

> 修复前请先对照代码确认问题仍存在。修复后把条目移动到"已核对修复"或补充修复说明。
>
> 状态标签：
> - `now-safe-to-fix`：不依赖 execute live soak，可按小修推进。
> - `blocked-by-execute-live-soak`：等 3.5b / execute live 稳定后再动。
> - `refactor-phase`：适合重构期统一处理，不作为小修插入。
> - `boundary-doc-needed`：先补设计/行为边界，再决定是否改代码。
> - `close-candidate`：已过时或可关闭，关闭前最后对照代码核一次。

---

## 当前仍存在

### B11：fetch_context 读写竞态

**状态**：`boundary-doc-needed`

**位置**：`core/pipeline.py` → `fetch_context()` / `post_process()`

用户在 1-2 秒内连发两条消息时，第二条 `fetch_context()` 可能读到第一条 `post_process()` 尚未 `capture_turn()` 的旧状态，表现为 history 缺上一轮、mood 仍是旧值。

**暂不修原因**：窗口已经被 slow_queue 拆分压到较短，给 `fetch_context()` 加 `uid_lock` 会让连发响应变慢。若实际出现漏听，再考虑加锁或做 per-uid 输入队列。

---

### B12：核心情景记忆未被上限裁剪保护

**状态**：`now-safe-to-fix`

**位置**：`core/memory/episodic_memory.py` → `write_episode()`

文档和设计原则都强调 `is_core` 记忆不应被裁掉，但 `write_episode()` 在超过 200 条时直接按 strength 排序删除最低 20 条，没有排除 `is_core=True`。`cleanup()` 手动清理路径有保护，自动写入路径没有。

**建议**：自动上限裁剪改为 normal/core 分组，永远保留 core，再从 normal 里删低 strength。

---

### F10：trait_tracker 未接入新固化 pipeline

**状态**：`refactor-phase`

**位置**：`core/memory/character_growth.py` / `core/memory/fixation_pipeline.py`

特质统计仍在 legacy `character_growth.update()` 内。当前主路径 `consolidate_to_growth()` 不调用 `trait_tracker`，所以只走新 fixation pipeline 时，`data/yexuan_inner/trait_state.json` 可能长期不刷新，`author_note_rotator` 的 underrepresented 加权会失真。

**建议**：在 `consolidate_to_growth()` 成功写入后，或在独立 slow_queue task 中刷新 trait_state。

---

### F11：memory 工具已注册但未暴露给正式主 LLM

**状态**：`refactor-phase`

**位置**：`core/tool_dispatcher.py` / `main.py` / `core/pipeline.py`

`read_diary/read_watch/search_diary/get_profile/get_episodic/get_growth` 都已在 `_TOOL_REGISTRY` 注册，`execute()` 也能执行；但当前工具探针只传 `info + desktop`，而 `pipeline.run_llm()` 调主模型时没有传 tools schema。因此 Author's Note 里"必须调用 read_diary"的规则没有真实工具调用通道支撑。

**建议**：二选一：要么给正式对话增加工具调用回合；要么把日记/记忆类工具改成 pre-pipeline 探针覆盖，并明确哪些场景允许触发。

---

### S1：仍有部分 data 路径绕过 sandbox

**状态**：`now-safe-to-fix`

**位置**：`core/prompt_builder.py`、`core/lore_engine.py`、`admin/routers/jailbreak_entries.py`、`admin/routers/lorebook.py`、`admin/routers/settings_misc.py`

项目规则要求所有 `data/` 路径通过 `core/sandbox.get_paths()`。当前仍有少量 `Path("data/...")` 常量，测试模式下可能绕过 `data/test_sandbox/{session}/`。

**建议**：按模块逐步迁移到 `get_paths()`，并为 lorebook / jailbreak entries 补测试模式验证。

---

### D2：调度器活跃窗口硬编码 120 秒

**状态**：`blocked-by-execute-live-soak`

**位置**：`core/scheduler/loop.py` → `_user_active_recently()`

低优先级主动消息在用户 120 秒内活跃时跳过。窗口不可配置，边界情况较难调：用户可能已经离开，也可能仍在连续输入。
当前 owner 入口已补 active 标记：QQ、桌宠 owner chat、手机 owner chat 都会调用 `mark_user_active()`。
但窗口长度、不同 trigger 的 defer/drop 策略仍未配置化，暂不关闭。

**建议**：把窗口提到 `config.yaml` 的 scheduler 节点。

---

### D10：diary_share 新旧路径去重源需要等 live 后统一

**状态**：`blocked-by-execute-live-soak`

**位置**：`core/scheduler/loop.py` → `mark_diary_shared()` / `core/scheduler/triggers/diary.py`

`diary_share` 相关去重依赖 `_last_diary_share` 与 `scheduler_state.json`。旧真实触发器和 proposal execute live 共同存在时，去重源容易分叉。
本轮只核对了 reminder 旧路径：legacy reminder 已改为只有 `_pipeline_send()` 实际 sent 后才
`mark_done()`；active window 拦截时留在队列下次 tick 再捞。该修复不等于 `diary_share` 已统一，
不要据此关闭 D10。

**建议**：等 execute live 接管稳定后，再统一 diary_share 的 mark / after_send / 状态持久化语义。

---

### D7：叶瑄日记尚未反向进入长期认知

**状态**：`refactor-phase`

**位置**：`data/yexuan_inner/diary/`

叶瑄每日写的日记目前只作为 prompt 层 6e 注入，尚未参与 character_growth 更新，也不作为 mood_state 的长期参考。

**待设计**：决定它是否应该影响角色对用户的认知，还是只保留为角色自己的短期内省材料。

---

### G2：花园写入尚未使用 safe_write / 锁

**状态**：`now-safe-to-fix`

**位置**：`core/garden/manager.py`

`water()` / `daily_check()` 当前用普通 `Path.write_text()` 写 `plants.json` 和 `storage.json`。现在已有 `garden_water`、`garden_daily`、`water_garden` 三条写路径；虽然调度器单 worker 下冲突概率不高，但用户触发 `water_garden` 时可能与调度器扫描同一份 storage。

**建议**：接入 `safe_write_json()` 或 garden 专用锁，把 `plants.json` / `storage.json` 的读改写包起来。

---

### G4：花园采后部分分支不离开 harvest

**状态**：`boundary-doc-needed`

**位置**：`core/garden/manager.py` → `daily_check()`

`vase` 分支会把花从 `harvest` 移到 `vase`，但 `dry` / `gift` / `ask` 只标记 `handle_triggered`，仍留在 `harvest`。之后过期扫描仍可能把同一朵花当作 `harvest_expired` 处理。若设计上做成干花或送给用户后应离开收获区，需要补状态迁移。

**建议**：明确 `dry/gift/ask` 的最终容器：移入 `history`、新增 `dried/gifted` 列表，或保留 harvest 但在过期逻辑里跳过非 fresh 状态。

---

### DESIGN-1：感知数据使用原则仍需补边界

**状态**：`boundary-doc-needed`

**位置**：`DESIGN.md` → “六、感知数据使用原则”

DESIGN 中仍有待补内容：什么时候主动提起现实数据、什么时候只影响态度、哪些数据可以直接说出口、哪些应隐藏在关心里。该边界会影响 sensor / watch / diary / activity 的主动行为，不宜直接用代码先行决定。

**建议**：先补设计边界，再决定对应代码行为。

---

### DESIGN-2：主动行为设计原则仍需补边界

**状态**：`boundary-doc-needed`

**位置**：`DESIGN.md` → “七、主动行为设计原则”

DESIGN 中仍有待补内容：叶瑄主动联系的心态、不打扰与主动的平衡、哪些事值得打断用户、哪些事等待用户来找。该边界会影响调度器 gating / execute live 的体感判断。

**建议**：先补设计边界，再继续调主动消息策略。

---

### F8：管理面板对话 UI 右键历史未实现

**位置**：`admin/static/index.html`

对话记录当前没有右键菜单或快捷历史操作。属于前端体验债，和主对话链路无关。

---

## 已核对修复

| 编号 | 结论 |
|---|---|
| B1 tool_result 双重注入 | 已修复。`perception_block` 只放 pending/跨通道接续，工具结果只走层10。 |
| B2 episodic `current_emotion` 硬编码 | 已修复。`fetch_context()` 调用 `mood_state.get_current()`。 |
| B3 `retrieve(emotion=\"\")` 死参数 | 已修复。`retrieve()` 签名已移除 emotion 参数，内部直接读 mood_state。 |
| B4 Windows 原子写覆盖失败 | 已修复。`safe_write` 使用 `Path.replace()`。 |
| B7 growth fingerprint/full 重复注入 | 已修复。tag 命中 full，未命中 fingerprint，二者互斥。 |
| B8 裁剪顺序 | 当前代码为 `6b_event_search → mid_term → 6d_diary → 6e_inner_diary → 6c_episodic → 5.5_lore`，已与文档同步。 |
| B9 episodic fallback 召回过窄 | 已缓解。7天内、strength≥0.6，score 使用 `max(0.5, 1/(age_days+1))` 排序。 |
| B10 mid_term 短消息摘要塌缩 | 已修复。fallback 同时参考 user/reply，LLM 门槛改为合计长度。 |
| F1 mood_state 未进 prompt | 已修复。情绪软提示内嵌到层1，不是独立层。 |
| F2 event_log search 相关性不足 | 已修复为块级评分 + 7天外过滤 + `MIN_SCORE=0.6`。 |
| F4 activity 沉默 10 分钟注入 | 已修复。层2.6 同时判断 history 为空或 presence 超 10 分钟。 |
| Q1 PromptBuilder 类参数缺失 | 类封装已不存在，主路径只用模块级 `build()`。 |
| Q3 fingerprint 长度不一致 | 当前写入和 prompt 截取均为 150 字。 |
| Q4 tag 注释误称 regex | 已修正为 substring 匹配。 |
| Q5 exit_yandere 硬编码兄弟路径 | 已改为读取 `config.yaml` 的 `emerald_desktop.path`。 |
| Q6 空壳 tag | `tool_only` / `quick_fact` 已不存在。 |
| D3 send_notification 关键词过窄 | 已扩展为"时间词 + 动作词"组合校验。 |
| D5 event_log 跨天召回 | 已改为乘法衰减、7天外 intensity 过滤、块级聚合。 |
| D6 tag 命中不可观测 | `tag_rules.debug` logger 已记录 hit/miss。 |
| D8 episodic 多样性不足 | 已用 emotion_texture novelty 做 MMR 筛选。 |
| E2 跨通道接续提示 | 已实现。`Pipeline.build_prompt(channel=...)` 在通道切换时注入层1感知。 |
| E3 LLM 输出校验与重试 | reflect/growth/legacy compress 均有格式校验和最多 3 次重试。 |
| E6 post_process 锁饥饿 | 已拆成关键路径 + slow_queue 慢任务。 |
| F3 inbox 笔记未接入 prompt_builder | 已通过废弃 `/inbox/upload` 解决，文件上传统一改为 `/upload/ingest` 直接进 pipeline，不再产生孤儿笔记。 |
| F7 花园状态未推送给 qq-st-bot | 已由 `core/garden`、`GET /garden/state`、`garden_water`、`garden_daily` 和 `water_garden` 接入；当前边界转为 G2/G4。 |
| G1 花园采后处理尚未实现 | 已实现 `garden_daily`：harvest 过期、采后 ask/dry/vase/gift/silent、vase 枯萎均已接入；剩余边界转为 G2/G4。 |
| G3 花园事件冷却名未真正节流事件发言 | 已修复。`garden_bloom`、`garden_harvest_expired`、`garden_vase_wilted`、`garden_handle_ask`、`garden_handle_gift`、`garden_handle_self` 发言前均显式 `_is_ready()`，发送后 `_mark()`。 |

---

## 已过时 / 可关闭候选

| 编号或来源 | 状态 | 结论 |
|---|---|---|
| HANDOFF Step 3 卡点 | `close-candidate` | “shadow log would_pick 恒为 hr_critical”“删 `_adapt_legacy_triggers`”已不符合当前主干；当前已有原生 proposer 与 execute dry-run。关闭前对照 `core/scheduler/gating.py` / `core/scheduler/proposer_registry.py` 核一次。 |
| D9 Watch 即时路径与 execute live 接管边界 | `close-candidate` | 当前 Watch event-driven 路径在 `WATCH_EXECUTE_MODE="live"` 时通过 proposal execute 执行；`sleep_end` / `hr_high` 的 active-window 与 sent 后 mark 语义已在 `policy.py` 中记录。rollback 分支仍保留旧 `_pipeline_send()` 路径，关闭前最后对照 `core/scheduler/triggers/watch.py` 和 `core/scheduler/execution.py` 核一次。 |
| short_term 加权裁剪未开 | `close-candidate` | 当前 `core/memory/short_term.py` 已有 `load_for_prompt()` 近场保留 + 远场加权择优。若仍有问题，应改记剩余边界，不再保留“未开”。 |
| get_growth 死工具 + character_growth 模块清理 | `close-candidate` | 当前应先标注 legacy/兼容边界；是否删除等观察期或重构期再决策。 |
| 探针输出自然语言 | `close-candidate` | 已记录为“不修”项；若无新增复现，不进入当前修复队列。 |

---

## 观察项

- `detect_emotion()` 只返回 neutral/happy/sad/gentle/surprised/angry；thinking 由工具探针触发，sleepy 由深夜 schedule 触发，yandere 由关键词触发，因此不是死状态。
- `short_term._sanitize_assistant_message()` 当前在读取 history 时清洗，不会回写磁盘；这能保护 prompt，但不能清理历史文件本身。
- `llm_output_validator` 的失败计数在内存中，debug 输出写到 `data/debug/llm_output/`，保留 7 天。
- `/upload/ingest` 支持文档（`.txt` / `.md` / `.docx`，单文件）和图片（`.jpg` / `.jpeg` / `.png` / `.gif` / `.webp` / `.heic` / `.heif` / `.bmp`，多文件）。QQ 路径仍由 NapCat 触发，走同一组 `ingest_*` 函数。旧 `inbox.py` 曾解析 `.pdf` 但产物未进 pipeline，等同未上线；若后续需要 PDF，可接入 skill 实现。
- event_log 的实际存储格式是 `data/event_log/{uid}/{date}.md`，不是 `.jsonl`；Phase 1 turn_sink 文档曾写错，已按代码现实修正。
- scheduler policy 表当前只做 scaffold / documentation-by-code / assertions，尚未接入 live defer/drop 决策。R1 后 `execute_dryrun.jsonl` 会记录 `blocked=true`，先观察 active-window blocked 分布，再决定是否把 `core/scheduler/policy.py` 接到真实执行层。

### identity-1：counter 累积无上限衰减，长期可能僵死维度

**状态**：`boundary-doc-needed`

位置：core/memory/fixation_pipeline.py → _synthesize_identity
counter_evidence_count 只在 LLM 重写 text 时归零，否则只增不减。
翻转机制依赖 LLM 主动判断"旧判断已不成立并重写 text"。如果 LLM 保守
不肯重写，某维度可能 counter 持续累积、confidence 被永久压到注入阈值以下，
形成"僵死维度"——既不注入也不翻转。
当前缓解：定期看 data/user_identity/{uid}.yaml，发现僵死维度手动清。
待评估：是否加 counter 时间衰减（last_conflict_at 超过 N 天无新冲突时
counter 缓慢回落），类似 episodic 的 decay。先观察实际是否发生再决定。

### identity-2：identity 注入时机依赖 fixation 链，冷启动期长

**状态**：`boundary-doc-needed`

新用户前几十轮对话，episodic 还没攒够触发 consolidate，user_identity.yaml
为空，6a_user_identity 层不注入。这是预期行为（宁可不注入也不瞎猜），
但意味着"叶瑄了解用户"有明显冷启动期。观察项：confidence 阈值 0.5 +
maturity_factor(ev/10) 双重门槛下，实际要多少轮对话才会有第一个维度
注入。如果太久（比如 200 轮还是空），考虑放宽 maturity_factor 或降阈值。

### 待核对记账

| 项 | 状态 | 说明 |
|---|---|---|
| context.max_turns 不生效 | `now-safe-to-fix` | 先对照 `core/memory/short_term.py` 与管理面板配置是否仍不一致，再正式展开。 |
| 裁剪后 debug_info 失真 | `now-safe-to-fix` | 先对照 `core/prompt_builder.py` 裁剪后 `layers_activated` 是否同步更新。 |
| `_layer` 透传 LLM | `now-safe-to-fix` | 先对照 `core/llm_client.py` 是否剥离元数据；若仍透传，独立修。 |
| mes_example 精简 | `refactor-phase` | 属 prompt 体感与 token 策略，不作为当前小修。 |
| 时间联动注入 | `refactor-phase` | 属 prompt/感知策略，等设计边界明确后再评估。 |

2026.5.30迁移data，拆完sandbox剩下的一点架构优美债
TD-001 sandbox retirement
→ 50+ 文件依赖
→ conftest 依赖
→ 兼容层必须保留

TD-002 _sb 命名
→ 只是不好看
→ 真改还撞 _dp()

P2
CharacterGrowth Retirement 最后尾巴

tool_dispatcher._get_growth_wrapper()

阻塞：
identity 缺关键事件
identity 缺未跟进话题

当前不处理

Dream 输出协议设计