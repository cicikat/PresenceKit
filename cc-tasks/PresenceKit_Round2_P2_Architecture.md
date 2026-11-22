# PresenceKit 第二轮｜P2 架构治理与技术债收敛

必须在第一轮联合验收完成后启动 · 不改变功能语义的渐进式重构

> **启动闸门** 第一轮 R1–R5、F1–F3 形成稳定可验证基线；关键不变量、三仓协议、恢复与回滚有证据。若仍有 P0/P1 故障、工具副作用重复风险或记忆对账失败，本轮禁止动大范围架构。

> **2026-11-22 进度** 已 [整理 P2-0 启动证据](../docs/refactors/round2-p2-baseline.md)。TTS 按安排跳过。首发额度接管已合并上线并通过固定 SHA 合同及 VPS 隔离验证。另有上游 400 的请求或中转适配原因待确认。P2-1 至 P2-4 尚未进入代码施工。

> **P2-1** 已固定 owner chat 和 Prompt 纯层的禁止依赖，并用现有 Reality/Dream、桌面收件及三仓 fixture 补验 Prompt 预算下的来源、引文及顺序。109 项通过。离线基准从仓库内 `tests/round2_p2_benchmark.py` 运行。

# 01 目标和排除范围

目标不是把目录“看起来整理干净”，而是降低跨模块隐式依赖、缩短错误定位路径、将副作用集中在明确的边界。单用户、单进程的后端形态继续保留，不为模块拆分引入微服务或分布式队列。

- 保持 Backend / Desktop / Mobile 三仓角色所有权、公共 API、EventContext / turn_id / request_id、现有 storage schema 与用户数据路径不变。

- 优先拆职责与依赖方向、梳理输入/输出契约，再搬文件；禁止一次把 core 189 个条目“全挪目录”或改全局 import。

- 无用户授权时不迁移 data、userdata、敏感配置，不删除 legacy 兼容路径；废弃项目要先证明无读写者及可回滚。

- 保持 AGENTS.md 为工程规则唯一入口；CLAUDE.md/CODEX.md 只保留代理入口。

# 02 P2 工作包

## P2-A｜Pipeline 编排与副作用边界

现状：core/pipeline.py 约 2,874 行。已经有 core/owner_chat、turn_sink、tool_runtime 等分域，禁止误判为完全未拆。

- 建立真实调用图：入口→会话锁→上下文获取→prompt→LLM/工具→critical persistence→fanout→slow postprocess。标注 await/锁/事务/副作用边界与可重入入口。

- 将纯编排、context fetch、generation/stream orchestration、tool-loop control、critical/slow adapter 按清晰接口拆入相应现有包；pipeline 留兼容 facade，分批替换调用。

- 依赖方向固定：domain 不 import HTTP router / dispatcher 实现；adapter 可依赖 domain；写入仅经授权 sink/ledger；避免多处重复修剪和隐性 global state。

- 逐段迁移，每批保证行为 diff 为零：prompt 快照、tool receipt、发言结果、错误码、消耗指标、Dream 入口和跨角色 scope 对照。

## P2-B｜Prompt Builder 与来源门控收敛

现状：core/prompt_builder.py 约 108 KB，混有多层 prompt、token 裁剪、来源和角色门控。

- 制作稳定层清单（层名、优先级、来源、是否可裁剪、强制隔离、token 预算），与 docs/prompt-layers.md 对齐。

- 分出“数据检索证据”“可测试纯层渲染”“token 预算/裁剪”“最终 prompt 编排”，保留精确层顺序与已冻结 ID。

- 为 Reality/Dream、web 结果、引用历史、tool pins、低质量模型格式差异建立金样本，不因重构改变人设或抹掉用户事实。

## P2-C｜Memory 分层与对账机制去耦

- 梳理 short/mid/episodic/event/user_identity/vector/hidden_state/storyline 的“事实记录、衍生投影、暂存索引”身份；主账本不可被下游摘要倒写成真相。

- 建立唯一共享的 scope/provenance/invalidation 契约；各层通过稳定服务接口读取，停止相互直接打开别人的存储文件。

- 依赖第一轮的补偿、幂等、对账结果，再考虑替换 legacy 路径；先建立一致性检查和只读 dry-run，不进行无证明的全量迁移。

- 减少热路径重复召回与重复 token 注入；所有精简需用检索质量样本与 P95 性能证据支持，不按“层数多”直接删记忆。

## P2-D｜Autonomy / Scheduler 状态模型简化

- 统一 signal / opportunity / evaluation / talk attempt 的状态词、时间戳、因果 ID 与 outcome；避免 scheduler 和 autonomy 分别实现相同冷却及去重概念。

- 从第一轮可观测到的 reason_code 出发，统计真实长期无声、频繁拒绝、重复候选；再消除冗余门控，不能为提高发言量直接取消 DND/Dream/用户活跃限制。

- 提供确定性时钟模拟与状态转移测试，包括手机 ambient、新旧触发合流、预算、无接收端、夜间与重启。

## P2-E｜Config / 版本 / 模块目录 / 依赖治理

- 统一 runtime config 的 schema/版本/默认值/跨字段依赖、热更新错误及回滚；所有后台配置通过既有原子写入路径。

- 校对 README 产品版本、pyproject package 版本、Desktop/Mobile 发布版本与 wire v0.1 的独立语义；不要为“统一数字”随意改协议版本。

- 按实际依赖图而非文件名字移动根目录临时测试脚本、AA\*.bat、兼容工具；保留兼容入口并提供退役窗口。

- 核查 requirements-core/full/stt/test/lock 的依赖集合与 Python 3.10/3.12 CI；只有冲突证据时才改锁文件。

- 许可证保持 PolyForm Noncommercial 的正式文本与 metadata 对齐，检查 GitHub 识别显示；不要未经授权改许可证。

# 03 必须输出的架构文档与分析

| **产出物**         | **内容及验收标准**                                                       |
|--------------------|--------------------------------------------------------------------------|
| A. 当前系统调用图  | 实际 import 与函数调用、主要锁、任务队列、I/O 责任、公共入口；列证据路径 |
| B. 目标模块/依赖图 | 包间依赖规则、可迁移文件清单与禁止方向；每步可回滚                       |
| C. 数据真相源矩阵  | 每类数据唯一主记录与 derived projections、TTL、事务/补偿、隔离/删除策略  |
| D. 风险与测试矩阵  | 每批重构影响端点、最小防回归集、性能预算、故障注入场景                   |
| E. 迁移/回滚清单   | 文件移动、兼容 facade、代码引用更改、版本窗口、禁止影响范围              |

# 04 施工顺序（不要合成一次大改）

1.  P2-0：先输出当前依赖图、现状报告、耦合热点的证据与基准（禁止改代码）。

2.  P2-1：先补结构性 lint / import 方向 / 关键端点金样本；建立可重复验收门槛。

3.  P2-2：从最易纯化的 Prompt 层与 Pipeline 纯函数开始拆；一批一个边界。

4.  P2-3：逐步收敛 Memory、Autonomy 与配置接口；只减少重复状态，不换数据真相源。

5.  P2-4：最后做目录、文档、脚本、依赖与许可证 metadata 清理；验证跨仓全部有效。

# 05 不变量与停止条件

- 功能不可退化：模型路由/原生 Gemini、Markdown、手机 ambient 候选、Agent 工具、Dream 隔离、推送、聊天渲染与历史全保持有效。

- 性能不可无证据下降：对关键链路记录 p50/p95、请求超时、context token、Memory 查询成本；出现不可接受退化先回滚当批。

- 任一批出现新的错位记忆、漏发/双发、tool 重执行、用户授权逃逸、跨角色泄漏或不可恢复数据失败，立即停止进入下一批。

- 测试与提交：无需新分支；只提交该批改动，关联证据与 commit，先本地验收再按仓库流程部署。

- 未运行真实设备 / 实际模型 / 生产联调的子项，保持 NOT VERIFIED。

| **交付标准** 不是“文件拆完”即完成；必须证明删除了哪些环状/反向依赖、减少了哪些重复状态、哪几个故障的定位路径变短，并保持稳定与兼容。 |
|--------------------------------------------------------------------------------------------------------------------------------------|

# 06 对应代码与文档入口

- core/pipeline.py、core/prompt_builder.py、core/turn_sink.py、core/tool_runtime/、core/owner_chat/

- core/memory/、core/autonomy/、core/scheduler/、core/dream/、core/post_process/

- admin/config_control.py、core/config_loader.py、core/data_paths.py、core/session_scope.py

- docs/prompt-layers.md、docs/memory.md、docs/agent-runtime-architecture.md、docs/three-repo-interface-catalog.md、docs/testing-and-merge.md

- .github/protocol-matrix.json、AGENTS.md、CLAUDE.md、CODEX.md
