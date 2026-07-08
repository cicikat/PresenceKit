# Brief 33 · 安全 P0:admin 鉴权与绑定修复(今天就做)

> 来源:审计 §3.1(docs/critique-fable-20260707.md),裁定见 docs/critique-triage-20260708.md。
> 全部指控已核实:占位 secret 可作全权 token(`admin/auth.py:70` 无过滤 + `:88` 非空即比对)、
> `main.py:82` 仅提示不阻断、config.example 默认 `0.0.0.0`。

## 1. 修复项

### 1.1 占位/空 secret 永不作为合法 token

`admin/auth.py::get_admin_secret`:返回值 ∈ {`""`, `PLACEHOLDER_ADMIN_SECRET`}(从
`admin/token_registry.py:25` import,勿重复定义字面量)→ 一律返回 `""`。
`resolve_token` 的 `if secret and ...` 现有短路即自动失效,不用改比对逻辑。
env `YEXUAN_ADMIN_SECRET` 同样过占位检查。

### 1.2 启动阻断

`main.py:82` 附近:secret 为空/占位 **且** token registry 无任何记录 → `sys.exit(1)`,
错误信息指向 `python scripts/setup_auth.py`(对齐同文件 gating_shadow 的阻断先例)。
secret 为空/占位但 registry **有** token → 允许启动(1.1 已让占位失效,无风险),
log warning 一条即可。

### 1.3 默认绑定收紧

- `config.example.yaml`:`admin.host` 改 `127.0.0.1`,旁注"局域网/公网访问需显式改并配强口令"。
- 代码侧读取处:config 缺省值同步改 `127.0.0.1`(cc 找到 uvicorn/server 启动读 host 的位置)。
- **不**强改用户现有 config.yaml 的 host(桌宠/手机端可能依赖局域网访问);
  但 host 非 127.0.0.1 且 secret 长度 < 12 时启动打 **error 级**横幅警告(不阻断)。

### 1.4 泄漏处置(本次审计的次生问题)

- `docs/critique-fable-20260707.md` §3.1 中的真实 secret 明文 → 替换为 `<redacted>`。
- 提示用户轮换:本 brief 的 PR 描述里写明"合并后请修改 admin.secret_key 并重启,
  或运行 setup_auth.py 重新生成"。(cc 不代改用户口令。)
- 检查 `docs/private-content-manifest.md` 是否需要把 `docs/critique-*.md` 列入
  开源前审查清单(审计类文档天然容易引用真实配置)。

## 2. 测试

`tests/test_auth_p0.py`:

1. secret=占位 → resolve_token(占位) 返回 None;secret=真值 → 原行为不变(回归)。
2. env 占位同样失效;env 真值优先级不变。
3. 启动阻断:占位 + 空 registry → SystemExit;占位 + 有 token → 正常启动 + warning。
4. 弱口令横幅:host 非本地 + 短 secret → error 日志出现;host 本地 → 不出现。

## 3. 不做什么

- 不做口令强度策略/密码学升级(scoped token 体系是 Brief 21/22 已有资产,不动)。
- 不动 token registry 与 scope 表。
- 不改现网用户 config(除脱敏审计文档外,用户侧动作全部以提示形式交还用户)。
