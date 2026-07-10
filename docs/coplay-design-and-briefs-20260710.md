# Coplay（陪玩模式）设计 + 施工工单

> 状态：设计文档 + 待执行工单（Brief 38–42）。2026-07-10。
> 讨论来源：用户方案（屏幕识别 + 内部读取陪玩、不剧透且角色不知情、自动开启桌宠、
> 关键节点主动说话、梦境式记忆隔离、预留"真一起玩"接口）。
> 落地时本文档拆出正式 `docs/coplay.md`，本文保留设计决策记录。

---

## 一、定位

Coplay 是 `docs/interaction-event-model.md` §五预留的 **ActivitySession 的第一个实例**
（`kind=activity` + 生命周期 session）。做它的同时顺手把 ActivitySession 从 v0.2+
预留变成最小实现——但**只实现 coplay 需要的最小状态机，不做通用 dispatch router**。

两条产品线，明确分开：

| | 陪玩模式（本期实现） | 一起玩接口（只定契约） |
|---|---|---|
| 角色定位 | 观察者 + 评论者（陪伴） | + 行动者（互动，另一个玩家） |
| 模块 | observer + commentator | + actor（空壳 Protocol） |
| 时机 | Brief 38–42 | 等 agent 能力成熟，另立 brief |

---

## 二、关键设计决策（拍板 + 理由）

### D1. 不新增 realm，coplay 是 reality 内的 ActivitySession

realm 维持 `reality/dream` 二元。理由：游戏是**真实共同经历**，不是虚构；用户之后会说
"上次我们打的那个 boss"，完全隔离会造成角色失忆，伤害大于剧透。记忆隔离档位定为
**介于 dream（不固化）与 reality（全固化）之间的第三档**：

- 游玩中每轮**不走** mid_term/episodic/identity 主链（复用 `web_echo`/`dream_echo`
  同款跳过通路，新增 `coplay_echo` 标志）；
- session 结束后 summarizer 产出"大概 + 清晰词句"写入**按游戏分桶的持久 game_log**
  （新存储，非主记忆）；
- 回到现实后走 afterglow 式软提示（复用 `dream_exit_afterglow` 模式）+
  聊到该游戏时 tag 门控注入 game_log。

### D2. 剧透压制 = 系统知情 / 角色不知情 的双层设计

模型训练数据里就有大作剧情，"不知情"只能是**行为约束**：

- **进度栅栏**：系统侧维护 per-game 进度标记（章节/区域/boss）；
- prompt 硬约束：「你和她第一次一起玩，只知道到目前为止发生的事，禁止预测、
  禁止暗示后续」；
- 未来（v2）攻略资料若进系统，必须按进度打 tag、只注入 ≤ 栅栏的条目
  （lore 门控同款）；v1 **不引入任何攻略资料**——没有资料就没有穿栅栏风险，
  模型自身知识靠 prompt 压制 + 实测校准（Brief 41 含泄漏率 mini eval）。
- 冷门游戏是简单情形（模型真不知道），**大作才是困难情形**，eval 用大作测。

### D3. 关键节点检测：结构化信号优先，视觉兜底

优先级：Steam 注册表/存档/成就 ＞ 图像差分启发式 ＞ VLM。VLM 只在"画面剧变且
廉价信号无法解释"时调用一次（额度紧张是硬约束）。

### D4. 内部读取红线（反作弊）

只读**文件**（存档、日志、appmanifest），**禁止读游戏进程内存**——VAC/EAC 游戏上
读内存有封号风险。actor（输入注入）风险更高，属于未来接口的红线，写进契约注释。

### D5. 主动说话的静默规则

打扰比说错更劝退。战斗中（画面变化率高）闭嘴；菜单/挂机/过场结束/成就解锁才可开口；
频率上限 + 复用 scheduler gating。

---

## 三、模块划分

```
core/coplay/
  session.py      # CoplaySession 状态机: off → armed → active → closing → off
  watcher.py      # 进程/Steam 检测, armed→active 转换, 拉起桌宠
  observer.py     # 截屏+差分+OCR(+VLM兜底)+存档watch → GameMoment 结构化事件
  game_state.py   # per-game 进度档 + game_log 桶 (经 sandbox.get_paths)
  commentator.py  # GameMoment → 静默规则/gating → 主动开口 trigger
  actor.py        # 【空壳】未来"一起玩"接口, 只有 Protocol + 红线注释
```

对接现有机制：session 生命周期事件走 scheduler trigger（stimulus）；对话走正常
reality pipeline + 新 prompt 层 `coplay_context`（带 `_layer` 字段）；记忆经
turn_sink → post_process 的 `coplay_echo` 跳过通路。

---

## 四、施工工单

**依赖图**：`38 → {39 ∥ 40} → 41 → 42`（39/40 可并行；41 需 38+40；42 需 41）。

---

### Brief 38 —— Coplay 骨架：状态机 + 配置 + 开关 + echo 通路

1. `core/coplay/session.py`：状态机 `off/armed/active/closing`，单例、线程安全
   （参考 dream_state 的实现风格）。`armed` = 用户开了陪玩模式但没检测到游戏；
   `active` = 游戏中。
2. 配置块 `coplay:`（enabled、pet_launch_cmd、poll_interval、游戏进程白名单补充表——
   非 Steam 游戏靠手动配置）。
3. admin router `admin/routers/coplay.py`：GET 状态 / POST arm / POST disarm
   （scope 复用现有 profile 体系，见 `docs/security.md`）。
4. `coplay_echo` 通路：`WriteEnvelope` 加标志（或 post_process 参数，对齐
   `web_echo` 现有做法），`fixation_pipeline.handler_summarize_to_midterm` 同路跳过
   mid_term/episodic/identity。**active 状态下 QQ/desktop 的每轮对话自动带上此标志。**
5. `actor.py` 空壳 Protocol：`observe() / act() / capabilities()` 签名 + docstring 红线
   （反作弊、联机 ToS、从 mod API 游戏切入）。**本 brief 只写接口不写实现。**

验收：状态机单元测试；active 轮次不产生 mid_term/episodic/identity 写入（测试断言）；
short_term 照常写（对话连续性）；文档新建 `docs/coplay.md` 骨架章节。

---

### Brief 39 —— 游戏检测 watcher + 自动拉起桌宠（依赖 38）

1. `core/coplay/watcher.py`：armed 状态下轮询——
   - psutil 进程扫描（steam.exe 等平台进程 + 配置白名单）；
   - Steam 当前游戏：注册表 `HKCU\Software\Valve\Steam\RunningAppID`
     （⚠️ CC 需实机验证该键的存在与语义，不确定处 fail-open 退回进程名匹配）；
   - appid → 游戏名：解析 `steamapps/appmanifest_<appid>.acf` 的 `name` 字段
     （库路径进配置）。
2. 检测到游戏 → session 转 `active`，记录 game_id/game_name；游戏退出 → `closing`
   （触发 Brief 42 的收尾链）。
3. 自动拉起桌宠：`active` 时若 desktop WS 未连接（`desktop_ws.is_connected()`），
   用 `coplay.pet_launch_cmd` spawn 桌宠进程（fail-open：拉不起来只打日志，
   不阻塞陪玩）。
4. 接入 scheduler：新 trigger `core/scheduler/triggers/coplay_watch.py`
   （参考 garden_water 的轮询 trigger 写法），不发言、只推状态机。

验收：模拟进程列表的单元测试；arm→启动游戏→active→关游戏→closing 全链路日志可见；
非 Steam 游戏走白名单路径可用。

---

### Brief 40 —— Observer 感知层（依赖 38，与 39 并行）

1. 截屏：`mss`（服务端与游戏同机，单用户前提成立）。采样率低频（配置，默认 5–10s）。
2. **图像差分剧变检测**（直方图/感知哈希，纯本地零成本）：输出
   `scene_change / combat_likely（持续高变化率）/ idle（长时间低变化）`。
3. OCR：本地引擎（rapidocr 或 paddleocr，CC 按 Windows 兼容性拍板）抓字幕/任务文本/
   死亡画面关键词。OCR 结果只进 GameMoment，不直接进 prompt（防注入 + 防剧透原文）。
4. VLM 兜底：`model_registry` 增加 vision preset（⚠️ 前置依赖：确认可用的多模态
   preset 与成本，见 `docs/model-presets.md`；没有就留接口、配置默认关）。
   仅当 scene_change 且 OCR 无法解释时调用一次，结果限一句话场景描述。
5. 存档 watch：配置 per-game 存档目录，mtime 变化 → `save_point` moment。
   **只读文件，禁止进程内存读取（D4 红线，代码注释写明）。**
6. 产出统一 `GameMoment` dataclass：`kind(scene_change/death/achievement/save_point/idle/combat_start/combat_end) + summary + ts`，推给 session 的 moment 队列。

验收：差分检测用录屏帧序列做离线单元测试；OCR/VLM 均 fail-open；moment 队列有
上限防堆积。

---

### Brief 41 —— 陪玩对话回路 + 主动开口 + 剧透栅栏（依赖 38+40）

1. `core/coplay/game_state.py`：per-game 进度档
   `data/.../coplay/{game_id}/state.json`（**必须经 `sandbox.get_paths()`**）：
   进度标记（observer 推断 + 用户对话中提到的自动更新）、最近 moments、高光时刻。
2. 新 prompt 层 `coplay_context`（**必须带 `_layer` 字段**，进 prune 序列，位置和
   预算 CC 参考 `docs/prompt-layers.md` 拍板）：注入当前游戏名、进度标记、最近 2–3 个
   moment、**D2 的不剧透硬约束文案**。active 状态才注入。
3. 主动开口：`commentator.py` 消费 moment 队列 → 静默规则（D5：combat 中丢弃或
   延迟；idle/成就/死亡/save_point 可触发）→ 频率上限（配置，默认 ≥5min 间隔）→
   经 scheduler proposer/gating 走正常 stimulus 链发言（`recall_policy="none"`，
   RC6 同款——由头已在种子 prompt 里，不需要旧记忆带偏）。
4. **剧透泄漏 mini eval**（本 brief 验收核心）：`tests/run_coplay_eval.py`——
   选 2–3 个大作，构造 15–20 条诱导性问题（"这 boss 后面是不是XX""结局是什么"），
   断言回复不含栅栏后专名（每游戏 5–10 个手工黑名单词）。跑出基线泄漏率写进
   `docs/coplay.md`；泄漏率高再考虑输出侧守卫（v2，本期不做）。
5. tag_rules 若有改动，跑 `python tests/run_eval.py`（硬规则 4）。

验收：eval 泄漏率有数字；战斗中无主动发言（模拟 moment 序列测试）；
`coplay_context` 层被 prune 逻辑识别。

---

### Brief 42 —— 记忆回流：session 收尾 + game_log + afterglow（依赖 41）

1. `closing` 状态触发收尾链（scheduler trigger 或 watcher 直调）：
   - summarizer 用一次 LLM 把本 session 的 moments + 对话浓缩成
     "大概经过 + 3–5 条清晰词句（原话引语）"——**引语存 raw quote，防摘要腔
     （对应 briefs-36-37 展望 §2 的坍缩通道）**；
   - 写入 `data/.../coplay/{game_id}/log.md`（追加式，含日期与进度标记）；
   - **同步 `provenance_log.append()`（硬规则 6）**。
2. afterglow 回流：复用 `dream_exit_afterglow` 的模式写 coplay 版——session 结束后
   现实 prompt 带一条软提示（"刚陪她打完XX，还有点意犹未尽"体裁），随时间衰减，
   fail-closed。
3. tag 门控注入：现实聊天中命中该游戏名/别名 tag 时，注入对应 game_log 摘要
   （新层或并入 `coplay_context`，带 `_layer`）。别名表进 game_state。
4. 主记忆验证：整个 session 结束后，mid_term/episodic/identity 无本 session 逐轮内容
   （只有 game_log 桶 + afterglow），测试断言。

验收：全链路集成测试（arm→玩→closing→afterglow→次日聊起该游戏能召回 game_log）；
文档 `docs/coplay.md` 补全 + `docs/memory.md` 增补 coplay_echo 通路说明。

---

## 五、遗留的不确定处（CC 落地时验证，验证不了就回报）

1. `RunningAppID` 注册表键在当前 Steam 客户端的存在与实时性（Brief 39）。
2. 可用的 vision preset 及其单次调用成本（Brief 40，默认关不阻塞）。
3. prompt 压制的实际泄漏率——Brief 41 的 eval 就是为了拿这个数字，高了再上守卫。
4. OCR 引擎在 Windows + 打包环境的兼容性（Brief 40 CC 拍板）。
5. 桌宠 spawn 方式（exe 路径 or npm dev）取决于 Emerald-client 当前形态（Brief 39 进配置解决）。

## 六、明确不做（防提前实现）

- 通用 ActivitySession dispatch router / `kind=activity` 全量事件模型（仍是 v0.2+）；
- 攻略/wiki 资料摄入与进度 tag 门控（v2，依赖 Brief 41 泄漏率数据）；
- 输出侧剧透守卫黑名单自动生成（v2，同上）；
- actor 的任何实现（输入注入/内存读取），包括"只在单机游戏试试"。
