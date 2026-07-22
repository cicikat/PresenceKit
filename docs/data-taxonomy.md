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
│   ├── observability/api_calls-YYYY-MM-DD.jsonl  # 外部 API 调用总账，保留最近 7 天
│   ├── scheduler_user_state.json
│   ├── spend/mandates.jsonl       # Brief 63 预留兼容读面，当前无 writer
│   ├── relations/{char_a}__{char_b}.json
│   ├── groups/{group_id}/
│   │   ├── meta.json
│   │   ├── transcript.json
│   │   └── arbiter_trace.jsonl
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
│   │   │   ├── interest_state.json
│   │   │   ├── presence.json
│   │   │   └── diary/
│   │   ├── works/{interest_id}/
│   │   ├── notes/{interest_id}.md
│   │   ├── garden/{plants.json,storage.json}
│   │   ├── pet.json
│   │   └── character_growth/  # 历史遗留文件；Brief 35 已移除代码读写
│   └── dreams/{char_id}/
│       ├── tmp/
│       ├── archive/
│       ├── summaries/
│       ├── impressions/
│       ├── postcards/schedule.json
│       ├── invariants/{uid}.json
│       ├── state/{uid}/dream_state.json
│       └── settings/{uid}.json
├── runtime/dreams/_stage/{group_id}/      # Brief 100：群聊梦境（Dream Stage）
│   ├── tmp/current_dream.jsonl
│   ├── archive/{dream_id}.jsonl
│   ├── state/dream_state.json
│   ├── settings.json
│   └── arbiter_trace.jsonl
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

### User-authored assets (C1)

`userdata/` 是本机私有的手写资产根目录，不属于 `data/` 的运行时状态沙盒，也不提交到 Git：

```text
userdata/
├── assets/stickers/{emotion}/
└── characters/
    ├── cards/{char_id}.{json,txt,md}
    ├── authored/{char_id}/
    │   ├── activity_pool.yaml
    │   ├── author_notes.json
    │   ├── traits.yaml
    │   ├── letter_samples/
    │   └── knowledge/
    ├── reality/{avatars,lorebooks,jailbreaks}/
    └── dream/{worlds,presets}/
```

访问必须经 `DataPaths` 或 `AssetRegistry`。读取优先 `userdata/`，仅在旧安装目录仍存在且主路径缺失时回退到
`assets/stickers/`、`characters/` 和 `content/characters/`；新建角色、梦境世界及其他可写资产写入
`userdata/`。`defaults/`、`examples/`、默认角色卡和梦境世界模板仍是随仓库发布的公共种子，不迁入
`userdata/`。

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
`author_notes_pool()` 属于 authored 静态内容，优先读
`userdata/characters/authored/{char_id}/`，仅为未迁移的旧安装回退旧位置。

`interest_state.json`、`works/{interest_id}/` 与 `notes/{interest_id}.md` 是当前成长系统的
角色级 canonical 真值；物理位置虽在 `runtime/` 树下，仍不可按临时缓存清理。

### Dream

Dream domain 独立落在 `data/runtime/dreams/{char_id}/`，不进入 reality memory 树。
现实侧只允许专用 loader 读取 `summaries/` 和 `impressions/` 生成低权回流层；
`tmp/`、`archive/`、`state/`、`settings/` 均不是现实记忆源。
`postcards/schedule.json` 保存冻结信件及投递状态，`invariants/{uid}.json` 保存跨世界观察；
两者均为 canonical，不应随临时梦目录清理。

**群聊梦境（Dream Stage，Brief 100）** 是完全独立的一棵树：
`data/runtime/dreams/_stage/{group_id}/`，物理上与 `stage_group_dir()`
（`data/runtime/groups/{group_id}/`，reality Stage 真值）刻意分离——`GET /group/list`
的 `*/meta.json` glob 永远扫不到这里，理由同 `private_exchange_dir()`。v1 零回流：
`tmp/current_dream.jsonl`（共享梦内 transcript，speaker 前缀）、`archive/`（硬退归档，仅供复盘）、
`state/dream_state.json`（`char_tension: {char_id: float}` + `per_char_snapshots` + 共享
`body_state`，schema 见 `docs/stage.md` §六）、`settings.json`（`per_char.jailbreak_presets`
回退链）、`arbiter_trace.jsonl` 均不进入任何现实 loader。

### Shared runtime and forensic

- IPC / 临时队列：`data/runtime/*.json`
- 调度器冷却真值：`data/scheduler_cooldowns.json`
- 调度器用户级运行态：`data/runtime/scheduler_user_state.json`
- Stage 真值与观测：`data/runtime/groups/{group_id}/{meta.json,transcript.json,arbiter_trace.jsonl}`
- 跨 Stage 的角色关系：`data/runtime/relations/{char_a}__{char_b}.json`
- 外部 API 调用总账：`data/runtime/observability/api_calls-YYYY-MM-DD.jsonl`（只记调用元数据，
  fail-open，最近 7 天；只读查询见 `GET /observability/api-calls`）
- Brief 63 兼容读面：`data/runtime/spend/mandates.jsonl`（当前无 writer）
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
| `data/runtime/dreams/_stage/{group_id}/` | 群聊梦境（Dream Stage）整棵树，Brief 100 |
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
