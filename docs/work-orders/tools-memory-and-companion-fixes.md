# 工具指引 · 记忆压缩与召回 · 提醒/语音/预览杂项 工单组

本组 16 张工单，来自 2026-10-02 用户反馈的 7 个问题。每张独立验收、独立提交
（commit 附 `Co-authored-by: Codex <codex@openai.com>`）。行号以审计时 HEAD `f906987` 为准，
开工前先 Grep 确认位置没漂移。

**开工前先读「零、调查结论」**。其中有几条和需求字面理解不一样，会改变你的做法：
- 「角色自己做数据库」三件事里，只有「写纯文本文件」和「自写 prompt（AGENT.md）」存在；
  **「自制工具」从未实现**。已有的两件模型也几乎看不见。
- 提醒问题是迁移回归，但**不能改回 `_pipeline_send()`**：`reminders` 在 `MIGRATED_TRIGGERS` 里，
  走那条路 prompt 会被直接丢弃。
- 记忆「越来越只剩吵架」不是单点问题，是一条从写入、强度、衰减、淘汰到召回的**完整放大链**。

**总原则（用户明确要求）：不给角色加行为约束。** 不要写「不要占有」「不要提起吵架」「保持温柔」
这类人设/行为指令。所有记忆类改动只修**机制**：存什么、怎么打分、怎么召回、怎么标注来源。
工具类改动只补**方向感**（什么事找哪类工具），不加「只有用户要求才能用」之类的限制。

---

## 依赖与并行

| 工单 | 主题 | 仓库 | 依赖 | 可并行 |
|---|---|---|---|---|
| T1 | 工具分类重组 + 用途路由 + AGENT.md 可见 | 后端 | 无 | 与全部 M、A 组并行 |
| T2 | 角色自有结构化资料库 `self_db_*` | 后端 | **T1**（新工具进 `self` 分类） | 与 M、A 组并行 |
| T3 | 角色自制工具（声明式配方） | 后端 | **T1、T2** | 与 M、A 组并行 |
| M1 | 强度 ≠ 情绪强度：写入打分、衰减、存量重算 | 后端 | 无 | 与 M3、M6 并行 |
| M2 | 冲突按「一整段」入库，连同澄清/道歉/修复结果 | 后端 | **M1**（同改 `write_episode`） | 与 M6 并行 |
| M3 | 用户原话事实 vs 角色解读 分开存 | 后端 | **M2**（同改 reflect prompt，串行避免冲突） | 与 M6 并行 |
| M4 | 召回混合分桶 + 去掉重复注入与情绪同调回路 | 后端 | **M1、M2**（用新字段） | 与 M6 并行 |
| M5 | 长期问题强制关系召回 + 相识时长事实 | 后端 | **M4** | — |
| M6 | storyline 周聚合修复（长期叙事层停摆） | 后端 | 无 | 与 M1-M5 并行 |
| C1 | 删除 `6a_user_identity_coldstart` 全部 | 后端 | 无 | 全并行 |
| A1 | 提醒到点改由角色生成再投递 | 后端 | 无 | 全并行 |
| A2 | 小红书读取服务失联可见 + 启动说明 | 后端 | 无 | 全并行 |
| A3 | 语音输入标记 + ASR 不确定提示 + 置信度降权 + 记忆打标 | 后端 + 桌面 | 无 | 与 A4 先后皆可 |
| A4 | 通话断句：在途分段不抢发、中文不加空格 | 桌面 | 无（与 A3 同改 `RoomWindow.tsx`，**串行**） | — |
| A5 | 文件预览浮层随窗口与字号缩放 | 桌面 | 无 | 全并行 |
| A6 | 角色可原地修改聊天产物文件 | 后端 | 无 | 全并行 |

推荐顺序：C1、A1 最小最独立，先做；记忆组按 M1 → M2 → M3 → M4 → M5，M6 随时插入；
工具组 T1 → T2 → T3。

---

## 零、调查结论

### 结论 1：工具「不知道该用哪类」的根因在发现层分类（影响 T1-T3）

Path C 首轮只给模型看分类入口 `load_tools_<category>`，每个入口是一行中文标签 + 至多 24 个工具名
（`core/tool_discovery.py:11-24, 44-48`）。问题：

- **自有空间工具被归错类**：`self_list/read/create/update/move/delete/restore` 7 个工具全部
  `category: "info"`（`core/tools/character_self.py:112-240`），而 `info` 的标签是
  「时间、搜索和外部信息查询」。想「记下自己的东西」的模型不会去点这个入口。
- **`info` 是大杂烩**：约 30 个工具（时间、天气、搜索、小红书、文档、花园、屏幕、喝酒、agent 任务、
  self 7 个、提醒 6 个、听歌 6 个…）。`self_*` 排在第 18-24 位附近，提醒/听歌被折成「等 N 个」。
- **没有「什么时候用」**：分类描述只说是什么；`11.6_tool_discovery` 层只讲加载机制；
  `11.5_tool_nudge`（`core/pipeline.py:1303-1322`）只覆盖「需要外部信息」；常驻能力句
  `core/prompt_builder.py:535-540` 列了屏幕/记忆/图片/生活记录，**没提自有空间**。
- **AGENT.md 自写 prompt 对模型完全不可见**：写 `self/AGENT.md` 会注入层 `6i_self_agent_md`
  （`core/character_self.py:62-64, 1329-1460`），但没有任何工具描述或 prompt 告诉模型这个文件存在；
  文件不存在时该层什么都不注入（`:1365-1369`）。只有 `docs/tools.md:569-571` 写了。
- **使用证据**：近 6 天工具审计约 260 次调用，`self_*` 只有今天一次 `self_list`（参数错误）+
  一次 `self_create`；从未有 `self_read`/`self_update`；没有 `AGENT.md`。
  `docs/tool-discovery.md:12-14` 早已记录「`self_*` 长期零调用」。

三件事的真实状态：

| 能力 | 状态 |
|---|---|
| 写入数据 | 只有纯文本文件（`self_*`），无结构、无查询、无追加；`self_update` 须整文件重写 + `expected_revision` |
| 自制工具并调用 | **不存在**。无任何 create/register tool 代码；`self_create` 描述明说「创建可执行文本不等于获准执行」 |
| 给自己加 prompt | 存在（`self/AGENT.md` → `6i_self_agent_md`），但模型不知道 |

另：autonomy 副链只开放 allowlist 里的工具（当前只有 `invite_video_call`，在管理面自主设置里配），
所以主动时段永远拿不到 self 工具。这是用户设置，**本组不改默认值**，交付说明里提示用户即可。

### 结论 2：记忆偏向冲突是一条放大链（影响 M1-M6）

按数据流顺序，每一环都在给「情绪激烈的片段」加权：

1. **逐轮入库**：mid_term 写入后，若**角色回复**的情绪是 sad/angry/happy，立刻把这一轮单独
   reflect 成一条 episodic（`core/memory/fixation_pipeline.py:1063-1072`，情绪来自
   `detect_emotion(reply)`，`core/pipeline.py:2008-2009`）。一场吵架 = N 条独立 episode。
   中性轮要等 11h sweep，可能先被 mid_term 20 条/12h 上限挤掉。
2. **强度叠加**：LLM 按「事件越重要、情绪越强则越高」打 strength（`fixation_pipeline.py:55-78`），
   `write_episode` 再规则加成：sad/angry +0.1，标签含 吵架/道歉/哭/生气/误会/和好 再 +0.2
   （`core/memory/episodic_memory.py:241-260`）。**道歉、和好也被当成冲突加分。**
3. **淘汰按强度最低先走**：满 200 条删 strength 最低 20 条（`episodic_memory.py:217-239`），
   日常琐事先被淘汰，库里越来越只剩高强度冲突。
4. **衰减偏袒负面**：sad/angry 衰减率 0.015/天，中性 0.05/天（`episodic_memory.py:867-890`），
   负面记忆衰减慢 3.3 倍。**另有 bug**：每天用「创建至今的天数」去乘当前强度，是复利式衰减；
   且 `decay_all(oid)` 不传 `char_id`（`core/scheduler/triggers/time_based.py:714`），
   只衰减默认角色桶。
5. **冲突永远是 open，修复不挂回去**：`status` 只有在 LLM 判 `is_closure` 时才会被关闭，而 closure
   的定义只覆盖「吃完了/考完了/不去了」这类任务（`fixation_pipeline.py:72-73`），还要求关键词子串命中、
   72h 内（`:355-431`）。和好/道歉被写成另一条独立 open 条目，和冲突没有任何关联。
   被关闭的条目反而**被召回直接排除**（`episodic_memory.py:458`），修复点永远检索不到。
6. **召回继续放大**：
   - 融合分 `0.4*语义 + 0.3*关键词 + 0.3*(strength*decay)`（`core/memory/vector_store.py:501-514`），
     强度直接进排序。
   - 情绪同调加分：episode 的 `emotion_peak` 等于当前心情时 +0.15~0.30（`episodic_memory.py:463-465`）。
     角色心情低落 → 优先想起难过/愤怒的记忆 → 回复更负面 → 心情更低。
   - 兜底召回：tag 没命中时注入「近 7 天、strength≥0.6、open」的条目（`episodic_memory.py:986-1049`），
     打分只有强度 × 新近度，与当前话题无关——两天前的吵架几乎每轮都中。
   - 重复注入：层 `9.5_episodic_top` 把 6c 的第一条在 history 之后再注入一遍
     （`core/prompt_builder.py:1388-1399`），而这条往往就是刚吵完、已在 history 里的内容。
   - `get_episodic` 工具默认 `allow_strengthen=True`，每次查询还给记忆加强度（`tool_dispatcher.py:191-195`）。
7. **解读被当事实**：
   - mid_term 压缩 prompt 要求「主语用『用户』」，但输入含角色回复（`core/llm_client.py:1021-1023, 1135`），
     角色的话和结论会被归到用户头上。
   - episodic reflect 只看 mid_term 摘要，不看用户原话；`narrative_summary`「供角色回忆用」、
     `user_state` 都是解读，没有「用户说的 / 角色理解的」之分。
   - identity 固化只看 `narrative_summary（情绪, 强度）`，没有时间戳；`evidence_count` 由 LLM 自报
     （`fixation_pipeline.py:80-127, 1483-1487, 1563-1565`）。一场吵架拆成 N 条 → 看起来像「反复出现的模式」。
   - important_facts 抽取 prompt 主动要求记「性格特点、精神状态、稳定的情感关系」（`core/memory/user_profile.py:566-569`）。
8. **长期叙事层停摆**：storyline 周聚合（本该承载「冲突 → 和好」这种弧线）持续失败
   `invalid_llm_output`，arcs 为空，淘汰批次在 inbox 里越积越多。原因见 M6。
9. **没有任何长期问题检测**：「认识多久/怎么看我/过去发生过什么」不命中任何 tag 规则
   （`core/tag_rules.py:28-31` 只有 我们/之前/以前 这类泛词，且只被日记触发和 storyline 消费）；
   全系统没有「第一次对话时间」这个值。

### 结论 3：coldstart 层正在误导「认识多久」（影响 C1）

identity 注入门槛 `min_confidence=0.5`（`core/memory/user_identity.py:201, 216`）。当前所有维度置信度
都远低于 0.5，于是 identity 层为空，而 `has_real_interaction_history()` 只要求 short_term 里 ≥5 轮，
条件成立 → **每一轮都注入「你们认识不久」**（`core/prompt_builder.py:1007-1019`）。关系已持续数月，
这句话在直接误导模型。用户要求整体删除，持续时间改由模型从记忆判断（M5 提供真实相识时间）。

### 结论 4：提醒是迁移回归，且旧路已关闭（影响 A1）

- 最初版本 `_pipeline_send("备忘录提醒时间到了：{content}，用{角色名}的方式提醒她")`，这句话是
  **给 LLM 的刺激**。退役旧执行器时（`a8179d3`）换成了 `talk_gate.send(...)`，而 `talk_gate.send` 的
  `text` 参数是**成品回复**（设计给 `talk_owner` 工具用），于是这句指令原样发到用户聊天框。
  现位置：`core/scheduler/loop.py:855-866`。
- `reminders` 在 `MIGRATED_TRIGGERS`（`core/scheduler/gating.py:57`）里。`_pipeline_send` 对已迁移触发器会
  **丢弃 prompt、改发 autonomy 信号**（`core/scheduler/loop.py:420-453`）。所以不能简单改回去。
- `add_reminder` 的 `content` 描述没说用谁的视角（`core/tools/reminder_tools.py:146`），角色写成了
  给自己的备忘（「陪用户喝水休息…」）。
- `finish_delivery(sent=False)` 会把条目退回 scheduled，下个 tick 无上限重试
  （`core/agent_runtime/scheduler_capability.py:682-688`）。

### 结论 5：小红书读取服务没人负责启动（影响 A2）

- `read_xiaohongshu` 是对 `http://127.0.0.1:18060` 的普通 HTTP 调用（`core/tools/xiaohongshu.py:93-167`），
  背后是独立的 xiaohongshu-mcp 进程，**不是 Python 虚拟环境的一部分**。venv 只跑后端；
  「之前用虚拟环境能用」只是当时恰好有读取服务在跑。
- 本机有两套读取服务抢同一端口：Docker 容器（restart `unless-stopped`）和手动启动的原生 exe
  （09-14 后停了没再起）。今天 17:10 调用失败、17:14 Docker 才起来。
- 后端自管理启动（`core/xiaohongshu_service.py:23-41`）在 `deployment.mode: remote_server` 下直接跳过，
  且托管 exe 未安装。**失联时完全静默**：控制台和 error.log 都没有，只写了 api_calls 台账。
- 结论给用户：**不需要每次手动启动 Docker**，在 Docker Desktop 里开「登录时启动」即可，容器会自动起来。
  不建议为此把 `deployment.mode` 改成 `local`（会影响约 21 处能力门控）。

### 结论 6：通话断句的主因是「700ms 自动发送不等在途分段」（影响 A3、A4）

- 桌面端分段：静音 900ms 或硬切 6s 结束一段（`Emerald-client/src/windows/room/useContinuousCallVoice.ts:5-7, 103-104`），
  逐段送 `/transcribe`。`RoomWindow.tsx:196-202` 在**最后一段转写到达 700ms 后自动发送**，不检查是否还在说话、
  是否还有分段在转写。CPU Whisper 慢一点，后半句就成了下一条消息。片段用空格拼接（`:89`），中文里多出空格。
- 模型侧：唯一的语音提示 `core/audio_perception.py:419-435` 只说「声学印象不确定」，**从没说转写文字本身可能有错字、断句错误**；
  而且只有开了远程 STT 或声学分析才有回执，普通本地 STT 路径什么标记都没有（`admin/routers/transcribe.py:171`）；
  多段合并的消息只带最后一段的回执 id（`RoomWindow.tsx:90`）。
- 置信度（`avg_logprob`/`no_speech_prob`）只用来过滤，然后丢弃（`admin/routers/transcribe.py:73`）。
- 语音标记不进记忆：`desktop_chat`（`admin/routers/chat.py:842-893`）不传任何 modality，
  ASR 错字和打字一样进 short_term / event_log / 画像抽取。

### 结论 7：预览浮层尺寸写死；文件本体在后端 chat_artifacts（影响 A5、A6）

- 预览不是独立窗口，是 `Emerald-client/src/windows/chat/components/ChatPanel.tsx:2128-2163` 的内联浮层：
  宽 `min(760px, 100%)`、只有 `maxHeight: 80vh` 没有 `height`，iframe `flex:1` 无处可长，固定在 `minHeight: 320`。
  窗口放大它不变。主题字号 `--chat-theme-font-scale` 进不了 iframe（独立文档），后端预览包装写死
  `font:14px/1.5`（`admin/routers/chat.py:835`）。
- 文件本体：`<仓库>/data/runtime/chat_artifacts/{char_id}/{uid}/{artifact_id}{.ext}`，索引同目录 `index.json`
  （`core/data_paths.py:1129-1143`）。角色用 `write_artifact` 写、`read_artifact`/`list_artifacts` 读
  （`core/tools/chat_artifacts.py`）；前端下载 `GET /chat/artifacts/{id}`、预览 `GET /chat/artifacts/{id}/preview`。
  **缺口**：`write_artifact` 每次新建 uuid，角色无法原地修改；旧版本占配额直到 50 个上限被静默删除。

---

## T1 · 工具分类重组 + 用途路由 + AGENT.md 可见

**目标**：模型一眼知道「这件事找哪类工具」，并知道自己有一块可以长期经营的空间。只补方向，不加限制。

**改动**

1. `core/tool_discovery.py` 的 `CATEGORIES` 新增并改写标签（标签写「什么时候用」，不只写「是什么」）：
   - `self`：「你自己的空间：想长期记住、整理、积累的东西（笔记、资料表、自制工具、给自己定的习惯 AGENT.md）」
   - `schedule`：「到点提醒与日程：答应了以后某个时间要做/要提醒的事」
   - `life`：「一起生活的小事：花园、喝一杯、听歌、玩具文件」
   - `info` 改为：「此刻的外部信息：时间、天气、网页搜索、小红书、用户文档、屏幕与摄像头」
   - `memory` 改为：「回想你们之间过去发生的事：日记、情景记忆、事件、用户资料」
   - `artifacts` 改为：「做一份文件交给用户看/下载（不是你自己的笔记）」
2. 把注册表里的 `category` 改到新分类：`self_*` → `self`；提醒 6 个 → `schedule`；
   `water_garden`、`drink_with_user`、听歌 6 个、`read_toy_file`/`write_toy_file` → `life`。
   用 Grep 找全注册点（`core/tools/character_self.py`、`core/tools/reminder_tools.py`、
   `core/listening_tools.py`、`core/tool_dispatcher.py`）。
3. **兼容旧白名单**（必须做，否则当前角色卡会丢工具）：角色卡 `presence_ext.tool_categories` 与
   `config.tool_exposure.*.categories` 里只写了 `info`。在 `core/tool_exposure.py::resolve()` 末尾加
   展开表 `_LEGACY_CATEGORY_EXPANSION = {"info": ("self", "schedule", "life")}`：列表含 `info` 时自动并入
   这三类；已显式列出的不重复。`_DEFAULTS` 同步补上。Path A（probe）的 `info`/`desktop` 默认同样展开。
   检查 `_MODE_RESTRICTED_CATEGORIES`、autonomy `_SANDBOXED_WRITE_TOOLS`、deployment capability 表等
   按 category 判断的地方（Grep `"info"` 与 `category ==`），新分类按原 `info` 同等对待，不能因此放宽或收紧权限。
4. `11.6_tool_discovery` 层（`core/pipeline.py:1258-1262`）追加一段**路由表**，一行一类，例如：
   「想长期记住或整理自己的东西 → self；想起以前的事 → memory；答应了到点提醒 → schedule；
   查此刻的信息 → info；给用户做一份文件 → artifacts；电脑上的操作 → desktop」。只列当前实际暴露的分类。
5. 常驻能力句（`core/prompt_builder.py:535-540`）加半句：「你也有一块自己的空间，可以长期记笔记、
   建资料表、写给自己的习惯（AGENT.md，写了下一轮起会一直带着）」。
6. `self/AGENT.md` 可见化：
   - `self_create` / `self_update` 的描述里写明 AGENT.md 的作用和 2000 字预算。
   - `self_list` 返回结果在 AGENT.md 不存在时附一行说明（不是错误，是提示）。
7. 第一轮 schema 精简：`MAX_LISTED_TOOLS` 保持 24，拆分后各类都不会被折叠；加一条断言测试防回退。

**不做**：不改 probe（Path A）的「用户明确要求才调用」规则；不改 autonomy allowlist 默认值；
不新增任何「必须/禁止使用某工具」的话术。

**验收**
- 新测试：旧式角色卡（`tool_categories` 只含 `info`）解析后能看到 `self`/`schedule`/`life` 的工具；
  不含 `info` 的卡看不到。
- 新测试：discovery 首轮 schema 中 `load_tools_self` 存在，描述含全部 `self_*` 名字。
- 既有回归：`pytest tests/ -k "tool_discovery or tool_exposure or character_self or reminder or listening" -n auto`。
- 文档：`docs/tool-discovery.md`（新分类与兼容展开）、`docs/tools.md`（各工具分类）、
  `docs/prompt-layers.md`（11.6 内容变化）。

---

## T2 · 角色自有结构化资料库 `self_db_*`

**目标**：角色可以给自己建表、写入、查询、修改，形成真正的「自己的数据库」。

**设计（已拍板）**
- 存储：每个 Reality `owner+char` 桶一个 SQLite 文件。在 `core/data_paths.py` 新增
  `character_self_db(char_id, uid)`，放在 self 文件根**之外**的同级目录（避免被 `self_*` 当文本文件读写），
  并在 `core/data_registry.py` 登记（per_char_user、备份策略 include）。所有路径经 `core/sandbox.get_paths()`。
- **不开放原始 SQL**。工具用结构化参数，标识符正则校验 `^[A-Za-z_一-鿿][\w一-鿿]{0,31}$`，
  值一律参数绑定：
  - `self_db_tables()`：列出表、列、行数。
  - `self_db_create_table(table, columns:[{name, type: text|number|bool|date}], description)`。
  - `self_db_insert(table, rows:[{...}])`（单次 ≤50 行）。
  - `self_db_query(table, where?, order_by?, limit≤50)`；`where` 支持 `eq/ne/gt/lt/contains/in`。
  - `self_db_update(table, where, set)`、`self_db_delete(table, where)`（where 必填，防全表误删）。
  - `self_db_drop_table(table)`：进回收（重命名为 `_trash_<ts>_<table>`），复用 self 的 trash TTL 语义。
- 配额：表 ≤20、每表行 ≤5000、文件 ≤ `self_access.max_total_bytes`。超限返回 `quota_exhausted`，不静默删。
- 权限：与 `self_*` 同一 grant（`self_revoked` 时全部冻结）；category `self`；写工具加入 autonomy
  `_SANDBOXED_WRITE_TOOLS`（仍需 allowlist 开启）；群聊禁用沿用 dispatcher 现有规则。
- 每次写操作写 self 审计（`origin`、表名、行数，不含行内容）。这不是用户记忆写入点，不在 AGENTS 规则 6 范围，
  不调用 `provenance_log.append()`。
- 并发：复用 self 桶的 `threading.RLock`。
- 观测（AGENTS 规则 7）：扩展 `GET /observability/character-self` 返回表名、行数、文件大小，不含内容。

**工具描述要写清用途**：例如 `self_db_create_table`：「给自己建一张表，长期记录同类的东西
（比如她提过想看的电影、你们约好的事、你自己的计划）。以后用 self_db_query 查。」

**验收**
- 单测：建表/插入/查询/更新/删除/回收；非法标识符、缺 where、超配额、跨角色/跨 uid 隔离、grant 撤销。
- 单测：SQL 注入样例（表名、列名、值中带 `'; DROP`）全部被拒或作为普通值存入。
- 文档：`docs/tools.md` 新增小节；`docs/character-files-and-agent-autonomy.md` 能力表补 self_db；
  `docs/data-taxonomy.md` 登记新数据路径。

---

## T3 · 角色自制工具（声明式配方）

**目标**：角色能把常做的一串操作存成自己的工具，以后一句话调用。**不执行任意代码**——执行代码仍只能走
现有 `process_run`（system 分类，危险模式门控），本单不碰。开工前读 `docs/security_model.md` 信任边界章节。

**设计（已拍板）**
- 存储：配方是 self 空间里的文件 `self/tools/<name>.json`，复用 self 的 revision/trash/配额/审计，
  角色也能用 `self_read` 看到。
- 配方结构：
  ```json
  {"name": "...", "description": "什么时候用", "params": {"q": "string"},
   "steps": [{"tool": "self_db_query", "args": {"table": "电影", "where": {"标题": {"contains": "{q}"}}}},
             {"tool": "web_search", "args": {"query": "{q} 影评"}}]}
  ```
  `args` 里 `{param}` 做字符串替换，只替换在 `params` 里声明过的名字。
- 工具：`self_tool_define(name, description, params, steps)`、`self_tool_list()`、`self_tool_run(name, args)`。
- 执行约束（防提权，不是对角色的行为约束）：
  - 步骤里的 tool 必须是**本轮该角色实际暴露**的工具（用 `tool_exposure.resolve` 的结果判定），且不在
    `system`/`browser`/`phone_control` 分类、不是 `self_tool_*` 自身（禁止递归）、不是 `self_management` 工具。
  - 每步走 `execute_structured(origin="assistant_loop")`，照常经过所有既有闸门、审计、危险模式检查。
  - ≤5 步；任一步失败即停，返回已完成步骤的结果和失败原因。
  - `define` 时就校验步骤工具是否合法，非法直接拒绝，返回原因。
- 发现层：T1 的 `load_tools_self` 描述里附「你做过的工具：a、b、c」（读 `self/tools/` 目录名，最多 10 个）。

**验收**
- 单测：定义/列出/运行；参数替换；未暴露工具、危险分类、递归、超步数在 define 时被拒；
  某一步失败时的部分结果；角色隔离。
- 单测：配方经 `self_update` 改写后下一次 run 用新版本。
- 文档：`docs/tools.md`、`docs/character-files-and-agent-autonomy.md`（把「自制工具」从「不支持」改为声明式配方）。

---

## M1 · 强度 ≠ 情绪强度：写入打分、衰减、存量重算

**目标**：`strength` 只表示「这件事对理解你们的长期关系有多大代表性」，情绪激烈程度另存一个字段，
不再让它决定留存与召回。

**改动**
1. 新字段 `emotional_intensity`（0-1）。reflect prompt（`core/memory/fixation_pipeline.py:55-78`）改：
   - `strength` 定义改为：「以后回想你们的关系时，这件事有多大代表性/还需要记得多久。
     情绪激烈本身不代表重要；一次争执里的多轮来回只算一件事。」
   - 新增 `emotional_intensity`：「当时情绪有多激烈」。
2. `write_episode`（`core/memory/episodic_memory.py:241-260`）：**删除** sad/angry +0.1、happy +0.05、
   冲突标签 +0.2 三条加成；`first_tags` → `is_core` 的逻辑保留。旧数据没有 `emotional_intensity` 时读取按 `None` 处理。
3. `decay_all`（`episodic_memory.py:867-890`）：
   - 衰减率不再按情绪区分，统一 base_rate（取 0.03）。
   - 修复复利 bug：记录 `last_decay_at`，每次只按「距上次衰减的天数」衰减；缺字段时以 `timestamp` 初始化且本次不衰减。
   - 签名加 `char_id`，`core/scheduler/triggers/time_based.py:714` 改为对 owner 的所有 Reality 角色桶逐个衰减
     （用现有枚举角色桶的 helper；Grep `list_char_ids` / `user_memory_root` 找）。
4. 存量重算脚本 `scripts/rebalance_episodic_strength.py`：
   - **默认 dry-run**，只打印每个桶的条目数、将改动条数、强度分布前后对比，不写盘。
   - `--apply` 时先整文件备份（`episodic.json.pre_rebalance_<ts>.bak`），再对每条：
     若 `emotion_peak in (sad, angry)` 减 0.1；若标签命中旧冲突集合减 0.2；happy/surprised 减 0.05；
     下限 0.1；`is_core` 不动；写入 `emotional_intensity = 旧 strength`（作为近似）。用 `safe_write`。
   - 写一行 `provenance_log.append()`（artifact=episodic，reason=rebalance_strength）。
   - 交付时只给出命令，**不要替用户执行 `--apply`**。

**验收**
- 单测：写入不再因情绪/冲突标签加成；衰减与情绪无关；连续两天调用 `decay_all` 的结果等于按 2 天单次衰减；
  非默认角色桶被衰减。
- 单测：脚本 dry-run 不改文件；`--apply` 生成备份且数值符合规则。
- 文档：`docs/memory.md` 三、情景记忆（写入、衰减章节）。

---

## M2 · 冲突按「一整段」入库，连同澄清、道歉、修复结果

**目标**：一次争执存成**一条**包含起因、经过、澄清、结果的记忆，而不是 N 条「用户指责/双方争执」；
事后的和好能挂回原冲突，并且修复点**可被召回**。

**改动**
1. **情绪段落缓冲**替代逐轮 eager（`fixation_pipeline.py:1063-1072`）：
   - sad/angry 轮不再立即 reflect，而是把 `mid_id` 追加到 `fixation_state` 的 `open_emotional_run`
     （按 scope 存，含 `started_at`、`last_at`、`mid_ids`）。run 打开期间后续每轮（不论情绪）都追加。
   - 关闭条件（任一）：连续 3 轮非 sad/angry；距 `last_at` 超过 30 分钟（由下一次写入或 episodic_sweep 检查）；
     run 长度达 12（防 mid_term 20 条上限把前面挤掉）。关闭时入队一次
     `reflect_to_episodic(mid_ids=全部, trigger="emotional_run")`。
   - `happy` 保持现有 eager 行为不变；`force_reflect` 不变。
2. reflect prompt 对 `trigger=="emotional_run"` 追加要求，输出新增字段：
   - `episode_kind`: `"conflict" | "emotional" | "ordinary"`
   - `outcome`: `"repaired" | "clarified" | "unresolved" | "paused"`（仅 conflict/emotional）
   - `repair_note`：怎么收尾的（谁澄清了什么、谁道歉、最后状态），≤40 字；没有就空。
3. **事后修复挂回**：扩展 closure 判定。reflect 输出新增 `repairs_conflict: bool`（这一段是否在为之前的争执和好/澄清）。
   为真时，在同 scope 72h 内找 `episode_kind=="conflict"` 且 `outcome in (unresolved, paused)` 的最近一条，
   更新为 `outcome="repaired"`、`repair_note`、`repaired_by=<新 episode id>`、`repaired_at`。
   **不设 `status=resolved`**（resolved 会被召回排除）。旧的任务型 closure 逻辑保持原样。
   修改要调用 `provenance_log.append()`（AGENTS 规则 6）。
4. 格式化（`episodic_memory.format_for_prompt`）：conflict 条目渲染成
   「{narrative_summary} → 后来：{repair_note}（{outcome 中文}）」，让争执和结果总是一起出现。
5. `memory_janitor` 合并（`core/scheduler/triggers/memory_janitor.py:140-143`）：若任一方有
   `outcome/repair_note/repaired_by`，合并结果保留这些字段（优先 repaired），不能被高强度一方覆盖丢失。

**验收**
- 单测：模拟 6 轮 angry + 3 轮 gentle → 只产生 1 次 reflect 入队，mid_ids 为 9 个。
- 单测：run 超时关闭、超长关闭、happy 仍 eager。
- 单测：后续一段 `repairs_conflict=True` 把前一条 unresolved 冲突更新为 repaired，原条目仍可被 `retrieve` 召回。
- 单测：janitor 合并保留修复字段。
- 文档：`docs/memory.md` 三点五（中期写入/晋升）、三（数据结构新增字段）、三点八。

---

## M3 · 用户原话事实 vs 角色解读 分开存

**目标**：「用户明确说过的」和「角色对用户的理解」分开存放；解读不进长期事实与 identity。

**改动**
1. mid_term 压缩（`core/llm_client.py:1021-1023`）：改为分别归属，例如
   「用户：说了/做了什么；{char_name}：回应了什么」，总长 ≤40 字。去掉「主语用『用户』」的强制。
   角色名用 `get_char_name()` 插值（AGENTS 规则 9）。
2. reflect 输入：除 mid_term 摘要外，按 `source_event_ids` 取对应**用户原话**（截断每条 ≤120 字、总 ≤1200 字）一并给模型。
   找原话的 helper 用事件账本或 event_log 现有读取函数，不新造存储。
3. reflect 输出：`raw_facts` 拆为
   - `user_said`：用户明确说出的内容，尽量贴原话；
   - `char_reading`：角色当时的理解/推测（明确标为理解）。
   旧字段 `raw_facts` 保留为两者合并（兼容老读者），新读者优先用新字段。`user_state` 描述加「（推测）」语义说明。
4. identity 固化（`fixation_pipeline.py:80-127, 1483-1487, 1563-1565`）：
   - 输入改为每条 episode 的 `user_said` + 日期，**不再给 `char_reading` 和 `narrative_summary`**。
   - `evidence_count` 不再由 LLM 自报：由代码按「所引 episode 覆盖的不同自然日数」计算；
     同一天的多条只算 1。置信度公式里的 `ev` 用这个值。
   - 触发阈值（`:33-36, 167-184`）中「5 条 strength≥0.6」改为「来自 ≥3 个不同自然日」。
5. important_facts 抽取（`core/memory/user_profile.py:566-569`）：只记用户**自己明确说出**的关于自己的事实；
   删去「性格特点」「精神状态」「稳定的情感/关系」这类推断类目；不确定是否是用户原意的不写。
6. 语音来源降权（依赖 A3 的标记，A3 未合并时用 `getattr` 兜底为「非语音」）：
   `input_modality=="voice"` 且 `asr_low_confidence` 的轮次，不参与 important_facts 抽取与 identity 证据。

**不做**：不改 identity 的 8 个维度定义；不手动清理现有 identity.yaml（新规则下会自然重写）。

**验收**
- 单测：mid_term prompt 文本含角色名插值且不再强制「主语用户」。
- 单测：identity 证据 3 条同日 episode → evidence=1，不触发固化；3 个不同日 → 触发。
- 单测：reflect 解析兼容只有 `raw_facts` 的旧输出。
- 单测：important_facts 抽取 prompt 不含「性格特点」「精神状态」。
- 身份连续性 eval：`pytest -n auto tests/identity_eval/`。
- 文档：`docs/memory.md`（四、user_identity 更新机制；用户画像条目模型）。

---

## M4 · 召回混合分桶 + 去掉重复注入与情绪同调回路

**目标**：召回不是「相似度 Top-K + 强度加权」，而是近期 / 中期 / 长期稳定 / 历史修复点各取代表。

**改动**
1. `core/memory/episodic_memory.py` 新增 `retrieve_mixed(user_id, topic, *, char_id, history, long_term=False)`，
   复用 `retrieve()` 的候选与打分代码（`:359-485`），分四桶各取最优：
   - recent（<72h）：1 条，且与 `history[-10:]` `_is_similar` 的剔除（已在上下文里的不重复召回）；
   - mid（3-30 天）：1 条；
   - long（>30 天，或 `is_core`）：1 条；
   - repair（`outcome in (repaired, clarified)`）：1 条，按 M2 格式连同修复一起渲染。
   总数 ≤4。`long_term=True` 时 long、repair 各放宽到 2 条、recent 置 0（供 M5）。
2. 打分调整：
   - 删除情绪同调 `emotion_bonus`（`:463-465`）。
   - 融合分里 `strength*decay` 项权重保持配置值，但 M1 后 strength 已是「代表性」。
3. `core/pipeline.py:391` 改调 `retrieve_mixed`；返回结构与 trace 字段保持兼容（prompt_builder 不需要知道分桶，
   但 trace 里记录每条来自哪个桶，供 `recall_trace` 排障）。
4. 兜底召回（`episodic_memory.py:986-1049`）：改为从 long / repair 桶取，不再取「近 7 天高强度」。
5. 层 `9.5_episodic_top`（`core/prompt_builder.py:1388-1399`）：只有当首条来自 long 或 repair 桶时才注入；
   来自 recent 时跳过（它本来就在 history 里）。用 episodic_result 携带的桶标记判断，不要重新召回。
6. `get_episodic` 工具（`core/tool_dispatcher.py:191-195`）传 `allow_strengthen=False`。
7. `event_log.search`（`core/memory/event_log.py:504`）：>7 天的块不再要求 `intensity>=1`，关键词命中也可入选，
   避免「旧记忆只有吵过的才能浮现」。

**验收**
- 单测：构造 4 个时间段 + 1 条 repaired 冲突的库 → `retrieve_mixed` 各桶各 1；recent 中与 history 相似的被剔除。
- 单测：当前心情 angry 时 angry 记忆不再获得加分。
- 单测：兜底不再返回近 7 天高强度条目。
- 单测：9.5 层在首条为 recent 时不出现。
- `python tests/run_eval.py`（层激活回归，AGENTS 规则 4 精神）。
- 文档：`docs/memory.md` 三、召回章节；`docs/prompt-layers.md`（6c、9.5 行为）。

---

## M5 · 长期问题强制关系召回 + 相识时长事实

**目标**：「你怎么看我 / 我们认识多久 / 过去发生过什么」这类问题，不等模型自己想起来调工具，
系统直接把关系层记忆拉齐给它；相识时长用真实首次对话时间，由模型自己判断怎么说。

**改动**
1. `core/tag_rules.py` 新增 `TagRule("query.relationship_longterm", [...])`，词表至少含：
   认识多久、认识多少天、认识多长时间、在一起多久、怎么看我、觉得我是、你眼里的我、我在你心里、
   我们之间、过去发生、以前发生、这段时间以来、一路走来、我们的关系、你还记得我们。
   不要放「我们」「之前」这种过泛的单词（已被 `topic.relation` 占用且误触多）。
2. `core/pipeline.py::fetch_context()`（`:301-304` 之后）计算 `_long_term_query`；为真时：
   - 调 `retrieve_mixed(..., long_term=True)`；
   - 兜底召回不跑；
   - `event_log.search` 时间窗放宽到全部可用天数（仍取 top 5）；
   - 若 dossier 功能启用（默认关，不要打开它），用空查询取最近 3 份；
   - context 返回 `long_term_query=True`。
3. 新增只读 helper `core/memory/relationship_span.py::first_interaction_at(uid, char_id)`：
   事件账本 `MIN(occurred_at)`（同 scope）→ 回退 `min(event_log.list_days())` → 回退 episodic 最小 `occurred_at`；
   全失败返回 None。结果按天缓存。
4. `_long_term_query` 为真时，在 prompt 里注入事实层 `2.56_relationship_span`（加 `_layer`，AGENTS 规则 3）：
   「你们第一次对话是在 {YYYY-MM-DD}（约 {N} 天前）。」仅此一句事实，不加评价、不加「你们很熟/认识不久」之类的判断。
   None 时不注入。**只在长期问题时注入**——平时不常驻，持续时间由模型从召回的记忆里自己体会。
5. 观测：`recall_trace` 记录 `long_term_query` 与 helper 命中来源（AGENTS 规则 7 复用现有 trace，不新增端点）。

**验收**
- 单测：词表命中/未命中样例（含「我们去吃饭」不命中）。
- 单测：长期问题时 recent 桶为 0、long/repair 放宽、`2.56_relationship_span` 存在且只含日期句；
  非长期问题时该层不存在。
- 单测：helper 三级回退。
- `python tests/run_eval.py`；文档：`docs/prompt-layers.md`（新层）、`docs/memory.md`（召回）。

---

## M6 · storyline 周聚合修复（长期叙事层停摆）

**根因**（`core/scheduler/triggers/storyline_weekly.py`、`core/memory/storyline.py`）：
- 只发 `system` 一条消息，无 user 轮（`:259-260`）；部分供应商拒绝或返回空。`consolidation_worker.py:177-182`
  已经为同一原因补过 user 轮。
- 输入无上限：全部新 episode + 整个 inbox（≤200）+ 游标以来的全部 event_log 原文（`:218, :422-498`）。
- 输出 `max_tokens_override=1200`（`:261`），JSON 截断即 `invalid_llm_output`（`:264-276`）。
- `_plan_ops`（`:350-419`）任一 op 不合法整批拒绝。
- 失败不重试：`_mark` 在工作前执行（`:82`）+ 7 天冷却；游标不前进，下周输入更大，**失败自我强化**。
- 没传 `char_id`。

**改动**
1. 消息改为 `[system(规则), user(本批材料)]`。
2. 分批：每批材料 ≤40 条、≤12000 字；按时间顺序逐批处理，每批成功即提交游标与 inbox 消费。
3. `max_tokens_override` 提到 3000。
4. `_plan_ops` 改为逐 op 校验：合法的提交，不合法的丢弃并计数，记录到 `meta.aggregation.rejected_ops`。
5. 失败重试：失败时不消耗 7 天冷却，记录 `consecutive_failures`，按 6h、12h、24h 退避重试；
   连续 3 次失败写一条 WARNING（走 error.log 现有台账）。
6. 传 `char_id`。
7. prompt 增加：「一段争执和后来的和好/澄清，应作为同一条弧线的前后节点」——这是叙事结构说明，不是行为约束。
8. 观测：现有 storyline 观测端点（`docs/memory.md` 四点六「观测端点」）补 `consecutive_failures`、`rejected_ops`、
   `pending_inbox`、`next_retry_at`。

**验收**
- 单测：system+user 两条消息；超大输入被分批；部分非法 op 不影响合法 op 提交；
  失败后 `next_retry_at` ≈ 6h；连续成功清零失败计数。
- 文档：`docs/memory.md` 四点六。

---

## C1 · 删除 `6a_user_identity_coldstart` 全部

用户决定：整体删除，持续时间由模型从记忆判断（M5 提供真实相识日期）。

**删除清单**
- `core/prompt_builder.py`：`identity_coldstart` 参数（`:412`）、docstring（`:461`）、`elif identity_coldstart:` 分支
  （`:1007-1019`）、层登记表条目（`:1915`）。
- `core/pipeline.py`：context 文档注释（`:249`）、计算块（`:441-447`）、context 字段（`:699`）、
  `build(... identity_coldstart=...)`（`:796`）。
- `core/memory/fixation_pipeline.py`：`_had_valid_before` 计算与 `identity_coldstart` 日志块（约 `:1635-1660`）。
- `admin/routers/memory.py`：`/fixation/status` 返回里的 `identity_coldstart` 字段（`:420, :427`）、
  整个 `/fixation/identity-coldstart-summary` 端点（`:431-`）。
- `tests/test_identity_coldstart.py`：整文件删除（测试随功能删，AGENTS 删除原则）。
  若 `/fixation/status` 其他测试断言了该字段，一并调整。
- 文档：`docs/prompt-layers.md:57` 行与 `:407` 中的提及；`docs/known-issues.md` identity-2 条目改为
  「已移除冷启动提示层（2026-10），冷启动期不再注入任何关系时长判断」并标记关闭；`docs/memory.md` 若有提及一并删。

**保留**：`core/scheduler/rhythm.has_real_interaction_history()` 被调度触发器使用（diary、interest_seed），**不要删**。

**验收**：`Grep coldstart` 只剩 `docs/known-issues.md` 的关闭说明；
`pytest tests/test_rhythm.py tests/test_execute_dryrun.py tests/ -k "prompt_builder or fixation or memory_router" -n auto`；
确认没有客户端引用该端点（审计时桌面/手机仓均无引用）。

---

## A1 · 提醒到点改由角色生成再投递

**改动**
1. `core/scheduler/loop.py` 新增 `async def _compose_trigger_reply(oid, char_id, prompt, *, search_query, recall_policy) -> str | None`：
   从 `_pipeline_send` 的生成段（scope freeze → `conversation_lock` → `fetch_context` → `build_prompt` → `run_llm`，
   `:540-575`）抽出，**只生成不落盘不发送**（不写 trigger stub、不 `record_assistant_turn`）。
   不经过 `MIGRATED_TRIGGERS` 分流，也不过 perceive_event dedupe（投递侧 talk_gate 已有 correlation 去重）。
   **锁必须在 helper 返回前释放**：`talk_gate.send` → `record_assistant_turn` 会再次获取
   `conversation_lock(uid)`（`core/turn_sink.py:88-90`），该锁不可重入，嵌套持有会死锁。
2. `_check_reminders`（`:855-866`）：先用 `_compose_trigger_reply` 生成，刺激文本写成导演注释风格（参照
   `core/scheduler/triggers/dream_exit.py:248-279`）：
   「（你之前设的提醒到时间了，事项是：{content}。现在用你自己的口吻自然地提醒{user_pronoun}。
   不要提到“备忘录”或复述这段说明。）」，`recall_policy="anchored"`，`search_query=content`。
   用户称呼用 `get_user_display_name()`/现有人称 helper，不写死名字（AGENTS 规则 9）。
   生成成功 → `talk_gate.send(oid, char_id, reply, source="user_schedule", ...)`，其余参数不变。
3. 生成失败：`finish_delivery(sent=False)` 走既有回退重试；`delivery_attempts >= 3` 时改发最小中性文案
   「到时间啦：{content}」，保证提醒不丢。
4. `add_reminder` 的 `content` 描述（`core/tools/reminder_tools.py:146`）补：
   「写成到时要提醒对方做的事本身（如“喝水休息”），不要写成给自己的备忘或提醒方式」。
5. `docs/scheduler.md:634, :1161-1166` 同步：说明投递仍走 talk_gate，但文本由角色生成。

**验收**
- 单测：到期提醒 → `run_llm` 被调用一次、`talk_gate.send` 收到的是生成文本而非模板；
  生成失败 → 不发送且退回；第 3 次失败 → 中性文案。
- 单测：发送的文本里不出现「备忘录提醒时间到了」。
- 回归：`pytest tests/ -k "reminder or talk_gate or scheduler_loop" -n auto`。

---

## A2 · 小红书读取服务失联可见 + 启动说明

**改动**
1. `core/tools/xiaohongshu.py:150-151`：`ConnectError`/超时单独识别，`logger.warning` 一条
   「读取服务 {reader_url} 无响应（可能未启动 Docker 容器或本地读取进程）」，同一原因 10 分钟内只记一次（参照工单 F 的降噪做法）。
   返回给模型的结果改为可区分的 `reader_offline`，模型可以如实告诉用户「读取服务没开」。
2. `core/xiaohongshu_service.py` 启动时：若 `_policy()` 为 `remote_deployment` 或 `not_installed`，INFO 记一次
   「后端不托管读取服务，需外部启动」，并对 `{reader_url}/health` 做一次 2s 探测，不通则 WARNING。
3. `docs/xiaohongshu-reader.md` 增加「日常启动」小节：读取服务与 Python venv 无关；推荐 Docker Desktop
   开「登录时启动」+ 容器 `unless-stopped`；或改用原生 exe 时先停容器、避免抢 18060 端口；用
   `/api/v1/login/status` 检查登录态。不写本机绝对路径（AGENTS 规则 11）。

**不做**：不改 `deployment.mode`；不改用户的 bat 启动脚本。

**验收**：单测模拟 ConnectError → `reader_offline` 且日志限频；启动探测失败只告警不阻塞启动。

---

## A3 · 语音输入标记 + ASR 不确定提示 + 置信度降权 + 记忆打标

**目标**：模型知道这句话来自语音转写、可能有错字和断句错误；低置信时进一步提示；语音来源在记忆里可辨认。
注意：提示只说明**输入的可靠性**，不规定角色怎么回应。

**改动（后端）**
1. `admin/routers/transcribe.py`：`_run_backend` 改为返回 `(text, quality)`，quality =
   `{avg_logprob_mean, avg_logprob_min, no_speech_max, dropped_segments}`（faster-whisper 有；sherpa、远程 json 为 None）。
   远程若 preset 支持 `verbose_json`，提供可选配置项启用，默认不变。
   响应体增加 `asr_quality`，**所有路径都签发回执**（`:171` 本地路径目前不签），回执 payload 带 quality。
2. `core/audio_perception.py`：`consume_receipt` 对「仅标记语音来源」不要求 `stt_presets.enabled`/声学分析开启；
   `prompt_hint()` 无论有无声学结果，都先输出一句：
   「这条消息来自语音转写，可能有同音错字、漏字或断句错位；按整体意思理解，个别奇怪的词不一定是对方的原话。」
   低置信（`avg_logprob_mean < -0.8` 或丢弃段 ≥1，阈值做成 `audio_music.asr_low_confidence_logprob` 配置）时再加一句
   「这次识别质量偏低，拿不准的地方可以直接问」。
3. 支持一条消息多个回执：`desktop_chat` 接受 `voice_receipt_ids: list[str]`（兼容旧单个字段），全部消费，
   取最差 quality。
4. 记忆打标：`desktop_chat`（`admin/routers/chat.py:885-893`）与 `admin/routers/mobile.py` 对应入口，有语音回执时给
   `run_owner_chat_turn` 传 `audit_extras={"input_modality": "voice", "asr_low_confidence": bool}`，
   确认它们经 `capture_turn` 落到 event_log meta / 事件账本（`core/pipeline.py:1875-1887`）。
   mid_term 摘要对语音轮加「（语音）」前缀，供 M3 消费。

**改动（桌面，`Emerald-client`）**
5. `RoomWindow.tsx:88-92` 累积本条消息所有分段的回执 id；发送时传 `voice_receipt_ids`（经 `backend.ts` / `src-tauri/src/lib.rs:1003-1022`）。
6. 协议变化同步 `Emerald-client/docs/protocol-v0.md` 与后端 `docs/three-repo-interface-catalog.md`。

**验收**
- 后端单测：本地 STT 也返回回执；prompt 含语音转写提示；低置信加句；多回执合并；
  `audit_extras` 进入 capture。
- 桌面：`npm run build` 通过；与 A4 合并后做一次真机通话验证（说一句含停顿的长句），浏览器/客户端实测未完成须在交付说明写明。
- 文档：`docs/audio-perception.md`、`docs/channels.md`（desktop_chat 新字段）。

---

## A4 · 通话断句：在途分段不抢发、中文不加空格（桌面）

文件：`Emerald-client/src/windows/room/useContinuousCallVoice.ts`、`Emerald-client/src/windows/room/RoomWindow.tsx`。

**改动**
1. hook 暴露 `isVoiceActive`（当前分段已有有声帧）、`pendingCount`、`transcribing`、`lastVoiceAt`。
2. 自动发送（`RoomWindow.tsx:196-202`）条件改为：`!transcribing && pendingCount===0 && !isVoiceActive`
   且距 `lastVoiceAt` ≥1500ms（常量化，可调）。任一条件不满足就推迟检查。
3. 拼接（`:89, :165`）：前后两段的衔接字符都是 CJK 时不加空格；否则保留空格。
4. 6s 硬切（`useContinuousCallVoice.ts:103-104`）改为：超过 4s 后遇到 ≥300ms 的短停顿就切；仍保留 8s 绝对上限。
5. 队列满丢最老段（`:82-84`）时 `console.warn`，不再静默。

**不做**：分段边界丢音（`onstop` 重启录音器）需要双录音器或 AudioWorklet，属于较大改动，本单不做，
在 `docs/known-issues.md` 现有条目上补一句仍 open 即可。

**验收**：`npm run build`；如有前端测试框架则补纯函数测试（拼接规则、发送条件）；
真机通话说一句「我今天……（停 1 秒）……有点累」应为一条消息。

---

## A5 · 文件预览浮层随窗口与字号缩放（桌面）

文件：`Emerald-client/src/windows/chat/components/ChatPanel.tsx:2128-2163`、`:711-718`。

**改动**
1. 卡片尺寸：`width: 'min(92vw, 1100px)'`，`height: 'min(86vh, 900px)'`（替代仅有的 `maxHeight`），
   iframe `flex: 1, minHeight: 0`。
2. 字号传入 iframe：`onPreviewArtifact` 拿到 HTML 后，在 `<head>`（没有则在开头）插入
   `<style>html{zoom:${themeFontSize/14}}</style>`。后端 CSP 允许 inline style（`admin/routers/chat_artifacts.py:52-55`），无需改后端。
3. 标题栏、关闭按钮字号使用 `chatThemeFontSize()`（`src/shared/chatAppearance.ts:101-103`）。

**验收**：`npm run build`；客户端实测：拉大/缩小聊天窗口浮层跟随；调大主题字号预览正文跟随。
无法实测时交付说明写「浏览器实测未完成」。

---

## A6 · 角色可原地修改聊天产物文件

**改动**
1. `core/tools/chat_artifacts.py` 新增 `update_artifact(artifact_id, content, expected_sha256?)`：
   覆盖同一 id 的文件（`safe_write`），更新 index 中的 `updated_at`、`size`、`revision += 1`；
   仅限当前 `(char_id, uid)` 自己的产物；保留上一版到 `{artifact_id}.prev{ext}`（只留一版）。
   本轮 payload 带上该产物，使前端出现「已更新」的文件卡。
2. 注册进 `_TOOL_REGISTRY`（category `artifacts`，补 `examples`、`keywords`，AGENTS 规则 2）。
   描述写明：「改你之前给对方的文件，用这个而不是 write_artifact 新建一份」。
3. 50 个上限淘汰时写一条 INFO 日志（目前静默删除）。

**不做**：前端文件列表、二进制产物（记入 `docs/known-issues.md` roadmap）。

**验收**：单测更新/版本/跨 scope 拒绝/并发 sha 冲突；`GET /chat/artifacts/{id}` 下载到新内容。
文档：`docs/tools.md`、`docs/character-files-and-agent-autonomy.md:124` 表格。

---

## 已拍板的决定

1. **不给角色加行为约束**。记忆组只改机制，工具组只补方向感。
2. 存量 episodic 强度重算只提供 dry-run 默认的脚本，由用户自己决定是否 `--apply`。
3. 自制工具是**声明式配方**，只能串联本角色本轮已暴露的工具，不执行任意代码。
4. 相识时长只在检测到长期问题时注入一句日期事实，不常驻、不加判断。
5. Memory dossier（Brief 258）保持默认关闭，本组不打开。
6. autonomy allowlist、`deployment.mode`、Docker 配置等用户运行时设置不改。
