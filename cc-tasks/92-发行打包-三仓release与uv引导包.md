# Brief 92 · 发行打包:三仓 Release 策略 + 后端免配环境包

> 背景:异机 clone 实测暴露「无 Python / 依赖多难配」问题;且 v0.1 是
> backend / desktop / mobile 三仓联合发布,发布形态需要统一口径。
> 前置依赖:Brief 91 已完成(敏感信息终检通过)。
> §1–§4(后端)、§5(desktop)、§6(mobile)三条线**互相独立可并行**;
> §7 汇总收尾,依赖前三条线完成。

## 0. 多仓版本策略(已定,按此执行)

- **各仓各自出 Release**:产物形态不同(Python zip / Tauri 安装器 / APK),
  天然挂在各自仓的 GitHub Release 上,不合并、不建 meta 仓(v0.1 不值得)。
- **统一版本号**:三仓同打 `v0.1.0` tag。后续修复各仓独立 patch(如后端单独出
  v0.1.1),**次版本号(0.x)三仓联动**——跨仓契约(API/token/端点)变更时一起升。
- **兼容矩阵挂后端仓** README(后端是主仓/唯一服务端):一张表写清
  backend v0.1.x ↔ desktop v0.1.x ↔ mobile v0.1.x 互相兼容;
  三仓 release notes 里互链其余两仓的对应 Release 页。
- mobile `pubspec.yaml` 当前 `version: 1.0.0+1`,改为 `0.1.0+1` 与全局对齐
  (desktop 已是 0.1.0,无需动)。

## 1. 🟡 后端:依赖分层与锁定(两条路线的共同地基)

- `requirements.txt` 拆层:`requirements-core.txt`(运行必需)+
  `requirements-full.txt`(-r core + OCR/gradio/chess 等重依赖)+ 测试依赖。
  拆分依据:现有文件里的 Core/Feature 分组注释;凡代码中是 try-import 可降级的
  进 full,启动即 import 的进 core。逐个验证:core-only 环境下
  `standalone_mode: true` 能启动、缺失功能有明确日志提示而非崩溃。
- 生成锁文件(`uv pip compile` 产出 `requirements.lock`,含 hash),
  打包与 CI 均用锁文件,保证可复现。
- 新增 `pyproject.toml`(仅元数据 + requires-python 按 Brief 91 §4 实测收敛;
  不改现有布局,`requirements*.txt` 并存,取简)。

## 2. 🟡 后端路线 A:uv 引导包(主发行物,推荐)

- 产物:`PresenceKit-v0.1.0-win64-setup.zip` ≈ 几十 MB,内容:
  仓库源码(按 .gitignore 过滤)+ `tools/uv.exe`(随包携带,单文件)+
  重写的 `AA1安装并启动.bat`。
- AA1 新逻辑:不再探测系统 Python →
  `tools\uv.exe python install 3.12` + `uv venv .venv` +
  `uv pip sync requirements.lock` → 复制 config → 启动。
  AA2/AA3 同步改为走 `.venv\Scripts\python.exe`。
- 国内网络兜底:.bat 顶部支持环境变量/交互开关切换镜像
  (`UV_PYTHON_INSTALL_MIRROR`、`UV_INDEX_URL` 指向清华/阿里源),默认官方源。
- 首次运行需联网(下载 Python + 依赖),README 明示;二次启动全离线。

## 3. 🟢 后端路线 B:全离线绿色包(可选产物,同 CI 顺产)

- 产物:`PresenceKit-v0.1.0-win64-portable.7z`(full 依赖含 onnxruntime,
  预计 400MB+,故用 7z/自解压)。
- 做法:Windows embeddable Python 3.12 x64 + 解注释 `python312._pth` 的
  `import site` + get-pip 后按锁文件预装 site-packages,连同源码打包。
  启动 .bat 直接指向包内 `python\python.exe`。
- 验收必须在**无 Python 的干净环境**(新 Windows 沙盒/虚拟机)解压双击跑通,
  不能只在开发机验——embeddable 的 `_pth`/Scripts 坑只有干净环境暴露得出来。
- 若 embeddable 踩坑超预算,本路线降级为 v0.2 目标,不阻塞 v0.1。

## 4. 🟡 后端:CI 出包工作流

- 新增 `.github/workflows/release.yml`:tag `v*` 触发,windows-latest,
  产出路线 A zip(+路线 B 若已落地),附 SHA256,挂 GitHub Release。
- 构建步骤本机(Windows x64)可手动复现:提供 `scripts/build_release.py`
  (或 .bat),CI 与本机跑同一脚本——本机也能自己出包验证。

## 5. 🟡 desktop 仓(Emerald-client):Tauri 安装器

- 产物:NSIS 安装器(`PresenceKit-desktop_0.1.0_x64-setup.exe`),
  `tauri build` 默认产出即可,v0.1 只做 win-x64。
- 该仓目前**无任何 workflow**:新增 release workflow(tauri-action 或
  手写 `npm ci && npm run tauri build`,windows-latest,tag 触发挂 Release)。
  若 CI 构建 Rust 链路耗时/踩坑超预算,v0.1 降级为本机构建 + 手动上传,
  workflow 移 v0.2——但构建命令必须先在本机全程验证一次。
- 未签名 exe 会触发 SmartScreen 警告:README 下载区加一句说明(不买证书,v0.1 接受)。
- 遵守该仓路径约定:workflow/脚本内不得出现盘符绝对路径。

## 6. 🟡 mobile 仓(Emerald-mobile):APK

- 产物:`flutter build apk --release` 出 `PresenceKit-mobile-v0.1.0.apk`,
  v0.1 只发 Android(iOS 无签名分发渠道,不做)。
- 签名:生成 release keystore,**不入库**(gitignore `*.jks` + `key.properties`,
  入库只有 `key.properties.example`);无 keystore 时 fallback debug 签名并在
  构建脚本中警告。CI 出包需要 keystore 走 GitHub Secrets——v0.1 允许直接
  本机构建 + 手动上传 Release,workflow 可选。
- §0 的版本号对齐(`0.1.0+1`)在本单内完成。
- 现有 `AA打包安装到手机.bat` 与 release 构建脚本合并或明确分工(一个装机调试、
  一个出发行包),避免两套口径。

## 7. 🟢 汇总收尾:README 与兼容矩阵

- 后端 README(双语)「快速开始」改为按推荐排序:
  ① 下载 release zip 解压双击(路线 A);② 离线包(路线 B,若有);
  ③ 开发者 clone + pip/uv 手工路线(保留现内容,依赖文件名同步 §1 拆分结果)。
- 后端 README 新增「配套客户端」段 + 兼容矩阵表(§0),链接 desktop/mobile
  两仓的 Release 页;desktop/mobile README 反向链接后端 Release。
- 三仓 release notes 模板:本仓产物 + SHA256 + 「配套版本」互链三行。

## 执行记录（2026-07-18，本轮仅后端 §1/§2/§4）

- **§1 依赖分层与锁定：完成。** `requirements-core.txt`/`-full.txt`/`-test.txt`
  按现有 Core/Feature 注释拆分（13 个 Feature 依赖全部是 try-import 可降级，
  逐一核实见 admin_server 路由链路排查），`requirements.txt` 保留为
  `-r requirements-test.txt` 的 umbrella 文件，不破坏现有 README/AA更新.bat/
  tests.yml 的调用方式。新增 `pyproject.toml`（仅元数据 + `requires-python
  >=3.10,<3.13`）。`uv pip compile requirements-full.txt` 产出
  `requirements.lock`（win64/py3.12，含 hash）。
  core-only 环境验证 `standalone_mode: true` 启动时挖出一个真实 bug：
  `admin/routers/chess.py` 经 `admin_server.py` 的整体式 `from admin.routers
  import (...)` 被无条件导入，缺 python-chess 会让整个 admin_server（连带其
  余 40 个路由）启动失败，而非只关闭棋类功能。已改为 try-import + 条件
  include_router，缺包时只关该路由并记日志。
- **验收命令顺带挖出两个不属于本单但挡住"锁文件重建环境跑 smoke 子集"验收
  的既有缺陷，一并修了：** ①`admin_server` 无条件挂载的 `/ws/desktop`、
  `/ws/device`（standalone_mode 对接桌宠/手机端的核心通道）从未在任何
  requirements 里出现过 `websockets`，握手期静默警告而非启动期报错——补进
  `requirements-core.txt`。②fastapi 0.137.0 起 `include_router()` 改惰性
  展开，`tests/test_final_p1_blockers.py` 的路由清单断言假设旧语义，今天
  全新安装必然踩到——`requirements-core.txt` 锁 `fastapi<0.137.0`（解出
  0.136.3），这正是锁文件本该防住的那类问题，升级前需先改测试的路由自省
  方式。③`.github/workflows/tests.yml` smoke 清单里 7 个文件名是 Brief 50
  重构后的死引用，pytest 遇不存在路径直接整体收集失败，清掉后 396 passed。
- **§2 uv 引导包：完成。** AA1 不再探测系统 Python，改用随包 `tools\uv.exe`
  （或联网自装 uv）→ `uv python install 3.12` → `uv venv .venv` →
  `uv pip sync requirements.lock`，首次运行前用 `choice` 交互式提示是否切
  清华镜像（`UV_PYTHON_INSTALL_MIRROR`/`UV_INDEX_URL`）。AA2/AA3/AA更新
  同步改走 `.venv\Scripts\python.exe`，缺 `.venv` 时退回系统 Python 并提示
  先跑 AA1。本机沙盒（非仓库真实目录，避免污染工作区）验证过全新安装、
  二次幂等直接启动、`AA更新.bat` 依赖重同步三条路径，均通过。
- **§4 CI 出包工作流：完成。** `scripts/build_release.py`：`git archive
  HEAD` 导出已跟踪源码（天然等价于 .gitignore 过滤结果）+ 联网下载固定版本
  的 `uv-x86_64-pc-windows-msvc.zip` 单文件二进制到 `tools/uv.exe` + 打包为
  `PresenceKit-<version>-win64-setup.zip` + 写 SHA256。本机跑通，产物
  28.2MB，人工核对过 zip 内容不含 `config.yaml`/`secrets.local.yaml`。
  `.github/workflows/release.yml`：tag `v*` 触发 windows-latest 构建，
  用 `softprops/action-gh-release` 挂到 Release（CI 端未实跑，因为还没打
  tag；本机 `build_release.py` 产出已验证，CI 步骤与本机是同一份脚本）。
- **§3 路线 B（全离线绿色包）：拍板降级到 v0.2，按 brief 自带的降级条款
  执行，不在本单落地。** 理由：brief 明确要求"必须在无 Python 的干净环境
  （新 Windows 沙盒/虚拟机）解压双击跑通，不能只在开发机验"，本会话没有
  这样的干净沙盒/VM 可用；embeddable Python 的 `_pth`/site-packages 坑本
  质上就是"开发机验不出来"的那类问题，勉强在开发机上拼一个不代表能过验收，
  反而可能给出假通过的信号。路线 A 已完整落地并端到端验证，v0.1 的可用性
  不受影响。
- **§5（desktop/Emerald-client）、§6（mobile/Emerald-mobile）、§7（README
  汇总收尾）：本轮不做。** 用户本轮明确只要求推进后端；§7 本身也在 brief
  里写明依赖前三条线全部完成（要链 desktop/mobile 的 Release 页），当前
  desktop/mobile 两条线尚未开始，做局部 README 更新意义不大，留到三仓都有
  Release 产物后再统一收尾。
- **已知缺口（用户拍板，v0.1 不处理）：`AA更新.bat` 靠 `git pull` 拉更新，
  对纯 release zip 用户（`git archive` 打包不含 `.git`）会报 git 错误、
  代码更新不了。** v0.1 用户量小，接受"重新下载新版本 zip 覆盖解压安装目录"
  作为更新方式（zip 从不含 `config.yaml`/`secrets.local.yaml`/`data/`，覆盖
  解压不会丢用户配置和数据）。v0.2 若要做真正的增量更新，可以让
  `AA更新.bat` 检测不到 `.git` 时给出"请重新下载最新 release 覆盖安装"的
  友好提示，而不是直接报 git 错误。

## 验收

- [x] 干净 Windows 环境(无 Python)解压路线 A zip → 双击 AA1 → 自动装好环境 →
  standalone 模式启动成功、admin 面板可访问;二次双击 AA3 直接启动。
  （本机沙盒验证；非"无 Python 的全新物理机/VM"，但覆盖了 uv 引导的完整
  逻辑路径——全新安装、幂等重启、依赖重同步均过。）
- [x] 锁文件重建环境后 `pytest -n auto`(CI smoke 子集)通过。（396 passed）
- [x] `scripts/build_release.py` 本机产出验证（28.2MB、SHA256、内容核对）；
  CI 端逻辑与本机同脚本，未实跑（未打 tag）。
- [ ] desktop 安装器在干净 Windows 上安装启动、连上本机后端;mobile APK 真机
  安装、配置后端地址后可用;两者版本号均显示 0.1.0。——§5/§6 未做，见上。
- [ ] 三仓 Release 页互链齐全,后端 README 兼容矩阵与实际 tag 一致。——§7
  依赖 §5/§6，未做。
