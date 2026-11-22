# T0.1：架构门禁防绕过加固报告

基线：`dac509e716f3d7839c2c63024686e8794c6453c7`。施工日期：2026-11-22。
T0.1 初始状态为等待独立审查；T0.1.1 已获用户明确授权，在 PR #34 修复 Push 基线漏洞、验收通过后直接合并。此授权不启动 T1，不以分支保护配置或额外审批作为合并前提。

## 修改范围及理由

| 文件 | 改动与目的 |
|---|---|
| `scripts/check_architecture_debt.py` | 纯标准库 + Git 的独立 CLI：读取目标提交的 JSON，以 source/rule/owner/target 为身份逐项比较，只允许删行和降低 count；拒绝重复身份、非法字段与非正整数 count |
| `tests/test_ar02_dependency_directions.py` | 保留原 AR-02 零容忍与扫描=JSON 检查；识别 sys.modules.get、builtins.__import__、简单模块赋值别名和跨 Router 星号导入；加入真实 Git 历史和 CI 同款子进程故障注入 |
| `.github/workflows/tests.yml` | 原 architecture job 获取完整 Git 历史，先执行收敛 CLI，再执行现有 AST 测试；没有新增重复流水线或忽略失败 |
| 本报告和 T0 清单中的入口链接 | 说明复现方式、CI 基线、证据边界与仓库所有者操作 |

未修改业务运行时代码、客户端、生产配置、用户数据或部署状态。
T0 的 71 行 JSON 原封不动，HTTP_HELPERS、原 AR-02 规则及目录范围没有扩大或放宽。
没有新增运行时 facade / adapter / bridge / wrapper、fallback、静默降级、双份状态。

## 基线选择和本地复现

CI 的 `python scripts/check_architecture_debt.py --ci` 读取 GitHub 事件：

| 事件 | 比较基线 |
|---|---|
| PR | fetch PR 目标分支当前 HEAD；不信任事件中可能过期的 base SHA，也不把 PR head 当作基线 |
| main push | Push Event 的 `before` SHA（整批推送前的 main），包括一次推送多个提交的情况；不使用 after 的父提交或已经前移的 origin/main |
| 非 main push | fetch 最新 origin/main；旧分支不能恢复 main 已消除的债务，须先同步 main |
| 手动 / 定时 | fetch 最新 origin/main |

Git 使用参数数组调用，不执行事件内容拼接的 shell 命令。基线无法读取、历史缺失、
JSON 无效或债务扩张时非零退出，不回到工作树 JSON 或其他基线。
main push 的 before 缺失、格式无效、全零或提交不可读取时失败关闭；没有 after^1 或其他替代基线。

本地使用项目支持的 Python 3.10–3.12，在仓根执行：

```text
git fetch origin main
python scripts/check_architecture_debt.py --baseline-ref origin/main
python -m pytest -q tests/test_ar02_dependency_directions.py tests/test_line_endings.py --tb=short
```

独立 architecture job 仅安装 pytest，并用
`python -m pytest --noconftest -c /dev/null -q tests/test_ar02_dependency_directions.py` 隔离项目运行配置。
Windows 可用 ignored 临时目录中的空 `[pytest]` 配置替代 `/dev/null`；不是跳过架构用例。
CLI 收敛检查和 AST/JSON 精确检查必须同时执行，单独任何一个均不构成完整门禁。

## 故障注入及实际结果

所有债务用例都创建临时真实 Git 仓库，调用同一 `scripts/check_architecture_debt.py` CLI；
通过收敛检查后，再通过 pytest 子进程调用真实的扫描=JSON 断言。没有模拟 Git 输出。
动态访问用例直接运行 CI 收集的扫描器测试。没有使用生产代码或私有数据作 fixture。

| 注入 | 验证结果 |
|---|---|
| 仅添加 JSON 债务 | CLI 拒绝扩张 |
| 新真实违规模块 + 同步 JSON 登记 | CLI 拒绝扩张，不能通过两边一起改逃过检查 |
| 同一身份增加 count 并同步增加源码 import | CLI 拒绝扩张 |
| 分别改 source、owner、target、rule | 四项均被 CLI 拒绝 |
| 只删 JSON，源码依赖仍在 | 收敛检查允许减少，但真实 AST/JSON 断言失败 |
| 迁移真实调用者后删账 | 两个真实入口均通过 |
| 源码和 count 一起减少 | 两个真实入口均通过 |
| 源码和 JSON 都不变 | 两个真实入口均通过 |
| PR / main push / Codex 分支 push 中把当前违规 head 冒充基线 | 三种 --ci 场景均读取正确 Git 基线并拒绝扩张；PR 用例刻意提供误导性的 base.sha |
| sys.modules.get / system.modules.get / from sys import modules，普通和注解赋值 | Core→Router 和 Router→Router 私有访问全部命中 |
| builtins.__import__ / bi.__import__ / from builtins import __import__ | Core→Router 和 Router 私有访问全部命中 |
| importlib + 简单模块别名 | 保持动态导入保护并识别别名私有访问 |
| 跨 Router 绝对/相对星号导入；Core 从 admin.routers 星号导入 | 全部拒绝 |
| 公开 Router 挂载、既有 HTTP helper、conversation_activity、Scheduler start | 现有正向测试继续通过 |

另外从 Git 读取 T0 基线扫描器，在隔离 namespace 中重现了 5 个遗漏：
sys.modules.get 的 Core/Router 两例、builtins.__import__ 的 Core/Router 两例、Router 星号导入。
同一输入由旧扫描器漏检、当前扫描器拒绝，证明针对实际缺口补测。

本地 Python 3.12.13 / Windows：仅安装 pytest 的新 venv 中，独立架构文件
**106 passed，0 failed / skipped（24.84s）**；CLI 对真实 origin/main 基线
**71 → 71 passed**。最终正常项目 fixture 与换行守卫组合：
`python -m pytest -q tests/test_ar02_dependency_directions.py tests/test_line_endings.py --tb=short`，
**107 passed，0 failed / skipped（32.07s）**；`git diff --check` 通过，逐文件正常/ignore-CR 差异一致，新增文本均 LF。
远端 architecture、Python 3.10/3.12 全量、Browser 和其他检查结果以审查 PR 当前 head 的
GitHub Actions 链接为准；不引用 T0 的旧 CI 作为本单证据。

## main 保护：owner_action_required

2026-11-22 只读查询：branches/main 返回 `protected=false`；rulesets 查询成功并返回空列表；
branch protection API 返回 404 `Branch not protected`。没有修改设置、Ruleset 或协作者权限。
这仅是当前凭据能观察到的状态，不能证明组织外部策略或管理员绕过权限如何配置。

仓库所有者需要在 GitHub Settings 中核验 main 的有效规则，建议：

- 禁止直接 push 到 main，要求通过 PR 合并。
- 将 `architecture` 设为 required status check。
- 要求合并前与目标分支保持最新。
- 是否允许管理员绕过规则，由所有者决定。

此项未由施工者代为完成，状态为 `owner_action_required`。
仓内检查不能对抗有权限同时删改 CI、检查器及测试的行为；可信审查和分支保护承担这一边界。

## 静态分析边界和未完成项

扫描器不执行任意业务代码，不是完整 Python 数据流分析器；不保证推断任意运行时字符串、
加密字符串、复杂反射、跨函数返回值、条件重绑定或动态生成的代码。
本次只跟踪已列明的导入/查找语法、字面量拼接和简单模块别名，不据此扩大 helper 豁免。
原 AR-02 窄域的动态加载禁令仍单独执行。

main 保护仍待所有者核验；T0.1.1 按用户授权验收通过即合并，不等待额外配置或人工审批，不开始 T1。
T0 的既有 71 行债务没有被删除或解决，只有门禁加固。

## T0.1.1：多提交 Push 基线修复

仅修改检查器的 main push 分支、现有架构测试及本文；PR/非 main push 选择逻辑、
债务身份、AST 扫描范围与 71 行 JSON 均不变，没有业务代码或仓库设置改动。

在真实临时 Git 仓库中，经 CI 同款 `--ci` CLI 验证：

- A 为原基线；B 新增真实 Core→Router 依赖并登记 JSON；C 仅改无关文件。
  `before=A, after=C` 必须拒绝并明确报告相对于 A 的债务扩张。
- 正常单提交 `before=A, after=B` 不增加债务时通过。
- before 全零、不可读取、格式错误、非字符串或缺失时均失败，不降级。

修复前新增用例实际出现 6 failed / 1 passed，失败来自真实 CLI 错误放行的断言，
不是测试环境或收集错误；修复后的结果及远端当前 head 的验收链接见 PR #34。
修复后本地 Python 3.12.13 / Windows：独立架构测试 113 passed；项目 fixture 下架构与换行守卫
组合 114 passed（37.05s），零失败/跳过；真实 origin/main 债务比较 71→71，diff/check 通过。
