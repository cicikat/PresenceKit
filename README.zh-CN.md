[English](README.md) | [简体中文](README.zh-CN.md)

# PresenceKit

一个有长期记忆、情绪状态、能主动联系你的私人 AI 陪伴后端。QQ 机器人只是众多可选接入通道之一。

---

## 三仓关系

```
PresenceKit（本仓，后端）
  ├── PresenceKit-desktop  Tauri 桌宠 + 管理面板客户端
  └── PresenceKit-mobile   Flutter 手机客户端
```

后端是唯一的真相源：长期记忆、情绪状态、调度器、工具系统、角色人格全部在这里。桌面端和手机端都是**瘦客户端**——只负责界面展示和用户交互，不拥有业务数据。三者通过 HTTP / WebSocket 通信，桌面端与手机端可以只连其一，也可以都不连（纯 QQ 机器人模式）。

---

## 特性

### 记忆系统（五层并行）

- **短期历史**：滑动窗口，最近 20 轮对话，读取时脱敏防止风格自反馈塌缩
- **中期摘要**：12 小时内的对话压缩视图，三时间桶渲染（刚才 / 几小时前 / 早些时候），LLM 压缩 + fallback 兜底
- **情景记忆**：由 mid_term 经 eager/sweep 晋升为结构化片段，含 strength 衰减、MMR 多样性召回、emotion_texture 去重
- **稳定行为模式**（user_identity）：角色对你的长期观察，由固化 pipeline 四段链路驱动（capture → midterm → episodic → identity），重启不丢状态
- **事件流水账**（event_log）：每日按天分文件，支持关键词搜索 + 强度衰减评分，7 天外低强度条目自动跳过

`character_growth` 仍作为 legacy 兼容数据保留，但已不是现实 prompt 的长期认知主入口。

### 情绪状态系统

- 每轮对话后 LLM 检测角色回复情绪，写入 `mood_state`
- 情绪漂移公式：`新强度 = 旧强度 × 0.7 + 新情绪强度 × 0.3`，切换需连续两轮确认
- 情绪底色以软提示形式注入 prompt，随强度分三档描述
- 情绪联动情景记忆召回评分，记忆越被想起越牢固

### Prompt 架构（12+ 层）

- 分层 prompt 架构，含 tag 门控、token 估算与质量梯度裁剪
- 世界书 / 角色卡 / 用户画像 / 实时状态 / 情绪底色 / 情景记忆 / 中期摘要 / 活动状态 / 角色日记 / Author's Note 轮转
- 探针机制：正式对话前用关键词快速路径 + 极简 LLM probe 预判 info/desktop 工具调用
- 层 11 Author's Note：性格特质轮转 + 纠偏注入（consistency_check 发现问题时追加）
- token 超限时按质量从低到高依次裁剪

### 梦境系统

- 独立 Dream Session pipeline：不进入现实对话 post-process，不写现实 history / memory，不触发 scheduler
- 入梦时冻结现实上下文快照，梦内使用独立 D0-D10 prompt 层栈、世界包和 lorebook
- 支持软退出与不可阻挡的硬退出；退出后归档梦境原文，并提炼低权重梦境印象
- 现实 prompt 只接收剥离场景细节后的 `6g_dream_impression`，避免把梦境误记成现实

### 主动触发调度器

- 早安 / 晚安 / 随机日间碎碎念（从有情感词的历史发言中抽取触发素材）
- 天气联动、每日手账（角色写日记）、记忆自然衰减
- 生日多段触发：前夜预热 / 零点告白 / 下午关心 / 夜间收尾
- 未完结话题追问、主动回忆触发
- 节日感知 / 时间节点感知 / 长假加速发送
- 情景记忆定期扫描晋升（episodic_sweep，冷却 30 分钟）
- 请勿打扰（DND）模块（已实现，可接入）
- 高优先级触发（生日 / 生理期 / 心率告警）用户活跃时也强制发送
- 冷却状态持久化，重启不丢失

### 情绪花园

- 角色拥有独立花槽，支持自动浇水、用户催促浇水、开花与采后处理
- 花园状态由管理面板读取，关键事件可进入主动触发调度器

### 现实数据感知

- **Apple Watch**：心率异常提醒（>100 低优先级 / >120 高优先级告警）、睡眠感知与报告（iPhone 捷径推送）
- **Obsidian 日记**：按日期读取，支持关键词搜索最近 30 天，读后标记已共享
- **生理期感知**：周期中和临近期 tag 门控，自动注入关怀层
- **手机传感器**：步数 / 电量 / 位置 / 亮屏次数，当天有数据即注入
- **桌宠屏幕活动快照**：TTL 5 分钟，tag 命中时注入

### 对话能力

- 图片识别（GLM / Gemini / OpenAI Vision）
- TTS 语音合成（GPT-SoVITS，情绪联动参考音频切换）
- 表情包发送（情绪联动，与 TTS 互斥）
- 工具调用：天气查询、备忘录提醒、网页搜索（DuckDuckGo）、桌面控制；memory 类工具已注册但尚未接入正式主 LLM 自动调用
- 桌面意图解析：角色说"我去把游戏关掉"→ 真的执行窗口最小化
- QQ / 桌宠 / 手机轮询三通道；桌宠主动下行优先 WebSocket，失败时降级到文件队列
- 桌宠 WebSocket 支持叙事分段 `message_segments` 视图，原始回复仍是记忆链路的 source of truth
- 跨通道连续性感知，切换时注入接续提示

### 工程质量

- 数据路径统一通过 `core/data_paths.py` 实现、`core/data_registry.py` 登记治理元数据、`core/sandbox.py` 提供单例胶水、`core/migration.py` 负责迁移期兼容读
- 测试模式把数据整体偏移到 `data/test_sandbox/{session_id}/`，不污染生产数据
- 原子写入（`safe_write`，跨平台 `os.replace`）
- LLM 输出校验 + 最多 3 次重试，失败保留旧数据
- Post-process 拆分为关键路径（持锁）和慢队列（单 worker，退避重试），避免锁饥饿
- 慢任务失败写入死信队列（DLQ），调度器定期监控
- 并发保护：per-uid 锁 + 全局情绪状态锁

---

## 技术栈

Python · FastAPI · NapCat (OneBot 11，可选) · DeepSeek / 任意兼容 LLM API · GPT-SoVITS（可选）

---

## 快速开始

**环境要求**

- Python 3.10–3.12（推荐 3.12；暂不支持 3.13+ —— `rapidocr-onnxruntime` 声明要求
  `<3.13`）

**安装**

```bash
git clone https://github.com/cicikat/PresenceKit.git
cd PresenceKit
pip install -r requirements.txt
```

**Windows 快捷方式**：也可以不走下面的手工步骤，直接双击 `AA1安装并启动.bat`
（装依赖、生成 `config.yaml`），填好 `config.yaml` 后依次双击
`AA2鉴权初始化.bat`（首次运行前必做的鉴权初始化）与 `AA3启动.bat`（启动）。
`AA2` 结束后会自动打开密码本与管理面板——面板首次打开会自动落在「配置」页，
按红色标记填完必填①基础聊天模型、必填② `owner_id` 即可开始聊天（详见下方「配置」）。
后续更新用 `AA更新.bat`（`git pull` + 重装依赖）。

**配置**

```bash
cp config.example.yaml config.yaml
```

按 `config.example.yaml` 中的注释填写必填项：LLM API Key、管理面板密钥、`scheduler.owner_id`；
如需 QQ 机器人再填 QQ 号。也可以跳过手改 yaml，直接在管理面板「配置」页填写这两项必填项。

`owner_id` 建议直接填你的 QQ 号——若这里用了别的 id，之后接 QQ 时会按 QQ 号另起一套记忆，
与桌宠期记忆不互通；留空会导致主动触发调度器静默跳过。

在 `characters/` 目录放入角色卡文件；当前 loader 支持 `.json`、`.txt` 和 `.md`，可参考 `examples/character_template.json`。仓库自带一份中性的 `default` 角色卡,开箱即可用。

**初始化鉴权**（首次运行前）

```bash
python scripts/setup_auth.py
```

自动生成管理面板密钥 + 各设备 token，写入本地密码本 `secrets.local.yaml`（已 gitignore）。
详见 [docs/token-rotation.md](docs/token-rotation.md)。

**运行**

```bash
# 只用桌宠或手机端：在 config.yaml 设置 standalone_mode: true，跳过 NapCat 连接
python main.py
```

如需接入 QQ：先启动 NapCat，确保 QQ 已登录、WebSocket 服务端监听 3001 端口，再启动 `python main.py`。

测试模式会把数据整体隔离到沙盒目录，不污染生产数据：

```bash
python run_test.py
```

管理面板：`http://127.0.0.1:8080`

---

## 可选接入

- **QQ / NapCat**：见上方「运行」一节；不需要 QQ 机器人时用 `standalone_mode: true` 跳过。
- **桌面端**：见 [PresenceKit-desktop](https://github.com/cicikat/PresenceKit-desktop)，需要后端保持运行。
- **手机端**：见 [PresenceKit-mobile](https://github.com/cicikat/PresenceKit-mobile)，通过局域网 IP 或 `adb reverse` 连接。
- **TTS**：GPT-SoVITS，配置见 `config.example.yaml` 中 TTS 相关字段。
- **Apple Watch**：通过 iPhone 捷径把心率 / 睡眠数据推送到后端接口，具体字段见 `config.example.yaml` 与 [docs/known-issues.md](docs/known-issues.md)。

---

## 测试

```bash
pytest
python run_test.py
```

改过 `tag_rules` 相关逻辑后建议再跑一次评测集：

```bash
python tests/run_eval.py
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构总览、Pipeline 四步骤、数据目录结构 |
| [docs/memory.md](docs/memory.md) | 五层记忆子系统设计与并发保护 |
| [docs/prompt-layers.md](docs/prompt-layers.md) | Prompt 层结构、Tag 门控、token 裁剪 |
| [docs/tools.md](docs/tools.md) | 工具系统、探针机制、桌面动作执行 |
| [docs/scheduler.md](docs/scheduler.md) | 调度器触发器完整列表与冷却设计 |
| [docs/channels.md](docs/channels.md) | QQ / 桌宠通道、WebSocket、文件降级与跨通道接续 |
| [docs/garden.md](docs/garden.md) | 情绪花园、自动/被动浇水、采后处理、管理面板状态接口 |
| [docs/dream.md](docs/dream.md) | Dream Session 隔离边界、独立 prompt、世界包与印象回流 |
| [docs/data-taxonomy.md](docs/data-taxonomy.md) | 当前 datapath 布局、治理元数据与迁移期兼容读 |
| [docs/assistant-turn-sink.md](docs/assistant-turn-sink.md) | assistant turn 统一写入、广播与叙事分段协议 |
| [docs/security_model.md](docs/security_model.md) | 管理面板、桌宠 WebSocket 与客户端密钥边界 |
| [docs/security.md](docs/security.md) | 鉴权模型（scoped tokens）：scope/profile 表、token 管理 API |
| [docs/token-rotation.md](docs/token-rotation.md) | 首次配置、各设备 token 轮换命令、401/403/429 排障 |
| [docs/fresh-clone-testing.md](docs/fresh-clone-testing.md) | 全新 clone 后如何正确测试（避免连上旧后端进程/旧数据） |
| [docs/known-issues.md](docs/known-issues.md) | 当前技术债与已核对修复项 |

---

## 注意

- 仅供个人学习使用
- 需自备 LLM API Key（推荐 DeepSeek，国内直连）
- 角色卡需自行准备，`characters/` 目录有格式示例；仓库自带的 `default` 角色卡不含任何真人隐私信息
- 本项目不包含任何角色版权素材
- 部分默认值、兼容路径和历史文档里仍保留 `yexuan` 命名，v0.2 起计划逐步统一，不影响当前功能

---

## License

This project is licensed under the PolyForm Noncommercial License 1.0.0.

Noncommercial use is permitted. Commercial use is not permitted without separate permission from the author.
