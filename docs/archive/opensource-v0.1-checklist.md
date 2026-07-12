# 开源 v0.1 前检查 — CC 执行清单

> 生成日期: 2026-07-05。三仓路径: 后端 `D:\ai\Emerald-presence`(→ PresenceKit)、
> 桌面端 `D:\ai\Emerald-client`(→ PresenceKit-desktop)、移动端 `D:\ai\yexuan_memery`(→ PresenceKit-mobile)。
> 按 P0 → P1 → P2 顺序执行,每项附验收标准。已拍板决定:前端 LICENSE 与后端一致(PolyForm NC 1.0.0);
> mobile 重开 git 历史,desktop 保留历史;README.md 英文为主 + README.zh-CN.md。

---

## 已排查结论(勿重复排查)

- `config.yaml` 从未进过后端 git 历史;`data/` 0 个文件被 track。✓ 安全
- 后端 LICENSE = PolyForm Noncommercial 1.0.0,已存在;两个前端仓无 LICENSE。
- 三仓 `git remote` 均仍指向 `chah69634-arch/Emerald-*` 旧地址。
- 旧 admin token `Emerald1231` 存在于 desktop 和 mobile 的 git **历史**中(工作区源码已清除);
  后端 docs 中仍有 3 处**过时引用**(见 P0-2)。
- mobile 工作区源码**现存**真实 QQ 号:`lib/pages/app_shell.dart:75` `_ownerUserId = '1043484516'`,另散布于 docs 与 test。
- 后端 `.gitignore` 忽略了 `characters/reality/`、`characters/*.json`、整个 `content/`
  → fresh clone 缺少运行必需的 authored 资产,production 模式不会从 `defaults/` 播种(只有 test 模式播种)。**这是新用户流程的最大阻断项。**
- desktop 的 Tauri identifier = `com.emerald-client.app`,productName = `tauri-app`。
- 后端本地 `config.yaml` 残留 `data_prefix: "data/test_sandbox/20260703_195354"`(测试沙盒写回未还原)。

---

## P0-1 「clone 后接上原数据」诊断与修复

E:\opensource-test 接上原数据不是单一 bug,有三个独立机制,全部需要处理:

**机制 A(最可能):端口复用。** desktop/mobile 默认连 `http://127.0.0.1:8080`。原后端
`admin.auto_start: true` 常驻 8080,新 clone 的前端启动后直接连上了**旧后端进程** = 旧数据。

**机制 B:desktop 配置与 clone 目录无关。** `src-tauri/src/client_config.rs` 的加载顺序:
1. `app_data_dir()/sensor_config.json`(= `%APPDATA%\com.emerald-client.app\`,全局共享,与 exe 在哪个盘无关)
2. `CARGO_MANIFEST_DIR/../config/client.local.json` — **编译期烧死的绝对路径**。在 D 盘编出的 dev build,复制到 E 盘运行仍会读 `D:\ai\Emerald-client\config\client.local.json`
3. `app_config_dir()/client.local.json`(同样全局共享)

**机制 C:若是文件夹复制而非 `git clone`**,`data/` 和 `config.yaml` 会被整个带过去。

### CC 执行项

- [x] 后端启动时 log 打印 data 根目录的**绝对路径**(`DataPaths._base.resolve()`)与 config.yaml 绝对路径。验收:启动日志一眼可见数据落在哪。（`main.py` `_init_modules()`）
- [x] desktop 启动时 log 打印实际命中的 client config 文件路径与 backendBase。验收:同上。（`client_config.rs` `load_client_config()`）
- [x] 清掉后端 `config.yaml` 残留的 `data_prefix` 行;检查 `run_test.py` 结束时是否还原 `data_prefix`,若无则补上(try/finally)。验收:跑一次 `python run_test.py` 后 config.yaml 无 test_sandbox 前缀残留。（本地 config.yaml 已清理；`run_test.py` 改为无论是否清理沙盒都会重置 `data_prefix`，且 `input()` 的 EOFError 也不再阻断重置）
- [x] Tauri 改名:`identifier` → `com.presencekit.desktop`,`productName` → `PresenceKit Desktop`。注意:appdata 目录会变,老用户(只有茶茶)需手动迁移或重新配置,在 PR 描述里注明。**⚠️ 提醒茶茶：下次启动 desktop 前删除 `%APPDATA%\com.emerald-client.app\` 或迁移到 `%APPDATA%\com.presencekit.desktop\`，否则会读不到旧配置。**
- [x] 给茶茶写一段「新用户流程正确测试姿势」加入 docs:必须 `git clone`(不是复制);测试前 `netstat -ano | findstr :8080` 确认原后端已停;desktop dev build 不可直接复制测试,需在新位置重新 build 或删除 `%APPDATA%\com.emerald-client.app`。→ 见 [docs/fresh-clone-testing.md](fresh-clone-testing.md)

---

## P0-2 敏感信息清理

### mobile(重开历史,已拍板)

顺序很重要:**先清工作区,再重开历史**,否则新初始提交仍带敏感串。

- [x] `lib/pages/app_shell.dart:75` 的 `_ownerUserId = '1043484516'` 改为从配置/设置页读取,默认值用占位符。(现为 `String _ownerUserId = ''`,通过 `_settingsStore.loadOwnerUserId()` 读取)
- [x] docs 全目录把 `1043484516` 替换为占位符。(复查:`grep -rn "1043484516" docs/` 无命中)
- [x] test 文件中的真实 QQ 号改用假 ID。(复查:`git grep -In "1043484516"` 只命中 `test/no_hardcoded_qq_number_test.dart` 自身的校验常量,属预期)
- [x] 移植后端的 `tests/test_no_hardcoded_qq_number.py` 思路,给 mobile 加同等测试。→ `test/no_hardcoded_qq_number_test.dart` 已存在,扫描 `lib/android/docs/test` 下文本文件防回归。
- [x] 重开历史并 force push 到 `cicikat/PresenceKit-mobile`。验收:`origin/main` = 单一 orphan 提交 `PresenceKit-mobile v0.1`(0ae5b7e),`spike/push-relay-ntfy` 远端分支已随 force push 一并清除(`[gone]`)。**⚠️ 本地遗留**:本地仍有旧分支 `main`(d75821f,完整旧历史)与 `spike/push-relay-ntfy` 未删除,`git log --all -S "Emerald1231"/"1043484516"` 在本地仍会命中这两个分支(它们从未推送,不影响远端干净,但建议清理,见下方待办)。
- [x] 全仓 grep 复查:`git grep -I "1043484516\|Emerald1231\|chah69634"` 在当前分支(v0.1/HEAD)工作区中干净(仅上述回归测试文件的自引用常量)。

### desktop(保留历史)

- [x] `docs/backend-integration.md:222` 的 `"user_id": "1043484516"` 替换为占位符。
- [x] 复查工作区无 `Emerald1231`(已确认,全仓 grep 干净)。历史中的旧 token 保留 — 前提验收见下。

### backend

- [x] **验证旧 token 已失效**:已核实并告知茶茶,茶茶选择自己处理,CC 不代为修改。
- [x] 更新过时文档:`docs/cross_project_interaction_flow.md:124-126` 与 `docs/interaction_issues_dedup.md:275` 仍描述"三端硬编码 Emerald1231",改为描述当前 token 注册表机制(`data/runtime/auth/tokens.yaml`)。
- [x] `.claude/.cache/edits_*.json` 从 git 移除并加入 .gitignore:`git rm -r --cached .claude/.cache`。
- [x] 人工审查以下已 track 文件是否宜公开:`.claude/settings.json` 含个人机器路径与用户名(`C:\Users\10434\...`、`D:\ai\qq-st-bot`、`D:\NapCat\config` 等),已 untrack 并加入 .gitignore;`.claude/RULES.md`、`.claude/Tail Integrity Act.md`、`.claude/hooks.buxuyong.md/` 内容无隐私,保留;desktop 的 `cc-tasks/`(10 个文件)已逐个 grep,无敏感串,保留。
- [x] 提醒茶茶(非 CC 任务):本地 `config.yaml` 中的 DeepSeek / GLM / SiliconFlow key 和 Gmail 应用密码虽未进 git,但曾在多个 AI 会话中被读取过,建议 v0.1 发布前顺手轮换一遍。(已在本轮对话中提醒;同时列入下方「需要茶茶手动做的事」#3)

---

## P0-3 新用户开箱资产(backend,最大阻断项)

**问题**:fresh clone 后以下运行必需资产全部不存在(被 .gitignore 忽略):
`characters/reality/lorebook.yaml`、`characters/reality/jailbreak_entries.json`、`characters/reality/lorebooks/`、
`content/`(整目录,含 activity_pool、traits、letter_samples、voice)、除 default.json 外的所有角色卡。
production 模式下 `data_paths.py` 只报 `authored asset missing` 不播种(播种逻辑仅 test 模式生效)。

### CC 执行项

- [x] 设计「公开默认角色」:确认 `characters/default.json` + `characters/default_author_notes.json` 内容无隐私、可直接开源使用(已是中性模板);缺的配套资产(traits/activity_pool)已在 `content/characters/default/` 补齐中性示例。
- [x] production 模式首启播种:`jailbreak_entries()`、`lorebook()` 在 production 下文件缺失时也从 `defaults/` 复制种子(而不是仅 log error)。test 模式行为不变(相关测试全绿)。
- [x] 确认 `defaults/` 目录本身被 track 且内容无隐私(逐文件过一遍,均为空壳/中性模板)。
- [x] `config.example.yaml` 的 `character.default` 已指向 `default`(核查时发现已是正确值,无需改动)。
- [x] **根因修复**:`.gitignore` 底部有一条与上方细粒度规则矛盾的整体 `content/` 忽略规则(2026-06-11 引入,与后续 2026-07-03 补充的细粒度规则从未协调过),导致 `content/` 整个目录(含刚补齐的 default 角色资产)实际从未被 track。已删除该矛盾规则,补充 `content/characters/default/*.yaml` 例外及 `content/characters/yexuan/{letter_samples,knowledge}/` 私有目录忽略。
- [x] **额外发现并修复两个新用户会立刻撞到的 bug**(通过下面的验收流程实测发现,不在原排查范围内):
  1. `config.example.yaml` 的 `scheduler.owner_id` 占位符 `'YOUR-qq号'` 含非 ASCII 字符,不满足 `safe_user_id()` 的 `^[A-Za-z0-9_-]+$` 校验,fresh clone 一启动调度器 period 触发器就抛 `ValueError` 崩溃(每次 tick 都报错)。改为空字符串 `''`,与代码里"未配置=空字符串"的既有约定一致。
  2. `core/activity_manager.py` 的模块级默认 `_DEFAULT_CHAR_ID = "yexuan"` 与 `core/data_paths.py` 由 config 驱动的 default 角色脱节;`core/scheduler/triggers/time_based.py` 的 `check_activity_switch()` 调用 `should_switch()`/`switch_activity()` 时忘记传 `char_id`(同文件其他触发器都正确传了),导致任何非 yexuan 的默认角色启动后动向状态一直挂在 yexuan 名下。已补上 `char_id=_active_char_id_or_none() or "yexuan"`。
- [x] **验收(核心)**:用 `git write-tree` + `git archive` 精确模拟"干净 clone"(只含当前已 stage 的可 track 文件),`cp config.example.yaml config.yaml` → 填占位 LLM key → `standalone_mode: true` → `python main.py`:启动日志全程无 `authored asset missing`;`POST /desktop/chat` 完整走完一轮(因用的是占位 LLM key,得到的是 pipeline 内置的优雅 fallback 回复,而不是本地崩溃——这是预期行为,真实 key 会走正常 LLM 回复)。

---

## P1-1 仓库改名收尾(三仓)

命名映射:`Emerald-presence → PresenceKit`,`Emerald-client → PresenceKit-desktop`,
`Emerald-mobile / yexuan_memery → PresenceKit-mobile`,GitHub owner `chah69634-arch → cicikat`。

- [x] 三仓 `git remote set-url origin`:
  - backend → `https://github.com/cicikat/PresenceKit.git` ✓
  - desktop → `https://github.com/cicikat/PresenceKit-desktop.git` ✓
  - mobile → `https://github.com/cicikat/PresenceKit-mobile.git`(此前已完成)✓
- [x] 全库替换旧名引用 —— **拍板缩小范围**(问过茶茶确认):三仓文件夹路径本身不改名(`D:\ai\Emerald-presence`、`D:\ai\Emerald-client`、`D:\ai\yexuan_memery` 维持原名),内部架构文档(AGENTS.md 正文细节、ARCHITECTURE.md、docs/backend-integration.md 等)里大量 `Emerald-client`/`Emerald-presence`/`Emerald-mobile` 其实是指代**磁盘上未改名的姊妹文件夹路径**,继续保留不算旧名残留,改了反而是错的。只替换「公开门面」:
  - backend:`README.md` clone 命令与 `cd` 目标目录、`AGENTS.md` 开头项目定位一句话、`chah69634-arch` 用户名(仅 README.md:105 一处)。✓ 已改
  - desktop:`README.md` 标题与首段一句话、`AGENTS.md` 标题与项目定位一句话。✓ 已改(desktop 全仓 grep `chah69634` 本就无命中)
  - mobile:`README.md` 标题、`AGENTS.md` 标题与项目定位一句话。✓ 已改(mobile 全仓 grep `chah69634` 本就无命中)
  - 内部文档里作为磁盘路径引用的 `Emerald-client/...`、`D:\ai\Emerald-presence\...` 等**不改**,保留现状。
- [x] 代码内配置键(如 config.yaml 的 `emerald_desktop`)v0.1 **保留键名不改**(改了是 breaking)——本轮未触碰任何配置键,符合要求;v0.2 更名计划待后续补充到文档。
- [x] 验收:三仓 `git grep -I "chah69634-arch"` 均为空(backend 已修复,desktop/mobile 本就干净)。

## P1-2 LICENSE(两前端)

- [x] 把后端 `LICENSE`(PolyForm Noncommercial 1.0.0)原样复制到 desktop 与 mobile 仓根目录。(`diff` 确认三份内容一致)
- [x] 三仓 README 末尾加 License 段,注明 PolyForm NC 1.0.0。backend 本就有;desktop/mobile 已补上。
- [ ] 验收:GitHub 仓库页侧栏能识别出 license。**需要 push 后在 GitHub 网页上确认,CC 本地无法验证,列入 P2 手动验收。**

## P1-3 README 重写(三仓,英文主 + 简体中文)

结构:`README.md` = English,`README.zh-CN.md` = 简体中文,两文件**第一行**均为语言切换:
`[English](README.md) | [简体中文](README.zh-CN.md)`。内容保持两语言同步。

当前后端 README 是早期 QQ 机器人版本,整体重写。各仓大纲:

**PresenceKit(backend)**
1. 一句话定位:有长期记忆、情绪状态、能主动联系你的 AI 陪伴后端(QQ 机器人只是可选通道之一)
2. 三仓关系图:PresenceKit(后端)↔ PresenceKit-desktop(Tauri 桌宠)↔ PresenceKit-mobile(Flutter)
3. 特性:五层记忆 / 情绪系统 / 12+ 层 prompt 架构 / 梦境系统 / 花园 / 主动调度(从现 README 提炼,删掉过时的 QQ-only 叙述)
4. Quickstart:clone → `pip install -r requirements.txt` → `cp config.example.yaml config.yaml` → 填 LLM key → `standalone_mode: true` → `python main.py`(依赖 P0-3 完成)
5. 可选:QQ/NapCat 接入、TTS、桌面端/移动端连接
6. 测试:`pytest` / `python run_test.py`;License 段

**PresenceKit-desktop**:定位(Tauri 桌宠 + 管理面板)、依赖后端运行中、`config/client.local.json` 配置方式(基于 `config/client.example.json`)、dev/build 命令、License。

**PresenceKit-mobile**:定位(Flutter 移动客户端)、连接方式(局域网 IP / adb reverse tcp:8080)、本机代理注意事项(NO_PROXY,见 CLAUDE.md)、build/安装脚本说明、License。

- [x] 三仓 README.md(英文)+ README.zh-CN.md(简体中文)已重写,首行均为语言切换互链。
  - backend:补充三仓关系图,特性章节沿用旧版内容(英文版翻译),quickstart 步骤修正为 `git clone https://github.com/cicikat/PresenceKit.git && cd PresenceKit`;移除了指向两个实际不存在的文件(`AA1先看说明书正式版README.md`、`AAWatch配置指南.md`,旧 README 死链)的引用。
  - desktop:补充"必须搭配运行中的后端"提示、连接后端章节(优先推荐应用内「偏好→连接设置」页,`config/client.local.json` 作为进阶/无界面选项)。
  - mobile:补充连接方式(`adb reverse`/局域网 IP)、`NO_PROXY` 测试注意事项、打包脚本说明。**旧 README 里的"Android 后台通知/通知闸门/行为 metadata"设计说明和"待捋逻辑"开放问题清单是有价值的内容,不是门面文案,已原样迁移到新建的 `docs/mobile/background-notification-design.md` 和 `docs/roadmap-notes.md`,新 README 里链接过去,没有被丢弃。**
- [ ] 验收:三仓 README.md + README.zh-CN.md 齐全,互链有效,quickstart 步骤与 P0-3 验收流程一字不差地可复现。**backend quickstart 与 P0-3 验收流程的命令逐条比对确认一致;desktop/mobile 因依赖真实设备/后端联调环境,CC 未能端到端重跑,建议茶茶实测一遍。**

---

## P2 发布前总验收

- [x] 后端 `pytest`(`-n auto`,单进程 `pytest -q` 交叉验证)。**发现并修复一处本轮引入的假阳性**:`tests/test_no_hardcoded_qq_number.py` 把本 checklist 文档自身(必然引用被清理的敏感字面量作为审计证据)当成了泄露,已加入该测试的 `_EXCLUDE_FILES`。**另发现两类与本轮改动无关的预置问题,未处理(超出本 checklist 范围)**:① `tests/test_r8e3_character_growth_readonly_contract.py` 单独跑绿,混在全量单进程跑法里会因 `_TOOL_REGISTRY` 全局状态被其他测试污染而报 `KeyError: get_growth`(测试间未隔离,非本轮改动导致);② `-n auto` 并行跑每次失败的用例都不一样(`test_user_pronoun.py`/`test_user_facts_smoke.py`/`test_scheduler_active_window.py` 等,与本轮只改了 README/docs/remote/一个测试排除名单的改动无关),像是并行 worker 间共享测试数据/fixture 引发的竞态,建议单独排查测试隔离,不在本次开源检查范围内。改过 tag_rules 则跑 `python tests/run_eval.py`(本轮未改 tag_rules,未跑)。
- [x] mobile `flutter test`(`NO_PROXY=localhost,127.0.0.1,::1`)。20 passed / 3 failed。3 个失败均在 `test/foreground_mobile_delivery_contract_test.dart`,报 `Found 0 widgets with icon "IconData(U+F0147)"`(UI 图标查找失败,widget 树/图标资源相关),**与本轮改动无关**(本轮 mobile 仅改了 README/AGENTS.md/LICENSE 等文档文件,未碰 `lib/`、`test/`);像是历史遗留的 UI 测试问题,建议后续单独排查,不阻塞开源发布(不涉及敏感信息或功能正确性)。
- [x] 敏感串终检(三仓,tracked 文件):`git grep -IE "1043484516|Emerald1231|chah69634|sk-[A-Za-z0-9]{20}|nfyb|D:/ai|D:\\\\ai|E:\\\\obsidian"`。
  - **🔴 发现一个不在原排查范围内的真实泄露,已处理**:`config.example.yaml:370` 的 `embedding.api_key` 写的是一个真实生效的 SiliconFlow key(`sk-eiczpk...`),自 2026-05-12 创建该文件起就在 git 历史里,不是占位符。茶茶已在 SiliconFlow 后台撤销旧 key 并生成新 key;CC 已把新 key `sk-orhhy...`(完整值见本地 `config.yaml:311`,未入库)写入本地 `config.yaml`,并把 `config.example.yaml` 对应行改为占位符 `sk-YOUR-EMBEDDING-API-KEY-HERE`。**注意:旧 key 仍会永久留在 git 历史里(本仓库策略是保留 desktop/backend 历史、不重开),已确认撤销失效即可,不需要再重开 backend 历史。**
  - backend 复查:`docs/interaction_issues_dedup.md:275` 命中 `Emerald1231` 属预期(P0-2 已处理的历史审计说明,状态标注"已解决",不是残留密钥)。
  - desktop 复查:`AGENTS.md:127` 命中 `D:/ai\Emerald-client` 属预期(P1-1 已拍板的磁盘路径引用,不改名,不是敏感信息)。
  - mobile 复查:仅命中 `test/no_hardcoded_qq_number_test.dart` 自身的回归测试常量,预期内。
  - `docs/opensource-v0.1-checklist.md` 本文档因记录审计过程会命中多条历史敏感串,按设计排除在此项终检范围外(理由同 `tests/test_no_hardcoded_qq_number.py` 的排除,见 P0-2 与该测试文件注释)。
- [ ] fresh clone E2E:停掉原后端、确认 8080 空闲后,在非 D 盘目录走一遍三仓 quickstart。**backend 部分已在 P0-3 用 `git write-tree`+`git archive` 精确模拟验证过;desktop/mobile 需要真实设备和图形界面(Tauri 窗口、Android 手机),CC 在当前环境跑不了,需要茶茶在 E:\opensource-test 或另一台机器上实测一遍三仓 quickstart 是否能从 fresh clone 走通。**
- [ ] GitHub 侧(茶茶手动):三仓 description/topics、About 互链。~~mobile force push 后确认远端历史干净、默认分支正确~~ ✅ CC 已用 `git ls-remote`/`git log` 核实三仓 `origin/main` 与本地 HEAD 完全一致,分支名均为 `main`。

## 需要茶茶手动做的事(CC 做不了)

1. GitHub 三仓的 description / topics / About 设置与互链。
2. ~~mobile 重开历史后的 force push 确认~~ ✅ 已完成(本次对话确认,远端历史干净)。
3. ~~SiliconFlow embedding key 轮换~~ ✅ 已完成(本次对话轮换)。DeepSeek / GLM / Gmail 应用密码未泄露,按茶茶判断不需要轮换。
4. 之后的虚拟机黑屏排查(另一个任务,届时远程操控)。
5. ~~mobile 本地旧分支清理~~ ✅ 已完成(`git branch -D main spike/push-relay-ntfy`,已确认删除)。
6. ~~三仓改动 push~~ ✅ 已完成。**过程中 CC 犯过一次错**:改 remote 时 shell 工作目录没切对,把 backend 的 origin 误设成了 `cicikat/PresenceKit-desktop.git`(desktop 的 origin 当时其实没被改到,还是旧的 `chah69634-arch/Emerald-client.git`)。茶茶推 backend 时触发了 `[rejected] fetch first` 报错,没有实际推送成功,没有产生任何错误数据;desktop 那次推送经核实其实已经正确落到 `cicikat/PresenceKit-desktop`(具体是茶茶用什么方式推的不确定,但远端内容已核实吻合本地)。CC 已重新用 `git -C <绝对路径>` 逐个修正三仓 remote 并验证 `HEAD == origin/main`,backend 随后正常 fast-forward 推送成功。三仓当前状态:本地 HEAD 与 origin/main 完全一致。
