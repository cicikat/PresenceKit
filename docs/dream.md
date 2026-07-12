# docs/dream.md — Dream System 总览与设计原则

> 本文是**合同级**文档（哲学边界 / 不变式 / 当前实现 / 允许扩展），不逐行追代码。
> 实现细节以 `core/dream/` 源码 grep 为准；本文滞后时，**代码为真**。
> 人称约定：**叶瑄 = 男性 = 他**（描述层）/ **「我」**（梦境输出层）；**用户（风谕）= 在梦境输出中一律称「你」**（不用「她」）；**身体数值 = 她的**（情景扮演数值 schema 字段名，不出现在生成文本中）。
> 梦境输出人称契约（单侧）：只演叶瑄自己这一轮，不替风谕旁白/配台词，「你」唯一指用户，不漂移。

---

## 一、这是什么

梦境系统是与现实聊天**分层**的隔离意识层（shared lucid layer）。

- **现实聊天**：meta 感陪伴对话，不写动作/环境描写，写正常 history/memory，维持他现实人格稳定。
- **梦境会话**：沉浸式带场景 RP，允许动作/环境/更强亲密张力，**走完全独立的 pipeline**，不写现实记忆，结束后只以薄回流影响现实。

他在两层是**同一个他**，不是第二人格、不是平行世界、不是 AI 自主做梦。现实关系连续存在；现实系统状态、工具链、memory pipeline、scheduler 在梦境期间保持**冻结隔离**。

### 三轴模型（理解整个系统的钥匙）

梦境由三条**正交**的轴组成，平时被揉成一团才出问题：

| 轴 | 是什么 | 可变性 |
|---|---|---|
| **身份（Identity Core）** | 他的人格/语气/依恋底色 + lucid 自知 | **不变量**，任何世界都不变 |
| **世界（World Ruleset）** | 现实衍生 / ABO / 吸血鬼 / 猫化 / 花苞 / custom | 可切，**从属于身份** |
| **身体（Body State）** | 她的赛博身体（heat/sensitivity/tension） | 可配可见度与强度 |

**核心决策一句话：身份在世界之上。世界是舞台不是灵魂。** prompt 里 D1（身份）永远排在 D2（世界）之上，且 D2 显式框定"今晚这场梦的规则，从属于他这个人"。这一个排序就是"换世界她还是他"的全部保证。

---

## 二、功能清单（你实际拥有的）

按交付顺序：

**基础层（MVP1）**
- 独立 dream pipeline（不走 post_process）；入梦冻结现实上下文快照
- 独立 dream_prompt 组装（不复用现实 prompt_builder，不过反话剧化 sanitizer）
- 梦境原文写 `tmp/current_dream.jsonl`，退出转 `archive/`
- 软退出（可被他挽留）+ 硬退出（绝对穿透）
- dream_summary + 短 TTL afterglow loader（Phase 7：现实层 `dream_afterglow_soft_hint` 已接线，`_format_afterglow_soft_hint()` 只读注入软提示）
- 隔离合同测试
- Dream Seed activity：睡前预构场景，12h TTL，下一次入梦时一次性注入 `entry_reason`

**三轴结构（v0）**
- `memory_access` 三档：`card_only` / `relationship_summary` / `full_snapshot`（全冻结只读）
- D0–D10 梦境 prompt 层栈，D1 固定在 D2 之上
- `body_state`：她的赛博身体，三轴各 0–100，dream-local，梦关即清
- `body_tracker`：独立分析器（仿 detect_emotion），**他永远拿不到原始数值**
- `body_projection`：4 档可见度 + 他情绪张力（yexuan_tension）耦合
- 前端 BodyState 类型 + 她的赛博感知侧栏面板

**印象回流（impression v2 · D2 细粒度化）**
- `impression_store`：`data/runtime/dreams/{char_id}/impressions/`，慢衰减（0.02/天），50 条上限
- `distill_impression`（D2）：梦结束提炼剧情概要 + 1–2 句清晰对白 + 情绪总览；输出字段：`plot`（≤80字概要）、`vivid_lines`（≤2条引语）、`impression_text`（80–150字第一人称总览）；仍禁止世界设定专有词和身体数值词；I4 写路径不变（只写 impression_store，不碰任何现实记忆存储）
- `impression_loader`（D2）：**唯一读 impressions/ 的模块**，ambient 取最近 ≤3 条；渲染为 plot + vivid_lines + impression_text 三段结构，包裹于 `<梦境印象 note="…">` 标签
- 现实层 `6g_dream_impression`：显式 XML 标签框定"以下是叶瑄做过的梦，不是现实发生的事"，叶瑄可像记得一个梦一样自然提起但绝不当作真实经历复述
- **D2 新隔离墙（固化端）**：`pipeline.post_process` 检测到当前有活跃 impression 时，在 `summarize_to_midterm` 入队 payload 中加 `dream_echo=True`；`handler_summarize_to_midterm` 收到此标记直接跳过，阻止该轮的梦境剧情通过 mid_term → episodic → identity 链路固化为现实事实
- **D2 echo 判定**：活跃 impression 仍照常注入 6g；但固化静音只覆盖出梦后 8 小时，之后仅当用户文本或回复命中梦境关键词时置 `dream_echo=True`，避免 30 天 impression TTL 饿死现实固化链。

**梦境明信片（archive 出站复盘，非第四层回流）**
- 合格 sandbox 梦（至少五个 assistant 轮、非 hard_exit、每个 dream_id 至多一次）会在 summary 后冻结成一封信；模板随机，投递日随机延迟 1–356 天。
- 明信片只读 dream summary/archive，并只写 `postcards/schedule.json` 与 SMTP；它是 archive 的第一个合法程序化读者，方向为梦 → 用户眼睛。
- 它绝不写 memory / mood / hidden_state / impression，绝不进入任何 prompt loader；世界专有词可留在用户面对的信内。

**世界包（v1）**
- 六世界包 `characters/dream_worlds/{reality_derived,abo,vampire,cat,flower_bud,custom}/`，各含 `ruleset.md` / `mes_example.md` / `vocab.json` / `lorebook.yaml`(骨架)
- `world_loader`：`load_world()` / `strip_vocab()` / `match_dream_lore()` 纯函数
- 入梦时世界冻结进 `dream_state.frozen_world`，整场不可切
- 独立梦境 lorebook 匹配器（不引用现实 lore_engine，避免现实 state 污染）
- 纵深防御：afterglow + distill 对回流文本执行 `strip_vocab`（剥世界专有词）

**高强度档（v2）**
- `threshold_break`：关掉 body_state 强度上限（数值可达全程 0–100）
- `numbers_visible`：该档把真实数值喂进他上下文
- `non_lucid`：他在虚构内不点破"这是梦"，但系统层 + dream_state 仍标记 dream
- 全开档污染矩阵测试

**Mirror 模式（v0.1）**
- `dream_mode=mirror`：三种模式之一（sandbox / scenario / mirror），入梦时冻结，整场不可切
- **Mirror 是只读镜子（v0.1）**：Mirror Mode 读取 User Hidden State 的粗粒度 snapshot，
  转化为隐喻倾向材料注入 DM 层；本版本绝不写回任何现实存储。
  User Hidden State 是底层状态层，不等于 Mirror Mode。
- **MirrorCore 入梦冻结**：`enter_dream(dream_mode="mirror")` 时，从
  `context_snapshot["user_hidden_state_snapshot"]` 构建 `MirrorCore`，
  写入 `dream_state["mirror_core"]`，整场不可更新；梦关（`clear_local_state()`）即清。
  后续隐性状态文件的任何变化（如 decay tick）都不影响当前会话的 mirror_core。
- **MirrorCore 字段**（`core/dream/mirror_core.py`）：
  ```python
  @dataclass
  class MirrorCore:
      snapshot_buckets: dict[str, str]  # 粗粒度桶，见下表
      symbolic_hints:   list[str]       # 轻量倾向提示，注入 DM 层
      source:           str = "user_hidden_state_snapshot"
      version:          str = "v0.1"
  ```
- **snapshot_buckets 字段映射**：

  | 桶键 | 来源 hidden state 字段 | 允许值 |
  |---|---|---|
  | `sensitivity_bucket` | `sensitivity.current` (`low/mid/high`) | `low / medium / high / unknown` |
  | `closeness_need_bucket` | `touch_need.deficit` (`low/mid/high`) | `low / medium / high / unknown` |
  | `embodied_ease_bucket` | `embodied_ease` (`guarded/neutral/easy`) | `low / medium / high / unknown` |
  | `association_presence` | `body_memory.entries` 条目数 | `none / light / present` |

  桶语义：`mid` → `medium`；`guarded` → `low`；`neutral` → `medium`；`easy` → `high`；
  `memory_cues` 空 → `none`；1–2 条 → `light`；3+ 条 → `present`。
  **禁止向任何下游（包括 prompt）暴露原始 float 值或百分比。**

- **DM 层（Dream Mirror Context）**：`build_dream_prompt()` 中 `dream_mode == "mirror"` 且
  `mirror_core` 非空时，在 DS_scenario 之后注入 `# DM·Mirror 梦境倾向材料` 层。
  层内容：粗粒度桶标签（中文化）+ symbolic_hints（轻量倾向文字）。
  三条禁令写死在 prompt 开头：不是诊断结论 / 不直接分析用户心理 / 不明说数值。
  sandbox 和 scenario 均不注入此层。

- **Mirror v0.1 写回保护**：`_generate_summary_bg()` 在 `dream_mode in ("scenario", "mirror")` 时
  同时跳过：
  - `wire_afterglow_from_summary()` — 不写 afterglow_residue.json，不调用 integrate_afterglow_and_save
  - `distill_impression()` — 不写 impression_store
  - `generate_summary()` 仍正常运行（梦境日志保留，不进入 Reality 流）

  未来 Mirror afterglow 必须：①在 impression entry 上增加独立 `mode/source` 标记，
  ②在 `impression_loader` 侧增加 Reality integrator gate，③通过显式 WriteEnvelope，
  不得复用 Sandbox 的无标记写入路径。

**Scenario 模式（v0–v0.8）**
- `dream_mode=scenario`：三种模式之一（sandbox / scenario / mirror），入梦时冻结，整场不可切
- `ScenarioCore`：隔离内核，存 `script_id / current_stage_id / stage_turns / ending_state`
  以及进度信号观察字段（v0.6），存入 `dream_state["scenario_core"]`，梦关即清。
  **不连接** user_hidden_state / symbolic_anchors / Mirror HUD / impression 写回
- **Scenario 不读 User Hidden State（v0.8）**：
  - `build_dream_prompt` 中 D4.5 层有 `dream_mode != "scenario"` 守卫；
    即使 `scene_state / symbolic_anchors` 含 `body_intimate / physical_closeness` 触发标签，
    Scenario 也绝不注入 `context_snapshot.user_hidden_state_snapshot`。
  - User Hidden State 不等于 Mirror Mode；Mirror Mode 才是未来读取 hidden_state snapshot 的候选模式。
- **Scenario 不写 User Hidden State（v0.8）**：
  - `_generate_summary_bg()` 增加 `dream_mode` 参数（从 `_do_close_dream` 在 `clear_local_state` 前捕获）；
    当 `dream_mode == "scenario"` 时，跳过 `wire_afterglow_from_summary()`，不写 `afterglow_residue.json`，
    不调用 `integrate_afterglow_and_save()`，`hidden_state.json` 保持不变。
- **Scenario 不写 Reality-facing impression（v0.8.1）**：
  - `impression_store`（`data/runtime/dreams/{char_id}/impressions/{uid}.json`）被 `impression_loader`
    读取后注入 Reality prompt 的 `6g_dream_impression` 层。如果 Scenario exit 写入该 store，剧本内容
    就会通过 6g 层污染现实聊天上下文。
  - 修复：`_generate_summary_bg()` 在 `dream_mode == "scenario"` 时同时跳过 `distill_impression()`，
    不写 impression_store。`generate_summary()` 仍正常运行（剧本 summary 保留在梦境日志中供调试，
    但不进入 Reality 流）。
  - Sandbox 保持原有 impression 行为（`distill_impression()` 照常调用）。
  - Mirror 未来如果要写 mirror_impression，必须在 impression entry 上增加独立 `mode/source` 标记，
    并在 `impression_loader` 侧增加 Reality integrator gate，不得复用 Sandbox 的无标记写入路径。
  - 旧数据清理：历史上已写入的 Scenario impression 条目在 `decay_after` 到期（30 天）后自动失效；
    本轮不做迁移。
- **Scenario 不注入 D5 body_projection（v0.8.2）**：
  - `build_dream_prompt()` 的 D5 层增加 `dream_mode != "scenario"` 守卫；
    即使 `body_projection_text` 非空，Scenario prompt 也绝不注入 D5·她的身体感知块。
  - Scenario 的身体/亲密表现应由 script stage 文字（`dramatic_task` / `entry_pressure` /
    `not_yet_allowed` / `drift_pressure`）和叙事本身控制，不应由通用 Dream body_state 系统驱动。
  - 如未来某个 Scenario 剧本确实需要启用身体投影，须通过显式 script-level flag（如
    `allow_body_projection: true`），而不是默认继承。本轮不实现该 flag。
  - body_state 本体、body_tracker、body_projection 计算不变；只是 Scenario prompt 不消费 D5 文本。
  - Sandbox / Mirror 保持原有 D5 注入行为。
- `ScenarioCore.increment_stage_turns()`：每轮 dream_turn LLM 成功后调用，返回新冻结实例
- **mid-session 写保护守卫**（v0.5）：DREAM_ACTIVE 状态下 `enter_dream` fail-loud：
  模式切换错误（`mode=X → mode=Y`）和 script_id 替换错误分别返回独立错误信息
- `DS_scenario` 层：只注入当前阶段；绝不注入后续阶段、出口判断、软门控
- `drift_pressure`（v0.5）：剧本 stage 可选字段；`after_turns: int` + `instruction: str`；
  当 `stage_turns >= after_turns` 时注入 DS 层"漂移压力 / Drift Pressure"块；
  只注入当前 stage，后续 stage 的 drift_pressure 不泄漏
- 剧本文件：`data/dream/scenarios/{script_id}.yaml`，authored content，不走 sandbox
- `prison_demo.yaml`：三阶段示例剧本（arrival / negotiation / fracture），arrival + negotiation 各含 drift_pressure
- **Progress Signal Skeleton（v0.6）**：软门控观察信号骨架
  - `ScenarioCore` 新增三字段（frozen dataclass）：
    - `last_progress_signal: str | None` — `"not_close"` / `"approaching"` / `"satisfied"`
    - `last_matched_exit_signs: list[str]` — 本轮命中的出口标志语义短句
    - `last_blocked_events: list[str]` — 用户尝试的 not_yet_allowed 短句
  - `ScenarioCore.with_progress_signal(signal, matched, blocked)` — 返回新冻结实例
  - DS 层追加 `<scenario_control>` 输出协议：要求 LLM 在每轮回复末尾附加隐藏控制块；
    当前 stage.exit_signs 作为 matched_exit_signs 的合法引用列表注入（仅当前 stage）
  - `_extract_scenario_control(reply)` → `(visible_reply, parsed_control | None)`：
    strip 控制块（不论合法与否）；非法/缺失时返回 `None`，fail-soft 不崩溃
  - dream_turn 处理链：先 strip 控制块 → 可见回复送 dream log / 返回值 → 合法时
    先 `with_progress_signal()`；若本轮发生 stage transition 或 completed，**跳过**
    `increment_stage_turns()`（过渡轮属旧 stage，新 stage 从 `stage_turns=0` 开始）；
    否则正常 `increment_stage_turns()`
- **Stage Transition MVP（v0.7）**：连续 satisfied 两次 → 顺序推进下一 stage
  - `ScenarioCore` 新增字段：`satisfied_streak: int = 0`（frozen dataclass）
    - `with_progress_signal("satisfied")` → streak +1；任何其他信号 → streak = 0
    - control block 缺失/非法 → `reset_satisfied_streak()` 归零（保守策略，防静默推进）
  - `ScenarioCore.advance_to_stage(next_stage_id)` — 推进到指定 stage：
    重置 `stage_turns=0 / last_progress_signal=None / last_matched_exit_signs=[] /
    last_blocked_events=[] / satisfied_streak=0`；`ending_state` 不变
  - `ScenarioCore.mark_completed()` — 设 `ending_state="completed"`（最后 stage 达成条件时）
  - `ScenarioCore.reset_satisfied_streak()` — 控制块缺失时调用
  - `scenario_loader.get_next_stage(script, current_stage_id)` — 按 YAML 顺序取下一 stage；
    当前已是最后 stage 时返回 `None`；找不到 current_stage_id 时 raise ValueError（fail-loud）
  - dream_turn 阶段推进逻辑（`satisfied_streak >= 2 且 ending_state != "completed"`）：
    1. 加载 script，调 `get_next_stage`
    2. 若有下一 stage → `advance_to_stage(next_stage.id)`
    3. 若无（已是最后 stage）→ `mark_completed()`
    4. 任何 transition 失败 → warning-only，不崩溃
  - DS 层：`ending_state=="completed"` 时在层顶注入"【剧本状态：所有阶段已完成】"
  - 不做：分支、多结局、新裁判模型、潜意识读写、impression/afterglow 整合、
    Mirror anchors、dream_depth / dream_stability 共享 HUD、LLM 自行指定 next_stage

---

## 三、Prompt 层栈

### 梦境侧（独立 D 栈，由 dream_prompt 组装）

| 层 | 内容 | 轴 / 控制 |
|---|---|---|
| `D0_jailbreak` | 破限预设（梦境独立源） | — |
| `D1_identity_core` | **不变量**：他人格 + lucid 自知 | 轴1，永不可关 |
| `D2_world_ruleset` | 今晚世界规则，显式框"从属于他" | 轴2，world_layer |
| `D3_dream_mes_example` | 世界对应 few-shot（与现实卡物理分离） | 轴2 |
| `D4_frozen_reality` | 冻结现实上下文（只读） | memory_access |
| `D4.5_hidden_state` | 用户隐性状态 bucket 只读快照（tag-gated，Phase 4）**Scenario Mode 下永远禁用** | tag: body_intimate / physical_closeness；`dream_mode != "scenario"` |
| `D5_body_projection` | 她的身体感知投影 **Scenario Mode 下永远禁用** | 轴3，boundary_level + yexuan_tension；`dream_mode != "scenario"` |
| `D6_scene_anchors` | 场景状态 + 临时符号锚点 | dream-local |
| `D7_dream_tension` | 梦内情绪张力（粗粒度分桶，dream-local；prompt 不暴露精确数值） | — |
| `D8_dream_director` | 梦境导演注记（允许动作/环境）+ 逃生协议提醒 | boundary_level |
| `DS_scenario` | 剧本当前阶段（仅 scenario 模式；script title / stage name / dramatic_task / entry_pressure / not_yet_allowed / drift_pressure） | dream_mode=scenario |
| `DM_mirror` | Mirror 梦境倾向材料（仅 mirror 模式；粗粒度桶标签 + 轻量 symbolic_hints；只读，不诊断，不暴露数值） | dream_mode=mirror |
| `D9_dream_history` | 梦内滚动短上下文（不过现实 sanitizer） | — |
| `D10_user_message` | 她当前梦内输入 | — |

梦境运行目录另含 `postcards/schedule.json`（冻结信文、投递日、发送重试状态）；该目录不是任何对话或记忆 loader 的输入。

> 与现实栈**相反**之处：D8 要求输出动作/环境（现实禁止），D9 绝不过反话剧化清洗（现实必过），全程无 retrieve / mood_state / author_note_extra。这些反转就是"必须独立 pipeline"的根据。

**D4.5 用户隐性状态快照（Phase 4，只读接入）**：

- `build_snapshot()` 在入梦时调用 `load_dream_snapshot(uid, now)` 并将结果冻结进 `context_snapshot["user_hidden_state_snapshot"]`。
- `build_dream_prompt()` 在每轮组装 D4.5 时检查 tag gate（`body_intimate` / `physical_closeness`），只有命中才注入。
- 注入内容只含 bucket label（sensitivity / touch_appetite / embodied_ease / memory_cues），**不含 float、uid、timestamp、weight、baseline、update_source**。
- Dream 无任何写路径：`DREAM_DIRECT_WRITABLE = frozenset()`；save_hidden_state / integrate_* / apply_time_decay / consolidate_baselines 均不在 Dream 路径中出现。
- Fail-closed：load 失败 / snapshot 格式异常 / tag 判断异常 → 不注入，记 warning，不阻断 Dream。
- 优先级：D4.5 排在 D4（frozen_reality）之后，如需裁剪优先裁 D4.5。

**Afterglow Residue 回流（Phase 6 — 已接线）**：

Dream 退出后，`_generate_summary_bg()` 在 `generate_summary()` 完成后调用
`wire_afterglow_from_summary()`（`core/dream/dream_exit_afterglow.py`）。
**Dream 本身不拥有写权限**；所有写入经 Reality-side integrator。

回流管道：

```
Dream Exit  →  _do_close_dream()  →  archive log  →  REALITY_AFTERGLOW
                                         ↓ (background task)
                                   generate_summary()  →  summary.json
                                         ↓
                              wire_afterglow_from_summary()
                                         ↓ derive tone from summary
                              AfterglowResidueInput (age_hours=0.0)
                                         ↓
                              save_afterglow_residue()  →  afterglow_residue.json (TTL 8h)
                                         ↓  stamp_dream_afterglow()
                              integrate_afterglow_and_save()  →  UserHiddenState
```

Tone 推导规则（从 summary record）：
- `exit_type=hard_exit` OR `afterglow=hurt_reluctance` → `"stress"` （负向）
- `afterglow=gentle_residue` + `summary_weight≥0.7` → `"comfort"` （正向 + ease）
- `afterglow=gentle_residue` → `"calm"` （正向，无 ease）
- 空 summary 或无法推导 → `"neutral"` （零效果 fallback）

Fail-closed：`save_afterglow_residue` 或 `integrate_afterglow_and_save` 失败 → warning，不阻断 Dream exit。

允许影响字段（仅两个，均为快速层）：
- `sensitivity.current` — ±1.5（正向 tone: comfort/calm/warm/safe/trusted；负向: fear/stress/threat）
- `embodied_ease` — +0.8（仅 comfort/safe/trusted；无负向影响）

永久禁止字段：`sensitivity.baseline` / `touch_need.baseline` / `touch_need.deficit` / `body_memory`

WriteEnvelope 双重门控：
- `can_write_memory=True` — 必须
- `source=DREAM_AFTERGLOW` — 必须；其他 source 即使 can_write_memory=True 也被拒绝

### 现实侧（梦的回流注入层，由现实 prompt_builder 注入）

| 层 | 内容 | 生命周期 |
|---|---|---|
| `6f_dream_afterglow` | 梦境余韵详细层（只读，非现实事实） | 0–2h 注入完整摘要/色调/意象；2–5h 注入模糊摘要/色调；5h 后返回空 |
| `dream_afterglow_soft_hint` | 梦境余韵软提示（只读，非事实，`may/可能` 限定，TTL 8h） | 详细层为空后接管；读 `afterglow_residue.json`；neutral+空tags 不注入；读取异常 fail-closed |
| `6g_dream_impression` | 梦境印象（plot + vivid_lines + 情绪总览，`<梦境印象>` XML 标签显式框定非现实） | 慢衰减；有活跃 impression 时注入；对应轮 `dream_echo=True` 跳过 mid_term 固化 |

`6f_dream_afterglow` 与 `dream_afterglow_soft_hint` 在 Reality prompt builder 中互斥，位于层 6e 之后、层 6g 之前。两层均进入 token 裁剪表且优先级最低（最先被裁剪）。写隔离不变：只读，不写 memory / mood / profile / hidden state。

afterglow 完整路径：Dream exit → summary → Reality prompt `6f_dream_afterglow`（0–5h）；同时 `wire_afterglow_from_summary()` → `integrate_afterglow_and_save()` → hidden_state.json（Phase 6 numeric wiring）并写 residue，供 `dream_afterglow_soft_hint` 在详细层结束后接管至 8h。

出梦后的首次现实开口由 scheduler `dream_exit` proposer 负责，而不是 hook 在关闭点：它等待异步 summary/afterglow 就绪后，走正常 Reality `_pipeline_send → fetch_context → build_prompt`，因此直接复用 `6f_dream_afterglow` / soft hint 上下文。触发器只在 `QUIET` 状态报名，按 `dream_state.char_id` 让做梦角色发言，并以 `last_greeted_dream_id` 保证一梦一次。scenario/mirror 不写 afterglow，按中性问候降级；sandbox afterglow 8h 内始终未就绪时，也仅在退出后一个有限清醒时段内降级问候一次。

---

## 四、三层回流（梦 → 现实，唯一出口）

| 产物 | 内容 | 去向 | 谁能读 |
|---|---|---|---|
| **archive 原文** | 梦境全文 | `archive/dream_*.jsonl` | **仅她复盘，任何 loader 永不读** |
| **afterglow summary** | 剥场景的情绪余韵 | `summaries/*.summary.json` | `wire_afterglow_from_summary()` 读取后转写 `afterglow_residue.json`（Phase 6 已接线）；summary 本身 never_retrieve |
| **impression residue** | 梦境印象（D2：plot + vivid_lines + 情绪总览） | `impressions/{uid}.json` → 6g | impression_loader 唯一读；对应轮 pipeline 标记 `dream_echo=True`，跳过 mid_term 固化 |

`dream_exit` 主动开口不是第四层回流产物：它不自行保存或拼装梦内容，只消费上述现实侧只读注入层并触发一次正常 Reality turn。

**边界精确化**：「我们共有过一场梦 + 那种情绪」**是**真实共同事件，可回流；「梦里去了哪、做了什么、什么世界设定」**不是**现实事实，只留 archive。剥离在**生成时**结构性完成，使下游没有可泄漏的场景。

---

## 五、数据目录

```
data/runtime/dreams/{char_id}/     独立 dream 根（不并入 reality memory 树）默认 char_id=yexuan
├── tmp/current_dream_{uid}.jsonl  梦境原文（dream_only，退出转 archive）
├── archive/dream_*.jsonl          归档原文（仅复盘，永不进任何 loader）
├── summaries/dream_*.summary.json afterglow 摘要（→ 6f）
├── impressions/{uid}.json         低权印象（→ 6g，唯 impression_loader 读）
├── state/{uid}/dream_state.json   per-uid 会话状态
└── settings/{uid}.json            per-uid 梦境设置

characters/dream_worlds/{world_id}/
├── ruleset.md                     D2 世界规则
├── mes_example.md                 D3 世界 few-shot
├── vocab.json                     专有词表（strip_vocab 纵深用）
└── lorebook.yaml                  梦境世界书（骨架，可填）
```

每个 dream 产物带 sentinel：`never_retrieve / not_memory_source / reality_boundary: dream_only`。

---

## 六、状态机

```
REALITY_CHAT → DREAM_ENTRANCE_AVAILABLE → DREAM_ACTIVE → DREAM_CLOSING → REALITY_AFTERGLOW → REALITY_CHAT
```

- `DREAM_LOCKED`：**预留 enum，未实现**。MVP 不做系统级软退锁，"挽留"只走 RP 叙事。
- 现实窗锁定：`DREAM_ACTIVE / DREAM_CLOSING` 时 `/desktop/chat`、`/mobile/chat` 和 QQ owner
  消息都会被**后端硬拒绝**现实回合（安全网，挡 stale client / 第二设备 / 竞态）；QQ
  拒绝路径不进入现实 pipeline，也不写 runtime / memory。沉浸连续性靠 UI 把用户锁在
  梦境窗实现，不靠端点 reroute。

---

## 七、接口与设置

### 端点（实现状态）

| 端点 | 状态 | 说明 |
|---|---|---|
| `POST /dream/enter` | ✅ 已有 | 入梦，冻结世界/快照 |
| `POST /dream/chat` | ✅ 已有 | 梦内回合，走独立 dream pipeline |
| `POST /dream/exit` | ✅ 已有 | 硬退出；无条件穿透并立即关闭梦境（Invariant D，永不可改） |
| `POST /dream/wake` | ✅ 已有 | 软挽留闸门；满足门控时角色挽留一次，否则直接硬退 |
| `POST /dream/resume` | ✅ 已有 | 挽留后留下；`DREAM_EXIT_REQUESTED → DREAM_ACTIVE` |
| `GET /dream/state` | ✅ 已有 | 只读 UI 投影：状态、身体数值、张力、场景和象征锚、`flow_entries`（梦境流动，见下） |
| `GET /dream/settings` | ✅ 已有 | 读取 per-uid 偏好默认值 |
| `PATCH /dream/settings` | ✅ 已有 | 枚举校验后的局部更新；`world_layer` / `lucid_mode` 仅影响下一场梦 |

**软挽留（`/dream/wake`）设计约束**：
- `/dream/exit` 保持纯硬退，**零改动**（Invariant D）。
- `/dream/wake` 是唯一软挽留入口，且只拦一次（`retention_offered_dream_id` 去重）。
- 门控：入梦有效轮数 ≥ 3（`RETAIN_MIN_TURNS`）AND（`emotional_tension ≥ 0.55` OR `body.heat ≥ 55`）。
- LLM 生成挽留失败 → fail-open，自动退化为硬退出，不卡用户。
- 用户坚持醒来：前端调 `/dream/exit`（硬退，必成功）。
- `DREAM_EXIT_REQUESTED` 状态下 `dream_turn()` 仍被 status 守卫拒绝（只接受 DREAM_ACTIVE / DREAM_CLOSING）；`/dream/resume` 把状态置回 DREAM_ACTIVE 后对话恢复。

**协议字段更名（Brief 25 §3 P2）**：`GET /dream/state` 的情绪张力字段协议名是
`char_tension`；`yexuan_tension` 作为已废弃别名双发（同值），供尚未升级的客户端过渡，
计划保留 ≥1 个版本后删除（见 `tests/test_no_hardcoded_character.py`
`YEXUAN_TENSION_ALLOWLIST` 的到期条件）。内部实现（`body_projection.py` /
`dream_pipeline.py` 的 `yexuan_tension` 参数名/dict key）不受此次协议更名影响，
仍是内部 plumbing，非对外协议。

入梦构建 `context_snapshot` 时会尝试消费 reality-scoped `dream_seed.json`。有效种子以前缀
`今晚的梦境设定：...` 注入 `entry_reason`；TTL 12 小时、一次性消费、失败不阻断 Dream。

### dream_state.json 字段

`user_id` / `status` / `dream_id` / `frozen_world` / `lucid_mode` / `context_snapshot` / `body_state{heat,sensitivity,tension}` / `emotional_tension`(他的，0–1) / `flow_entries`（见下）。关闭后保留 `char_id` / `last_dream_id` / `last_exit_type` / `last_dream_mode` / `last_exited_at` / `last_greeted_dream_id`，供现实侧出梦问候去重与降级判断；`clear_local_state()` 不清这些字段。软挽留相关字段：`retention_offered_dream_id`（标记本场已挽留过，防重复；`clear_local_state()` 不清，因为存在于活跃梦期间的 state dict 外层）。

### 梦境流动（`flow_entries`，Brief 25 §2）

规则驱动、零额外 LLM 调用的"发生了什么"时间线，供前端侧栏展示（此前前端读的
`flow_entries`/`dream_events`/`events` 后端从未产出，永远走前端三条固定 fallback
文案；现在后端真正产出 `flow_entries`）。

- **数据结构**：`state["flow_entries"]: list[{"ts": iso, "kind": str, "summary": str}]`，
  FIFO 上限 10 条，最新在末尾。实现在 `core/dream/dream_flow.py`（纯函数，不做 I/O）。
- **产出规则**（一轮最多 2 条命中，按下表顺序）：

  | kind | 触发 | summary |
  |---|---|---|
  | `status_shift` | 入梦 / `/dream/wake` 进入 EXIT_REQUESTED / 挽留成功 / 关闭 | 「梦境正在成形」「醒来的边缘在靠近」「他把你留了下来」「梦在慢慢消散」 |
  | `scene_shift` | `scene_state` 较上轮变化 | 「场景转入：{前20字}」 |
  | `tension_up` / `tension_down` | `emotional_tension` 较上轮 Δ≥0.15 | 「他的情绪张力在上升/回落」 |
  | `anchor_new` | `symbolic_anchors` 新增项 | 「新的象征浮现：{anchor}」 |

  模板不写角色名（统一用「他」，与前端既有 fallback 文案一致），天然不含硬编码角色名。
- **产出点**：`enter_dream()`（清空重开 + 追加 `status_shift`"梦境正在成形"）、
  `dream_turn()`（`patch_local_state()` 前后 diff 出 `scene_shift`/`tension_*`/`anchor_new`）、
  `admin/routers/dream.py` 的 `dream_wake()`（`exit_requested`/`retained`）、
  `_do_close_dream()`（`closing`，在 `clear_local_state()` 之前写入，因为该函数不清
  `flow_entries` 字段）。
- **暴露**：`GET /dream/state` 的 `flow_entries` 字段；非梦境期该 key 本就为空数组。
- **测试**：`tests/test_dream_flow_entries.py`。

### dream_settings.json 字段（UI 设置页对应）

| 字段 | 取值 |
|---|---|
| `memory_access` | card_only / relationship_summary / full_snapshot |
| `boundary_level` | vague / body_perceptible / numbers_visible / threshold_break |
| `world_layer` | reality_derived / abo / vampire / cat / flower_bud / custom |
| `lucid_mode` | lucid_shared / non_lucid |
| `enable_dream_lorebook` | bool |
| `jailbreak_preset` | `characters/dream_presets/{name}.md` 的安全 ASCII 名；缺失时回退 `default` |
| `reality_context_full_turns` | int，默认 3。D4 层 `recent_reality_context` 完整注入的轮数上限（见下方说明）。 |

**D4 `recent_reality_context` 轮数衰减**

入梦后的现实背景注入分两段，以防现实语感随时间带偏梦境：

- **前 N 轮（dream_turn < N）**：D4 注入完整的 `recent_reality_context`（逐字现实对话摘要）。
- **第 N 轮起（dream_turn ≥ N）**：逐字背景停止注入，改注入一句概括 `（你记得入梦前你们在{gist}）`，其中 `gist` 是 `recent_reality_context` 的一句浓缩，在入梦时冻结于 `context_snapshot["recent_reality_gist"]`，梦中不再二次调 LLM。

N 由 `dream.reality_context_full_turns` 配置（默认 3）。`dream_turn` 是梦内已完成的 assistant 轮次数，在 `dream_pipeline.dream_turn()` 调用 `build_dream_prompt()` 前从 `dream_history` 中统计。

> 破限属梦境独立源（D0），不在 settings 暴露开关（独立 pipeline 天然不漏进现实，工程上最不用操心）。

### Emerald-client 当前接线

同级项目 `<desktop-client-root>` 已把 Dream 作为正式 overlay 接入，不再是 mock preview：

- React API：`src/shared/api/dream.ts` 通过 Tauri invoke 调用 Dream 端点；
- Rust bridge：`src-tauri/src/lib.rs` 提供 `dream_get_state` / `dream_enter` / `dream_chat` /
  `dream_exit` / `dream_get_settings` / `dream_update_settings`；
- UI：`src/windows/dream/` 提供入梦、梦内聊天、WAKE / Esc 硬退出、状态侧栏、偏好和帮助窗；
- 状态轮询：`GET /dream/state` 每 8 秒刷新，显示她的 body 数值、他梦内张力、场景和象征锚；
- 本地外观：聊天字号、主题字号、动态字体包、RGB 主题色、聊天背景导入裁切和模糊度只存客户端；
- 后端偏好：memory access、感知边界、世界层、清明模式、dream lorebook 走 `/dream/settings`。

当前客户端没有暴露 `jailbreak_preset` 选择器，后端仍保留该 PATCH 字段和默认值。

---

## 八、设计原则（贯穿全系统）

1. **独立 pipeline by construction**：三个全局单例污染源（`mood_state` / `Pipeline.author_note_extra` / 现实反话剧化 sanitizer）证明梦必须走完全独立的 pipeline，而不是在 post_process 加分支。后者会变成"每次改 post_process 都要记得排除梦"的黑名单腐烂。
2. **隔离靠"没接线"，不靠"过滤"**：承重墙是 reflect/consolidate 源码里**根本没有** impressions 读取路径（grep 不到字符串本身就是合同）。`strip_vocab` 等是**纵深，不是墙**。
3. **三轴正交，身份在世界之上**：D1 永在 D2 之上，换世界不换人。
4. **强度活在表现层，逃生活在系统层**：挽留 / 低气压 / 破限 / 高强度 / non_lucid 都是叙事与表现；`hard_exit` 是**绝对的系统保证**，任何强度档 / non_lucid / config 都不能削弱、关闭。最猛的配置配最硬的逃生测试。
5. **生成时剥离**：场景 / 世界 / 身体在回流产物**生成时**结构性剥掉，下游就没有可泄漏的东西。
6. **三层回流，各有独立 store**：死 archive / 短 afterglow / 低权印象，现实记忆链都不读。
7. **反假绿铁律**：凡"X 不在 Y 里"的合同断言，**必配**"X 在 Y 里"的正样本对照，防空库 / stub 伪装成验证。（这条是踩坑踩出来的——空库断言曾两次伪装成洗白验证。）
8. **有界耦合**：他情绪耦合 dream-local、单轮封顶（≤0.15）、梦关即清，永不写现实 mood_state。
9. **D7 粗粒度张力桶**：`yexuan_tension`（float 0–1）在进入 D7 prompt 前经 `_bucket_tension()` 映射为四档语义标签（`< 0.25` → 低位 / `< 0.5` → 上升中 / `< 0.75` → 高位 / `≥ 0.75` → 临界），**绝不向 LLM 暴露精确百分比**，避免过拟合数值细节。HUD / UI 若需显示原始数值，走独立读路径，与 prompt 注入完全解耦。sandbox / mirror / scenario 共享同一分桶逻辑；`yexuan_tension` 本体、HUD 存储、ScenarioCore、hidden_state 均不受影响。

---

## 九、合同段（invariant / current / future）

### INVARIANT（绝不能破）
- 现实记忆冻结只读；梦内绝不回写现实（含 mood_state 一字节不碰）
- impression/afterglow store 物理隔离（reflect/consolidate/retrieve 无读取路径）
- body_state 始终 dream-local、梦关即清；threshold_break 只解**数值上限**，不解生命周期/隔离/逃生
- 数值只在 `numbers_visible+` 进他上下文
- `hard_exit` 绝对：任何档位 / non_lucid / 叙事挽留 / config 都不能削弱
- 身份在世界之上；世界入梦冻结、整场不可切
- non_lucid 只改他虚构内自知；系统层仍标记 dream，墙 + 逃生不变

### CURRENT（当前实现）
见第二节功能清单。三轴 + 四档 + 六世界 + 软硬双出口已落地；Mirror v0.1 已落地（只读镜子，MirrorCore 入梦冻结，DM 层注入，无写回）；三层产物均会生成，
现实 prompt 接入互斥的 `6f_dream_afterglow` / `dream_afterglow_soft_hint`（只读余韵层）和 `6g_dream_impression`。测试数量以
`tests/test_dream_*.py` 当前收集结果为准，不在合同文档里固定计数。

**现实侧 loader 不引用 dream 路径——已有自动测试护栏**
`tests/test_dream_isolation_guard.py` 静态扫描 `core/memory/*.py`、`core/pipeline.py`、
`core/prompt_builder.py` 的非注释源码行，断言不出现 `dreams/`、`impression_loader`、
`afterglow`、`dream_summary`、`dreams/archive` 等 dream 域标记。Phase 7 为 `prompt_builder.py` 的 `afterglow` 引用添加了 allowlist 条目（read-only 用途，注释说明）。
唯一允许的例外用 `_ALLOWLIST` 显式白名单：`core/pipeline.py` 对 `impression_loader` 的
import（它只传递预加载文本给 prompt，不读 dream 数据）。
配有反假绿正样本：断言 `core/dream/impression_loader.py` 本身含有 `dreams/` 标记，
证明扫描逻辑确实能命中，不是对空集的无效断言。
任何 path 重构触碰上述文件时，此测试即为门控。

### FUTURE（允许扩展，未做，留 seam）
- 世界专属身体语义
- mid-dream world drift（梦中切世界）
- impression relevance retrieve（按现实话题召回印象）
- impression 再概括 / 跨梦持久身体（后者违反 INVARIANT，**明确不做**）
- 角色改文件功能（落地时：逃生路径文件 + 隔离边界必须排除在角色可写范围外）
- 硬件氛围（OLED zzz / 灯光）：由"梦境模式开关"驱动，**绝不由梦境内容驱动**

---

## 十、已知边界 / 技术债

- **F1 虚构场景**：他读 6g 印象后，可能在现实回合**编造**一个梦场景，被正常 capture 当事实。靠 6g 文案约束缓解，**非结构性防御**。低危（明确框成梦），可接受。
- **vocab_strip 是手维护黑名单**：新世界/新术语忘填 `vocab.json` 会静默漏。但因承重墙是 store 隔离，仅在 F1 边界才有影响，不致命。**任何人不得把它当墙用。**
- **身份稳定性测试是弱代理**：只断言人称正确 + 依恋关键词在场，真验证靠实际游玩。
- **DREAM_LOCKED 预留未实现**：无系统级软退锁。
- **dream settings 仍保留旧路径降级读**：`_LAYOUT_DREAM = "v1"` 后写入
  `data/runtime/dreams/{char_id}/settings/{uid}.json`，读取仍可通过 `for_read()` 回退旧
  `data/dreams/settings/{uid}.json`。清理旧文件前先看 fallback 观测。
