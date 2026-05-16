# docs/known-issues.md — 已知问题与技术债

> 修复前请先对照代码确认问题仍存在。修复后把条目移动到"已核对修复"或补充修复说明。

---

## 当前仍存在

### B11：fetch_context 读写竞态

**位置**：`core/pipeline.py` → `fetch_context()` / `post_process()`

用户在 1-2 秒内连发两条消息时，第二条 `fetch_context()` 可能读到第一条 `post_process()` 尚未 `capture_turn()` 的旧状态，表现为 history 缺上一轮、mood 仍是旧值。

**暂不修原因**：窗口已经被 slow_queue 拆分压到较短，给 `fetch_context()` 加 `uid_lock` 会让连发响应变慢。若实际出现漏听，再考虑加锁或做 per-uid 输入队列。

---

### B12：核心情景记忆未被上限裁剪保护

**位置**：`core/memory/episodic_memory.py` → `write_episode()`

文档和设计原则都强调 `is_core` 记忆不应被裁掉，但 `write_episode()` 在超过 200 条时直接按 strength 排序删除最低 20 条，没有排除 `is_core=True`。`cleanup()` 手动清理路径有保护，自动写入路径没有。

**建议**：自动上限裁剪改为 normal/core 分组，永远保留 core，再从 normal 里删低 strength。

---

### F3：inbox 笔记未接入 prompt_builder

**位置**：`admin/routers/inbox.py` / `core/prompt_builder.py`

`/inbox/upload` 会把原文存到 `data/inbox/`，把叶瑄读后的笔记存到 `data/yexuan_inner/notes/`，并维护 `data/yexuan_inner/notes_index.json`。但 prompt_builder 没有读取 notes_index，因此笔记不会进入对话上下文。

**建议**：新增一层 notes recall，按标题/标签/最近投递时间门控注入，避免每轮全量塞入。

---

### F10：trait_tracker 未接入新固化 pipeline

**位置**：`core/memory/character_growth.py` / `core/memory/fixation_pipeline.py`

特质统计仍在 legacy `character_growth.update()` 内。当前主路径 `consolidate_to_growth()` 不调用 `trait_tracker`，所以只走新 fixation pipeline 时，`data/yexuan_inner/trait_state.json` 可能长期不刷新，`author_note_rotator` 的 underrepresented 加权会失真。

**建议**：在 `consolidate_to_growth()` 成功写入后，或在独立 slow_queue task 中刷新 trait_state。

---

### F11：memory 工具已注册但未暴露给正式主 LLM

**位置**：`core/tool_dispatcher.py` / `main.py` / `core/pipeline.py`

`read_diary/read_watch/search_diary/get_profile/get_episodic/get_growth` 都已在 `_TOOL_REGISTRY` 注册，`execute()` 也能执行；但当前工具探针只传 `info + desktop`，而 `pipeline.run_llm()` 调主模型时没有传 tools schema。因此 Author's Note 里"必须调用 read_diary"的规则没有真实工具调用通道支撑。

**建议**：二选一：要么给正式对话增加工具调用回合；要么把日记/记忆类工具改成 pre-pipeline 探针覆盖，并明确哪些场景允许触发。

---

### S1：仍有部分 data 路径绕过 sandbox

**位置**：`core/prompt_builder.py`、`core/lore_engine.py`、`admin/routers/jailbreak_entries.py`、`admin/routers/lorebook.py`、`admin/routers/settings_misc.py`

项目规则要求所有 `data/` 路径通过 `core/sandbox.get_paths()`。当前仍有少量 `Path("data/...")` 常量，测试模式下可能绕过 `data/test_sandbox/{session}/`。

**建议**：按模块逐步迁移到 `get_paths()`，并为 lorebook / jailbreak entries 补测试模式验证。

---

### D2：调度器活跃窗口硬编码 120 秒

**位置**：`core/scheduler/loop.py` → `_user_active_recently()`

低优先级主动消息在用户 120 秒内活跃时跳过。窗口不可配置，边界情况较难调：用户可能已经离开，也可能仍在连续输入。

**建议**：把窗口提到 `config.yaml` 的 scheduler 节点。

---

### D7：叶瑄日记尚未反向进入长期认知

**位置**：`data/yexuan_inner/diary/`

叶瑄每日写的日记目前只作为 prompt 层 6e 注入，尚未参与 character_growth 更新，也不作为 mood_state 的长期参考。

**待设计**：决定它是否应该影响角色对用户的认知，还是只保留为角色自己的短期内省材料。

---

### G1：花园采后处理尚未实现

**位置**：`core/garden/manager.py` / `core/garden/constants.py`

花园已实现五槽位、自动浇水、开花后进入 `storage.harvest` 并重新播种；但 `HARVEST_HANDLE_SECONDS`、`VASE_WILT_SECONDS` 和处理概率常量目前只定义未消费。开花后的询问用户、自己处理、送给用户、静默、花瓶枯萎等逻辑还没落地。

**建议**：先设计采后状态机，再补定时 sweep 或管理面板操作，避免 harvest 无限堆积。

---

### G2：花园写入尚未使用 safe_write / 锁

**位置**：`core/garden/manager.py`

`water()` 当前用普通 `Path.write_text()` 写 `plants.json` 和 `storage.json`。现阶段主要写入口是 `garden_water` 调度器，风险较低；但 `force_water()` 已作为公开函数存在，未来接工具或管理面板按钮后可能出现并发写覆盖。

**建议**：开放第二写入口前，接入 `safe_write_json()` 或 garden 专用锁。

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
| F7 花园状态未推送给 qq-st-bot | 已由 `core/garden`、`GET /garden/state` 和 `garden_water` 调度器接入；当前边界转为 G1/G2。 |

---

## 观察项

- `detect_emotion()` 只返回 neutral/happy/sad/gentle/surprised/angry；thinking 由工具探针触发，sleepy 由深夜 schedule 触发，yandere 由关键词触发，因此不是死状态。
- `short_term._sanitize_assistant_message()` 当前在读取 history 时清洗，不会回写磁盘；这能保护 prompt，但不能清理历史文件本身。
- `llm_output_validator` 的失败计数在内存中，debug 输出写到 `data/debug/llm_output/`，保留 7 天。
