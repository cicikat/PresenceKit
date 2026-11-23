# 后端测试矩阵

> 本文是后端测试入口和验收边界的统一说明。它不替代代码、测试文件或 CI；当命令、测试范围与本文冲突时，以当前代码和 `.github/workflows/tests.yml` 为准。
>
> 盘点日期：2026-11-22。本文记录的是测试结构与证据边界，不代表本轮已经重新跑完所有测试。

## 一、运行前提

- 项目支持 Python 3.10–3.12；CI 覆盖 3.10 和 3.12。Codex / Claude Code 的 Windows 沙箱问题见 [`dev-environment.md`](dev-environment.md)。
- 测试数据必须使用临时目录或测试沙箱，不得污染生产 `data/`。
- 本地运行相关测试优先使用并行 pytest：

```powershell
pytest -n auto tests/<相关测试文件>.py -q
pytest --testmon
```

- 只有在需要完整回归时才运行全量套件；全量失败必须记录失败项、环境和是否与当前改动相关：

```powershell
pytest -n auto -q
```

## 二、测试层级

| 层级 | 入口 | 主要覆盖 | 证据边界 |
|---|---|---|---|
| 单元 / 模块回归 | `tests/test_*.py` | memory、pipeline、prompt、scheduler、auth、channels、tools 等模块 | 不能证明真实网络、真实设备或发布包行为 |
| 架构边界 | `tests/test_ar02_dependency_directions.py`；独立 `architecture` CI job | AR-02/T1 零反向依赖，已消除 T1 债务不可恢复；T2 活动时钟和跨业务身份/配置读取零反向依赖；已消除的全部 26 条 Scheduler 债务不可恢复及故障注入 | AST 静态合同；业务验证见 [T1 报告](refactors/migration-cutover-t1.md)，T2 验证见 [T2 报告](refactors/migration-cutover-t2.md)，阶段清单见 [迁移清单](refactors/migration-cutover-inventory.md) |
| 跨模块契约 | 相关 `test_*.py` | HTTP/WS、鉴权、写入闸门、消息收口、跨层字段契约 | 目前主要是后端内的契约；三仓 head 兼容仍需单独矩阵 |
| Prompt / tag 评测 | `python tests/run_eval.py` | tag 激活、prompt layer 过滤和消融边界 | 规则评测，不等于真实 LLM 输出质量 |
| 身份连续性评测 | `python tests/run_identity_eval.py` | identity cold start、连续性、演化与拒绝场景 | 离线评测，不等于长期真实用户观察 |
| 格式评测 | `python tests/run_format_eval.py` | 多段落、短文本豁免、换行和输出格式 | 不覆盖通道展示和真机渲染 |
| 记忆召回评测 | `python tests/run_memeval.py` | episodic、event_log、mid-term 的召回与拒召 | 不覆盖真实生产数据迁移 |
| Coplay 评测 | `python tests/run_coplay_eval.py` | Coplay watcher、commentator、afterglow 等场景 | 会调用当前配置的 LLM；需显式配置和单独记录成本/结果 |
| 传感器手工冒烟 | `tests/manual/smoke_sensor_*.py` | sensor event、aware、judge 等本地边界 | 不是 Android / ESP32 / 真实传感器验收 |
| 发布 / 跨仓验收 | [`v1-release-readiness.md`](v1-release-readiness.md) | backend、desktop、mobile、协议 fixture、升级恢复 | 需要冻结三仓版本并保留构建、安装、真机或恢复证据 |

## 三、CI 实际运行范围

当前工作流按事件选择以下验证，不能把单个绿色 job 当作全部验收：

| 工作流 / job | 触发与范围 | 证据边界 |
|---|---|---|
| tests.yml / pytest | 非 main push、PR、schedule、手动；Python 3.10/3.12，pytest -n 4 -m smoke，清单见 tests/ci_suites.py；拒绝 skip | 仅核心 smoke |
| tests.yml / full-pytest | main push、PR、schedule、手动；Python 3.10/3.12 全量 pytest、skip 分类、串行状态隔离；3.12 另跑业务 mutation | SKIP_CROSS_REPO=1，不代表三仓或真实设备 |
| tests.yml / browser | main push、PR、schedule、手动；隔离 Admin Chromium 合同 | 不代表手机或真实生产配置 |
| offline-evals.yml / offline | main push、PR、schedule、手动；3.12 identity / format / memeval，失败退出，上传日志 | 离线门禁，非真实 LLM 质量验收 |
| offline-evals.yml / coplay-opt-in | 仅手动显式 run_coplay=true | 外部模型、单独成本边界，不是 PR 必跑 |
| protocol-compatibility.yml | workflow_dispatch / workflow_call，冻结三仓完整 SHA，提供者与消费者合同，上传 evidence | 仅对记录的 SHA 组合有效；普通 tests 不自动触发 |
| manual-sensor-evidence.yml | 手工传感器证据入口，具体条件见工作流 | 不能由单元测试替代真机证据 |

main 直接推送会运行双版本全量与 browser，smoke job 按条件跳过；PR 会运行三类 job。使用测试结果时应同时标明提交 SHA、工作流、事件、实际 job 结果和 skip 分类。QQ、真实 WebSocket 对端、ESP32、真实支付通知与 Android 生命周期仍需对应实测。

.github/protocol-matrix.json 与 tests/protocol_fixtures/v1 已存在；它们描述协议和固定组合证据，不能把未运行的三仓 SHA 组合标成通过。手机财务同步的原生回归见手机 ci.yml 的 finance-native；真实 loopback HTTP 合同仍需显式启用 financeContract，不能与支付真机验收混淆。

## 四、提交前建议顺序

1. 先运行当前改动直接相关的测试：`pytest -n auto tests/<相关文件>.py -q`。
2. 改动 tag 规则或 prompt layer 时，补跑 `python tests/run_eval.py`。
3. 改动 identity、记忆召回、格式或 Coplay 时，补跑对应离线评测。
4. 涉及通道、协议、启动、调度器或状态持久化时，补跑相关契约测试，并检查 [`v1-release-readiness.md`](v1-release-readiness.md) 的跨仓验收项。
5. 需要全量回归时运行 `pytest -n auto -q`，记录通过数与既有失败；不要用未注明范围的“测试通过”。

## 五、当前已知测试缺口

- 已有版本化 protocol fixture 与冻结 SHA 的跨仓工作流；尚未验证的 mobile `/mobile/chat`、poll/ack、desktop HTTP/WS correlation 组合仍需单独执行并记录。
- scheduler / relay / mobile queue、Dream 期间 reality park、sensor 到真实发送的完整链路仍需要跨进程或真机证据，单元测试不能替代。
- smoke marker 由 `tests/ci_suites.py::mark_suites` 在收集时添加，清单缺失会失败；不要按测试源码里有无装饰器判断覆盖。
- [`test_record.md`](test_record.md) 只是历史手工记录模板，不是可复现测试规范；新的手工验收应记录日期、版本、环境、命令、输入、结果和证据位置。
- 工单 257 R8 复用测试与缺口见 [`runtime-recovery-257.md`](runtime-recovery-257.md)。该记录不是全量套件或真机 soak；桌面消费者、SHA matrix、真机重连/后台/撤权仍 open。
