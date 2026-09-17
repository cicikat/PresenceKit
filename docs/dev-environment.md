# docs/dev-environment.md — Windows Agent 开发与验证环境

> 给 Codex / Claude Code 等自动化 agent 使用。这里记录 Windows 沙箱中已经实际遇到的环境问题和可靠处理方式。
> 目标是避免把环境失败误判成代码失败，也避免验证过程污染生产数据或其他 agent 的并行改动。

---

## 一、开始前检查

本项目可能与 `Emerald-client`（前端仓库，与本仓同级目录）跨仓联动。仅对本次实际涉及的仓库检查状态，不因环境检查扩展任务范围。

```powershell
git status --short
git -c safe.directory=<Emerald-client 路径> -C <Emerald-client 路径> status --short
```

- 只改当前任务涉及的文件。
- 不回滚、不格式化、不清理其他 agent 或用户的改动。
- 验证后再次检查状态，确认没有遗留 `.tmp`、构建产物或意外文件。

---

## 二、Python 与 pytest

### `python` / `py` 不可用

Codex Windows 沙箱里可能出现：

- `python` 不在 `PATH`
- `py.exe` 存在，但运行时报 `No installed Python found!`

这不是项目代码失败。先检查：

```powershell
Get-Command python, py, pytest -ErrorAction SilentlyContinue | Select-Object Name,Source
```

运行项目 pytest 时，优先使用项目支持的 Python 3.10–3.12 环境（推荐 3.12），检查项目虚拟环境与可用解释器。下节 3.14 是本机遗留备用，不是受支持的标准验收环境。workspace dependency
discovery 返回的 bundled Python 可用于无需项目依赖的基础 Python 操作，但它可能没有安装 pytest；
出现 `No module named pytest` 时不能据此判定测试不可运行。
不要把某个用户名下的绝对 runtime 路径硬编码进项目脚本或文档。
也不要从工作区父目录递归搜索后随便使用其他项目附带的 `python.exe`；那些解释器的依赖集和
运行时约束不属于本项目，容易产生假失败或污染。

### 本机遗留备用 pytest（Codex / Claude Code）

仅无可用受支持环境时检查以下历史入口是否仍存在，不把它写成项目默认环境：

```powershell
& '<用户目录>\AppData\Local\Python\pythoncore-3.14-64\Scripts\pytest.exe' -n auto tests/test_stage* -q
& '<用户目录>\AppData\Local\Python\pythoncore-3.14-64\Scripts\pytest.exe' --testmon
```

它对应 Python 3.14，超出项目支持范围；依赖可能与 requirements 不一致，运行结果不能替代受支持环境验收。不要误用 WindowsApps 别名或失效的 PATH 残留。若实际执行因沙箱拒绝而失败，对同一条范围明确的命令申请必要权限后重跑。

### pytest 临时目录权限错误

pytest 默认使用用户 `%TEMP%`。在受限沙箱中可能报：

```text
PermissionError: [WinError 5] ... AppData\Local\Temp\pytest-of-...
```

将临时目录指向仓库内可写目录后重跑：

```powershell
$env:TEMP="$PWD\.tmp"
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
& '<用户目录>\AppData\Local\Python\pythoncore-3.14-64\Scripts\pytest.exe' -n auto -q
```

完成后只能清理确认位于仓库内的 `.tmp`。删除前必须校验解析后的绝对路径仍在仓库根目录下，禁止对未校验的计算路径递归删除。

### 测试结果判读

- 先跑任务相关测试，确认本次改动本身通过。
- 不例行追加全量测试。仅用户要求或影响面、相关失败表明必要时，按 `AGENTS.md` 的测试规则运行全量并记录结果。
- 完整套件失败不等于本任务失败。若失败来自已有并行改动、过期 fixture 或缺失测试资产，应明确记录，不能顺手修改无关模块。
- 若本次改动改变了合理前置条件，例如 desktop/system 工具新增 danger-mode 门控，应更新相关旧测试，让它显式建立该前置条件。

---

## 三、Emerald-client 验证

前端仓库（与本仓同级，克隆时按需调整路径）。

### Git dubious ownership

沙箱用户与仓库所有者不同，git 可能拒绝执行：

```text
fatal: detected dubious ownership in repository at '<Emerald-client 路径>'
```

使用单命令范围的安全目录参数：

```powershell
git -c safe.directory=<Emerald-client 路径> -C <Emerald-client 路径> status --short
```

不要擅自执行 `git config --global --add safe.directory ...`，避免永久扩大信任范围。

### 前端 build 的 Vite `.vite-temp` EPERM

在沙箱内运行：

```powershell
npm.cmd run build
```

可能出现：

```text
EPERM: operation not permitted, open '...\node_modules\.vite-temp\...'
```

这是 Vite 写临时文件被沙箱拦截，不是 TypeScript 或业务代码失败。处理顺序：

1. 可先运行 `npx.cmd tsc --noEmit`，单独确认 TypeScript。
2. 对原始 `npm.cmd run build` 请求必要权限后重跑，不能仅凭 `tsc` 通过就宣称生产 build 通过。
3. Rust/Tauri command 或 `src-tauri` 有改动时，另跑：

```powershell
cargo check
```

`cargo check` 也会写构建产物，沙箱拦截时按原命令申请权限后重跑。

### 本地 UI 目检

显著前端改动应尝试启动 `npm.cmd run dev -- --host 127.0.0.1` 并用内置浏览器验证。
Windows 沙箱中内置浏览器可能因 `CreateProcessAsUserW failed: 5` 无法连接。此时：

- 明确记录“浏览器目检未完成”，不能声称 UI 已目检。
- `npm run build`、`tsc --noEmit` 和 `cargo check` 只能证明编译层通过，不能替代视觉/交互验证。
- 启动的 dev server 通常会随命令会话结束；若仍监听端口，只停止本次启动且命令行明确属于
  `Emerald-client` / Vite 的进程，不得按模糊进程名批量终止。

---

## 四、PowerShell 与沙箱注意事项

- Windows 下优先用 `npm.cmd` / `npx.cmd`，避免 PowerShell 脚本执行策略干扰。
- 重要命令若因明确的沙箱写权限或进程权限失败，应对**同一条、范围明确的命令**申请权限后重跑。
- 不要通过改写到别的 shell、关闭安全检查或扩大全局配置来绕过沙箱。
- `Get-NetTCPConnection` / `Get-CimInstance` 在沙箱中可能报拒绝访问；需要识别或停止自己启动的服务时，申请范围明确的权限，并严格核对端口与命令行。
- 换行以仓库 `.gitattributes` 为准：文本 LF，`*.bat` / `*.cmd` CRLF；本机不要改 `core.autocrlf` 来代替它。
- `git diff --check` 仅在本次实际修改的仓库、文件上运行，换行符警告不等于功能 diff 错误。新的混用 / 非 bat 文件出现 CRLF 应视为 `tests/test_line_endings.py` 失败，不要当噪音忽略。

---

## 五、推荐验证清单

后端任务：

```text
1. git status --short
2. 任务相关 pytest
3. 仅必要时扩大测试范围；纯文档/指令改动无需 pytest
4. 必要时用 `<可用 Python>` -m py_compile 检查相关 Python 文件，不重复已充分验证的检查
5. git diff --check
6. 仅清理本任务创建且路径核对通过的临时产物
```

跨前端任务：

```text
1. 前后端两个仓库分别检查 dirty 状态
2. npx.cmd tsc --noEmit
3. npm.cmd run build
4. cargo check（涉及 Tauri/Rust 时）
5. 尝试本地 UI 目检；失败则如实记录原因
6. 两仓分别运行 git diff --check
```
