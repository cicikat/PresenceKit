# 私人内容备份集清单

> **用途**：列出哪些路径属于"丢了不可重建、git 又不存"的，供磁盘外备份参考。  
> 不包含可由运行时重建的缓存/索引，也不包含已进 git 的模板文件。  
> 对应 `data_registry.py` 的 `git_policy = ignore-but-authored` 语义。

> **审计类文档提醒（Brief 33 §1.4）**：`docs/critique-*.md`（审计报告）与
> `docs/*-triage-*.md`（裁定记录）这类文档天然容易引用真实配置值（曾在
> `docs/critique-fable-20260707.md` 中发现明文 admin secret）。`git add` 前须人工
> grep 一遍真实密钥/口令/QQ 号等敏感值，确认已脱敏（占位符/`<redacted>`）再入库，
> 开源前总检查（`docs/opensource-v0.1-checklist.md` 类清单）也应把这类新增文档纳入扫描范围。

---

## 一、track(git) — 已跟踪，可从 git 恢复

| 路径 | 说明 |
|------|------|
| `examples/character_template.json` | 角色卡空模板（在 examples/ 下，不再出现在 admin UI 列表中） |
| `characters/dream_worlds/*/lorebook.yaml` | 内置梦世界 lorebook（6 个世界） |
| `characters/dream_worlds/*/ruleset.md` | 梦世界规则文本 |
| `characters/dream_worlds/*/mes_example.md` | 梦世界对话示例 |
| `characters/dream_worlds/*/vocab.json` | 梦世界词汇表 |
| `config.example.yaml` | 配置文件模板 |

**尚未 git add 的模板文件（不受 gitignore 限制，但还未跟踪）：**

| 路径 | 说明 |
|------|------|
| `content/characters/yexuan/traits.example.yaml` | traits 编写模板 |
| `content/characters/yexuan/activity_pool.example.yaml` | 活动池编写模板 |
| `content/jailbreak_presets/示例.example.json` | jailbreak 预设模板 |
| `defaults/lorebook.yaml` | lorebook 空种子（`entries: []`） |
| `defaults/relations.yaml` | relations 最小默认种子 |
| `defaults/blacklist.yaml` | blacklist 空种子 |
| `defaults/jailbreak_entries.json` | jailbreak 空种子 |

> 建议执行一次 `git add` 将上述文件纳入跟踪，避免误以为"已备份"。

---

## 二、ignore-but-authored(需备份) — 不在 git，唯一副本在磁盘

**丢失 = 不可重建。须磁盘外备份（移动硬盘 / 网盘 / 加密云存储）。**

### 2a. DataPaths 注册路径（git_policy = ignore-but-authored）

| DataPaths 方法 | 当前物理路径（fallback） | 目标路径（accessor primary，S8 后生效） |
|---------------|------------------------|----------------------------------------|
| `activity_pool()` | `data/yexuan_inner/activity_pool.yaml` | `content/characters/yexuan/activity_pool.yaml` |
| `yexuan_traits()` | `data/yexuan_traits.yaml` | `content/characters/yexuan/traits.yaml` |
| `author_notes_pool()` | `characters/yexuan_author_notes.json` | `content/characters/yexuan/yexuan_author_notes.json` |

> 物理文件尚未迁移（S8），accessor 已优先读新位置；fallback 路径即当前实际文件。  
> **备份时以 fallback 路径为准（当前唯一副本）。**

### 2b. 非 DataPaths 路径（直接访问，未注册）

| 路径 | 说明 |
|------|------|
| `characters/他.json` | 角色卡主体（JSON 格式） |
| `characters/他.txt` | 角色卡文本版 |
| `characters/yexuan_author_notes.json` | 当前 author notes 池（同 2a fallback） |
| `characters/yexuan_author_notes - 副本.json` | author notes 备份副本 |
| `characters/dream_presets/custom.md` | 自定义梦境预设 |
| `characters/dream_presets/default.md` | 默认梦境预设 |

### 2c. seed 类（种子已跟踪为空模板，运行时副本含私人定制内容）

| 路径 | DataPaths 方法 | 说明 |
|------|---------------|------|
| `characters/reality/lorebook.yaml` | `lorebook()` | 现实世界书条目（defaults/ 空种子仅用于 test sandbox） |
| `characters/reality/jailbreak_entries.json` | `jailbreak_entries()` | 破限预设条目（defaults/ 空种子仅用于 test sandbox） |
| `data/relations.yaml` | `relations()` | 实际关系数据（defaults/ 种子为最小默认） |
| `data/blacklist.yaml` | `blacklist()` | 实际黑名单（defaults/ 种子为空） |

> `lorebook.yaml` 和 `jailbreak_entries.json` 在 production 模式下不再自动从 defaults/ 种子生成——
> 若文件缺失，`data_paths.py` 会打印 ERROR 日志并返回原路径，调用方自行 fallback 空列表。
> 文件缺失时应从版本库（`git checkout`）或磁盘备份恢复。

### 2d. 用户级不可重建配置

| 路径模式 | DataPaths 方法 | 说明 |
|---------|---------------|------|
| `data/runtime/dreams/{char_id}/settings/{uid}.json` | `dream_settings_path()` | 每用户的 dream 偏好设置 |
| `data/runtime/memory/{char_id}/{uid}/identity.yaml` | `user_memory_root()` | 用户身份信息（手动或 LLM 积累） |
| `data/diary_fallback/` | `diary_fallback()` | obsidian_path 未配置时的本地日记目录 |

---

## 三、ignore-runtime(可重建) — 不在 git，程序可自动重建或可接受丢失

| 路径模式 | 说明 |
|---------|------|
| `data/runtime/memory/{char_id}/{uid}/history.json` | 对话历史（丢=失忆，但属运行积累非配置） |
| `data/runtime/memory/{char_id}/{uid}/{mid_term.json,episodic.json}` | 记忆摘要 |
| `data/runtime/memory/{char_id}/{uid}/memory_index.json` | 情景记忆索引（可重算） |
| `data/runtime/characters/{char_id}/inner/` | 运行时角色状态（mood/activity/trait_state 等） |
| `data/runtime/dreams/{char_id}/tmp/` | 进行中的梦（退出转 archive） |
| `data/runtime/dreams/{char_id}/archive/` | 梦境归档（count-cap GC，不进 loader） |
| `data/logs/` | forensic 日志（可丢，不影响业务） |
| `data/runtime/channel_queue.json` 等 | runtime IPC 文件（重启清） |
| `data/cache/image_cache/` | 视觉缓存（sha256，可重算） |
| `data/inbox/` | 上传原始文件（解析后可删） |
| `data/test_sandbox/` | 测试沙盒（cleanup() 清理） |

---

## 备份建议

1. **最高优先级**：备份 §2a + §2b（私人authored，丢失=永久失去人设配置）  
2. **次优先级**：备份 §2c（私人内容定制，种子可恢复但定制条目不可重建）  
3. **可选**：备份 §2d（用户级配置，重配代价高但非零）  
4. §三 无需特别备份（可接受丢失或可重建）

备份频率建议：每次修改 §2a/§2b 后立即备份；§2c 每周一次。
