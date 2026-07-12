# Data Taxonomy

本文记录当前 `data/` 路径治理实现。代码真值在：

| 文件 | 职责 |
|---|---|
| `core/data_paths.py` | `DataPaths` 实现、布局开关、路径安全检查 |
| `core/sandbox.py` | `get_paths()` / `init_paths()` 单例胶水，测试沙盒前缀写入 |
| `core/data_registry.py` | 每个公开路径方法的 durability / domain / scope / git_policy 元数据 |
| `core/migration.py` | 迁移期 `for_read(new, old)` 降级读与命中观测 |
| `core/paths.py` | 未来 taxonomy 命名空间规划占位，当前不接 loader |

## 当前布局

`core/data_paths.py` 当前启用：

```python
_LAYOUT_CHARACTER_INNER = "v1"
_LAYOUT_REALITY = "v1"
_LAYOUT_DREAM = "v1"
```

生产环境的实际写入根目录如下。不要根据旧设计稿中的 `data/memory/`、`data/characters/`
或 `data/dreams/` 推断当前落盘位置。

```text
data/
├── runtime/
│   ├── channel_queue.json
│   ├── mobile_queue.json
│   ├── agent_actions.json
│   ├── pending_perception/
│   ├── scheduler_user_state.json
│   ├── memory/{char_id}/{uid}/
│   │   ├── history.json
│   │   ├── event_log/{date}.md
│   │   ├── mid_term.json
│   │   ├── episodic.json
│   │   ├── memory_index.json
│   │   ├── profile.json
│   │   ├── identity.yaml
│   │   ├── diary_context.txt
│   │   ├── reminders.json
│   │   └── fixation_state.json
│   ├── characters/{char_id}/
│   │   ├── inner/
│   │   │   ├── mood_state.json
│   │   │   ├── activity_state.json
│   │   │   ├── activity_snapshot.json
│   │   │   ├── observations.jsonl
│   │   │   ├── author_note_state.json
│   │   │   ├── trait_state.json
│   │   │   ├── presence.json
│   │   │   └── diary/
│   │   ├── garden/{plants.json,storage.json}
│   │   ├── pet.json
│   │   └── character_growth/  # 历史遗留文件；Brief 35 已移除代码读写
│   └── dreams/{char_id}/
│       ├── tmp/
│       ├── archive/
│       ├── summaries/
│       ├── impressions/
│       ├── state/{uid}/dream_state.json
│       └── settings/{uid}.json
├── scheduler_cooldowns.json
├── group_context/
├── inbox/
├── cache/image_cache/
├── logs/
│   ├── error.log
│   ├── dead_letter_queue/
│   ├── fixation.jsonl
│   ├── trigger_state.jsonl
│   ├── gating_shadow.jsonl
│   └── execute_dryrun.jsonl
├── debug/llm_output/
├── diary_fallback/
├── jailbreak_entries.json
├── lorebook.yaml
├── relations.yaml
└── blacklist.yaml
```

测试模式把上述相对路径整体偏移到 `data/test_sandbox/{session_id}/`。`sandbox.init_paths()`
还会更新 `config.yaml` 的 `data_prefix`，供旧桌宠文件轮询兼容。

## 路径边界

### Reality memory

现实记忆主干统一写入 `get_paths().user_memory_root(uid, char_id)`：

| 文件 | 当前用途 |
|---|---|
| `history.json` | 层 9 短期历史 |
| `event_log/{date}.md` | 层 6b 事件搜索 |
| `mid_term.json` | 12 小时中期摘要 |
| `episodic.json` / `memory_index.json` | 层 6c / 9.5 情景记忆与索引 |
| `profile.json` | 层 5 画像与低频传感数据 |
| `identity.yaml` | 层 6a 稳定行为模式 |
| `diary_context.txt` | 层 6d 用户近期日记上下文 |
| `reminders.json` | 待办备忘 |
| `fixation_state.json` | 固化 pipeline 状态 |

`DataPaths.history()`、`mid_term()`、`profiles()` 等分类型 accessor 仍保留给少量兼容调用方；
新增 per-user 读写优先使用 `user_memory_root()`。

### Character inner

角色内部状态统一落在 `data/runtime/characters/{char_id}/`。其中 `inner/`、`garden/` 和
`pet.json` 都按角色隔离。`character_growth/` 是 Brief 35 移除模块留下的历史数据：不再有
代码读写，不应作为当前状态或新增路径使用。`activity_pool()`、`yexuan_traits()`、
`author_notes_pool()` 属于 authored 静态内容，优先读 `content/characters/{char_id}/`，
物理文件未迁移时回退旧位置。

### Dream

Dream domain 独立落在 `data/runtime/dreams/{char_id}/`，不进入 reality memory 树。
现实侧只允许专用 loader 读取 `summaries/` 和 `impressions/` 生成低权回流层；
`tmp/`、`archive/`、`state/`、`settings/` 均不是现实记忆源。

### Shared runtime and forensic

- IPC / 临时队列：`data/runtime/*.json`
- 调度器冷却真值：`data/scheduler_cooldowns.json`
- 调度器用户级运行态：`data/runtime/scheduler_user_state.json`
- forensic 日志与 DLQ：`data/logs/`
- 上传文件与视觉缓存：`data/inbox/`、`data/cache/image_cache/`

## 迁移兼容

`core/migration.py` 仍保留 `for_read(new, old)`：新路径缺失、为空或无法解析时回退旧路径，
并记录命中次数和最近命中样本。当前仍可见的兼容读包括：

- `event_log` 单日读取、scheduler 今日发言检查和 last-mentioned 搜索；
- `event_log` 近 30 天 union 读；
- `dream_settings` 从旧 `data/dreams/settings/{uid}.json` 回退。

authored 静态内容另有 new-primary / old-fallback，但由 accessor 自己判断文件存在性，不走
`for_read()`。其余 reality memory loader 已直接读取 `user_memory_root()` 下的新路径。

因此不能把旧目录删除当作默认安全动作。清理前先确认 fallback 观测归零，并检查
`tests/test_fallback_obs.py`、`tests/test_s56_layout_roundtrip.py`、`tests/test_event_log_union.py`。

## Never Prompt Load

以下路径默认不应直接进入现实 prompt/retrieve：

| 路径 | 说明 |
|---|---|
| `data/logs/` | forensic 日志和 DLQ |
| `data/debug/` | 异常样本 |
| `data/runtime/channel_queue.json` 等 | IPC 队列 |
| `data/runtime/dreams/{char_id}/tmp/` | 活跃梦境原文 |
| `data/runtime/dreams/{char_id}/archive/` | 梦境归档原文 |
| `data/runtime/dreams/{char_id}/state/` | 梦境状态机 |
| `data/runtime/dreams/{char_id}/settings/` | 用户梦境偏好 |
| `data/inbox/` | 上传原始文件 |
| `data/cache/image_cache/` | 图片描述缓存 |

注意：`core/tools/diary_reader.py` 会对配置的日记根目录按文件名递归查找。不要把日记根目录
指向整个 `data/`，否则同名 `YYYY-MM-DD.md` 可能被误读。

## 新增路径规范

1. 在 `core/data_paths.py` 增加 accessor，所有运行态 `data/` 路径都从 `get_paths()` 获取。
2. 在 `core/data_registry.py::REGISTRY` 登记治理元数据；`tests/test_data_registry.py` 会自检。
3. 明确 test sandbox 偏移、旧路径兼容和清理策略。
4. 属于 retention 的路径同步登记 `RETENTION_POLICY`，并接入 `scheduler._check_log_maintenance()`。
5. 更新本文和对应专题文档。
