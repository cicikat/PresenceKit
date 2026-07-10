# 评估报告：后端接口盘点 × 三端调用面 × 功能迁移建议

> 由 Claude Desktop 静态审计产出（2026-07-10）。本文档是**决策依据 + 工单池**，标 ✅ 的条目可直接派给 CC。
> 方法：提取 `admin/routers/*` 全部 214 个端点，与三个前端的 URL 构造代码交叉比对
> （管理面板 `admin/static/index.html`、桌面 `Emerald-client` src+src-tauri+sensor-service、手机 `yexuan_memery` lib）。
> 已处理 JS 模板串 / Rust `format!("{}")` / Dart `$var` 三种拼接风格。
>
> **口径注意**：/watch/event、/hardware/* 由手表/ESP32 外设调用，旧桌面端 Emerald-desktop 未扫
> （已废弃仓库）；标"无人调用"仅指三个在役前端。

## 覆盖率总览

| 前端 | 调用端点数 / 214 |
|---|---|
| 管理面板 | 92 |
| 桌面 Emerald-client | 96 |
| 手机 yexuan_memery | 32 |
| **三端都不调用** | **39** |

## 一、39 个无人调用接口的分类裁决

### A. 建议直接删除（legacy，8 个）✅ 可开一个删除工单

| 端点 | 证据 |
|---|---|
| `POST /agent/think` | 文件头注释自证："供 Emerald-Desktop（该前端已废弃）的 agent loop 调用"。纯 LLM 直通，还挂 admin scope，属攻击面。 |
| `POST /mobile/chat` | 手机端按 AGENTS.md 约定走 `POST /desktop/chat`，此接口从未被接。语义与 desktop/chat 重复（仅 channel 标记不同）。 |
| `POST /desktop/deactivate` | 桌面端下线由 WebSocket 断连处理（desktop_ws.py set_active(False)），HTTP 版从未被调。 |
| `GET /activity/list` | 前端都用 `/activity/current`。 |
| `GET /observe/vector`（无 uid 变体） | 管理面板只用 `/observe/vector/{uid}`。 |
| `GET /relations/`（根列表） | 面板用 `/users/` 列用户、`/relations/{user_id}` 看单人。 |
| `PATCH /auth/tokens/{label}` | 面板只用 GET/POST/DELETE/rotate。 |
| `POST /sensor/activity` | 无任何调用方（删前 CC 再确认一次 core 内无引用）。 |

删除时同步：`_TOOL_REGISTRY` 无关联；删 `agent.py` 整个 router；跑 `pytest -n auto` 回归。

### B. 保留不动（运维/调试出口，3 个）

`GET /system/health`（监控用）、`DELETE /scheduler/signatures`（调度器去重复位）、
`GET /scheduler/sensor_aware/audit`（触发审计）。curl 手工用，不接 UI。

### C. 后端功能是活的、纯缺前端 UI（不是 legacy，别删）

| 端点组 | 现状 | 建议归属 |
|---|---|---|
| `relationship-facts/*` 7 个 | 2026-06-21 上线的动态 lorebook 模块，core 在用，**管理无入口** | 管理面板加"关系事实"页（含 confirm/reject 审核流）✅ |
| `memory/{uid}/*` 细粒度 PUT/DELETE 8 个 | 孤儿写接口：episodic/mid-term/user-facts/event-log **连读取接口都没有**，UI 无从编辑 | 见"三、缺接口"——先补 list 读接口，再在面板做记忆管理页 ✅ |
| `coplay/*` 3 个 | 最新 commit 刚建成（Brief 38-42 陪玩模式） | 桌面端接（等既定计划，不算债） |
| `model-presets` 2 个 | **正好对应你要的"API 实时切换+标签"**，见"四" | 三端都接 ✅ |
| `settings/screen-peek`、`tts-config`、`users/{uid}/pronoun` | core 都在读（voice_adapter/prompt/称谓系统），设置项无 UI | 管理面板设置页补三个小卡片 ✅ |
| `sensor/push`+`status`+`today` 3 个 | prompt 层在读手机传感器摘要（user_facts/prompt_builder），但**已无客户端在喂数据**——这是为手机设计的接口 | 转正给手机端，见"二-6" |

## 二、桌面 → 手机迁移推荐（接口全齐，抄桌面 UI 即可）

按"手机上真的会用"排序：

1. **活动系统**（阅读/五子棋/国际象棋/梦境预构）——`/activity/*` 全套接口齐，桌面
   `src/windows/activity/` 有完整 UI 参考。手机加一个"活动"页：进行中活动卡片（`/activity/current`）
   + 棋盘/书页视图 + 活动内聊天。五子棋/象棋触屏体验比桌面还自然。✅
2. **群聊 Stage**——`/group/*` 9 个接口齐，桌面 GroupListPanel/GroupChatPanel 可参考。✅
3. **状态感知**——资料页加"她现在在干什么/心情如何"：`GET /activity/current` + `GET /mood/state`
   （手机现在两个都没调）。低成本高感知。✅
4. **语音输入**——`POST /transcribe` 桌面在用；手机聊天框加个麦克风按钮（Android 录音 → 上传转写）。✅
5. **Dream 补全**——手机已有 enter/chat/exit/state/settings，缺 `stats`（梦境统计）与
   `wake/resume`（外部唤醒/续梦）。小改。✅
6. **手机传感器上报**——`POST /sensor/push`（步数/电量/亮屏次数），Android 原生拿这三样很容易，
   正好复活"一-C"末行那组接口，给主动触发喂素材（"你今天走了好多路"）。✅
7. **角色切换**——手机只调了 `characters/active-info`；补 `GET /characters` + `PUT /characters/active`
   下拉切换。✅

**不建议迁**：Live2D/3D 桌宠、room/toy 视觉层（性能+交互形态不适配）、observe/provenance 调试面
（管理面板专属）、悬浮 presence-nag（手机已有自己的悬浮窗体系）。

## 三、压根没有接口的功能（前端想做也做不了）

| 缺口 | 说明 | 建议 |
|---|---|---|
| **model preset 的增删改** | 只有 GET 列表 + PUT active-routing 切换；preset 本体只能手改 config.yaml | 新增 `PUT/DELETE /model-presets/presets/{name}`、`PUT /model-presets/routing-profiles/{name}`，写入 config.yaml 后热重载（复用 set_active_routing 的写回+reload 模式）✅ |
| **relay 中继配置** | relay_base_url/topic/token 只能改 config.yaml | 新增 `GET/PUT /settings/relay`（token 打码返回），手机能力检查页直接填 ✅ |
| **花园互动** | 只有 `GET /garden/state`；浇水靠聊天触发 LLM probe，手机花园页只能看不能点 | 新增 `POST /garden/water`（复用 water_garden 工具逻辑），手机/桌面花园页加浇水按钮 ✅ |
| **记忆层读取** | episodic/mid-term/user-facts/event-log 无 list 接口（删改接口是孤儿） | 新增 4 个 `GET /memory/{uid}/...` 列表接口，admin scope ✅ |

## 四、UX 动机的新接口：API 实时切换/填写/标签命名（你点名的）

后端**已经有八成**：`model_presets` 天生就是"带标签的 API 配置"——
`presets`（每个有名字、api_key、base_url、model）+ `routing_profiles`（场景→preset 映射）+
`active_routing`（当前方案）+ `GET /model-presets`（api_key 自动打码）+ `PUT active-routing`（热切换）。

缺的三块：

1. **preset/profile 的 CRUD 接口**（见"三"第一行）。
2. **连通性测试**：`POST /model-presets/presets/{name}/test` —— 用该 preset 发一条 1 token 的
   ping，返回延迟/错误，前端"测试"按钮用。✅
3. **三端 UI**：管理面板设置页加"模型路由"卡片（列 preset、加/编/删、一键切换、测试）；
   桌面 ConnectionSettingsPage 加同款；手机设置页至少做"查看+切换 active_routing"。✅

安全注意：这组接口挂 admin scope；手机默认 mobile token 无权限——**故意的**，手机端只读
`GET /model-presets`（打码）+ 切换需要用户在手机里填 admin token 或给 mobile profile 加 scope，
CC 施工时保持现状（只读+提示），不要给 mobile scope 扩权。

## 五、工单拆分与依赖

| # | 工单 | 依赖 | 仓库 |
|---|---|---|---|
| W1 | 删 8 个 legacy 接口 + 回归 | 无 | Emerald-presence |
| W2 | model-presets CRUD + test 接口 | 无 | Emerald-presence |
| W3 | 管理面板：模型路由卡片 + 关系事实页 + 三个小设置卡片 | W2 | Emerald-presence (admin/static) |
| W4 | 记忆层 list 接口 + 面板记忆管理页 | 无 | Emerald-presence |
| W5 | relay 设置接口 + 花园浇水接口 | 无 | Emerald-presence |
| W6 | 手机：状态感知 + 角色切换 + Dream 补全（小件打包） | 无 | yexuan_memery |
| W7 | 手机：活动系统页（大件） | 无 | yexuan_memery |
| W8 | 手机：群聊 Stage 页（大件） | 无 | yexuan_memery |
| W9 | 手机：语音输入 + 传感器上报 | 无 | yexuan_memery |
| W10 | 桌面：接 coplay | 无 | Emerald-client |

W1/W2/W4/W5 互不冲突可并行；W3 等 W2；手机三张（W6-W9）可并行但建议按序验收。
改接口契约的（W1/W2/W5）记得同步 `yexuan_memery/docs/backend/integration.md`。
