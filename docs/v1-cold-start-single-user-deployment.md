# v1 冷启动与单用户部署

本文是新 v1 安装的 operator runbook，描述受支持的单用户形态：一个 owner、一个后端进程，以及本地或明确受保护的远程客户端。即使所有客户端都属于同一个人，鉴权仍然是强制要求。

## 全新安装

clone 与 Release 的首次配置共用 `config.example.yaml`。工具授权和 tool loop 默认可用，分类、步数及冻结工具排除沿用已确认偏好；用户显式关闭工具仍会阻断执行。桌面/手机、QQ、微信、TTS、视觉、HDS、MCP、硬件、邮件和浏览器等外部接入保持默认关闭，先配好依赖与凭据，再在管理面开启。Embedding 留空即不调用向量服务，继续关键词召回。表情包默认关闭；不包含维护者的账号、模型连接、授权状态文件或私人数据。

首次连接桌面或手机前，在管理面「客户端设置」开启对应通道；管理面自身默认可用。已有安装不会被公共模板覆盖，升级保留用户自己的 `config.yaml`。

1. 使用 Python 3.10-3.12（推荐并受支持的是 3.12），安装依赖，并将 `config.example.yaml` 复制为 `config.yaml`。
2. 运行 `python scripts/setup_auth.py`。将 `secrets.local.yaml` 保持在 source control 之外。该命令创建 break-glass admin secret 和 scoped device token；后续再次运行时不会打印已有 token value。
3. 如果只部署桌面端，设置 `standalone_mode: true`。除非 NapCat 是此部署的一部分，否则保持 `qq.enabled` 为 false。
4. 启动 `python main.py`。服务启动前必须具备有效的 auth secret/token、可加载的 default character 和有效 data root。
5. 在配置的 loopback 地址打开 admin panel。Setup 页面要求可用的 chat model 和 `scheduler.owner_id`；Embedding 是可选项，未配置时会回退到 keyword recall。
6. 选择或创建 active character card，测试模型连接，然后通过桌面客户端或 `/desktop/chat` 发送一条 owner chat。

冷启动成功条件是真实的 assistant reply，而不只是 HTTP 进程正在运行。不要把开发环境的 `data/`、`config.yaml` 或 `secrets.local.yaml` 复制到冷启动测试中。

## 保守默认值

公共模板在 owner 明确 opt-in 前有意保持 dormant：

| Surface | 首次运行预期 | 验证方式 |
|---|---|---|
| Unsolicited speech | `scheduler.enabled: false`；没有 owner 就不会运行 proactive | `GET /scheduler/status` |
| Autonomy | Durable autonomy config 初始关闭 | `GET /admin/autonomy/effective-state` |
| MCP | 全局和每个 server 的开关都关闭；启动时不会连接外部 MCP | `GET /settings/mcp` |
| Hardware | Hardware 和 Intiface 关闭；启动时不创建 job | `GET /hardware/status`（可用时） |
| High-risk tools | Shutdown、sleep、toy actuation 等工具关闭或需要 confirmation gate | Admin tool policy page 和 `GET /status` |
| QQ/NapCat | 默认关闭；standalone mode 不创建 QQ connection | startup log 和 `GET /status` |
| Embedding | Placeholder credential 视为未配置；聊天仍可使用 | `GET /settings/setup-status` |

`coplay.enabled` 只是 deployment capability switch，不会启动 game session。绝不能把它当成自动开始游戏的 permission。

## 就绪检查表

首次设置、每次 restore 或 upgrade 后都运行这些检查，并用 release candidate commit 记录结果。

- [ ] **Model：** Setup 报告 base chat model 已配置且 connection test 成功。Provider error 必须可处理，不能静默选择另一个 provider。
- [ ] **Data paths：** `/status` 报告预期的 production data root；`mode` 不是 test sandbox。v1 初始化成功后应存在 `data/layout_version.json`。
- [ ] **Permissions：** service account 可以读取 bundled asset，并写入声明的 `data/` 与 `userdata/` 根目录，但 backup destination 位于安装目录之外。Secrets 不能被全局读取。
- [ ] **Authentication：** panel 接受 scoped panel token；缺少 secret/token 会阻止启动；device token 不能调用 admin-only route。
- [ ] **Character：** active card 成功加载并交付 chat reply。Placeholder card 是产品质量警告，不是 runtime fallback。
- [ ] **Scheduler/autonomy：** `/scheduler/status` 与 `/admin/autonomy/effective-state` 显示预期的 enabled state、owner、cooldown 和 channel。关闭的 scheduler 不能产生 turn。
- [ ] **MCP：** 未明确需要时，`/settings/mcp` 显示 disabled。启用时每个 server 都有本地 allowlist/policy；server 失败时应报告 unavailable，但不能阻止本地聊天。
- [ ] **Channels：** 只测试一个预期 channel。按情况验证 desktop WS 或 mobile poll/ack；使用 `standalone_mode` 时保持 QQ 断开。
- [ ] **Health and logs：** 使用 `state.read` 可访问 `/system/health`，理解 silent-failure counter，日志中没有 credential URL。

以上检查是运行证据。HTTP health response 为绿色，单独不能证明 model、channel 或 scheduler 已就绪。

## v0.2.2 迁移

v0.2.2 是 preview source，不是 automatic-update source。受支持的路径有意保持显式：

1. 停止旧进程，并创建/验证 offline private-state snapshot。
2. 在新的空目录安装 v1。
3. 只复制 [Offline Private-State Backup](offline-state-backup.md) 列出的受保护状态：`data/`、`userdata/`、本地 configuration/secrets 和经过复核的 legacy 私有 authored asset。不要覆盖 `core/`、`scripts/`、`defaults/`、`examples/`、`.venv/` 或其他程序文件。
4. 使用 `--fail-on-diverged --fail-on-invalid` 运行 authored-root dry run。`legacy-only`、`diverged`、`invalid`、`incomplete` 或 `unresolved` 结果都是需要人工复核的可操作阻塞项，绝不会被静默覆盖。
5. 启动 v1 并完成冷启动和就绪检查。第一次可用启动会写入 v1 layout marker；它不会声称已经迁移任意 preview state。

v1 及更高版本的 updater 只接受受支持的 v1 marker 和非 downgrade target。Preview source、future schema、缺失 marker 和 downgrade 都会在程序替换前失败。将 updater snapshot 或 offline private-state snapshot restore 到新的 target，然后手动切换。

## Backup、restore 与 retention

创建 snapshot 前停止服务。命令拒绝运行中或未知状态的服务，并且永远不会上传数据：

```powershell
python main.py backup-state create --output <protected-volume>\presencekit-snapshot --protection-mode protected_volume
python main.py backup-state verify <protected-volume>\presencekit-snapshot
```

至少在另一个受保护卷上保留一份近期 snapshot。Retention 和 off-site encryption 由 operator 负责；`protected_volume` 不是 encrypted archive。依赖 backup 前先测试 restore：

```powershell
python main.py backup-state restore <snapshot> --target <new-empty-directory>
```

Restore 会验证哈希，并在关闭 outbound call 的情况下执行只读 startup check。它不会替换 live installation、删除 source 或隐式执行 version migration。复核 recovery report，然后手动切换。

## 单用户 server 形态

- 本地客户端将 admin service 绑定到 `127.0.0.1`。若需要 LAN 或远程访问，在前面放置 HTTPS reverse proxy，限制 proxy 只暴露所需路径，并保持后端 bind 为私有。不要向公网暴露 plain HTTP 或 break-glass secret。
- 只给 service account 权限保存 `secrets.local.yaml` 和 proxy credential。使用 scoped 的 `panel`、`desktop`、`mobile`、`watch` 和 `device` token；不要在 edge device 上复用 admin secret。
- 停止服务后 backup，验证 manifest，并保持 backup destination 在安装目录之外。设备丢失或 backup 离开受保护主机后轮换 token。
- 按配置的 size/keep limit 轮换 `data/logs` 和 forensic log。不要把 secret 放进 debug prompt，也不要在短暂诊断窗口之外启用 LLM request logging。
- 使用相同 service account 和 data root 重启。此前进程留下的 active hardware job 会标记为 expired，并尝试显式停止；永远不会自动恢复。
- Scheduler schedule entry 默认使用 `restart_miss_policy: skip`。过期的 wake/autonomy signal 是 terminal 状态，autonomy 关闭时 one-shot desktop wake signal 会被丢弃。因此重启不会重放过时的 proactive event。

## 外部 MCP 失败行为

MCP 是可选项。`mcp_servers.enabled: false` 时，启动不得尝试 network connection。显式启用的 server 无法访问时，会记录为 unavailable，不暴露其工具，本地聊天继续运行。对于 `outcome_unknown` hardware action，不要自动重试；应检查设备状态或使用 emergency stop path。

## 剩余发布风险

以下事项不会由本 runbook 证明，必须继续出现在发布决策中：Android production signing 和 Keystore migration、真机 relay/Doze recovery、跨仓协议兼容性、可选 hardware 与 MCP integration，以及迁移 dry run 报告的任何 authored-root 条目。这些是 release evidence gap，不是削弱鉴权或默认启用 integration 的理由。
