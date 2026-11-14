# Browser + Visual Workbench 314 交付记录

## 施工前差异表

审计基线：后端 `a9e5155`、桌面 `ad05fbb`。本表区分代码存在、接线与真实设备验收。

| 能力 | 已实现 | 已接线 | 开关可用 | 已实测 | 缺口 |
|---|---|---|---|---|---|
| 旧 BrowserWorker / PlaywrightAdapter | 单动作任务 | admin + browser_automation | 既有 browser 控制面 | 既有回归 | 无持久桌面会话、截图/VLM；remote_server 禁止本机执行 |
| Windows 文件工作区 | grant/revision/epoch/nonce/lease/ACK | Path C + Tauri polling | 后端、桌面授权面 | 合成协议/Rust 回归 | 不承担浏览器渲染 |
| Task / Work Session | 持久任务、取消、TTL、回执 | 文件执行链 | 既有设置 | 既有回归 | 浏览器须复用，不另造生命周期 |
| 视觉连接 | screen 路由及 fallback | 屏幕观察/多媒体 | 图像连接管理 | 既有回归 | 场景观察 schema 不等于网页视觉评审 |
| Browser Workbench | 无 | 无 | 无 | 无 | 隔离 sidecar、受限 preview、持久会话、截图传输、视觉评审、故障闭环 |

## 验收原则

仅使用合成工作区与合成认证。生产聊天、角色历史与私人文件不作为测试资产。截图与 DOM 是不可信资料，不能提供作者证明或改变授权。只读委托禁止修改文件；页面打开、截图、视觉分析、保存回读及刷新分别提供证据。远程服务器既有本机浏览器拒绝边界保持有效。

## 删除候选

后续可单独评估退役旧单动作 `browser_automation` 的重复适配层；本单保留兼容入口，未执行删除。

## 实现与边界（2026-11-15）

后端新增交互式 browser adapter，复用文件传输、Task Manager 与 Work Session；Path C 可开始、操作、读取快照/console、截图、视觉评审、查询和取消同一任务。会话固定 inspect，不允许写文件。相同角色/设备/工作区可并行一项文件任务，文件回执关联 browser task/session；实际修改仍由原文件执行器保存、回读、verify，再刷新预览。打开、操作、截图、视觉评审与文件保存分别报告证据。

Windows 使用打包的 Playwright 1.58.0 / Chromium 和 PyInstaller 6.19.0 onedir worker；缺失产物不广告能力。原生执行器经 grant 读取最多40个、合计2MiB的显式文本资源，worker只收文本副本。预览使用全拦截虚拟loopback origin，无监听端口，拒绝外网、WS/WebRTC、frame、表单、下载和弹窗。独立context不使用个人profile。固定可信React/ReactDOM/Three runtime及esbuild stdin转译支持受限React/Vite源码；不执行项目scripts、配置、插件、安装命令或任意npm依赖，不提供Vite server/HMR。

JPEG最多384KiB，40,000字节分块，校验task/seq/hash/尺寸；后端最多12张、600秒内存保存，撤权/过期失效。管理页通过state.read鉴权、no-store读取JPEG，可刷新、展开复核并回收blob URL。图像会发送到现有screen视觉连接及允许的fallback；不承诺自动遮盖用户页面中的私密内容。DOM、图像、console与视觉观察均不写角色记忆或普通日志。

协议细节见 [设备合同](device-file-agent-contract.md#isolated-desktop-browser-preview-314)。没有新增开关、scope或手机协议；后端总闸、桌面授权和screen连接继续负责有效状态。旧BrowserWorker的remote_server限制保留。

## 已执行证据

| 验证层 | 实际入口与结果 | 能证明的范围 |
|---|---|---|
| 后端定向 | Python3.12 `pytest tests/test_browser_workbench.py tests/test_device_file_intents.py tests/test_device_file_agent.py tests/test_device_file_discovery.py tests/test_tool_incident_312.py tests/test_tool_schema_portable.py tests/test_image_presets.py -vv --tb=short`：78 passed | dispatcher、挂载HTTP、只读意图、视觉主备/无路由/错误/fenced JSON、授权失效；桌面和视觉网络为边界替身 |
| 核心smoke | Python3.12 `pytest -m smoke -q --tb=short`：241 passed，8200 deselected | 核心既有链无已发现回归 |
| 新增故障回归 | Python3.12 `pytest tests/test_browser_workbench.py -q --tb=short`：15 passed | 包括断连、撤权、epoch改变、丢ACK超时和shutdown；已下发click保持outcome_unknown，拒绝晚ACK/重放；不等于跨进程重启测试 |
| 打包Chromium | 桌面 `python scripts/check-browser-sidecar.py`：PASS | 真实HTML交互、首屏/全页、stale ref、ReactTSX状态变化、Three/WebGL像素、console/pageerror/404/CSP脱敏、WebRTC禁用、刷新/关闭 |
| Windows原生 | 桌面 `cargo test --locked --lib --features browser-workbench-acceptance device_file_agent -- --nocapture`：8 passed，113 filtered out | 真正Rust Runtime→grant→打包worker/Chromium，保存/回读/verify→刷新→新截图；撤销grant后检查实际worker/driver/Chromium进程树退出 |
| 管理面浏览器 | `python scripts/check_browser_workbench_ui.py --chromium <打包Chromium路径>`：PASS | production fragment/core/relationship/i18n，真实点击刷新、鉴权JPEG解码390px、重复HTTP、404清图、空态，无pageerror；HTTP为合成替身，未覆盖完整登录 |
| 真实视觉链 | `python scripts/check_browser_workbench_online.py`，合成broker→打包Chromium→真实配置screen视觉API：PASS | 相同task/session两档截图进入primary/general，结构化观察识别重叠与低对比；不等于完整Tauri应用或生产角色委托 |
| 后端全量 | Windows / Python3.12 `pytest -n auto -q --tb=short`：8445 passed、13 subtests passed，318.49秒 | 最终工作区全量；TEMP/TMP使用同盘隔离目录，MCP代理测试先加载SDK再替换网络构造器，保留业务断言 |
| 桌面前端与构建 | 637个前端测试、TypeScript、Vite build、`cargo check --locked`：PASS | Vite仍有既有大chunk提示；不等于完整Tauri窗口验收 |
| 桌面当前head CI | [run 43651463665](https://github.com/cicikat/PresenceKit-desktop/actions/runs/43651463665)：frontend、macOS Rust、Windows Rust均成功 | Windows包含打包worker和真实Chromium验收；对应提交 `e3db161d98b4b2d6a7202c58a52b85b05d610a23` |
| 后端实现提交CI | [Tests 43652477701](https://github.com/cicikat/PresenceKit/actions/runs/43652477701)与[offline 43652477692](https://github.com/cicikat/PresenceKit/actions/runs/43652477692)成功 | 对应实现提交 `c867d03add4eb8678bd834ee220cbbd992eb7642`；含Python3.10/3.12 smoke、双版本全量、串行隔离、mutation与浏览器 |
| 三仓固定合同 | [protocol 43652492390](https://github.com/cicikat/PresenceKit/actions/runs/43652492390)：后端provider、桌面consumer、手机consumer均成功 | 后端 `c867d03add4eb8678bd834ee220cbbd992eb7642` / 桌面 `e3db161d98b4b2d6a7202c58a52b85b05d610a23` / 手机 `43232e283e9c3649dbf19e7798d90b3fb013c4db`；其他组合需独立证据 |

打包worker证据哈希（JPEG内容仅短期保留，不入仓）：

| 画面 | SHA-256 | 字节数 |
|---|---|---:|
| HTML viewport | `c61b0ef130b623fdaa3772f441723d482b7a31b4338589c316e8569c26d62399` | 12574 |
| HTML full-page | `59413057f2725ed6a8cb76b48050186de0f3c571c5a56b32c724f107ed23174b` | 23089 |
| React/TSX | `28e3eb758120aa0456c86cdcbaf263ae8919d2199339e2427fae831bc8325206` | 3803 |
| Three/WebGL | `6586134c24a319afabfb239fd55c2ed25f1fb013f24402a59e8cbf284f4fc8fc` | 6104 |
| 真实VLM desktop 1440×900 | `954b3ffc43a1c1eda1c93fb03206db59b5801ffed410b6eec9778c9208038e11` | 28860 |
| 真实VLM mobile 390×844 | `d0858c47f710a8e6f70e2e4499b7ce2312cc54336ee9ffdcf332033d0b943575` | 21933 |

真实视觉验收使用task `83ebf69b8a744601aef240ab1631d623`、session `586b08b4c6574e688fb584911b9e4ee7`。两档均 `vision_status=reviewed`，connection `general`、route role `primary`。此前一次desktop评审漏报低对比，业务断言失败；没有放宽断言，改用明确的36px低对比按钮及重叠面板后两档通过。模型可能漏检，结论不保证穷尽缺陷。

## 未完成与交付状态

- 完整Tauri窗口→远程HTTP→Chromium→真实VLM同链、物理断网/崩溃及后端跨进程重启未验收。当前分层证据不能写成正式Windows端到端完成。
- Fixture B的实际修复后VLM复评、Fixture D的Canvas专门VLM评价尚缺；已有保存回读/刷新/hash变化及Canvas像素证据不能替代它们。
- 原生响应有20秒上限、会话TTL600秒。桌面撤权与在途native调用串行，不能保证调用中即时中断；任务取消/重启由下一次poll移除browser_tasks并释放进程。
- 实现提交：后端 `c867d03add4eb8678bd834ee220cbbd992eb7642`，[PR #16](https://github.com/cicikat/PresenceKit/pull/16)；桌面 `e3db161d98b4b2d6a7202c58a52b85b05d610a23`，[PR #7](https://github.com/cicikat/PresenceKit-desktop/pull/7)。上表证据仅适用于所列提交。交付记录后续提交的当前head复验、最终三仓组合、合并及部署状态以这两个PR的最终交付记录为准；未附成功运行的组合均为 `not_verified`。

## 部署与回滚

先部署含required operation_intent与browser broker的后端，再安装随包worker的Windows桌面；按桌面脚本构建sidecar后才打包Tauri。没有worker时保持能力不可用。macOS不广告browser，文件功能继续原合同。回滚先桌面后后端，避免新桌面接不提供必填意图的旧后端。部署核对提交、服务与health；禁止用生产私聊做开发验收，不默认备份。
