# Brief 234：受限临时程序 Runner

> 状态：implemented；前置：230、233；高风险工单，必须在 workspace capability 和 Task Manager 稳定后施工。

## 目标

允许角色生成并运行短时临时程序，服务于明确任务，例如整理 workspace 文件；不提供任意 shell 或
无限期后台进程。

## 执行合同

- 程序只能来自 task workspace，不能引用任意宿主路径。
- 解释器/命令使用 allowlist；参数结构化，不接受 shell 字符串拼接。
- CPU、内存、输出大小、墙钟、子进程数量和磁盘空间均有限制。
- 网络默认关闭；需要网络时使用独立 capability 和显式策略。
- stdout/stderr 有界保存，超限截断并标记结果。
- 任务必须可取消；进程树必须可回收。
- 进程结束后清理临时目录或保留为明确 artifact。

## 禁止

不把 coplay 中的业务 `subprocess` 用法暴露为通用 Agent 工具；不复用 `shell=True` 作为新能力实现；
不允许读 token、凭据、项目内部 data 或浏览器 profile。

## 验收

- 成功、超时、取消、崩溃、资源超限和 outcome_unknown 均有稳定 task 状态。
- 进程无法越过 workspace 根或访问 Dream/Reality 私有状态。
- 运行结果只进入当前工作任务或用户明确指定的 artifact，不自动进入 memory/event ledger。
- remote_server 模式下拒绝本地进程能力。
