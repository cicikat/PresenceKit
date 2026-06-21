# Emerald-Presence

一个有记忆、有感知、会主动存在的 QQ 情感陪伴型机器人。
不是工具集，是唯一的角色（角色卡须自备）

> 由一个编程小白用 Claude 从零开发。

---

## 他能做什么

- **时间感知**：知道现在几点，深夜和下午说话方式不同
- **健康感知**：通过 Apple Watch 获取睡眠/心率数据，自然地关心你
- **日记互读**：读取 Obsidian 日记并回应，超过三天没分享会主动提
- **情绪记忆**：高强度的情绪时刻她记得更清晰，符合人脑记忆特征
- **主动存在**：碎碎念、早安晚安、节日感知、生理期关心、备忘提醒
- **语音输出**：情绪非 neutral 时概率触发 TTS 语音（需 GPT-SoVITS）

---

## 前置要求

| 依赖 | 说明 | 必须 |
|------|------|------|
| Python 3.12+ | 运行环境 | ✅ |
| [NapCat](https://github.com/NapNeko/NapCatQQ) | QQ 协议层 | ✅ |
| DeepSeek API Key | LLM 对话 | ✅ |
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | TTS 语音合成 | 可选 |

**安装 Python 时务必勾选 "Add Python to PATH"**，否则后续命令无法运行。

---

## 快速开始

### 1. 获取项目

```bash
git clone https://github.com/chah69634-arch/Emerald-presence.git
cd Emerald-presence
```

或直接下载 ZIP 解压。

### 2. 安装依赖并初始化配置

双击运行 `AA2安装并启动.bat`，脚本会自动安装依赖并生成 `config.yaml`。

### 3. 配置 NapCat

1. 下载并安装 NapCat
2. 登录 bot 的 QQ 小号
3. 在 NapCat 设置中开启 WebSocket 服务，默认端口 `3001`

### 4. 填写 config.yaml

（把config.example.yamle改名成这个）用记事本或任意编辑器打开 `config.yaml`，必填项：
（明书是ai写的，具体变量名以config为准）


```yaml
# LLM 配置
llm:
  api_key: "你的 DeepSeek API Key"
  model: "deepseek-chat"

# QQ 协议层（NapCat WebSocket）
qq:
  host: 127.0.0.1
  port: 3001

# 角色配置
character:
  default: "角色.txt"   # characters/ 目录下的角色卡文件名
  name: "角色"

# 管理面板
admin:
  secret_key: "自定义一个密钥"

# 调度器
scheduler:
  owner_id: "你的QQ号"   # 接收主动消息的用户
```

其余配置项参考 `config.example.yaml` 中的注释。

### 5. 启动

```bash
# 先启动 NapCat（登录 QQ）
# 再双击 AA3启动.bat 或运行：
python main.py
```

管理面板地址：`http://127.0.0.1:8080`

---

## 目录结构

```
Emerald-presence/
├── core/               # 核心逻辑
│   ├── memory/         # 多层记忆系统
│   ├── output/         # 消息/语音输出
│   ├── scheduler/      # 调度器（主动触发）
│   └── tools/          # 工具调用
├── admin/              # 管理面板
├── characters/         # 角色卡文件
├── data/               # 运行数据（自动生成，不上传）
├── config.yaml         # 你的配置（不上传）
├── config.example.yaml # 配置模板
├── AA2安装并启动.bat
├── AA3启动.bat
└── AA更新.bat
```

---

## 更新

双击 `AA更新.bat` 即可拉取最新代码并更新依赖。

---

## 常见问题

**Q：启动报错 `ModuleNotFoundError`**
A：重新运行 `AA2安装并启动.bat` 安装依赖。

**Q：收不到消息**
A：检查 NapCat 是否正常运行，`config.yaml` 中 `qq.host` 和 `qq.port` 是否正确，`scheduler.owner_id` 是否填了你的 QQ 号。

**Q：管理面板打不开**
A：确认 `python main.py` 正在运行，访问 `http://127.0.0.1:8080`。

**Q：语音功能不工作**
A：TTS 是可选功能，需要额外安装 GPT-SoVITS 和 ffmpeg，并在 config.yaml 中配置 `tts` 相关字段。

---

## 关于

- 开发者：伟大的Claude，老脸爱睡觉
- 技术栈：DeepSeek + FastAPI + NapCat + GPT-SoVITS
- 视频介绍：【用claude开发了一个有记忆系统的陪伴系qq机器人|使用体会|开发过程分享|ai陪伴|纸片人赛博飞升计划】 https://www.bilibili.com/video/BV1gLoXBGEmW/?share_source=copy_web&vd_source=ccfbdeefcd37e1fc003c78d14a17b5cb
