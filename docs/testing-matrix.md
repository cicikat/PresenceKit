# 后端测试矩阵

> 本文是后端测试入口和验收边界的统一说明。它不替代代码、测试文件或 CI；当命令、测试范围与本文冲突时，以当前代码和 `.github/workflows/tests.yml` 为准。
>
> 盘点日期：2026-08-10。本文记录的是测试结构与证据边界，不代表本轮已经重新跑完所有测试。

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
| 跨模块契约 | 相关 `test_*.py` | HTTP/WS、鉴权、写入闸门、消息收口、跨层字段契约 | 目前主要是后端内的契约；三仓 head 兼容仍需单独矩阵 |
| Prompt / tag 评测 | `python tests/run_eval.py` | tag 激活、prompt layer 过滤和消融边界 | 规则评测，不等于真实 LLM 输出质量 |
| 身份连续性评测 | `python tests/run_identity_eval.py` | identity cold start、连续性、演化与拒绝场景 | 离线评测，不等于长期真实用户观察 |
| 格式评测 | `python tests/run_format_eval.py` | 多段落、短文本豁免、换行和输出格式 | 不覆盖通道展示和真机渲染 |
| 记忆召回评测 | `python tests/run_memeval.py` | episodic、event_log、mid-term 的召回与拒召 | 不覆盖真实生产数据迁移 |
| Coplay 评测 | `python tests/run_coplay_eval.py` | Coplay watcher、commentator、afterglow 等场景 | 会调用当前配置的 LLM；需显式配置和单独记录成本/结果 |
| 传感器手工冒烟 | `tests/manual/smoke_sensor_*.py` | sensor event、aware、judge 等本地边界 | 不是 Android / ESP32 / 真实传感器验收 |
| 发布 / 跨仓验收 | [`v1-release-readiness.md`](v1-release-readiness.md) | backend、desktop、mobile、协议 fixture、升级恢复 | 需要冻结三仓版本并保留构建、安装、真机或恢复证据 |

## 三、CI 实际运行范围

`.github/workflows/tests.yml` 当前只运行固定的 v0.1 后端 smoke subset，并在 Python 3.10 / 3.12 矩阵上执行。它会复制 `config.example.yaml`，验证公开 fresh-clone 配置下的鉴权、安全写入、QQ 收口、prompt layer、现实 scrub、trait / author-note 等回归。

CI **不会自动证明**以下内容：

- 全量 `tests/` 已通过；
- identity、format、memeval、coplay 等独立评测已通过；
- `tests/manual/` 的真实传感器冒烟已通过；
- desktop / mobile 的构建、协议兼容、真机安装升级已通过；
- 真正的 LLM、QQ、WebSocket 对端、ESP32 或 Android 设备行为已通过。

因此，“CI 通过”只能写成“后端 smoke subset 通过”，不能写成“三仓测试通过”或“发布验收完成”。

## 四、提交前建议顺序

1. 先运行当前改动直接相关的测试：`pytest -n auto tests/<相关文件>.py -q`。
2. 改动 tag 规则或 prompt layer 时，补跑 `python tests/run_eval.py`。
3. 改动 identity、记忆召回、格式或 Coplay 时，补跑对应离线评测。
4. 涉及通道、协议、启动、调度器或状态持久化时，补跑相关契约测试，并检查 [`v1-release-readiness.md`](v1-release-readiness.md) 的跨仓验收项。
5. 需要全量回归时运行 `pytest -n auto -q`，记录通过数与既有失败；不要用未注明范围的“测试通过”。

## 五、当前已知测试缺口

- CI 没有全量 pytest job，也没有独立的评测 job；评测脚本失败不会阻塞当前 CI。
- 三仓没有统一、版本化的 protocol fixture；mobile `/mobile/chat`、poll/ack、desktop HTTP/WS correlation 需要在固定三仓 commit 上做兼容矩阵。
- scheduler / relay / mobile queue、Dream 期间 reality park、sensor 到真实发送的完整链路仍需要跨进程或真机证据，单元测试不能替代。
- `pytest.ini` 注册了 `audit`、`contract`、`smoke` marker，但当前测试文件没有实际使用这些 marker；如果要按层级筛选，需先补标记或删除无效配置。
- [`test_record.md`](test_record.md) 只是历史手工记录模板，不是可复现测试规范；新的手工验收应记录日期、版本、环境、命令、输入、结果和证据位置。
- 工单 257 R8 复用测试与缺口见 [`runtime-recovery-257.md`](runtime-recovery-257.md)。该记录不是全量套件或真机 soak；桌面消费者、SHA matrix、真机重连/后台/撤权仍 open。
