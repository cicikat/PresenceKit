# 工单：管理面板重定位（从"半残设置编辑器"改成"运维控制台"）

> 仓库：`D:\ai\qq-st-bot`（面板在 `admin/`）。涉及前端边界处只做标注，不在本工单施工。
> 先看 `AGENTS.md`。日期 2026-06-19。
> 这是**架构归位**工单，不是 bugfix。先读完「零、定位决策」再动手，每个改动都要能对回那张表。

---

## 零、定位决策（product decision，先统一认知再施工）

现状问题（审计结论）：后端 33 个 router / ~6300 行 API（`admin/routers/`，每天在改），但面板前端 `admin/static/index.html` 实质停更在 06-03，只渲染 9 个页面，覆盖约 1/3 API。新子系统（garden / mood / dream / hidden_state / hardware / sensor / activity / chat_log / diary / group-Stage）**有 API、无面板**。同时 `config.yaml` 膨胀到 ~240 行、Emerald-client 长出自己的设置窗口——**三处都在管"设置"，没人定边界**。

定下三者职责，后续所有改动按此归位：

| 层 | 定位 | 装什么 | 不装什么 |
|---|---|---|---|
| **config.yaml** | 部署期 / 密钥 / 基建（装一次） | API key、SMTP 密码、`secret_key`、端口、`base_url`、provider 选择、`buttplug_ws`、`standalone_mode` | 运行中要反复切的开关 |
| **Emerald-client（前端）** | 主人的日常运行界面 | 聊天、看日记/梦境/活动、玩具控制、TTS 开关、活动偏好、情绪/花园展示等**主人天天碰**的东西 | 后端内部状态、记忆修复、人设创作 |
| **管理面板** | **运维 / 调试 / 创作控制台**（不是设置副本） | 后端可观测性 + 记忆外科手术 + 资产创作（角色卡/世界书/破限/prompt 资产） | 与 config 重复的部署期设置；与前端重复的运行时交互 |

一句话边界：**密钥与基建→config；主人天天用的→前端；后端内部的查看·修复·创作→面板。面板别再做设置，做后台运维台。**

---

## 一、全 router 归位表（施工总纲）

对 `admin/admin_server.py:32-75` 注册的每个 router 给一个动作。四种动作：
**保留**（面板核心价值，留/做厚）、**改只读**（面板只看不写，写入权交给 config 或前端）、**新建只读视图**（有 API 无 UI，补一个观测页）、**前端归属**（运行时交互交给 Emerald-client，面板顶多留 debug 只读）。

| router | 现面板有 UI? | 动作 | 理由 |
|---|---|---|---|
| `system`（status/reload/logs） | 有 | **保留** | 运维核心：状态、热重载、日志 |
| `scheduler`（config/status/trigger） | 有 | **保留** | 状态查看 + 手动触发是面板独有价值 |
| `character` | 有 | **保留·做厚** | 人设创作，面板护城河 |
| `lorebook` | 有 | **保留·做厚** | 世界书创作 |
| `jailbreak_entries` | 有 | **保留** | 破限条目创作 |
| `settings_prompt_assets` | 部分 | **保留** | prompt 资产创作属面板 |
| `users` / `memory` / `relations` | 有 | **保留·做厚** | 记忆查看与修复（外科手术），面板独有 |
| `debug` / `hidden_state_debug` | 无 | **新建只读视图** | DEV-ONLY 调试，缺 UI；补只读观测页 |
| `chat_log` | 无 | **新建只读视图** | 有 API 无 UI，运维要看 |
| `dream` / `dream_seed` | 无 | **新建只读视图** | 梦境状态 debug；运行时交互归前端 |
| `mood` | 无 | **新建只读视图** | 情绪状态观测；主人展示归前端 |
| `garden` | 无 | **新建只读视图** | 花园状态观测；浇水/采收交互归前端 |
| `activity`/`reading`/`gomoku`/`chess` | 无 | **前端归属** | 活动是主人交互；面板顶多只读 debug |
| `hardware`（玩具） | 无 | **前端归属** | toy 窗口已在前端；ws 端点在 config |
| `sensor` / `mobile` / `watch` | watch 部分 | **前端归属** | 设备侧；面板只留 status 只读 |
| `group`（Stage） | 仅 group-distill | **拆分** | 群成员/角色册创作→面板；实时群聊→前端 |
| `agent` | 无 | **新建只读视图** | 按内容定，多半是状态观测 |
| `settings_llm` | 有（设置页） | **改只读** | 部署期配置，权威在 config |
| `settings_proxy` | 有（设置页） | **改只读** | 同上 |
| `settings_misc`（tts/chat风格/上下文等） | 有（设置页） | **拆分** | TTS 开关/对话风格→前端；其余部署期→config 只读 |
| `diary` | 无 | **前端归属** | 主人阅读，前端已有 diary 窗口 |

> 施工执行者：动手前若发现某 router 实际行为与上表理由不符（例如 `agent` 其实是写操作），在工单里标注并按"零"的边界原则重新归类，不要硬套。

---

## 二、面板瘦身：砍/降级重复的"系统设置"页

文件 `admin/static/index.html`，设置页区块 `id="page-settings"`（**:553–801**），含 6 个子块：

- `<h3>代理配置`（→ `/proxy`，settings_proxy）
- `<h3>上下文长度`（→ `/context-config`）
- `<h3>TTS 语音`（→ `/tts-config`，settings_misc）
- `<h3>对话风格`（→ `/chat-mode` `/chat-style` `/chat-multi-message`）
- `<h3>LLM 生成参数`（→ `/llm-params`，settings_llm）
- `<h3>视觉模型配置`（→ `/vision-params`）

**根因**：这些是部署期配置，权威已在 `config.yaml`（`llm:` / `proxy:` / `tts:` / `vision:` / `context:` / `chat:`）。面板再放一套可写表单 = 双写源、必然漂移，也是面板"看着像还活着其实没人维护"的主要观感来源。

**修法（按风险从低到高三选一，推荐 B）**：

- 方案 A（最省）：保留这些子块但**全部改只读**——表单控件 `disabled`，顶部加一行提示「部署期配置，请改 `config.yaml` 后热重载」，值仍从对应 GET 接口拉取展示。对应后端把 `settings_llm` / `settings_proxy` 的 PUT/POST 路由保留但前端不再调用（或后端标 deprecated）。
- 方案 B（推荐）：**LLM / Vision / 代理 / 上下文** 四块改只读镜像（同 A）；**TTS 开关 / 对话风格** 这两块从面板**移除**，标注「已迁移至 Emerald-client 偏好设置」（前端已有偏好弹窗，见 Emerald `ChatWindow.tsx`，本工单不施工，仅留 TODO）。
- 方案 C（最彻底）：整页 `page-settings` 降级为「部署配置只读总览」，一屏只读展示 config 关键项 + 一个「热重载 config」按钮（复用 `/reload`）。

**验收**：设置页不再能写入任何部署期配置；改 `config.yaml` + 热重载后，只读视图能反映新值；面板不再与 config 存在双写。

---

## 三、面板补强：新建"可观测性"只读视图

把"有 API 无 UI"的子系统补上**只读**观测页（先不做控制，避免再和前端打架）。在 nav（`admin/static/index.html:289-306`）新增一个分组，例如「观测/调试」，下挂：

- [ ] **记忆探查**：`memory` 各层只读视图（short-term / mid_term / episodic / identity / event_log），按 `uid` 切换；已有 `/memory/{uid}/short-term`、`/memory/{uid}/rag`，按需补 GET。
- [ ] **隐性状态**：`hidden_state_debug` 只读快照页（DEV-ONLY，可加构建开关）。
- [ ] **梦境**：`dream` / `dream_seed` 当前会话与预构种子的只读状态。
- [ ] **情绪 / 花园**：`mood` 当前情绪、`garden` 五花槽状态只读卡片。
- [ ] **聊天日志**：`chat_log` 按日期/uid 浏览。
- [ ] **调度器影子**：`scheduler` gating_shadow / 触发历史只读（手动 trigger 按钮保留）。

> 统一用现成的 `api('GET', path)` helper（`index.html:1193`）。每个视图先调一次真实接口确认返回结构再写解析——**不要照 router 函数名猜字段**。

**验收**：每个新视图能拉到真实后端状态且不报错；纯只读，无写入按钮。

---

## 四、保留并做厚：创作能力（面板护城河）

`character` / `lorebook` / `jailbreak_entries` / `settings_prompt_assets` 是 config 和桌宠都不适合干的——保留现有编辑/导入导出，确认 import/export 链路（`/lorebook/import|export`、`/jailbreak-entries/import|export`、`/characters/upload|export`）仍可用，作为面板的主推定位。本阶段不强求新功能，只确保不被瘦身误伤。

**验收**：角色卡/世界书/破限的增删改、导入导出全程可用。

---

## 五、（可选·前瞻）拆掉 13 万字符单文件

`admin/static/index.html` 单文件 ~131KB 是本次掉队的根因：每加一个子系统都要手搓一大段内联 JS，边际成本太高 → 没人加 → 滞后。若决定让面板长期承担"观测+创作"，建议后续把它按页面拆成多文件（哪怕仍是原生 JS，按 `page-*` 拆 + 共享 `api()`/`authHeaders()`）。**本工单不强制做**，仅作为三、补视图时"新页面单独成文件、不再堆进 index.html"的方向约束。

---

## 六、执行顺序与总验收

建议顺序：
1. **零 + 一**（团队/你确认归位表）——这是决策闸门，表没拍板不要往下做。
2. **二**（瘦身设置页，方案 B）——先止血双写。
3. **四**（确认创作链路没被误伤）。
4. **三**（按归位表逐个补只读观测视图，新页面独立成文件）。
5. 前端归属项（activity/hardware/diary/mood/garden 的运行时交互）开**单独的 Emerald-client 工单**，本仓只留 TODO 标注。

总验收：
- [ ] 面板不再写入任何 config 已管的部署期配置（无双写源）。
- [ ] 归位表里标「新建只读视图」的子系统都有可用观测页。
- [ ] 创作四件套（角色/世界书/破限/prompt 资产）功能完好。
- [ ] nav 结构反映新定位（创作 / 观测调试 / 运维 三类），不再有"半残设置"。
- [ ] README / 面板标题（`admin/admin_server.py:26`）措辞与"运维控制台"定位一致（可选）。
