# PresenceKit 工单｜按业务模块与职责边界拆分后端及管理面板（Architecture Refactor · AR-01）

> **交给 Codex 执行。** 本工单不是讨论或仅输出审计报告，要求在仓库中实施、测试、提交并交付可核验结果。
>
> 仓库：`cicikat/PresenceKit`（后端 + Admin；本轮**不改**桌面端、手机端两个仓库）。
>
> 参考快照：2026-11-15 GitHub `main` 提交 `7cd436e019c3aca51617f3570424f3654805d1d6`。**施工时以工作区当前代码和最新规范为准，禁止直接覆盖后续改动。**
>
> 施工约束：**直接在当前仓库、当前工作分支工作，禁止新建施工分支或 worktree**；尊重既存未提交修改。完成每个阶段必须独立提交一次 Git commit，再进入下一阶段。不要在未明确授权的情况下部署 VPS 或改变生产数据。是否 push/合并按当前用户授权及仓库规范执行；不能以推送/合并作为擅自改动分支的理由。

## 一、背景与已核实的问题

这次重构的目标是提高长期维护、定位故障和独立测试的效率，**不是追求单文件行数越少越好**，也不是从原生 JS 重写为 React/Vue。

已在参考快照核实：

| 区域 | 现状 | 实际问题 |
| --- | --- | --- |
| `core/tool_dispatcher.py` | 约 3,571 行；注册表、Schema/探针暴露、权限/参数校验、工具执行、结果与观测混合 | `_execute_structured_impl()` 约在 2998–3511 行，许多工具名称专属分支及调用签名适配集中在一处；修改某类工具容易触碰中央执行器 |
| `admin/routers/chat.py` | 约 1,477 行；`run_owner_chat_turn()` / `_execute_owner_chat_turn_locked()` 串起完整消息流程 | 后者约 440 行，混合探针、上下文、生成、流推送、异常、过滤、记忆收口和广播，故障定位与回归边界不清晰 |
| `admin/routers/dream.py` | 约 1,975 行，约 34 个路由声明 | Dream 会话、退出/挽留、档案、状态观测、预设、世界文件、剧本管理集中在同一 FastAPI Router；部分业务操作直接写在路由层 |
| `admin/static/index.html` + `admin/static/js/*.js` | 页面加载大量经典 `<script>`，共享全局函数、事件委托与 DOM | 已有按页面拆分，但业务脚本之间的隐式依赖、公共渲染函数和页面初始化顺序仍需梳理 |
| `admin/static/i18n.js` | 约 7,723 行，主要为中英字典 | 末尾还包含翻译运行时、Legacy DOM 翻译、`MutationObserver` 以及原生 `prompt/confirm/alert` 包装，文本与行为混放 |

**已有架构不能忽略：** `core/tools/*` 中有独立实现及 `register_tools()` 接入；`core/pretool_router.py`、`core/turn_sink.py`、`core/agent_runtime/`、`core/dream/*`、`core/owner_turn_service.py` 已在承担业务职责；管理面板新版与旧版共用业务 handler 和 API。必须复用这些现有模块，不许平行造第二套。

## 二、总目标与不可突破的合同

**结构原则：按业务域归属功能，按职责层区分入口 / 编排 / 授权 / 执行 / 展示。**

- 新增同域功能，尽量只改对应业务模块及其注册定义，不必编辑中央 500 行 `if/elif tool_name`。
- 发生故障时，可定位到清晰的一层（例如：准入、参数、调用、模型输出、流式传输、记忆落盘、浏览器视图），日志能标识阶段；不为了排错新增泄漏敏感正文的日志。
- 公共入口尽量薄，业务规则有唯一权威实现；必要的兼容转发必须指向**同一对象和同一实现**，禁止复制状态、缓存或并行维护两份逻辑。
- **100% 保持现有对外行为**：FastAPI 路径、方法、请求/响应字段、HTTP 状态码、WebSocket 消息事件与排序、配置与默认值、工具名称与 Schema、工具权限、Dream/Reality 隔离、uid/char_id 作用域、数据目录、已定义的错误与未决状态语义。
- 不修改 `data/`、`userdata/` 内容或数据库 Schema，不做迁移，不重置凭据，不清除历史，不新增功能开关，不改 LLM prompt 内容，不调整模型路由或工具使用策略。
- 不修改生产发送顺序、先落盘再推送等既有不变量；不得让未经确认的工具结果冒充成功。
- 不因为重构顺便启用停用的功能、删除 Legacy UI、改变原有安全策略或加入高风险操作确认弹窗。
- 生产代码改动遵守 `AGENTS.md`、`docs/testing-and-merge.md`、`docs/tools.md`、`docs/agent-runtime-architecture.md`、`docs/admin-ui-migration.md`、`docs/three-repo-interface-catalog.md` 等当前规范。若文档与实际代码有差异，以现有权威实现和已验证合同为准，记录差异，不暗中更改业务。

## 三、开工前基线（必须完成，不能停在这一阶段）

1. `git status --short`、`git rev-parse HEAD`；识别本地改动并避让。若同一区域已有未提交变更，先阅读差异，不允许 `reset --hard`、`git clean -fd` 或直接覆写。
2. 绘制简明调用与依赖图，至少包含：
   - Path A 预探针、Path C `run_agentic_loop`、`get_tools_schema`、`execute_structured`、`_TOOL_REGISTRY` 的调用与注册路径；
   - `/desktop/chat`、`/mobile/chat`、`/v1/owner/turns` → `run_owner_chat_turn` → Pipeline → `turn_sink` → desktop/mobile 的实际路径；
   - Dream `/dream/*` 各 API → `core/dream/*` → 数据写入/读取；
   - Admin `index.html` 脚本装载、公共状态/API 客户端、页面脚本、i18n 与新旧 UI 切换路径。
3. 导出对比基线：注册工具名称、类别、参数 Schema、暴露过滤与权限、关键 API OpenAPI 路由签名、UI 页面/脚本加载顺序与关键互动样本。敏感字段不可写入基线。
4. 识别动态 import、循环依赖、测试中 monkeypatch 到 `core.tool_dispatcher` / `admin.routers.chat` 的名字、`_TOOL_REGISTRY` 引用和加载即注册等副作用；记录必须维持的兼容导出。
5. 跑相应定向测试拿到**真实基线**（失败/跳过完整分类，不能预设全部绿）。一次性测试产物存临时忽略目录；不碰生产数据。

形成简短的 `docs/refactors/modularization-ar01.md`：依赖图、目标边界、基线快照、迁移顺序、风险和验收证据持续更新。不要复制整本架构文档。

## 四、阶段 A（P0）｜工具模块按业务归属，执行链按职责分层

**关键原则：保留旧入口 `core/tool_dispatcher.py` 作为兼容 Facade，迁出真正职责；`core/tools/` 现有实现不重复创建。** 新模块路径按依赖现状微调，建议目标结构：

```text
core/
  tool_dispatcher.py             # 稳定外部入口/兼容导出（瘦 Facade）
  tool_runtime/
    registry.py                  # 唯一注册表构建、汇总和初始化
    exposure.py                  # schema 暴露、配置过滤、分类/探针支持
    validation.py                # 参数校验/portable schema 的调用侧校验
    policy.py                    # 来源、身份、角色、权限、scope 和副作用准入
    invoker.py                   # 工具调用签名适配/执行（按注册元数据驱动）
    outcome.py                   # ToolExecutionOutcome、结果归一化、trace/状态
  tools/
    <已有领域模块>.py             # 各域工具自身的实现及 register_tools()
```

此目录为**责任示意**，不是强制增加每个文件；某个既有模块已承担对应工作则直接复用。避免为几行代码造一层无意义 wrapper。

具体施工：

1. 盘点 `_TOOL_REGISTRY` 来源。让工具实现与 Schema 注册尽量相邻，按**信息/网络、记忆、桌面/设备、文件与 Agent Runtime、生活/媒体、外部连接**等现有业务归属注册。尽量复用 `core/tools/*.py::register_tools(registry)`；不要把所有工具定义平移到另一个超大 `registry.py`。
2. 将统一校验/授权/来源/角色闸门收敛到可测试的共享函数，**不允许工具模块绕过统一校验**。区分通用闸门和工具特定的授权前置条件，不能根据 `dangerous` 标签擅自恢复已废弃的确认策略。
3. 替换中央执行器中能够抽离的 `if/elif tool_name` 调用适配分支：优先为工具定义结构化的调用适配器或执行元数据，统一把服务器可信 `uid`、`char_id`、`origin`、`session_state` 传给相应 adapter；模型参数不允许覆盖这些可信主体字段。**绝不把关键的 per-tool owner/scope 校验当成普通参数拼接而丢失。**
4. 把 portable schema/`get_tools_schema`/Path A 探针与 Path C 动态工具发现的入口绑定保持一致；注册顺序若影响可见排序或测试，按基线保留。不能使工具在未加载其分类前被执行。
5. `ToolExecutionOutcome`、工具状态（成功/缺参/拒绝/失败/结果未知）、`ToolResult.safe_summary`、action trace、工具状态推送、persist 指纹去重和结果写入行为保持原语义；不得仅把失败异常统一吞成字符串成功。
6. 保留 `core.tool_dispatcher.ToolDispatcher`、`execute_structured`、`get_tools_schema`、`register_send_callback`、`tool_loop_active` 等现有公开调用点的兼容性。`_TOOL_REGISTRY` 如有运行期注册/monkeypatch，Facade 与新模块须**共享同一个可变注册表对象**而非 copy；如测试替换模块属性导致兼容困难，应先理清使用者，再给出等价但可维护的注入 seam。
7. 避免 `tool_runtime.registry` ↔ `tool_dispatcher` ↔ `core.tools` 相互导入造成启动循环；保证重复 import 不会重复注册或覆盖状态。

**阶段 A 验收**：所有工具目录和 Schema 相对基线的差异为 0（除明确解释的展示顺序）；Path A / Path C、MCP、文件只读/编辑、Computer Use/Browser/Agent Session、角色授权、权限拒绝、失败与未决结果回归通过；`tool_dispatcher.py` 只保留稳定入口及必要的薄兼容逻辑，不再是业务实现的唯一归宿。阶段完成独立 commit。

## 五、阶段 B（P0）｜Reality 聊天从 Router 中提取可独测流程

不改变 Pipeline 的真实权威，不造第二条聊天链。建议拆成：

```text
admin/routers/chat.py                  # 路由 + 安全的兼容入口
admin/routers/chat_media.py            # 下载/预览/上传端点（若路由迁移有净收益）
core/owner_chat/
  service.py                            # owner turn 主流程、scope 冻结、conversation_lock
  prepare.py                            # 探针/上下文并行、构建 prompt
  generation.py                         # 普通/Agent 路径选择，流式读取
  delivery.py                           # 输出过滤、stream delta/end/canonical、turn_sink
```

实际文件可少于示意，但下列**四个边界必须明确**：请求准入与会话身份；生成前准备；模型生成与流式传输；最终消息记录与投递。

1. `admin.routers.chat.run_owner_chat_turn()` 保留可被 `mobile.py`、`owner_turn.py`、其他生产入口和现有测试 import 的 API/签名，内部委托唯一 service。
2. 冻结的角色 scope、`EventContext`、可信用户输入与引用回复 `reply_to`、`conversation_lock` 的覆盖范围，必须原样继承；异步探针和上下文的兄弟任务取消语义不能退化。
3. 保持当前 streaming 触发条件：哪些通道流式、何时非流式；`ToolProtocolStreamFilter`、分段器、异常时 partial delta、`stream_start` / `stream_delta` / `stream_end` 与最终 canonical `channel_message` 的顺序、ID 去重行为不得改变。
4. `invalid_assistant_tool_protocol`、空回复 502、上游错误、任务失败、stream end、异常和 canceled 的处理分别验证。**不要借重构改变网关异常语义或把异常当成空成功。**
5. `record_assistant_turn()` 仍是记忆/消息写入和多通道扇出的权威入口；清洗、脱敏、渲染文本与原始文本分工不变。不得出现双落盘、双推送、串写 Dream/Reality、丢 `char_id`、因切角色中途错写、掉附件/引用等问题。
6. `admin/routers/chat.py` 中已禁用的 `/chat` 兼容路径与状态码保留，不把不可达代码当成新功能复活；是否删除旧代码另列**删除 brief 候选**，本工单不擅自删。
7. 日志按 `admission` / `prepare` / `generate` / `stream` / `commit` / `deliver` 阶段标记原有请求 ID 和脱敏状态；不重复记账、不过量增加日志或敏感数据。

**阶段 B 验收**：desktop/mobile/owner-turn 及需覆盖的 QQ/主动链调用相同 service（至少断言原有共享入口行为）；流式与非流式一致；身份/锁/消息落盘/工具循环/异常行为回归通过。阶段完成独立 commit。

## 六、阶段 C（P1）｜Dream API 按功能域分路由，业务实现回归 core/dream

将单一 `admin/routers/dream.py` 按明确的用户可理解功能划分，不按照平均行数切：

```text
admin/routers/dream.py                # 总 router（include_router）及兼容导出
admin/routers/dream_sessions.py       # enter / chat / exit / wake / resume
admin/routers/dream_archives.py       # 档案查询与详情
admin/routers/dream_worlds.py         # 世界列表、创建、重命名、删除、lorebook
admin/routers/dream_scenarios.py      # 剧本检验、增删改查
admin/routers/dream_presets.py        # 独立预设与相关设置
admin/routers/dream_observability.py  # 状态/HUD/operations/stats（可据实际合并）
core/dream/                            # 现有业务与状态机的唯一权威
```

1. **先按路由功能归属迁移，再决定需要下沉的业务 helper**。保持 `/dream/*` 所有真实请求路径、HTTP method、auth scope、OpenAPI 接口、Body 默认值、错误码和注册顺序；不能引入重复路由或改变请求解析。
2. 路由层若直接承担世界文件操作、剧本 YAML 规范化或 HUD 派生等可独立测试的纯业务逻辑，才下沉到 `core/dream/` 合适现有模块或同域新模块；不要为了形式包装一次转发。
3. `dream_turn`、`force_exit_dream`、锁、Dream/Reality 隔离、RPG 端点约束、硬退出不可拒绝、挽留状态、场景协调和出梦延续仍以现有核心实现为准。
4. 世界与预设文件读取必须保留 `core.sandbox.get_paths()` 和 layered authored asset 回退规则。写入仍落到正确的 `userdata` 位置；不得把用户素材写回源码目录，也不得自动搬迁素材。
5. 对测试与其他模块引用的旧 helper 设置最小兼容层；禁止复制一套新的状态机。

**阶段 C 验收**：路由清单/权限/错误响应对比一致；进入、聊天、硬退、软挽留、恢复、档案、剧本 YAML 校验和所有文件 CRUD 用临时沙盒验证；Reality 记忆不被 Dream 流程污染。阶段完成独立 commit。

## 七、阶段 D（P1）｜Admin 前端按功能域整理与 i18n 解耦（不重写框架）

当前管理面是原生 JS、多页面 JS 文件、共享 handler 的架构。本阶段目标是**减少全局隐性依赖，提高页面级定位能力**，保持页面视觉设计与交互。

1. 先输出简明的 `页面 → 页面 JS → 共享 API/state → DOM 容器 → CSS` 映射表；标记互相依赖的全局方法、全局变量、初始化事件顺序、旧版/新版 UI adapter 与公共组件。
2. 优先复用现有 `admin/static/js/core.js`、`admin-page-layout.js`、`ui-version.js`、各功能脚本及 `admin-components-v2.css`。共享 API/鉴权/Toast/页面状态有唯一权威，不再复制一套业务加载函数。
3. 各功能页的 DOM 查找、表单绑定、渲染、请求与可见错误尽量留在同一功能模块；共用部分抽到很少的公共组件/工具，不新增散乱的 `utils1.js`、`helpers2.js`。不强制迁移到 ES Module、打包器或 UI 框架；确需改变装载机制须先证明兼容所有页面、缓存与测试。
4. `i18n.js` 将**字典数据**与**翻译执行/Legacy 兼容适配**解耦。可把中文/英文按语言或按领域拆字典，将现有 `t()`、`AdminI18n`、动态 DOM 翻译等逻辑放在明确模块。保留现有 key、fallback、初始化时机、语言切换、双语、动态插入节点翻译、旧页面、模态框与原生确认框的行为。不得漏掉翻译项。留意同步脚本加载先后，避免出现 `t is not defined`。
5. 保留新版和旧版 UI 切换、同一 DOM 节点身份、表单草稿、播放器状态、页内导航、折叠、缓存版本、全局 data-action 事件委托。禁止“为了模块化”复制一份新版业务 DOM 或丢旧版兼容。
6. CSS 按现有 tokens/组件维护，**不得大面积改视觉、不新建第二套 Design System、不得因脚本拆分改变颜色或对齐**。确需移动 CSS 时先证明选择器、层叠优先级、移动端宽度与状态色一致。
7. 静态资源如有增删/引用变更，必须检查每个引用的版本参数/缓存规则。检查浏览器 Network 不存在脚本 404、重复初始化、重复发请求或旧缓存混合加载。

**阶段 D 验收**：至少用 `tests/admin_ui_versions_browser.cjs`、`tests/admin_ui_business_browser.cjs`、`tests/admin_static_assets.py` 等现有入口覆盖**实际可点击行为**；新/旧版、宽/窄屏、语言切换、动态翻译、设置保存、权限错误展示、导航与表单状态保持一致。阶段完成独立 commit。

## 八、测试与回归矩阵（每阶段执行相关项，最终执行跨阶段集成）

必须根据当前代码及 `docs/testing-and-merge.md` 确认测试入口，不得因为路径不同就删除测试或把 skip 当 pass。推荐从下列仓库已存在的入口选择：

```bash
# 工具 / Agent / schema
pytest -q tests/test_execute_structured_production.py tests/test_pretool_router.py tests/test_tool_schema_portable.py tests/test_tool_grounding.py
pytest -q tests/test_computer_use.py tests/test_agent_runtime_workspace.py

# 前端 / 路由行为：以当前仓库规范、依赖和测试文件实际内容为准
pytest -q tests/admin_static_assets.py
node tests/admin_ui_versions_browser.cjs
node tests/admin_ui_business_browser.cjs

# 后端核心 Smoke
pytest -m smoke -q

# 跨阶段最终回归：如现有规范要求全量或改动影响面涉及核心入口，执行并记录
pytest -n auto -q
```

补充验收项目（先复用旧测试与 fixtures，缺少独立覆盖时再加）：

| 维度 | 必须证明什么 |
| --- | --- |
| 工具注册 | 名称/Schema/分类、Path A 与 Path C 暴露、role/origin/bypass、MCP 和 builtin 兼容不变 |
| 工具执行 | 成功、缺参、非法参数、权限拒绝、上游失败、结果未知、取消、重复执行保护的状态一致；无危险权限绕过 |
| 聊天 | desktop/mobile/owner-turn API；同步/流式/Agent；结果落盘、stream ID、最终替换、附件与引用；失败不能伪装成功 |
| 数据边界 | uid+char_id 冻结，Reality 与 Dream 隔离、线程/锁/取消、短期/长期记忆无额外写入 |
| Dream | 进梦/聊天/硬退/挽留/恢复；世界/剧本/预设/档案；auth、409/422、文件路径和错误语义一致 |
| Admin | 两版 UI、常用设置保存、脚本依赖、双语言、动态 i18n、弹窗、移动布局、缓存、控制台异常 |
| 性能 | 关键对话首字/总体耗时、Admin 初始化与重复请求次数不显著退化；不随意宣称“性能提升” |

测试使用临时目录/模拟外部服务，严禁使用生产真实记忆、账户或密钥。保持既有日志脱敏和安全准入。若 Windows 环境与测试依赖不满足，按 `docs/dev-environment.md` 分类记录，不得自行修改业务逻辑绕过环境问题。

## 九、实现规矩与防止“重构反而更乱”

- **一次只搬一个清晰职责**：先引入新模块及等价测试，改入口为委托，再删除旧实现；每阶段先测试再 commit，禁止一次把四个大文件全重写。
- 禁止为了降低行数创造几十个一次性模块。拆分依据：功能归属、独立输入输出、独立测试、未来变更影响半径，而不是“每文件不超过 200 行”。
- 禁止保留两套可维护逻辑；兼容层只能 thin delegation 或同对象 re-export。对需要历史兼容的调用，注明调用者、计划移除条件；不要顺手删兼容功能。
- 对中央 `elif` 特例，如确因安全/上下文而必须保留，说明原因并写归属测试；不要为了统一 API 消灭实际的业务授权差异。
- 不新增面向用户的设置或三仓协议；除非事实证据证明绝对必要，否则本轮只改后端 Admin 仓。
- 无需引入 DI 框架、事件总线、全新状态管理库或大规模前端框架迁移。若现有模块可承担责任，就不要再造抽象层。
- 源码与文档避免绝对本地路径、真实 token、账号、个人消息正文。检查 LF/CRLF 规范，不允许因换行制造海量伪 diff。
- 每阶段之后检查 `git diff --check`、`git status`、测试及 import；必要时先回滚**本阶段提交**，不要让未通过的中间态覆盖可工作的上一阶段。

## 十、最终交付（不可省略）

1. 完成 A/B/C/D 各阶段的**独立 commit**（如果某阶段已有等价结构，给出证据并做最小必要修复，而不是硬拆）。无需等用户逐阶段确认，按本工单连续执行；出现无法安全推进的真实障碍，保留已验证成果，说明阻断原因。
2. 更新 `docs/refactors/modularization-ar01.md`：前/后目录与依赖图、各模块责任边界、旧入口与兼容 shim、仍保留的技术债与拟议**删除 brief 候选**（只提建议，不执行本轮未授权的旧功能删除）。
3. 按 `AGENTS.md` 更新受影响的权威文档（仅修改需要的段落，例如 `ARCHITECTURE.md`、`docs/tools.md`、Admin 文档）；不要全文重排。
4. 输出相同基线的前/后对比证据：工具 schema、路由/OpenAPI、静态脚本装载、UI 交互、关键消息流与权限拒绝。解释全部非零差异，不得仅报告“测试绿了”。
5. 每阶段报告提交 SHA、修改文件、执行命令、passed/failed/skipped 分类、未执行项目及理由；最终检查工作区是否干净，并说明与执行前用户本地改动的关系。
6. 不自动部署 VPS、不修改生产 `data/`/`userdata/`；本轮不做线上发布或远端状态迁移。涉及 push/PR/merge 时遵守当前授权及仓库交付规则，记录最终被验证的 SHA。绝不把未验证设备/真实服务写成已通过。

### 最终完成定义（DoD）

- 工具新增/维护点归业务域，中央执行器不再持续膨胀；权限、Schema、结果与审计合同不退化。
- 聊天入口从路由的巨大混合流程变成可按步骤独立测试的清晰编排，消息和工具行为与此前相同。
- Dream 路由能按用户功能快速定位，核心状态与资产路径只有一个权威实现。
- Admin 能按页面/模块快速定位 JS 与 CSS，字典与翻译运行时分离，新旧 UI 均可用。
- 本轮工作只是**组织结构重构、可维护性与故障可诊断性改进**；没有意外行为变化、数据迁移或用户端新配置。

**执行人最终汇报格式**：阶段表（commit / 主要迁移 / 测试证据 / 剩余风险）+ 前后责任图 + 总体验收 + 未完成项。不要只说“重构已完成”。
