# Brief 91 · 开源 v0.1 扫尾:敏感信息终检 + fresh-clone 可跑性

> 背景:v0.1 对外发布前审计(20260718)发现工作区 tracked 文件仍有隐私泄漏、
> ignored-but-tracked 漏网、以及 fresh clone 按 README 走不通的硬伤。
> 本单与 Brief 92(打包)**可并行**,但本单 §1/§5 是发布前置,必须先完成。

## 1. 🔴 tracked 文件敏感信息清除

- `docs/archive/opensource-v0.1-checklist.md`:含真实 QQ 号 `1043484516`、
  旧 token `Emerald1231`、邮箱前缀 `chah69634`。处置:`git rm --cached`,
  gitignore 加 `docs/archive/opensource-v0.1-checklist.md`(与已有的
  `docs/opensource-v0.1-checklist.md` 规则同理——本体保留本地,不进版本库)。
- `docs/archive/interaction_issues_dedup.md:275`:含 `Emerald1231` 明文。
  改为「旧 admin token(已轮换)」措辞,不留原串。
- `AGENTS.md:214` 与 `DESIGN.md:163`:含 `C:\Users\10434\...` 本机路径。
  AGENTS.md 改为通用写法(如 `<Python安装目录>\Scripts\pytest.exe`);
  DESIGN.md 见 §2 直接 untrack。
- 全仓终检(验收命令,必须零命中,回归测试自引用常量除外):
  `git grep -IE "1043484516|Emerald1231|chah69634|sk-[A-Za-z0-9]{20}|C:\\\\Users|D:\\\\ai|D:/ai"`
  另跑一遍 8–11 位连续数字扫描,人工复核疑似 QQ 号/手机号。

## 2. 🟡 ignored-but-tracked 漏网清理

`git ls-files -i -c --exclude-standard` 当前命中三个,逐一处置:

- `DESIGN.md` → `git rm --cached`(gitignore 已有规则,本意就是不公开)。
- `tests/eval_set.json` → `git rm --cached`(同上)。
- `data/dream/scenarios/test_short.yaml` → 先 grep 确认测试是否引用:
  有引用则移到 `tests/fixtures/` 并改引用路径(`data/` 整体 ignore 的规则不破);
  无引用则 `git rm --cached`。

## 3. 🟡 fresh clone 可跑性修复

- `requirements.txt` 测试段补 `pytest-xdist>=3.5.0` 与 `pytest-testmon`
  (CLAUDE.md 强制 `pytest -n auto`,fresh clone 目前装完直接跑不了)。
- `start.bat`:硬编码 `D:\ai\Emerald-presence` + `python3`(Windows 无此命令)。
  已被 AA3启动.bat 取代 → 直接删除,README 若提及同步改。
- `.gitignore` 补 `MagicMock/`(pytest mock 落盘产物)与 `.pytest_cache/`。
- AA 系列 .bat 目前是 GBK 编码,非中文系统显示乱码。统一改为 UTF-8 + 首行
  `chcp 65001 >nul`,逐个在本机双击验证不乱码、流程能走通。

## 4. 🟡 Python 版本口径统一

- README 写 3.10+,CI 测 3.11,开发机跑 3.14——三个口径没对齐过。
- 在 3.10 与 3.13 各建一次干净 venv 装 `requirements.txt` 验证
  (重点看 `rapidocr-onnxruntime`/`onnxruntime`/`sqlite-vec` 有无轮子);
  按实测结果收敛 README 的版本区间,CI matrix 至少覆盖区间两端。
- 推荐随包/推荐安装版本统一定为 **3.12**(轮子覆盖最稳,与 Brief 92 打包一致)。

## 5. ⛔ 决策项:git 历史处置(需用户拍板,不可逆)

- 现状:`git log -S` 确认历史中 5+ 提交含 `Emerald1231`、真实 QQ 号;
  origin(cicikat/PresenceKit)已有约 420 个提交,本地 ahead 66。
- 选项 A(推荐,与 mobile 仓 v0.1 处置一致):orphan 单提交重开历史,
  force push,同步清理本地旧分支;§1/§2 完成后执行,一次解决工作区+历史。
- 选项 B:保留历史,跑 git-filter-repo 精准清洗敏感串后 force push(工作量更大,
  且 cc-tasks/docs 里的历史引用可能出现悬空)。
- **执行前停下来问用户选 A 还是 B,拿到答复再动。**

## 6. 🟢 收尾杂项

- `cc-tasks/` 现有 14 个 tracked 工单随 §1 的终检命令一并扫描;干净即保留公开
  (设计过程文档,有开源价值),扫出隐私则单文件处置。
- README 双语版「快速开始」与 AA 系列 .bat 流程对齐(现在 README 只写 pip 手工路线,
  没提 .bat;Brief 92 落地后再补 release 包下载路线)。

## 执行记录（2026-07-18）

- **§5 拍板：不清洗 git 历史（选项 A/B 均不执行）。** 理由：`Emerald1231` 是测试期旧
  token，早已轮换失效；真实 QQ 号与邮箱前缀此前已在多个公开场合暴露过，翻 100+ 个
  历史提交清洗的成本收益不成立。改为只处理"当前工作区/索引不再泄露"这一条底线：
  §1 tracked 文件敏感信息已清除或改写措辞（archive checklist untrack、
  `interaction_issues_dedup.md` 明文改脱敏措辞、`AGENTS.md` 本机路径通用化），
  §1 终检 grep 命令零命中（回归测试自引用常量除外）；`AGENTS.md` 新增规则 11，
  禁止未来再把真实密钥/QQ号/邮箱/本机绝对路径写进 tracked 文档或代码。
  origin 历史保留不动，不 force push。

## 验收

- §1 终检命令零命中;`git ls-files -i -c --exclude-standard` 输出为空。
- 干净目录 fresh clone → 按 README 步骤(装依赖→复制配置→setup_auth→
  `standalone_mode: true` 启动)全程无报错;`pytest -n auto`(smoke 子集)通过。
- CI 绿。
