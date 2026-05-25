前提：这份文档是gpt读完架构文档和接口文档后写的，仅供参考，具体参考代码

# security_model.md（草案）

> 当前阶段以“功能先完成”为主。
> 本文档用于记录未来开源、社区化、插件化后必须处理的安全与架构风险。

---

# 核心原则

## 1. 社区资源默认不可信

所有：

* 角色包
* 主题包
* 插件
* 表情包
* 动作包
* prompt preset

都必须视为“不可信输入”。

禁止默认信任任何社区内容。

---

## 2. LLM 输出默认不可信

LLM：

* 可能 hallucination
* 可能 prompt injection
* 可能输出危险工具调用
* 可能越权访问

因此：

* 工具权限必须由后端控制
* 不允许“模型说了就执行”
* 所有危险操作必须经过权限检查

---

## 3. 后端白名单 API 才可信

只有：

* channel
* tool_dispatcher
* sandbox
* registry
* approved actions

等后端白名单系统可以真正执行操作。

禁止：

* 插件直接写 memory
* 插件直接写 queue 文件
* 插件直接操作本地系统

---

# 一、社区包安全

## 风险

社区包可能：

* 携带恶意代码
* 路径穿越
* prompt 注入
* 资源炸弹
* 覆盖系统文件

---

## 第一阶段允许的文件类型

建议仅允许：

* json
* yaml
* png
* jpg
* webp
* gif
* mp3
* wav
* md
* txt

禁止：

* exe
* dll
* bat
* ps1
* sh
* py
* js
* ts
* jar

---

## 导入校验

导入时必须检查：

* schema_version
* manifest
* 文件大小
* 文件数量
* 解压后总大小
* 文件类型白名单
* 路径穿越（../）

---

## 包结构建议

```text
package/
├── manifest.json
├── character/
├── emotes/
├── motions/
├── themes/
├── lore/
└── preview/
```

---

# 二、Prompt Injection

## 风险

角色卡可能包含：

* 忽略系统 prompt
* 读取本地文件
* 泄露 API key
* 执行桌面动作
* 绕过安全层

---

## 防护原则

角色卡只能影响：

* 人设层
* 对话层
* lore 层

禁止影响：

* system rules
* tool permission
* sandbox policy

---

## 工具调用规则

工具调用必须：

* 后端白名单验证
* 参数校验
* 权限校验

不能只依赖 LLM 判断。

---

# 三、本地文件与用户隐私

## 高风险数据

以下数据属于高隐私：

* profiles
* episodic_memory
* history
* diary
* character_growth
* data/dreams/
* mood_state
* API keys

---

## 建议目录隔离

```text
data/
├── core_private/
├── packages/
├── user_assets/
└── imported_characters/
```

---

## 原则

社区资源：

* 只能读自己的目录
* 不能直接访问 memory
* 不能读取 config/.env

---

# 四、桌面动作安全

## 风险

LLM 可能：

* 无限发送动作
* 打开危险网站
* 删除文件
* 模拟危险行为

---

## 动作分级

### safe

无需确认：

* 表情
* 姿态
* UI 动画
* 普通消息

### confirm

需要用户确认：

* 打开网页
* 打开文件
* 外部跳转

### danger

默认禁止：

* 删除文件
* 执行 shell
* 写系统目录
* 注册表操作

---

# 五、消息队列与循环

## 风险

可能出现：

* 消息风暴
* 自触发循环
* scheduler 无限广播
* pending perception 连锁污染

---

## 需要限制

* rate limit
* queue size limit
* cooldown
* retry limit
* DLQ size limit

---

# 六、导出与分享

## 风险

用户导出角色包时：

可能误包含：

* 私人记忆
* 历史记录
* diary
* profile
* growth

---

## 默认允许导出

* 角色设定
* 立绘
* 表情
* lore
* author notes
* preset

---

## 默认禁止导出

* memory
* history
* profiles
* event_log
* diary
* API keys

---

# 七、WebSocket 与 Mobile 安全

## 风险

可能：

* 伪造桌宠客户端
* 局域网劫持
* 恶意轮询 mobile queue
* 注入 desktop chat

---

## 建议

* WS token
* localhost 限制
* Bearer token
* session timeout
* rate limit

---

# 八、资源炸弹

## 风险

社区资源可能：

* 超大图片
* 超长 prompt
* 几万表情
* 嵌套 zip
* 巨型音频

---

## 建议限制

* 单文件大小
* 总包大小
* prompt 长度
* 图片尺寸
* 文件数量
* 解压后大小

---

# 九、未来插件系统（高风险）

## 当前建议

插件系统不要过早开放。

优先级：

```text
Lv1：资源包
Lv2：声明式扩展
Lv3：真正插件系统
```

---

## 真插件开放前必须具备

* 权限系统
* 沙箱
* API version
* 插件 manifest
* 生命周期管理
* 崩溃隔离
* 插件目录隔离

---

# 十、未来架构债关注点

## 当前架构总体健康

优点：

* 单 pipeline
* 通道隔离
* sandbox
* uid lock
* queue 分层
* 文档同步 hook

适合继续扩展。

---

## 未来可能的架构债

### 1. data/ 目录职责膨胀

需要逐渐：

* core data
* user assets
* packages
* runtime cache

分层。

---

### 2. schema version 缺失

未来：

* 角色包
* memory
* themes
* API

都建议加入：

```json
"schema_version": 1
```

---

### 3. 插件边界不够硬

必须保证：

* 插件不能直接碰 memory
* 插件不能绕过 channel
* 插件不能写系统目录

所有能力必须走 API。

---

# 当前结论

当前架构：

* 没有明显“必须推翻重构”的问题
* 非常适合继续做桌宠生态
* 最大风险来自未来社区化与插件化

当前优先级仍然应该是：

1. 完成核心体验
2. 做角色包/主题包标准
3. 做导入导出
4. 再考虑插件系统
