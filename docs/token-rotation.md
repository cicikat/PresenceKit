# Token 轮换与配置速查

> 首次配置见 `python scripts/setup_auth.py`（自动生成 `config.admin.secret_key` + 五个标准 token +
> 本地密码本 `secrets.local.yaml`）。本文档是之后手工排障/轮换时的命令清单。
> 鉴权模型/scope 表见 `docs/security.md`。

## 通用说明

- **明文只显示一次**：面板/API 只存 `sha256(token)`，创建/轮换的响应体是明文能被看到的唯一
  时刻。界面没有「查看已有 token 明文」这回事——不是没做，是设计上不可能（服务端根本没存）。
  丢了就 rotate，不影响其他持有者。
- **401 vs 403 vs 429**：
  - `401` — token 缺失/无效（不认识这个值，或者 label 被 disable/删除/过期）。
  - `403` — token 本身有效，但 scope 不够（`detail` 里会给出所需 scope，这个信息可公开）。
  - `429` — 同一来源 IP 60 秒内 ≥10 次 401 触发限速，持续 300 秒；**重启后端可立即解除**
    （限速状态是进程内存，不落盘）。
- **Rotate 的连带影响**：旧值立即失效，持有旧值的设备会从下一次请求开始收到 `401`；
  如果设备本身有重试逻辑，短时间密集重试可能把自己的 IP 顶进限速窗口（见上条）。
  Rotate 后应尽快把新值同步进对应设备的配置。
- **break-glass secret**（即 `config.admin.secret_key` / env `YEXUAN_ADMIN_SECRET`，登录面板用的
  「密码」）：永远等价一条虚拟 `admin` scope token，label 固定 `legacy-admin`，是 bootstrap 锚点
  ——没有它就无法调用建 token 的管理 API。
  - 改法：改 `config.yaml` 里 `admin.secret_key` 的值，或设置环境变量 `YEXUAN_ADMIN_SECRET`
    （env 优先于 config.yaml）。改完需要重启后端才生效（不是热重载）。
  - 保管建议：只有你自己知道；不要把它当成某个具体设备的凭据发出去——边缘设备应该用
    `POST /auth/tokens` 签发的最小权限 token（见下表），丢了只影响那一个设备。

## 各设备一览

| label | profile / scopes | 配置去向 | 换装后需重启 |
|---|---|---|---|
| `desktop-main` | `desktop`（chat, state.read, memory.read, activity, persona, hardware, sensor.write, ws.desktop） | `PresenceKit-desktop/config/client.local.json` → `adminToken` | 桌面客户端（Tauri，含内嵌 sensor） |
| `mobile-main` | `mobile`（chat, state.read, memory.read, activity, persona, sensor.write） | 手机 app 系统设置 → Token 弹窗 | 手机 App（yexuan_memery） |
| `watch-main` | `watch`（sensor.write） | Watch 端配置（如 iOS Shortcuts，Bearer 值） | Watch 端 Shortcut |
| `esp32-device` | `device`（ws.device） | 固件配置（`firmware/presence-device/include/secrets.h`，烧录前写入） | **需要重新烧录**，不是热更新 |
| `admin-panel` | `panel`（admin） | 浏览器面板登录框（`localStorage.qq_admin_key`） | 无需重启，重新登录面板即可 |

`sensor` profile 仍保留，供未来独立只写感知端按需手工签发；首次配置不再生成 `sensor-service` label。历史同名 token 确认无调用后可停用或删除。

Rotate 后本仓这一端不需要重启（后端 token registry 是 mtime 热重载）——上表「换装后需重启」
指的都是**对端设备**，因为它们把旧明文缓存在了自己的配置里。

## Rotate 命令

以下命令均需持有 `admin` scope 的 token（`legacy-admin` 或 `panel` profile token）。
把 `<ADMIN_TOKEN>` 换成你的 break-glass secret 或 admin-panel token，`<label>` 换成表中的 label。

**PowerShell**

```powershell
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/auth/tokens/<label>/rotate" `
  -Headers @{ Authorization = "Bearer <ADMIN_TOKEN>" }
$resp.token   # 新明文，仅此一次——立刻复制进对应设备配置
```

**curl**

```bash
curl -s -X POST http://127.0.0.1:8080/auth/tokens/<label>/rotate \
  -H "Authorization: Bearer <ADMIN_TOKEN>" | jq -r .token
```

停用/启用（不换值，只是临时挂起，例如怀疑设备丢失但还没确认要不要 rotate）：

```powershell
Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8080/auth/tokens/<label>" `
  -Headers @{ Authorization = "Bearer <ADMIN_TOKEN>"; "Content-Type" = "application/json" } `
  -Body '{"disabled": true}'
```

```bash
curl -s -X PATCH http://127.0.0.1:8080/auth/tokens/<label> \
  -H "Authorization: Bearer <ADMIN_TOKEN>" -H "Content-Type: application/json" \
  -d '{"disabled": true}'
```

批量重来一遍（五个标准 token 全部轮换，例如怀疑密码本泄漏）：

```bash
python scripts/setup_auth.py --rotate-all
```

## 排障

- 面板显示 `401`：token 缺失/被 disable/过期/写错了。检查 `secrets.local.yaml` 里对应条目，
  或直接在面板 Token 页 rotate 一个新的。
- 面板显示 `403`：token 有效但 scope 不够，`detail` 会写明缺哪个 scope——大概率是拿错了别的
  设备的 token，或者手工建 token 时 profile 选错了。
- 面板/设备突然 `429`：某处在用坏 token 高频重试（常见于换装漏改一处、或旧固件还在跑）。
  重启后端立即解除；根治要找到还在用旧值重试的那个进程。
