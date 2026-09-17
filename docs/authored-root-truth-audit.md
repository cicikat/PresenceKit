# PresenceKit Authored Asset Root Truth Audit

审计日期：2026-07-29

> **Snapshot / pre-consolidation.** 本文记录 C1.4 前的旧 root 审计证据，路径与数量不再描述当前发布布局。当前真值是 `bundled/`（发行内置、只读、可替换）、`userdata/`（用户私有、可写、更新保护）与 `data/`（运行时、可写、按数据策略保护）；旧 root 仅在兼容/迁移语境出现。
范围：`characters/`、`content/`、`defaults/`、`userdata/`、`examples/`，以及所有会决定这五类资产读写、播种、fallback、迁移和发行行为的当前代码。
边界：本单只读；没有删除、移动、复制、重命名文件，没有启动服务或真实 pipeline，也没有读取用户私有正文。报告只记录相对路径、类别、大小和 SHA-256。

## 结论先行

这四个 root 不是四份重复的同一套资产：

| root | 唯一裁决 | 当前职责 | 删除结论 |
|---|---|---|---|
| `userdata/` | **canonical-required** | 用户私有 authored 主根：角色卡、角色 authored 内容、现实/Dream 资产、贴纸、音视频和本地模型 | 绝对不能删；会直接丢失不可重建内容 |
| `defaults/` | **seed-required** | Git 跟踪的公共 seed/default，供现实资产和 Dream 世界骨架播种 | 不能删；fresh clone、新建 Dream 世界和冷启动依赖 |
| `characters/` | **unresolved-do-not-touch** | Git 跟踪的公开 default 角色卡、作者注池、Dream postcard 模板；同时仍是旧安装兼容读源，另有测试/导入写入 | 不能按“旧 root”整目录删除 |
| `content/` | **seed-required** | Git 跟踪的 `default` 角色配套 activity/traits；另保留旧 authored character 的 fallback API | 不能删；`default` 角色和相关测试/冷启动会失效 |
| `examples/` | **seed-required** | Git 跟踪的公开格式示例与角色卡模板；`character_template.json` 仍被 admin 新建角色流程读取 | 不能删；新建角色、示例契约测试和文档会失效 |

当前 snapshot 中没有发现旧私有 `characters/reality/`、`characters/dream_worlds/`、`characters/dream_presets/` 或 `content/characters/yexuan/` 数据。`examples/` 也没有与四个 authored root 的同内容副本。它们的“旧私有数据是否完整迁入 userdata”只能在有旧安装备份/迁移清单时证明；当前仓库没有 migration marker，也没有按资产生成的迁移报告。

最重要的代码风险不是当前四目录库存，而是 fallback 的粒度不一致：角色卡按文件/id 合并，userdata 同 id 胜出；lorebook、jailbreak、Dream world/preset 多数按“目录存在即选目录”的 first-match，空的 userdata 目录也会静默遮蔽旧目录。若旧安装只完成了目录创建而没有完成文件迁移，功能可能出现缺资源而不读 legacy 的情况。

## 1. 审计方法与证据边界

- 目录库存：递归只读扫描，统计存在性、文件数、字节数、子目录、扩展名分布、最新文件修改时间、零字节/空目录/README-only。
- Git：`git ls-files`、`.gitignore`、`git check-ignore`、当前路径历史。
- 内容比对：SHA-256、相对路径、basename、同名逻辑资产的映射比对；不输出用户正文。
- 代码：重点检查 `core/data_paths.py`、`core/asset_registry.py`、`core/sandbox.py`、`core/data_registry.py`、`core/migration.py`、character/Dream/lore/prompt/sticker/TTS/model loader、admin router、脚本和测试。
- 发行：检查 `scripts/build_release.py` 的 Git archive 行为与 `scripts/update_release.py` 的 protected roots。

当前工作树在审计前已有未提交变化：`ARCHITECTURE.md`、多份 docs、`docs/docs-truth-census.json`、`docs/docs-truth-census.md`、`test_garden_injector.py`。这些不是本单修改，报告没有覆盖或回滚它们。

## 2. 四目录库存

### 2.1 数量、Git、类型与时间

大小使用文件系统字节数；MiB 为约值。Git tracked 数只统计 `git ls-files` 返回的文件。

| root | 存在 | 文件数 | 总大小 | Git tracked | 最近修改（UTC） | 最近文件 |
|---|---:|---:|---:|---:|---|---|
| `characters/` | 是 | 11 | 9,258 B | 7 | 2026-07-24 14:30:13 | `memeval_d1a62a1531f5.json` |
| `content/` | 是 | 2 | 1,394 B | 2 | 2026-07-24 23:31:04 | `characters/default/traits.yaml` |
| `defaults/` | 是 | 8 | 1,508 B | 8 | 2026-07-18 11:30:44 | `dream_worlds/_default/lorebook.yaml` |
| `userdata/` | 是 | 112 | 332,975,044 B（约 317.6 MiB） | 0 | 2026-07-26 00:52:59 | `characters/dream/presets/审讯.md` |

`.gitignore` 明确忽略 `userdata/`，并忽略 `characters/*.json`、`characters/*.txt` 但用例外保留 `characters/default.json` 与 `characters/default_author_notes.json`；`content/characters/*/*.yaml` 默认忽略，但 `content/characters/default/*.yaml` 明确保留。`defaults/` 没有被忽略。

### 2.2 子目录结构与文件类型

`characters/`

```text
characters/
├── default.json                         tracked public default card
├── default_author_notes.json            tracked public author-note seed
├── memeval_*.json (4)                    ignored generated test fixtures
└── dream_postcards/templates/ (5 .md)   tracked runtime-read templates
```

类型：`.json` 6、`.md` 5。无空目录、无零字节文件、无 README-only 目录。没有证据表明这 11 个文件含用户私有 authored 正文；4 个 `memeval_*.json` 是测试生成物，5 个 postcard 模板和 2 个 default 文件是公开/默认资产。`memeval_*.json` 具有相同 SHA-256：`6c1f0f2591b19d66a7c0509a2c37d85b81c9d6c6f91fc63dff29e5fc4432f19a`。

`content/`

```text
content/
└── characters/default/
    ├── activity_pool.yaml
    └── traits.yaml
```

类型：`.yaml` 2。无空目录、零字节文件或 README-only 目录。两文件均为 Git 跟踪的 `default` 角色公开配套 seed；当前没有 `content/characters/yexuan/` 私人目录。

`defaults/`

```text
defaults/
├── blacklist.yaml
├── jailbreak_entries.json
├── lorebook.yaml
├── relations.yaml
└── dream_worlds/_default/
    ├── lorebook.yaml
    ├── mes_example.md
    ├── ruleset.md
    └── vocab.json
```

类型：`.yaml` 4、`.json` 2、`.md` 2。8 个文件全部 Git 跟踪；无空目录、零字节文件或 README-only 目录。它们是公共 seed/template，不是用户私有内容。

`userdata/`

```text
userdata/
├── assets/stickers/{占位,委屈,害羞,开心,心疼,无奈,沉默}/
└── characters/
    ├── cards/                         7 character cards
    ├── authored/yexuan/               notes, knowledge, letter samples, voice, local models
    ├── reality/{lorebooks,jailbreaks}/
    └── dream/
        ├── presets/
        └── worlds/{abo,cat,custom,flower_bud,reality_derived,vampire,_default,审讯}/
```

类型：`.ckpt` 1、`.jpg` 8、`.json` 23、`.md` 26、`.mp3` 1、`.mp4` 4、`.png` 16、`.pth` 1、`.txt` 5、`.yaml` 27。无空目录、零字节文件或 README-only 目录。该 root 明确含用户私有 authored 内容；报告不复制其正文、模型权重内容或私密文件内容。`_default`/世界骨架中存在 seed copy，其他 Dream/world、reality、卡片、贴纸、voice/model 文件属于用户 authored 或本机部署资产；未发现名为 runtime 的子树。

`examples/`

```text
examples/
├── activity_pool.example.yaml
├── assistant.example.json
├── benwo.example.json
├── character_template.json
├── jailbreak_preset.example.json
└── traits.example.yaml
```

6 个文件、8,614 B，全部 Git tracked；类型为 `.yaml` 2、`.json` 4，最近修改时间为 2026-07-25 07:31:04（本地时间）。无子目录、空目录、零字节文件或 README-only 目录。全部是公开示例/模板，不含用户私有 authored 内容、defaults、generated 或 runtime 数据；未发现与其他四 root 完全相同的 SHA-256 文件。

### 2.3 “是否重新创建”的初步结论

| root/子树 | 启动自动创建 | 其他创建来源 |
|---|---|---|
| `characters/` tracked 文件 | 否 | fresh clone/release 解包；`scripts/import_st_card.py` 默认写 `characters/<id>.json`；测试 fixture 写临时或真实 root |
| `characters/dream_worlds/` legacy 子树 | 不是普通启动创建；但 admin Dream `_ensure_default_world_template_seeded()` 在 userdata world root 不存在时可能创建并 seed 该 legacy 选中目录 | `admin/routers/dream.py` 的 world CRUD |
| `content/` | 否 | Git/release 提供；当前业务代码只读/fallback |
| `defaults/` | 否 | Git/release 提供；作为 seed source 被读取 |
| `userdata/` | 整体不在普通启动中预建 | admin 角色卡、Dream world/preset、现实 prompt 资产等写入点按需 `mkdir`; release updater 保护不覆盖 |
| `examples/` | 否 | Git/release 提供；admin 新建角色读取模板，其他文件主要供文档/测试/人工参考 |

发行包由 `git archive HEAD` 生成，只含 tracked 文件；因此 `userdata/` 和被 ignore 的旧私有 root 不会进入发行包。更新器把 `data/`、`userdata/`、`.venv/` 与本地配置列为 protected，更新不会覆盖 `userdata/`，也不会把 ignored legacy 私有文件重新解包回来。

## 3. 代码引用审计

下表按代码责任归并直接引用；仅 UI label、注释和历史文档命中不作为运行时 reader。括号内为当前源码证据范围。

### 3.1 路径核心与 registry

| 文件/函数 | root | 操作 | 类型/优先级 | 失败行为 | 主运行链 |
|---|---|---|---|---|---|
| `core/data_paths.py` `user_*`/`legacy_*` accessors | `userdata`、`characters`、`content` | read path / write target selection | C1 primary + legacy path provider | 返回路径，不保证存在 | 是 |
| `DataPaths.stickers_dir()` | `userdata/assets/stickers`、`assets/stickers` | read | userdata dir exists 即优先，否则 legacy | 可能返回不存在的 selected path，调用方空读 | 是 |
| `character_card_dirs()` + `AssetRegistry._scan_characters()` | `userdata/characters/cards`、`characters` | read/scan | 两目录合并；legacy 先扫描、userdata 后覆盖同 id | 不存在目录跳过 | 是 |
| `authored_character_dir()` | `userdata/.../authored/{char_id}`、`content/characters/{char_id}` | read/write path | 目录级 first-match | userdata 目录存在但缺文件时不继续找 content | 是 |
| `activity_pool()` / `yexuan_traits()` | userdata、content、`data/` fallback | read path | 文件级：userdata > content > data fallback | 返回 data fallback；调用方异常时空结果 | 是 |
| `author_notes_pool()` | userdata、content、`characters` | read path | 文件级：userdata `author_notes.json` > content `{char}_author_notes` > legacy root `{char}_author_notes` > `characters/default_author_notes.json` | default pool missing 时 loader 抛 FileNotFoundError | 是 |
| `letter_samples_dir()` / `letter_knowledge_dir()` | userdata、content | read path | 目录级 userdata > content | selected dir 缺文件时不 merge | 是 |
| `_reality_p()`、`jailbreak_entries()`、`lorebook()` | userdata reality、legacy `characters/reality`、`defaults` | read/write/copy | 文件级：user file > legacy file；二者都无则返回 user target 并从 defaults `copy2` seed | seed source 缺失则 error log，仍返回目标路径 | 是 |
| `lorebooks_dir()` / `jailbreaks_dir()` | userdata reality subdir、legacy reality subdir | read/write path | 目录级 first-match | user dir 存在即不看 legacy | 是 |
| `dream_worlds_dir()` / `dream_presets_dir()` | userdata Dream、legacy Dream | read/write path | 目录级 first-match | user dir 存在即不看 legacy | 是 |
| `default_dream_world_template_dir()` | `defaults/dream_worlds/_default` | read seed source | 唯一 tracked template source | source file 缺失则只跳过该文件 | 是（admin CRUD） |
| `jailbreak_presets_dir()` | `content/jailbreak_presets`、`data/jailbreak_presets` | check/read path | legacy/dead accessor；非当前 Dream preset registry 路径 | fallback to data | 否/待清理 |
| `core/migration.for_read()` | 任意新/旧路径 pair | read/check | 新文件非空且可解析，否则旧文件 | 计数、记录 fallback，不复制/移动 | 被少量兼容调用方使用；不是 C1 copy migration |
| `core/asset_registry.py` compatibility constants/`AssetEntry.path()` | `characters` legacy subtrees | read fallback/direct path | 兼容构造；scanner 实际用 DataPaths | unknown asset id `ValueError` | 是，部分仅扩展/测试兼容 |
| `admin/routers/character.py` `create_character()` | `examples/character_template.json` | read | public template source；不是 authored asset fallback | 模板不存在则 `HTTPException(500)` | 是 |

### 3.2 角色、现实 prompt、Dream、admin 与输出

| 文件/函数 | root 相关操作 | 事实分类 |
|---|---|---|
| `core/character_loader.load()` | 经 `AssetRegistry` 读 character card | 主链 canonical reader；无静默默认卡 fallback，unknown/missing fail-loud |
| `core/activity_manager._load_pool()` | 通过 `DataPaths.activity_pool()` 读；另以 `userdata/...`、`content/...` 做 fallback 检查日志 | 主链 reader；存在一个实现缺口：对非默认 char 只记录“fallback 默认池”，没有把 `pool_path` 改成默认池，最后仍打开 `DataPaths` 返回路径 |
| `core/author_note_rotator.get_current_note()` | 通过 `author_notes_pool()` 读，向 `data/runtime` 写轮换状态；fallback 到 tracked default author notes | 主链 reader + runtime writer，旧 root 只读意图明确 |
| `core/lore_engine.LoreEngine.load()` | 读 selected `lorebooks_dir/{stem}.yaml`，按 enabled 顺序合并 | 主链 reader；文件不存在跳过，解析失败记录并继续 |
| `core/prompt_builder._load_jailbreak()` | 读 selected modular `jailbreaks/{stem}.json`，再读 combined `jailbreak_entries()` | 主链 reader；两来源 merge，`content.strip()` 去重；不是 root 间 merge |
| `core/dream/world_loader` | 读 selected worlds root；world 缺失回 `reality_derived`；字段缺失回同 root `_default` | 主链 Dream reader；缺失/损坏 field fail-open 到空/default |
| `core/dream/scene_label_loader`、`symbolic_loader`、`hud_label_loader` | 读 selected world package；symbolic 缺 world profile 时回 selected root `anchor_weights.json`，scene label 回内建文案 | 主链 reader；不是直接读 defaults |
| `core/dream/dream_pipeline._load_preset_text()` | Registry resolve selected preset；缺失回 selected `default.md`，仍缺则 D0 disabled | 主链 reader；legacy 仅通过 selected dir fallback 进入 |
| `core/dream/postcard.py` | 直接读 tracked `characters/dream_postcards/templates/{id}.md` | 主链 reader；这是模板职责，不是 C1 私有 Dream root |
| `admin/routers/character.py` `_safe_path`/角色 CRUD | 读 userdata card；文件已在 legacy 才返回 legacy；新建/上传/保存通常写 userdata，但已有 legacy 同名文件会被写回 legacy | 主链 admin writer；fallback 尚非严格 read-only |
| `admin/routers/dream.py` preset/world CRUD | 读写 `dream_*_dir()`；userdata dir 存在时写 userdata，否则旧安装可能继续写 legacy；新 world 的 `_default` 从 defaults `copy2` 到 selected dir | 主链 admin writer/bootstrap |
| `admin/routers/settings_prompt_assets.py` avatar endpoints | runtime avatar read/write/delete；authored avatar read顺序 userdata reality > legacy reality | 主链 reader/writer；上传写 `data/runtime`，不改 authored root |
| `admin/routers/lorebook.py`、`jailbreak_entries.py` | 读写 `DataPaths.lorebook()` / `jailbreak_entries()` | 主链 admin writer；legacy file selected 时会写 legacy |
| `core/output/sticker._pick_sticker()` | 读 userdata sticker pack/common pool，目录不存在才 legacy `assets/stickers` | 主链 reader；不读四 root 中的 `characters/content/defaults` |
| `core/output/voice_adapter` | 读 config 的 `tts.ref_audio`、model paths，项目内相对路径锚到 repo root；当前 config/example 指向 userdata | 主链 reader；无四 root fallback，仅扩展名/同 stem 音频 fallback |
| `core/model_registry`、`admin/routers/settings_llm.py` | 读 config `model_presets` / per-character routing | 主链 reader；没有 `defaults/` 或 `characters/` preset directory reader |
| `tests/test_assistant_example.py`、`tests/test_authored_assets.py` | 读 `examples/*.json` 及模板 | test fixture/contract；不是生产 authored reader | 测试失败或契约不成立 |
| `docs/*`、`README*`、`docs/c1-root-asset-inventory.md` | 引用 `examples/` 路径和职责 | docs/example；不构成生产读写 | 不影响运行时 |

### 3.3 脚本、测试、文档与 dead/archive

| 引用 | 分类 | 影响 |
|---|---|---|
| `scripts/import_st_card.py` 默认输出 `characters/<id>.json` | canonical write（旧 CLI） | 仍可能把新导入卡写回 legacy root；显式 `--out` 可绕过 |
| `scripts/update_release.py` `PROTECTED_ROOTS` | check/protect | 不覆盖 `userdata`；不创建/迁移四 root |
| `scripts/build_release.py` `git archive HEAD` | package source | 只打包 tracked `characters/content/defaults`，不打包 userdata/ignored legacy private files |
| `tests/test_user_asset_paths.py` | fixture + priority contract | 验证 userdata card 胜 legacy、缺失时 fallback legacy |
| `tests/test_authored_assets.py` | tracked seed contract | 验证 defaults 存在/可解析、characters 根不含 template/example 文件 |
| `tests/memeval/engine.py`、`tests/conftest.py` | generated/test fixture write | 会在真实 `characters/*.json` 写短期测试卡并清理；当前 4 个 `memeval_*` 是 ignored generated residue |
| `tests/test_character_new.py` | fixture + canonical write assertion | admin 新建角色的目标断言是 `userdata/characters/cards/` |
| `tests/test_dream_world_fallback.py` 等 | temporary legacy fixture | 只测试旧 `characters/dream_worlds` fallback/field fallback；不证明生产旧目录仍有数据 |
| `docs/data-taxonomy.md`、`docs/dream.md`、`docs/c1-root-asset-inventory.md` | current/supporting docs | userdata-first、defaults seed、legacy fallback；代码对 first-match/write-back 的事实优先 |
| `docs/tools.md`、`docs/docs-truth-census.*` | docs/example + known drift | census 已指出部分示例仍提示 `characters/`；不能作为当前 writer 证据 |
| `docs/archive/opensource-v0.1-checklist.md`、`core/paths.py` | archive / explicit experimental | `core/paths.py` 生产零引用；删除或真正迁移另授权，不是静默遗留 |

## 4. 真实读取/写入优先级

下面是从当前实现归纳出的实际顺序，不是依据文档猜测。

### 4.1 Character card

```text
1. userdata/characters/cards/*.json|*.txt|*.md
2. characters/*  legacy/public/default scan
```

scanner 先把 legacy 结果放入 dict，再扫描 userdata 覆盖相同 `id`；所以同 id 时 userdata 胜出。不同 id 会合并出现在 registry。`characters/default.json` 是 tracked default card，可与 userdata cards 并存。写入时 admin 新卡默认 userdata；但 `_safe_path()` 对已经存在的 legacy 同名文件会返回 legacy，因此旧安装上的编辑/上传不是严格的 read-only fallback。`scripts/import_st_card.py` 默认仍直接写 legacy。

### 4.2 Reality prompt assets

组合文件：

```text
1. userdata/characters/reality/lorebook.yaml
2. characters/reality/lorebook.yaml       legacy fallback
3. defaults/lorebook.yaml                  仅当 1、2 都缺失时 copy2 seed 到目标
```

`jailbreak_entries.json` 同序，对应 `defaults/jailbreak_entries.json`。同名时 user 文件获胜；不是 merge。admin 写回 `DataPaths` 选中的文件，因此 legacy 文件存在而 user 文件缺失时仍可能写 legacy。

拆分文件：

```text
1. userdata/characters/reality/lorebooks/     directory first-match
2. characters/reality/lorebooks/              仅当 1 不存在
```

`jailbreaks/` 同序。目录内按 active ids 逐文件读；selected directory 内没有的文件不会继续从另一个 root 补齐。两种拆分源与 combined entries 源在 `prompt_builder` 内按启用/layer 过滤后合并，并按内容去重；这是同一 selected root 内的两种存储，不是 `userdata` 与 legacy 的跨-root merge。

### 4.3 Dream world

```text
1. userdata/characters/dream/worlds/       若目录存在，整体选中
2. characters/dream_worlds/                仅当 1 不存在
3. selected root/{world_id}/
4. selected root/reality_derived/           world 目录缺失时
5. selected root/_default/{field}           单字段缺失/空时
6. defaults/dream_worlds/_default/{field}   仅 admin 新建流程先 copy 到 selected root/_default
```

`world_loader.load_world()` 不会直接从 `defaults/` 读；fresh clone 的 `POST /dream/worlds` 由 `_ensure_default_world_template_seeded()` 把 defaults copy 到 selected world root，再复制给新 world。当前 userdata world root 已存在，因此 admin world write 当前落 userdata。若旧安装没有 userdata world root，当前 `dream_worlds_dir()` 会把 legacy 目录作为返回值，CRUD 可在 legacy 上创建/修改。

world 的 `symbolic_profile.yaml` 优先于 selected root 的 `anchor_weights.json`；scene labels 缺失时回内建常量。当前 userdata `_default` 与 defaults 的规则/lorebook 已发生分叉，因此不能以“同名默认文件”判断它仍是纯 seed。

### 4.4 Dream preset

```text
1. userdata/characters/dream/presets/*.md       若目录存在，整体选中
2. characters/dream_presets/*.md                仅当 1 不存在
3. requested preset id/file
4. selected root/default.md                       requested 缺失时
5. D0 disabled                                   default 也缺失/为空
```

Registry 对 preset 是目录级 first-match，中文 stem 还需 `_DREAM_PRESET_ID_MAP`；unknown id 的 admin path helper 会在 selected dir 拼 `{id}.md`。写入同样写 selected dir，不是自动复制到 userdata。

### 4.5 Dream scenario

scenario 已收口到 userdata-first authored 分层：

1. `userdata/characters/dream/scenarios/{id}.yaml`：canonical write/read；
2. `data/dream/scenarios/{id}.yaml`：历史只读 fallback。

同 ID 时 userdata 胜出；admin 编辑 legacy-only 剧本会在 userdata 创建覆盖副本，绝不改写旧文件。legacy-only 剧本不可直接删除。`scenario_loader` 与 admin CRUD 共用 `DataPaths.dream_scenario_read_dirs()`，缺失或 schema 错误仍 fail-loud。

### 4.6 Avatar

```text
1. data/runtime/characters/{char_id}/avatar.{png|jpg|jpeg|webp}  runtime override
2. userdata/characters/reality/avatars/{char_id}.png              authored
3. characters/reality/avatars/{char_id}.png                       legacy authored fallback
4. 404 / avatar_url=None
```

上传/删除只写删 runtime override；删除后回到 2/3。当前四 root 库存没有 `avatars/` 文件。

### 4.7 Lorebook、sticker、TTS、model preset 与其他 AssetRegistry 资源

| logical asset | 实际顺序 | merge/写回 |
|---|---|---|
| reality lorebook | user combined file > legacy combined file > defaults seed | first file wins；admin 写 selected file |
| modular lorebook | user directory > legacy directory | directory first-match；enabled files在目录内按顺序 merge |
| modular jailbreak | user directory > legacy directory；另与 combined entries 读源合并去重 | directory first-match；admin combined entries 写 selected file |
| sticker common pool | userdata common dir > `assets/stickers` legacy | directory first-match；没有 copy-back；角色 pack 先 user `stickers_packs/{pack}`，缺情绪回 common |
| TTS assets/config | `config.yaml` 的全局 tts 或 `presence_ext.tts_preset` overlay；当前路径直接指向 userdata | 无四-root fallback；音频仅扩展名/same-stem fallback |
| model presets | `config.yaml.model_presets` > provider/default parameter merge；角色 `presence_ext.model_routing` 选择 profile | 无 filesystem root merge；模型文件路径当前由 config 指向 userdata |
| character card | userdata cards + legacy characters 合并，same id userdata wins | scanner-level merge；admin normally user, legacy existing-file caveat |
| Dream preset | userdata preset dir > legacy preset dir；requested > default.md | directory first-match；不读 defaults preset |

### 4.8 Examples

`examples/` 不是 runtime authored asset resolver 的候选 root，也不参与 userdata/legacy/defaults 的优先级竞争。唯一确认的主运行链读取是：

```text
admin create-character:
1. examples/character_template.json
2. 写入 userdata/characters/cards/{char_id}.json
```

`assistant.example.json` 由测试读取；`benwo.example.json` 由文档/示例引用；`activity_pool.example.yaml`、`traits.example.yaml`、`jailbreak_preset.example.json` 是公开格式参考。它们不会自动复制到 userdata，也不会作为生产 fallback 被 loader 扫描。

## 5. 重复与冲突审计

### 5.1 路径集合

- 四个 root 之间没有相同的 root-relative path collision。
- `examples/` 与其他四个 root 没有相同相对路径，也没有 SHA-256 exact duplicate；同名示例只是格式参考，不是迁移副本。
- `characters/` 的 4 个 `memeval_*.json` 是同内容 generated test fixtures，不是四 root 迁移副本；它们均被 `.gitignore` 忽略但当前尚在磁盘。
- 当前旧私有 legacy 子树不存在，因此没有发现“legacy 私有文件仍在、userdata 对应文件缺失”的当前条目。

### 5.2 exact duplicate（SHA-256 完全相同）

这些是内容层 exact duplicate；是否可删除仍要看它们是否为 seed copy，不能按 hash 直接删：

| SHA-256 | 位置/解释 |
|---|---|
| `364b37738032a28a72829f4d2192b70bd9353c9ee8dddfc5b70c2785f995d6ac` | `defaults/jailbreak_entries.json` 与 userdata reality 下 3 个空/占位式 modular jailbreak 文件；basename/逻辑源不同，属于 seed/空壳重复，不是同路径迁移证明 |
| `26d1c40ebd685d7627e65fdb4e05faf57c791bb4e7499e17ea12e2b560f40286` | `defaults/lorebook.yaml` 与 userdata reality 下 2 个同内容 modular lorebook 文件；属于空 seed copy |
| `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` | `defaults/dream_worlds/_default/{lorebook.yaml,vocab.json}` 与 userdata 多个 world 的 `vocab.json`、部分 `_default`/审讯 lorebook；属于 seed/空 vocab 复用 |
| `dca216ab33b4ba594ed113e90906e29585490bae39fd2b1c187d0cc9f6bc7a1b` | defaults `_default/mes_example.md` 与 userdata `_default/mes_example.md`、`审讯/mes_example.md`；符合 Dream 骨架 copy |
| `6c1f0f2591b19d66a7c0509a2c37d85b81c9d6c6f91fc63dff29e5fc4432f19a` | `characters/memeval_*.json` 4 份；generated test residue |

### 5.3 diverged copy / expected override

| logical comparison | result | 判定 |
|---|---|---|
| `defaults/lorebook.yaml` → `userdata/characters/reality/lorebook.yaml` | defaults hash `26d1...`; userdata hash `855b6f...` | **expected seed override / user-authored divergence**；userdata 是实际 prompt source |
| `defaults/jailbreak_entries.json` → userdata combined entries | defaults hash `364b...`; userdata hash `c4c2...` | **expected seed override / user-authored divergence**；不能覆盖 userdata |
| Dream defaults `_default/ruleset.md` → userdata `_default/ruleset.md` | `51200f...` → `14cae2...` | **diverged copy**；可能是用户编辑或迁移后的版本，不能按 default 删除 |
| Dream defaults `_default/lorebook.yaml` → userdata `_default/lorebook.yaml` | `37517e...` → `766537...` | **diverged copy**；userdata `_default` 已不是纯空 seed |
| Dream defaults `_default/mes_example.md`、`vocab.json` → userdata `_default` | exact | **expected seed copy** |
| `content/characters/default/activity_pool.yaml` vs `userdata/characters/authored/yexuan/activity_pool.yaml` | `075eef...` vs `ff508c...` | **不同角色的同 basename，不是同逻辑冲突** |
| same basename `lorebook.yaml` / `ruleset.md` / `vocab.json` across different Dream worlds | 多数 diverged | **expected world-scoped authored content**，不能合并 |

### 5.4 unresolved / silent shadowing

当前磁盘没有 legacy 私有副本，所以不能观察到实际 legacy-vs-user 内容冲突；但代码已经确定以下 unresolved 风险：

1. `userdata/characters/reality/lorebooks/` 只要存在，即使为空，`characters/reality/lorebooks/` 就不会被扫描。
2. `userdata/characters/reality/jailbreaks/`、`userdata/characters/dream/worlds/`、`userdata/characters/dream/presets/` 同样是目录级遮蔽。
3. `authored_character_dir()`、`letter_samples_dir()`、`letter_knowledge_dir()` 也是目录级 first-match；userdata 目录中缺一个文件不会逐文件回 legacy。
4. 角色卡例外：scanner 会合并两边，同 id 由 userdata 后扫描结果覆盖；这是当前唯一明确的 file/id-level merge。
5. defaults 与 userdata 的 expected seed override 不是 unresolved conflict；但缺少 marker，无法证明具体 userdata `_default` 文件是自动 seed、手动编辑还是历史迁移复制。

## 6. 创建来源与迁移状态

### 6.1 来源判定

| 来源 | `characters` | `content` | `defaults` | `userdata` | `examples` |
|---|---|---|---|---|---|
| Git 仓库本身 | 7 tracked public/default/template files | 2 tracked default files | 8 tracked seed files | 否，0 tracked | 6 tracked public examples/templates |
| startup/bootstrap | 不创建 tracked root；legacy Dream 子树可被 admin seed helper 按需创建 | 否 | 否 | 普通启动不整体创建；写入点按需创建 | 否 |
| migration code | 无 C1 copy/move routine；只保留 fallback readers | 同左 | seed source | canonical target only by normal writers | 无迁移角色 |
| admin settings/CRUD | character legacy existing-file caveat；Dream/reality selected-dir writer | 无当前 writer | 只读 seed source | 角色卡/Dream/现实 authored 主要 writer | 只读 `character_template.json` |
| updater/release | release 从 tracked Git archive；不打包 ignored private roots | tracked package | tracked package | protected，不覆盖 | tracked package |
| tests | `characters/*.json` fixture/test residue；临时 Dream legacy trees | temp fixtures/contract | existence/loadability contract | canonical target assertions/真实 world read fixture | example/contract tests |
| 历史遗留但当前无人创建 | `characters/default*` 不是历史垃圾；legacy private subtrees当前不存在 | `content/default` 仍有职责 | 仍在主链 | 不是历史遗留，是当前主根 | 不是历史遗留；由 Git 保留，`character_template` 仍在主链 |

### 6.2 迁移 marker、版本和 dry-run

- 未发现专门的 authored-root migration marker、schema/layout version、per-file manifest、迁移完成标记或 cleanup marker。
- `_LAYOUT_CHARACTER_INNER`、`_LAYOUT_REALITY`、`_LAYOUT_DREAM` 是 runtime/data layout 常量，均断言为 `v1`；不是 authored root C1 完成标记。
- `core/migration.py` 的 `for_read(new, old)` 只检查新文件是否存在、非空、可解析；失败返回 old 并增加 fallback 计数/观测。它不 copy、不 move、不删除旧文件。
- `scripts/migrate_data_v1.py` 是 `data/` runtime/memory 迁移工具，不是四个 authored root 的 C1 迁移工具。
- `scripts/migrate_uid_only_memory_dry_run.py` 也是 `data/` memory dry-run，不覆盖本单四 root。
- Git 历史中的 `3b9b55c feat: complete DataPaths migration and registry rollout` 主要提交路径治理、fallback、registry、defaults 和测试；没有显示对用户私有四-root内容执行统一 copy/move 的代码。`docs/c1-root-asset-inventory.md` 把 C1 记为已完成，但当前代码证据只能证明“新布局与 fallback 已接线”，不能证明每个旧安装的数据已被逐项迁移。

### 6.3 对六个迁移问题的回答

1. **旧目录数据是否完整进入 userdata？** 当前仓库 snapshot 的 legacy 私有子树为空/不存在，无法从现状证明历史安装完整迁移；只能确认当前 userdata 已有一套 substantial authored 库。
2. **复制还是移动？** 没有 C1 copy/move migration implementation；当前 defaults→userdata Dream seed 是 `copy2`，不是旧用户资产迁移。历史 C1 更像人工/部署层迁移加代码切换，缺少逐文件证据。
3. **是否 legacy 有而 userdata 缺？** 在本次四-root当前扫描中，未发现旧私有 legacy 条目；`characters/default*`、`content/default/*` 属公共 seed，不应要求镜像到 userdata。`userdata` 的 `yexuan/traits.yaml` 缺失且 content legacy yexuan 也缺失，代码继续回落 `data/yexuan_traits.yaml`。
4. **是否 userdata 与 legacy 不同？** 当前没有 legacy 私有副本可比；发现的 defaults↔userdata 分叉见 §5.3，属于预期 seed override 或未决迁移来源。
5. **是否当前代码仍可能只从 legacy 读？** 是：postcard templates 直接读 tracked `characters/`；legacy character card/author notes/content authored/Dream/reality fallback 仍可被选中；`scripts/import_st_card.py` 默认写 legacy；`AssetEntry.path()` 有兼容直接构造路径。
6. **删除旧目录是否会真实损坏？** 整个 root 会：`characters/` 的 tracked default/postcard 模板和 fallback/API/test/import 仍依赖；`content/` 的 default activity/traits 仍是 tracked 配套；`defaults/` 是 seed source。仅当前不存在的 legacy 私有子树若先完成外部备份、验证 userdata 覆盖并禁止 legacy write，才可单独评估清理。

## 7. 最终裁决与删除风险

| root | current writers | current readers | bootstrap creator | migration role | duplicate state | deletion safety |
|---|---|---|---|---|---|---|
| `userdata/` | admin character CRUD；Dream world/preset CRUD；现实 prompt CRUD 在 selected user path；其他 authored writers | character registry/loader、activity/author notes/traits/letters、reality prompt、Dream loaders、sticker、TTS/model config | lazy per writer；不是普通启动 bootstrap | C1 canonical target | 大量 expected seed copies与用户分叉；当前无 legacy private pair | **极不安全；数据不可重建** |
| `defaults/` | 无业务 writer | `_reality_p` seed、`jailbreak_entries()`、`lorebook()`、Dream default template、tracked asset tests | 不创建自己 | public seed source | 与 userdata 有 exact seed copy、diverged override | **不安全；fresh clone/新建 world/冷启动受损** |
| `characters/` | `scripts/import_st_card.py` 默认 writer；admin 旧文件 fallback writer；tests writer；Dream legacy seed helper可建子树 | registry/loader legacy scan、author notes fallback、postcard direct reader、legacy reality/Dream fallback | 不普通启动创建；Dream helper在缺 user root 时可创建 legacy Dream subtree | public default + compatibility source | tracked public files + generated test residue；当前无 legacy private subtrees | **整 root 不安全；子树需分层评估** |
| `content/` | 当前无明确生产 writer；测试 fixture可能写 temp | `activity_pool`/traits/author notes/letters/knowledge fallback；`default` activity 配套 | 否 | public default + migration fallback source | 与 userdata 同 basename但多为不同角色/不同逻辑；无当前 yexuan legacy data | **root 不应删；default 配套仍需保留** |
| `examples/` | 无业务 writer；Git/release source | `admin/routers/character.py` 读取 `character_template.json`；测试/文档读取其他示例 | 不创建、不 seed | public example/template source；无用户迁移角色 | 与其他 root 无相对路径或 SHA-256 exact duplicate | **不能删；新建角色和契约测试受损** |

### 7.1 当前绝对不能删的目录

- `userdata/`：当前私有卡片、Dream/reality authored、贴纸、音视频和本地模型都在这里。
- `defaults/`：受 `DataPaths` 和 Dream world admin seed 直接读取。
- `characters/`：至少 `default.json`、`default_author_notes.json`、`dream_postcards/templates/` 是 tracked/current readers；不能把“私有 legacy 子树当前为空”扩大成“根目录可删”。
- `content/`：`content/characters/default/` 是随仓库发布的 default 角色配套；删除后 default 角色的 activity pool 读取、fresh clone 语义和 authored asset contract 会改变。
- `examples/`：`character_template.json` 仍被 admin 新建角色读取，其余文件由测试/文档/人工参考使用；不能与已迁移的私有 legacy 子树混为一谈。

## 8. 后续建议（只提出，不执行）

### 推荐顺序：B → C → F，最后才评估 E/D

**B. 停止新写入，保留 read fallback**：先修正 `_safe_path()`、`DataPaths` selected-dir writer 和 `scripts/import_st_card.py`，让任何 legacy 命中只读并把新写入固定到 userdata；增加日志/观测记录“fallback source”和“write target”。这是收敛前置条件。

**C. 加一次性迁移后只读**：增加 authored-root migration manifest/schema version、dry-run/report、逐文件 SHA-256 对账和冲突策略；迁移顺序必须是 userdata 缺失才复制，冲突不覆盖用户文件，完成后 marker 才允许 legacy read-only fallback。必须覆盖 cards、authored char dirs、reality、Dream worlds/presets、stickers，以及 config 中 TTS/model paths。

**F. 发行包中保留，但运行时不使用**：对 `characters/`/`content/` 中确实是公开 seed/template 的文件保留；`examples/` 全部作为公开格式示例保留，其中 `character_template.json` 仍是运行时模板 source；把 legacy private subtree 从新发行包排除，更新器继续保护 userdata。`characters/default*` 与 postcard templates 不能随 legacy private subtree 一起删。

**D/E 暂不建议直接做**：当前没有 per-install 完成标记，也没有可靠的冲突/备份证明。若未来要归档或删除旧私有子树，前置条件必须包括：

1. 备份整个 `userdata/` 与旧 legacy authored 子树，保留带 SHA-256 的 manifest；不把正文写入日志。
2. dry-run 输出 missing / exact / diverged / unresolved 清单，并人工确认 diverged 条目。
3. 禁止旧路径写入，验证新安装、旧安装升级、空 userdata、空但存在的 userdata 目录四类场景。
4. 运行 focused priority/CRUD/Dream/reality/sticker/TTS tests；再做一次真实旧安装升级验收，不能只用静态 grep。
5. rollback 通过恢复备份和重新启用 legacy read-only fallback；删除动作本身应先移到显式 archive/回收目录，而不是不可逆递归删除。

建议拆成后续工单：

1. **C1.1 writer gate**：所有 admin/import writer 固定 userdata，legacy fallback 变 read-only。
2. **C1.2 file-level resolver**：为 authored character、letters、lorebooks、jailbreaks、Dream worlds/presets 统一逐文件/逐资源优先级，消除空目录 silent shadowing。
3. **C1.3 migration dry-run**：manifest、marker、冲突分类、只读报告和 rollback。
4. **C1.4 legacy cleanup**：仅针对完成 manifest 的旧安装；分开处理 tracked public seed 与 private legacy subtree。
5. **C1.5 docs/tests truth sync**：清理仍把 `characters/` 写入描述成 canonical 的示例/旧文案，并增加“writer target + fallback hit”契约测试。

## 9. 验证记录

- 四 root 文件扫描、SHA-256、Git inventory：已完成，只读。
- 未启动 `main.py`、`run_test.py`、admin server 或真实 Dream/LLM 流程；没有触碰生产 `data/` 或 `userdata/` 内容。
- 没有删除、移动、复制、重命名任何文件。
- `git diff --check`：在报告写入后执行。
- 工作树验证：在报告写入后确认除本报告新增外，没有其他文件被本单修改；现有未提交变化按基线保留。

## 10. 本次审计提交

实际 commit hash 由 Git 在提交时生成，并在本次任务交付结果中报告。该 commit 只包含本文件，不包含审计前已经存在的未提交变化。

## Brief 158 update

TTS reference audio and model packages now use canonical `userdata` character
voice directories through `DataPaths`. The admin API returns only logical IDs,
source provenance, and bounded metadata; it does not expose local filesystem
paths. Live2D and 3D package uploads are stored and validated server-side but
remain explicitly `partial` until a desktop consumer contract is implemented.
