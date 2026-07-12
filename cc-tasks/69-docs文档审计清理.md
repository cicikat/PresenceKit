# 69 — docs 文档审计清理（坏指针 / Brief 35 残留 / 一次性文档归档 / 补索引）

> 来源：2026-07-12 三仓库文档审计（Desktop 出单，CC 执行）。
> 本单可与 Emerald-client/cc-tasks/21、Emerald-mobile/cc-tasks/06 **并行**，无前置依赖。
> 单内 A/B/C/D 可并行；E 依赖 C 完成（归档后再建索引，避免索引马上过时）。

## A. 坏指针（P0）

- `AGENTS.md` 「工作惯例」节引用 `docs/critique-triage-20260708.md`，该文件不存在。
  处理：git log 查该文件去向；若已删，把引用改为一句话概括其精神；若误删，恢复。

## B. Brief 35 已删模块的残留表述（P1）

Brief 35 已整体移除 `character_growth` 模块 + `get_growth` 工具，但以下文档仍把它当现役：

- `ARCHITECTURE.md` 数据树（~L190）：`character_growth/` 三个文件带现役描述（"角色对用户的整体认知"等）。
  处理：标注 `←legacy，Brief 35 已移除代码，磁盘文件仅历史遗留`，或从树中移到"历史遗留"小节。
- `docs/cross_project_interaction_flow.md` L74：把 `character_growth` 列为现役记忆层并给出代码路径（该代码已删）。
- `docs/data-taxonomy.md` L57/L111：树含 `character_growth/`，正文当现役描述。
- `docs/security_model.md` L185：列出该目录，核对上下文是否需标 legacy。

验收：`grep -rn "character_growth" docs/ *.md` 的每一处命中，要么在讲"已移除/遗留"，要么已删。

## C. 一次性审计/交接文档处置（P1）

以下均为带日期的快照/交接文档，先确认其待办是否已落地，再归档到 `docs/archive/`（新建目录）或删除。逐个确认，不要盲删：

| 文件 | 状态线索 | 建议 |
|---|---|---|
| `handoff-memory-confab-fixation-20260621.md` | 写着"代码侧补丁待CC执行" | 确认补丁是否已合入（查 fixation_pipeline 相关 commit），已合入则归档 |
| `memory-recall-audit.md` | 2026-06-19 交接文档 | 同上，确认后归档 |
| `proactive-trigger-audit.md` | 诊断+止血参数 | 止血参数若已落地则归档，架构讨论结论若有另开文档 |
| `interaction_issues_dedup.md` | 2026-05-19 审计，内容含已删模块，与 known-issues.md 职责重叠 | 未修条目并入 known-issues.md 后归档 |
| `cross_project_interaction_flow.md` | 2026-05-19 审计，同上 | 归档（内容已大面积过时，不值得校准） |
| `opensource-v0.1-checklist.md` | 执行清单，README/LICENSE 已就绪 | 若 v0.1 已发布则归档；遗留的测试隔离问题移入 known-issues.md |
| `配置改进候选.md` | Cowork 会话元流程文档 | 非本项目运行时文档，移 docs/archive/ 或仓库外 |
| `test_record.md` | 空白手测模板 | 移到 tests/ 旁或保留但在索引里标"模板" |
| `vbox-hyperv-vtx-troubleshooting.md` | 一次性环境排错记录 | 归档，或并入 dev-environment.md 附录一句话+链接 |

验收：docs/ 根目录剩下的都是"活文档"（描述当前系统的），快照类都在 archive/ 且 AGENTS.md 无断链。

## D. security.md / security_model.md 命名易混（P2）

两文件职责其实不同（前者=鉴权实现 SEC-AUTH-2，后者=整体风险边界），但文件名几乎相同。
处理：两文件开头各加一行"本文≠另一文，另一文见 →"交叉引用；可选把 `security.md` 改名 `security-auth.md`（改名需同步 AGENTS.md 表格与全仓引用）。

## E. 补缺失文档（P2，依赖 C）

1. **`docs/README.md` 索引**：docs/ 近 40 个文件，AGENTS.md 表只覆盖一半。建一个按主题分组的索引（活文档/归档分开），参考 Emerald-mobile/docs/README.md 的格式。
2. **`docs/api-reference.md` 端点单一真值**：目前后端 HTTP/WS 端点清单散在 Emerald-client/AGENTS.md「后端连接信息」与 Emerald-mobile/docs/backend/integration.md，三处各自维护必然漂移。在后端仓建一份权威端点表（路径/方法/scope/消费方），两个客户端仓改为链接指向此文件。
3. **config.yaml 配置项参考**（可选，工作量大可另开单）：目前无任何文档系统性列出 config.yaml 可用键。
