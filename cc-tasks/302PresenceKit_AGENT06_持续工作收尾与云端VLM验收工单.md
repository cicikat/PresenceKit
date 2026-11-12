# PresenceKit · AGENT-06 持续工作收尾、验证与云端 VLM 联调

> 类型：增量修复工单 / P0 优先
> 基线：`cicikat/PresenceKit` main（核对到 `5a1ab61152a0`）；`cicikat/PresenceKit-desktop` main（核对到 `8f42b735f1ea`）
> 适用场景：当前 AGENT-01/02/04/05 已实现、AGENT-03 主动小创作未施工。
> 原则：**只修可复现缺口；不创建第二套 Agent Runtime / Task Store / 角色系统；不新增敏感词封禁、逐动作确认或购买/删除字样触发的页面阻断。**

## 0. 已有实现与本次边界

已存在：
- 后端 `core/agent_runtime/agent_sessions.py`：父 Agent Session，意图模型决策、子任务派发、改令、取消、预算、进度投影。
- 后端 `core/agent_runtime/computer_use.py`：Windows 子任务观察—规划—动作—回执循环；`computer_use_vision.py`：云端 JPEG 截图代理。
- 后端 `agent_context.py`：角色卡/关系事实/事件检索/`self/AGENT.md` 的有限投影；Worker 接收精简目标。
- 桌面 `src-tauri/src/computer_use/`：Windows 执行器、原生 Esc 取消、蓝色边缘覆盖层、按任务租约同步；`AgentSessionIndicator.tsx` 轮询状态。
- 已有真实 Windows + Notepad + 文本模型的完整单例；**Edge 图标纯视觉、物理 Esc、多屏、真实跨聊天改令尚未完成组合验收**。

本单**不处理**：AGENT-03 主动惊喜的新能力、通用手机 Computer Use、完整 Codex 框架迁移、任意 shell/文件系统授权、重新设计模型配置 UI。现有云端截图授权继续保留；不对屏幕文字做关键词审查。

## AGENT-06A · 修复整体任务提前完成（P0）

### 可定位的缺陷
`core/agent_runtime/agent_sessions.py::_drive` 中，`agent_finish` 的验证目前相当于：

```python
any(r["state"] == "succeeded" and r.get("evidence") and r["revision"] == revision for r in session.receipts)
```

这仅能证明当前 revision 的**某一个子目标**有证据，不能证明整个用户请求完成。例如 A、B 两个操作只做完 A，父 Agent 仍可能返回 `completed`。

### 改动要求
1. 保留当前主 Agent 意图规划与 Worker 执行分层；父 Agent 不直接自行发送鼠标操作。引入**有界的父任务子目标账本**（已有 `Session.receipts` 可以扩展，不另建全局数据库）。每个子目标有稳定 ID、所覆盖的用户需求、状态、证据引用、任务 revision、验收结果。
2. 将纯 `{"kind":"agent_finish"}` 改成带结构化完成声明的形式，如 `{"kind":"agent_finish", "completed_subgoals":["..."], "summary":"..."}`。父端先执行确定性检查：声明的 ID 必须真实存在、属于当前 revision、具有已确认的成功证据；不存在待完成的**明确用户要求**；不能把未知/失败动作当作成功。再对目标覆盖进行有界语义核验；不要仅用模型的一句话作为独立证据。
3. 支持动态拆分子任务，不要求用户预先提供完整步骤清单；但主 Agent 若拆出多个明确子目标，必须逐个对账。用户改令时淘汰冲突子目标，不得借用旧 revision 的证据。
4. 对 `outcome_unknown` 或无法核实的目标保持未知/等待验证，不可自动标记 `completed`，不可擅自重做可能已执行的副作用动作。
5. 子任务失败后可以基于新观察重新规划，不要无条件重复已确认执行过的快捷键、文字输入或保存。

**验收**：
- 「A + B」只做 A、模型试图 finish → 必须拒绝整体成功并继续处理 B/返回未完成。
- A + B 均有匹配证据 → `completed`；重复回执不会触发重复动作。
- 用户中途改 B→C → A 是否仍有效由任务修订规则决定；旧 B 的成功不能替代 C。
- 模拟断线/取消发生在已派发动作之后 → 结果不明，不得强制重放。

## AGENT-06B · 暂停、等待用户、继续执行（P0）

### 可定位的缺陷
- `agent_sessions.py` 的 `resume` 目前仅接纳 `user_paused` / `desktop_busy`，不接纳 `session_execution_budget` / `session_failure_budget`。
- `agent_need_input` 仅输出固定 `kind`，没有用户能读懂的具体问题。
- 到期时 `finally` 会把 Task Manager 任务写为 paused 并清空进程内 `_active`，但现有 `control` 只查 `_active`：**持久化暂停的任务不能靠同一接口正常恢复**。

### 改动要求
1. 区分 `waiting_user`（附具体问句）、`paused_by_owner`、`paused_budget`、`paused_device`、`outcome_unknown`，向状态 API / 桌宠状态条提供可读原因；不要以失败/无响应笼统展示。
2. 普通用户暂停可恢复；`waiting_user` 经同一 Session 的 `steer` 补充信息恢复；预算用尽则**明确展示「继续本任务」**，由用户续授一段新的预算，而不是无限静默续费 API。
3. 如父任务已持久化为 paused、`_active` 清空，支持安全的续段/恢复机制：重新认领任务或创建有来源关联的后续 attempt；保留目标、有效已确认回执、revision、执行进度；续段前重新校验当前设备会话、角色身份、租约、已完成动作，不自动重放不确定动作。
4. 把现在可配置的预算秒数与 Task Manager TTL、租约时长、恢复语义统一：不可出现预算改短但固定旧 TTL 导致 UI 状态飘忽的情况。技术预算仍要有，**用户不需要选「授权 3/5/15 分钟」才能发任务**。
5. 取消/终止优先级高于恢复；重复控制命令幂等；不会因为 `steer` 重复调用而重复创建子任务。

**验收**：
- 主 Agent 返回 `agent_need_input` 并包含问题；UI 可以看到问题；用户一条聊天消息补充信息后继续原任务。
- 用户暂停→恢复、预算暂停→点击继续、后台服务重启后恢复，各自状态准确；已执行的文字输入不会重复。
- API 预算终止后不会自转消耗 Token；按 Esc 停止后不可由任何晚到的续轮重新开始。

## AGENT-06C · 整个 Agent 状态的蓝雾与 Esc（P1）

### 可定位的缺陷
`src-tauri/src/computer_use.rs::cue_active` 目前以正在执行的 peer `task_id` 判断蓝雾；子任务间的主 Agent 思考、等待下一步时覆盖层可能消失，和「工作状态持续可见」的期望不符。

### 改动要求
1. **蓝雾的生命周期由父 Agent Session 决定，强度由当前阶段决定**。`planning/observing/acting` 显示；`waiting_user/paused` 使用弱态或静态提示；`completed/failed/canceled` 立即收束。若用户没有启动 Agent Session，仅普通旧版 Computer Use 则继续按原子任务机制显示。
2. 优先复用现有 `/computer-use/agent-session` 状态、`AgentSessionIndicator.tsx` 和 `overlay.rs`；可以通过现有设备 poll 附带少量状态，避免额外高频轮询或创建第二个状态源。
3. 多屏保持透明、鼠标穿透、不抢焦点，且不能污染截图；任务结束立即清除。蓝雾不可覆盖交互区域的可见文字或打断软件快捷键。
4. 原生 **Esc = 用户主动停止当前 Agent 工作**（不只是停止某个子任务）；桌面/聊天/桌宠停用须收敛到同一父会话取消语义。收到取消后立刻拒绝后续动作和迟到回执；保留已有 Ctrl+Shift+F12 紧急停止。纯文字聊天、非 Agent 状态不能劫持 Esc。
5. 不增加昂贵的动效引擎、额外常驻透明窗口集合或新权限开关；覆盖层仅在实际需要时存在。

**验收**：
- 任务跨三个子目标时，蓝雾不因两次任务之间的推理而闪烁。
- 真机物理 Esc、桌宠停止、聊天取消都令父任务停止，之后无任何新输入。
- 分别测单屏 125%/150% DPI、双屏不同缩放、窗口切换和锁屏/解锁；失败如实写 observe。

## AGENT-06D · 云端 VLM 接口及纯视觉完成证据（P0，真实联调）

### 当前事实
- `core/agent_runtime/computer_use_vision.py` 已经将 JPEG + 目标描述转为 Chat Completions，并严格解析 `{"status":"found","x":0.xx,"y":0.xx}` 或 `{"status":"uncertain"}`。
- 现有 Cursor/UIA Worker 的 `finish_candidate` 主要依赖 `observation.nodes` 中的 node ID + 原文；对纯图标、Canvas、自绘控件，**即使成功点击也可能缺乏可引用的 UIA 证据**，不能宣称通用视觉任务已验收。
- 先前仓库中的 GLM-4.6V-FlashX 合成定位测试通过，但真实代理出现过格式无效；不要把它当作所有模型都能稳定工作。

### 改动与验收
1. 继续通过 `image_presets` 的 `computer_use` 专用路由选择视觉模型，文本父 Agent 与 Worker 的路由保持独立。API Key 只存在后端；云端上传仍按设备会话级授权；保持 JPEG 大小、目标描述和租约校验。
2. 补充**非 UIA 控件的观察/验收契约**：允许基于原始截图或经模型审查的视觉状态进行结构化结果校验；视觉证据与 action receipt、frame 版本及任务 revision 绑定。不要把「截图变了」直接等同成功，也不要让模型自由给自己判成功。
3. 真实用户可先接 `qwen3-vl-flash`（阿里百炼 OpenAI 兼容、默认非思考），备用 `qwen3-vl-plus`。本单不改上游模型 API，仅处理必要的 schema 兼容。若提供商返回带代码块/解释、额外字段、超时，应记录错误类别和可复现样本；不能粗暴放宽坐标校验。若试行格式恢复，只允许无副作用的有限重新定位，不重放鼠标动作。
4. 进行**三类测试**：记事本 UIA 操作；Edge 真正图标定位与点击；至少一种没有良好 UIA 的应用/网页 Canvas。尽可能在真实 Tauri + Windows + 用户选的视觉模型下跑通。场景含缩放 100/125/150%、目标遮挡、按钮不在截图、云端超时及取消。
5. 每个测试记录：模型、地址地域（不记录密钥）、像素尺寸、定位时间 p50/p95、有效 JSON 率、坐标命中率、动作成功率、整体目标成功率、输入/输出 Token 与近似成本。实机没跑过的一律 `observe`。

**最低验收**：真实 Qwen3-VL-Flash 通过 OpenAI 兼容连接返回规范化坐标，并完成至少一个纯图标点击 + 观察确认；真实记事本多步任务仍能完成；云端模型不可用时能明确回报失败/暂停，且不会回退到未授权的公网图像服务。

## 提交顺序 / 回归要求

1. `AGENT-06A`：整体完成判定与跨 revision 证据测试。
2. `AGENT-06B`：暂停与恢复状态，后端状态 API 及前端显示。
3. `AGENT-06C`：父会话蓝雾/取消整合；Windows 实机验证。
4. `AGENT-06D`：接入用户自选 VLM，真实截图/图标联调与观察验证。

### 不能破坏的行为

- 不改变主人主动委托与自主行为的权限边界；`AGENT-03` 主动惊喜另开施工，不顺手放开 `live_owner_only`。
- **禁止关键词黑名单、扫描“购买/删除/支付”自动禁用 Computer Use、逐动作弹确认**。
- 角色卡和记忆仍只进入主 Agent 的有界意图上下文；Worker 只拿精简目标/工作习惯/执行证据，不注入整张角色卡。
- 不重写已有 `Task Manager`、`Work Sessions`、`Computer Use` 原生执行器，不引入 Codex/Claude Code/OpenClaw 的整套运行时做替身。
- 不把设备事件/截图明文写入持久记忆或普通日志；角色可记住**有根据的任务结果**，但不能凭模型自述伪造执行事实。
- 不得拿 Fixture、模拟桌面回执、合成截图的绿灯冒充实机完成。

### Codex 执行指令（可直接复制）

> 基于 `cicikat/PresenceKit` 和 `cicikat/PresenceKit-desktop` 当前 `main`，先复查这些问题在最新代码是否仍存在；仅针对可复现缺陷实施 AGENT-06A→D。每一部分独立 commit、记录测试命令和真实证据，检查 diff 与跨仓契约；不要推送、部署或扩大合并范围，除非用户明确要求。若缺乏真实 Windows 或付费模型 API 条件，记录 `observe` 而不是编造验收。禁止增加基于屏幕文案的关键词拦截、逐步确认、第二套 Agent Runtime。

## VLM 路由配置附录（用户手动设置）

推荐：阿里云百炼 **`qwen3-vl-flash`**。作为现有 PresenceKit `computer_use` 图像 Preset 的**云端** Chat Completions 连接（`kind=vision`, `api_protocol=chat_completions`, `is_local=false`）。

- 北京地域 Base URL：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 新加坡地域 Base URL：`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- 模型：`qwen3-vl-flash`；兼容图像输入，默认非思考模式，利于短 JSON 输出。
- 备选：`qwen3-vl-plus`（接口类型和地域 URL 相同）。
- 如果已有业务空间专属域名，优先用本地域对应 `/compatible-mode/v1`；Key 与地域严格对应。
- API Key 只保存在 PresenceKit 后端；桌面端云端截图上传许可仍需开启，避免误以为 `is_local=false` 表示本地推理。
- **不能保证仅保存路由就立即通过正式 Computer Use**：必须执行 AGENT-06D 的真机闭环验收。

官方参考：
- https://help.aliyun.com/zh/model-studio/qwen-vl-compatible-with-openai
- https://help.aliyun.com/zh/model-studio/qwen3-vl-flash
- https://help.aliyun.com/zh/model-studio/base-url
