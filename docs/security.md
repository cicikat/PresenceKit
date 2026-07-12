# 鉴权模型（SEC-AUTH-2：Scoped Tokens）

> 本文只说明鉴权实现；整体风险边界、数据与部署假设见 [security_model.md](security_model.md)。

> 施工 brief：`cc-tasks/21-鉴权分层-scoped-tokens.md`。当前状态见 `docs/known-issues.md` → SEC-AUTH-2。
> P1（基座）+ P2（路由映射）+ P3（token 管理 API / 审计 / 限速）已合入。
> P4 前半（六类持有者签发 token）已完成（2026-07-04，生产 `tokens.yaml` 已有六条记录）；
> 后半（客户端接入后轮换 legacy secret）待各客户端仓完成配对 cc-tasks 后再做——见本文末尾。

单 owner 系统，不做多用户 / OAuth / JWT / session。所有管理面 HTTP + WS 端点鉴权统一走
`admin/auth.py`，opaque token + scope 分层，**default-deny**：端点未声明所需 scope 时自动
收敛为 `admin`-only（迁移期 fail-closed，见下——P2 完成后已无遗漏端点，但新增 router 忘记
声明仍会被 `tests/test_sec_auth2_scopes.py` 的全量扫描守卫拦下）。

## Scope 表

10 个 scope，`admin` 蕴含全部（`"admin" in token.scopes` 时任意 `require_scopes(...)` 直接通过）。

| scope | 语义 | 典型端点 |
|---|---|---|
| `admin` | 全权：settings 写、系统运维、token 管理、记忆写删 | `/system/reload`、`PUT /llm-params`、`/users/*` |
| `chat` | owner 对话回合 + 通道生命周期 + 上传/转写 | `/desktop/chat`、`/mobile/*`、`/desktop/wake\|activate`、`/upload/ingest`、`/transcribe`、`/group/*` |
| `state.read` | 低敏状态只读 | `/mood/state`、`/activity/current`、`/garden/state`、`/sensor/realtime`、`/watch/status`、`GET /status` |
| `memory.read` | 高敏内容只读 | `/diary/*`、`/chat-log/*`、`/history`、`/memory/*`（GET）、`/debug/user-hidden-state`、provenance/observe、relations（GET） |
| `sensor.write` | 感知数据写入（只写不读） | `POST /sensor/push`、`POST /watch/event` |
| `activity` | 活动/梦境 overlay 全生命周期 | `/dream/*`、`/activity/reading\|gomoku\|chess\|dream_seed/*` |
| `persona` | 人设/世界/呈现配置读写 | `/settings/prompt-assets`、`/jailbreak-entries`、`/lorebook`、character 卡、`/chat-mode\|chat-style\|chat-multi-message`、头像 |
| `hardware` | 实体硬件控制 + 危险模式开关 | `/hardware/*`、`GET\|PATCH /system/meta-mode` |
| `ws.desktop` | 连接 `/ws/desktop` | WS |
| `ws.device` | 连接 `/ws/device` | WS |

## Profile 表（建 token 时用 profile 名即可）

| profile | scopes | 发给谁 |
|---|---|---|
| `desktop` | chat, state.read, memory.read, activity, persona, hardware, sensor.write, ws.desktop | 桌面 Tauri 客户端 |
| `mobile` | chat, state.read, memory.read, activity, persona, sensor.write | 手机 Flutter 端（yexuan_memery，同为 owner 胖客户端；刻意不含 hardware/admin——手机是最易丢失的设备，丢机不泄危险模式与 settings 写权） |
| `sensor` | sensor.write | 手机 sensor-service |
| `watch` | sensor.write | Watch |
| `device` | ws.device | ESP32 具身硬件 |
| `panel` | admin | 管理面板网页 |

## 代码结构

| 文件 | 职责 |
|---|---|
| `admin/scopes.py` | `SCOPES` 全集、`PROFILES` 预置组合、`expand_scopes()`（展开 `profile:*` / 显式 scope，可混用，未知名抛 `ValueError`） |
| `admin/token_registry.py` | `TokenRecord` dataclass；加载 `data/runtime/auth/tokens.yaml`，按 mtime 热重载；`hash_token()`；`find_by_hash()`（跳过 disabled / 已过期，`hmac.compare_digest` 比对） |
| `admin/auth.py` | `TokenInfo` dataclass；`resolve_token(raw)`（legacy secret 优先，其次查 registry）；`require_scopes(*scopes)` 依赖工厂（含 401 限速 + 审计调用）；`verify_token = require_scopes("admin")` 别名；`authenticate_ws(websocket, required_scope)`；`reset_rate_limit_state_for_test()`（测试用） |
| `admin/audit.py` | `log_event(event, *, label=None, path=None, ip=None)` — 追加写 `data/runtime/auth/audit.jsonl`（走 `core.safe_write.safe_append_jsonl`，fail-open，不记 token 值） |
| `admin/routers/auth_tokens.py` | Token 管理 API：`GET/POST /auth/tokens`、`POST /auth/tokens/{label}/rotate`、`PATCH /auth/tokens/{label}`（disable/enable）、`DELETE /auth/tokens/{label}`、`GET /auth/profiles`（均 `admin` scope）；`GET /auth/whoami`（零 scope，任意有效 token） |
| `core/data_paths.py` | `auth_dir()` / `auth_tokens_file()` / `auth_audit_log()`（均经 `core/sandbox.get_paths()`，test 模式自动隔离） |

## 鉴权语义

- **HTTP**：无效 / 缺失 token → `401 Unauthorized`；有效 token 但 scope 不足 → `403`
  （`detail` 中给出所需 scope，这个信息可公开）。
- **WS**：`/ws/desktop` 要求 `ws.desktop`，`/ws/device` 要求 `ws.device`；scope 不足或无
  token 一律 `close(code=1008)`，不做区分（WS 没有 403 的等价物）。
- **legacy 兼容**：env `YEXUAN_ADMIN_SECRET`（优先）或 `config.admin.secret_key` 的值永远
  等价一条虚拟 `admin` token，label 固定 `legacy-admin`。这是 bootstrap 锚点——没有它就无法
  调用建 token 的管理 API。P1~P3 期间所有现存客户端（桌面、手机端、Watch、ESP32、管理面板网页）
  持有这同一个 secret，行为零变化。
- token 值不得进入任何日志；`admin/log_filter.py` 的 sanitizer 覆盖 access log，新代码只允许
  记录 label 和 hash 前 8 位。

## Token 管理操作手册

创建/轮换/停用/吊销均需持有 `admin` scope 的 token（legacy secret 或 `panel` profile token）。
**首次配置、按设备的 rotate 命令（PowerShell/curl）、401/403/429 排障、break-glass secret
修改方法，全部见 [`docs/token-rotation.md`](token-rotation.md)（避免两处维护，本节只列 API）。**

- **创建**：`POST /auth/tokens`，body `{"label": "esp32-front-door", "profile": "device"}`
  （或 `{"label": "...", "scopes": ["chat", "state.read"]}` 显式指定，`profile` 与 `scopes`
  二选一）。响应 `{"label": ..., "token": "emt_..."}` —— **明文仅此一次返回，界面展示后立即
  丢弃**，之后无法再取回，只能 rotate 换新值。
- **列表**：`GET /auth/tokens` — 返回 label / scopes / expires_at / disabled / hash 前 8 位，
  不含明文，不含完整 hash。`GET /auth/profiles` — profile → scopes 常量表（Create 表单用）。
- **轮换**：`POST /auth/tokens/{label}/rotate` — scope 不变，换发新明文（同样仅此一次），
  旧值立即失效。用于怀疑设备丢失/被拆机时的应急吊销+续用。
- **停用/启用**：`PATCH /auth/tokens/{label}` body `{"disabled": true|false}` — 不删除记录，
  立即使该 token 后续请求返回 401；再次 `disabled: false` 即恢复。
- **吊销**：`DELETE /auth/tokens/{label}` — 物理删除该条记录（未选用仅 disabled 标记方案，
  删除更符合"设备已失联，直接断"的语义，且避免 registry 无限累积废弃条目）。
- **身份自检**：`GET /auth/whoami` — 返回 `{label, scopes}`，任意有效 token 可调（不要求
  `admin`），管理面板用它在右上角显示"当前登录身份"。
- `label` 校验 `^[a-z0-9-]{1,32}$`；`legacy-admin` 是保留字，不可创建/轮换/停用/吊销
  （它不是 `tokens.yaml` 里的真实条目，而是 `resolve_token()` 里对 legacy secret 的虚拟映射）。

`tokens.yaml` 格式（`data/runtime/auth/tokens.yaml`，由上述 API 维护，一般不需要手工编辑）：

```yaml
tokens:
  - label: desktop-main          # 唯一，人类可读，^[a-z0-9-]{1,32}$
    hash: "sha256:9f2a…"         # sha256(token) 十六进制，不存明文
    scopes: ["profile:desktop"]  # profile:* 或显式 scope 列表，可混用
    created_at: "2026-07-03T12:00:00+08:00"
    expires_at: null             # 可选，ISO 8601
    disabled: false
```

Token 明文格式：`emt_` + `secrets.token_urlsafe(32)`，由 `POST /auth/tokens`（或 rotate）
服务端生成，`emt_` 前缀便于日志清洗时正则识别和肉眼辨认。

## P4 现状（六类持有者签发进度）

生产 `data/runtime/auth/tokens.yaml` 已建好六条记录（`GET /auth/tokens` 可核实 label/scopes，
不含明文）：

| label | profile | 发给谁 | 客户端代码 | 实际部署状态 |
|---|---|---|---|---|
| `desktop-main` | desktop | 桌面客户端（Emerald-client Tauri） | ✅ 已接入（401/403/429 语义、`cargo test` 41/41） | 待你确认本机正在跑的桌面客户端已配置为新 token |
| `sensor-service` | sensor | 手机 sensor-service | ✅ 已接入（同仓 Emerald-client/sensor-service） | 待你确认本机 sensor-service 已配置为新 token |
| `mobile-main` | mobile | 手机端（yexuan_memery 安卓） | ✅ 已接入（`_extractError` 401/403/429、后台轮询 403 停止重试） | 待你在手机 App 设置里填入新 token |
| `esp32-device` | device | ESP32 具身硬件（`firmware/presence-device`） | ✅ 已接入（`secrets.h` 已改为新 token，2026-07-04） | **待重新烧录**——板子目前跑的还是烧录时的旧 legacy secret 固件 |
| `watch-main` | watch | Watch | 无本仓代码（推测是 iOS Shortcuts 直连 `/watch/event`） | 待你在 Shortcut 里把 Bearer 值换成新 token |
| `admin-panel` | panel | 管理面板网页 | 无需改代码（`localStorage` 通用 Bearer 透传） | 待你登录面板时改填新 token |

明文只在创建/轮换时的终端输出里出现过一次，本仓库任何文件都不包含明文（`esp32-device`
的值例外——它写在 gitignored 的 `firmware/presence-device/include/secrets.h` 里，因为
那正是这个文件存在的目的：本地设备凭据）。若丢失，`POST /auth/tokens/{label}/rotate`
换新值即可（旧值同时失效，不影响其他持有者）。

**legacy secret 尚未轮换**。客户端代码侧五类（除 Watch 外）都已具备读取 scoped token 的
能力，但这不等于*运行中*的实例已经切换——桌面/sensor-service/mobile 需要你在各自配置里
把值换成上表的新 token，ESP32 需要重新烧录，Watch 需要改 Shortcut。**全部六个都确认真的
在用新 token 之后**，才轮换 legacy secret 的值（机制不变，只换值），P4 才算收尾；
在那之前轮换会立刻锁死所有仍用旧值的设备。

## 审计文件位置

- `data/runtime/auth/audit.jsonl`：每行一条 JSON，字段 `ts` / `event` / `label` / `path` / `ip`。
  记录事件：`token_created` / `token_rotated` / `token_deleted`、`auth_failed`（401）、
  `scope_denied`（403）、`meta_mode_danger`（`PATCH /system/meta-mode` 切到 danger，含操作者
  label）。写失败 fail-open（`core.safe_write.safe_append_jsonl` 内部已 try/except），绝不
  阻塞请求；**不记 token 值**，401 场景下 `label` 固定为 `"invalid"`。
- 401 限速：`admin/auth.py` 进程内存计数（`_failure_times` / `_blocked_until`），按来源 IP，
  60s 窗口内 ≥10 次认证失败 → 该 IP 后续所有认证请求直接 429，持续 300s；重启进程清零。
  只统计 401（无效/缺失 token），不统计 403（scope 不足但 token 本身有效，不算攻击信号）。
  测试隔离：`admin.auth.reset_rate_limit_state_for_test()`，`tests/conftest.py` 已挂 autouse
  fixture 在每个测试前后清零——这是模块级进程状态，忘记重置会导致某个测试文件里密集的
  no-token/wrong-token 用例把后面无关测试一起拖进 429。

## 守卫测试

`tests/test_sec_auth2_scopes.py`：scope 展开、registry 加载/热重载/过滤/create+rotate+delete、
`resolve_token` 语义、`require_scopes` 的 401/403/429 边界、`authenticate_ws` 的 scope 参数化、
全量 `APIRoute` default-deny 扫描（新增 router 忘记声明 scope → CI 失败）、真实
`admin_server.app` 上的 scope 语义验证、`/auth/tokens` 全生命周期（创建→用新 token 访问对应
scope 端点→rotate 后旧值失效→delete 后 401）、限速阈值触发与 403 不计入限速窗口、
`/auth/whoami`（零 scope 依赖，任意有效 token）、`/auth/profiles`、`PATCH /auth/tokens/{label}`
（disable 后 401、enable 恢复、legacy-admin 422）。

`tests/test_sec_ws1_auth.py`：WS token 提取（仅 header，拒绝 query）、access log 不泄漏
token 值等 SEC-WS-1 契约，已随 `authenticate_ws` 签名变化同步更新。
