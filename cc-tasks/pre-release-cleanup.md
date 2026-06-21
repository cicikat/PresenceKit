# 工单：开源发布前清理 checklist

> 目标：让仓库 clone 即可启动、不泄露隐私、不夹带内部脚手架。
> 执行者按勾选清单逐条做。⚠️ = 发布阻断点（不做就无法用 / 会泄露）。
> 日期 2026-06-19。先看 `AGENTS.md`。

---

## A. config.example.yaml 收尾（已手改，剩余泄露/瑕疵）

文件：`config.example.yaml`（这是最终版，另一份 `config.yaml.example` 见 C 节删除）

- [ ] ⚠️ **L205** `owner_birthday: 04-24` 是真实生日 → 改占位 `owner_birthday: MM-DD`（或注释说明格式）。
- [ ] ⚠️ **L148** `data_prefix: "data/test_sandbox/20260619_205210"` 是测试沙盒路径 → 改为 `data_prefix: "data"`（使用者照抄会把生产数据写进 test 沙盒）。
- [ ] **L3** `llm.api_key: YOUR_VISION_API_KEY` 标签串了（这是 LLM key 不是 vision）→ 改 `YOUR_DEEPSEEK_API_KEY` 之类。
- [ ] **L94** `content/characters/角色id/voice/dault.wav` 拼写 → `default.wav`。
- [ ] （可选）**L213** `signatures: - 今天的光很好` 是个人化签名，按需改成中性示例或留注释。
- [ ] 复核：已无 `yexuan` / `叶瑄` / 真实 QQ / 邮箱残留（已确认 L36/37/84/128 仅为 localhost 端口与 smtp 标准值，保留）。

---

## B. ⚠️ default 角色最小可启动集（发布阻断点）

现状：`.gitignore` 结尾的裸 `characters/` 把整个目录屏蔽，`git ls-files characters/` 为空。
→ 全新 clone **没有任何角色卡**，`active_prompt_assets` 初始化时按 `character.default` 加载会
`FileNotFoundError`，程序起不来。

已有素材：
- `defaults/`（已 track）：`lorebook.yaml` / `jailbreak_entries.json` / `blacklist.yaml` / `relations.yaml` —— seed 齐，**但缺角色卡**。
- `examples/character_template.json`（已 track）：空白模板。
- `characters/default_author_notes.json`：已存在但被 `characters/` 屏蔽。

要做（二选一思路，推荐前者）：

- [ ] **方案一 · 放行最小集**：在 `.gitignore` 末尾用 `!` 负规则精确放行一套 default，例如：
  ```
  characters/
  !characters/default.json
  !characters/default_author_notes.json
  !characters/reality/
  !characters/reality/lorebooks/
  !characters/reality/lorebooks/base.yaml
  ```
  并新建一张**脱敏的 `characters/default.json`**（中性角色，不含 yexuan/叶瑄/亲密向内容），
  把 `config.example.yaml` 的 `character.default: 角色id` 对齐成 `default`。
- [ ] **方案二 · seed 复制**：把 default 角色卡放进 `defaults/`，首跑由 setup/启动脚本复制进 `characters/`。
- [ ] ⚠️ 确认私密角色**绝不放行**：`yexuan.json` / `yexuanJ-5412.json` / `hongcha.json` /
  `yexuan_author_notes.json` / `characters/dream_presets/` / `characters/reality/jailbreaks/` /
  含 NSFW 的 `dream_worlds/*`（审讯 / 触手巢穴 / abo / vampire 等）保持 ignore。

---

## C. 剔除内部开发脚手架（会随开源公开，对使用者无价值且夹带私路径/功能细节）

这些当前被 git 跟踪，clone 会一起公开。建议 `git rm`（git 历史仍保留），或挪到本地 ignore 目录。

- [ ] `cc-tasks/`（38 个工单，含 `D:\ai\...` 私路径、亲密向功能细节，**也包括本工单**）
- [ ] `codex_prompt_dream_exit_trigger.md` / `codex_prompt_prompt_leak_fix.md` / `codex_prompt_tier1_memory.md` / `codex_prompt_tier2_memory.md`
- [ ] `待办方案与排序.md`
- [ ] `HANDOFF.md`
- [ ] `data_registry_seed.txt`（确认非运行所需 seed，若是运行依赖则保留并改名说明）
- [ ] 决定 `docs/` 与 `docs/specs/` 是否公开：specs 是稳定设计文档可留；逐个过一遍有无私路径/隐私。
- [ ] **不要并进 `docs/specs/`**：cc-tasks 是一次性工单，性质 ≠ 设计文档。要留就 `docs/archive/`，否则直接删。

---

## D. 配置示例去重 + .gitignore 修正

- [ ] 删除冗余的 `config.yaml.example`（git 索引里仍跟踪），以 `config.example.yaml` 为唯一权威示例。
- [ ] `.gitignore` 注释写「模板已移至 `docs/templates/`」，但该目录不存在（真实模板在 `examples/character_template.json`）→ 改注释指向 `examples/`，避免误导贡献者。
- [ ] 复核 `.gitignore` 负规则（B 节）生效：`git check-ignore -v characters/default.json` 应显示被放行。

---

## E. README + 改名核对（你说的「更新一次 README」）

- [ ] `README.md` 与 `AA1先看说明书正式版README.md`：clone URL、仓库名是否已同步成改名后的新地址。
- [ ] `admin/admin_server.py` L26/L101 标题 `QQ-ST-Bot 管理面板` 按需改名（纯展示）。
- [ ] `AGENTS.md` / `CLAUDE.md` 中 `D:\ai\qq-st-bot` 路径字样按需更新（或公开版去掉绝对路径）。
- [ ] （内容提示）项目含成人向功能（buttplug / intimacy / 梦境），决定 README 是否加一句内容/年龄提示。

---

## F. data/ —— 无需处理（确认结论）

`data/` 整个目录已 `.gitignore`，全是运行时文件，**对开源零影响**。`data/_archive/`、`data/test_sandbox/`
等只是本地磁盘占用，想清随时清，与发布无关。

---

## G. 最后一步：全新环境验证

- [ ] 在干净 VM / 新目录 `git clone` 新仓库。
- [ ] 复制 `config.example.yaml` → `config.yaml`，填 key。
- [ ] 不手动放任何私密角色，直接 `python main.py`（或 standalone）确认**能起来、能对话**（验证 B 节 default 集生效）。
- [ ] `pytest` 跑通。
- [ ] 确认仓库里 grep 不到 `yexuan` / `叶瑄` / 真实 QQ / 生日 / 邮箱（排除已剔除目录）。

执行顺序建议：A → D → C → B → E →（commit）→ G。B 是阻断点，G 是终验。
