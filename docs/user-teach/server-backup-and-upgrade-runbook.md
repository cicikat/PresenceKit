# 服务器备份、回传与升级操作手册

本文面向 Ubuntu 单机部署。目标是每周把服务器上的 PresenceKit 私有状态回传到本地，并在升级前留下可验证的恢复点。

底层规则与文件清单以 [offline-state-backup.md](../offline-state-backup.md) 为准。本文提供可直接执行的运维流程。

## 先理解三种目录

- `<安装目录>`：正在运行的源码和数据，例如 `/home/<用户>/apps/presencekit`。
- `<快照目录>`：`backup-state create` 生成的独立目录，例如 `/home/<用户>/backups/presencekit/server-<时间戳>`。
- `<恢复演练目录>`：`backup-state restore` 生成的全新目录。它用于校验恢复结果，不会自动替换正在运行的安装。

恢复过的初始快照如果已经把其中的受保护文件复制到 `<安装目录>`，数据就已经进入运行后端；后续创建的 `server-<时间戳>` 只是独立备份，不存在“等待合并”状态。

快照当前覆盖：

- `data/` 中需要保护的状态，排除可重建缓存、普通日志和临时运行标记；
- `userdata/`；
- `config.yaml`，以及存在时的 `config.local.yaml`、`secrets.local.yaml`；
- inventory 中声明的兼容私有 authored assets。

它不备份源码、`.venv/`、公共 `bundled/` 或普通日志。`protected_volume` 是未加密快照，可能包含 token、API 密钥、角色资料和聊天记忆；只能存放在可信磁盘，不要上传到公开 GitHub Release 或公共网盘。

## 每周备份与回传

以下命令都在服务器执行。先进入安装目录：

```bash
cd /home/<用户>/apps/presencekit
```

### 1. 停止服务

```bash
sudo systemctl stop presencekit
systemctl is-active presencekit
```

第二条应输出 `inactive`。不要在服务运行时复制 `data/`，否则 SQLite、JSON 和 YAML 可能来自不同时间点。

### 2. 创建并验证快照

```bash
stamp=$(date +%Y%m%d-%H%M%S)
snapshot="/home/<用户>/backups/presencekit/server-$stamp"
mkdir -p /home/<用户>/backups/presencekit

./.venv/bin/python main.py backup-state create \
  --output "$snapshot" \
  --protection-mode protected_volume

./.venv/bin/python main.py backup-state verify "$snapshot"
```

只有 `verify` 成功后才继续。输出目录不能预先存在；若本轮失败，换一个新时间戳，不要复用半成品目录。

如果出现 `service_state_unknown`：

```bash
systemctl is-active presencekit
ps -ef | grep '[p]ython.*main.py'
ls -l data/runtime/service_state.json 2>/dev/null
```

先确认没有残留后端进程。不要为了绕过保护直接手工打包 live data。Linux 进程自识别问题已在 `90a3f7c` 修复；旧版部署应更新到包含该修复的版本。

### 3. 压缩并生成传输哈希

```bash
archive="$snapshot.tar.gz"
tar -C "$(dirname "$snapshot")" -czf "$archive" "$(basename "$snapshot")"
sha256sum "$archive" | tee "$archive.sha256"
ls -lh "$archive" "$archive.sha256"
```

目录快照仍留在服务器；`.tar.gz` 只是便于回传的传输包装，不会改变 manifest。

### 4. 立即恢复服务

```bash
sudo systemctl start presencekit
systemctl is-active presencekit
systemctl status presencekit --no-pager -l
```

应看到 `active (running)`。备份停机时间不应被文件回传速度拉长，因此回传在服务重新启动后进行。

### 5. 从 Windows 回传，支持断点续传

微信扫码 SSH 容易在长时间 `scp` 中断。推荐使用系统自带的 `sftp`，并用 `get -a` 续传：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\PresenceKitBackups" | Out-Null
sftp <SSH用户>@<服务器公网地址>
```

进入 SFTP 后：

```text
lcd <本地备份目录>
get -a /home/<用户>/backups/presencekit/server-<时间戳>.tar.gz
get /home/<用户>/backups/presencekit/server-<时间戳>.tar.gz.sha256
bye
```

首次下载也可以直接使用 `get -a`。若连接中断，重新扫码登录并再次执行同一条 `get -a`；不要删除已经下载的部分文件。

### 6. 本地复算哈希

```powershell
Get-FileHash -Algorithm SHA256 "$env:USERPROFILE\PresenceKitBackups\server-<时间戳>.tar.gz"
```

结果必须与服务器 `sha256sum` 完全一致。大小看似正确但哈希不同，仍然属于损坏或未传完，必须续传或重下。

## 本地验证与恢复演练

Windows 自带 `tar.exe` 对某些非 ASCII 文件名可能失败。遇到这类错误时，用 Python 标准库解包：

```powershell
$archive = "$env:USERPROFILE\PresenceKitBackups\server-<时间戳>.tar.gz"
$extractRoot = "$env:USERPROFILE\PresenceKitBackups\verify-<时间戳>"
New-Item -ItemType Directory -Force $extractRoot | Out-Null

@'
import sys
import tarfile

archive, target = sys.argv[1], sys.argv[2]
with tarfile.open(archive, "r:gz") as tf:
    tf.extractall(target, filter="data")
'@ | python - $archive $extractRoot
```

在本地 PresenceKit 仓库中验证解包后的快照目录：

```powershell
python main.py backup-state verify "<解包后的快照目录>"
```

每月至少做一次恢复演练，目标必须是不存在或完全为空的目录：

```powershell
python main.py backup-state restore "<解包后的快照目录>" --target "<新的空恢复目录>"
```

成功标志是 manifest 校验通过、离线初始化验证通过，并生成 `.presencekit-recovery/recovery-report.json`。这仍不会自动切换生产安装。

## 从备份恢复服务器

不要直接把 tar 包覆盖到 live 安装。推荐流程：

1. 在服务器安装与快照兼容的 PresenceKit 版本和依赖。
2. 解压快照并运行 `backup-state verify`。
3. restore 到安装目录之外的全新空目录。
4. 检查恢复报告和目录内容。
5. 停止服务后，只按受保护 inventory 将恢复结果切换到安装目录。
6. 启动服务并检查 health、管理面板和客户端连接。

示例验证和 restore：

```bash
cd /home/<用户>/apps/presencekit
./.venv/bin/python main.py backup-state verify /home/<用户>/restore/<快照目录>
./.venv/bin/python main.py backup-state restore \
  /home/<用户>/restore/<快照目录> \
  --target /home/<用户>/restore/presencekit-recovered
```

## 更新服务器到新版本

服务器源码部署推荐跟随已经发布并验证过的 `vX.Y.Z` tag，而不是无条件追踪 `main`。发布 ZIP 当前面向 Windows，Ubuntu 使用 Git 源码和本机 venv。

### 更新前检查

```bash
cd /home/<用户>/apps/presencekit
git status --short
git describe --tags --always
systemctl is-active presencekit
```

`git status --short` 应为空。若有本地修改，先人工确认来源；不要运行 `reset --hard`，也不要让 `git pull` 猜测如何合并服务器私改。

### 正式更新顺序

1. 按本文创建、验证并回传一份升级前快照。
2. 保持服务停止状态，获取 tags 并查看候选版本：

```bash
cd /home/<用户>/apps/presencekit
git fetch --tags origin
git tag --sort=-version:refname | head -n 10
```

3. 切换到明确的目标 tag：

```bash
git switch --detach vX.Y.Z
```

4. 同步 Linux 依赖并检查：

```bash
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-full.txt
./.venv/bin/python -m pip check
```

5. 启动并验收：

```bash
sudo systemctl start presencekit
systemctl is-active presencekit
systemctl status presencekit --no-pager -l
curl -sS http://127.0.0.1:8080/system/health
```

若目标 release 尚未包含服务器临时 cherry-pick 的修复，不要切换。应等待包含该修复的新 tag，或由维护者明确给出补丁 commit；`git fetch origin <短哈希>` 不保证能获取对象。

### 追踪 main 的开发服务器

只有明确接受未发布变更时才使用：

```bash
git switch main
git pull --ff-only origin main
```

仍需先备份、停服务、同步依赖和验收。`--ff-only` 会在本地历史分叉时明确失败，避免自动生成意外 merge。

## 常见中断处理

| 中断点 | 处理 |
|---|---|
| 创建快照失败 | 保持服务停止，读错误；修复后使用新时间戳重建。 |
| `verify` 失败 | 不回传、不作为恢复点；保留现场排查或删除后重建。 |
| 压缩中断 | 删除不完整的 `.tar.gz` 后重新压缩；目录快照不受影响。 |
| SFTP/扫码连接中断 | 服务无需停止；重连后对同名本地文件执行 `get -a`。 |
| 本地哈希不一致 | 视为不可用，继续续传或删除本地文件后重下。 |
| 依赖安装失败 | 不启动新代码；保留终端输出，修复网络/版本后重试，必要时切回原 tag。 |
| 新版本启动失败 | 停服务，保存日志；切回原 tag 并恢复其依赖。若新代码已改写数据，使用升级前快照恢复，不做 downgrade 猜测。 |

## 保留建议

- 本地至少保留最近 4 份周备份和每次升级前备份。
- 服务器可只保留最近 2 份已回传且哈希一致的归档，但删除前再次确认本地文件可读。
- 至少保留两种物理介质；任何离开可信设备的副本都应使用操作系统或成熟归档工具加密。
- 记录每份备份的日期、运行版本、服务器哈希、本地哈希和恢复演练结果。

## 服务器状态同步到本地

服务器备份回传并完成哈希校验后，如需将最新状态同步到已有的 Windows 本地项目，不要替换整个项目目录。源码、`.venv/` 和 `.git/` 保留本地版本，只同步备份中的受保护状态。

将回传的 `.tar.gz` 解包到独立的恢复演练目录。
在本地项目中验证快照：

```powershell

python main.py backup-state verify "<解包后的快照目录>"

```

先恢复到新的空目录并完成恢复验证：

```powershell

python main.py backup-state restore "<解包后的快照目录>" --target "<恢复演练目录>"

```

确认 `.presencekit-recovery/recovery-report.json` 中 `overall_status` 为 `ok` 后，再同步到本地项目。

同步 `data/` 和 `userdata/` 时复制目录内容，而不是将目录本身再次嵌套：

```powershell

robocopy "<恢复演练目录>\data" "<本地项目>\data" /E

robocopy "<恢复演练目录>\userdata" "<本地项目>\userdata" /E

```

不要使用会产生 `data/data/` 或 `userdata/userdata/` 层级的目录复制方式。

`config.yaml`、`config.local.yaml`、`secrets.local.yaml` 按本地部署需求决定是否同步；其中 `config.local.yaml` 和 `secrets.local.yaml` 可能包含本地环境专属配置，不应无条件覆盖。

同步完成后启动本地 PresenceKit 并检查运行状态。确认正常后，可删除本次解包和恢复演练产生的临时目录；已校验的 `.tar.gz` 及其 `.sha256` 应作为正式备份保留。
\n+Windows 正确校验命令：使用项目依赖所在的 Python 解释器绝对路径，不要使用 WindowsApps 占位的 `python.exe`。命令格式：

```powershell
& "<Python 绝对路径>" main.py backup-state verify "<解压后的快照目录>"
```
