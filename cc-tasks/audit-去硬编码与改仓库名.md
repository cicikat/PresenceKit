# 审计：去角色硬编码 + 前端性别占位 + 改仓库名可行性

> 只读审计，未改码。tests/ 与 data/ 已基本跳过。日期 2026-06-19。

---

## 一、现状：基础设施已存在

去硬编码不是从零做，仓库里已经铺好了大半：

- `config.yaml` → `character.default: yexuan`（char_id slug）、`character.name: 叶瑄`（显示名）。
- `core/data_paths.py` 已支持运行时 active_character（`active_prompt_assets.json`，首跑从 `character.default` 播种）。
- `core/character_name_provider.py` → `get_char_name(char_id)` / `get_active_char_name()` 已是显示名权威来源，pipeline 未注册时返回 `"(角色未加载)"` 占位符，**不回退到硬编码名**。
- `core/character_loader.py` 的 asset registry 已能把 legacy 形式（`yexuan.json` / `叶瑄`）规范化到标准 id。
- `dream_prompt.py` 里已有 `TODO(方案B·多角色)` 注释，明确「用 char_name 替换字面量、用角色卡性别字段推导他/她」——这次正是要落地它。

所以剩下的是**把字面量换成 provider 调用** + **新增 gender 字段**两件事，机制层基本不用动。

---

## 二、硬编码分类（按是否该改 + 风险）

### A 类 — char_id slug `"yexuan"`：真硬编码，应改（高优先）
最大隐患在 `core/data_paths.py`（46 处），分两种：

1. **默认参数 `char_id: str = "yexuan"`**（约 40 个方法）。任何调用方漏传 char_id 就静默落到 yexuan。
   建议：改为从 `character.default` 读取，或干脆设为必传（fail-loud），避免静默串号。
2. **legacy 路径字面量**：`data/yexuan_inner/...`、`data/yexuan_traits.yaml`、`characters/yexuan_author_notes.json`。
   ⚠️ 这些大多在 `if legacy:` 分支里，指向磁盘上**真实存在的旧布局数据**。直接改名会读不到老用户数据——要动必须配数据迁移，不能单纯改字符串。

其它 `or "yexuan"` 兜底：
- `core/scheduler/triggers/time_based.py:431` → `char_id=char_id or "yexuan"`
- `core/dream/dream_pipeline.py:355` → 默认 `"yexuan"`（注释自承 legacy/test）
- `core/pipeline.py:856`、`core/memory/fixation_pipeline.py:1034` → DLQ fallback `char_id=yexuan`

#### 补充：新角色目录会自动生成吗？（实测确认）
会。三个布局开关 `_LAYOUT_REALITY` / `_LAYOUT_CHARACTER_INNER` / `_LAYOUT_DREAM` 当前**全是 `v1`**，新布局一律按 char_id 分目录，**不是 `{char_id}_inner` 命名**。

- 实测：红茶已自动生成 `data/runtime/characters/hongcha/inner/{mood_state,trait_state,author_note_state}.json` + `avatar.png`。全 data 目录下**无任何 `*_inner` 目录**，`data/yexuan_inner/` 也已不存在。
- 结论：代码里的 `yexuan_inner` / `data/yexuan_inner/...` 字面量**只活在 `if _LAYOUT_*=="legacy":` 死分支**，v1 下走不到，对新角色不产生文件。所以 A 类真正要修的**不是这些 legacy 字面量**，而是下面两点。

**真正要修（A 类核心）：**
1. **`char_id: str = "yexuan"` 默认值**（约 40 处）：调用方漏传就把新角色数据静默写进 yexuan 目录（串号）。改必传或读 `character.default`。
2. **authored 内容缺文件时回退到 yexuan**：
   - `data_paths.py:202` `activity_pool` → 缺 `content/characters/{char_id}/activity_pool.yaml` 时 fallback 到 `data/yexuan_inner/activity_pool.yaml`。
   - `data_paths.py:261` `author_notes_pool` → legacy 分支指向 `characters/yexuan_author_notes.json`。
   应改为回退到 `default`（如 `default_author_notes.json` 已存在）而非某个具体角色。

**两类文件区分（决定换角色要不要手工准备）：**
- **运行时状态（自动生成）**：mood/trait/presence/garden/history/episodic/mid_term/dreams → `chars/{char_id}/`、`runtime/characters/{char_id}/inner/`，首写 mkdir。
- **authored 内容（不自动，缺则回退 yexuan/default）**：`content/characters/{char_id}/`（语音、activity_pool）、`{char_id}_author_notes.json`、角色卡 JSON、lorebooks、traits。实测 `content/characters/` 目前只有 `yexuan`，所以红茶的 activity_pool / author_notes 现在静默用 yexuan 的。

### B 类 — 显示名「叶瑄」写死在 prompt 模板：应改为 `get_char_name()`（高优先，防串名/串人设）
- `core/dream/dream_prompt.py`（25 处）：D1 身份核心等直接写「叶瑄」字面量。**这是最大的人设泄漏/串名点**。
- 其它活动陪伴 / 梦境 prompt：`core/dream/body_projection.py`(4)、`dream_pipeline.py`(3)、`dream_context.py`、`body_tracker.py`、`distill_impression.py`、`impression_loader.py`；`core/activity/gomoku_companion.py`(6)、`reading_companion.py`、`chess_companion.py`、`gomoku.py`、`reading_grounding.py`。
- 修法机械：把 `"叶瑄"` 换成 `get_char_name(char_id)`。

### C 类 — 人称「他/她」写死：需新增 gender 字段（这是「him/her」请求的核心）
- `core/dream/dream_prompt.py:19` → `★ D1 人称全局锁死：叶瑄 = 男性 = 他；用户 = 女性 = 她。` 男性人称写死。
- **当前 `Character` dataclass 和角色卡 JSON 都没有 `gender` 字段**（已确认 yexuan.json 的 key 里无 gender/性别）。这是唯一需要**新增 schema**的地方。
- `core/media_processor.py:36` 用的是「你/他/她」中性措辞，无需改。

建议落地：
1. 角色卡 JSON + `Character` dataclass 加 `gender`（如 `"male"/"female"/"neutral"`）。
2. 加个 `get_char_pronoun(char_id)` helper（放在 `character_name_provider.py` 旁），由 gender 推导「他/她/ta」。
3. dream_prompt 的人称锁、B 类各 prompt 用 helper 取代写死的「他」。

### D 类 — config / 内容路径耦合（半硬编码，每部署可配）
`config.yaml`：`character.name: 叶瑄`、`notify.from_name: 叶瑄`、上线提示 `叶瑄已上线，等你回来`(L31)、`diary.characters: [yexuan]`、语音路径 `content/characters/yexuan/voice/*.wav`(7 处，把目录名和 char_id 绑死)。
属配置项，可保留；但 voice 目录名与 char_id 耦合，换角色要一并建目录。

### E 类 — 角色卡 / 世界包 / 示例：用户数据，正常保留，不算硬编码
`characters/*.json`、`characters/dream_worlds/*`、`dream_presets/*`、`reality/lorebook*.yaml`、`content/characters/yexuan/traits.example.yaml`。这些是 yexuan 这个角色的**内容**，不是代码里的硬编码。换角色时换数据，不改码。

### F 类 — 文档 / 历史任务单：不用动
`cc-tasks/*`、`docs/*`、`codex_prompt_*.md`、`待办方案与排序.md`。历史记录，低优先，可不清理。

---

## 三、前端（admin/static/index.html）

- **「叶瑄」字面量 6 处**：L288 侧栏标题 `🎭 叶瑄`、L306 `与叶瑄`、L858/860 页标题、L2688 `叶瑄正在输入…`、L2694 fallback `（叶瑄没有回应）`。
  建议：后端补一个返回当前角色名（+ 性别）的接口或在页面注入全局 JS 变量，前端统一用 `CHAR_NAME` 渲染。
- **「him / 他」**：前端**几乎没有代词渲染**。`他` 只出现在注释 `// 💬 与他`(L2654)，以及无关词 `其他`。所以「前端 him 改 him/her」实际工作量很小——前端目前是直接显示角色名而非代词。真正要做的是：(1) 名字占位符，(2) 给「性别」一个可设置入口，喂给后端 prompt（C 类）。

---

## 四、改仓库名可行性：**可行，低风险**

代码层不会因改名断裂——Python 全用包相对导入（`core.`/`admin.`/`channels.`），不引用仓库名；`data_paths` 用相对路径 `Path("data/...")`，也不带绝对仓库路径。

需要同步处理的点（都不涉及业务逻辑）：

| 位置 | 内容 | 处理 |
|---|---|---|
| GitHub remote | `origin .../qq-st-bot.git` | GitHub 上改仓库名 → `git remote set-url origin <新URL>`。`AA更新.bat` 的 `git pull` 改完即可用 |
| `README.md` L105 / `AA1先看说明书正式版README.md` L39 | clone 地址 | 改文本 |
| `admin/admin_server.py` L26/L101 | 标题 `QQ-ST-Bot 管理面板` | 改文本（纯展示） |
| 本地文件夹 `D:\ai\qq-st-bot` | 绝对路径 | 改本地目录名后，更新 `.claude/settings.json` L95 的允许路径 |
| `.claude/projects\d--ai-qq-st-bot\` | CC 项目记忆目录 | 随本地路径变化，CC 会按新路径重建（旧记忆可手动迁移） |
| `AGENTS.md` / `CLAUDE.md` / `cc-tasks/*` | 文中 `D:\ai\qq-st-bot` 字样 | 文档，按需改 |
| `.claude/.cache/*.json` | 旧 edit 历史里的绝对路径 | 可丢弃，不用管 |

**结论**：改名 = ① GitHub 改名 + `remote set-url`，② 本地目录改名 + 更新 `.claude/settings.json`，③ 几处展示文本/文档。无导入、无数据路径破坏。

---

## 五、建议执行顺序

1. 先 B 类（dream_prompt + 活动 prompt 的「叶瑄」→ `get_char_name`）：风险低、收益大（防串名）。
2. C 类：加 gender 字段 + pronoun helper + 替换 dream_prompt 人称锁。
3. A 类默认参数：`char_id="yexuan"` → 读 config.default 或改必传（注意 legacy 路径分支别动数据）。
4. 前端：补角色名接口 + 全局变量替换 6 处「叶瑄」。
5. 改仓库名（独立、随时可做）。（改为Emerald-presence）
6. 完成后跑 `pytest` + `python tests/run_eval.py`（若动了 tag/prompt 层）。
