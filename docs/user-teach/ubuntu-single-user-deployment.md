# Ubuntu 单用户服务器部署示例

本文是一份从空 Ubuntu 服务器部署 PresenceKit Backend 的小型生产 runbook，也记录一次真实部署中遇到的常见问题。适用目标是个人使用、低并发、通过 Tailscale 私网访问；不包含 NapCat、MCP hardware gateway 或 BLE 服务。

## 方案结论

- Ubuntu 使用 Git 源码部署，固定到已发布的 `vX.Y.Z` tag。当前 backend Release ZIP 面向 Windows，不适合作为 Linux 服务包。
- Python 支持 3.10-3.12；Ubuntu 22.04 自带 Python 3.10 可以使用。必须使用 venv，避免污染系统 Python。
- 不需要 Docker。单后端进程由 systemd 管理更简单，也更节省 2 GB 主机资源。
- 后端监听 `127.0.0.1:8080`，Tailscale Serve 负责 tailnet 内转发。不要把无鉴权或管理端点直接暴露到公网。
- 私有数据从 `backup-state` 快照恢复，之后按 [server-backup-and-upgrade-runbook.md](server-backup-and-upgrade-runbook.md) 定期回传。

## 推荐目录

```text
/home/<用户>/
├── apps/
│   └── presencekit/           # Git 源码、venv 和 live data
├── backups/
│   └── presencekit/           # 服务器快照与传输归档
├── incoming/                  # 从本地上传的初始快照
├── restore/                   # 解包和离线恢复演练
└── scripts/                   # 可选的个人运维脚本
```

备份目录必须位于安装目录之外。

## 1. 检查基础环境

```bash
uname -a
lsb_release -a
python3 --version
git --version
python3 -m pip --version
python3 -m venv --help >/dev/null && echo "venv: OK"
free -h
df -h /
swapon --show
systemctl --version | head -n 1
```

安装缺失组件：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-pip python3-venv curl ca-certificates
```

不要在测试期顺手升级到新的 Ubuntu 大版本。先让当前 LTS 部署稳定运行并完成备份演练；发行版升级应另行安排维护窗口。

### 2 GB 主机增加 swap

2 GB RAM 可以运行个人后端，但依赖安装或瞬时任务可能触及内存上限。若 `swapon --show` 为空，可创建 2 GB swap：

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
swapon --show
free -h
```

## 2. 拉取后端源码

```bash
mkdir -p /home/<用户>/apps
cd /home/<用户>/apps
git clone --branch vX.Y.Z --depth 1 <PresenceKit仓库URL> presencekit
cd presencekit
git status --short
git describe --tags --exact-match
```

最后两条应分别无输出和输出目标 tag。生产服务器不建议直接 clone `main`。

## 3. 创建 venv 并安装依赖

Linux 源码部署使用 `requirements-full.txt`：

```bash
cd /home/<用户>/apps/presencekit
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-full.txt
./.venv/bin/python -m pip check
```

可做核心导入检查：

```bash
./.venv/bin/python -c "import aiohttp, fastapi, uvicorn, openai, yaml, sqlite_vec; print('core dependencies: OK')"
```

不使用 Windows 的 `.venv/Scripts/python.exe`，Linux venv 入口是 `.venv/bin/python`。

## 4. 初始化或迁移私有数据

### 全新用户

从仓库示例配置开始，按项目当前首次配置文档创建 `config.yaml`、本地覆盖和 scoped tokens。不要把真实 token、API key 或账号信息提交到 Git。

### 从本地迁移

先在本地停止 PresenceKit，用 `backup-state create` 和 `verify` 创建完整快照，再上传到 `/home/<用户>/incoming/`。不要只凭经验手工挑选 `data/` 和 `userdata/`；inventory 还负责配置和兼容私有资产。

服务器收到 `.tar.gz` 后先比对上传前记录的 SHA-256：

```bash
sha256sum /home/<用户>/incoming/<快照>.tar.gz
tar -tzf /home/<用户>/incoming/<快照>.tar.gz | head
```

解包、验证并恢复到新目录：

```bash
mkdir -p /home/<用户>/restore
tar -C /home/<用户>/restore -xzf /home/<用户>/incoming/<快照>.tar.gz

cd /home/<用户>/apps/presencekit
./.venv/bin/python main.py backup-state verify /home/<用户>/restore/<快照目录>
./.venv/bin/python main.py backup-state restore \
  /home/<用户>/restore/<快照目录> \
  --target /home/<用户>/restore/presencekit-initial
```

成功输出应说明“恢复完成并通过离线初始化验证”。检查恢复内容后，在服务尚未启动、安装目录中不存在同名私有根的前提下，把恢复结果中的受保护项放入安装目录。典型项是：

```text
data/
userdata/
config.yaml
config.local.yaml       # 存在时
secrets.local.yaml      # 存在时
```

不要把 `.presencekit-recovery/` 当运行数据复制进去，也不要用旧快照覆盖新版本的源码、`.venv/`、`bundled/` 或脚本。

## 5. 配置远程单机模式

建议把服务器差异放在 gitignored 的 `config.local.yaml`，不要改 tracked 示例。最终生效值至少应满足：

```yaml
standalone_mode: true
deployment:
  mode: remote_server
admin:
  host: 127.0.0.1
  port: 8080
qq:
  enabled: false
mcp_servers:
  enabled: false
hardware:
  enabled: false
```

字段的实际合并方式以当前版本配置加载器为准。不要只看文件文本，应在启动日志或只读 effective-state 接口中确认最终值。

`127.0.0.1` 不等于“其他设备永远不能访问”。它阻止公网网卡直连，Tailscale Serve 在服务器本机反向代理到这个回环端口，tailnet 设备仍可访问。

## 6. 安装 Tailscale

按 Tailscale 官方 Linux 安装流程执行：

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
tailscale ip -4
systemctl is-enabled tailscaled
systemctl is-active tailscaled
```

扫码或浏览器授权时，必须确认服务器加入了与电脑、手机相同的 tailnet。登录错账号时，先退出当前节点再重新授权：

```bash
sudo tailscale logout
sudo tailscale up
```

官方安装与 Serve 文档：

- <https://tailscale.com/docs/install/linux>
- <https://tailscale.com/docs/features/tailscale-serve>

## 7. 用 systemd 托管后端

创建 `/etc/systemd/system/presencekit.service`：

```bash
sudo tee /etc/systemd/system/presencekit.service >/dev/null <<'EOF'
[Unit]
Description=PresenceKit Backend
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
Type=simple
User=<用户>
Group=<用户>
WorkingDirectory=/home/<用户>/apps/presencekit
ExecStart=/home/<用户>/apps/presencekit/.venv/bin/python /home/<用户>/apps/presencekit/main.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=control-group
UMask=0077
Environment=PYTHONUNBUFFERED=1
ExecStopPost=/usr/bin/rm -f /home/<用户>/apps/presencekit/data/runtime/service_state.json

[Install]
WantedBy=multi-user.target
EOF
```

把所有 `<用户>` 替换成实际 Linux 用户名。然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now presencekit
systemctl is-enabled presencekit
systemctl is-active presencekit
systemctl status presencekit --no-pager -l
```

`is-enabled` 应为 `enabled`，`is-active` 应为 `active`。

如果 unit 显示 `static`，通常是缺少 `[Install]`。不要把追加命令在目录名后换行执行，否则 shell 会把 `presencekit.service` 当成新命令。最稳妥的做法是完整重写 unit，再 `daemon-reload` 和 `enable`。

## 8. 配置 tailnet 内访问

先验证后端本机可达：

```bash
curl -I http://127.0.0.1:8080
```

根路由只允许 `GET` 时，`curl -I` 发送的 `HEAD` 返回 `405 Method Not Allowed` 也能证明 Uvicorn 已经监听；这不是服务启动失败。

在不需要公网 HTTPS 的测试期，可使用 tailnet-only HTTP Serve：

```bash
sudo tailscale serve reset
sudo tailscale serve --bg --http=80 http://127.0.0.1:8080
sudo tailscale serve status
```

客户端使用 Serve 输出的 tailnet hostname，不要在自己电脑浏览器里访问 `127.0.0.1:8080`；客户端的 `127.0.0.1` 指向客户端自身。

远程 endpoint 形式为：

```text
管理面/API: http://<服务器MagicDNS名称>/
桌面 WebSocket: ws://<服务器MagicDNS名称>/ws/desktop
```

## 9. 代理问题

若 `curl --noproxy "*" http://<服务器MagicDNS名称>/` 能下载 HTML，而普通浏览器打不开，问题在客户端系统代理/PAC，不在 PresenceKit、systemd 或 Tailscale。

验证时在 Windows PowerShell 执行：

```powershell
Resolve-DnsName <服务器MagicDNS完整域名>
curl.exe --noproxy "*" http://<服务器MagicDNS完整域名>/ -o "$env:TEMP\presencekit.html"
Get-Content "$env:TEMP\presencekit.html" -TotalCount 5
```

将服务器 MagicDNS 主机名和 `*.ts.net` 加入代理软件的 DIRECT/bypass 规则。PresenceKit desktop 的原生 HTTP 客户端和 WebSocket 已有绕过系统代理的能力，但普通浏览器的首次管理面请求仍由浏览器代理策略决定。

遇到 HTTPS Serve 握手失败时，不要立刻改后端监听到 `0.0.0.0` 或开放云防火墙。先用 HTTP tailnet-only Serve 判断是否只是本地代理/TLS 链路问题。Tailscale 流量仍只对 tailnet 可见，但管理 token 仍应正常启用。

## 10. 验收与重启演练

部署完成后至少检查：

```bash
systemctl is-enabled presencekit
systemctl is-active presencekit
systemctl status presencekit --no-pager -l
tailscale status
sudo tailscale serve status
ss -ltnp | grep ':8080'
```

期望后端只监听 `127.0.0.1:8080`，systemd 为 enabled/active，Serve 指向该端口。然后从桌面和手机验证管理面、模型测试、聊天与 WebSocket。

最后执行一次服务器重启演练：

```bash
sudo reboot
```

重新登录后重复上述检查。能看到客户端 WebSocket 自动连接，才说明 systemd、Tailscale 和客户端配置真正完成闭环。

## 11. 常见踩坑表

| 现象 | 原因与处理 |
|---|---|
| `.venv/Scripts/python.exe` 找不到 | 这是 Windows 路径；Ubuntu 使用 `.venv/bin/python`。 |
| `git fetch origin <短哈希>` 找不到 remote ref | 短 commit ID 不一定是远端 ref；获取 branch/tag，或使用完整可达 commit。 |
| backup 报 `service_state_unknown` | 先确认服务和残留进程已停止；更新到包含 Linux 自进程排除与 Windows pid 探测修复的版本。 |
| `systemctl enable` 说 unit 没有 installation config | unit 缺 `[Install]`/`WantedBy`；完整修正文件后 reload。 |
| `Resolve-DnsName`、`curl.exe` 在 Ubuntu 找不到 | 这是 Windows PowerShell 命令，应退出 SSH 后在 Windows 终端执行。`exit` 正常会结束 SSH 会话。 |
| `curl -I` 返回 405 | 服务已响应，但根路由不接受 HEAD；改用 GET 或 health endpoint。 |
| Tailscale 两端在线但浏览器打不开 | 用 `curl.exe --noproxy "*"` 区分代理问题，并设置 DIRECT 规则。 |
| `scp` 扫码会话中途断开 | 使用 SFTP `get -a` 断点续传，完成后必须比对两端 SHA-256。 |
| Windows `tar` 解压中文文件名失败 | 用 Python `tarfile` 安全解包，再运行 `backup-state verify`。 |

## 12. 后续维护

- 每周回传一次已验证的私有状态快照。
- 每次更新代码前再做一份升级前快照。
- 更新 follow release tag，不在服务器直接改源码。
- 每月做一次 restore 到空目录的离线恢复演练。
- 定期检查磁盘、内存、swap、systemd 日志和 Tailscale 连接。
- 暂不使用的 QQ、MCP 和 hardware 保持显式关闭，避免把未验收的外部依赖带入 24 小时服务。
