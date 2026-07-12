# Brief 54 · 三件套：coplay 启用链路修缮 + 反坍缩增强 + 后端日志清洗

> 2026-07-11。三个子任务相互独立，可并行；54-A 优先（功能现在不可用）。
> 前端配套工单见 `Emerald-client/cc-tasks/20-pet口型说话信号与日志清洗.md`。

---

## 54-A coplay 启用链路修缮【P0：陪玩模式现在完全无反应】

**实测复现（2026-07-11）**：前端"游戏模式"开关打开、Steam 运行、游玩《底特律》，
watcher 毫无反应。定位到两层原因：

1. **`config.yaml` `coplay.enabled: false`**（`coplay_watch.py:24` 第一行就 return）。
   前端 arm 的是状态机，watcher 总开关在 config——**双开关，且互相不知道对方的存在**。
2. **arm 根本没落到后端**：`data/runtime/coplay/` 目录不存在（一次 arm 都没写过状态
   文件）。Tauri 代理（`lib.rs:617` POST /coplay/arm）和 scope（desktop profile 含
   `activity`）都没问题，最可能是**后端进程没重启**（新路由不在运行中的老进程里，
   404），且前端开关对失败静默。

**改法（拍板）**：

1. **消灭双开关**。`coplay.enabled` 语义改为部署级"允许陪玩功能"，**默认 true**；
   运行时唯一开关就是状态机 armed/off。`coplay_watch` 的判定不变（enabled 且
   state≠off 才干活——armed 由用户控制，这已经足够，不需要两个都手动开）。
   `config.example.yaml` 同步改默认值 + 注释说明语义。
2. `/coplay/state` 响应加 `enabled` 字段，前端可显示"功能被部署配置禁用"
   （前端侧见 client 工单 20-B）。
3. **arm/disarm 失败必须可见**：本仓无改动（是前端职责），但 `/coplay/arm` 在
   `coplay.enabled=false` 时应返回 409 + 明确 detail，而不是照常 arm 一个永远
   不会被 watcher 消费的状态——现状就是这种"成功了但什么都不会发生"的静默陷阱。
4. **检测可观测性**：armed 状态下 watcher 每次 tick 把"读到的 RunningAppID / 匹配
   到的白名单进程"写 DEBUG 日志（配合 54-C 的等级规范，平时不可见，排查时开
   DEBUG 立刻能看到检测链路卡在哪一步）。另外在 `/coplay/state` 加
   `last_probe` 调试字段：`{running_app_id, matched_process, ts}`（fail-open，
   探测失败时为 null）——用户不用开日志就能从前端设置页看到检测到了什么。

**用户操作项（写进 docs/coplay.md 的"启用步骤"一节，现在文档里没有）**：

```yaml
coplay:
  enabled: true
  steam_library_paths: ['C:\Program Files (x86)\Steam']   # 按实际库路径
  game_whitelist:
    - {name: '底特律：化身为人', process_name: 'DetroitBecomeHuman.exe'}
      # ⚠️ 进程名请任务管理器实测确认，作为 RunningAppID 未验证时的保底
```
改完 **重启后端**，前端重新开一次游戏模式。

**验收**：关掉又打开游戏模式 → `data/runtime/coplay/.../coplay_state.json` 出现且
status=armed → 启动 Steam 游戏 ≤120s 内 status=active、`last_probe` 有值；
`enabled=false` 时 arm 返回 409。相关测试（session/watcher/router）同步更新。

---

## 54-B 反坍缩增强：长度提示延续三轮 + 分段检测

**背景**：角色回复越到后面越长、越密、不分段（长度坍缩 + 排版坍缩）。仓里已有
anti_collapse 机制（commit 3bc4323"anti-collapse retry"，`tests/test_anti_collapse*.py`）
——**先读现有实现**再动手，新逻辑要接在同一模块里，不要另起炉灶。

两个需求（用户拍板的产品行为）：

1. **长度反坍缩提示延续 3 轮**：现状疑似"检测到超长→当轮注入→下轮就撤"，模型
   下一轮立刻弹回长文。改为：一旦触发长度阈值，"回复要短"提示词**连续注入 3 轮**
   （per-uid 内存计数器即可，重启丢失可接受；每轮递减，期间再次触发则重置为 3）。
2. **分段检测（新增）**：**连续 2 轮** assistant 文本不含 `\n` 且长度 > 阈值
   （config 可调，默认 40 字——执行时可在 40/60 之间实测拍板，40 偏紧）→ 注入
   "超过两句请空行分段"类提示，同样延续 3 轮。触发条件用 `_sanitize_assistant_message`
   之前的原始回复文本判定（分段符可能被 scrub 影响，先核实再选取判定点）。

**实现约束**：

- 两个提示作为 prompt 层注入必须带 `_layer` 字段（硬规则 3）；建议合并为一个
  `anti_collapse_hint` 层，内容按触发的维度拼装（长度/分段/两者）。
- 与现有 anti-collapse retry 的关系是**预防 vs 兜底**：注入提示是预防，retry 是
  生成后兜底，二者共存；确认 retry 的触发统计（如有）能区分两种来源。
- 3 轮延续本身就是迟滞，恢复正常不提前清零，避免振荡。
- 阈值与轮数进 config（`anti_collapse:` 块，缺省值硬编码兜底）。

**验收**：单元测试覆盖"触发→3 轮衰减→期间再触发重置"与"连续2轮无\n+超长才触发、
1 轮不触发"；`pytest -n auto -k anti_collapse` 绿；docs 同步（prompt-layers.md 新层
登记 + 裁剪序）。

---

## 54-C 后端日志清洗

**原则一句话：记边沿，不记电平。** 状态从 A 变成 B 打一条 INFO；持续处于 B 不打。

1. 重复性"连接成功/心跳正常/轮询完成"全部降 DEBUG 或删除：napcat/WS 重连成功
   只在**曾断开后恢复**时打 INFO，进程启动首次连接打一条，之后不打。
2. 周期性 tick（scheduler 主循环、garden_water、coplay_watch、hidden_state_decay
   等）：无状态变化不打日志；有转换（armed→active、harvest、decay 实际发生）才 INFO。
3. 每轮 pipeline 的常规 DEBUG 保持 DEBUG，但检查有没有误标成 INFO 的（如
   semantic_recall 命中列表、prompt 长度统计一类）。
4. 范围：`main.py` / `channels/*` / `core/scheduler/*` / `admin/admin_server.py`
   （uvicorn access log 若全量输出，收敛到 WARNING 或关闭 access log 只留业务日志）。
5. **不改** error/warning 语义，不动 silent_failure 计数。

**验收**：正常挂机（无对话、无游戏）10 分钟，日志新增 ≤ 10 行；断开 NapCat 再恢复，
能看到恰好一对 disconnect/reconnect INFO。

---

### 并行性

54-A / 54-B / 54-C 三者无共享文件冲突（A：coplay+router；B：anti_collapse+prompt 层；
C：横切但只动日志语句），可并行或按 A→C→B 顺序单线跑。
