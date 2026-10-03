# 工单批次：手机端生活记录队列 / 文件卡片 / 重试状态

> 状态：待施工。主要改 `Emerald-mobile` 仓库，M2 涉及少量后端。三张工单互不依赖，可并行；每张验收后在对应仓库独立提交。
> 行号为写单时快照，施工前先 Grep 复核。

---

## 工单 M1：生活记录图片「队列已满」误报

### 现象
上传生活记录图片时提示数量到上限，无法继续上传。

### 根因
1. 后端没有图片张数限制。
2. 报错来自本机队列检查，代码在 `android/app/src/main/kotlin/com/presencekit/mobile/LifeRecordsStore.kt`，错误码 `queue_full`，提示文案见 `lib/l10n/app_zh.arb:74`。
3. 问题在两处检查，按可能性排序：
   - :99-100，图片字节数（主因）。这里统计目录里所有 `*.image` 文件，上限 100 MiB。`acknowledge()`（:187）同步成功后故意保留本地原图，所以已经同步过的图片永远占着「队列」配额。累计 30 到 50 张照片后，就再也加不了新图。
   - :95-96，操作条数。上限 200，统计时不区分 realm，也不排除 rejected 状态。失败的操作只要不重新保存或删除，就会一直累积。
4. 统计页（:67-75）只计算仍被引用的图片，所以统计显示正常，保存时却被拒，两边口径不一致。

### 施工要求
1. 字节上限只统计「还有待上传操作」（pending 或 retry）的图片。已经确认上传的图片不计入队列配额，仍保留作本地展示。
2. 本地缓存另设一个总上限，可配置，默认 1 GiB。超过时按 LRU 清理「已同步且服务端可取回」的本地图片，展示时回退到服务端 URL。待上传的图片永不被清理。这样仍然满足 `docs/known-issues.md:66,69` 里「不静默驱逐待上传数据」的约定。
3. 操作条数改为只统计 pending 和 retry；rejected 状态的条目在列表中显示，可以一键清除，不再占配额。
4. 统计页和保存检查共用同一个计数函数，口径一致。
5. 队列真的满了时，提示里写清是哪一项满了（待上传 X 项 / Y MiB），并提供「去队列页」入口。
6. 更新 `docs/known-issues.md` 的相关条目。
7. 补测试：对 Kotlin store 写单元测试，覆盖三种场景：已同步图片不计入配额；rejected 不计入；LRU 不清理待上传图片。

### 验收
- 连续上传 60 张以上的大图（已同步）后仍能继续上传。
- 断网时累计到真实上限会正确拒绝，并给出清晰的提示。

---

## 工单 M2：手机端显示角色发送的文件（参考桌面端）

### 现象
角色用文件工具发送文件时，桌面端会显示文件卡片，可以下载和预览；手机端什么都不显示。

### 根因
1. 后端已经发出文件信息：
   - `core/turn_sink.py:191-192,272-273` 把 artifacts 发给 desktop 和 mobile。
   - `channels/mobile.py:166,194-195` 把它放进 poll item 的 `artifacts` 字段。
   - `/mobile/poll` 原样返回。
2. 每项的字段（`core/tools/chat_artifacts.py:96-99`）：`{id, filename, mime, size, download_url, preview_url?}`。
3. 手机端整个代码库里没有任何处理 artifact 的代码：解析器丢弃了这个字段，也没有对应的组件。`docs/channels.md:310` 把手机 UI 标为 roadmap。
4. 端点 `GET /chat/artifacts/{id}` 和 `/chat/artifacts/{id}/preview`（`admin/routers/chat.py:784,802`）需要 `chat` scope，手机 token 已经有这个权限。
5. 另一个缺口：聊天历史不持久化 artifacts（见桌面 `ChatPanel.tsx:150` 的注释），所以刷新历史后卡片会消失。

### 施工要求
1. 手机端：
   - 在 poll 和 `/mobile/chat` 响应的消息模型里解析 `artifacts`，字段参照桌面端 `Emerald-client/src/shared/api/chatArtifacts.ts` 的 `normalizeChatArtifacts`。
   - 新增文件卡片组件：显示文件名、大小和类型图标。点击预览：图片或文本类用应用内查看，其他类型交给系统打开。点击下载：带 token 请求 `download_url`，保存到下载目录或调用系统分享。
   - 视觉参照桌面端 `ChatPanel.tsx:546-567`。
   - 处理下载失败（404 或已过期）、无网，以及大文件的进度显示。
2. 后端：让聊天历史保留 artifacts 元数据，只保留元数据，不复制文件，让历史接口返回这个字段，使手机端和桌面端刷新后都能看到卡片。新增持久字段时注意 AGENTS.md 规则 7。如果改动太大，可以拆成单独的工单，但要在 known-issues 里登记。
3. 文档：更新 `docs/channels.md:310`（不再标为 roadmap）、`docs/three-repo-interface-catalog.md` 的 artifacts 条目，以及手机仓的协议文档。
4. 测试：手机端为模型解析和组件写 widget test；后端为历史返回 artifacts 写测试。

### 验收
- 角色发送 txt、图片、pdf 各一个，手机端都显示卡片，能预览和下载。
- 重启 App 或刷新历史后卡片仍在（取决于第 2 步是否在本单完成）。

---

## 工单 M3：发送成功却显示「重试」，且不会自动消失

### 现象
消息明明已经发送成功，却显示「重试」；之后有新消息进来，重试按钮还在。

### 根因
代码在 `Emerald-mobile/lib/controllers/chat_controller.dart`：
1. 发送是同步 HTTP，超时 120s（上传 180s），见 `services/backend_client.dart:446,782`。带工具调用的长回合很容易超时。超时后 `_markSendFailed`（:575-596,:645-656）把消息标为失败，但服务端仍然会完成这个回合，并通过 `/mobile/poll` 把回复送达。
2. `_markSendFailed` 的兜底逻辑（:651-655）会去标记「最近一条未失败的己方消息」，可能标错消息。
3. 失败之后没有任何对账：
   - `finally` 只在没有错误时刷新历史（:614-617）。
   - `chat_history_reconciliation.dart:116` 匹配时直接跳过 failed 条目（`if (item.failed) continue;`），所以失败标记永远清不掉。
4. 后端 poll item 里带着 `request_id`（`channels/mobile.py:198-199`），但手机端从不读取，关联信息白白丢掉。
5. `_applyChatResponse`（:515-523）只因为响应里缺少 `turn_id` 就标 failed，属于误判。

### 施工要求
1. 消息状态改为 `sending` → `sent` / `uncertain` / `failed`：
   - 超时、断网这类「不知道服务端收没收到」的错误标为 `uncertain`：显示「确认中」，不显示重试按钮。
   - 只有服务端明确返回 4xx/5xx 错误时才标为 `failed`。
2. 对账：
   - 解析 poll 消息和历史里的 `request_id`。收到同一 `request_id` 的回复或历史记录时，把对应的本地消息标为 `sent` 并去重。
   - 去掉 :116 对 failed/uncertain 条目的跳过。
   - 失败分支也要触发一次历史刷新。
3. `uncertain` 超过一个可配置的窗口（默认 5 分钟）仍未对上，才转为 `failed` 并显示重试按钮。
4. 有新的助手消息到达时，按 `request_id` 对账一次；对不上就保留状态，但不允许因为「最近一条」的兜底逻辑去标错别的消息。删除 :651-655 的兜底。
5. 不再因缺少 `turn_id` 就标 failed（:515-523）。
6. 重试使用同一个 `request_id`（已经是这样，:325,:1253）。
   - 后端确认 `/mobile/chat` 对重复的 `request_id` 是幂等的：已完成的直接返回结果或 409，不重复跑回合。
   - 如果后端不是幂等的，在后端补一个短期的 `request_id` 去重缓存，并补测试。
7. 测试：手机端为控制器写单元测试，覆盖四个场景：超时后 poll 带回同一个 `request_id` 时清除状态；历史刷新时清除状态；最近一条兜底不会再标错消息；缺少 `turn_id` 不算失败。后端补幂等测试。
8. 如果这次改动影响了跨仓字段（`request_id` 在 poll 和历史中的语义），同步更新 `docs/three-repo-interface-catalog.md`。

### 验收
- 制造一次超过 120s 的长回合：界面先显示「确认中」，回复到达后自动变为已发送，全程不出现重试按钮。
- 真实的服务端错误仍然显示重试；重试不会导致重复回复。
