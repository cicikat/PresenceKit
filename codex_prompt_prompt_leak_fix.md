# Codex 任务：堵住生成端 prompt 的内部信息泄露面

## 背景

叶瑄会"看见"并复述本不该进入角色视野的系统内部概念（截图实例：他主动说"桌宠是你那边的一个界面""你发来的那些触发标记和日志残留逻辑"）。已审计定位到多个泄露面：内部通道名、方括号系统标签、开发词汇、原始应用名/窗口标题，被直接拼进了喂给 LLM 的 prompt。本任务**彻底中性化**这些注入，让角色感知保留、但机制词汇/实现细节不外泄。

先读 `AGENTS.md` 与 `docs/prompt-layers.md`。所有改动必须：保留原有功能语义（接续感知、屏幕/手机感知、记忆纪律、风格纠偏照常生效），只改**措辞与格式**，不改触发条件与层结构。改完更新 `docs/prompt-layers.md` 对应层说明。

> 验证关键词锚点：核对每处时 grep 一下确认行号，代码可能已微移。

---

## ① 跨通道接续提示：泄露内部通道名「桌宠 / QQ」（最高优先）

文件：`core/pipeline.py`，约 360–366 行：
```python
_channel_names = {"qq": "QQ", "desktop": "桌宠"}
_from = _channel_names.get(self._last_channel, self._last_channel)
_to = _channel_names.get(channel, channel)
_switch_hint = f"（刚才还在{_from}那边说话，现在换到{_to}这里了。是同一个对话的延续。）"
```
问题：`_switch_hint` 拼进 `_perception` → prompt，把内部通道/UI 名「桌宠」「QQ」原样喂给模型。人设卡里没有「桌宠」，模型只能从这里学到。

**改法**：去掉具体通道名，只传达"同一段对话在不同地方延续"的语义，且不点名任何界面/通道/实现：
```python
_switch_hint = "（你感觉她像是换了个地方继续跟你说话，但这还是同一段对话的延续。）"
```
- 删掉 `_channel_names` 映射与 `_from/_to` 的具名拼接（若 `_last_channel` 这个状态仅此处使用，保留判断"通道是否变化"的逻辑，只是不再把名字写进文本）。
- 不要在 prompt 文本里出现 "桌宠 / desktop / QQ / 通道 / channel" 任何字样。

---

## ② 方括号系统标签：模型照搬的腔调来源（高）

这些 `role:system` 内容里的 `[xxx]` / 元标签会被模型当成可模仿的"系统词汇"，是它复述"触发标记""日志残留逻辑"那种腔调的根源。逐个中性化为**自然的第三人称旁白**，去掉方括号和"感知/纠偏"等机制词，保留信息与"可自然提及、不要刻意报告"的约束。

文件：`core/prompt_builder.py`：

- 约 468：`f"[身体数据感知] 用户最近一次睡眠：{_seg_date}，..."`
  → 改为：`f"（{character.name}留意到她最近一次睡眠：{_seg_date}，……可以自然地提起，不用像在报告数据。）"`
- 约 501：`f"[手机感知] {last_updated} 收到来自用户手机的数据：…"`
  → 改为不带标签的旁白：`f"（{character.name}知道她今天的一些情况：{'，'.join(_parts)}。可以自然地提起，不要刻意罗列。）"`（去掉 "收到来自用户手机的数据" 这种实现描述与 `last_updated` 时间戳）。
- 约 520：`f"[屏幕感知] {_activity_text}。{name}知道这些，可以自然地提及…"`
  → 改为：`f"（{character.name}注意到她此刻{_activity_text}，可以自然地提起，不用刻意报告。）"`（同时见 ④ 对 `_activity_text` 内容本身的清洗）。
- 约 872：`f"[人设纠偏：{author_note_extra}]"`
  → 去括号、去"人设纠偏"字样，作为纯指令并入 author_note，例如直接追加 `author_note_extra` 文本本身，或包成 `f"（{author_note_extra}）"`，不出现"人设/纠偏"。
- 约 907：`f"[输出风格：{style_instruction}]"` 和约 927 `f"[{_style_hint}]"`
  → 去掉 `[输出风格：]` / 方括号，作为普通祈使句注入（如直接 `style_instruction`），不出现"输出风格"这种机制词。

> 注意：约 400 行群聊渲染 `f"[{time_str}] 群友{sender}：{content}"` 和约 697/708 的 `【{name}昨天的记录/心情】` 属于**对话/回忆排版**，角色本就该知道，不在本次中性化范围；保留。

判断原则：凡是"描述系统在做什么/数据从哪来/这是什么层"的字样（感知、纠偏、输出风格、收到数据、感知到）都去掉；只留"角色注意到了什么 + 怎么自然地用"。

---

## ③ 常驻记忆协议里的开发词汇：彻底中性化（按用户要求）

文件：`core/prompt_builder.py`，约 824–840 起的 `author_note_lines`（`【记忆使用协议】`/`【记忆置信边界】` 等整段）。
当前正文含大量开发词汇：「代码、文件、测试、日志、git 状态、额度」「历史 checkpoint」「当前仓库仍如此」等，每轮注入，等于持续让角色知道他涉及代码/git/日志/仓库。

**改法（彻底中性化）**：把整段记忆纪律**保留其约束意图**（旧记忆只作线索、以当前输入为准、区分稳定偏好 vs 临时状态），但**删除一切开发/工程词汇**，换成中性表达：
- 「代码、文件、测试、日志、git 状态、额度、日期、天气、现实状态」→「她当前正在做的事、当前的进展、此刻的现实情况」
- 「历史 checkpoint」→「过去的某个阶段记录」
- 「当前仓库仍如此」→「现在仍然如此」
- 通读整段，凡 git / 仓库 / 代码 / 文件 / 测试 / 日志 / checkpoint / 额度 一律替换为中性日常说法，语义不变。

并 grep 一遍同文件其余 author_note / 系统层，确认没有别处遗留这类词汇（如 "仓库""commit""部署"等）。

---

## ④ sensor 情境叙事注入原始应用名 / 窗口标题（中）

文件：`core/scheduler/triggers/sensor_aware.py`，`build_situation_narrative()`，约 285 行：
```python
if focus_app and title_hint:
    focus_str = f"正在用 {focus_app}（{title_hint}）"
elif focus_app:
    focus_str = f"正在用 {focus_app}"
```
问题：把原始 `focus_app`（应用名）和 `title_hint`（窗口标题）直接拼进 prompt，会泄露具体应用/窗口标题，模型可能复述。

**改法**：把 `focus_app` 归类到**粗类别**再描述，绝不直接输出原始应用名或窗口标题：
- 若仓库已有应用分类映射（检查 `sensor_events.py` 的 `APP_CATEGORY_CHANGED` 相关逻辑 / 任何 app→category 表），复用它，输出如「在写东西/在看视频/在浏览网页/在处理一些事情」之类的中性短语。
- 若没有现成映射，新建一个**小白名单类别表**（编辑器/浏览器/视频/通讯/游戏/其他），未知应用一律归「在做自己的事」，不回退到原始串。
- `title_hint` 默认**不进 prompt**（窗口标题最容易带敏感串）；如确需保留信息量，只允许经过同样的类别化后再用。
- 同步检查 `_event_summary()`（约 44 行）里透出的 `focus_app / focus_title_hint / screen_text_hint / screen_app_label`：这些若有进入任何 prompt 文本的路径，一并类别化；纯审计/日志用途的可不动，但要在注释里写明"不得进 prompt"。

并同样清洗 ② 里 `_load_activity_snapshot()` 产出的 `_activity_text`（屏幕感知层 3.8 的内容源）——确认它不含原始应用名/窗口标题/屏幕文本，否则在该函数出口处类别化。

---

## ⑤ 残留历史清理（数据，非代码）

短期历史里仍有之前 trigger_stub 泄露期间生成的 assistant 回合（"你那边突然涌进来一堆触发标记""那几行触发标记"之类），模型会顺着自己旧话继续编。需要把这些**引用了内部触发/标记概念**的历史回合清掉。

⚠️ 这是对**生产数据** `data/runtime/memory/{char_id}/{uid}/history.json` 的清理，**必须在 bot 停机时做**（在线写入会和清理抢写）。提供一个干跑+确认的小脚本（放 `scripts/`，默认 dry-run，`--apply` 才落盘，且先备份）：
- 扫描各 `history.json`，标出 content 命中 `触发标记|触发: |日志残留|涌进来.*标记|后台进程` 等的回合；
- dry-run 打印命中条目供人工确认；
- `--apply` 时备份原文件（`.bak_时间戳`）后删除命中回合并原子写回。

不要自动跑 `--apply`；交给用户停机后执行。

---

## 验收 / 测试
新增 `tests/test_prompt_no_internal_leak.py`：
1. 构造一次跨通道切换（`_last_channel="qq"` → `channel="desktop"`），断言生成的 perception 文本里**不含**「桌宠 / desktop / QQ / 通道 / channel」。
2. 触发手机感知 / 屏幕感知 / 身体数据层，断言注入文本**不含**「[手机感知] / [屏幕感知] / [身体数据感知] / 感知] / 收到来自用户手机的数据」等标签字样，但仍含对应数据。
3. 断言 author_note 注入文本**不含**「代码 / git / 仓库 / 日志 / 测试 / checkpoint / 输出风格 / 人设纠偏」等机制/开发词汇。
4. `build_situation_narrative` 给定 `focus_app="Visual Studio Code", title_hint="secret_project.py"`，断言输出**不含**原始串 "Visual Studio Code" 与 "secret_project.py"，而是类别化短语。
5. 回归：以上各层在中性化后仍然注入（即信息没被误删，只是换了措辞）。

跑：`pytest tests/test_prompt_no_internal_leak.py -v`，并跑 `python tests/run_eval.py` 确认层激活未受影响（若动了 tag 相关）。

## 硬性约束
- 只改措辞/格式，不改层的触发条件、`_layer` 字段、裁剪优先级、token 逻辑。
- `data/` 路径经 `core/sandbox.get_paths()` / `path_resolver`；显式传 `char_id`；`core/safe_write` 原子写。
- 改完更新 `docs/prompt-layers.md`（相关层的内容描述）与必要的 `docs/` 同步；无需更新则显式说明理由。
- ⑤ 的脚本默认 dry-run，绝不自动 `--apply`。
