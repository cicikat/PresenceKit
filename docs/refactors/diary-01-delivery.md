# DIARY-01 每日角色日记修补

日期：2026-11-25。后端内部维护任务变更；桌面、手机协议与设置不变。

## 回归与修补

修补前新增用例实际出现 12 项业务失败：显示名/文件名直接进入路径导致异常，
无效白名单未阻断错误角色写入，路径、context、Task、Work Session 创建失败及
无素材被错误标记为完成。另一个主循环用例复现 speech proposer 抛错后日记不落盘。
修补使用正式资产精确映射、逐角色异常隔离和现有 scheduler/Task Manager 退避，
不修改两段生成 Prompt、输出格式、O_EXCL、fsync 或副链所有权。

## 23:30 完整执行证据

`tests/test_inner_diary_write.py::test_configured_reference_writes_canonical_diary`
将时钟固定到 2026-05-25 23:30；公共测试卡包含工单给定的中文显示名，分别用
显示名、ASCII ID、已注册文件名通过真实白名单入口执行。仅替换模型网络边界，
实际执行 Task 创建、lease 获取、Work Session、两段生成及排他写入。
断言最终文件相对隔离数据根为
`runtime/characters/fixture_character/inner/diary/2026-05-25.md`，包含事件与感受，
Task 与 Work Session 均为 succeeded，完成冷却恰好记录一次。
这是模拟模型的完整任务证据，不是生产日记或真实模型验收。

覆盖还包括中文重名、未知引用、目录越界、单角色模型失败、准备/终态故障、
无素材退避后恢复、23:00/01:00/05:00 边界、主动日记发言独立性，以及主循环
在 proposer 失败后仍通过真实 worker 写出文件。既有补写用例验证同一路径的
锁、独占创建、重试与角色隔离。

## 环境与范围

Windows，Python 3.12.13；全部测试使用公共资产和隔离目录。实际本机配置仅
只读检查日记白名单及资产映射，逐项解析成功；具体映射在本次会话报告，
不把私有部署身份写入版本库。未改写配置，未运行生产数据测试或部署。
T4 与 T3.2 不属于本提交。

验证：`python -m pytest tests/test_inner_diary_write.py tests/test_diary_backfill.py
tests/test_ar02_dependency_directions.py tests/test_no_hardcoded_character.py
tests/test_line_endings.py -q`：256 passed（122.72s）。`git diff --check` 通过；
本次文件的普通 diff 与忽略 CR 的 diff 统计一致。
