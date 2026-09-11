# Brief 247 — Prompt 来源、视角与链路审计

状态：完成。后续依赖：248 → 249；按顺序执行，每单验收后独立提交。

## 目标与边界

盘点框架自有 prompt、角色 authored 资产、引用材料与调用任务的边界；核实幽灵代码、失效配置、文档漂移和疑似断链。没有调用证据不认定为死代码。仅审计 prompt 相关路径，不扩展为全仓清理。

## 检查表

- [x] 盘点 Reality builder、调用前附加层、Dream/Stage、分类与记忆辅助任务。
- [x] 制定主体、人称、来源、角色资产保真规则。
- [x] 对疑似幽灵代码和断链逐项记录定义、调用者、消费者与结论。
- [x] 核对管理面观测/开关、桌面和手机消费边界。
- [x] 将未完成项同步 known-issues 与 three-repo-interface-catalog，标注 open/roadmap/observe。
- [x] 差异检查，完成审计文档并独立提交。

交付：`docs/prompt-unification-audit.md`，含证据表、迁移范围、保留项和验收边界。
