# PresenceKit 工单 319｜酒馆历史记忆 v5：长期记忆接入与连续对话回查

> 优先级 P1 · 目标仓库 `cicikat/PresenceKit` · Codex 施工 · 2026-11-14
>
> 用户已表示跨平台本地文件 Agent 工单做完。本单仅涉及**酒馆历史记忆**，不修改文件 Agent、Windows/macOS 适配、MC、LOFTER、其它日记和手机/桌面 UI。
>
> **授权边界：**本工单授权代码、合成数据测试、只读真实数据盘点与 dry-run。生产 `--apply` 必须有针对实际批次、用户/角色和备份的**额外明确授权**；不得自动执行。代码完成 ≠ 数据已真正导入。

## 一、要修的实际问题

用户已经让 GPT 清洗过 SillyTavern（酒馆）聊天：原始回复很长，有大量重复的场景、神态、动作修饰，清洗包保留了对话脉络。叶瑄现在会调用 `search_character_notes`、`search_documents`、`search_events`、`get_episodic`，却只能找到最近用户讲述旧事产生的片段与一些抽象概述，找不到旧酒馆真正的连续聊天、细节与时间线。

**验收目标是两条并行链路：**

1. **长期记忆链路**：整理好的历史经历、关系、相处偏好能成为正常召回的一部分；对外展示来源标注 `酒馆`，并区分现实旧自述、meta 相处、dream 剧情。不能只是把一份 Markdown 当背景说明每轮塞给模型。
2. **历史原文链路**：用户说“去年 11 月”“今年 1 月”“那段剧情前后发生了什么”，叶瑄能检索经过清洗的历史消息，沿着同一会话顺序展开前后上下文、给出可核验引用；未找到时不能装作找到了。

**禁止**为实现该目标重跑对私密档案的 LLM 大清洗，或者把 1,712 条历史消息作为当前 `event_log`/`short_term` 回合重放。

## 二、唯一输入与预期数量

使用之前已完成的**v4** 整理包：`叶瑄_记忆整理_v4_亲密内容正常纳入.zip`。私有目录由用户/本机配置决定；Windows 可参考 `D:\ai\private-imports\`，macOS 使用用户指定的私有导入目录。不要默认文件必然在某处，也不要全盘扫描私有目录。

```text
sillytavern_v4_完整长期记忆/
  memory_import.jsonl                # 候选主文件，127 条
  v4_manifest.json                   # 清单和 sha256
  normalized_archive.jsonl           # 清洗后的历史消息 1712 条
  source_context_index.jsonl         # 候选与历史对话的回查索引
  reclassification_audit.jsonl       # 旧候选重分类，27 条
  new_adult_private_candidates.jsonl # 新增亲密/私密候选，40 条
  relationship_and_intimacy.md       # 人工对照材料，不是机器权威输入
lofter_低置信度档案/
  ...                                # 本轮完全不处理
```

| 对象 | 整理包预期 | 实施语义 |
|---|---:|---|
| 清洗后旧酒馆消息 | 1,712 | 只读历史档案，供日期/关键词/上下文查询 |
| `memory_import.jsonl` 候选 | 127 | 不和旧 v2/v3 包重复叠加 |
| `import_default=true` | 121 | 经审核的可导入长期记忆候选 |
| `import_default=false` | 6 | `pending_review`，默认不进入常规召回 |
| LOFTER OCR | 44 | 不读取、不导入、不索引，防止误合并 |

上述是**整理包清单的预期**，不是 VPS/本机已实际验证的结果。Codex 要读取真正文件的行数、hash、schema、唯一 ID、时间范围和来源引用再报告。如果输入包未提供，不得伪造数据或声称“实际导入完成”。

**保密要求：**真实 ZIP、JSONL、私密原文、解压中间目录不得进入公开仓库、Git 历史、Issue/PR、CI artifact、日志、遥测、Prompt 调试转储。生产真实数据不得默认发往外部模型或第三方服务。需要本机→OrangeVPS 传输时，必须先核对实际目的地、范围和用户授权，使用最小权限的认证加密渠道；可优先在隔离本机做 dry-run。

## 三、先查源码，定位真实断点（A 阶段）

施工前记录当前 Git SHA，审查最新 `main`、本地分支和以下已实现合同，不得凭工单臆造函数签名：

- `core/memory/scope.py`、`path_resolver.py`、`data_paths.py`：真实 `uid + char_id`，不可回退其它角色。
- `core/memory/episodic_memory.py`：`write_episode()` 有约 200 条容量上限和淘汰逻辑。**不要循环调用它导入 121 条，造成原有情节记忆被挤掉。**
- `core/memory/vector_store.py`：现有 `rebuild()` 会删除整个向量 DB，再从 episodic 和近期 event_log 重建。**不能仅 `upsert(source=sillytavern_archive)` 就宣称导入持久有效，重建会丢。**
- `core/memory/event_store.py`、`event_query.py`、`source_policy.py`、`event_migration.py`：`legacy_unknown` 等来源默认隔离，不能粗暴取消全局隔离；旧 RP 不是新产生的 Reality turn。
- `core/memory/dossiers.py`、`docs/character-memory-dossiers.md`、`cc-tasks/258*`、`259*`：主题档案是派生理解，不能代替真实证据；历史回填和首夜生产运行有独立闸门。
- `core/tools/event_tools.py`、`core/tools/character_recall.py`、`core/tool_dispatcher.py`、`core/tool_exposure.py`、`core/prompt_builder.py`：判断角色实际有哪些检索工具、Path A/C 发现能力、返回预算和 source 展示逻辑。
- `scripts/migrate_memory_events.py`、`docs/offline-state-backup.md`：复用备份/校验/恢复示例。既有 318 文件 Agent 不是绕过记忆存储的写库后门。

**输出仅含元数据的诊断表**，明确逐项区分：

1. 整理包是否在运行机器的私有目录中，实际哈希与条目数是否匹配；尚未拿到数据就填 `missing_input`。
2. `search_character_notes / search_documents / search_events / get_episodic` 目前各自检索的 authority、scope、source，以及酒馆命中数量。
3. 2025 年 11 月和 2026 年 1 月相关检索为何失败：未导入、未索引、日期缺失、source 被过滤、角色作用域不符、工具未发现、输出截断中的哪一种；要有可复验路径或测试。
4. 现有 Dossier、Embedding、向量索引在生产角色上的实际有效状态（不是“代码里有函数就算可用”）。
5. 导入新数据和重建索引是否可能影响原有记忆；如有风险，停止生产写入并修改设计。

## 四、语义合同（必须保留 v4 决策）

内部 `source_kind=sillytavern_archive`；用户可见显示 **`酒馆`**，正常召回或展开引用应携带该来源。

来源和故事性质是两条轴：

| 字段 | 对应语义 | 不可发生的错误 |
|---|---|---|
| `namespace=real` | 旧聊天中用户当时的现实自述 | 直接当成今天仍有效的现实事实 |
| `namespace=meta` | 关系、相处、写作、偏好讨论 | 永久固定称呼/偏好、覆盖后来的明确更正 |
| `namespace=dream` | 酒馆 RP 的故事经历 | 说成现实共同经历；没 `world_id` 时乱认领某个 Dream 世界 |
| `historical_assistant_dialogue` | 旧模型当时的回复 | 将角色当时的自述认定为用户证实的现实证据 |
| `privacy_class=personal` | 私有内容，按 owner/char 权限保护 | 因含成人、亲密、性内容被自动丢弃 |
| `import_default=false` | 需复核 | 被批量脚本自动放行 |

**用户明确要求成人/亲密/私密内容像其他长期记忆一样正常参与候选和合适语境的召回**，不是关键词过滤或一律转成泛化概述。但历史剧情中的同意、限制、身体状态不构成现在和未来的永久现实授权。

允许 v2 继承条目缺少可选字段（如 `privacy_class`、`memory_tier`、`retrieval_policy`）：采用保守私有默认，不因为缺字段遗漏旧候选。时间不详必须标 `unknown`，不能捏造精确发生日期；`dream` 原文无 `world_id` 不得伪造。

## 五、推荐实现：一个历史来源、两个粒度，不破坏现有记忆

```text
v4 ZIP / verified source
   ├── normalized_archive.jsonl (1712)
   │     └─ 同角色受控历史档案：消息顺序 / 日期精度 / 场景 / source refs
   │            ├── 关键词、时间、来源检索（Embedding 不可用仍可查）
   │            ├── 按同一会话展开 before/after
   │            └── 可选向量索引（派生且可重建）
   └── memory_import.jsonl (127)
         ├── 121 eligible → 带来源的长记忆派生层 / 正常记忆检索
         └── 6 pending_review → 禁止默认召回
```

不强求使用某个新库。如果当前项目的 Memory Event、Dossier、资料检索不足以正确表达旧 RP 证据，新增**轻量的 legacy archive adapter**，但是统一复用 `MemoryScope`、权限和现有正常召回入口，不建第二人格、第二独立记忆权威，不破坏原先数据。

### B1｜历史原文的受控档案

- 使用真实 `uid + char_id + dataset_version + source_kind + source_message_ref` 建稳定引用。
- `normalized_archive` 的消息/段落是**历史档案**，不是新的聊天室、现有 event_log 或新的真实 Reality turn。
- 记录原有 actor、source、session/scene 序号、时间及精度；`source_context_index` 的每个来源引用要能展开。断链保留 `source_incomplete`，不能编造上下文。
- 在项目 path resolver 规定的私有数据目录持久化来源权威、数据版本、batch id、source hash、record status、撤销墓碑；禁止裸路径拼接和绕权限 SQL。
- 仅保留必要的受控档案/索引；不把 1712 条原文自动写入常驻 prompt。

### B2｜正常长期记忆

- 121 条候选只在**明确生产 apply** 后作为带来源的历史长期记忆参与正常召回；6 条不自动导入。
- 必须可由 `legacy_id -> source_message_refs -> normalized_archive` 追溯；不能只存摘要不留出处。
- 不直接循环调用有容量淘汰的 `write_episode()`、不改原有 `user_identity`、`user_profile`、`mood_state`、`short_term`、`event_log` 或剧情存档。
- 对重复或矛盾项标 `duplicate_candidate` / `conflict_review`，不自动覆盖；当前用户纠正高于旧候选。
- 如果现有 Dossier 默认关闭，不能强迫生产开启整套历史回填才能看到旧酒馆；最小可用是可靠的只读旧档案 + 正常有界历史记忆检索，Dossier 仅可增益。
- 归档长期记忆必须在正常记忆层显示 `[酒馆]`，而非未经标注的“我们真实经历过……”。

### B3｜向量索引和数据恢复

- 优先复用现有检索/Embedding 接口，不默认将私密原文出站到未获授权的远端 Embedding 服务。
- 关键能力必须有**不依赖 Embedding** 的关键词和日期检索（SQLite FTS5 或等价本地索引均可）。
- 如果向量作为可选能力：要能从档案权威重建相同来源；旧 `vector_store.rebuild()` 不能删掉已经生效的酒馆索引后不恢复，不能因此顺手删其它来源。
- 确定性索引重建后，旧酒馆可查且被撤回条目不会复活。存量 `episodic` 不触发任何容量裁剪。

## 六、角色可达召回及场景展开（C 阶段）

**不是管理员可以在 SQL 查到就算接入。叶瑄在真实聊天里的工具链必须能查。**

允许按现有风格增加 `search_archive_memory` / `expand_archive_context`（或扩展现有 `search_events` 与记忆路由）；必须注册工具 schema、权限闸、Path A/Path C 暴露、中文发现文案、回执、范围和长度预算。不得只加一层 prompt 假装能力存在。

至少支持：

- `query`、可选 `start_date/end_date`、`source=sillytavern_archive`、可选 `namespace`，受限 `limit/cursor`。
- 关键字/中文短句/来源/时间联合检索；不足时可追问但不能假装无数据。
- 按 `archive_ref` 展开同一 session/scene 内前后 N 条消息，保留 user/assistant 顺序、分段、日期精度；禁止跨故事线按时间随便拼接。
- 日期相对指代以**用户实际发问当时的本地时间**为准。例：2026-11 提问“去年 11 月”查 `2025-11-01..2025-12-01`，“今年 1 月”查 `2026-01-01..2026-02-01`。原数据没有可靠日期就不能宣称精确命中月份。
- 返回受控命中摘要 + `source_label=酒馆` + `namespace` + `archive_ref` + 日期精度 + 继续展开提示；非必要不直接带大段私密原文进通用动作日志。
- 一般聊天只在相关时有界召回 121 eligible 项；不每轮全量注入。涉及旧故事的明确提问，可沿真实引用进行多步检索。
- “找到了”的自然语言输出必须有真实工具回执支持。查到的只是近期用户重复讲旧事的片段，不能冒充真正的旧档案已找到。
- 无关主动消息、群聊、其他角色、默认通知预览不得无缘无故泄露私密故事；正确 owner 私聊、正确角色、相关语境可正常召回。

## 七、导入、回滚与运行闸门（D 阶段）

A→D 必须拆成可验证小提交；可以先完成代码与 dry-run，**生产写库永远单独授权**。

1. **预检 dry-run（默认）**：安全处理 ZIP（防路径穿越、软链接、压缩炸弹）；验证 `v4_manifest`、JSONL、ID 唯一、source refs、时间分布；只生成私有元数据统计；源缺失、校验错、混入 LOFTER 时 fail closed。
2. **恢复门槛**：对真正会修改的 scoped storage + index 做一致性备份、verify 和隔离恢复演练。旧 ZIP 不是运行数据备份。
3. **显式 apply**：要求用户/角色 ID、固定 dataset sha256、备份凭据、明确作用域与批次号。不可由自动开机、主聊天自然语言、文件 Agent 的一般文件写能力隐式开启。
4. **持久进度**：分块导入，`pending/committed/retryable_failed/pending_review/conflict/revoked` 等清晰状态；事务/receipt/检查点确保重试幂等。同 ID 同 hash 应 `skipped_existing`，同 ID 不同 hash 直接冲突，不默默替换。
5. **撤回**：能 `rollback_batch`（或等价操作），只撤销本批派生状态/可检索性，**不删原始历史、不动其它旧记忆**；支持单条更正/撤回，在索引重建后仍不召回。
6. **观测**：只读（必要 scope）显示 `expected / verified / imported / indexed / searchable / pending_review / conflicts / failed / revoked`，区分 dataset、批次、作用域、最后报错码，绝不暴露原文。
7. **部署**：运行 OrangeVPS 时不得把本地 Windows/Mac 路径映射为 VPS 路径；要检查私有资源是否在实际服务器的预期目录，授权后再传输/导入，不能因代码合并就假装生产已完成。

## 八、测试与验收（E 阶段）

使用**虚构合成 fixture**跑 CI；真实 ZIP 仅在有权限的私有环境做统计和 dry-run，不打印任何私密正文。至少覆盖：

- [ ] 预期 1712、127=121+6 的结构校验；真实包数字不符能停下来解释，而不是强行凑数；44 条 LOFTER 不被触碰。
- [ ] 老版本缺少可选字段不丢项；含成人/亲密/私人内容的 eligible 项不被主题关键词一刀切过滤。
- [ ] `real/meta/dream` 分类与显示来源正确；Dream 无 world_id 不擅自加入当前 Dream 世界；过去同意不是当前同意。
- [ ] 旧 user 原话与模型回复不混淆；新的现实用户更正对旧候选优先。
- [ ] 2025-11 与 2026-01 时间过滤精确，未知日期另列；相对年份正确解析；无数据不能凭空“找到了”。
- [ ] `search -> expand` 按同一场景顺序展开，可翻页；原文场景缺页/链接损坏能标明缺口。
- [ ] Embedding 不可用时关键词/日期检索仍可用；向量重建后旧档案依然可查；其它已有向量来源未丢。
- [ ] 121 条数据接入不引发 `episodic` 的 200 条裁剪，现有短期和其它记忆哈希/条数不意外改变。
- [ ] `uid/char/realm` 越权、错误身份、未经批准、旧会话丢失、坏 ZIP、断点/锁冲突、断电时安全失败。
- [ ] 重复 apply 幂等、同 ID 不同 hash 冲突；rollback 后正常召回、专用查询和索引重建都看不到撤回项。
- [ ] 实际模型在 owner 对话中能查历史且正确展示 `[酒馆]`，不能仅通过一个管理 API 检查通过；没跑实测则记为未验收。

建议真实人工验收问题：

> “去年十一月我们在酒馆聊过什么？”（目标为 2025 年 11 月；要真日期和来源）
>
> “今年一月那段剧情的前因后果是什么？”（2026 年 1 月；需展开前后对话）
>
> “你怎么确定那段剧情是酒馆记忆，而不是我昨天告诉你的？”（需具体源引用）
>
> “你能找到过去的亲密剧情吗？”（只在对应 owner/角色且与提问相关时引用，不自动删减成空泛概述）

## 九、最终交付给用户的报告格式

Codex 应提供：

1. **根因结论**：数据源没部署 / 只写摘要 / 原文未索引 / 检索未接 / source 被过滤 / 日期解析失效 / 其它原因；指出具体代码路径与脱敏证据，不猜测。
2. **真实数据统计**：`1712/127/121/6` 期望值与实际已核验数；引用完整率、日期分布/未知项、缺失、冲突和待审。不要将旧整理包的数字冒充当次检查结果。
3. **状态矩阵**：代码完成、合成测试、真实包 dry-run、隔离 apply、VPS 生产 apply、真实模型召回，每项分别 `passed / not_run / blocked / failed`。
4. **源码与 CI**：仓库 SHA、PR、测试命令与结果；不纳入真实私密内容。
5. **权限和恢复**：备份 verify、最近批次、apply 是否运行、rollback 入口、索引重建与撤回证明。
6. **使用说明**：如何配置私有 ZIP 来源和目标角色；在用户单独批准后如何执行生产 apply；如何在对话里搜索 `[酒馆]` 记忆。

### 施工原则

**让叶瑄能真实查到旧酒馆的完整对话，也能像正常记忆一样想起整理后的长期经历；所有记忆有来源、有时间和故事属性，不损害现存数据，能核实、能纠正、能撤回。**
