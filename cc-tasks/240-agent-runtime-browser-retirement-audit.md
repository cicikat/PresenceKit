# Brief 240：浏览器任务迁移后的后端退役审计与单一控制面

> 状态：`implemented`
> 优先级：`critical`
> 范围：`Emerald-presence` 后端
> 前置：Brief 238、239；浏览器任务已迁入后端 admin 面板并完成一次真实 Chromium 验收
> 推荐模型：GPT-5.5 或 GPT-5.6-sol（需要同时审计路由、鉴权、测试与文档）

## 背景

浏览器任务目前有两套可见路径：

- admin 控制面：`/settings/agent-runtime-browser` 及其 `tasks*` 子路由；
- 旧 owner-bridge/观测路径：`/observability/agent-runtime-browser`、`/agent-runtime-browser/*`。

迁移完成后不能凭“新页面能用”直接宣称旧路径是幽灵代码，也不能无限期保留两套写入口。必须用仓内调用者、权限边界和公开接口契约做出退役或兼容结论。

## 任务

1. 建立路由调用矩阵：逐项搜索后端、桌面端、测试、脚本和文档对上述旧/新路径的引用，并记录调用方、所需 scope、是否产生副作用。
2. 对旧写路由做明确决策：
   - 若仍由 Tauri owner bridge 或其他已发布客户端使用，保留为兼容接口，补充版本/退役条件、调用方测试和 `docs/three-repo-interface-catalog.md` 说明；
   - 若无真实调用者，删除旧写路由、无用 schema、专属测试和仅服务旧路径的文档；不得用宽泛白名单掩盖删除。
3. 保留观测 GET 仅当它仍是 admin 只读观测的必要兼容入口；否则合并到 settings 观测实现并删除重复序列化逻辑。无论保留与否，都必须保证不返回 URL、query、cookie、token、profile 路径、页面正文或原始 params。
4. 检查任务创建、确认、运行是否存在第二条绕过 request fingerprint、owner/char 绑定或 `waiting_confirm` 闸门的路径；任何发现都要删除或接入同一服务函数。
5. 检查临时验收配置、fixture、profile、receipt 是否被写入受版本控制路径；清理仓内无调用者的临时脚本和死测试。不得删除生产 `data/` 或用户记忆文件。
6. 同步更新 `docs/feature-control-surface.md`、`docs/three-repo-interface-catalog.md`、`docs/known-issues.md` 和 OpenAPI 相关断言，使“后端 admin 才是配置/提交入口”的事实与保留的兼容接口一致。

## 硬验收

以下任一项不满足即不通过：

1. `rg` 调用矩阵覆盖所有旧/新路由；每个保留接口都有明确调用者和 scope，每个删除接口都没有未迁移调用者。
2. `pytest -n auto -q tests/test_settings_browser.py tests/test_agent_runtime_browser.py tests/test_agent_runtime_task_manager.py tests/test_admin_ui_route_coverage.py` 零失败、零错误；新增回归测试覆盖：非 allowlist、跨域 redirect、请求字段替换、重复 confirm/run、Dream/remote/disabled/unavailable fail-closed。
3. 使用隔离配置启动临时后端和本地 fixture：admin 创建 `read_page` 必须到 `succeeded`；高风险 `post` 必须先为 `waiting_confirm`，确认后只能执行创建时的同一 fingerprint；发布动作不得被自动执行。
4. 通过 admin 浏览器硬刷新确认：allowlist、worker health、任务状态和错误码可见；凭据、profile、绝对路径、完整 URL/query、页面正文不可见。
5. 关闭临时配置后，生产配置和 `data/` 下记忆文件的内容哈希不变；临时 fixture/profile/receipt 全部位于仓库外或 ignored 临时目录。
6. `git diff --check` 通过；`git status --short` 只包含本工单相关文件；提交一个独立 commit，message 必须包含 `Brief 240`。

## 交付物

- 路由调用矩阵和退役/兼容决策记录；
- 删除后的死代码、测试和文档，或保留兼容接口的理由与退役条件；
- 自动化测试结果、浏览器硬刷新截图/日志摘要（脱敏）；
- 生产记忆文件哈希前后对比；
- 独立 commit。

## 不做

- 不新增通用网页代理能力；
- 不把客户端重新变成浏览器任务配置入口；
- 不修改用户记忆内容、迁移生产记忆或执行真实发布/支付/不可逆操作；
- 不把“发布任务暂停等人工确认”改成自动通过。

## 本次验收记录（2026-09-06）

- 路由矩阵：见 `docs/agent-runtime-browser-route-matrix.md`。Emerald-client Brief 72 当前
  diff 已删除旧 Tauri browser commands，后端旧 `/agent-runtime-browser/*` 与
  `/observability/agent-runtime-browser` 无真实 source 调用者，已删除并同步 OpenAPI、
  `docs/api-reference.md`、三仓总账和 `docs/known-issues.md`。
- 统一控制：admin confirm 先校验 immutable `browser-request.v1` fingerprint，再调用
  `confirm_and_run_task()`；请求字段替换不会改变 waiting 状态。高风险 `confirmed=true`
  也不能跳过 `waiting_confirm`。
- 自动化：
  `pytest -n auto -q tests/test_settings_browser.py tests/test_agent_runtime_browser.py
  tests/test_agent_runtime_task_manager.py tests/test_admin_ui_route_coverage.py` -> `26 passed`；
  `py_compile`、`git diff --check` 通过。
- 临时实例：`127.0.0.1:18742` admin + `127.0.0.1:18743` fixture，配置和 receipt 均位于
  ignored `.tmp/brief240-acceptance`；Chromium 截图为
  `.tmp/brief240-acceptance/admin-hard-refresh-final.png`。旧观测路由实测 `404`，admin
  页面显示 allowlist/worker/task 状态和人工确认按钮。
- 生产只读对比：`config.yaml`、`data/memory`、`data/user_identity` 共 1 个文件的聚合
  SHA-256 前后均为 `2070B0657EBB2C4A57D46A7441093D39EB7E75AF232BF2ECA58DF45F258BBD77`。
- 未执行真实发布、支付或不可逆动作；临时 worker/profile/fixture 不进入 Git。
