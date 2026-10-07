# 离线私有状态 backup

`scripts/update_release.py` 为 updater rollback 创建的是**程序文件**副本。它会有意保留本地状态，因此不是私有状态 backup。`backup-state` 是独立的离线快照命令，负责保存下文描述的本地私有状态。

## 范围与安全边界

只有在 PresenceKit 已停止后才能创建快照。命令会在读取任何源文件前检查 lifecycle PID marker 和进程状态。若服务仍运行或结果无法确定，则 fail closed；它不会停止进程、围绕并发写入重试，也不会执行在线 backup。

当前的非敏感决策状态也可由 `state.read` token 通过 `GET /observability/backup-service-state` 查询；返回值只有 `offline`、`running` 或 `unknown`。

当前 protection inventory（版本 1）包括：

- `data/`，但排除明确的派生/取证缓存：vector index 与 SQLite sidecar、memory index、image cache、inbox、普通日志、debug 输出、pending perception 文件、选择性启用的 LLM request log，以及临时 service PID marker；
- 存在时的 `userdata/`；
- 必需的 `config.yaml`，以及存在时的 `secrets.local.yaml`；
- 仍为兼容性保留的 legacy 私有 authored-asset 子树：私有 cards/notes、Reality/Dream 资产、贴纸和非 example 的按角色 content。公共 `bundled/`、defaults、examples、源代码、虚拟环境、构建输出、release-updater backup 和普通日志不会被复制。

inventory 集中在 `core.backup_state.PROTECTION_ROOTS`。未来经过审计的私有根目录必须在这里分类；已知但未分类的根目录会导致创建失败，而不是被静默省略。

## 创建与验证

### Windows 每周 ZIP

在根目录双击 `备份数据.bat`。先手动停止服务；运行中或状态未知时拒绝打包。
脚本复用 protection inventory，排除 `data/test_sandbox/` 和上述缓存，先在安装目录外
创建并验证临时快照，再在根目录生成 `presencekit-data-<时间戳>-<随机后缀>.zip`。
ZIP 内保留 manifest 和 checksum，逐文件解压读取并核对 SHA-256 后才发布最终文件。
不覆盖或删除旧备份；失败清理本次临时文件。需要安装目录所在卷有足够的暂存空间。

这是用户主动选择的本地未加密 ZIP，包含配置、凭据与私有记忆，不能当成加密便携归档。
仅存放在可信存储；已加入 Git 忽略。每周复制到自行选择的备份介质。
恢复时先解压到独立目录，再用下文的 `backup-state verify` / `restore`。
此入口不改变原有 `backup-state create` 的输出边界与 portable encryption 限制。

快照必须放在安装目录之外，包括其 `data/` 和 `userdata/` 根目录之外。输出路径不能已经存在。

```powershell
python main.py backup-state create --output <protected-volume>\presencekit-snapshot --protection-mode protected_volume
python main.py backup-state verify <protected-volume>\presencekit-snapshot
```

使用 `--json` 获取小型结构化结果，使用 `--quiet` 抑制成功消息。结果不会打印 config、token、secret、memory 或 manifest 文件内容。

首个发行版本唯一支持的模式是 `protected_volume`：它是未加密的目录快照，operator 必须明确声明目标位于受保护卷中。不能把它描述成 encrypted。Portable/offsite archive 需要经过审计的标准加密后，命令才会支持。当前依赖集合没有获批准的 archive-encryption library，因此本功能不会自创密码学，也不接受命令行密码。未来 portable mode 应增加经过审核且维护中的依赖，并使用隐藏输入或 OS secret-store 处理密钥。

## Manifest 与验证

每份快照都有 `manifest.json` 和同级的 `manifest.sha256` integrity check。版本化 manifest 包含产品版本、data-layout marker 字段、时间戳、backup ID、protection mode、protection-root inventory、可选的缺失文件，以及每个文件的相对路径、大小、SHA-256 和 root ID。

它不会保存安装路径、配置内容、memory、token 或 secret。SQLite 文件只在服务停止时复制；未被明确排除的 WAL/SHM 文件作为普通声明文件纳入，不会被猜测或删除。

`verify` 会验证 manifest/checksum、受支持的 schema、layout metadata、root inventory、必需 config 条目、每个声明文件的大小/哈希、安全相对路径、reparse point、可读性和意外文件。它只验证 backup 完整性。

## 不支持的行为

该命令不会原地 restore、修改 live data、停止/启动服务、在服务运行时 backup、上传云存储、复制到网络盘或自动删除旧快照。在 restore 和 operator 存储策略经过设计与测试前，Retention 会刻意保持独立。

## Restore 与恢复演练

Restore 会先验证源快照，然后只发布到 live installation 和快照之外的不存在目录或完全空目录：

### Memory Event migration recovery drill

Historical Memory Event import requires a stopped service and a fresh verified
snapshot. First run `python scripts/migrate_memory_events.py --uid <uid>
--char-id <char_id>` against an isolated copy. Review the dry-run
`malformed`, `legacy_unknown`, and `conflict` counts before using `--apply
--backup-output <protected-volume>\<new-snapshot>` for one small batch.
Interruption is resumed by rerunning the same command; do not remove migration
state, old Markdown, archive DB, or media manually. Restore drills use
`backup-state restore` into a new empty directory, then verify the migration
pre-state and snapshot checksum before an operator decides to continue.

```powershell
python main.py backup-state restore <snapshot> --target <new-empty-directory>
```

它会拒绝不安全/绝对路径、reparse point、Windows 大小写冲突、ADS/device name、过长路径、超出文件数量/大小限制的输入以及哈希不匹配。每个恢复文件都会重新计算哈希。默认只读 startup check 会解析 config/auth，加载 active character/assets 和 Lore/Pipeline，并解析 JSON state，但不会启动服务。no-outbound guard 会阻止 LLM、MCP、QQ、channel fanout、scheduler、web/search/weather 和 hardware 调用。不会消费 queue，也不会写入 runtime memory/state。

恢复目录会收到 secret-safe 的 `.presencekit-recovery/recovery-report.json`。`--no-startup-check` 仅适用于诊断，不建议用于恢复。最终切换仍是手动操作；没有 automatic rollback、online restore 或隐式跨版本迁移。
