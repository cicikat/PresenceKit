# 三仓 Release 制作指南

> 面向人：以后要独立打 release 的你（或接手的人）。覆盖 backend
> （本仓 PresenceKit）、desktop（PresenceKit-desktop / Emerald-client）、
> mobile（PresenceKit-mobile / Emerald-mobile）三仓的打包与上传全流程。
> 背景与历史决策见 `cc-tasks/` 归档中的「发行打包」工单（若已清理，
> 看本文档 + 各仓 workflow 文件即可，不依赖工单原文）。

## 0. 版本策略（先看这个，别打错版本号）

- **各仓各自出 Release**，不合并、不建 meta 仓：backend 出 zip，desktop 出
  安装器 exe，mobile 出 apk，三个产物形态不同，天然挂在各自仓的 GitHub
  Release 页。
- **Tag 统一命名 `vX.Y.Z`**，三仓同一轮发布打**相同的版本号**（例如这次
  三仓都是 `v0.1.2`），即使某一仓这轮其实没什么实质改动——保持版本号
  对齐比省一次空发布更重要，用户看兼容矩阵才不会懵。
- 后续如果只有一仓需要紧急修复，可以该仓单独出 patch（不强制三仓联动），
  但**涉及跨仓契约**（API / token 格式 / WS 协议等）变更时必须三仓一起升版本。
- 三仓 Release Notes 互相用链接指向对方对应版本的 Release 页
  （形如 `https://github.com/cicikat/PresenceKit-desktop/releases/tag/vX.Y.Z`）。

## 1. 前置：装 gh CLI 并鉴权

三仓都用 `gh release` 系列命令收尾（创建/编辑/上传资产），本机装一次即可：

```powershell
winget install --id GitHub.cli -e
```

鉴权推荐用 Personal Access Token（scopes 至少要 `repo` + `workflow`），
**不要把 token 提交进任何文件**，每次开新终端按需临时设置环境变量：

```bash
export GH_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
gh auth status   # 确认已登录且没打印出完整 token
```

用完这一轮发布后，如果 token 是临时贴在聊天/工单里给别人用的，**发布完
建议去 GitHub → Settings → Developer settings → Personal access tokens
里撤销/轮换**，避免长期暴露。

## 2. backend（本仓 PresenceKit）：CI 全自动，本机也能复现

**产物**：每个 tag 打三份 uv 引导包（免配 Python 环境）及各自 `.sha256`：

- `PresenceKit-<version>-win64-setup.zip`
- `PresenceKit-<version>-macos-arm64-setup.zip`
- `PresenceKit-<version>-macos-x64-setup.zip`

1. 改版本号：`pyproject.toml` 里的 `version = "X.Y.Z"`。
2. 提交、push 到 `main`。
3. 打 tag 并推送，触发 `.github/workflows/release.yml`（`windows-latest`，
   跑 `scripts/build_release.py --all-platforms`，下载各平台 uv 二进制后打包，
   用 `softprops/action-gh-release` 自动创建 Release 并挂资产、生成 changelog）：

   ```bash
   git tag -a vX.Y.Z -m "PresenceKit vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. CI 通常 1 分钟内跑完（纯 Python 打包，不编译）。用
   `gh run list -R cicikat/PresenceKit -L 1` 看状态。
5. Release 会自动发布（非 draft），标题默认是 tag 名，需要手动改成
   `PresenceKit vX.Y.Z` 并补一段人话说明（见 §5 模板）：

   ```bash
   gh release edit vX.Y.Z -R cicikat/PresenceKit \
     --title "PresenceKit vX.Y.Z" --notes-file notes.md
   ```

**本机复现构建**（不依赖 CI，验证产物用）：

```bash
python scripts/build_release.py --version vX.Y.Z --all-platforms
# 产物在 dist/，人工核对 zip 里不含 config.yaml / secrets.local.yaml
```

**顺带检查**：同一次 push 会触发 `tests.yml`（Tests workflow）。workflow 会从
`config.example.yaml` 生成临时公开配置，因此 smoke 子集在 Python 3.10/3.12
双矩阵都应为绿色；任何失败都应作为发布阻断信号排查，不能依赖开发机私有配置
或资产来解释。

### 日常更新：`AA更新.bat`

先停止服务再运行该脚本。Git clone 会沿用 `git pull`：发现未提交改动时展示
`git status --porcelain` 并要求明确输入 `Y` 才继续；它不会执行 reset、stash 或删除
`data/`、`config.yaml`、secrets，但同一文件的本地改动仍可能让 `git pull` 冲突。

解压的 Release 包会启动内置的版本更新器：从 GitHub Releases 列出可选版本（默认最新，也可选旧版本），
下载 zip 与 `.sha256` 后先校验，再只覆盖程序文件。`bundled/` 属于可替换的程序文件；`data/`、`userdata/`、`config.yaml`、
`secrets.local.yaml`、`.venv/` 和随包的 `tools/uv.exe` 始终保留；替换的程序文件会备份到
`_update_backup_<旧版本>/`，成功后只保留最近一份。跨版本降级会要求再次输入 `Y`，因为本地 data
格式可能不兼容。

发行包根目录的 `VERSION` 文件记录当前版本；构建脚本会自动写入它。更新完成后才同步锁定依赖；依赖
同步失败不会回滚已替换的程序文件，应先保留终端错误再处理网络或环境问题。GitHub 访问失败时，更新器
会明确提示并保留手动下载 release zip、仅覆盖程序文件的路径；它使用 `config.yaml` 里启用的代理配置。

### v1 更新基线与 preview 迁移

> **PresenceKit v1.0.0 is the first supported update baseline. Preview v0.x
> installations must migrate through backup and fresh installation.**

v0.x 是 preview / beta，不支持自动升级或数据连续性承诺。升级到 v1 时先停止服务，独立备份
`data/`、`userdata/`、`config.yaml` 和 `secrets.local.yaml`（如有），
然后在**新空目录**安装 v1 并复制这些受保护内容。不要复制旧程序资产或环境：
`characters/`、`content/`、`defaults/`、`examples/`、`core/`、`scripts/`、`.venv/`。

如需保留 legacy authored fallback，先运行只读的 C1.3 检查：

```bash
python scripts/authored_root_migration_dry_run.py --fail-on-diverged --fail-on-invalid
```

出现 `legacy-only`、`diverged`、`invalid`、`incomplete` 或 `unresolved` 时必须人工处理，
不得由 updater 猜测或覆盖。首次成功启动 v1 会创建
`data/layout_version.json`，记录 v1 baseline、数据 schema 与首次初始化的 v1 版本；它不是通用
migration engine。

从 v1.0.0 开始，解压版 updater 只接受 `VERSION >= v1.0.0`、支持范围内的
`data/layout_version.json` schema，以及 non-downgrade target。preview、未知安装、缺失/无效
marker、未来 schema 和 downgrade 都会在替换程序前 fail-loud，并提示手动迁移或恢复备份。
正常 v1+ 更新会创建 `_update_backup_<旧版本>/` 完整快照，替换程序与 `bundled/`，但不会覆盖
`data/`、`userdata/`、配置、secrets、`.venv/` 或 `tools/uv.exe`。

v1 不支持 downgrade。需恢复时停止服务并运行：

```bash
python scripts/update_release.py --restore-backup _update_backup_<旧版本>
```

请先保存升级后新增、且不希望丢失的文件。依赖同步失败不会自动回滚程序或依赖；修复后重试，或走上述
恢复路线。

## 3. desktop（Emerald-client / PresenceKit-desktop）：CI 出安装器，会挂 Draft

**产物**：`PresenceKit-desktop_X.Y.Z_x64-setup.exe`（NSIS 安装器）+
`checksums.txt`（SHA256）。

1. 三处版本号要同步改（漏一处 `cargo build` 时 Cargo.lock 会自动纠正
   `src-tauri/Cargo.toml` 那一份，但另外两处不会自动改，务必手动核对）：
   - `package.json` → `"version"`
   - `src-tauri/tauri.conf.json` → `"version"`
   - `src-tauri/Cargo.toml` → `[package] version`
   （`src-tauri/Cargo.lock` 里 `name = "tauri-app"` 那条会在下次
   `cargo check`/`cargo build`/CI 构建时自动跟着 Cargo.toml 同步，
   本机如果装了 pre-commit/hook 通常已经自动帮你同步，提交前 `git diff`
   确认一下即可。）
2. 提交、push 到 `main`。
3. 打 tag 并推送，触发 `.github/workflows/release.yml`
   （`tauri-apps/tauri-action`，`windows-latest`，编译 Rust + 前端，
   耗时明显更长，**参考约 9～10 分钟**）：

   ```bash
   git tag -a vX.Y.Z -m "PresenceKit-desktop vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. 轮询构建状态：`gh run list -R cicikat/PresenceKit-desktop -L 1`。
5. **注意：这个 workflow 里 `releaseDraft: true`**——构建成功后 Release
   会以**草稿**状态存在，默认对外不可见，必须手动发布：

   ```bash
   gh release edit vX.Y.Z -R cicikat/PresenceKit-desktop \
     --draft=false --title "PresenceKit-desktop vX.Y.Z" --notes-file notes.md
   ```

6. 未签名 exe 会触发 SmartScreen 警告，属预期（没买证书），Release Notes
   里提一句「更多信息 → 仍要运行」，与仓库 README 下载区文案保持一致。

**本机复现构建**（CI 踩坑或想先自测再打 tag 时用）：

```bash
npm ci
npm run tauri build
# 产物在 src-tauri/target/release/bundle/nsis/
```

## 4. mobile（Emerald-mobile / PresenceKit-mobile）：CI 校验 + 手动签名发布

**产物**：`PresenceKit-mobile-vX.Y.Z.apk` + `.sha256`。本仓有
`.github/workflows/ci.yml`：main/PR 上运行 `flutter pub get`、`flutter gen-l10n`、
`flutter analyze`、`flutter test`；它不构建、签名或发布 APK。没有 release workflow，
因此正式签名与 GitHub Release 仍须在受控发布环境手动完成：

1. 改版本号：`pubspec.yaml` 里的 `version: X.Y.Z+buildNumber`
   （`+` 后面是 Android versionCode，日常 patch 一般不用动，除非同一
   版本号内需要发第二个 build）。
2. 提交、push 到 `main`，等待 mobile CI 绿灯；CI 只是 Dart/Flutter 校验，不能证明
   Android 正式签名、安装升级或真机 relay 恢复。
3. 本机构建：

   ```bash
   flutter pub get
   flutter build apk --release --flavor prod
   # 产物：build/app/outputs/flutter-apk/app-prod-release.apk
   ```

4. **签名说明**：`android/key.properties` 不入库（`.gitignore` 已排除，
   仓库里只有 `key.properties.example`）。
   - 有正式 keystore：按 `key.properties.example` 在本机建一份
     `android/key.properties`（不要提交），构建脚本会自动检测并用正式签名。
   - 没有或不完整的正式 keystore：release task 会在打包前 **fail loud**，不会回退到
     debug 签名；debug APK 只来自明确的开发/调试任务，**不得作为 v1 正式分发 APK 发布**。
     先完成签名密钥保管、复现构建和升级签名验证。
5. 打包资产 + 校验和，创建 Release（这仓没有 tag 触发的自动化，`gh release
   create` 是唯一入口）：

   ```bash
   cd build/app/outputs/flutter-apk
   cp app-prod-release.apk PresenceKit-mobile-vX.Y.Z.apk
   sha256sum PresenceKit-mobile-vX.Y.Z.apk > PresenceKit-mobile-vX.Y.Z.apk.sha256

   git tag -a vX.Y.Z -m "PresenceKit-mobile vX.Y.Z"   # 仅做版本标记，不触发任何 CI
   git push origin vX.Y.Z

   gh release create vX.Y.Z \
     PresenceKit-mobile-vX.Y.Z.apk PresenceKit-mobile-vX.Y.Z.apk.sha256 \
     -R cicikat/PresenceKit-mobile \
     --title "PresenceKit-mobile vX.Y.Z" \
     --notes-file notes.md
   ```

6. iOS 不发：没有签名分发渠道（需要 Apple 开发者账号 + 证书），v0.1 阶段
   明确只发 Android。

## 5. Release Notes 模板（三仓通用）

三仓每次发布的说明建议按这个结构写（存成临时 `notes.md` 传给
`--notes-file`，不用长期维护在仓库里）：

```markdown
<一句话人话概括这轮改了什么，例如："用户友好改版：xxx">

- 要点 1
- 要点 2
- （有已知限制/注意事项单独一行加粗标出，比如未签名 exe；debug 签名 APK 不得作为 v1 发行资产）

配套版本：desktop [vX.Y.Z](.../PresenceKit-desktop/releases/tag/vX.Y.Z) ·
backend [vX.Y.Z](.../PresenceKit/releases/tag/vX.Y.Z) ·
mobile [vX.Y.Z](.../PresenceKit-mobile/releases/tag/vX.Y.Z)
```

三仓互链时把非自身的两个仓库链接填进去即可。backend 仓的 README 顶部
兼容矩阵表也要在三仓都发完后同步更新一次（对应旧 Brief 92 §7 的收尾项，
若该工单已归档清理，直接照着 README 现有格式改新一行）。

## 6. 三仓都发完之后

- 检查三个 Release 页链接是否都互相对上（不要出现 404 或指错版本号）。
- backend README 的兼容矩阵表补一行 `vX.Y.Z ↔ vX.Y.Z ↔ vX.Y.Z`。
- 如果这轮任何一仓的 workflow 文件本身有改动（比如调整了构建步骤），
  记得该仓单独 commit 说明原因，不要和版本号 bump 混在同一条 message 里
  （方便以后 `git log` 排查构建脚本变更史）。
