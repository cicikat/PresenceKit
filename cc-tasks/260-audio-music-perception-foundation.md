# 260 — Audio + Music Perception Foundation v0

日期：2026-09-20。状态：施工中。A 已冻结；B 已落地共享分析；C 已接入语音链；D–G 未施工。
运行时合同以 `core/audio_music_contract.py` 与 [audio-perception.md](../docs/audio-perception.md) 为准。

目标：同一套音频分析底座支持语音听觉印象和音乐声音特征；后端维护共同听歌状态与角色注释，经统一 Player Adapter 控制自有播放器，并让真实播放事件成为既有主动性的候选来源。不是只做播放器，也不是把 STT 的情绪字段当成听觉分析。

## 当前基线与影响面

- 已核对 `core/audio_perception.py`、[audio-perception](../docs/audio-perception.md)：253.6 已有命名 STT、可选 tone、`3.8_audio_impression` 和绑定 owner/char/channel/text 的五分钟一次性凭据；尚无原始声学分析。本单扩展已有入口，不另造转写或凭据链。
- `core/tool_dispatcher.py` 已有 `desktop_play_pause` 和 `_play_song_wrapper`：前者投递媒体键，后者搜索网易云并投递 `play_netease`。这些动作不是后端播放状态、音频访问能力或实际播放成功的证明。
- [interaction-event-model](../docs/interaction-event-model.md)、[scheduler](../docs/scheduler.md)、[autonomy](../docs/autonomy.md)：perceive gate 负责去重和 Dream Guard；播放事件只生成候选 signal，主动发言仍经 autonomy 与 `talk_owner`。
- 后端涉及分析、状态、工具、管理面与主动性；桌面涉及首个真实播放宿主和事件回报。手机先保持现有转写凭据兼容，不默认增加手机播放器。施工前读取实际受影响仓库的 AGENTS 与协议章节。
- 首个宿主拟定为自有桌面播放器；施工 A 阶段确认现有可复用入口。没有可复用播放器时需实现最小真实播放面，不能用 mock adapter 冒充完成。

## v0 合同

### 1. 共享音频分析与语音

解码、采样、分帧、质量判定和资源预算共用；通过显式 `speech | music` 模式选择分析，不凭扩展名猜模式。STT 继续只承担转写，声学分析不依赖供应商提供情绪字段。

语音输出至少包含：

| 特征 | 合同 |
|---|---|
| pitch curve | 有界时间序列，包含时间、F0 Hz、voicing/confidence；无声或不可信帧为 null，不能补成 0 Hz |
| pitch summary | 仅在有效有声帧上求 median、range、variation、trend；冻结分位数范围、变化量与趋势的定义、单位、最小有效帧数与算法版本 |
| energy | RMS/dBFS 及时间变化；说明设备增益、距离、压缩会影响数值，不能等同实际声压 |
| pace | 明确采用发音事件率或 STT 时间戳下的单位率，注明估计方法和单位；缺依据为 unknown，不能拿文字长短冒充实测语速 |
| voiced ratio | 有效有声帧占比，给出分母、有效音频时长和质量标记 |
| impression | `calm | tired | bright | tense | unclear`，附依据、质量与规则版本；质量不足、证据冲突或输入不适合时为 unclear |

情绪映射是保守启发式，不是情绪识别准确率承诺。低音不等于 tired，高音不等于 tense；没有个体基线时，不声称“比平时更低/更快”。v0 不增加持久个人声纹或跨轮基线。供应商 tone 仅为独立标注来源的 optional hint，不能覆盖声学失败或缺失。

复用 `3.8_audio_impression`，优先注入少量可读特征和不确定印象；不把完整曲线塞进 prompt，明确“听觉印象，不是事实”，不自动写入用户身份、健康或稳定情绪记忆。凭据扩展为服务端可信结果引用，仍维持 scope、原文摘要、TTL、容量和一次消费；客户端不能自报特征成为 system 提示。

STT 与分析隔离：STT 成功、分析失败时照常发送文字；分析任务设硬预算和有界并发，不增加无上限 send 前等待。过期结果不追注下一轮、不重发聊天。STT 失败沿现有入口降级，声学结果不能制造转写文本。解码过程限制输入字节、解码后时长/帧数/内存，超时可回收；语音临时音频清理，不持久保存原始语音。

### 2. 音乐特征

歌曲复用共享底座，保留能量曲线、动态起伏、onset/节奏强度、tempo 候选与置信度、音高/频谱变化。多声部音乐不能把单声道语音 F0 结果冒充整首歌的旋律；无法可靠提取主旋律时使用带方法说明的音高分布/chroma 等摘要，旋律未知明确标记。

不对歌曲输出 tired/tense 等人的情绪枚举。无需默认做 STT、歌词识别、乐器识别或自动下载歌曲。只能分析实际可访问的音频；只有歌名/歌手/封面时可维护状态，但 `analysis_status=unavailable`，不能声称听到了声音结构。

长曲采用有界采样与后台分析，记录分析覆盖区间和覆盖比例，不能把片段称为整曲分析。按内容摘要与分析版本缓存，不能仅按歌名命中。音乐摘要与语音印象分层；歌曲标题、注释、adapter 返回值均作为不可信数据，不能变成指令。

### 3. 后端状态、共同听歌与角色注释

- Track：稳定 `track_id`、provider/source ID、标题、作者、可选时长、受控音频引用、分析状态/版本；同名歌曲不自动合并。
- Playback session：owner、参与角色、adapter/device、session ID、当前 track、状态、位置、队列、revision。v0 每 owner 一个有效播放宿主，采用 lease/generation 排除过期宿主；角色切换不把同一次播放重复计数，也不把旧事件归给新角色。
- History：以 playback occurrence ID 记录实际 started、终止原因、累计有效播放时长、是否自然 finished。命令投递与 seek 跳转都不算已听。
- 统计：区分 started_count、listen_count、completed_count；v0 listen_count 拟按累计实际播放达到 `min(30 秒, 曲长的 50%)` 一次计入，未知曲长用 30 秒。暂停、seek 不累计，重播产生新 occurrence；last_listened_at 在达到条件时更新。阈值版本化并在管理面说明，不把断连时间推算为已播放。
- 角色注释：按 owner/char/track 隔离，保存角色自有短注释、时间、revision 和来源 occurrence；属于主观理解，不是用户偏好事实。参与角色的听歌记录与角色注释分开，不自动归给所有角色。提供有界读写工具，不默认每首歌都调用模型写注释。
- 持久状态经 sandbox/data registry、原子写与锁管理；明确定额、历史保留与统计重建规则。历史提交与计数须幂等，崩溃后 reconcile，不能靠 perceive 的进程内短 TTL 保证播放账本正确。

### 4. Player Adapter

后端拥有期望队列与命令账本，adapter 回报实际播放状态；界面明确区分 requested、confirmed、failed、outcome_unknown。进程重启后先查询宿主，不自动声称仍在播放，也不重复执行结果未知的 next。

统一接口至少提供 capabilities、get_state、play(track)、pause、resume、stop、set_queue、next；seek 可按 capability 支持。命令携带 command_id、session/generation、expected_revision，返回接受/拒绝与原因；实际成功依靠回报或状态查询确认。能力不足返回 unsupported，不偷偷退化为不可验证的系统媒体键。

事件包含 event_id、session/generation、单调 sequence、occurrence_id、track_id、状态/位置、发生时间以及可选 causation command_id；支持 started/changed/paused/resumed/finished/stopped/error 和必要的进度上报。事件按绑定宿主鉴权，处理重复、乱序、重连快照、缺失结束和断连。身份由后端绑定，不信任客户端任意指定 owner/char。

自有播放器先完成真实播放闭环。网易云/MCP 仅预留适配边界，本单不承诺第三方接口可用；未来按 capabilities 接入，缺音频读取时只能提供播放状态。自有歌曲来源先支持用户提供的受控音频，禁止 adapter 将任意本机路径或 URL 变成通用读文件/网络代理。

### 5. 工具与主动性

提供有界的当前状态/队列/历史/注释查询、写角色注释与选择下一首能力；选择仅限可用曲库/队列，携带 revision 防止覆盖用户刚做的切歌。工具注册进入 `_TOOL_REGISTRY`，补 examples/keywords，遵守既有 origin、scope、工具暴露与 autonomy policy。允许角色在已启用的共同听歌会话中选歌，不表示可以无会话启动声音。

实际播放事件先幂等提交状态，再作为 low-trust 候选经过 perceive gate 与现有 signal 路径；Dream 阻断发言候选不阻断播放状态记账。started/changed 同一转换合并，进度不逐帧触发；设置 TTL、冷却、每段会话预算与消费状态，关闭功能或过期后不积压补发。

命令 tool result 不重新包装 stimulus；独立确认的播放生命周期事件保留 causation，并抑制“角色选歌→自己被切歌唤醒→再选歌”的反馈循环。主动发言只能经 autonomy 决策与 `talk_owner`，保持 DND、Dream、会话并发和预算闸门。事件存在不保证角色一定开口。

### 6. 控制面、可观测性与兼容

语音分析、音乐分析、音乐控制和音乐主动性分别有默认关闭的开关；展示 configured/effective/阻断原因，不能把 STT 已配置等同分析可用。管理面只读展示分析成功/失败/超时、方法版本和耗时、adapter 能力与在线状态、真实播放状态、队列 revision、计数口径、最近事件及 signal 抑制原因。错误不含原始语音、密钥或完整供应商响应。

精确 API schema、scope 和存储分类在 A 阶段按现有 security/data-taxonomy 冻结；状态与敏感听歌历史/注释按敏感度授权，不能将后者无差别暴露给通用状态 token。新增管理面静态内容需更新版本并完成浏览器验收。

同步实际变化的 audio-perception、prompt-layers、tools、autonomy/scheduler、feature-control-surface、three-repo-interface-catalog 及桌面协议。手机只有实际字段/行为变化时才修改。纯文字、旧转写客户端、既有 TTS 和 WS/poll/ack 不得因新增功能失效。

## 分阶段施工与验收

每阶段只有相关回归、差异与换行检查通过后才能勾选；每张实施子单完成即独立 Git commit，跨仓分别提交并记录关联。A/B/C 已独立提交。以下 D–G 仍未施工。

- [x] A — 冻结分析 schema/单位/算法、资源预算、来源支持、scope、状态机、计数和事件幂等合同；盘点自有播放宿主、桌面音频/TTS 协调与依赖，明确受影响文件和删除候选。
- [x] B — 实现共享解码及 speech/music 特征分析；隔离夹具覆盖静音、短音频、已知频率/斜坡、削波、噪声、多声部、坏编码、超时与超长输入；断言数值/质量退化，不只断言字段存在。
- [x] C — 接入现有语音链与凭据、保守标签及 prompt；复用 `tests/test_audio_perception.py` 等回归，补 STT 成功而分析失败、跨角色/通道、编辑文本、关闭开关、重复消费和普通文字无层。QQ、上传、桌面、手机入口分别记录验证边界。
- [ ] D — 落地音乐存储、角色注释、统计与只读观测；覆盖并发队列修改、重复/乱序事件、角色切换、循环播放、seek/暂停、缺 finished、重启/崩溃恢复，证明不重复计数、不误归属。
- [ ] E — 实现 Player Adapter 与自有播放器；实测播放/暂停/继续/切歌/自然结束、离线重连、命令结果未知、过期宿主及 TTS 共存；fake adapter 只用于回归，不能替代真实声音验收。
- [ ] F — 接通选歌/注释工具、音乐摘要与 stimulus→autonomy；验证可自主选择下一首、可静默、可因歌曲发言，以及反馈循环抑制、冷却、Dream/DND、撤权与会话结束。
- [ ] G — 管理面设置/观测、静态版本与浏览器实测；同步受影响合同。真实语音听感抽检、歌曲特征和端到端共同听歌分别记录证据；未做的设备/供应商/编码验收明确标为未完成。

## 完成定义与删除候选

完整 v0 必须同时证明：原始语音有可追溯声学特征且失败不挡文字；歌曲可形成独立声音摘要；自有播放器真实事件驱动后端状态与可靠统计；角色能读写自己的歌曲注释、选择下一首，并可通过既有主动性链开口。只有 STT tone、mock 播放或静态界面不能结单。

删除候选：253.6 中“供应商 tone 即最终听觉结论”的实现与文档，在新路径落地时替换；旧 `play_netease` 与媒体键入口在使用盘点后评估是否迁入 adapter 或继续作为独立桌面动作，不能直接声称等价或擅自删除。未授权清理不执行；正式删除时同步移除失效守卫、测试和文档，不留下只保护退役路径的测试。
