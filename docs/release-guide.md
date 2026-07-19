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

**产物**：`PresenceKit-<version>-win64-setup.zip`（uv 引导包，免配 Python
环境）+ 同名 `.sha256`。

1. 改版本号：`pyproject.toml` 里的 `version = "X.Y.Z"`。
2. 提交、push 到 `main`。
3. 打 tag 并推送，触发 `.github/workflows/release.yml`（`windows-latest`，
   跑 `scripts/build_release.py`，用 `softprops/action-gh-release` 自动
   创建 Release 并挂资产、生成 changelog）：

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
python scripts/build_release.py --version vX.Y.Z
# 产物在 dist/，人工核对 zip 里不含 config.yaml / secrets.local.yaml
```

**顺带检查**：同一次 push 会触发 `tests.yml`（Tests workflow）。这个
workflow 在 CI 环境里缺 `config.yaml`（本机专属配置，不入库）会有一批
用例失败，是已知的长期现象（和发布本身无关），不用因为它变红就卡住发布；
但如果失败清单里出现新的、和你这次改动直接相关的用例，要单独排查。

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

## 4. mobile（Emerald-client / PresenceKit-mobile）：本仓无 CI，手动构建 + 手动发布

**产物**：`PresenceKit-mobile-vX.Y.Z.apk` + `.sha256`。这仓目前没有
release workflow（Android 签名密钥需要走 GitHub Secrets，v0.1 阶段拍板
暂不上 CI，见下方签名说明），全流程本机走完：

1. 改版本号：`pubspec.yaml` 里的 `version: X.Y.Z+buildNumber`
   （`+` 后面是 Android versionCode，日常 patch 一般不用动，除非同一
   版本号内需要发第二个 build）。
2. 提交、push 到 `main`。
3. 本机构建：

   ```bash
   flutter pub get
   flutter build apk --release
   # 产物：build/app/outputs/flutter-apk/app-release.apk
   ```

4. **签名说明**：`android/key.properties` 不入库（`.gitignore` 已排除，
   仓库里只有 `key.properties.example`）。
   - 有正式 keystore：按 `key.properties.example` 在本机建一份
     `android/key.properties`（不要提交），构建脚本会自动检测并用正式签名。
   - 没有正式 keystore（目前状态）：Gradle 配置会 fallback 到 **debug 签名**，
     构建能过，但安装/更新时系统会提示签名信息，Release Notes 里要**明确
     写清楚**，别让用户以为是篡改包。
5. 打包资产 + 校验和，创建 Release（这仓没有 tag 触发的自动化，`gh release
   create` 是唯一入口）：

   ```bash
   cd build/app/outputs/flutter-apk
   cp app-release.apk PresenceKit-mobile-vX.Y.Z.apk
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
- （有已知限制/注意事项单独一行加粗标出，比如未签名 exe / debug 签名 apk）

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
