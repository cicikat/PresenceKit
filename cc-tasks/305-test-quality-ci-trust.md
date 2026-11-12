# 测试质量治理与 CI 可信度修复

对应工单：306.md（工单正文编号 305）。

## 已实现

- 离线评估流水线启用 pipefail，保留失败日志；离线套件数量不足返回失败，memeval 按模式判定严格 XPASS，在线评估明确标注 report_only。
- push Smoke、PR Python 3.10/3.12 全量、串行状态隔离和独立浏览器任务；JUnit 检查空套件、失败及未分类 skip。
- 三仓合同支持显式 SHA 的可复用调用，记录实际 checkout SHA 和步骤结果；未实测的矩阵保持 not_verified。
- 删除指定重复测试，将向量 fail-open、睡眠 Prompt、管理面导航和翻译的部分源码断言替换为行为验证；路由清单来自真实 FastAPI 注册。
- Gmail 三个 restart 场景启动独立 Python 子进程，使用隔离配置、清理凭据环境并检查秘密不出现在输出中。
- 共享不可变测试素材，保留每个测试的可写沙盒；减少同步测试的事件循环初始化。
- 五种局部内存 mutation 覆盖秘密泄漏、角色越权、重复副作用、禁用通道发送及工具协议放行。

## 续修与验收

- 并行 Smoke 通过 241 项。移除慢队列 fixture 对同步测试隐式创建的 event loop 后，旧测试改为自管事件循环或在 pytest 管理的同一循环内执行；相关串行回归通过 256 项。
- 两条 Playwright 验收覆盖新版登录及邮件设置、旧版导航和移动视口。测试实际展开 OAuth 卡片、点击保存、核对 HTTP 请求与重新读取的状态，并执行真实双语翻译。保存按钮补齐已有翻译键，浏览器 CI 固定到 Playwright 支持的 Ubuntu 24.04。
- Windows Python 3.12 离线 identity、format、memeval 分别通过 8、4、50 项。关闭食物记忆的 SQLite 连接后，临时沙盒可正常清理；评估异常计入失败，无法被 XFAIL 吞掉。
- 五种故障注入全部被对应业务测试发现。局部 mutation 在独立进程注入，不修改磁盘上的业务源码。
- [PR #2](https://github.com/cicikat/PresenceKit/pull/2) 持续运行双版本 Smoke、全量、串行隔离、浏览器和离线评估。以 PR 当前提交的运行记录为准。
- [三仓协议验收](https://github.com/cicikat/PresenceKit/actions/runs/43172604200) 对 backend `125c7bb`、desktop `a2704db`、mobile `0f2a7ee` 的 provider/consumer fixture 通过。扩充后的合同还检查跨仓活动接口，并在每次运行生成针对所验三仓提交的证据。

本次变更在后端仓库交付。测试使用公开资产、隔离数据和合成 OAuth 凭据。
