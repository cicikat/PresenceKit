# qq-st-bot

一个有长期记忆、能主动联系你的私人陪伴型 QQ 机器人。

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

Python · FastAPI · NapCat (OneBot 11) · DeepSeek · GPT-SoVITS

---

## 快速开始

**环境要求**

- Python 3.10+
- [NapCat](https://github.com/NapNeko/NapCatQQ)（QQ 协议端）

**安装**

```bash
git clone https://github.com/chah69634-arch/qq-st-bot.git
cd qq-st-bot
pip install -r requirements.txt
```

**配置**

```bash
cp config.example.yaml config.yaml
```

按 [AA1先看说明书正式版README.md](AA1先看说明书正式版README.md) 和 `config.example.yaml` 填写必填项：LLM API Key、QQ 号、管理面板密钥。

在 `characters/` 目录放入角色卡文件；当前 loader 支持 `.json`（SillyTavern）、`.txt` 和 `.md`，可参考 `characters/character_template.json`。

**运行**

```bash
# 1. 启动 NapCat，确保 QQ 已登录，WebSocket 服务端监听 3001 端口
# 2. 启动机器人
python main.py
```

只使用桌宠或手机端时，可在 `config.yaml` 设置 `standalone_mode: true`，跳过 NapCat 连接。

测试模式会隔离数据写入：

```bash
python run_test.py
```

管理面板：`http://127.0.0.1:8080`

---

## 文档

| 文档 | 内容 |
|---|---|
| [AA1先看说明书正式版README.md](AA1先看说明书正式版README.md) | 安装、启动、常见问题 |
| [AAWatch配置指南.md](AAWatch配置指南.md) | Apple Watch 心率 / 睡眠数据接入（iPhone 捷径） |
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
| [docs/known-issues.md](docs/known-issues.md) | 当前技术债与已核对修复项 |

---

## 注意

- 仅供个人学习使用
- 需自备 LLM API Key（推荐 DeepSeek，国内直连）
- 角色卡需自行准备，`characters/` 目录有格式示例
- 本项目不包含任何角色版权素材
- 本项目以叶瑄为示例角色。常用角色名可在 `config.yaml` 的 `character.name` 中调整；部分默认值、兼容路径和文档仍保留 `yexuan` / “叶瑄”命名。

---

## License

MIT
