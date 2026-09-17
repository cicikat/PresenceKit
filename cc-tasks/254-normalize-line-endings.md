# 工单 254：仓库换行符统一为 LF（`.bat` 除外）

日期：2026-09-17。先写工单再施工。这是一次**独立的换行归一化**，不夹带功能改动。

## 背景（查仓库后）

当前本地 `core.autocrlf=false`、`core.eol=lf`，但**没有** `.gitattributes` / `.editorconfig`。历史 blob 不统一，agent 用编辑器或整文件写出时会把混用文件一次性收成某一种换行，Git 把 `\r\n` ↔ `\n` 当成整行改动，功能 diff 被淹没。`AGENTS.md` 因此禁止「顺手转换行」——那是在**没有归一化提交**时的防护，不是永久不要统一。

2026-09-17 对 `git ls-files` 文本文件扫描（跳过含 NUL / 常见二进制扩展名）：

| 类别 | 数量 |
|---|---|
| 已跟踪文件 | 1422 |
| 纯 LF | 1308 |
| 纯 CRLF | 54 |
| LF/CRLF 混用 | 49 |
| 无换行 | 10 |

需要动的主要是 **54 + 49 = 103** 个已跟踪文本文件。按扩展名：`.py` 74、`.md` 16、`.json` 5、`.bat` 5，其余少量 yaml / 无扩展名。按目录：`core/` 53、`docs/` 13、`tests/` 9、`admin/` 8，其余零散。

混用样本包括 `core/tool_dispatcher.py`、`docs/tools.md`、`core/memory/fixation_pipeline.py`、`admin/routers/dream.py` 等大文件——正是 agent 返工过的那类。

`.bat` 在 Windows `cmd.exe` 下需要 CRLF，不能收成 LF。

工作区此刻除本单外只有 `cc-tasks/250-admin-prompt-followups.md` 删除和未跟踪的 `cc-tasks/9.17审计结果.md`，**不纳入本单**。

## 总原则

1. **文本默认 LF。** 已跟踪源码、文档、yaml/json、测试、workflow、firmware 文本一律 LF。
2. **`.bat` / `.cmd` 保留 CRLF。** 用 `.gitattributes` 钉死，不靠本机 `autocrlf`。
3. **只改换行，不改内容。** 不格式化、不删行尾空格、不强行补文件末换行、不碰 BOM、不转编码。
4. **一次独立提交。** `.gitattributes` 与工作区/索引 renormalize 必须同一次提交，否则 index 会显示「整文件改过」却说不清。
5. **不改 `core.autocrlf`。** 仓库真相是 `.gitattributes`，本机继续 `false`，避免 Windows 再把 LF 展开成 CRLF。
6. **不扫 `data/`、`userdata/`、忽略文件、二进制。** 只处理 `git ls-files` 里的文本。
7. **归一化之后改规则。** `AGENTS.md` 从「禁止整文件转换」改成「仓库已是 LF；功能提交不得再引入混用 / CRLF（`.bat` 除外）」。

## 不做

- 不改功能代码、不顺手修 lint、不重排 import。
- 不改 Git 历史（不 filter-branch / BFG）。
- 不把全局或本地 `core.autocrlf` 改成 `true` / `input`。
- 不归一化 `data/`、`userdata/`、`.venv/`、构建产物。
- 不把 `.bat` 收成 LF。
- 不把本单和 250 / 9.17 审计文件混进同一提交。

---

## 工单 1 — 钉死策略：`.gitattributes` + `.editorconfig`

### 要做

- [x] 新增 `.gitattributes`：
  - `* text=auto eol=lf`（文本检出/提交为 LF）
  - `*.bat text eol=crlf`、`*.cmd text eol=crlf`
  - 常见二进制显式 `binary`（图片、压缩包、字体、音频等），避免被 `text=auto` 误判
- [x] 新增 `.editorconfig`（给编辑器，不替代 git 属性）：
  - `root = true`
  - `[*]`：`end_of_line = lf`，`charset = utf-8`
  - `[*.{bat,cmd}]`：`end_of_line = crlf`
  - **不**在本单规定 indent / max_line_length，避免变成格式化工单
- [x] 不修改本机或仓库推荐的 `core.autocrlf`（保持 `false`）

### 验收

- 两个文件本身是 LF。
- `.bat` 规则比 `* eol=lf` 更具体，Git 会按最长/最后匹配给 `.bat` CRLF。

### 不做

- 不在本单加 pre-commit 框架或新 CI workflow 文件（守卫放工单 3，尽量复用 pytest）。

---

## 工单 2 — 归一化已跟踪文本（与工单 1 同一提交）

### 要做

- [x] 用字节级脚本处理 `git ls-files` 文本：
  - 跳过 NUL、常见二进制扩展名、读失败路径
  - 非 `.bat`/`.cmd`：`\r\n` → `\n`，残留 `\r` → `\n`；仅当字节变化才写回
  - `.bat`/`.cmd`：统一成 CRLF（若已是纯 CRLF 则不写）
  - 用 `write_bytes`，禁止 `Path.write_text`（Windows 会按 `os.linesep` 再展开）
- [x] `git add --renormalize .`，让 index 与 `.gitattributes` 对齐
- [x] 提交前核对：
  - `git diff --stat` 与 `git diff --ignore-cr-at-eol --stat`：内容 diff 应接近空；有内容行变化则停下来查，不提交
  - 不暂存 `cc-tasks/250-admin-prompt-followups.md` 的删除、不添加 `cc-tasks/9.17审计结果.md`

### 验收

- 再跑同一扫描：混用 = 0；非 bat 的已跟踪文本无 CRLF；5 个 `.bat` 仍为纯 CRLF。
- `git diff --ignore-cr-at-eol` 对归一化文件无实质内容 diff。

### 不做

- 不 `git checkout` 还原别人的工作区。
- 不补文件末尾换行、不 strip 行尾空白。

---

## 工单 3 — 防回归 + 改 agent 规则

依赖工单 2 已经是 LF 仓库。

### 要做

- [x] 增加轻量守卫测试（pytest，只扫 `git ls-files` 文本）：
  - 非 `*.bat` / `*.cmd` 不得含 `\r`
  - `*.bat` / `*.cmd` 不得混用（允许纯 CRLF）
  - 非 git 工作副本时 skip，不把 `data/` 扫进来
- [x] 改 `AGENTS.md`「Windows 换行噪音」：
  - 写明仓库文本 LF、`.bat` CRLF，真相在 `.gitattributes`
  - 功能提交仍禁止「顺手整文件转换行当格式化」
  - 提交前若 `git diff --stat` 与 `--ignore-cr-at-eol --stat` 差很多 → 先停，说明本文件在归一化之后不该再出现
  - 删除「也不要为此改 `core.autocrlf`」里容易被读成「永远不要统一仓库」的含义；改为「不要改 autocrlf 来代替 gitattributes」
- [x] `docs/dev-environment.md` 补一句：换行以 `.gitattributes` 为准；`git diff --check` 的换行警告仍不等于功能 diff 错误，但新的混用应视为本守卫失败

### 验收

- 新测试在当前工作区通过。
- agent 再改已是 LF 的文件时，只要不引入 `\r`，不会再出现上千行假 diff。

### 不做

- 不把该测试塞进 GitHub smoke 子集（全量 `full-pytest` 会跑到即可；本测试应秒级结束）。
- 不改三仓客户端仓库。

---

## 提交

- 工单 1+2+3 合成 **一次** commit：策略文件、字节归一化、守卫测试、文档规则必须一起生效。
- message 说明这是换行归一化、内容不变；`Co-authored-by: Codex <codex@openai.com>`。
- 之后 `git blame -w` 可忽略本提交的空白/换行噪音。

## 风险

- 这 103 个文件在 `git log -p` / 无 `-w` 的 blame 里会显示整文件重写。这是一次性代价，比继续混用更可控。
- 若施工瞬间有其他 agent 改同一批文件，停手，不整文件 checkout。
