# 角色心声文风（2026-09-11）

管理面入口现为「prompt检视」。原生心声和独白注入后，刷新同一异步上下文中对应
messages 对象的快照，补齐实际注入层与字符统计；不新建一轮，也不串到后台调用。
这里展示组装后的分层 prompt，不代表 provider 转换后的原始 HTTP 报文。

状态：current（提示拼接与控制面）；observe（实际模型思考摘要的遵从程度）。

Brief 249：monologue 的 scoped mood 提示改用“你此刻”，不借全局活跃角色名称标注其他角色的状态。无配置字段或 effective state 变更；心声文体内第一人称约定保留。

## 当前实现

`core/thinking_voice.py` 把基础声音、称呼、语体、当前情绪变体、强度和过渡提示拼接起来。
心声用第一人称“我”，主要用“你”或沿用已有亲昵称呼；明确要求不用“用户”、自己的名字或
第三人称自称，不写需求分析、回复策略和任务清单。语气真挚、朴素，不强行煽情或照抄例句。
这些是生成约束，不是事后文本替换；不能保证上游摘要器逐字遵守。

九种已有 mood 各三个小幅变体：neutral/gentle/happy/sad/angry/surprised/thinking/sleepy/yandere。
日记自述、私信口吻、朴素随笔三个相近语体通过角色 ID 与错峰日周期稳定伪随机选择。
24 小时内语体和变体序号不随轮数、mood.updated_at 或强度更新重抽，重启也不重抽；
到周期边界才允许换一次，换后也可抽到原风格。情绪变化仅更换对应情绪的表达提示。
沿用 mood_state 已有平滑 current/intensity/pending；不新增情绪写入、随机状态或台账。

`thinking.character_voice` 默认 true，但只有 thinking.enabled=true 时生效。
native/auto-native 在 `maybe_apply` 增加 `11.6_thinking_voice` system 层，无额外模型调用；
工具循环首步前注入一次，后续复用，重试不叠加。同既有 `11.7_inner_monologue` 一样位于
prompt_builder 裁剪之后，不属于消融开关；关闭方法是 character_voice=false。
该层只提出可见摘要的文风约定，不要求公开内部推理，不要求在正文中补写心声或 think 标签。
原始 reasoning 仍原样独立归档和按 turn_id 读取，没有被改写成虚构记录。

monologue 路线复用同一拼接器，并把当轮已构建的 `2_char_desc` 人设（最多 6000 字）、
最近两轮对话和本次文字（最多 800 字）传给既有独白调用。独白仍只注入当轮，影响主回复，
仍不进 history/event_log。native 与 monologue 都遵守 apply_to_proactive 和非 chat 路由闸门。
原有 monologue 已有一次前置网络往返，本次不新增后处理或前置调用次数。

## 控制面与跨端

管理面「对话与思考」增加“角色心声文风（通用提示引导）”，支持关闭，预览当前拼接提示。
`GET/POST /settings/thinking` 仍使用 persona scope，增加 character_voice；GET 的 voice_preview
返回 register/emotion/variant/rotation_hours/prompt、enabled/effective/blocking_reason，以及
control=prompt_guidance/output_guaranteed=false。effective 表示提示发送条件满足，不表示
上游已返回思考或已通过文风验收。预览是当前状态，不是历史回合的实际请求快照。

桌面现有“展开思考”和本地显示开关继续消费原协议，不增加 IPC/WS 字段或权限。
手机目前无 reasoning 展开 UI（roadmap）；主聊天同样使用后端生成链。未改 poll/ack/TTL、
通知、中继、发送锁或消息记忆写入。IME inbox 不参加此提示，也不会因此自动触发角色发言。

## 官方接口核对

以下是本次查阅范围内的结论，不代表所有服务商与中转都相同：

| 服务商 | 官方公开控制 | 独立的思考区 system prompt |
|---|---|---|
| [Gemini](https://ai.google.dev/gemini-api/docs/thinking) | 思考强度/预算、可见摘要 | 未找到该参数；普通提示的摘要口吻遵从待实测 |
| [Claude](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) | thinking 模式/预算、摘要显示；普通提示可引导思考行为 | 未找到独立字段；摘要可能由另一模型生成 |
| [OpenAI](https://developers.openai.com/api/docs/guides/reasoning) | effort、summary；返回摘要而非原始 reasoning tokens | 未找到独立字段 |
| [DeepSeek](https://api-docs.deepseek.com/api/create-chat-completion/) | thinking/effort，reasoning_content；Beta prefix completion 接受思考前缀 | 有接近的 Beta 思考前缀输入，但不是独立 system prompt，也不保证不影响答案 |

本次没有接入厂商专用前缀补全。通用 prompt 属于主请求的一部分，可能改变最终回复措辞，
不是“只改展示、不影响生成”的后处理。未修改实际模型路由或 thinking.mode。

## 验证与边界

- 77 项相关测试通过：开关/非 chat/主动消息闸门、无额外 native LLM、工具复用幂等、
  输入消息不变、角色 scope、风格稳定、情绪变化、人设传递、独白过滤、归档与称呼守门。
- Chromium 实际打开本地管理面静态资源并清缓存硬刷新；合成 API 验证可见开关、
  提示预览、保存和 effective 变化。不把合成接口验证当真实登录验收。
- 当前配置的主模型用两条虚构上下文进行了真实调用，均返回正文但没有 reasoning 字段。
  因而没有可用于验收的原生摘要，不能声称原生思考已不再出现疏离称呼。
- observe：用户实际长上下文、真实中转的 reasoning 暴露与风格遵从，以及桌面原生窗口体验。
  历史归档不会重写；后端需加载新代码后，新回合才使用该提示。
