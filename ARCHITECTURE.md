# ARCHITECTURE.md — 系统架构总览

---

## 系统全貌

角色通过 QQ、桌宠和调度器三个入口进入同一条 pipeline，输出再交给通道层发送：

```
QQ 消息 → main.py → message_queue
桌宠消息 → admin/routers/chat.py（POST /desktop/chat）
调度器主动消息 → core/scheduler/loop.py
         ↓（入口共用）
      Pipeline（core/pipeline.py）
         ↓
      LLM（DeepSeek）
         ↓
      channels.registry 广播到活跃通道（QQ / 桌宠）
```

通道细节见 `docs/channels.md`。花园这类不进入对话 pipeline 的伴生状态，见 `docs/garden.md`。

---

## Pipeline 四步骤

```
用户消息
    │
    ▼ 步骤0（在 pipeline 之前，main.py 里）
探针判断工具（关键词快速路径 + 极简 LLM probe，只看 info+desktop 类）
get_tags()（对消息打话题标签，传给 build_prompt）
工具执行（结果写入 tool_result）
    │
    ▼ 步骤1  fetch_context()
并发拉取所有记忆数据：
├─ short_term.load()          → history              [同步]
  ├─ user_relation.get_relation()→ relation             [同步]
  ├─ group_context.get_recent() → group_context         [同步]
  ├─ character_growth.load()    → growth_content        [同步]
  ├─ lore_engine.match()        → lore_entries          [同步]
  ├─ episodic_memory.retrieve() → episodic_result       [同步]
  ├─ episodic_memory.retrieve_fallback() → episodic_fallback_result  [同步]
  ├─ get_reminders()            → reminders             [同步]
  ├─ diary_context.load()       → diary_context         [同步]
  ├─ user_profile.load()        → profile               [异步线程]
  ├─ mid_term.format_for_prompt() → mid_term_context    [异步线程]
  └─ event_log.search()         → event_search_result   [异步]
    │
    ▼ 步骤2  build_prompt()
  get_tags() 计算标签（或复用探针阶段的）
  读取 pending_perception（上轮失败的桌面动作感知）
  调 prompt_builder.build() 组装 messages[]
    │
    ▼ 步骤3  run_llm()
  调 llm_client.chat(messages)，含重试
    │
    ▼ 步骤4  post_process()（asyncio.create_task，不阻塞）
  │
  │  【关键路径】uid_lock(uid) 内，按顺序同步完成：
  ├─ detect_emotion()                  asyncio.wait_for(timeout=8s)，超时降级 neutral
  ├─ global_lock("mood_state") 内：
  │   ├─ mood_state.update(emotion)    更新情绪状态
  │   └─ yandere 触发检测              关键词 + 关系阈值
  └─ capture_turn()                    写 history + event_log（user/assistant，含 turn_id）
  │
  │  【慢队列】uid_lock 释放后入 slow_queue，单 worker 异步执行：
  ├─ summarize_to_midterm              LLM 压缩单轮到 mid_term，写血缘字段；emotion 显著时触发 reflect_to_episodic(eager)
  ├─ reflect_to_episodic               mid_term 列表 → episodic，更新 fixation_state；达阈值触发 consolidate_to_growth
  ├─ consolidate_to_growth             unconsolidated episodic → character_growth.md（含备份+校验+回滚）
  ├─ consistency_check                 人设一致性检测，问题存 author_note_extra
  └─ user_profile_update               每 N 轮触发，入队时已判断条件
  │  （旧 handler mid_term_append / episodic_compress 保留供 DLQ 残留任务重试）
  │
  │  【side effects】保持 asyncio.create_task，不入慢队列：
  ├─ TTS / 表情包（随机互斥）
  └─ _parse_and_execute_intent()       桌面操作意图解析
  │
  │  slow_queue worker 特性：
  │  - 单 worker（per-uid 顺序由 handler 内 uid_lock 保护）
  │  - 失败退避重试（0.5s×1, 1.0s×2，共 3 次）
  │  - 超限写入 DLQ：data/dead_letter_queue/{ms_ts}_{task_type}.json
  │  - 不持久化队列（进程退出丢失，有意设计）
```

---

## 探针（probe）机制

探针在 pipeline 之前，main.py 里处理，目的是在角色卡加载前就判断是否需要调工具：

- 使用极简 system prompt（`get_probe_prompt()`），不带角色卡
- 只判断 info + desktop 两类工具
- memory 类工具不走探针，靠 LLM 在正式对话中自主调用
- 两个入口（main.py / admin/routers/chat.py）共用同一个 `get_probe_prompt()` 函数

---

## 数据流向总图

```
用户消息
    ↓ get_tags()
    ↓ 探针 + 工具执行
    ↓
fetch_context ──读──→ data/ 目录各文件
    ↓
build_prompt ──组装──→ messages[0..12层]
    ↓
run_llm ──→ reply
    ↓
post_process ──写──→ data/ 目录各文件
```

---

## 数据目录结构

> 所有路径必须通过 `core/sandbox.get_paths()` 获取，不得硬编码。

```
data/
├── event_log/{uid}/              按天分割的对话流水账（search 最近 30 天）
├── episodic_memory/{uid}.json    情景记忆（最多 200 条，含 strength 衰减）
├── memory_index/{uid}.json       标签倒排索引（episodic 用）
├── character_growth/
│   ├── 角色_{uid}.md             角色对用户的整体认知（由 fixation pipeline 固化更新）
│   ├── 角色_{uid}.felt.md        感受层版本（存在时优先读，不存在时降级到 .md）
│   └── 角色_{uid}.fingerprint.txt  压缩版指纹（前 150 字；当前 prompt 直接从 felt/.md 取前 150 字）
├── profiles/{uid}.json           用户画像
├── history/{uid}.json            短期对话历史（最近 20 轮）
├── mid_term/{uid}.json           中期对话摘要（12小时过期，最多20条，三时间桶）
├── diary_context/{uid}.json      用户日记上下文
├── group_context/{gid}.json      群聊最近动态（prompt 层 4 注入）
├── reminders/{uid}.json          用户备忘录列表（调度器检查到期即发）
├── pending_perception/           桌面动作失败感知（时间戳命名，两阶段提交）
├── activity_snapshot.json        桌宠推来的活动快照（TTL 5 分钟）
├── inbox/                        用户投递的原文档
├── pet.json                      角色宠物状态（core/pet.py 管理）
├── garden/
│   ├── plants.json               五个情绪花槽状态（stage/growth/last_watered）
│   └── storage.json              收获/花瓶/历史记录（当前主要写 harvest）
├── yexuan_inner/
│   ├── diary/                    角色日记（每日 23:00 调度器触发）
│   ├── notes/                    角色读文档后的笔记
│   ├── notes_index.json          笔记索引（inbox 生成；暂未注入 prompt）
│   ├── mood_state.json           角色当前情绪状态（全局唯一）
│   ├── activity_pool.yaml        活动状态池（手写配置，固定路径，不走沙盒隔离）
│   ├── activity_state.json       当前活动状态（activity_manager 管理）
│   ├── observations.jsonl        角色行为观察日志（style_hint 来源，prompt 层11）
│   ├── author_note_state.json    author_note_rotator 轮转状态
│   ├── trait_state.json          性格特质命中统计（trait_tracker 写，author_note_rotator 读）
│   └── presence.json             每个用户上次说话时间（presence.py 管理）
├── agent_actions.json            桌面动作队列（桌宠端轮询）
├── channel_queue.json            调度器广播队列（asyncio.Lock 保护）
├── scheduler_state.json          调度器冷却状态
├── fixation_state/{uid}.json     固化 pipeline 状态（episodic_since_last/strength_accumulated 等，重启不丢）
├── logs/
│   └── fixation.jsonl           固化 pipeline 每 job 追加一行（ts/job/uid/status/duration_ms）
├── dead_letter_queue/            慢任务 DLQ（handler 3次失败后落盘，含 task/error/failed_at）
├── debug/
│   └── llm_output/              LLM异常输出存档（带时间戳，7天自动清理）
└── (锁池)                        core/memory/locks.py 管理，运行时内存对象，不落盘
```

---

## 全局 Pipeline 实例管理

`core/pipeline_registry.py` 持有唯一的 Pipeline 实例，供管理面板和后处理纠偏等跨模块获取。
调度器另有一份 `_pipeline` 引用，由 `scheduler.set_pipeline(pipeline)` 注入。

```python
# 注册（main.py 初始化时）
pipeline_registry.register(pipeline)

# 跨模块获取（如 admin/routers/chat.py、consistency_check handler）
pipeline = pipeline_registry.get()
```

---

## token 估算与裁剪

估算用字符数（`len(content)`，不是真实 token 数）：

| 阈值 | 行为 |
|---|---|
| > 15000 字符 | 打 warning 日志 |
| > 20000 字符 | 触发强制裁剪 |

裁剪顺序（依次删，按质量从低到高）：
`6b_event_search` → `mid_term` → `6d_diary` → `6e_inner_diary` → `6c_episodic` → `5.5_lore`


---

## Hook：文档同步提醒

`.claude/hooks/` 下两个 hook 在 Claude Code 编辑代码后自动检查文档是否需要同步更新。

### 工作机制
PostToolUse（每次 Edit/Write/MultiEdit）
└─ track_edits.py 把改动路径追加到 .claude/.cache/edits_{session_id}.json
Stop（Claude 准备结束响应时）
└─ remind_docs.py 读 cache，比对规则
├─ 改了代码但相关文档未动 → decision: "block" + reason 拦下
└─ 文档已同步或只改了文档 → 清空 cache，放行

`stop_hook_active=true` 时直接放行，保证最多卡一轮，Claude 要么补文档、要么明说"无需更新：理由"再停。

### 规则映射（remind_docs.py 顶部维护）

**全局兜底**：改 `core/`、`main.py`、`admin/` 下任何代码 → 提示 `ARCHITECTURE.md` + `AGENTS.md`

**专项追加**：

| 改动路径关键词 | 追加提示文档 |
|---|---|
| `core/memory/`、`core/safe_write.py`、`core/integrity_check.py`、`core/llm_output_validator.py`、`tools/extract_observations.py` | `docs/memory.md` |
| `core/prompt_builder.py`、`core/tag_rules.py`、`core/mood_text.py`、`core/author_note_rotator.py`、`core/lore_engine.py`、`characters/`、`data/jailbreak_entries.json` | `docs/prompt-layers.md` |
| `core/tool_dispatcher.py`、`core/tools/` | `docs/tools.md` |
| `core/scheduler/` | `docs/scheduler.md` |

`docs/known-issues.md` 不进自动规则，bug 修复后手动记账。

### 不会触发的情况

- 改 `tests/`、`README.md`、配置文件等非映射路径
- 同一轮里代码和对应文档都改了（pending 为空）
- 上一轮已被 hook block 过（防死循环）

### 配置位置

| 文件 | 作用 |
|---|---|
| `.claude/settings.json` | hooks 节点声明 PostToolUse + Stop 两个钩子 |
| `.claude/hooks/track_edits.py` | 记录本轮编辑过的文件 |
| `.claude/hooks/remind_docs.py` | Stop 前检查 + 阻塞，规则映射也在此 |
| `.claude/.cache/edits_*.json` | 每个 session 的编辑记录（已 gitignore） |

### 新增规则

`remind_docs.py` 顶部 `SPECIFIC_RULES` 追加一条：
```python
(["路径关键词1", "路径关键词2"], "docs/新文档.md", "说明"),
```
其他都不动。

### 临时关闭

`.claude/settings.json` 加 `"disableAllHooks": true`，或直接删 hooks 节点。改完自动 reload，不用重启 Claude Code。
