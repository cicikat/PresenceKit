# Prompt 统一规范与链路审计

核对日期：2026-09-11；对应 Brief 247–249。范围为 prompt 来源与生成接线，不是全仓死代码证明。

## 统一约定

框架对当前发言角色的指令统一用“你”；角色自述中的“我”指角色本人。
用户事实用显示名或用户选定的第三人称称谓；未选择时默认“她”，支持“他 / 祂 / TA / 它”。称谓不等于生理性别，不从名字、生理数据或关系推断性别。
多角色材料用真实 speaker/角色名标明归属，不用全局 active character 代替当轮角色。
引用、日记、世界书、角色卡和工具返回保留正文，不以全文替换修改人称。
模板插值只发生在明确声明的槽位；不对注入资料执行第二次模板展开。

分类器、工具探针、记忆提取器具有独立任务身份；不强制扮演聊天角色。
Dream 保留独立身份/退出协议与渲染格式，不能将 Reality 规则直接覆盖过去。
框架负责事实边界、来源、工具状态与通道格式，角色资产负责具体文风与关系表达。

## 来源与消费地图

| 来源 | 消费链 | 视角与保真边界 |
|---|---|---|
| 角色卡 system/description/personality/scenario、世界书、author notes | character loader → `prompt_builder.build` 的 1/2/5.5/11 层 | authored 正文保留；框架外层明确角色 |
| 时间、presence、mood、health、screen | builder 的 1/2.5/2.6/3.x 层 | 角色状态第二人称，用户数据明确主体；保留新鲜度闸 |
| profile、identity、episodic、event、mid-term、diary | `Pipeline.fetch_context` → `build_prompt` → builder 5/6/9 层 | 事实/观察/历史/梦境/外部资料不同来源，不互相改写 |
| QQ、desktop、mobile 当前输入与短期历史 | main / owner-chat → Pipeline → builder | 真实 role/speaker 保留；历史脱敏与去同质机制另有职责 |
| Path A 工具探针 | `get_probe_prompt` → dispatcher → builder 10 层 | 探针是工具调度器；结果是数据，失败不是完成事实 |
| Path C 工具循环 | `run_agentic_loop` → filtered schemas → `llm_client.chat_turn` | 最终 schema 决定可用工具；每步 tool 消息决定执行事实 |
| native voice / monologue | `thinking.maybe_apply` → llm_client | 内心文风允许第一人称；不是历史；有独立控制面 |
| Reality Stage | `core/stage/context.py` → scoped generation → builder | 在场者/私聊/共享 transcript 分开，明确 speaker |
| Dream / Dream Stage | `core/dream/dream_prompt.py` / stage dream runtime | D0–D10/DS/DM 独立，引用真实角色名；D1/D8 仍有视角混杂 |
| 记忆反思与 identity consolidation | `fixation_pipeline` 的独立模板 → consolidation 调用 | 提取任务，不是聊天人格；禁止借统一人称改写存储 |

## 证据与处置

以下是审计时基线；每条最终结果在后续工单完成后更新。

| ID | 证据 | 基线结论与处置 |
|---|---|---|
| P01 | builder `_normalize_injection` 被最终 system 循环调用，将“用户”及英文 user 替换为“她”；`char_name` 参数未使用 | 活代码缺陷，影响引用和通用性；248 删除替换器及专用旧测试 |
| P02 | builder 读 mood 后，只在 system_prompt 含精确标题+槽位时插入 mood；空 system_prompt 整段跳过 | 条件断链；249 修复为框架拥有的稳定注入位置，保留旧槽位兼容 |
| P03 | `mood_text.get_mood_text` 使用 `_char_name()`；builder 已持有 scoped character | 存在错角色命名风险；248 将聊天状态文案统一为“你此刻”，管理面仍可显式显示名称 |
| P04 | builder `LayerSpec` 仅定义；core/admin/scripts/tests 与双端 source 未见消费者 | 幽灵结构候选；249 删除类和专属 imports，保留实际工作的 `_layer` metadata |
| P05 | dispatcher `format_tool_capability_note` 只有定义和三个 R5 测试，无生产调用；实际路径是 probe builder / `get_tools_schema` | 未接入的旧工具名单生成器；249 删除连同专用测试，不接回缺少完整 effective gates 的名单 |
| P06 | builder layer 11 写“本轮没有任何工具执行结果/禁止声称调用”；Path C 后续增加 tool 消息却保留该段，nudge 注释承认冲突 | 静态规则与多步结果冲突；249 改成随实际返回结果判定的规则，不依赖 nudge 开关补救 |
| P07 | `prompt_capture.capture_injected_messages` 的生产调用在 thinking；Path C 后续 loop_msgs 的 nudge、image refs、tool 消息没有相同刷新调用 | open：builder/thinking 快照不等于整个工具循环最终请求；独立全步请求观测后续处理 |
| P08 | docs/prompt-layers 声称“正式主 LLM 没有 tools schema” | 文档过期，不是主链断开；249 按 Path A/C 区分更正 |
| P09 | `9.5_episodic_top` 重复最相关记忆；2.55 与尾部 gap 提示重复时间 | 有意的注意力/位置强化，不能只按重复删除；observe：真实模型消融后决定 |
| P10 | `prompt_style` 仅 XML 包装；thinking 层已有实际调用与 capture 刷新 | 保留，不属于幽灵代码；不能以统一视角为由删除 |
| P11 | Dream D1 第三人称，D8 “你的意志…属于用户一侧”；thinking voice 有文体内“我/你” | roadmap：独立 Dream/心声视角校准，必须保留退出、模式和声音约定，不做机械替换 |

搜索边界为当前仓库生产源码、测试及双端相关 source；不证明仓库外私有扩展没有导入旧 helper。

## 三面闭环

- 管理面：`/observe/prompt-layers/{uid}` 和 Dream/probe 观测使用 `memory.read`；消融 `/prompt-ablation` 与 `/dream-prompt-ablation` 使用 `admin`。本轮沿用这些端点，不新增落盘状态或设置开关。
- 桌面：`src/shared/api/adminBridge.ts` → Rust `admin_bridge::open_admin_panel` 打开管理面；角色文本变化由既有 chat/WS 消费，客户端不组装本轮 system prompt。
- 手机：`lib/services/backend_client.dart` 的 `/mobile/chat` → mobile router → `run_owner_chat_turn`，继承后端模板；无新增原生开关、权限、ack、TTL、队列或通知协议。
- 只改后端模板和文档，不改静态资产；本轮不宣称真实浏览器/真机联调完成。

## 迁移与验收范围

248 先收敛 Reality builder 自有文案和原文保真，不改 tag/召回/层顺序/预算。
249 清理已证实无生产消费者的代码、修复 mood 条件断链和多步工具事实指令冲突。
保留现有逻辑层的顺序、裁剪和观测，将身份、状态、资料、历史、执行事实、输出规则的职责写清楚；不把多个消息合成大字符串。

离线回归覆盖主体、引用保真、层元数据、裁剪/消融、旧槽位兼容、工具成功/失败和调用链。
真实模型文风 A/B、多角色同名歧义与 Dream/心声专项统一仍为 observe/roadmap，未调用真实模型评测。

Brief 248 结果：P01 已删除，P03 的 Reality builder 已使用显式第二人称；thinking mood 的命名随后由 249 修复。空角色卡也有同一身份锚。首轮 164 passed，修正两项本任务失败后补充回归 53 passed；两项生产目录全量指纹测试因运行中的无关 SQLite 变化失败。

## Brief 249 实施

- P02：mood 现在在框架层追加，与 authored 标题解耦；顺带修复无槽位时 perception 丢失，旧槽位与消融继续工作。缺失/损坏/空 mood 不生成虚构状态。
- P03：thinking 的 scoped mood 同样用“你此刻”，管理面保留命名显示。
- P04/P05：删除未使用结构、旧工具名单生成器与三个仅测试该生成器的用例；实际 probe/schema/read_diary 注册保留。
- P06/P08：单一工具事实规则面向每一步当前上下文；初始提示只描述组装阶段，后续结果同样可用。不再依赖 nudge 覆盖禁令，文档按 Path A/C 更正。
- P07/P09/P11 保留 open/observe/roadmap。不是全仓或全部 Dream/心声模板已经统一。

### 框架规则归属

| 职责 | 唯一主要归属 | 保留的逻辑边界 |
|---|---|---|
| 当前发言者与材料人称 | 1_system_prompt | 当前身份和引用作者不同 |
| 状态与感知 | 1/2.5/2.6/3.x | 角色状态、用户状态、TTL 分开 |
| 事实与资料 | 5/6 系列 | 观察/历史/外部资料/梦境不互转 |
| 示例与历史 | 7/9 | style example 与真实 role/speaker 不合并 |
| 工具执行事实 | 11_author_note 通用规则 + 10/tool 返回证据 | 软件执行闸不由 prompt 代替 |
| 输出格式与角色文风 | 11 的通道格式 + authored note | 已删除框架对角色职业身份的固定排除 |

不增加一套旁路 renderer/registry。现有 `_layer` 与来源、顺序、裁剪和消融继续承担可观测的模块边界。

249 验收：首轮 212 passed，修正新增循环测试 fixture 后 61 passed，收尾 mock 调整后四项循环测试复测通过。现有 probe/schema、thinking、裁剪/消融、引用/角色边界通过；无真实模型或真机验收。


## 用户称谓接线补充（2026-09-11）

current：复用 GET/PATCH `/users/{user_id}/pronoun`（admin），默认“她”，允许“她 / 他 / 祂 / TA / 它”；管理面「个人设置 → 用户称谓」编辑，保存后下一次组装生效。GET 返回有效称谓，原始配置可经 `/users/{user_id}/facts` 观测。Reality 身份约定、事实边界、长期观察、日记/历史/重点事实框架使用所选称谓；显示名及多人 speaker 归属保留。引用、角色卡、历史正文不做全文替换。未新增配置源、存储或网络调用。

桌面使用现有 openAdminPanel 管理面桥，手机 `/mobile/chat` 继承后端组装；无新 WS/IPC/ack/TTL/锁或通知协议。手机原生称谓编辑及 Dream 专项称谓统一为 roadmap；真实模型文风、双端真机体验为 observe。

thinking 原生提示标题改为【你们约定的思维链thinking输出方式*特调】，保留原有文风正文与执行闸门。

验收补充：82 项称谓、Reality 原文保真及 thinking 回归通过。Chromium 清缓存硬刷新真实管理面静态资源，合成 API 下验证五种称谓保存与回显；未写真实用户配置。管理面全局 i18n 检查有 3 项既有失败（IME/小红书翻译覆盖及旧资源版本断言），不作为本次功能通过项。
