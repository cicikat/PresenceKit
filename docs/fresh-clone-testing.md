# docs/fresh-clone-testing.md — 新用户开箱测试姿势

> 给人工测试者（例如验证 v0.1 开源前的「clone 后接上原数据」问题是否已修复）使用。
> 背景：开源前检查曾发现此问题；该一次性检查清单已归档，本文保留当前可执行的验证步骤。

## 为什么会「接上原数据」

E:\opensource-test 这类测试目录接上原有对话记录，根因不是单一 bug，而是三个独立机制叠加：

1. **端口复用**：desktop/mobile 默认连 `http://127.0.0.1:8080`。若原后端 `admin.auto_start: true`
   仍常驻 8080，新 clone 的前端启动后会直接连上**旧后端进程**，而不是新 clone 出来的那份。
2. **desktop 配置与 clone 目录无关**：`client.local.json` 的候选路径里有一条是编译期烧死的
   `CARGO_MANIFEST_DIR/../config/client.local.json` 绝对路径；同一份 dev build 换个盘运行仍会读到
   编译时那个仓库路径。另外 `sensor_config.json` / `app_config_dir()/client.local.json` 存在于
   `%APPDATA%\<tauri identifier>\`，是全局共享的，与 exe 在哪个目录无关。
3. **文件夹复制而非 `git clone`**：直接拷贝目录会把 `data/` 和 `config.yaml` 一起带走。

（v0.1 起 desktop 启动日志会打印实际命中的 client config 文件路径与 backendBase，后端启动日志会打印
data 根目录与 config.yaml 的绝对路径——出现连错数据时先看这两行日志。）

## 正确的测试步骤

1. **必须 `git clone`，不是文件夹复制。** 复制目录会带走 `data/` 与 `config.yaml`，测试没有意义。
2. **测试前确认旧后端已停**：
   ```powershell
   netstat -ano | findstr :8080
   ```
   如果有输出，先停掉那个进程（或换一个端口），否则新前端会连到旧后端上。
3. **desktop 不要直接复制 dev build 去测试。** 编译期路径是烧死的，复制到新位置运行仍会读旧仓库的
   `client.local.json`。两个选择二选一：
   - 在新位置（新 clone 出的 desktop 仓）重新 `npm run tauri build` / `npm run tauri dev`；
   - 或者删除 `%APPDATA%\com.presencekit.desktop\`（v0.1 改名前是 `%APPDATA%\com.emerald-client.app\`）
     后再启动，清掉全局共享的 `sensor_config.json` / `client.local.json` 残留。
4. 启动后端时确认日志里 data 根目录绝对路径确实指向新 clone 目录，不是旧目录。
5. 启动 desktop 时确认日志里 client config 命中路径与 backendBase 是新 clone 对应的值。

## 默认角色契约

干净 clone 没有 `config.yaml`；自动化测试若需要配置，应从公开的
`config.example.yaml` 复制一份临时配置，默认角色是 `default`。测试不得隐式依赖
开发机的 `character.default`、私有角色卡或 `data/` 资产。

涉及角色作用域的测试必须将同一个 `char_id` 显式传给写入端与读取端。若需要 traits、
作者注池等 authored 资产，应使用已跟踪的 `default` 资产，或在测试 fixture 内构造并注入
临时资产；不要把私有角色素材加入仓库或 CI。
