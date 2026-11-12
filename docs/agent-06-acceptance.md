# AGENT-06 施工与验收记录

后端基线 `5a1ab61`，桌面基线 `8f42b73`。两仓各使用本地 `codex/agent-06`。2026-11-12 验证。

| 工单 | 已覆盖 | 待实测 |
|---|---|---|
| 06A | 模拟设备完成 A 后试图结束 A+B，协调器拒绝并继续 B。两项均有当前修订证据后完成。旧修订、重复回执及丢失回执路径有回归。 | `observe`，真实跨聊天改令及随后操作。 |
| 06B | 后端回归验证等待问题、暂停、预算续授、进程标识切换后重新认领、取消后拒绝晚到的续轮。Edge 组件夹具验证桌宠问题显示与「继续本任务」。 | `observe`，实际后端进程重启与真实桌宠组合。 |
| 06C | 后端协议回归验证父会话活动/弱态、关闭租约取消、拒绝迟到回执。Windows Rust 执行器和传输测试通过。 | `observe`，物理 Esc、桌宠与聊天取消的实机组合，单屏 125%/150%、双屏混合缩放、窗口切换和锁屏/解锁。 |
| 06D | 模拟设备与 HTTP 供应商覆盖空 UIA 节点时的视觉定位和后置核验，拒绝错误帧、重复上传、缺少动作回执、仅有客户端成功回执及不确定结论。定位 JSON 继续校验字段和坐标。 | `observe`，真实 Qwen3-VL-Flash、Edge 纯图标、Canvas 与新父会话的记事本完整联调。 |

## 实机及真实模型证据

| 场景 | 实测与状态 | 模型／地域 | 像素／延迟 p50、p95 | JSON／命中／动作／目标成功率 | 输入、输出 Token／费用 |
|---|---|---|---|---|---|
| Windows 记事本 | 第一次真实启动临时文档后遇到 `scope_changed`。用同一临时文档重试，仍无法保持授权窗口在前台。旧版 AGENT-05 的单例见已知问题。 | 原生冒烟测试无模型 | 未取得有效观察 | 未完成 | 无模型调用 |
| Edge 纯图标 | `observe`。当前 `computer_use` 无可用云端连接，未取得真实供应商响应。 | Qwen3-VL-Flash 待配置 | 无样本 | 无样本 | 无样本 |
| Canvas 或自绘控件 | `observe`。协议回归使用合成 JPEG 和模拟响应。 | Qwen3-VL-Flash 待配置 | 无真实样本 | 无真实样本 | 无真实样本 |
| 云端遮挡、不在截图、超时及取消 | `observe`。协议回归覆盖不确定、超时、暂停与取消后的租约拒绝。 | 模拟供应商 | 无真实样本 | 无真实样本 | 无真实样本 |

格式复现样本使用合成响应，例如 `` ```json {"status":"found","x":0.5,"y":0.5} ``` ``、`{"status":"found","x":0.5,"y":0.5,"extra":true}` 及 `{"status":"found","x":1.2,"y":0.5}`。定位端拒绝这些响应，并通过 `last_vision.error_category` 区分格式、结构及坐标错误。`GET /computer-use/status` 的最近一次云端调用记录模型、地域、帧尺寸、耗时、状态及供应商上报的 Token。真实样本和费率齐备后才可填写成本。

百炼的 OpenAI 兼容域名按业务空间和地域选择。配置连接后需要重新开启桌面截图授权，再验收纯图标的定位、点击、结果核验和父会话完成四个阶段。参见 [百炼 OpenAI Vision 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-vl-compatible-with-openai) 和 [百炼地域地址](https://help.aliyun.com/zh/model-studio/base-url)。

## 验证命令

后端定向回归通过 115 项。桌面 Rust 回归通过 4 项，另有 4 项环境依赖测试由默认命令跳过。桌面构建和 Edge 组件夹具通过。

- 后端使用 Python 3.12 运行 `python -m pytest -q tests/test_computer_use.py tests/test_agent_runtime_task_manager.py tests/test_tool_schema_portable.py tests/test_no_hardcoded_character.py tests/test_r3_scope_lint.py tests/test_line_endings.py tests/test_pretool_router.py`。
- 桌面构建与原生回归执行 `npm.cmd run build` 和 `cargo test --locked --manifest-path src-tauri/Cargo.toml --lib computer_use`。
- 桌面组件测试启动本地 Vite，设置 `DESKTOP_TEST_URL` 后执行 `node scripts/computer-use-browser.mjs`。
- Windows 原生记事本冒烟使用 Rust 测试可执行文件，参数为 `--ignored --exact computer_use::tests::notepad_smoke --nocapture`。测试目标为临时文本文件。
