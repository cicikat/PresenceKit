# Brief 115 · MCP 连接跨 task 生命周期崩溃 —— 止血已做，根治已落地（2026-07-24）

写于 2026-07-23。这次是茶茶在管理面板重载 MCP、以及角色直接调用 MCP 工具（`account`）
时两次把**整个进程**崩掉（不是单个请求 500，是 uvicorn 主循环被取消退出）排查后的记录。
止血已经落地并通过 `py_compile`，测试已同步更新。

**2026-07-24 更新**：§3 描述的根治方案已实现——`core/mcp_client.py` 里每个 server 的
连接现在由它专属的常驻 task（`_owner_loop`）独占持有和关闭，`init_mcp_servers`、
`disconnect_server`、`reload_server_from_config`、`sync_mcp_servers`、
`shutdown_mcp_servers` 全部改成只发信号（`_send_command` 把指令丢进队列、await 一个
Future 拿结果），真正的 `AsyncExitStack.aclose()`/重连永远在那个专属 task 自己的
上下文里执行，调用方可以是任意 task。`admin/routers/settings_mcp.py` 三个接口的
热更新能力已恢复（去掉了"需重启"文案），`tests/test_settings_mcp.py` 断言同步改回。
新增 `tests/test_mcp_client.py::TestOwnerTaskLifecycle`，用
`asyncio.current_task()` 在 connect/close 两端打点，断言跨 task 触发 reload/
disconnect 时实际的 open/close 仍然发生在同一个专属 task 里。
`_call_tool` 内部失败重连（`_reconnect_server`）按 §3 原计划维持原样未改（连接已经
坏掉时关闭它的风险显著更低，见该函数新增的 docstring 说明），是唯一未纳入本轮改造
的残余路径。

验收：`pytest -n auto tests/test_mcp_client.py tests/test_settings_mcp.py
tests/test_admin_mcp_ui.py` 26 项全过。真实环境验证（管理面板改 MCP 配置、角色调用
MCP 工具、连续两次重载，进程不退出）尚未做，留给下一次有真实 MCP server 可连时手动跑一遍。

---

## 1. 症状与真实根因（已用完整 traceback 确认）

管理面重载 MCP server 或角色调用 MCP 工具时，服务端整个退出，日志显示：

```
[mcp_client] server 'cedar_toy' 热重载失败: MCP server 'cedar_toy' 初始化失败: ...
asyncio.exceptions.CancelledError: Cancelled via cancel scope 19ce566ce10 by
  <Task pending name='Task-90' coro=<RequestResponseCycle.run_asgi() ...>>
```

traceback 的取消源头一路指到 `uvicorn.Server.serve() → main_loop() → asyncio.sleep(0.1)`
——也就是说，一个**普通 HTTP 请求的 task（Task-90）反向取消了 uvicorn 主循环自己的
cancel scope**，导致整个服务器进程退出，不只是这一个请求出错。

**根因**：`core/mcp_client.py` 里 `_ServerHandle.stack` 是一个 `AsyncExitStack`，在
`_connect_server()` 里被"进入"（open）；MCP 的 http transport（`streamablehttp_client`）
内部用 `anyio` 的 task group 撑连接读写。这个 stack 往往是在**服务启动时的 task**（`main.py`
的顶层协程）里打开的；但 `disconnect_server()` / `reload_server_from_config()` /
`sync_mcp_servers()` 是被**某次 HTTP 请求自己的 task** 调用的。anyio 的结构化并发规则
是：cancel scope 必须在打开它的同一个 task 里关闭；跨 task 调用 `stack.aclose()` 会让
anyio 尝试在错误的 task 上下文里取消这个 scope，观察到的实际效果是牵连取消了共享的
祖先 scope（这里恰好牵连到了 uvicorn 主循环）。

## 2. 本轮已做的止血（已落地，已过 `py_compile`，测试已同步）

**改动文件**：`core/mcp_client.py`、`admin/routers/settings_mcp.py`、`tests/test_settings_mcp.py`

1. `core/mcp_client.py`：`_connect_server` / `disconnect_server` / `test_server_config` /
   `_call_tool` 四处把 `except Exception` 加宽成 `except BaseException`，并把捕到的异常
   统一转成普通 `RuntimeError` 再上抛——这一步单独并不能解决"跨 task 取消祖先 scope"的
   问题（那是取消语义层面的，不是"漏抛异常"），但能保证**其余**因 CancelledError/
   BaseExceptionGroup 逃逸导致的漏网退出被堵上，是必要但不充分的一层。
2. `admin/routers/settings_mcp.py`：**真正止血的是这一步**——把
   `update_mcp_settings`（总开关）、`update_mcp_server`（单 server 更新）、
   `import_mcp_server`（导入）三个接口里"写配置后立即热重载/热同步"的调用全部去掉，
   改成只写 `config.yaml`，返回文案明确告知"需重启进程后生效"。这样管理面板的正常操作
   不会再触发跨 task 关闭 `AsyncExitStack` 这条崩溃路径。`test_server_config`（连接测试，
   探测用）不受影响——它自己在同一个请求 task 里打开又关闭，没有跨 task 问题，仍然安全。
3. `tests/test_settings_mcp.py`：同步更新三个断言，确认 `sync_mcp_servers` /
   `reload_server_from_config` 不再被这三个接口调用，返回体里带"重启"字样。

**现状**：管理面板改 MCP 配置=只落盘，角色实际连接的 MCP server 仍然只在**进程启动时**
建立（`main.py` 调 `init_mcp_servers()`）、**进程退出时**统一关闭
（`shutdown_mcp_servers()`，这两处都在同一个顶层 task 谱系里，不跨 task，是安全的）。
改完配置要重启一次进程才会用上新配置——这是本轮止血接受的代价。

## 3. 尚未做的根治（下一轮排期）

真正的修法是让每个 MCP server 的连接生命周期**只被同一个专属常驻 task 持有和关闭**，
请求方不再直接 `await handle.stack.aclose()`，而是发一个信号（`asyncio.Event` 或
`asyncio.Queue`）请这个专属 task 自己去关：

```
main.py 启动
  → 为每个 enabled 的 server 各起一个常驻 background task（不是请求 task）
    → task 内部：打开 AsyncExitStack + ClientSession，进入一个等待循环
    → 收到"reload"信号 → 在自己的 task 里 aclose() 旧 stack，重新连一次
    → 收到"shutdown"信号 → 在自己的 task 里 aclose()，task 退出
管理面板 PATCH /settings/mcp* → 只是把信号丢进对应 server 的队列，立即返回，
  不等待、不跨 task 直接碰 AsyncExitStack
```

工具调用（`_call_tool`）本身不涉及跨 task 关闭，理论上不受这条根因影响，本轮的
`except BaseException` 加固已经够用；根治的范围主要是 `disconnect_server` /
`reload_server_from_config` / `sync_mcp_servers` 这条线。

**验收要求**：
1. 新增测试模拟"在不同 task 里触发 reload"，确认不会抛出跨 scope 相关异常（用
   `anyio.from_thread` 或手动 `asyncio.create_task` 模拟即可，不需要真实网络）。
2. 恢复 `admin/routers/settings_mcp.py` 三个接口的热更新能力（去掉"需重启"文案），
   同步改回 `tests/test_settings_mcp.py` 的断言。
3. `pytest -n auto tests/test_mcp_client.py tests/test_settings_mcp.py tests/test_admin_mcp_ui.py`
   全过。
4. 真实环境验证：管理面板改 MCP 配置、角色调用 MCP 工具、连续两次重载，进程不退出。

## 4. 与本次连带发现，顺手记录

- `tool_loop.categories`（`config.yaml`）本轮加了 `mcp`，角色现在能"看见" MCP 类工具了
  （此前默认类目是 `info/desktop/memory`，从来没含 `mcp`，这是"角色说自己用不了 MCP"
  的另一半真根因，纯配置问题，和上面的崩溃是两件事）。
- 茶茶提到"24 个工具超过 20 上限"：查证是两处**纯建议性、不拦截**的 UI/日志提示
  （`core/pipeline.py` 里 tool loop 总暴露量 >20 打 warning；`admin/static/index.html`
  里测试单个 MCP server 时若发现 >20 个原始工具会提示"超过安全红线，请勾选最小白名单"）。
  cedar_toy 这个 server 上游确实有 24 个工具，但 `allow_tools` 白名单已经正确收窄到 4 个
  （list_games/get_guide/play/account），机制工作正常，和这次崩溃无关，不需要处理。
