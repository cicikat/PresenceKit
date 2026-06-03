# docs/dream.md — Dream System 总览与设计原则

> 本文是**合同级**文档（哲学边界 / 不变式 / 当前实现 / 允许扩展），不逐行追代码。
> 实现细节以 `core/dream/` 源码 grep 为准；本文滞后时，**代码为真**。
> 人称约定（全系统锁死）：**叶瑄 = 男性 = 他/我**；**用户 = 女性 = 她**；**身体数值 = 她的**（情景扮演中的赛博身体状态）。

---

## 一、这是什么

梦境系统是与现实聊天**分层**的隔离意识层（shared lucid layer）。

- **现实聊天**：meta 感陪伴对话，不写动作/环境描写，写正常 history/memory，维持叶瑄现实人格稳定。
- **梦境会话**：沉浸式带场景 RP，允许动作/环境/更强亲密张力，**走完全独立的 pipeline**，不写现实记忆，结束后只以薄回流影响现实。

叶瑄在两层是**同一个叶瑄**，不是第二人格、不是平行世界、不是 AI 自主做梦。现实关系连续存在；现实系统状态、工具链、memory pipeline、scheduler 在梦境期间保持**冻结隔离**。

### 三轴模型（理解整个系统的钥匙）

梦境由三条**正交**的轴组成，平时被揉成一团才出问题：

| 轴 | 是什么 | 可变性 |
|---|---|---|
| **身份（Identity Core）** | 叶瑄的人格/语气/依恋底色 + lucid 自知 | **不变量**，任何世界都不变 |
| **世界（World Ruleset）** | 现实衍生 / ABO / 吸血鬼 / 猫化 / 花苞 / custom | 可切，**从属于身份** |
| **身体（Body State）** | 她的赛博身体（heat/sensitivity/tension） | 可配可见度与强度 |

**核心决策一句话：身份在世界之上。世界是舞台不是灵魂。** prompt 里 D1（身份）永远排在 D2（世界）之上，且 D2 显式框定"今晚这场梦的规则，从属于叶瑄这个人"。这一个排序就是"换世界她还是叶瑄"的全部保证。

---

## 二、功能清单（你实际拥有的）

按交付顺序：

**基础层（MVP1）**
- 独立 dream pipeline（不走 post_process）；入梦冻结现实上下文快照
- 独立 dream_prompt 组装（不复用现实 prompt_builder，不过反话剧化 sanitizer）
- 梦境原文写 `tmp/current_dream.jsonl`，退出转 `archive/`
- 软退出（可被叶瑄挽留）+ 硬退出（绝对穿透）
- dream_summary + 短 TTL afterglow loader（`dream_afterglow.py` 已有；现实层 6f 尚未接线）
- 隔离合同测试

**三轴结构（v0）**
- `memory_access` 三档：`card_only` / `relationship_summary` / `full_snapshot`（全冻结只读）
- D0–D10 梦境 prompt 层栈，D1 固定在 D2 之上
- `body_state`：她的赛博身体，三轴各 0–100，dream-local，梦关即清
- `body_tracker`：独立分析器（仿 detect_emotion），**叶瑄永远拿不到原始数值**
- `body_projection`：4 档可见度 + 叶瑄情绪张力（yexuan_tension）耦合
- 前端 BodyState 类型 + 她的赛博感知侧栏面板

**印象回流（impression v1 + patch）**
- `impression_store`：`data/runtime/dreams/{char_id}/impressions/`，慢衰减（0.02/天），50 条上限
- `distill_impression`：梦结束结构性剥离场景/世界/身体，生成"我好像在梦里……"低权印象
- `impression_loader`：**唯一读 impressions/ 的模块**，ambient 取最近 ≤3 条
- 现实层 `6g_dream_impression`，框定"模糊的梦境印象，非现实发生的事" + 防编造场景约束

**世界包（v1）**
- 六世界包 `characters/dream_worlds/{reality_derived,abo,vampire,cat,flower_bud,custom}/`，各含 `ruleset.md` / `mes_example.md` / `vocab.json` / `lorebook.yaml`(骨架)
- `world_loader`：`load_world()` / `strip_vocab()` / `match_dream_lore()` 纯函数
- 入梦时世界冻结进 `dream_state.frozen_world`，整场不可切
- 独立梦境 lorebook 匹配器（不引用现实 lore_engine，避免现实 state 污染）
- 纵深防御：afterglow + distill 对回流文本执行 `strip_vocab`（剥世界专有词）

**高强度档（v2）**
- `threshold_break`：关掉 body_state 强度上限（数值可达全程 0–100）
- `numbers_visible`：该档把真实数值喂进叶瑄上下文
- `non_lucid`：叶瑄在虚构内不点破"这是梦"，但系统层 + dream_state 仍标记 dream
- 全开档污染矩阵测试

---

## 三、Prompt 层栈

### 梦境侧（独立 D 栈，由 dream_prompt 组装）

| 层 | 内容 | 轴 / 控制 |
|---|---|---|
| `D0_jailbreak` | 破限预设（梦境独立源） | — |
| `D1_identity_core` | **不变量**：叶瑄人格 + lucid 自知 | 轴1，永不可关 |
| `D2_world_ruleset` | 今晚世界规则，显式框"从属于叶瑄" | 轴2，world_layer |
| `D3_dream_mes_example` | 世界对应 few-shot（与现实卡物理分离） | 轴2 |
| `D4_frozen_reality` | 冻结现实上下文（只读） | memory_access |
| `D4.5_hidden_state` | 用户隐性状态 bucket 只读快照（tag-gated，Phase 4） | tag: body_intimate / physical_closeness |
| `D5_body_projection` | 她的身体感知投影 | 轴3，boundary_level + yexuan_tension |
| `D6_scene_anchors` | 场景状态 + 临时符号锚点 | dream-local |
| `D7_dream_tension` | 梦内情绪张力（live，dream-local） | — |
| `D8_dream_director` | 梦境导演注记（允许动作/环境）+ 逃生协议提醒 | boundary_level |
| `D9_dream_history` | 梦内滚动短上下文（不过现实 sanitizer） | — |
| `D10_user_message` | 她当前梦内输入 | — |

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
| `6f_dream_afterglow` | 即时情绪余韵（剥场景/世界/身体） | summary 和 loader 已有，现实 prompt pipeline 尚未接 6f 注入；afterglow 当前只写 hidden_state（via integrator），不注入文本层 |
| `6g_dream_impression` | 低权"我好像在梦里…"模糊印象 | 慢衰减 |

当前只有 `6g_dream_impression` 从 dream 域读入现实 prompt，并进入 token 裁剪表最早裁剪。
`6f` loader 仍是独立可测模块，但未接 `core/pipeline.py` / `core/prompt_builder.py`。
afterglow 回流路径（Phase 6）: Dream exit → `wire_afterglow_from_summary()` → `integrate_afterglow_and_save()` → hidden_state.json（不经过 prompt pipeline）。

---

## 四、三层回流（梦 → 现实，唯一出口）

| 产物 | 内容 | 去向 | 谁能读 |
|---|---|---|---|
| **archive 原文** | 梦境全文 | `archive/dream_*.jsonl` | **仅她复盘，任何 loader 永不读** |
| **afterglow summary** | 剥场景的情绪余韵 | `summaries/*.summary.json` | `wire_afterglow_from_summary()` 读取后转写 `afterglow_residue.json`（Phase 6 已接线）；summary 本身 never_retrieve |
| **impression residue** | 低权模糊印象 | `impressions/{uid}.json` → 6g | impression_loader 唯一读 |

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
| `POST /dream/exit` | ✅ 已有 | 硬退出；无条件穿透并立即关闭梦境 |
| `GET /dream/state` | ✅ 已有 | 只读 UI 投影：状态、身体数值、张力、场景和象征锚 |
| `GET /dream/settings` | ✅ 已有 | 读取 per-uid 偏好默认值 |
| `PATCH /dream/settings` | ✅ 已有 | 枚举校验后的局部更新；`world_layer` / `lucid_mode` 仅影响下一场梦 |

### dream_state.json 字段

`user_id` / `status` / `dream_id` / `frozen_world` / `lucid_mode` / `context_snapshot` / `body_state{heat,sensitivity,tension}` / `emotional_tension`(叶瑄的，0–1)

### dream_settings.json 字段（UI 设置页对应）

| 字段 | 取值 |
|---|---|
| `memory_access` | card_only / relationship_summary / full_snapshot |
| `boundary_level` | vague / body_perceptible / numbers_visible / threshold_break |
| `world_layer` | reality_derived / abo / vampire / cat / flower_bud / custom |
| `lucid_mode` | lucid_shared / non_lucid |
| `enable_dream_lorebook` | bool |
| `jailbreak_preset` | `characters/dream_presets/{name}.md` 的安全 ASCII 名；缺失时回退 `default` |

> 破限属梦境独立源（D0），不在 settings 暴露开关（独立 pipeline 天然不漏进现实，工程上最不用操心）。

### Emerald-client 当前接线

同级项目 `D:\ai\Emerald-client` 已把 Dream 作为正式 overlay 接入，不再是 mock preview：

- React API：`src/shared/api/dream.ts` 通过 Tauri invoke 调用 Dream 端点；
- Rust bridge：`src-tauri/src/lib.rs` 提供 `dream_get_state` / `dream_enter` / `dream_chat` /
  `dream_exit` / `dream_get_settings` / `dream_update_settings`；
- UI：`src/windows/dream/` 提供入梦、梦内聊天、WAKE / Esc 硬退出、状态侧栏、偏好和帮助窗；
- 状态轮询：`GET /dream/state` 每 8 秒刷新，显示她的 body 数值、叶瑄梦内张力、场景和象征锚；
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
8. **有界耦合**：叶瑄情绪耦合 dream-local、单轮封顶（≤0.15）、梦关即清，永不写现实 mood_state。

---

## 九、合同段（invariant / current / future）

### INVARIANT（绝不能破）
- 现实记忆冻结只读；梦内绝不回写现实（含 mood_state 一字节不碰）
- impression/afterglow store 物理隔离（reflect/consolidate/retrieve 无读取路径）
- body_state 始终 dream-local、梦关即清；threshold_break 只解**数值上限**，不解生命周期/隔离/逃生
- 数值只在 `numbers_visible+` 进叶瑄上下文
- `hard_exit` 绝对：任何档位 / non_lucid / 叙事挽留 / config 都不能削弱
- 身份在世界之上；世界入梦冻结、整场不可切
- non_lucid 只改叶瑄虚构内自知；系统层仍标记 dream，墙 + 逃生不变

### CURRENT（当前实现）
见第二节功能清单。三轴 + 四档 + 六世界 + 软硬双出口已落地；三层产物均会生成，
但现实 prompt 当前只接 `6g_dream_impression`，`6f_dream_afterglow` 仍未接线。测试数量以
`tests/test_dream_*.py` 当前收集结果为准，不在合同文档里固定计数。

**现实侧 loader 不引用 dream 路径——已有自动测试护栏**
`tests/test_dream_isolation_guard.py` 静态扫描 `core/memory/*.py`、`core/pipeline.py`、
`core/prompt_builder.py` 的非注释源码行，断言不出现 `dreams/`、`impression_loader`、
`afterglow`、`dream_summary`、`dreams/archive` 等 dream 域标记。
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

- **F1 虚构场景**：叶瑄读 6g 印象后，可能在现实回合**编造**一个梦场景，被正常 capture 当事实。靠 6g 文案约束缓解，**非结构性防御**。低危（明确框成梦），可接受。
- **vocab_strip 是手维护黑名单**：新世界/新术语忘填 `vocab.json` 会静默漏。但因承重墙是 store 隔离，仅在 F1 边界才有影响，不致命。**任何人不得把它当墙用。**
- **身份稳定性测试是弱代理**：只断言人称正确 + 依恋关键词在场，真验证靠实际游玩。
- **DREAM_LOCKED 预留未实现**：无系统级软退锁。
- **dream settings 仍保留旧路径降级读**：`_LAYOUT_DREAM = "v1"` 后写入
  `data/runtime/dreams/{char_id}/settings/{uid}.json`，读取仍可通过 `for_read()` 回退旧
  `data/dreams/settings/{uid}.json`。清理旧文件前先看 fallback 观测。
