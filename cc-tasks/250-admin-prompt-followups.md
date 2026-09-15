# 工单 250：管理面字段/开关与 Prompt 接续修正

日期：2026-09-14。仅后端仓库。每项相关测试通过、差异检查后独立 commit，随后打勾。未完成不得勾选。上下文不够时停在未勾项，把本文件丢给新窗口续做。

## 总进度

- [x] 1. 角色卡编辑器按功能列齐全（先修 CSS 隐藏，再补缺字段）
- [x] 2. 现实资产启用与头像：按管理面样式规范拉开；世界书显示标题/关键词，不直接展示 `base`
- [x] 3. 危险模式改为常驻开关；看屏幕/操作电脑、表情包、陪玩、浏览器、MCP 按类别放进功能与行为，细分仍跳原页
- [ ] 4. 0–8 点且手机/桌面均无活动迹象时，主动循环隐藏 `observe_user_screen`；对话与 `peek_screen_content` 不因此关闭
- [x] 5. `6e_inner_diary` 昨日记录不再内嵌「今日事件」
- [x] 6. `10.7_recent_material` 去掉 sha256 等内部字段，改成可读摘要
- [x] 7. `10.8_recent_tool_results` 改成唤醒时间 + 工具 + 设备 + 结果 + 有/无发言
- [ ] 8. 系统提示词补一句：不确定时自己查屏幕/记忆/工具结果/图片文档/生活记录
- [ ] 9. 写清工具在角色面前长什么样（发现入口、nudge、schema），同步文档

可并行：1 / 2 / 5 / 8；3 依赖功能中心改动；4 独立；6 与 7 同改 `context_continuity` 但分开提交；9 可在 8 之后写文档。

## 现状（已核对）

1. `admin/static/pages/character.html` 的 JSON 表单其实有 name/gender/system_prompt 等字段，但 `#char-edit-form` / `#char-text-form` 带 `admin-inline-056`（CSS `display:none`）。JS 只设 `style.display=''`，盖不过 class，所以页面上只剩梦境行为卡。缺 `world_book`、`post_history_*`、`alternate_greetings`。
2. 创作页 `loadCreationAssets()` 把世界书 checkbox 和头像 file 挤在同一段。规范见 `docs/admin-design-implementation.md`（分隔线设置区、标题 20px/副标题 12px、间距 6px、普通开关不成独立卡）。`_scan_lorebooks()` 的 label 就是文件 stem（`base`）。世界书 YAML 通常无 title，有 keyword。
3. 危险模式在 `device-policy`，带 TTL；`PATCH /system/meta-mode` 开启必写 `expires_at`。`_current_mode()` 在 `expires_at is None` 时已经会保持 danger。功能总览把 flag 平铺，看屏幕/表情包/浏览器没有同类总开关行。
4. `observe_user_screen` 走自主工具面；设备活动来自 `core/perception/screen_observation.py` 的 poll（心跳 30s、空闲 300s）。
5. 日记文件格式是 `## 今日事件`，注入时包 `<昨日记录>`，标题矛盾。
6. `10.7` 把资料 JSON（含 sha256）整段塞进 prompt，已读后 24 小时仍在。
7. `10.8` 用 `frame_tool_message` 包历史结果，套话多，且没有设备/是否发言。
8. `1_system_prompt` 只有存在性定义 + 人设 + 情绪，没有「不确定就去查」的提醒。
9. Path C 首轮只给 `load_tools_<category>`；另有 `11.6_tool_discovery` 与 `11.5_tool_nudge`。具体 schema 要等分类加载后下一轮才出现。

## 工单 1：角色卡编辑器

去掉编辑表单上的 `admin-inline-056`，改为内联 `display:none`，由现有 JS 切换。按角色卡功能补齐字段：

- 已有：name、gender、scenario、system_prompt、description、personality、mes_example、birthday、anniversaries、first_mes、dream_behavior
- 补：world_book、post_history_instructions、post_history_extra、alternate_greetings
- 高级里可放 `presence_ext.proactive` / `tool_loop`（模型/声音/形象仍在角色绑定，不搬回来）
- 中文标签；保存仍合并进原 JSON，不丢未展示字段
- 缓存版本：`character.html` fragment + `character.js`

验收：选中 JSON 卡能看到正文编辑区；纯文本卡仍走文本框；保存往返不丢字段。

## 工单 2：现实资产启用与头像

按 `docs/admin-design-implementation.md` 把启用组合和头像拆成设置行：标题、副标题、分隔、底部操作，不要和 file input 贴在一起。

显示名：配置仍提交 id。UI label 优先 title/name；世界书无标题则用条目关键词；都没有再回退 stem。`core/asset_registry.py` 扫描时写入 label。破限文件用条目 title。

验收：创作页世界书不再只显示 `base`；头像区与 checkbox 有间距；PATCH 仍发 id。

## 工单 3：危险模式常驻 + 功能与行为分类开关

- `PATCH /system/meta-mode`：danger 不再写过期时间；忽略/不再要求 `ttl_seconds`。GET 在 danger 时 `expires_at=null`。
- 功能与行为按类别分组，每项用现有 `centerSwitch` 行：开关 + 细分跳转 + 记录。
  - 感知与电脑：视觉感知、按需截图、屏幕内容查看、危险模式 → `device-policy` / 观测页
  - 输出与互动：表情包 → `output-settings`；陪玩 → `coplay-config`
  - 外部能力：MCP → `mcp`；浏览器 → `agent-runtime-browser`
- 细分页保留原表单（截图冷却、表情包概率、浏览器域名、MCP 授权）。`device-policy` 去掉 TTL。
- 同步 i18n、feature-control-surface、手机若仍传 ttl 则忽略且保持常驻。

验收：开启危险模式后重启/过两小时仍是 danger，直到手动关；功能页分类清楚，不是一堆按钮塞在底。

## 工单 4：夜间无活动隐藏主动截屏

仅自主循环（`origin=autonomy_loop` / autonomy schema）。本地 00:00–08:00，且电脑、手机都没有活动迹象（无合格 poll：心跳≥30s 或空闲≥300s 或 unavailable / 无设备）时，不把 `observe_user_screen` 放进自主工具面；系统提示里也不再怂恿截屏。`talk_owner` 仍可发言。`peek_screen_content` 与普通聊天 Path C 不因此隐藏。有任一侧近期活动则照常。

验收：冻时间 + 无设备 / 夜间有活动 / 白天无活动 三条。

## 工单 5：昨日日记标题

注入 `6e` 时把摘录里的 `今日事件`/`今日感受` 改成昨日对应标题，或去掉与外层 `<昨日记录>` 重复的标题。生成侧文件格式可仍用今日（写的是当天），只改 prompt 投影。

## 工单 6：近期资料投影

`10.7`（以及未读的 `10.6` 若同样 dump JSON）改为短摘要：文件名/标题、时间、摘录。不出现 sha256、revision、内部 id。已读参考仍最多 1 条。测试改断言。

## 工单 7：近期工具结果投影

`10.8` 仅自主唤醒结果。文案接近：

`这是 HH:MM 你被唤醒时调用的工具 <name> 在{她}的手机/电脑上的结果：…。你有/无发言。`

无 device 则省略设备短语。发言标记在该次 autonomy run 结束时回写（`talk_sent`）。不再套 `frame_tool_message` 长边界。结果正文仍用已有 safe_summary，截图失败/sensitive 继续不保留。

## 工单 8：系统提示提醒

`1_system_prompt`（或紧随其后、不可裁的短句）加入：可以调用工具看现在屏幕、查以前的记忆、以前的工具结果、{user_pronoun}发过的图片/文档和上传的生活记录；不确定时自己查。人称用 `user_pronoun`，不写死角色名。

## 工单 9：工具在角色面前的形态（说明，可无代码）

写进 `docs/tool-discovery.md` + `docs/prompt-layers.md`，说明角色实际看到：

1. 首轮 tools[] 只有 `load_tools_<category>`，description 为「加载…的工具定义」，参数 `{}`。
2. 系统层 `11.6_tool_discovery`：按分类加载，加载不是执行。
3. 用户消息前 `11.5_tool_nudge`：需要信息就调工具；禁止把工具名当台词。
4. 模型调用 `load_tools_memory` 后，下一轮该分类换成具体 function schema（name/description/parameters），其他分类仍是入口。
5. 本轮工具结果在 role=tool + `10_tool_result`；跨轮自主结果在 `10.8`。

附一份精简示例（假数据）。不把真实密钥/路径写进文档。

## 共同验收

管理面静态改动：更新 `?v=` 与 `ADMIN_UI_FRAGMENT_VERSION`，硬刷新受影响页。无浏览器则写明未完成。专题文档、feature-control-surface、接口总账按改动同步。手机/桌面本地授权未改的标 observe。只暂存本任务文件。
