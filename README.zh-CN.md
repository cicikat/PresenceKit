[English](README.md) | [简体中文](README.zh-CN.md)

# PresenceKit

单用户 AI 陪伴**后端**。它拥有角色人格、长期记忆、情绪状态、工具执行、主动联系和梦境隔离运行时；QQ 机器人只是可选通道之一。桌面端（[PresenceKit-desktop](https://github.com/cicikat/PresenceKit-desktop)）和手机端（[PresenceKit-mobile](https://github.com/cicikat/PresenceKit-mobile)）是瘦客户端：负责界面、采集和投递，**不拥有**记忆、人格、调度或业务数据。

当前产品列车是 **v1.1.0**。桌面线协议仍是冻结的 **v0.1**（`POST /desktop/chat` + `/ws/desktop`），不是新的 EventBus。精确 HTTP schema 以运行中的 `/openapi.json` 为准。专题文档在 `docs/`；根目录的 `ARCHITECTURE.md` / `DESIGN.md` / `AGENTS.md` 是开发入口，产品能力以 `docs/` 为准。

---

## 三仓关系与责任边界

```
PresenceKit（本仓，后端 / 唯一业务真相源）
  ├── PresenceKit-desktop  Tauri 桌宠 + 管理面板壳
  └── PresenceKit-mobile   Flutter Android 客户端
```

| 问题 | 答案 |
|---|---|
| 谁存记忆、角色卡、梦境、花园、生活记录、token？ | **只在后端** `data/` 与 `userdata/` |
| 谁决定要不要主动说话？ | 后端 `core/autonomy`；`talk_owner` 是唯一用户可见出口 |
| 谁渲染气泡、Live2D、通知、热力图？ | 对应客户端 |
| 谁截屏、读传感器、跑 IME？ | 客户端采集，后端接收、裁决、决定是否开口 |
| 可以只跑后端吗？ | 可以：纯管理面板聊天、纯 QQ，或只接一端 |
| 可以只跑客户端吗？ | 不可以；客户端没有本地人格/记忆引擎 |

**配套版本（本轮）**

| 仓 | 版本 | 说明 |
|---|---|---|
| 后端 PresenceKit | [v1.1.0](https://github.com/cicikat/PresenceKit/releases/tag/v1.1.0) | 本仓 |
| 桌面 PresenceKit-desktop | [v1.1.0](https://github.com/cicikat/PresenceKit-desktop/releases/tag/v1.1.0) | 渲染/ack 在客户端；冻结 v0.1 协议 |
| 手机 PresenceKit-mobile | [v1.1.0](https://github.com/cicikat/PresenceKit-mobile/releases/tag/v1.1.0) | 生活记录 UI、热力图、梦境外观等在手机端 |

跨仓契约、设置归属、open/observe 缺口见 [docs/three-repo-doc-index.md](docs/three-repo-doc-index.md) 与 [docs/three-repo-interface-catalog.md](docs/three-repo-interface-catalog.md)。功能开关、effective state、scope 以 [docs/feature-control-surface.md](docs/feature-control-surface.md) 为权威。

---

## 系统是什么 / 不是什么

**是**

- 单 owner 本地或受保护内网陪伴运行时：一个进程、一个 `scheduler.owner_id`、一套角色卡与记忆。
- Reality / Dream 两个隔离 realm。现实写记忆；梦境走独立 pipeline，只允许薄回流（印象 / afterglow），不得把梦记成现实。
- HTTP + WebSocket 服务（默认 `http://127.0.0.1:8080`）+ 可选 NapCat/OneBot 11 QQ。
- 管理面板（本仓 `admin/static/`）是运营、配置、观测真值入口。

**不是**

- 多租户 SaaS、OAuth、公开聊天机器人平台。
- 客户端本地 LLM / 本地记忆库。
- 保证 Android 在所有 OEM/Doze 下后台必达（relay 是信号，正文在 poll 队列；见手机协议）。
- 自动付款、自动发帖、任意 shell/文件系统权限。Agent Runtime 的 process/browser/workspace 都是有界能力，默认关。
- 已实现的统一 EventBus / 新桌面 WS v1。那些是历史/延后设计，见 [docs/interaction-event-model.md](docs/interaction-event-model.md)、[docs/v1-release-contract.md](docs/v1-release-contract.md)。

保守默认：scheduler、autonomy、MCP、硬件、IME、视觉按需截图、生活记录角色可读、小红书读取等多半默认关。能聊天 ≠ 会主动找你、会截屏、会动硬件。

---

## 当前能做什么（按域）

### 对话与通道

- **Owner 私聊**：QQ（可选）、`POST /desktop/chat`、`POST /mobile/chat` 共用 `run_owner_chat_turn()` 与 `core/conversation_gate.py` 的 per-user 锁。同一用户多端不会并行进入 `fetch_context → LLM → 关键后处理`。
- **外部脚本/硬件发言**：`POST /v1/owner/turns`（profile `owner-input`，幂等 `client_turn_id`），见 [docs/owner-turn-api.md](docs/owner-turn-api.md)。
- **主动下行**：桌宠优先 `/ws/desktop`，瞬时失败可降级文件队列（remote 部署不写本机 fallback）；手机走 durable `/mobile/poll` + `/mobile/ack`，可选 ntfy/relay **只发 signal**；ESP32 走 `/ws/device`（无文件降级）。
- **跨通道接续**：切换通道时注入接续提示；canonical 回复文本是记忆真相，桌面 `message_segments` 只是叙事视图。
- **媒体**：图片识别（独立视觉连接 + OCR + 手机覆盖）、上传 ingest、可选 STT（命名连接或本地 Whisper）、TTS（GPT-SoVITS 等）、情绪表情包（与 TTS 互斥；QQ 走图片段，桌宠/手机走自包含 sticker payload）。
- **思考/独白**：可选 native reasoning 归档（不进记忆）、前置独白、角色心声文风；桌面可展开，手机思考 UI 仍为 roadmap。见 [docs/thinking-voice.md](docs/thinking-voice.md)、[docs/audio-perception.md](docs/audio-perception.md)。

### 记忆（后端独占）

并行层，不是单一向量库：

| 层 | 作用 |
|---|---|
| 短期 history | 近场滑动窗口，读时脱敏，防风格自反馈 |
| 中期 mid_term | 约 12h 压缩视图，三时间桶 |
| 情景 episodic | mid_term 晋升；强度衰减、MMR、去重 |
| user_identity | 角色对你的长期观察；固化链 capture → mid_term → episodic → identity |
| event_log | 按日流水账，关键词 + 强度；过期前可 salvage 持久事实 |
| Memory Event ledger | 现实 dual-write 证据账本；默认不进 prompt；工具 `search_events` 等仅 Path C + memory 分类 |
| 向量库 | 语义召回；web 来源与梦境同等隔离，不固化进 identity |
| user_hidden_state | 隐性状态 + 12h 衰减 / 7d 基线；Dream 只读 snapshot |
| storyline | append-only 叙事弧 + 周频聚合；淘汰片段进 inbox 而非物理删除 |
| 生活记录 | 独立 SQLite；手机同步；角色可读需开关；不自动写长期记忆 |
| LLM reasoning archive | API 返回的思考默认落独立库，admin-only 读，不回流 prompt |

遗忘策略是降级/墓碑，不是随便物理删证据。并发：`uid_lock` + 全局 mood 锁 + 原子写入。详见 [docs/memory.md](docs/memory.md)、[docs/data-taxonomy.md](docs/data-taxonomy.md)、[docs/vector-store.md](docs/vector-store.md)、[docs/life-records.md](docs/life-records.md)。

### Prompt 与模型

- 分层 prompt（tag 门控 + token 质量梯度裁剪）。现实层包括人设、来源边界、时间、在场、世界书、用户画像、情绪、情景/中期/事件、资料接续 `10.6–10.8`、工具痕迹、Author's Note 轮转等。权威表：[docs/prompt-layers.md](docs/prompt-layers.md)。
- 多 preset：DeepSeek / OpenAI Chat Completions / Responses / Anthropic 兼容 / 本地；routing profile 按用途切 `chat` / `probe` / `intent` / `sensor_judge` / `ime_judge` / `monologue` / `rpg_kp` / `consolidation` 等。角色卡可绑整个 profile。见 [docs/model-presets.md](docs/model-presets.md)。
- 需要自备 API Key。Embedding 可选；未配置则关键词召回。

### 工具、MCP、Agent Runtime

- 所有工具必须进 `_TOOL_REGISTRY`，经 `execute(origin=...)` 闸门。未知 origin fail-closed。
- **Path A**：关键词快路径 + LLM probe（info/desktop）；Path C 有效时跳过普通 probe。
- **Path C tool loop**：全局默认关；角色卡 `presence_ext.tool_loop` 可覆盖。function-calling 主循环按分类发现加载 schema。
- 类别示例：时间/天气/备忘、网页搜索、日记/玩具文件、记忆读写、生活记录、花园、桌面窗口动作、屏幕观察、小红书只读、聊天产物沙盒、硬件（默认冻结）等。
- **MCP**：可选外部工具传输，不是客户端协议；默认关；需本地 allowlist。实验性，不阻塞聊天。
- **危险模式**：`PATCH /system/meta-mode`；关机/睡眠等仍需二次确认。手机 token **不含** `hardware`/`admin`。
- **Agent Runtime**（Reality）：持久任务、有界 work session、受控 workspace、process runner、隔离 browser worker。启动时遗留 `running` → `outcome_unknown`，不自动重放。Dream 不共用这套 runtime。见 [docs/agent-runtime-architecture.md](docs/agent-runtime-architecture.md)、[docs/tools.md](docs/tools.md)。

### 主动性（scheduler + autonomy）

调度器/传感器**只产事实 signal**，不写台词。同一 tick 合并为 `autonomy-opportunity.v1`，由 `core/autonomy` 评估；只有显式 `talk_owner` 才进 `turn_sink`。旧直发路径已封存。见 [docs/autonomy.md](docs/autonomy.md)、[docs/scheduler.md](docs/scheduler.md)。

候选事实包括：早安/晚安/日间碎碎念、天气、日记、生日多段、未完话题、主动回忆、节日/时间节点、心率/睡眠、出梦开口、花园事件、IME 活动理解、桌面 reopen（`POST /desktop/wake` Path B 只入队 signal）、overflow、信件等。维护类（衰减、janitor、event salvage、hidden-state）不发言。DND、active-user、Dream、预算、对话锁均可挡住开口。高优先级（生日/生理期/心率）提高 urgency，**仍不绕过** autonomy 闸门。

### 梦境、群聊、活动

- **Dream**：独立 D0–D10 层栈；sandbox / scenario / mirror；软退出 + 硬退出。RPG Dream 后端 API 已有（`dream_mode=rpg`），桌面双栏 UI / 手机消费未完成。群聊梦境（Dream Stage）仅 sandbox，零回流，hard_exit 绝对。
- **Reality Stage**：多角色群聊，规则仲裁 Phase A/B/R/T，一次 owner 锁，零后台自发 LLM。
- **ActivitySession**：阅读 / 五子棋 / 国际象棋 / dream_seed；显式 API 生命周期，不进短期记忆。
- **Coplay**：桌面陪看游戏（观察者，非代打）；会话结束浓缩 `game_log`，不进主记忆链。
- **花园**：五槽情绪花，自动/工具浇水；状态不直接进 prompt，事件可变主动机会。客户端只读。

### 感知与外部世界

- 手机传感器、Watch 心率/睡眠、桌面屏幕活动快照（TTL）、按需截图（双闸：后端开关 + 设备本地授权，默认关）。
- IME 草稿 inbox：接收 ≠ 已读 ≠ 会说话。
- Obsidian 日记、邮件来信（SMTP）、支出余额只读观测（绝不自动付款）。
- External Companion：`POST /integrations/companion/events`（如游戏内邀请/饮酒衰减观察），后端决定是否开口。
- 小红书分享只读工具（可选本地托管读取服务）。
- Wake Bridge：外部论坛等 durable inbox，再进入既有主动性链。

### 管理面

浏览器打开后端即可。页面覆盖：首次配置、角色卡、模型路由、功能总开关、调度/自主预算、梦境设定、MCP、token、生活记录、观测中心（记忆/梦境/工具/API 账本/runtime signals/agent browser…）。密钥本快捷打开仅 loopback。

---

## 主要架构与执行链

```
QQ / NapCat                 → main.py → message_queue
桌宠 POST /desktop/chat     ─┐
手机 POST /mobile/chat      ─┼→ conversation_gate → Pipeline
Owner Turn API              ─┘
调度/传感器                 → autonomy-signal → opportunity → talk_owner? → turn_sink
梦境                        → 独立 dream pipeline（不写现实 memory / 不跑 scheduler）
```

**现实 Pipeline（`core/pipeline.py`）**

0. 探针 / Path C 分类发现  
1. `fetch_context()` 并发拉记忆、关系、世界书、日记、向量等  
2. `prompt_builder.build()` tag 门控组装  
3. 主生成：tool loop 或单次 `llm_client.chat`  
4. `turn_sink`：send 前只做毫秒级本地落盘（history/event_log）；send 后异步情绪、固化、TTS  

输出经 `channels.registry.broadcast()` 或 HTTP 直接返回。QQ 可见发送另走 OneBot adapter，记忆仍统一 `record_assistant_turn()`。

启动顺序见 [docs/runtime-lifecycle.md](docs/runtime-lifecycle.md)：配配置 → 校验鉴权 → 加载角色 → Pipeline → 恢复 Agent 任务（遗留 running 标 unknown）→ HTTP。

数据治理：`core/data_paths.py` + `sandbox.get_paths()`；**禁止硬编码 `data/`**。用户私有 authored 在 `userdata/characters/`；发行只读种子在 `bundled/`。

---

## 后端 vs 客户端（能力表）

| 能力 | 后端 | 桌面 | 手机 |
|---|---|---|---|
| 人格 / prompt / 记忆写入 | 权威 | 展示历史 | 展示历史 |
| 主动要不要说话 | 权威 | 渲染/通知 | poll + 通知；relay 仅唤醒 |
| 桌面窗口最小化等 action | 发出 allowlist action | 执行并 ack（协议 v0.1） | 无 |
| 硬件 Intiface | 闸门 + 工具 | 可持 hardware scope | **无 hardware scope** |
| 截屏 / 前台观察 | 策略、冷却、注入 | 采集与本地授权 | 系统设置可撤销授权 |
| 生活记录采集 UI | 存储/识别/权限 | 管理面配置 | v1.1.0 采集/同步 UI |
| 聊天热力图 | `/chat-log/stats/calendar` | roadmap | v1.1.0 资料页 |
| 梦境 HUD / 外观 | Dream API / 状态 | HUD（消费后端） | 背景/字号/醒来确认 |
| 群聊 Stage | 后端会话 | 视客户端是否接 | 手机已去掉群聊入口 |
| 管理配置 / token | 权威 | 可嵌面板 | 只连后端，不持 admin |
| ESP32 固件 | `/ws/device` | — | — |

---

## 权限与能力范围

单用户、Bearer opaque token、**default-deny**。实现：[docs/security.md](docs/security.md)；威胁模型：[docs/security_model.md](docs/security_model.md)。

**Scope**：`admin`（全权）、`chat`、`state.read`、`memory.read`、`sensor.write`、`integration.write`、`companion.write`、`diary.sync`、`life_records`、`activity`、`persona`、`hardware`、`ws.desktop`、`ws.device`。

**首次 `scripts/setup_auth.py` 签发的典型 profile**

| profile | 给谁 | 刻意不含 |
|---|---|---|
| `panel` | 管理面板 | —（admin） |
| `desktop` | 桌宠 | admin |
| `mobile` | 手机 | hardware、admin、ws.desktop |
| `watch` | Watch 捷径 | 除 sensor.write 外全部 |
| `device` | ESP32 | 除 ws.device 外全部 |
| `owner-input` | 脚本/硬件发言 | 仅 chat |

明文 token 只在创建/轮换时出现一次。401=不认识，403=scope 不够，429=401 限速（内存态，重启清除）。把 admin secret 发到手机是错误用法。

LLM **不能**直接执行系统能力。工具还受角色权限、分类暴露、danger 模式、确认流、origin、Dream/群聊禁入规则约束。

---

## 配置、启动、使用

### 环境

- Python **3.10–3.12**（推荐 3.12；3.13+ 因 `rapidocr-onnxruntime` 暂不支持）
- 自备聊天模型 API；可选 embedding、TTS、STT、NapCat、Docker（小红书本地读取）
- 默认绑定 `127.0.0.1:8080`。局域网/远程必须自己上 HTTPS 反代，不要把明文 HTTP 和 break-glass secret 暴露到公网。

### Windows 发行包 / 源码快捷方式

1. `AA1安装并启动.bat` — uv 安装 Python 3.12、`.venv`、按 `requirements.lock` 装依赖；无 `config.yaml` 则从 example 复制。
2. `AA2鉴权初始化.bat` — `python scripts/setup_auth.py`，写入 gitignore 的 `secrets.local.yaml`。
3. `AA3启动.bat` — `python main.py`。不接 QQ 时设 `standalone_mode: true`。
4. 浏览器打开面板，用 `admin_secret` 登录；填基础聊天模型 + `owner_id`（建议用 QQ 号，否则以后接 QQ 会另起记忆）。
5. 新建自己的角色卡（自带 `default` 只是占位名「角色名」）。
6. 用面板聊天、桌宠或手机发第一条消息。

更新：源码用 `AA更新.bat`（先停服务）；解压发行包用内置更新器，只覆盖程序文件，保留 `data/`、`userdata/`、`config.yaml`、`secrets.local.yaml`、`.venv/`、`tools/uv*`。v1.0.0 起才支持自动向前升级；v0.x 必须备份后全新安装。见 [docs/backend-upgrade-recovery.md](docs/backend-upgrade-recovery.md)。

### macOS（arm64 / x64 发行包）

发行 zip 内含对应平台 `tools/uv`。在空目录解压后：

```bash
chmod +x tools/uv
tools/uv python install 3.12
tools/uv venv --python 3.12 .venv
tools/uv pip sync requirements.lock --python .venv/bin/python
cp config.example.yaml config.yaml
.venv/bin/python scripts/setup_auth.py
# 编辑 config.yaml：standalone_mode: true（若不接 QQ）
.venv/bin/python main.py
```

桌宠/手机填 `http://<Mac的局域网IP>:8080` 时，需把 `admin.host` 改成可达地址并配好 token；优先反代，不要裸奔公网。

### 源码安装（任意平台）

```bash
git clone https://github.com/cicikat/PresenceKit.git
cd PresenceKit
pip install -r requirements.txt   # 或 uv pip sync requirements.lock
cp config.example.yaml config.yaml
python scripts/setup_auth.py
python main.py
```

`config.yaml` 是唯一运行时配置；面板写的也是它。`config.example.yaml` 带注释，键集合由 `scripts/gen_config_example.py` 同步。测试隔离：`python run_test.py`（写入 `data/test_sandbox/`）。

角色卡 live 路径：`userdata/characters/cards/`（`.json` / `.txt` / `.md`）。模板：`bundled/templates/character_template.json`。私有世界书/梦境素材在 `userdata/characters/`。

### 接客户端

- **桌面**：后端保持运行；客户端填 URL + desktop token。未签名安装器会 SmartScreen。协议权威在桌面仓 `docs/protocol-v0.md`。
- **手机**：局域网 IP 或 `adb reverse`；mobile token。前台 `/mobile/chat`，后台 poll/ack；`relay_*` 可选。
- **QQ**：NapCat WebSocket（示例 3001）+ `qq.enabled` + 非 standalone；改 QQ 开关需**重启**。
- **Watch**：捷径 POST `/watch/event`，`watch` token。
- **ESP32**：烧录 `firmware/presence-device/`，device token 连 `/ws/device`。

冷启动清单：[docs/v1-cold-start-single-user-deployment.md](docs/v1-cold-start-single-user-deployment.md)。Token 轮换：[docs/token-rotation.md](docs/token-rotation.md)。

---

## 部署形态

| 形态 | 要点 |
|---|---|
| 本机桌宠 | `standalone_mode: true`，bind loopback |
| 本机 + QQ | NapCat 同机，再启后端 |
| 家庭服务器 | Ubuntu systemd 示例见 `docs/user-teach/ubuntu-single-user-deployment.md`；用 Tailscale 等私网，不要公开 8080 |
| 备份 | `python main.py backup-state create --output <受保护卷>`；恢复不自动切 live 目录 |

---

## 测试与 CI

```bash
pytest                          # 任务相关即可；全量 pytest -n auto
python tests/run_eval.py        # 改 tag_rules 后
python tests/run_identity_eval.py
```

GitHub `tests.yml`：3.10/3.12 smoke + main 上 full pytest（`config.example.yaml`，无私有角色）。发布阻断以 CI 为准。

---

## 文档地图

| 文档 | 内容 |
|---|---|
| [docs/README.md](docs/README.md) | 后端文档入口 |
| [docs/three-repo-doc-index.md](docs/three-repo-doc-index.md) | 三仓按功能直达 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 总览与 pipeline |
| [docs/channels.md](docs/channels.md) | QQ / 桌宠 / 手机 / 设备 |
| [docs/api-reference.md](docs/api-reference.md) | HTTP/WS 端点族 |
| [docs/memory.md](docs/memory.md) | 记忆 |
| [docs/prompt-layers.md](docs/prompt-layers.md) | Prompt 层 |
| [docs/tools.md](docs/tools.md) | 工具 / MCP |
| [docs/scheduler.md](docs/scheduler.md) / [docs/autonomy.md](docs/autonomy.md) | 主动 |
| [docs/dream.md](docs/dream.md) / [docs/stage.md](docs/stage.md) | 梦境与群聊 |
| [docs/security.md](docs/security.md) | Token / scope |
| [docs/feature-control-surface.md](docs/feature-control-surface.md) | 开关与 effective state |
| [docs/known-issues.md](docs/known-issues.md) | open / observe |
| [docs/v1-release-contract.md](docs/v1-release-contract.md) | v1 保证面 vs 实验性 |
| [docs/release-guide.md](docs/release-guide.md) | 打 release |

带日期的排查快照不是运行时真值。

---

## 注意

- PolyForm Noncommercial 1.0.0：非商用允许；商用需作者另行许可。
- 自备模型与角色卡；仓库不包含任何角色版权素材。
- 禁止把真实密钥、QQ 号、手机号、本机绝对路径写进会入库的文件。
- 部分兼容路径仍可能出现历史 `yexuan` 字段名，不等于产品绑定该角色。

## 贡献者

- cicikat — 项目作者
- Codex — 编码助手
- Claude Code — 编码助手
- Grok — 编码助手

## License

PolyForm Noncommercial License 1.0.0.
