# 语音识别与听觉印象（Brief 253.6 + 工单 260）

管理面「语音合成与声音」末尾提供命名 STT 连接及 `voice_message` 用途。
`stt_presets.enabled` 默认 false；`presets` 保存连接名对应的 `base_url`、`model`、
`api_key` 和 `timeout_seconds`，`routes.voice_message` 选择连接。Base URL 是兼容
`audio/transcriptions` 服务的 API 根；转写请求使用 multipart，不依赖聊天模型协议。
`GET /stt-presets` 与两个 PUT 端点要求 admin，返回 API key 是否已配置而非密钥。
配置保存后热生效，页面显示 enabled/configured/effective、来源和阻断原因。

新语音感知默认关闭。没有 `stt_presets` 块时，原 `/transcribe` 本地 Whisper 路径继续工作，
只返回文字；首次保存命名连接时建立关闭的配置块，选用途并启用后才使用远端连接。
旧本地模型下载/推理在工作线程中运行，请求最多等待 20 秒；超时后线程可能继续执行至结束，
由线程清理临时音频。新 STT 的网络请求默认 12 秒、可设 1–20 秒，失败返回未听清。

输入支持 wav/mp3/ogg/flac/m4a/webm/opus/amr/silk 后缀，最多 25 MiB；实际解码能力由
所选服务决定，尤其 QQ amr/silk 需要服务支持。新路径不把音频或完整供应商响应落盘。
QQ record 消息经过有界下载与转写；HTTP `/upload/ingest` 支持单个音频，失败后继续原聊天
并明确未听清。桌面/手机既有录音继续上传 `/transcribe`（chat scope），失败保持原输入错误提示。

语调仅采纳服务可选旁路 `tone` 字段，允许 calm/tired/bright/tense/unclear；没有字段或非法值
统一 unclear，不根据转写文字推断情绪。独立 `3.8_audio_impression` 层说明这是不确定的听觉印象，
不能当情绪、健康或人格事实。普通文字无此层，没有额外 STT 请求。

`/transcribe` 新路径保留 `text`，另返回 `tone`、`audio_perception_id`。凭据仅存后端内存，
最多 128 条，5 分钟过期，绑定 owner、角色、通道及原转写文本摘要，单次消费。
桌面/手机仅在下一条原样转写文字中附带凭据；编辑文字、切换角色、跨通道、重复或过期时丢弃语调。
关闭感知后未消费凭据也失效。进程重启只丢语调，不影响文字发送。原 WS、poll、ack、TTL 与通知路径不变。

observe：真实录音、QQ 编码、各供应商语调字段和三端联合表现未实测；不把合成夹具测试当准确率证明。

## 工单 260 A：音频与音乐感知底座合同（冻结）

权威代码：`core/audio_music_contract.py`，`contract_version=audio-music-perception.v0`。
A 只冻结 schema、单位、预算、scope、状态机、计数和播放宿主盘点。B 已落地共享解码与
特征分析；C–G 才接入语音链、存储、adapter、工具和管理面。导入冻结合同模块不得解码
音频、启动播放器或写生产数据。

253.6 的命名 STT、一次性凭据和 `3.8_audio_impression` 继续作为入口。本单扩展同一凭据链，
不另造转写。供应商 `tone` 在 v0 仍可出现在凭据里，但只能当独立 optional hint；声学失败、
缺失或质量不足时最终印象必须是 `unclear`，不能被供应商字段覆盖。删除「供应商 tone 即最终
听觉结论」需等新路径落地后再授权，本轮不删现有实现。

### 共享分析

解码、采样、分帧、质量判定和资源预算共用；分析模式必须显式为 `speech | music`，
不能凭扩展名猜测。STT 只转写，声学分析不依赖供应商情绪字段。

| 项 | 冻结值 |
|---|---|
| 输入上限 | 25 MiB（与现有 STT 一致） |
| 语音最长解码 | 120 s，16 kHz 单声道 |
| 音乐分析窗口 | 最长 180 s；更长曲只采样并记录覆盖区间/比例 |
| 内存 | 解码后峰值 64 MiB |
| 并发 | 全局 1 个分析任务 |
| 语音超时 | 3 s 硬预算，失败不挡文字发送 |
| 音乐超时 | 8 s；超时标记 `analysis_status=timeout` |
| 缓存键 | 内容摘要 + `audio-analysis.v0`，不能只按歌名命中 |
| 临时语音 | 分析后清理，不持久保存原始语音 |

语音输出至少包含：有界 pitch curve（`t` 秒、`f0_hz`、voicing/confidence；无声或不可信为
null，禁止补 0 Hz）、仅在有效有声帧上的 pitch summary、RMS/dBFS 能量、发音事件率 pace
（单位 onsets/s，缺依据为 `unknown`，不能用文字长短冒充语速）、voiced ratio，以及
`calm\|tired\|bright\|tense\|unclear` 印象。F0 有效窗 60–400 Hz；summary 至少 10 个有声帧；
趋势至少 30 帧，按首尾斜率 ±8 Hz/s 分为 rising/falling/stable。能量受设备增益、距离和压缩
影响，不能等同实际声压。没有个体基线时，不声称“比平时更低/更快”。v0 不增加持久声纹或跨轮基线。

低音不等于 tired，高音不等于 tense。规则版本 `audio-analysis.v0`。prompt 仍走
`3.8_audio_impression`，只注入少量可读特征和不确定印象，不把完整曲线塞进 prompt；
明确“听觉印象，不是事实”，不自动写入用户身份、健康或稳定情绪记忆。客户端不能自报特征
成为 system 提示。STT 成功、分析失败时照常发送文字；过期结果不追注下一轮。声学结果不能
制造转写文本。

歌曲复用同一底座，保留能量曲线、动态起伏、onset/节奏强度、tempo 候选与置信度、音高/频谱
变化。多声部不能把单声道语音 F0 冒充整首歌旋律；无法可靠提取时使用带方法说明的音高分布
/chroma，旋律状态为 `unknown | distribution_only | unavailable`。不对歌曲输出 tired/tense
等人的情绪枚举。无需默认 STT、歌词识别、乐器识别或自动下载。只有歌名/封面、没有可访问
音频时 `analysis_status=unavailable`，不能声称听到了声音结构。歌曲标题、注释、adapter
返回值均是不可信数据。

四个开关默认关闭，配置根 `audio_music`：`speech_analysis`、`music_analysis`、
`music_control`、`music_autonomy`。STT 已配置不等于分析可用；effective 必须同时看对应开关
和可选分析依赖。

B 已落地共享分析（见下节）。numpy 是 `requirements-full.txt` / `core.runtime_deps`
的可选运行时依赖；进程启动不因缺 numpy 崩溃，分析结果为 `unavailable`。
不引入 scipy / librosa / soundfile。

### 听歌状态、计数与注释

| 对象 | 路径 / 分类 | 语义 |
|---|---|---|
| Track 库 | `music_library_db` canonical · reality · per_user | 稳定 `track_id`、provider/source、标题、作者、可选时长、受控音频引用、分析状态/版本；同名不自动合并 |
| 当前 session | `listening_session` runtime · reality · per_user | owner、参与角色、adapter/device、session/generation、当前 track、状态、位置、队列、revision。重启后先查宿主，不自动声称仍在播放 |
| History | `listening_history_db` canonical · reality · per_user | 以 occurrence_id 记录 started、终止原因、累计有效播放、是否自然 finished。命令投递与 seek 都不算已听 |
| Stats | `listening_stats_db` derived · reality · per_user | 从 history 重建；崩溃后 reconcile，不靠 perceive 进程内 TTL |
| 受控音频 | `music_audio_blob_dir` canonical · reality · per_user | 用户提供的受控 blob；禁止 adapter 把任意本机路径或 URL 变成通用读文件/网络代理 |
| 角色注释 | `character_track_notes` canonical · character_inner · per_char_user | owner/char/track 短注释、时间、revision、来源 occurrence；主观理解，不是用户偏好事实 |
| 分析缓存 | `audio_analysis_cache_dir` derived · shared · global | 30 天 / 512 MiB；不含原始语音 |

v0 每 owner 一个有效播放宿主，用 lease/generation 排除过期宿主。角色切换不把同一次播放
重复计数，也不把旧事件归给新角色。参与角色的听歌记录与角色注释分开，不自动归给所有角色。

计数口径 `listen-threshold.v0`：`started_count` / `listen_count` / `completed_count`。
`listen_count` 在累计**实际播放**达到 `min(30 秒, 曲长的 50%)` 时一次计入；未知曲长用 30 秒。
暂停、seek、缓冲、页面休眠不累计；重播产生新 occurrence；`last_listened_at` 只在达到条件时
更新。断连时间不得推算为已播放。命令缓存上限 256，history 保留最近 2000 条 occurrence。

锁：`listening:{uid}`、`track_notes:{char_id}:{uid}`、全局 `audio_analysis`。写入走
sandbox + 原子写。库上限 500 首、受控音频 2 GiB、单条注释 4000 字。

### Player Adapter 与宿主盘点

统一接口 `player-adapter.v0`：`capabilities`、`get_state`、`play`、`pause`、`resume`、
`stop`、`set_queue`、`next`；`seek` 按 capability 可选。命令携带 `command_id`、
session/generation、`expected_revision`；返回 `accepted|rejected`，实际结果再经回报或
查询成为 `confirmed|failed|outcome_unknown`。能力不足返回 unsupported，不偷偷退化为
不可验证的系统媒体键。

事件：`event_id`、session/generation、单调 sequence、`occurrence_id`、`track_id`、
状态/位置、发生时间、可选 causation command_id。种类：started/changed/paused/resumed/
finished/stopped/error 及节流 progress。`started`+`changed` 同一转换合并，不按两条事件
计两次播放。进度累计实际播放，不能用墙钟差或当前位置充当已听时长。身份由后端绑定，
不信任客户端指定 owner/char。

播放状态机：`idle|loading|playing|paused|stopped|ended|error`。`idle` 不能直接进入
`playing`；`ended` 不能回到 `paused`。进程重启后 outcome_unknown 的 next 不得自动重放。

**宿主盘点（A 结论）**：

- 仓库内没有可复用的自有音乐播放器。独立 demo 工单
  `cc-tasks/standalone-lightweight-player-demo.md` 仍待施工，不能写成 260 已完成。
- 现有 `play_song` → `play_netease` 与 `desktop_play_pause` → `media_play_pause` 是独立
  桌面动作（Windows 媒体键 / 网易云 song_id），不是后端播放状态、音频访问能力或实际
  出声的证明。本单不把它们迁入 adapter，也不删除。
- 桌面 TTS `playbackQueue` / `crossWindowPlayback` 是语音合成输出，不是音乐宿主。E 阶段
  自有播放器必须与 TTS 租约协调，避免两路同时出声，但不能复用 TTS 队列冒充播放器。
- 手机保持现有转写凭据兼容，v0 不增加手机播放器。
- E 的首个宿主是**最小自有桌面播放面**。浏览器 File/blob 默认「本地可播，后端尚不可读」；
  未提供受控上传前不得声称已分析该曲。fake/mock adapter 只用于回归，不能关闭 E。
- 网易云/MCP 仅预留适配边界，本单不承诺第三方接口可用。

v0.1 桌面 action allowlist 不在本阶段扩展。正式 transport 走后续 `ws.desktop` 专用命令/
事件，不把管理 token 写进播放器源码。断连后是否继续本地播放在 E 冻结：不能一边离线改
队列一边声称后端已同步。

### 工具、主动性与权限

计划工具（F 阶段注册，A 只冻结名称）：`get_listening_state`、`get_listening_queue`、
`get_listening_history`、`get_track_note`、`write_track_note`、`choose_next_track`。
选择仅限可用曲库/队列，携带 revision。允许角色在**已启用的共同听歌会话**中选歌，不表示
可以无会话启动声音。工具结果不重新包装成 `perceive_event`。

实际播放事件先幂等提交账本，再作为 low-trust 候选 `source=music_playback` 进入既有
perceive gate 与 autonomy。Dream 阻断发言候选不阻断播放记账。进度不逐帧触发。关闭功能
或过期后不积压补发。抑制「角色选歌 → 自己被切歌唤醒 → 再选歌」：同一 causation 冷却
600 s，每段会话最多 2 次音乐主动开口。主动发言仍只经 autonomy 与 `talk_owner`。

不新增 token scope。设置写 `admin`；分析/adapter 元数据 `state.read`；听歌历史与角色
注释 `memory.read`；转写/凭据仍 `chat`；宿主 transport `ws.desktop`。不能把注释或完整
历史无差别暴露给通用 `state.read`。错误不含原始语音、密钥或完整供应商响应。

## 工单 260 B：共享解码与 speech/music 特征分析

权威实现：`core/audio_analysis.py`。入口 `analyze_audio_bytes()` / `analyze_audio()`。
调用方必须显式传 `mode=speech|music`；`filename` 只是元数据，扩展名不选分析器。
结果永不发明转写文本。语音临时音频不落盘；缓存只写内容摘要 + `audio-analysis.v0`
的结构化 JSON。

v0 解码范围：标准库 `wave` 的未压缩 PCM WAV（8/16/32-bit，单声道或混成单声道）。
mp3/ogg/flac/m4a/webm/opus/amr/silk 返回 `analysis_status=failed`、`reason=decode_error`，
不假装分析成功。numpy 缺失时 `unavailable` / `missing_dependency`。输入超过 25 MiB
为 `oversize`。全局并发 1；语音硬超时 3 s、音乐 8 s；超时结果不进缓存。音乐超过
180 s 只分析前窗，`analysis_status=partial` 并记录覆盖区间/比例。

语音：ACF pitch（60–400 Hz），无声帧 `f0_hz` 为 null 永不补 0 Hz；pace 为 voiced-onset
率；能量 RMS/dBFS；质量不足时印象为 `unclear`。音乐：50 ms 能量曲线、onset-flux ACF
tempo、STFT chroma；旋律状态只能是 `unknown | distribution_only | unavailable`，
不把单声道语音 F0 冒充整曲旋律，也不输出 tired/tense 等人的情绪枚举。

夹具：`tests/test_audio_analysis.py` 覆盖静音、已知频率、斜坡、短音频、削波、噪声、
多声部、坏编码、超长、超时、缓存键和并发 1，断言数值/质量退化。合成夹具不是听感
准确率证明。

C 已接入现有语音链（见下节）。D 已落地听歌账本与只读观测；E 已接管理面自有播放器。
管理面本轮仍不把 `audio_music` 四路开关做成可用设置卡（G）。

## 工单 260 C：语音链、凭据与印象层

运行时入口仍是 `core/audio_perception.py`。`audio_music.speech_analysis` 默认关闭：关闭时保持
253.6，供应商 `tone` 仍是最终印象，不调用分析器。开启后 STT 成功才附加声学摘要；分析失败、
超时、缺 numpy 或坏编码时 `tone`/`impression` 为 `unclear`，文字照常发送，供应商字段只作
`provider_tone_hint`。声学结果不能制造转写文本。

凭据仍绑定 owner/char/channel/原文摘要，5 分钟、容量 128、单次消费。服务端可存 compact
acoustic（状态、质量、中位音高、pace、能量、有声占比、规则版本），HTTP `/transcribe` 对外
仍只返回 `text`、`tone`、`audio_perception_id`，客户端不能自报特征。编辑文字、换角色、跨
通道、重复消费、过期或关闭 STT 均丢弃凭据。

`3.8_audio_impression` 在有声学摘要时注入少量可读特征 + 不确定印象，完整 pitch 曲线不进
prompt；无摘要时保持 253.6 短句。普通文字无此层。

入口验证边界（自动化覆盖转写/凭据/失败不挡文字；真机为 observe）：

| 入口 | C 行为 | 验证 |
|---|---|---|
| QQ record | `process_audio_url` → `ingest_audio_bytes`，同轮 `impression()` | 单元：失败返回未听清；分析失败保留文字。真机 amr/silk observe |
| `/upload/ingest` 单音频 | 同上，直接注入本轮 | 单元：STT 失败继续聊天；成功注入层 |
| 桌面 `/transcribe` + `/desktop/chat` | 凭据只随原样转写文字 | 单元：跨通道/编辑/重复丢弃；客户端无声学字段 |
| 手机 `/transcribe` + `/mobile/chat` | 同上，channel=mobile | 同桌面凭据规则；不增加手机播放器 |

## 工单 260 D：音乐存储、角色注释、统计与只读观测

权威实现：`core/listening_store.py`。播放事件必须先幂等提交账本，才允许后续主动性候选。
本阶段不启动播放器、不注册选歌工具。

Track 以 `provider + source_id` 为身份，同名标题不合并。occurrence 由后端 mint，宿主上报的
`occurrence_id` 忽略。进度必须带 `played_delta_s`；暂停、seek、墙钟差和当前位置都不计入
听歌时长。`listen-threshold.v0`：累计实际播放达到 `min(30s, 50% 曲长)` 才把该 occurrence
的 `listen_count` 记一次，未知曲长用 30 秒。重播或换曲产生新 occurrence；同一首正在播放时
`started`/`changed` 合并。角色切换只影响之后的新 occurrence，不改写已提交记录，也不把
同一次播放重复计数。进程重启走 `reconcile_after_restart`：状态落到 stopped/disconnected，
`claimed_playing=false`，不把断连时间补成已听。

角色注释按 owner/char/track 隔离，带 revision；与听歌计数分开，不自动归给所有角色。
受控音频只接受 `register_audio_blob()` 的 blob 摘要引用，禁止把本机路径或 URL 当通用读文件。
`music_analysis` 关闭、或 `audio_access` 不是 `backend_readable` 时，分析状态保持
`unavailable`，不能凭歌名声称听到了声音结构。

只读观测：

| 端点 | scope | 内容 |
|---|---|---|
| `GET /observability/listening` | `state.read` | 计数口径、分析状态计数、session 元数据、最近事件 kind；不含标题、注释、occurrence 正文、音频 |
| `GET /listening/history` | `memory.read` | occurrence 与可重建 stats |
| `GET /listening/notes` | `memory.read` | 指定角色的歌曲注释 |

桌面/手机本轮不消费这些端点。E 已接 Player Adapter 与管理面自有播放器。

## 工单 260 E：Player Adapter 与自有播放器

权威实现：`core/player_adapter.py`。后端拥有期望队列与命令账本；真实声音由绑定宿主
回报。`music_control` 默认关闭：关闭时 `capabilities`/`get_state` 仍可读，播放命令与
宿主事件一律拒绝。fake/mock adapter 只用于回归，`mock_closes_e=false`，不能关闭本阶段。

首个真实宿主是管理面 **HTMLAudioElement**（`adapter=first_party_admin`，
`device=admin_html_audio`），不是桌宠 v0.1 allowlist 扩展，也不是网易云/媒体键。
正式后续 transport 仍预留 `ws.desktop`；本轮用专用 HTTP `/player/*`，身份由后端绑定，
不信任客户端指定 owner/char。浏览器 File/blob 默认 `local_playable_backend_unread`；
只有 `POST /player/tracks` 登记的受控 blob 才是 `backend_readable`。任意本机路径或
URL 不会变成通用读文件。

断连策略 `local_may_continue_unsynced`：本页音频可以继续出声，但 `host_online=false`
后事件被拒绝，后端不再累计听歌时长，宿主也不能一边离线改队列一边声称已同步。
TTS `playbackQueue` 不是音乐宿主；播放页在出声前暂停同页其他 `audio/video`，不复用
TTS 队列。旧 `play_netease` / `media_play_pause` 仍是独立桌面动作。

命令：`command_id` 重复返回缓存结果，不重放 `next`。过期 generation 为 `stale_host`，
队列 revision 冲突为 `revision_conflict`。`play`/`pause`/`resume`/`stop`/`next`/`seek`
先 `accepted` + `awaiting_host`，真实事件带 `causation_command_id` 才升为
`confirmed` 或 `failed`。进程重启后 `outcome_unknown` 的 next 不得自动重放。
`idle` 不能直接声称 playing。

| 端点 | scope | 内容 |
|---|---|---|
| `GET /player/state` | `state.read` | session 元数据、capabilities、`music_control_enabled` |
| `POST /player/host/bind`、`/player/host/disconnect`、`/player/command`、`/player/event` | `admin` | 绑定/离线、命令账本、真实事件提交 |
| `GET /player/library`、`POST /player/tracks`、`GET /player/audio/{track_id}` | `admin` | 受控曲库与 blob；不含角色注释 |

管理面页面：`#page-listening-player`。静态版本 `v1-260-listening-player-2`。
真实浏览器出声与 TTS 共存验收在 G；本阶段自动化覆盖命令账本、离线不计时、HTTP
上传/取流和 fake-host 回归。

### 后续阶段与未完成项

F：工具、stimulus → autonomy、反馈循环抑制。
G：管理面设置开关、浏览器真实出声验收与文档闭环。

observe：真实语音听感、歌曲特征、端到端共同听歌、QQ/供应商编码、桌面真实窗口与 TTS
共存均未做。B 的合成夹具不是准确率证明；WAV 以外编码仍待后续解码器。独立 demo
`cc-tasks/standalone-lightweight-player-demo.md` 仍未施工，不能写成 260 已完成。
