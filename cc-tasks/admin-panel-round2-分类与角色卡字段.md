# 工单：管理面板 round2 —— 视觉分类重整 + 角色卡编辑器对齐 schema + 删 TTS 废话框

> 仓库 `D:\ai\qq-st-bot`，面板在 `admin/static/index.html`（单文件）。先看 `AGENTS.md`。
> 接 `admin-panel-repositioning.md`（已落地：导航已改"运维控制台"、观测组已加、设置页改只读镜像）。
> 本轮三件事：A 分类重整、B 角色卡编辑器对齐真实 schema、C 删 TTS 占位框。

---

## A. 导航/视觉分类重整（当前分类极难用）

**现状**（`index.html:283-313` 附近）：
- 顶部 `角色卡 / 世界书 / 调度器` **无分组标题**，且把"创作"(角色卡/世界书)和"运维"(调度器)混在一起。
- `错误日志` 被塞进 `🔧 工具` 组——日志不是工具，是观测/运维。
- 5 个组、内容很薄，没对上重定位时定的 **创作 / 运维 / 观测** 三分法。

**改成三桶（与重定位 doctrine 对齐）**：

```
🎨 创作
  角色卡  世界书  破限           (破限若仍内嵌在世界书页内则保持，仅确认入口可达)
🛠 运维
  系统状态  调度器  工具管理  用户管理  黑名单  错误日志  系统设置
🔍 观测/调试
  情绪·花园  梦境状态  记忆探查  隐性状态  聊天日志
```

- `系统状态` 作为默认 active 页放运维组首位（保持现有 default 行为）。
- `错误日志` 从工具组挪到运维组。
- 若觉得运维组 7 项偏长，可拆成 `🛠 运维`(状态/调度/工具/日志) + `⚙️ 系统`(用户/黑名单/设置)，二选一，不强制。
- 纯视觉：组标题样式沿用现有 `padding:8px 20px 2px;...uppercase` 那套，别引新样式。

**顺带清理设置页观感**（`index.html:560` `page-settings`）：现在是一堆"只读镜像"卡 + 两张"已迁移"空卡，显得像坏了。
- 每张只读镜像卡（代理/上下文/LLM/视觉）加一个小 `只读` 徽标或标题后缀，明确"这里只看、改 config.yaml"。
- 两张"已迁移"占位卡按 C 节删除。

**验收**：左栏只有 创作/运维/观测 三类（或运维再拆系统），每项点击正常切页；设置页不再有空的"已迁移"卡，只读镜像有只读标识。

---

## B. 角色卡编辑器对齐真实 schema（"变量对不上"的真因）

**诊断（已核实，非破坏性，但功能落后）**：
- 保存逻辑已用 `const body = { ..._charData, ... }`（`index.html:2526`）合并，**不会丢字段**，这点没问题。
- 真问题：编辑器只暴露 6 个字段——`name / scenario / description / personality / first_mes / mes_example`（表单 `index.html:521-549`，回填 `:2498-2503`）。
- 但真实角色卡（`characters/yexuan.json`、`hongcha.json`）和 `core/character_loader.py` 的 `Character`（:24-41）schema 是：
  `name, system_prompt, description, personality, scenario, mes_example, world_book, anniversaries, birthday, gender`。
- 即：**编辑器缺了最关键的 `system_prompt`**（每张真实卡都有、是人设核心），还缺 `gender / birthday / anniversaries`；反而留了个**真实卡根本不用的 `first_mes`**。
- 后果：想"重写"角色卡时改不到 system_prompt，且对着一个空的无用字段。

**修法**：
1. **加 `system_prompt`**：大号 `<textarea>`（比 description 更高，它通常最长），回填 `d.system_prompt`，并入保存 body。**这是本节最重要的一项。**
2. **加 `gender`**：`<select>` male / female / neutral，回填 `d.gender ?? 'neutral'`。
3. **加 `birthday`**：结构是 `{month, day, prompt}`。给三个输入（月/日/prompt 文本），回填 `d.birthday`，保存时组回对象；`d.birthday` 为 null 时留空、保存时若三项皆空则写 null（保持"未迁移=回落全局 config"语义，见 character_loader 注释）。
4. **加 `anniversaries`**：结构是 `list[dict]`，编辑成本高 →
   **加一个「高级 (raw JSON)」可折叠区**，直接编辑 `anniversaries`（以及任何未来新增字段）的原始 JSON。这同时给所有结构化/未来字段一个不丢失的编辑出口。
5. **`first_mes`**：真实卡不用它（loader 仍兼容读）。不必删，但移到"高级/可选"区或加 hint「多数角色卡不使用」，别再占主区显眼位置。
6. 保存仍走 `PUT /characters/{name}`（后端 `save_character` 整体写回 + 热重载，配合 `..._charData` 合并是安全的，不动后端）。

**世界书无需改字段**：面板表单 `keyword / content / enabled / regex / insertion_order`（`index.html:1719-1728`）与后端 `LoreEntry`（`admin/routers/lorebook.py:59-64`）**完全一致**，不要瞎改。
- 仅需确认一件事：角色卡内嵌的 `world_book` 数组 与 `/lorebook` 端点编辑的全局世界书是不是同一份数据。若是两套，在世界书页加一行说明区分（避免你以为改了卡里的 world_book 其实改的是全局表）。这是确认项，不是必改。

**验收**：
- 打开 `yexuan.json` 能看到并编辑 `system_prompt / gender / birthday`，保存后 `characters/yexuan.json` 对应字段正确更新，其余字段（world_book/anniversaries 等）不丢。
- 高级 raw JSON 区能编辑 anniversaries 且保存生效。
- 保存后后端热重载无报错（看 `/logs`）。

---

## C. 删掉 TTS（及对话风格）"已迁移"占位框

**现状**：`index.html:611-616` 是 `TTS 语音` 的"已迁移"占位卡，`:617-622` 是 `对话风格` 的同款占位卡。

**改**：
- **整块删除**这两张卡（不只是清文字，连卡片容器一起删），**不留任何"已迁移/见前端"之类的说明文字**。
- 用户已确认：这类"迁移到哪了"的信息以后统一写进操作说明，不放面板里。
- 删后检查：`page-settings` 里没有指向已删 DOM 的悬空 JS（如 `loadTtsConfig()` / 对话风格相关的 get/save），一并清掉对应函数和调用，避免 console 报错。
- 同理确认 `/tts-config`、`/chat-mode|style|multi-message` 这些前端调用在面板侧已无引用（后端路由是否保留不在本工单范围，别动后端）。

**验收**：设置页不再出现 TTS 语音 / 对话风格 卡；面板加载无因悬空引用产生的 JS 报错。

---

## 执行顺序
1. **C**（删占位框，最小、先清场）。
2. **A**（分类重整 + 设置页只读标识）。
3. **B**（角色卡编辑器加字段，工作量最大）。

> 单文件 `index.html` 已很大；B 的 raw JSON 高级区尽量用现有 `api()` / 表单样式，别引入新依赖。改完自测：切每个导航页、编辑并保存一张角色卡、看 `/logs` 无新错误。
