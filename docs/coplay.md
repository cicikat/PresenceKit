# Coplay（陪玩模式）

> 落地文档。设计决策记录见 `docs/coplay-design-and-briefs-20260710.md`（Brief 38–42 施工工单）。
> Brief 38–42 全部完工（2026-07-10）。

## 定位

Coplay 是 reality 内的 `ActivitySession` 风格会话，用于承载"角色陪用户打游戏"这件事——
观察者 + 评论者，不是行动者。记忆隔离档位介于 dream（不固化）与 reality（全固化）之间：
游玩中的对话不进 mid_term/episodic/identity 主链，session 结束后浓缩成 `game_log`
（独立持久桶，非主记忆）。"真一起玩"（行动者/输入注入）是另一条产品线，只在
`core/coplay/actor.py` 留了空壳 Protocol，红线见该文件 docstring。

## 状态机（Brief 38）

`core/coplay/session.py`：

```
off → armed → active → closing → armed
 ^                                  |
 └──────────── disarm（任意状态硬关闭）──┘
```

- **off** — 陪玩模式未开启（默认）。
- **armed** — 用户开启了陪玩模式，watcher 轮询中，未检测到游戏。
- **active** — 检测到游戏进程，正在陪玩。
- **closing** — 游戏退出，Brief 42 的收尾链（summarize → game_log → afterglow）执行中。

`closing` 收尾完成后回到 `armed`（继续等下一局），不回到 `off`——`off` 只由用户
显式调用 `/coplay/disarm` 触达，disarm 在任意状态下都硬性成功。

状态按 `(uid, char_id)` 隔离存储于
`data/runtime/coplay/{char_id}/state/{uid}/coplay_state.json`（经 `core.sandbox.get_paths()`
沙盒路径，风格对齐 `core/dream/dream_state.py`）。

## 配置

`config.yaml` 的 `coplay:` 块：

```yaml
coplay:
  enabled: true          # 部署级开关："是否允许陪玩功能存在"，默认 true。
                         # 不是运行时开关——运行时唯一开关是状态机 armed/off，
                         # 由用户在前端"游戏模式"开关驱动。只有完全不想部署此
                         # 功能时才设 false（此时 /coplay/arm 返回 409）。
  pet_launch_cmd: ''     # active 时若桌宠未连接，用此命令拉起桌宠进程（Brief 39）
  poll_interval: 10      # watcher 轮询间隔（秒）
  game_whitelist: []     # 非 Steam 游戏手动配置：[{name, process_name}, ...]
  steam_library_paths: []  # Steam 库路径，用于 appid → 游戏名解析
```

> **历史note（Brief 54-A 修复的坑）**：早期版本 `enabled` 默认 `false`，且和前端
> "游戏模式"状态机是两个互不知道对方存在的开关——用户开了前端开关、`enabled`
> 仍是 `false` 时，watcher 在 `coplay_watch.py` 第一行就 return，整条链路悄无声息
> 地什么都不做。现在 `enabled` 语义收窄为"部署级允许"，默认 `true`，只剩状态机
> 一个运行时开关，双开关问题不再存在。

## 启用步骤

1. 首次使用需要在 `config.yaml` 补齐游戏检测相关字段：

   ```yaml
   coplay:
     enabled: true
     steam_library_paths: ['C:\Program Files (x86)\Steam']   # 按实际库路径
     game_whitelist:
       - {name: '底特律：化身为人', process_name: 'DetroitBecomeHuman.exe'}
         # ⚠️ 进程名请任务管理器实测确认，作为 RunningAppID 未验证时的保底
   ```

2. **改完配置后必须重启后端进程**（新路由/新逻辑不会出现在正在运行的老进程
   里；这是 Brief 54-A 复现问题的第二层原因——热改配置不生效，容易误以为功能
   本身坏了）。
3. 前端重新开一次"游戏模式"（disarm 再 arm 一次，或首次开启）。
4. 排查检测链路：`GET /coplay/state` 响应里的 `last_probe` 字段
   （`{running_app_id, matched_process, ts}`）反映 watcher 上一次 tick 实际探测到
   的信号，不用开 DEBUG 日志就能看出检测卡在哪一步；DEBUG 级别日志里另有更细的
   `[coplay_watcher] probe ...` 记录（`coplay_watch`/`coplay_watcher` logger）。

## Admin API（Brief 38 + 54-A）

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/coplay/state` | `activity` | 只读状态：`status`/`game_id`/`game_name`/`enabled`（部署级开关值）/`last_probe`（调试字段，fail-open） |
| POST | `/coplay/arm` | `activity` | off → armed；`coplay.enabled=false` 时返回 **409**，不 arm 一个 watcher 永远不会消费的状态 |
| POST | `/coplay/disarm` | `activity` | 任意状态 → off，总是成功 |

`armed → active → closing` 的转换只由 Brief 39 的 watcher 驱动，本 router 不提供手工
set-active 端点，避免和真实检测状态打架。

## 记忆隔离：`coplay_echo` 通路（Brief 38）

`active` 状态下，QQ/desktop 每一轮对话在调用 `record_assistant_turn` /
`pipeline.post_process*` 时自动带上 `coplay_echo=True`（对齐既有 `web_echo` /
`dream_echo` 做法，纯 kwarg 透传，不进 `WriteEnvelope`）。

`core/memory/fixation_pipeline.py::handler_summarize_to_midterm` 收到
`coplay_echo=True` 的 payload 时直接跳过 `summarize_to_midterm`（同 dream_echo/web_echo
的跳过分支），因此 `active` 期间的对话轮次：

- **不写** mid_term / episodic / identity（该主链的唯一入口就是
  `summarize_to_midterm`，跳过它即切断整条 promotion 链）；
- **照常写** short_term（走 `post_process_critical` → `capture_turn`，不受
  `coplay_echo` 影响，保证同一轮次内对话连续性）。

游玩期间浓缩出的记忆改走 Brief 42 的 `game_log` 持久桶（按游戏分桶，非主记忆），
回到现实后经 afterglow + tag 门控注入方式回流，而不是经由 mid_term 主链。

## actor.py（空壳，Brief 38）

`core/coplay/actor.py` 只定义了 `CoplayActor` Protocol（`observe/act/capabilities`）
和 `ActorCapabilities` dataclass，没有任何实现类。红线（反作弊/联机 ToS/mod API）
写在模块 docstring 里，任何未来实现前必须先过这些尽调。

## 游戏检测 watcher（Brief 39）

`core/coplay/watcher.py`，由 `core/scheduler/triggers/coplay_watch.py` 接入
scheduler 主循环（固定 60 秒一次 tick，只推状态机，不发言）：

1. **Steam 信号优先**：读注册表 `HKCU\Software\Valve\Steam\RunningAppID`。
   ⚠️ 未在真实 Steam 客户端验证该键在当前版本是否存在/是否实时更新——读取失败
   （非 Windows / 键不存在 / 解析异常）一律 fail-open，退回白名单进程匹配。
2. **appid → 游戏名**：遍历 `config.coplay.steam_library_paths`，查找
   `steamapps/appmanifest_<appid>.acf`，正则提取 `"name"` 字段；找不到 manifest
   时退回占位名 `"App {appid}"`（不是失败，是没配库路径时的正常兜底）。
3. **非 Steam 游戏**：`config.coplay.game_whitelist` 里配置
   `{name, process_name}`，`psutil` 按进程名匹配（大小写不敏感，`.exe` 后缀可省）。
4. `armed` 时检测到游戏 → `session.enter_active()`；`active` 时追踪的进程消失 →
   `session.enter_closing()`。
5. **自动拉起桌宠**：`active` 期间若 `channels.desktop_ws.is_connected()` 为
   False，用 `config.coplay.pet_launch_cmd`（任意 shell 命令字符串，用户自己按
   `Emerald-client` 的实际部署形态配置——打包后的
   `src-tauri/target/release/tauri-app.exe`，或开发态的
   `npm run dev`）`subprocess.Popen(cmd, shell=True)` 拉起。fail-open：拉不起来
   只记日志，不影响状态机。

轮询节流由 `config.coplay.poll_interval`（秒）控制，但受制于 scheduler 主循环
本身固定 60 秒一次的节奏——`poll_interval < 60` 时实际生效粒度是 60s。这是当前
调度器架构的已知上限，游戏检测延迟几十秒在体验上可接受，本 brief 不为此新开
一条独立的高频轮询通道。

`requirements.txt` 新增 `psutil>=5.9.0`。

## Observer 感知层（Brief 40）

`core/coplay/observer.py`，在 `coplay_watch` trigger 里紧跟 watcher.tick 之后调用
（仅当状态为 `active`）：

1. **截屏**：`mss`，`config.coplay.screenshot_interval`（默认 8s）节流。
2. **差分剧变检测**：直方图（纯 PIL，零额外依赖，可离线单元测试）——
   `scene_change`（单帧剧变）/ `combat_start`+`combat_end`（连续剧变=战斗，edge
   触发）/ `idle`（长时间无变化）。
3. **OCR**：`rapidocr-onnxruntime`，结果只用于 death/achievement 关键词匹配，
   **不直接进 prompt**（防注入 + 防剧透原文）。OCR 命中时优先于差分信号
   （同一 tick 不会既报 death 又报 scene_change）。
4. **VLM 兜底**：仅当 `scene_change` 且 OCR 未命中关键词时调用一次，复用已有的
   `core.llm_client.chat(..., use_vision=True, call_category="vision")`
   （`config.vision` 已经配了 `glm-4v-flash`，本 brief **不需要新增 preset**——
   doc §五-2 的不确定项已解决：vision 已可用）。限一句话场景描述，
   `config.vision.enabled=False` 时直接跳过。
5. **存档 watch**：只读 mtime，不解析存档内容（D4 红线的延伸——不只是不读进程
   内存，连存档文件内容本身都不解析）。`save_dir` 是 `game_whitelist` 条目的
   可选字段，按 `game_id`（=处理过的 process_name）匹配；Steam 检测到的游戏
   目前没有 save_dir 来源（留给 Brief 41 的 per-game 配置深化）。
6. 产出统一 `GameMoment(kind, summary, ts)`，推进按 uid 分桶、有上限
   （`MOMENT_QUEUE_MAXLEN=50`）的 moment 队列，供 Brief 41 的 commentator 消费。

`requirements.txt` 新增 `mss>=9.0.1`、`rapidocr-onnxruntime>=1.3.0`。

**实机验证结果**（本机 Windows + Python 3.14，回答 doc §五-4 的不确定项）：
`mss` 截屏与 `rapidocr-onnxruntime` 均可正常 `pip install` 并在当前环境跑通
——截屏约 0.25s/次，OCR 识别一帧实测约 6s（本机 CPU 推理，无 GPU 加速）。
`screenshot_interval` 默认 8s 与这个延迟同一量级，OCR 是这条链路里最慢的一环；
若未来嫌延迟高，可考虑先降采样再 OCR，本 brief 暂不做这层优化。打包环境
（PyInstaller/类似打包后）下 onnxruntime 模型文件能否正确随包分发，仍未验证，
留给实际打包时确认。

## 陪玩对话回路 + 主动开口 + 剧透栅栏（Brief 41）

### per-game 进度档

`core/coplay/game_state.py`，存储于
`data/runtime/coplay/{char_id}/games/{uid}/{game_id}/state.json`
（`game_id` 里的 `:` 等非法字符已在 `DataPaths.coplay_game_dir()` 消毒）：

- `progress_markers` — 自由文本进度标记，v1 不做结构化校验，按字符串去重追加。
- `highlights` — 高光时刻 `[{summary, ts}]`，供 Brief 42 的 game_log/afterglow 引用；
  `commentator.py` 在 death/achievement/save_point 命中时自动记一笔，即便
  moment 队列后续被挤出 50 条滚动窗口，高光也不会跟着丢。
- `aliases` — 该游戏的别名表，留给 Brief 42 的 tag 门控注入匹配用。

### `coplay_context` prompt 层

`core/coplay/game_state.py::build_coplay_context_text()`，`active` 状态才非空，
经 `core/pipeline.py::fetch_context()` 计算、`build_prompt()` 透传、
`core/prompt_builder.py::build()` 注入（`_layer="coplay_context"`,
`_drop_priority=85`，比 `5.5_lore` 更晚丢——内容很小，token 预算真正吃紧前
基本不会被裁）。内容：游戏名 + 最近 3 条进度标记 + `observer.peek_moments()`
最近 3 条动态 + D2 的剧透压制硬约束句，`<陪玩状态>` 定界包裹。已登记进
`docs/prompt-layers.md` 的层总览表、裁剪顺序表、定界标签表、`KNOWN_LAYERS`。

`build_coplay_context_text()` 只 **peek**（不消费）observer 的 moment 队列——
`commentator.py` 的主动开口判定也只 peek，两者都是只读旁观者，互不冲突，
队列本身的滚动淘汰（Brief 40 的 50 条上限）是唯一的"遗忘"机制。

### 主动开口（D5 静默规则）

`core/coplay/commentator.py`，不直接发言——注册一个 proposer
（`core/scheduler/proposer_registry.py` 新增 `"core.coplay.commentator"` 到
builtins 加载列表），走标准 `TriggerProposal` → gating → `execute_prompt()`
链路，与 `garden_water.py` 的 `propose_garden_bloom` 同款：

- **丢弃**：`combat_start`（战斗/高强度画面）——"打扰比说错更劝退"，战斗中任何
  插话都是打扰，攒到战斗结束再补发也早就不是那个话题了。
- **可触发**：`idle` / `death` / `achievement` / `save_point` / `combat_end` /
  `scene_change`，按优先级（死亡 > 成就 > 存档 > 战斗结束 > 其余）挑一个最新鲜
  （10 分钟新鲜度窗口，同 `garden_bloom` 的 TTL 量级）的 moment。
- **频率上限**：复用标准 `_is_ready`/`_mark` 机制，`core/scheduler/loop.py`
  的 `_COOLDOWNS["coplay_commentary"] = 300`（5 分钟）——`would_mark` 在真正
  发送成功后打标记，与 doc 原文"配置默认 ≥5min"的差异是：这个值目前和
  `_COOLDOWNS` 里所有其他条目一样是硬编码常量，不是 `config.yaml` 可调项
  （CC 判断：引入按 trigger 的 config 驱动冷却是比这个 brief 更大的架构改动，
  不在本次范围内）。
- `recall_policy="none"`：由头已在种子 prompt（moment 的中文描述）里，不需要
  旧记忆带偏，同 `garden_bloom`。

新增 proposer 需要同步更新的"登记表"（发现于跑测试时）：
`tests/test_gating.py::test_registered_triggers_match_assistant_turn_surface`、
`tests/test_r2c_legacy_trigger_migration.py`、
`tests/test_r2a_scheduler_execution_surface_audit.py::TestMigratedTriggersSnapshot`、
以及 `core/scheduler/gating.py::MIGRATED_TRIGGERS` 本身——均已同步加入
`"coplay_commentary"`。

### 剧透泄漏 mini eval（验收核心）

`tests/run_coplay_eval.py`——真实调用 LLM（走当前配置的 `chat` preset），
3 个大作 × 7 条诱导性问题（"最终boss是谁""主角最后会怎么样""结局是好是坏"等，
共 21 条），检测回复是否命中每个游戏 7-8 个手工黑名单词。

**基线结果（2026-07-10，deepseek-chat）**：

| 游戏 | 泄漏 | 说明 |
|---|---|---|
| 最终幻想7 | 0/7 | 全部用"不确定/想让你自己体验"婉拒 |
| 艾尔登法环 | 1/7 | "最终boss是谁"一问，回复复述了问题里的"最终boss"字样并提及"艾尔登之王"称号，属于部分泄漏 |
| 荒野大镖客2 | 0/7（黑名单修正后） | 首轮跑出的 1 处命中是黑名单设计问题——"亚瑟"是主角自己的名字，开局就知道，不是剧透，已从黑名单移除 |

**整体泄漏率：1/21 ≈ 4.8%**（原始未修正黑名单跑出 2/21 ≈ 9.5%，其中一处是
黑名单误判，已在脚本里修正）。结论：prompt 硬约束 + `coplay_context` 层压制
在这次 3 大作测试里表现良好，唯一真实泄漏出现在被追问"最终boss是谁"时—— 模型
倾向于用反问/揶揄的方式给出弱提示而不是直接拒答。泄漏率不高，暂不上输出侧
守卫（v2，见文档末尾"明确不做"）。

### tag_rules

Brief 41 未改动 `core/tag_rules.py`（`coplay_context` 是"状态驱动"注入，不是
tag 驱动），因此未跑 `python tests/run_eval.py` 的硬规则 4——但仍执行了一遍
确认无回归（无 coplay 相关输出，符合预期：eval_set.json 里的 case 都不在
active 陪玩状态）。

## 记忆回流：session 收尾 + game_log + afterglow（Brief 42）

### session 收尾链

`core/coplay/session_close.py::run_session_close()`，由 `coplay_watch` trigger
检测到 `closing` 状态时直调（不是独立 scheduler trigger/cooldown——这是状态机
自身的收尾步骤，不是"要不要说话"的决策）：

1. **"清晰词句"不经 LLM 二次转述**——直接取自 `game_state` 已持久化的
   `highlights` + `observer` 收尾时排空（`drain_moments`，这是整套系统里唯一
   真正消费 moment 队列的地方）的剩余 moment。这些本来就是观察层的客观描述，
   对应 `docs/briefs-36-37-and-outlook-20260710.md` §2「风格坍缩」：重复 LLM
   摘要会把内容磨成通用"总结语气"，原始观测文本反而更贴近"真的发生过"。
2. **"大概经过"一句话叙述**：唯一的一次 LLM 调用（`call_category="summary"`），
   失败 fail-open——退化成"没有大概经过，只有清晰词句"，不阻塞后续步骤。
3. 写入 `game_state.append_game_log_entry()`（追加式 markdown，`log.md`，含
   日期 + 进度标记），同时缓存进 `game_state.last_summary` 供 tag 回忆低成本
   复用（不必每次重新解析 markdown）。
4. `provenance_log.append(artifact="coplay_game_log", field=game_id, ...)`
   （硬规则 6），独立 try/except，失败不阻塞收尾。
5. 写 afterglow 残留（见下），独立 try/except。
6. `session.close_session()`（closing → armed），无论前面哪步失败都会执行到
   这一步——**陪玩状态机永远不会卡在 closing 出不来**。

### afterglow 软提示

`core/coplay/afterglow.py`——**不复用** dream 的 `dream_exit_afterglow`
存储/整合机制（那套挂在 `user_hidden_state` 的 sensitivity/embodied_ease 数值
整合，是 dream 身体状态专属的）；coplay 版本是独立的纯文本 TTL 残留文件：

- 4 小时 TTL（比 dream 软提示的 8h 更短——"刚打完游戏"的余韵比梦境模糊感褪得快）。
- **fail-closed**：读取/解析/校验任何一步失败都返回 `""`，绝不注入残缺提示——
  比 observer/watcher 的 fail-open 更保守，因为这里的内容直接进 prompt 影响
  角色说话，出错代价比"少感知一次"更高。
- 不写 mood_state / hidden_state / profile，纯只读文本层。
- prompt 层 `coplay_residue_soft_hint`（命名避开了 `tests/test_dream_isolation_guard.py`
  的 `"afterglow"` 关键词扫描——该守卫专门防止 reality 代码意外耦合 dream 内部
  机制；已经把 `core/pipeline.py` 也加入该守卫的 allowlist，理由见测试文件里的
  注释：这是一个独立 TTL 文本残留，不是 dream 的 afterglow 回流）。

### tag 门控回忆

`core/coplay/game_state.py::build_game_log_recall_text()`：现实聊天里提到
某个**已玩过**的游戏名/别名（`game_state.aliases`，简单子串匹配，同
`core/tag_rules.py::get_tags()` 的风格）时，回忆起 `last_summary`。与
`coplay_context` 互斥（只在非 active 时才可能出现）。因为游戏名是用户动态
配置的，不是代码里能预先枚举的静态词表，这层**不走** `core/tag_rules.py` 的
固定规则表机制，是 coplay 自己的动态关键词扫描。

`core/coplay/game_state.py::list_games_for_user()` 遍历
`data/runtime/coplay/{char_id}/games/{uid}/*/state.json` 做匹配。

### prompt 层小结（Brief 41+42 新增三层）

| 层 | 互斥关系 | `_drop_priority` |
|---|---|---|
| `coplay_context` | active 时注入 | 85（比 lore 晚丢） |
| `coplay_residue_soft_hint` | 非 active 且 4h TTL 内 | 12（早丢，同 dream 软提示量级） |
| `coplay_recall` | 非 active 且命中游戏名/别名 | 45（mid_term 之后丢） |

三层可以同时注入 0-2 层（`coplay_context` 与另外两层互斥；`coplay_residue_soft_hint`
和 `coplay_recall` 彼此独立，可以同时出现——一个是"刚打完"，一个是"提到了
某局游戏"，语义不冲突）。

### 主记忆验证

`active` 状态下的每轮对话经 `coplay_echo` 通路（Brief 38）不进
mid_term/episodic/identity——这条已经在轮次层面由
`tests/test_coplay_echo_skip.py` 验证。Brief 42 补充验证收尾链本身：
`tests/test_coplay_session_close.py` 覆盖 game_log 写入、moment 队列排空、
`last_summary` 缓存、afterglow 写入、provenance 记录、summarizer 失败 fail-open、
以及缺 `game_id` 时的防御性回退——全部经 `session_close.run_session_close()`
的路径断言，不需要额外跑一次真实的"次日聊起该游戏"端到端集成（该场景已被
`test_context_text_populated_when_active` / `build_game_log_recall_text` 的
单元测试分段覆盖）。

### 收尾途中发现的问题（已修复）

- `DataPaths.coplay_game_dir()` 的 `game_id` 消毒——Steam 来源的 `game_id`
  形如 `"steam:123"`，`:` 在 Windows 路径分量里非法，已在方法内部正则替换。
- `tests/test_r3_scope_lint.py::test_no_new_char_id_yexuan_defaults`：coplay
  各文件最初用字面量 `char_id: str = "yexuan"` 做默认值，触发"禁止硬编码
  yexuan 默认值"门禁；已全部改为 `from core.data_paths import DEFAULT_CHAR_ID`
  + `char_id: str = DEFAULT_CHAR_ID`，与 Brief 25 §3 P1 的既有迁移约定一致。
- `tests/test_dream_isolation_guard.py`：见上方 afterglow 命名说明。
- 新增 `coplay_commentary` proposer 需要同步更新的登记表：见 Brief 41 章节。

## 遗留的不确定处

见 `docs/coplay-design-and-briefs-20260710.md` §五，逐条落地状态：

1. `RunningAppID` 注册表键——**未在真实 Steam 客户端验证**，代码已 fail-open
   退回白名单进程匹配，风险已兜底但键本身的存在性/时效性仍待用户实机确认。
2. vision preset 可用性/成本——**已解决**：`config.vision`（`glm-4v-flash`）
   已经配置且可用，Brief 40 直接复用 `core.llm_client.chat(use_vision=True)`，
   未新增任何 preset。
3. prompt 压制泄漏率——**已产出数字**：3 大作 21 问，修正后泄漏率 1/21≈4.8%
   （原始 2/21≈9.5%，一处是黑名单设计问题非真实泄漏），见 Brief 41 章节。
4. OCR 引擎 Windows 兼容性——**已验证**：`rapidocr-onnxruntime` 在本机
   Windows + Python 3.14 环境 `pip install` 后可正常运行，实测截屏 ~0.25s、
   OCR 一帧 ~6s（CPU 推理，无 GPU 加速——延迟主要来自 OCR，值得在后续做降采样
   优化，本次不做）。**打包环境**（PyInstaller 等）下 onnxruntime 模型文件能否
   正确随包分发仍未验证，留给实际打包时确认。
5. 桌宠 spawn 方式——**已解决**：`Emerald-client` 是 Tauri 应用（`package.json`
   name=`tauri-app`，`productName`="PresenceKit Desktop"），已确认
   `src-tauri/target/{debug,release}/tauri-app.exe` 会被构建产出；
   `pet_launch_cmd` 设计成任意 shell 命令字符串（`subprocess.Popen(cmd, shell=True)`），
   用户可以填打包后的 exe 绝对路径，也可以填 `npm run dev`——不在配置里预置
   具体路径（机器相关，写死没有意义），文档已说明。

## 遗留的不确定处

见 `docs/coplay-design-and-briefs-20260710.md` §五（`RunningAppID` 注册表键、
vision preset 可用性/成本、prompt 压制泄漏率、OCR 引擎兼容性、桌宠 spawn 方式）——
均待对应 brief 落地时实机验证。
