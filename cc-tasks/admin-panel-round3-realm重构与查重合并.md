# 工单：管理面板 round3 —— 现实/梦境双 realm 创作 + 多处查重合并 + 调度器/工具/黑名单归位

> 仓库 `D:\ai\qq-st-bot`，面板 `admin/static/index.html`。先看 `AGENTS.md`。
> 接 round2。本轮信息密度高，按 A–F 分别施工，每条都附了已核实的后端事实。
> **B 节需要新增后端接口（dream.py 或新 router）**，其余以前端 + 个别后端扩展为主。

---

## A. 侧边栏改名：现实破限+世界书其实是一个 realm

**现状（已核实）**：
- `/lorebook` → `characters/reality/lorebook.yaml`（现实世界书）。
- `/jailbreak-entries` → 现实破限，且其 UI **就嵌在世界书页里**（`index.html:430` "破限预设条目" 在 `page-lorebook` 区内）。
- 所以现在的 "世界书" 这一页实际是 **现实 realm 的世界书 + 破限**，标签名误导。

**改**：创作组（round2 的 🎨 创作）调整为三项：
```
🎨 创作
  角色卡
  现实设定        ← 现 "世界书" 页改名（含世界书 + 破限，都属现实 realm）
  梦境设定        ← 新增（见 B 节）
```
- 仅改 nav 标签 + 页标题为「现实设定」（或「现实 · 世界书/破限」），页内结构不动。

**验收**：左栏创作组显示 角色卡 / 现实设定 / 梦境设定；现实设定页内世界书与破限都在、功能不变。

---

## B. 梦境世界书/破限接入创作（新接口，逻辑见下）

**现状（已核实）**：梦境只有**运行时**接口（`/dream/enter|chat|exit|state|settings|stats`、`/dream_seed/*`），**没有任何 authoring CRUD**。资产躺在磁盘没有面板入口。

**资产与格式（关键：梦境格式 ≠ 现实，别套用现实模型）**：
- 梦境世界目录：`characters/dream_worlds/{world}/`，world 有 `abo / cat / custom / flower_bud / reality_derived / vampire / _default / 审讯` 等。
- 每世界世界书：`characters/dream_worlds/{world}/lorebook.yaml`，格式是**裸 list**（无 `entries:` 包裹），每条：
  ```yaml
  - keywords: ["标记", "临时标记"]   # 注意是 keywords（复数），现实是 keyword（单数）
    content: |
      ...
    insertion_order: 2
    regex: false
    # 无 enabled 字段
  ```
- 每世界梦境预设/破限：`characters/dream_presets/{world}.md`（markdown 文本，如 `abo.md / 审讯.md`）。

**要新增的后端接口**（放 `admin/routers/dream.py` 或新建 `dream_authoring.py`，挂在创作语义下）：
- `GET  /dream/worlds` → 列出 `dream_worlds/` 下的世界名（排除以 `_` 开头的内部项可选）。
- `GET  /dream/worlds/{world}/lorebook` → 读 `lorebook.yaml`，返回条目数组（保留 `keywords/content/insertion_order/regex`）。
- `POST /dream/worlds/{world}/lorebook` / `PUT .../{index}` / `DELETE .../{index}` → 增改删，**写回时维持裸 list 格式 + `keywords` 复数键 + 不强加 enabled**。
- `GET/PUT /dream/worlds/{world}/preset` → 读写 `dream_presets/{world}.md` 全文。
- 所有写操作经 `core/sandbox.get_paths()`（硬规则 1），不要硬编码路径。

**前端「梦境设定」页**：
- 顶部：**世界选择下拉**（来自 `/dream/worlds`）。
- 中部：该世界世界书条目表 —— 可复用现实世界书的表格/弹窗组件，但**适配 dream schema**（字段 `keywords` 复数、无 enabled；新增/编辑弹窗去掉 enabled 勾选）。建议给 dream 单独一套 `loadDreamLore()/saveDreamLore()`，不要和现实的 `saveLore()` 混用，避免 keyword/keywords 串味。
- 底部：该世界的预设 md 编辑框（`textarea` + 保存）。

**注意**：梦境世界含成人向内容（abo/审讯/触手巢穴等）。单用户自用面板，正常编辑即可；但这部分资产在 `.gitignore` 内（见 `pre-release-cleanup.md`），别在本工单改动 ignore 规则、也别把内容写进任何会入库的文件。

**验收**：梦境设定页能选世界、看到并增删改该世界 lorebook 条目（写回 yaml 格式正确、keywords 复数保留）、编辑并保存预设 md；切到运行时进梦境验证改动生效（看 `/logs` 无解析错误）。

---

## C. 系统状态 与 系统设置 查重合并

**现状（已核实重叠）**：
- `/status`（`admin/routers/system.py:21`）：实时态（active_sessions、known_user_count）+ `config_summary`（llm_model / provider / short_term_rounds / admin_host / port）。
- 系统设置页（`page-settings`）：round1 已改成只读镜像（代理/上下文/LLM/视觉）。
- **重叠**：LLM model/参数 在状态页 config_summary 和设置页 LLM 镜像里**各显示一遍**。

**改**：合并成**单页「系统状态」**：
- 顶部：实时运行态（会话/用户数等，保留现状页内容）。
- 下方：把设置页的只读 config 镜像（代理/上下文/LLM/视觉）并进来，去掉与 config_summary 的重复项（LLM 只留一处）。
- **删除独立「系统设置」nav 项**；设置页 DOM 内容迁入状态页或直接复用其卡片。
- 清理迁移后悬空的 JS（loadStatus / 各只读镜像 loader 合并到一个页面初始化里）。

**验收**：左栏只有「系统状态」一个系统页；LLM/代理/上下文/视觉信息不再两处重复；页面加载无悬空引用报错。

---

## D. 调度器：基础配置移走 / 触发器状态保留并补全 / 手动触发不动

**现状（已核实）**：
- `/scheduler/config`（GET/PUT，`scheduler.py:45-86`）= 直接读写 `config.yaml` 的 `scheduler:` 块（enabled/morning_greeting/night_reminder/random_message/presence_nag/signatures 等）。→ 这就是面板的「基础配置」模块。
- `/scheduler/status`（:33）→ `{enabled, triggers: get_status()}`。
- `/scheduler/trigger/{name}`（:101）= 手动触发。

**改**：
1. **基础配置模块**：这些是部署期开关、已在 `config.yaml`。按 doctrine + 你的判断**移除编辑 UI**，调度开关交给 `config.yaml`（改后热重载）。如需保留可降级为只读镜像，但你倾向删 → 默认删除「基础配置」整块。
2. **触发器状态**：保留，并**补全**。当前 `get_status()`（`core/scheduler/loop.py:645`）只遍历 `_COOLDOWNS`，每项返回 `last_triggered/cooldown_sec/remaining_sec/ready` —— **漏掉了 proposer_registry / state_machine / sensor_aware 等触发器**，看不全。
   - 后端扩展 `get_status()`：枚举**所有已注册触发器**（不只 _COOLDOWNS），补 `enabled` 状态、下次可触发预估；可选接入 `/scheduler/sensor_aware/audit`（:114）展示最近决策。
   - 前端触发器状态表按补全后的字段渲染。
3. **手动触发**：**不动**（保留现状）。

> 理念对齐：你说"触发器不该是后台负责的"——这里"触发器**状态**"是**观测**（看它有没有在跑），属面板合理职责，保留；真正该移走的是**配置开关**（基础配置 → config）。两者区分对待。

**验收**：调度器页无「基础配置」编辑块；触发器状态列出全部触发器（不止 cooldown 那几个）含 enabled/下次预估；手动触发照常。

---

## E. 工具管理：查重，删编辑、移交 config

**现状（已核实）**：`/tools`（GET）+`/tools/{name}`（PUT，`settings_misc.py:29-42`）= 读写 `config.yaml` 的 `tools:` 块 enabled 标志 + 热重载。和 config 完全重复。

**核实"古老"**：`get_tools` 列的工具来自 config.yaml `tools:` 列表，可能与 `core/tool_dispatcher.py` 的 `_TOOL_REGISTRY` 实际注册项**漂移**（config 里有已删工具 / 注册表新增的没列）。CC 先比对两边，确认是否过期。

**改**：工具启用属部署期（已在 config.yaml）→
- **删除「工具管理」nav 与编辑页**，工具开关交给 `config.yaml`。
- 可选：在合并后的「系统状态」页保留一行**只读**"已启用工具"做诊断（从 `_TOOL_REGISTRY` 实际注册项读，而非 config 列表，顺带解决漂移）。
- 删后清理悬空 JS（loadTools 等）。

**验收**：左栏无「工具管理」；如保留只读诊断行，其列表反映真实注册工具；无悬空 JS 报错。

---

## F. 黑名单：有逻辑，并入用户管理

**现状（已核实有真实逻辑）**：`core/qq_adapter.py` 加载 `blacklist.yaml`，`is_blacklisted()` 对黑名单用户的 QQ 消息**静默丢弃**（:200-201）。功能有效 → **保留**。

**改**：并入「用户管理」页，不再独立成项：
- 用户列表下方加一个「黑名单」框（增/删 uid），复用现有 `/relations/blacklist`、`/relations/blacklist/{uid}` 接口。
- **删除独立「黑名单」nav 项**。

**验收**：黑名单增删在用户管理页内可用、写回 `blacklist.yaml`、对 QQ 生效；左栏无独立黑名单项。

---

## 最终导航形态（三组）

```
🎨 创作    角色卡 · 现实设定 · 梦境设定
🛠 运维    系统状态(并入设置/只读镜像/工具诊断) · 调度器(触发器状态+手动触发) · 用户管理(并入黑名单) · 错误日志
🔍 观测    情绪·花园 · 梦境状态 · 记忆探查 · 隐性状态 · 聊天日志
```
（删除：独立「系统设置」「工具管理」「黑名单」nav；移走：调度器基础配置、工具开关 → config.yaml）

## 执行顺序
1. **C/E/F**（查重合并删项，先收敛导航）。
2. **A + D**（改名 + 调度器拆分；D 的触发器补全含后端 `get_status()` 扩展）。
3. **B**（梦境创作，工作量最大，含新后端接口）。

> 改完自测：逐个导航页可切换、现实/梦境世界书各增删一条、看 `/logs` 无新错误。改了 `tag_rules` 无关；改了调度器配置面 → 同步 `docs/scheduler.md`；新增梦境 authoring 接口 → 同步 `docs/channels.md` 或相应文档。
