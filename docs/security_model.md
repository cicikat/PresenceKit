# docs/security_model.md — 当前安全模型与风险边界

> 本文按当前代码校准，不是理想化设计稿。项目仍是单用户本地陪伴系统，默认部署假设是可信本机/内网；如果未来开源、社区化或插件化，必须把这里的“当前缺口”先补上。

---

## 一、当前已落地的边界

### 后端白名单执行

LLM 不能直接执行系统能力。所有工具都必须在 `core/tool_dispatcher.py` 的 `_TOOL_REGISTRY`
注册，通过 `execute()` 做开关、危险标记、权限和确认流程。

- 探针只暴露 `info` / `desktop` 类工具：`get_tools_schema(categories=["info", "desktop"])`
- 危险工具 `device_shutdown` / `device_sleep` 标记 `dangerous=True`，并检查 `agent_control` 权限
- 工具开关来自 `config.yaml tools:`，默认启用，危险工具通常配置为关闭
- 桌面动作先走 WebSocket ack，失败才降级文件队列

### 管理接口 Bearer token（SEC-AUTH-1 / SEC-AUTH-1B，2026-06-11 收口）

`admin/auth.py` 使用简单 Bearer token，密钥来自 `config.admin.secret_key`（或环境变量
`YEXUAN_ADMIN_SECRET`）。SEC-AUTH-1B 已完成：`GET /system/data-path` 也依赖
`verify_token()`。当前 HTTP 管理面全部需要 Bearer token，除非未来另有明确声明并审查的
public endpoint。

受保护端点（全量）：
- `/characters/*` / `/scheduler/*` / `/lorebook/*` / `/jailbreak-entries/*`
- `/mobile/activate` / `/mobile/chat` / `/mobile/poll` / `/mobile/push`
- `/desktop/chat` / `/desktop/activate` / `/desktop/deactivate` / `/desktop/wake`
- `/upload/ingest`
- `/dream/enter` / `/dream/chat` / `/dream/exit` / `/dream/state` / `/dream/settings`
- `/activity/*` / `/reading/*` / `/gomoku/*` / `/chess/*`
- `/agent/think`
- `/sensor/*`（含 `/sensor/activity`）/ `/watch/*`（含 `/watch/event`）/ `/garden/*` / `/mood/*` / `/diary/*`
- `/memory/*` / `/users/*` / `/relations/*`
- `GET /system/data-path`
- 所有 LLM/settings 路由

**鉴权失败行为**：`verify_token()` 抛出 HTTP 401/403，FastAPI 在函数体执行前拒绝请求。
因此鉴权失败时：不触发 LLM、不写磁盘、不创建 runtime state。

**token 安全**：token 值不会出现在任何错误响应或日志记录中。

**客户端调用方式**：所有受保护端点均需 `Authorization: Bearer <YEXUAN_ADMIN_SECRET>` header。
HTTP 管理面不接受 `?token=` / `?secret=` query 鉴权；`/watch/event` 也只接受 Bearer header。

### 路径与测试沙盒

运行态 data 路径应通过 `core/sandbox.get_paths()` 获取。`core/sandbox.py` 只是单例胶水，
实际路径实现位于 `core/data_paths.py`，治理登记位于 `core/data_registry.py`。`mode=test` 时，
路径前缀切到 `data/test_sandbox/{session}/`，并把 `data_prefix` 写入 `config.yaml` 供桌宠端读取。

`core/safe_write.py` 提供：
- `safe_write_text/json/bytes()`：写临时文件后 replace
- `safe_append_jsonl()`：追加 jsonl，用于日志类观测文件

### 上传和媒体限制

`POST /upload/ingest` 只接受文档和图片：
- 文档：`.txt` / `.md` / `.docx`，单文件，最大 5MB
- 图片：`.jpg` / `.jpeg` / `.png` / `.gif` / `.webp` / `.heic` / `.heif` / `.bmp`，可多张，单张最大 10MB
- 图片会按 sha256 做描述缓存，HEIC/WEBP/BMP 等会归一化，长边上限 1920
- 文件名落盘前取 `Path(filename).name`，避免客户端传入路径穿越名

### 角色卡路径保护

`admin/routers/character.py` 的 `_safe_path(name)` 会把目标路径 resolve 到 `characters/` 下，防止
`../` 路径穿越。上传只允许 `.json` / `.txt` / `.md`，JSON 会先解析校验。

### 网络代理控制

LLM client 在无显式 proxy 时使用 `trust_env=False`，网易云搜索的 aiohttp session 也使用
`trust_env=False`。桌宠 WebSocket 客户端侧仍需遵守 `AGENTS.md` 里的规则：连接前临时清除
`HTTP_PROXY` / `HTTPS_PROXY`，连接结束后恢复。

### Write Envelope v0

现实写入已增加 fail-closed 的 Write Envelope v0：
- 未 stamp 的事件默认不写 memory / mood
- `is_test=true` 或 `is_debug=true` 强制不可写
- sensor / watch 原始感知默认不写 profile

这是 P0 写入准入，不是完整权限系统，也不表示 `policy.py`、完整字段契约或 sensor privacy
全系统已经完成。

### User Hidden State Phase 2 — 安全边界

Phase 2 在 Phase 1.5 持久化基础上增加了三个组件，边界如下：

**`to_dream_snapshot()` （`core/memory/user_hidden_state.py`）**
- **只读投影**：不修改任何 `UserHiddenState` 字段，不写磁盘，不发 WriteEnvelope stamp。
- **低分辨率输出**：只暴露 bucket 字符串（low/mid/high/guarded/neutral/easy）和 cue 字符串列表；原始 float 数值不出现在任何返回值中。
- **Dream 写锁**：`DREAM_DIRECT_WRITABLE = frozenset()` — Dream turn 不能通过此函数向隐性状态写任何字段。
- **Fail-closed**：发生意外异常时返回中性 mid/neutral snapshot，Dream LLM 调用不会因状态投影错误而中断。

**`integrate_event_and_save()` / `integrate_impression_and_save()` （`core/memory/user_hidden_state_integrator.py`）**
- **Reality-side only**：这两个函数是 Reality 侧 integrator 的 disk-wired 入口；Dream turn 不得调用。
- **WriteEnvelope 门控**：仅在 `write_envelope.can_write_memory=True` **且** `result.accepted=True` 时才写盘；被拒绝的 envelope 不碰磁盘。
- **中期层限定**：只写 `touch_need.deficit`（事件路径）或 `sensitivity.current`（印象路径）；长期层（`sensitivity.baseline`、`touch_need.baseline`、`embodied_ease`、`body_memory`）永不修改。
- **原子写入**：底层调用 `safe_write_json`，写临时文件后 `replace`，保证写操作的原子性。
- **不触发 consolidate**：这两个函数不调用 `consolidate_baselines()`，不触发基线升级。

**`load_dream_snapshot()` （`core/memory/user_hidden_state_store.py`）**
- **唯一 Dream 读取路径**：Dream session 应通过此函数获取隐性状态快照，不应直接读取 `UserHiddenState` 字段。
- **读后不写**：函数本身不写磁盘，不接 Dream pipeline 写路径。
- **结果可变性隔离**：返回的 dict 是新对象，修改它不影响已持久化状态。

### Dream Guard 与渲染标签收口（P2.4 fail-closed）

- `DREAM_ACTIVE` / `DREAM_CLOSING` 时 QQ owner 消息、`/desktop/chat`、`/mobile/chat`、
  `/desktop/wake` Path B 均被拒，不进入现实 pipeline，不写 runtime / memory。
- **Fail-closed**：dream state 文件存在但 JSON 损坏 / 读取异常 / 状态非法时，
  同样拒绝 reality turn（`BLOCK_UNCERTAIN`），记录 `logger.error`。
  仅 `FileNotFoundError`（文件不存在 = 正常无梦态）被允许通行。
  实现：`core/dream/dream_state.get_reality_guard_status()` +
  `admin/routers/chat._check_reality_not_in_dream()`（已替换旧 `except: pass`）。
- QQ / mobile 输出移除 `<say>` 等展示标签；reality memory / event_log 保存纯文本；
  desktop segments 保持原行为。

---

## 二、当前明确的缺口

### 无鉴权本地入口（SEC-AUTH-1 / SEC-AUTH-1B，2026-06-11 已收口）

所有 HTTP 管理端点（包括只读的 `GET /system/data-path`）均已接入 Bearer token 鉴权
（见上方"管理接口 Bearer token"一节）。SEC-AUTH-1B 已完成，此项已关闭。

**客户端影响**：旧版 Emerald-client 若调用以下端点时未带 `Authorization: Bearer <token>`
header，将收到 401/403 拒绝：
- `POST /desktop/activate` / `/desktop/deactivate` / `/desktop/wake`
- `POST /upload/ingest`
- `POST /dream/enter` / `/dream/chat` / `/dream/exit`
- `GET /dream/state` / `/dream/settings`
- `PATCH /dream/settings`
- `POST /agent/think`
- `POST /sensor/activity`
- `POST /watch/event`

这是预期的 fail-closed 安全变化。所有调用方需补充 `Authorization: Bearer <token>` header。

### WebSocket 鉴权（R9 / SEC-WS-1 final，2026-06-11）

WS 鉴权已从 query token 迁移到 `Authorization: Bearer` header。`admin/auth.authenticate_ws()`
统一处理鉴权逻辑，`admin/admin_server.py` 的 `ws_desktop_endpoint` 不再声明 `?token=`
query 参数。

- 唯一路径：`Authorization: Bearer <secret>` header，token 值不会出现在任何日志或错误响应。
- `?token=<secret>` query fallback 已移除，即使 token 正确也以 code `1008` 拒绝。
- uvicorn access log 的 `QuerySanitizeFilter` 保留，防止被拒绝请求或其他敏感 query 参数泄漏。

Emerald-client 已完成 Tauri Rust native bridge header 迁移；SEC-WS-1 已 final，query token
过渡风险关闭。

> R9 / SEC-WS-1 处理 WS query token 迁移；`SEC-AUTH-1`（HTTP endpoint 鉴权收口）已于
> 同日完成（2026-06-11），见上方"无鉴权本地入口"一节。

### sandbox 不是安全沙箱

`core/sandbox.py` 是路径集中管理和测试数据隔离，不是权限隔离。它不能阻止任意代码读取项目外文件，也不能限制第三方插件能力。未来插件化必须另做权限模型。

### 导入/社区包体系未成型

当前只有角色卡上传和 lore/jailbreak 等管理导入，没有统一 package manifest、schema version、资源总量限制、压缩包解压防护或插件生命周期隔离。

### source / privacy 策略仍不完整

`turn_sink` 已统一 QQ、desktop/mobile/scheduler/sensor 的 assistant turn；冻结 `/chat` 已返回
410，Write Envelope v0 也已提供 fail-closed 写入准入。当前仍不是完整
`can_write_memory` / `can_affect_mood` / `privacy.allow_memory` 字段契约。

---

## 三、高隐私数据

默认不要导出、分享或打包：

- `data/runtime/memory/`
- `data/runtime/characters/{char_id}/character_growth/`
- `data/runtime/characters/{char_id}/inner/mood_state.json`
- `data/runtime/dreams/`
- `data/diary_fallback/`
- 用户日记、Watch/传感器数据、API keys、`config.yaml`

默认可以考虑导出：
- 角色设定
- 立绘 / 表情 / 主题资源
- lore / author notes / preset

导出前必须显式排除 memory、history、profile、diary、event_log、API keys。

---

## 四、未来插件/社区化准入线

开放社区资源前至少需要：

1. package manifest + `schema_version`
2. 文件类型白名单、单文件大小、文件数量、解压后总大小限制
3. 路径穿越检查和嵌套压缩包拒绝
4. 插件目录隔离，禁止插件直接写 memory / queue / system path
5. 工具权限 manifest，危险能力必须用户确认
6. WebSocket / mobile / desktop 配对机制
7. 导出清单，默认排除所有私人记忆和密钥

推荐演进顺序：

```text
Lv1：纯资源包
Lv2：声明式扩展
Lv3：带权限 manifest 的插件系统
```

---

## 当前结论

当前安全模型适合“单用户、本机、可信客户端”的开发阶段。真正的风险不是核心 pipeline，而是把未配对的客户端入口、社区资源和插件能力暴露到不可信环境。准备开放生态前，优先补 WS/mobile/desktop 配对、导入导出白名单、统一 source/privacy 策略。
