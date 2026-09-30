# 视频通话实时性与感知精度工单组

本组共 7 张工单，覆盖三块需求：STT 延迟与准确度、TTS 与图像识别的资源争用（含重复播报）、
视频通话的即时性与主动性。每张独立验收、独立提交。

先读「零、风险与不可直接实现项」再开工。风险 2 与风险 4 的取舍已确认（见各单开头），
风险 1 与风险 3 是开工时必须遵守的约束。

依赖与并行：

| 工单 | 主题 | 依赖 | 可并行 |
|---|---|---|---|
| A | 本地资源争用解耦（TTS / 视觉） | 无 | 与 C、D1 并行 |
| B | 观察帧时间戳 | 无（与 A 同文件，建议 A 之后） | 与 C、D1 并行 |
| C | TTS 重复播报定位与修复 | 无 | 与 A、B 并行 |
| D1 | STT 延迟与准确度可配置化 | 无 | 与 A、B、C 并行 |
| D2 | 本地模型运行配置页（管理面） | **D1 必须先落地** | 不可先做 |
| E | 细粒度声线印象（合同升版） | D1（复用 STT 链路验收） | 不建议与 D1 并行改同一模块 |
| F | 通话内主动性放宽 | **A 必须先落地** | 不可先做 |

---

## 零、风险与不可直接实现项

### 风险 1：本地 GPU 是真实瓶颈，锁不是随便加的（影响 A、F）

当前视觉路由 `video_call` 指向本机 Ollama 的 `qwen2.5-vl` 量化模型（loopback，
`config.yaml` `image_presets`），TTS 是本机 GPT-SoVITS（loopback 9872）。实测本机
GPU 8188 MiB 已占用约 7486 MiB。`core/video_call.py:20` 那把
`_local_resource` 锁把两者串起来，注释写的是"共享"，实际作用是**防止两个本地大模型同时吃显存**。

所以工单 A 不能简单"拆锁让两边并行"——真并行大概率换来 OOM 或两边一起变慢。A 的方案是
**保留互斥、改掉"饿死"语义**：让视觉请求在 TTS 占用时短暂排队而不是立刻放弃，并且无论
是否拿到锁都要走完主动信号发射。若后续把视觉换成远程 API，锁可按 host 判定自动解除，
这一步不在本组范围。

### 风险 2：细粒度声线（哭腔 / 夹嗓子 / 气声 / 忽高忽低 / 语气低沉）不能按字面全部实现（影响 E）

三层阻碍，按严重程度排列：

1. **冻结合同挡着。** `core/audio_music_contract.py:20` 的
   `IMPRESSIONS = frozenset({"calm","tired","bright","tense","unclear"})` 是
   `audio-music-perception.v0` 冻结集，`tests/test_audio_music_contract.py` 断言精确标签集。
   扩标签必须升 `CONTRACT_VERSION` / `ANALYSIS_VERSION`，属于契约变更，需要你明确授权。
2. **需要的声学特征现在没有。** `core/audio_analysis.py` 只有自相关 F0、
   pitch_summary（中位 / 极差 / IQR variation / 趋势）、RMS dBFS、voiced onset rate、
   voiced_ratio。判断气声要谐噪比（HNR）或频谱倾斜，夹嗓子要共振峰或 CPP，
   哭腔要 jitter / shimmer 加断续包络——一个都没有。而 `docs/audio-perception.md` 明确
   "不引入 scipy / librosa / soundfile"，这些都得用 numpy 手写。
3. **没有个体基线。** 文档写的是"没有个体基线时，不声称'比平时更低 / 更快'"。
   "语气低沉""哭腔"本质是**相对该用户平时**的偏移，绝对阈值必然误判。v0 明确不做持久声纹。

按可行性分档，E 只承诺前两档（**已确认采纳，哭腔 / 夹嗓子不做**）：

| 需求标签 | 可行性 | 依据 |
|---|---|---|
| 忽高忽低 | **可做** | 已有 `variation`（IQR/median）和 `trend`，只需新阈值，零新特征 |
| 带着气声 | **可做（中置信）** | 需新增 numpy 谐噪比或高频能量占比，计算量可控 |
| 语气低沉 | **只能做弱版** | 无基线，只能表述为"音高偏低且语速偏慢"，不能断言"比平时低" |
| 夹嗓子 | **不做** | 需共振峰 / CPP 估计，numpy 手写在 3 s 预算内不可靠，误判率不可接受 |
| 哭腔 | **不做** | 需 jitter/shimmer + 呼吸断续联合判据 + 个体基线，v0 三者都缺 |

另有硬预算：`SPEECH_ANALYSIS_TIMEOUT_S = 3.0`、`MAX_CONCURRENT_ANALYSIS = 1`。新特征必须
塞进这 3 秒，超时按现有 fail-open 返回 `unclear`。

哭腔 / 夹嗓子不是调阈值能解决的，需要引入个体声纹基线（跨轮持久化用户嗓音统计），
属于新的隐私面和新的记忆写入点，要独立 brief 和独立授权。**本组不做，已确认。**

### 风险 3：主动性放宽会直接变成骚扰（影响 F）

通话内"角色不说话"不止一个原因。实测本机 autonomy 状态（`data/runtime/autonomy/.../state.json`
最近 100 次 run）：`expired` 39、`blocked_user_active` 31、`duplicate` 27，真正
`talk_sent` 只有 3 次。叠着的闸有六道：

| 闸 | 现值 | 位置 |
|---|---|---|
| 相机信号最小间隔 | 60 s | `core/video_call.py:25` |
| autonomy 评估最小间隔 | 900 s | autonomy config `min_interval_seconds` |
| 全局主动发言最小间隔 | 2700 s + 0~20% 抖动 | `config.yaml:562` |
| 每日主动上限 | 16 | `config.yaml:563` |
| 连续未回应上限 | 2 次即硬停 | `core/autonomy/talk_gate.py:17` |
| camera_silent 前置 | 需用户静默 ≥120 s | `core/autonomy/policy.py:160` |

其中 900 s 那道有个**结构性问题**：`core/autonomy/policy.py:203` 的 `latest` 取的是
**所有 source 的 `last_evaluated_at` 最大值**。interval / topic_followup / spontaneous_recall
每次 tick 都在刷新它，于是相机信号总被判 `duplicate`——这解释了那 27 次。

F 要做的是**通话内作用域放宽**，不是全局调小。即便如此，2700 s 全局间隔若在通话内放宽到
分钟级，一通 20 分钟的电话可能收到 5~8 条主动发言。这是你要的"互动性"还是骚扰，取决于阈值，
F 里给默认值和开关，**默认保守**，你在管理面自己调。

### 风险 4：GPU STT 在当前环境直接不可用（影响 D1、D2）

`admin/routers/transcribe.py:39` 硬编码 `WhisperModel("base", device="cpu", compute_type="int8")`。
换 GPU 看起来是最省事的提速手段，但实测：`ctranslate2.get_cuda_device_count()` 返回 1、
模型能加载，**推理时报 `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`**。
ct2 4.8.2 要 cuBLAS 12 / cuDNN 9 运行库，当前 venv 没装，也没有 `nvidia-*` pip 包。

也就是说 D **不能承诺"切 GPU 就快了"**。要走 GPU 得先补运行库，而那又会跟 Ollama 视觉模型
和 GSV 抢那 8 GiB（风险 1）。D 的主线因此是 **CPU 侧选型 + 切分策略**，GPU 作为可选项留配置位并
fail-loud 回落。

本机 CPU 实测（5 s / 12 s 合成音，`vad_filter=True`）：

```
base/cpu/int8  @5s   best= 1.44s  RTF=0.287
base/cpu/int8  @12s  best=15.10s  RTF=1.258   ← 比 small 还慢，解码循环退化
base/cpu/fp32  @12s  best=25.48s  RTF=2.123
small/cpu/int8 @5s   best= 2.34s  RTF=0.468
small/cpu/int8 @12s  best= 3.67s  RTF=0.306
```

两个结论直接进 D：

- `base` 在长片段上会退化到 RTF > 1，12 s 段落要 15 s 转写，逼近 `transcribe.py:139` 的
  20 s 超时。客户端 `MAX_SEGMENT_MS = 12_000`（`Emerald-client/src/windows/room/useContinuousCallVoice.ts:5`）
  正好踩在这个区间。**"延迟高"的主因可能不是模型太大，而是段落太长 + base 在长段上不稳。**
- `small/int8` 在 12 s 段上比 `base` 快 4 倍且准确度更高。**同时降延迟和提准确度是可达的**，
  路径是 `base → small` 加缩短分段，不是上 GPU。

（合成音会放大 base 的重复解码退化，真实语音差距应小于 4 倍；D 的验收要用真实录音复测。）

### 风险 5：文档与代码漂移（影响 D1、E）

`docs/audio-perception.md` 写"视频通话每 6 秒分段"，实际 `MAX_SEGMENT_MS = 12_000`。
改分段的工单顺手修正这处，不单独立项。

---

## 工单 A：本地资源争用解耦，视觉不再被 TTS 饿死

现状（已核实）：本机 TTS 合成在 `core/output/voice_adapter.py:692-696` 判定 loopback 后
`async with local_resource()` 持锁，GSV 走外部分句逐段 `predict`（`voice_adapter.py:564`
的 `_GSV_SYNTHESIS_LOCK` 内串行 N 段），**一条多句回复整段持锁**。周期视觉
`core/video_call.py:205-207` 用 `wait_for_resource=False`，锁一被占就立刻
`_counts["busy"] += 1` 并 return。客户端 4 s 一帧（`useVideoCallCamera.ts:5`），
于是 TTS 说话期间连续拿不到帧。

关键连带影响：这个 early return 发生在 `_queue_camera_signal()`（`video_call.py:239`）**之前**，
所以被饿死时**一个主动信号都不会产生**。你怀疑的"图像识别卡住导致角色没机会说话"成立。

- [ ] 核对锁的两侧调用点与显存约束；确认保留互斥（见风险 1），只改饿死语义。
- [ ] 周期观察改为**有界等待**而非立即放弃：新增上界（建议 `VISION_LOCK_WAIT_SECONDS`，
      默认略小于客户端帧间隔，避免请求堆积），超时仍返回 `busy` 并计数。单帧在飞的客户端
      约束（`busyRef`）已有，不会因等待而并发膨胀。
- [ ] **拆开"拿不到锁"与"不产生信号"**：TTS 占用导致跳过本帧时，仍按 `CAMERA_SIGNAL_INTERVAL_SECONDS`
      节流发射一次相机信号，证据用会话内**上一次成功描述**并标注其时效，没有历史描述才真正跳过。
      信号 evidence 保持 `untrusted_visual_description` 信任级，不因复用而升级。
- [ ] TTS 侧减少单次持锁时长：GSV 外部分句已是逐段合成，改为**按段释放并重新获取**共享锁，
      让视觉能在句间隙插入。段间重新排队会略微增加整句合成总时长，在验收里量化；
      若劣化明显则改为仅在段数超过阈值时让行。
- [ ] 观测：`/observability/video-call` 现有 `counts` 增加锁等待与让行计数（`vision_lock_waited`、
      `vision_lock_timeout`、`signal_from_stale_description`），复用现有端点与 `state.read` scope，
      不新增端点。
- [ ] 测试：复用 `tests/test_video_call.py`（已有 `local_resource()` 持锁用例，`:128`）。
      补三例：TTS 持锁期间周期观察在上界内拿到锁；超上界返回 busy **且仍发了信号**；
      会话无历史描述时不发信号。
- [ ] 换行与差异检查（`git diff --stat` 对比 `--ignore-cr-at-eol --stat`），独立提交。

注意：`core/prompt_builder.py` 与 `admin/routers/sensor.py` 有他人未提交改动，只暂存本工单文件。

---

## 工单 B：观察帧带采集时间戳

现状：`core/video_call.py:247` 的 receipt 存 `(expiry, uid, char_id, token_label, description)`，
**没有采集时刻**；`consume()` 只返回 `description`。注入时
`admin/routers/chat.py:882-885` 的包装文案写死"当前视频电话摄像头画面"。而客户端
`OBSERVATION_MAX_AGE_MS = 40_000` 允许复用 40 s 内的 receipt，后端 TTL 45 s——
角色被告知"当前"，画面实际可能是 40 秒前的。

- [ ] receipt 增加采集时刻字段；`consume()` 返回描述与**年龄秒数**（用 `time.monotonic()`
      与现有 expiry 同源，不引入第二种时钟）。
- [ ] 注入文案改为显式年龄，去掉无条件的"当前"：约 N 秒前采集。年龄按档位粗化表述
      （如刚刚 / 约 N 秒前），避免给出精确到毫秒的假精度。
- [ ] `observe_fresh_camera()`（角色主动看，`video_call.py:109`）路径同样带年龄；该路径本身是
      新帧，年龄接近 0，文案应体现"刚拉取"，与复用旧帧区分开。
- [ ] 同步 A 工单新增的"复用上次描述"信号：evidence 里也带年龄，避免角色把旧画面当现况。
- [ ] 测试：`tests/test_video_call.py` 补 receipt 年龄随时间推进、consume 返回年龄、
      过期仍拒绝；`tests/` 中 `/desktop/chat` 相关用例补注入文案含年龄、不含无条件"当前"。
- [ ] 换行与差异检查，独立提交。

---

## 工单 C：TTS 重复播报定位与修复

先说结论：**"重复前面已有的话"目前只能部分定位，本单第一步是定位而非直接改。**
已核实的事实与仍待确认的假设分开列，不要拿假设当结论改代码。

**已核实（读码确认）：**

- msg_id 在后端是**每轮一个、跨通道共享**的：`core/turn_sink.py:373-374` 优先用持久化
  `turn_id`，没有才 `uuid4()`，同一个 `_ws_msg_id` 同时用于 fanout、`message_segments`
  推送和 HTTP 返回。所以"后端每次投递重新生成 id 导致客户端去重失效"这条**可以排除**。
- 客户端去重键就是这个 msg_id：`CallSpeechPresenter.tsx:22` 的 `seenRef`。
  它有两个结构性弱点：
  1. **是 per-mount 的 `useRef(new Set())`**，组件重挂载即清空。重挂载后若同一条消息
     再次到达（重连补发、WS 重订阅），会被当新消息重新入队。
  2. **上限 64 且是 FIFO 淘汰**（`:23`），逐出最老的 id。长通话超过 64 条后，早期 msg_id
     被逐出，若该消息再次到达就能再说一遍。
- `splitLines`（`:11-13`）按 `\n+` 把一条消息拆成多行、逐行播报。去重只在 msg_id 粒度，
  **一旦一条消息被重复入队，它的所有行会整批重播**——听感正是"重复前面已有的话"。
- `message_segments` 是并行的第二条推送（`turn_sink.py:418-444`）。`CallSpeechPresenter.tsx:29-32`
  的注释明确写了"只有 canonical 事件产生语音，segments 是文本旁路，绝不是另一轮"——
  说明这个重复来源**以前出现过并已修过一次**（`turn_sink.py:422-428` 的注释也记录了
  空格 join 导致多段落重复显示的历史 bug）。**同类问题复发的可能性要优先排查。**
- `VoiceMessageBar` 的自动播放是 `autoPlayAttemptedRef` 一次性闸（`:185-189`），
  且 `key={current.id}` 让每行一个新实例，单行不会自己重播。但 `prepareAudio` 依赖
  `text`（`:138`），**同一段文本在不同行重复出现时会各自合成各自播放**——这在原文本身
  重复时是正确行为，不是 bug，排查时不要误判。

- [ ] **第一步只做定位，不改行为**：在通话中复现，抓取同一 `msg_id` 是否出现两次
      `channel_message`、以及重复播报的两行 id 是否同 msg_id 不同 index（原文重复）
      还是同 msg_id 同 index（投递重复）。**这两种情况修的地方完全不同**：
      前者是模型输出重复（属 prompt / 生成问题），后者是投递或去重失效。
      先给出判定再进第二步。
- [ ] 若是**投递重复**：核查 WS 重连补发路径（`channels/desktop_ws.py`）是否存在无 ack
      去重的待发缓冲，以及 `/desktop/chat` HTTP 响应体是否也带回复文本而客户端两条都念。
      修法优先"投递侧不重复"，而不是靠客户端去重兜底。
- [ ] 无论根因为何，**客户端去重都要加固**（这两条弱点本身就是缺陷）：
      去重集合提到组件外或持久化到会话作用域，避免重挂载丢失；容量上限改为按通话会话
      保留全部 msg_id（一通电话的量级完全放得下），或改用有序集合 + 时间窗淘汰，
      不要用"逐出最老"这种会让老 id 复活的策略。
- [ ] 若是**原文重复**（模型真的把上文又说了一遍）：那不是播放层 bug，属生成层问题，
      记入 `docs/known-issues.md` 并单独立项；本单不在播放层做"去掉重复句"这类
      掩盖性处理——那会把模型的真实输出改掉。
- [ ] 观测：通话内播报条数与去重命中数，便于下次直接看数据而不是靠耳朵判断。
- [ ] 测试：同 msg_id 二次到达不重复入队（含重挂载后）；超容量后老 msg_id 二次到达
      仍不重播；`message_segments` 到达不产生第二次语音。
- [ ] 换行与差异检查，独立提交（客户端与后端若都改则分两次提交）。

---

## 工单 D1：STT 延迟与准确度可配置化

现状：`admin/routers/transcribe.py:39` 硬编码 `WhisperModel("base", device="cpu", compute_type="int8")`，
进程内单例懒加载（`_stt_backend`），20 s 超时（`:139`）。当 `config.yaml` 存在 `stt_presets`
时走 `core/audio_perception.ingest_audio_bytes` 的远程 OpenAI 兼容链路；**当前实配没有
`stt_presets`，所以跑的是这条硬编码本地链路**。

先读风险 4 的实测数据：GPU 在本环境推理直接报 `cublas64_12.dll` 缺失，`base` 在 12 s 段上
RTF 1.258（比 `small` 慢 4 倍），客户端分段上限正是 12 s。**主线是选型加缩短分段，不是上 GPU。**

**范围确认（2026-09-30）：采纳 CPU 主线（`small` + `int8` + 缩短分段），
并且 device / compute_type 等必须在管理面可选——不是只留 `config.yaml` 字段。**
理由是这个仓库是开源的，别的用户机器不一样；本机以后也可能挂服务器换显卡。
管理面部分拆成工单 D2 单独交付。

- [ ] 把模型尺寸 / device / compute_type / beam_size / 超时从硬编码提到配置（建议
      `stt_local:` 块，与既有 `stt_presets` 远程链路并存，不改远程分支语义）。默认值改为
      实测更优的组合（`small` + `int8`），保留 `base` 作为低配回落。
      **默认值必须适配未知硬件**：开源用户可能没有 GPU、也可能内存很小，
      默认组合要在纯 CPU 低配机上能跑起来，不能默认假设有显卡。
- [ ] device 支持 `auto`：探测 CUDA 可用**并实际跑通一次极短推理**再采用，捕获
      `cublas`/`cudnn` 这类运行库缺失并 **fail-loud 记日志后回落 CPU**，不静默降级成"看起来在用 GPU"。
      配置显式写 `cuda` 而实际不可用时，返回 503 并在 detail 里点明缺少的运行库，不悄悄回落
      （显式配置失败要让人看见）。
- [ ] 模型热切换：配置变更后重建单例（现 `_init_stt` 只初始化一次，改配置不生效）。
      切换期间的并发请求要么用旧实例完成、要么等新实例，不能拿到半初始化对象。
- [ ] 客户端缩短分段：`Emerald-client/src/windows/room/useContinuousCallVoice.ts:5`
      的 `MAX_SEGMENT_MS` 从 12 s 降到 6 s 量级，与 `docs/audio-perception.md` 已写的
      "每 6 秒分段"对齐（顺手修正风险 5 的漂移）。同时复核 `END_SILENCE_MS = 900` 与
      `MAX_PENDING_SEGMENTS = 3`：分段变短则待处理队列上限应相应上调，否则说得快的时候会丢段。
- [ ] 已知遗留不在本单修复、只记账：`useContinuousCallVoice.ts` 在 `onstop` 里重启
      MediaRecorder，段边界有音频丢失窗口。缩短分段会**提高**边界出现频率，若验收听感变差，
      记入 `docs/known-issues.md` 并单独立项（改成双 recorder 交替或 AudioWorklet 连续采集）。
- [ ] 观测：转写耗时与所用模型 / device 进入现有 `core/api_call_log.py` 总账（`/observability/api-calls`
      已有 `state.read` 端点），不新增端点。
- [ ] 测试：配置解析与默认值、`auto` 探测失败回落 CPU、显式 `cuda` 不可用返回 503、
      配置变更触发重建。真实录音复测延迟（合成音会放大 `base` 的解码退化，数字不可直接采信）。
- [ ] 文档：`docs/audio-perception.md` 更新分段秒数与本地 STT 选型；
      `docs/feature-control-surface.md` 补 `stt_local` 字段、默认值与生效方式。
- [ ] 换行与差异检查，跨仓两次独立提交（后端一次、客户端一次）。

---

## 工单 D2：本地模型运行配置页（管理面）

新开一个管理面页面，集中放"本地跑的模型用什么硬件、什么精度"这类选项。动机：仓库开源，
别人的机器和本机不一样；本机以后也可能挂服务器、换显卡。这些选项散在 `config.yaml`
里改起来不现实。

现状与可复用的东西：

- STT 远程链路已有页面：`tts-config` 页 + `loadSttConfig()`
  （`admin/static/js/character.js:690`）+ `GET /stt-presets`
  （`admin/routers/settings_llm.py:1524`）。**那套管的是远程 OpenAI 兼容连接，
  不是本地运行参数**，两者不要混在一起。
- 页面注册三处：`admin/static/index.html` 的导航 `<a>` 与 `<div class="page">` 占位、
  `admin/static/js/core.js:293` 的 `loaders` 表。fragment 放 `admin/static/pages/`。
  可照 `embedding-config.html` 的卡片结构抄，那个页面同样是"一块模型配置 + 状态 + 保存"。
- 状态展示可照 `snapshot()`（`core/audio_perception.py:44`）的做法：
  返回 configured / effective / blocking_reason，页面直接显示**生效状态**而不只是回显配置。

- [ ] 新增页面（建议 `local-model-runtime`，中文标题「本地模型运行」）。**只放本地运行参数**：
      device（`auto` / `cpu` / `cuda`）、compute_type、模型尺寸、beam_size、超时。
      远程连接继续留在原页面，页面上互相给一句指路，不要两处都能改同一个字段。
- [ ] 每个选项旁标注**代价**，不只给下拉框：模型尺寸对应大致显存/内存占用与速度档位，
      compute_type 说明精度与速度的取舍。开源用户第一次看到这页时要能自己选对，
      不用回来读源码。
- [ ] 显示**实际生效状态**而非配置回显：当前真正加载的 device 与模型、
      以及 `auto` 探测的结论（落到 GPU 还是回落 CPU、回落原因）。
      这是这页最重要的一块——D1 里 `auto` 会静默回落 CPU，用户必须能看见它回落了。
- [ ] 硬件能力探测只读接口：可用 device、检测到的 GPU 与显存、缺失的运行库
      （如本机的 `cublas64_12.dll`，见风险 4）。**探测失败要给出人能看懂的下一步**，
      不是只抛一个 DLL 名字。scope 按现有设置页惯例取 `admin`。
- [ ] 保存后热切换（依赖 D1 的重建单例），页面显示切换结果；切换失败要显示失败原因
      并保留原有可用实例，不能把语音识别改坏了还显示保存成功。
- [ ] 静态资源版本：更新 `ADMIN_UI_FRAGMENT_VERSION`（`admin/static/js/core.js:23`）
      与 `index.html` 里相关 `?v=`，按 `AGENTS.md` 的 Admin Static Asset Cache 规则。
- [ ] 浏览器实测验收：打开新页面，核对文字、下拉项、生效状态与保存交互；
      故意配一个当前机器不支持的 device，确认页面显示的是回落/失败而不是假成功。
      **源码或构建成功不能代替可见结果验收**；若浏览器被环境阻断，报告里明确写
      「浏览器实测未完成」并记录替代验证。
- [ ] 预留扩展但不提前抽象：页面标题和结构按"本地模型运行"来组织，
      以后本地视觉模型（Ollama）或本地 TTS 的运行参数可以加进同一页。
      **本单只接 STT 一块**，不为将来的条目先建空壳。
- [ ] 文档：`docs/feature-control-surface.md` 补这页的归属、字段与权限；
      `docs/three-repo-doc-index.md` 若有页面清单同步一条。
- [ ] 换行与差异检查，独立提交。

---

## 工单 E：细粒度声线印象（契约升版）

**范围已确认（2026-09-30）：做"忽高忽低"与"带着气声"，做弱版"语气低沉"，
不做"哭腔"与"夹嗓子"**（理由见风险 2：缺 jitter/shimmer 与共振峰特征，且无个体基线）。
本单仍要升冻结合同版本。哭腔 / 夹嗓子若将来要做，需独立 brief 讨论个体声纹基线，
不在本单也不在本组。

现状：`core/audio_music_contract.py:20` 五标签冻结集，
`conservative_impression()`（`:275-319`）用 median_hz / variation / pace / dBFS 四个量映射。
`core/audio_analysis.py` 无 HNR、无频谱倾斜、无 jitter/shimmer、无共振峰。
硬预算 `SPEECH_ANALYSIS_TIMEOUT_S = 3.0`、`MAX_CONCURRENT_ANALYSIS = 1`、仅 numpy。

- [ ] 升 `CONTRACT_VERSION` 与 `ANALYSIS_VERSION` 到 v1，**保留 v0 常量与映射不删**
      （删除要独立授权 brief，`DELETION_AUTHORIZED = False`）。v1 标签集在五标签基础上**新增**，
      不重命名、不移除既有标签，旧 receipt 与旧 trace 仍可解释。
- [ ] 新增标签（建议命名保持英文 key、中文只在 prompt 文案出现）：
      - 忽高忽低：复用已有 `variation` 与 `trend`，零新特征，只加阈值与"高变异且无稳定趋势"判据。
      - 带着气声：新增 numpy 谐噪比或高频/总能量占比。必须在 3 s 预算内，实测超预算则降级为不发此标签。
      - 语气低沉（弱版）：低 median_hz 且低 pace，**文案不得出现"比平时"**，无基线不做相对断言。
- [ ] 判据冲突时回落 `unclear`，沿用 v0 的保守策略（`conservative_impression` 已有
      `quality: "conflict"` 语义），不做多标签并列输出，避免角色拿到一串矛盾印象。
- [ ] prompt 文案沿用 `core/audio_perception.py:335` `_acoustic_prompt()` 的克制措辞：
      仍标注"不确定的听觉印象，不是情绪 / 健康 / 人格事实"。新特征进"可读特征"行，
      不进断言行。层名仍为 `3.8_audio_impression`，不新增层。
- [ ] `TONES`（`core/audio_perception.py`）与 `PROVIDER_TONE_HINTS` 同步扩充；
      供应商旁路 tone 仍不得覆盖声学失败。
- [ ] 观测：印象标签分布与新特征计算耗时进现有音频观测面，便于判断新特征是否吃掉 3 s 预算。
- [ ] 测试：`tests/test_audio_music_contract.py:55` 的精确标签集断言随合同升版一并更新
      （这是合同变更的必然连带，不是绕过守卫）；补新标签的阈值边界、冲突回落 `unclear`、
      新特征超时后 fail-open 不阻塞 STT。
- [ ] 用真实录音人工核对：故意压低嗓子 / 故意气声 / 故意起伏说同一句话，看标签是否跟着变。
      误判率不可接受时宁可不发标签——**宁缺毋滥优先于覆盖率**。
- [ ] 文档：`docs/audio-perception.md` 更新标签集、v0→v1 差异、新特征的局限与"为什么不做
      哭腔 / 夹嗓子"的结论（留下判断依据，避免以后重复讨论）。
- [ ] 换行与差异检查，独立提交。

---

## 工单 F：通话内主动性放宽（A 落地后再评估）

**先做 A 再看。** 风险 3 的实测分布（`expired` 39 / `blocked_user_active` 31 /
`duplicate` 27 / `talk_sent` 3）说明视觉饿死只是其中一环，A 修完应重测一次，
再决定 F 要放宽到什么程度。

已核实的结构性问题：`core/autonomy/policy.py:203-210` 的 `latest` 取**所有 source
`last_evaluated_at` 的最大值**，interval / topic_followup / spontaneous_recall 每 60 s
tick 都在刷新它（`core/scheduler/loop.py:1100`，tick 周期 60 s），于是相机信号被
`min_interval_seconds`（900 s）判 `duplicate`。这条**不是通话特有的 bug**，但通话场景受害最重。

- [ ] 先做只读复测：A 落地后跑一次真实通话，从 `/observability/video-call` 与 autonomy
      run 记录取新的 disposition 分布。**先有数据再调阈值**，不凭感觉放宽。
- [ ] 修 `latest` 的跨 source 污染：相机这类**会话内**信号的最小间隔应按自身 source 的
      `last_evaluated_at` 判定，而非全局最大值。注意这会同时影响 ime / desktop_wake 等
      带 `allow_*` 豁免的 source，改动面要在单内说清，不要顺手改掉无关 source 的语义。
- [ ] 通话内作用域放宽，**不动全局默认值**：存在活跃相机会话时，对相机来源放宽
      `CAMERA_SIGNAL_INTERVAL_SECONDS`（`core/video_call.py:25`，现 60 s）与 autonomy
      最小间隔；全局 `global_proactive_min_gap_seconds`（现 2700 s）与 `max_daily_proactive`
      （现 16）在通话内按**会话上限**另算，会话结束即恢复。参考既有 `ledger_exempt`
      机制（`core/scheduler/policy.py:59`）的做法：豁免"能不能发"，**不豁免记账**。
- [ ] 通话内必须有独立上限：每通电话主动发言条数上限 + 最小间隔，默认保守
      （建议每通不超过 3 条、间隔不低于 3 分钟）。这是风险 3 的骚扰闸，**不可省略**。
- [ ] **已核实无需处理的一项**：通话内 STT 转写经 `RoomWindow.handleSend` → `sendChat` →
      `/desktop/chat` → `run_owner_chat_turn`（`admin/routers/chat.py:97`）确实调用了
      `record_user_message`，所以口头回应会清零 `consecutive_unanswered_talks`。
      `talk_gate.py:17` 的硬停在通话内不是真因，本单不改。
- [ ] **反而要处理的是 `blocked_user_active`（31 次，占比第二）**：用户一说话就
      `mark_user_active()`，`TriggerState` 转 `CHATTING`，而 `camera_silent` 要求
      `last_owner_turn_ts` 静默 ≥120 s（`core/autonomy/policy.py:160-162`）。
      一通说话频繁的电话里这个条件几乎永不成立——**角色结构性地没有插话窗口**。
      通话内应把这个静默门槛按会话作用域下调（配合上面的每通上限控制骚扰），
      这才是"互动性不够"最直接的一道闸。
- [ ] **同一道闸有第二处，只改 admission 不够**：`core/autonomy/runner.py:586` 与 `:629`
      在工具循环内反复调 `_user_became_active_for_job()`，非挂断 job 走
      `_user_active_recently()`（`core/scheduler/loop.py:364`，窗口同为 120 s）。
      即使放宽了 admission 的 `camera_silent`，相机 job 一进循环就会被判
      `CANCELED_BY_USER_ACTIVITY`。通话内相机 job 必须**同时**放宽这两处，用同一个
      会话作用域判据，否则 F 做完现象不变。（这也解释了为什么现有 `video_call_hangup`
      要专门走 `_is_video_call_hangup` 分支绕开它——通话内信号需要同类处理。）
- [ ] 开关与默认：通话内放宽整体默认**关闭**，管理面可开并可调上限。默认开启等于替你决定
      了骚扰阈值。
- [ ] 观测：通话内主动发言计数、被哪道闸挡下的分布，接入现有 autonomy 观测面，
      便于你按实际体感调参。
- [ ] 测试：`latest` 改为按 source 后既有 autonomy 用例不回归；通话内上限生效；
      会话结束后恢复全局语义；通话内豁免仍正常记账。
- [ ] 文档：`docs/feature-control-surface.md` 补通话内放宽的字段、默认值与生效条件。
- [ ] 换行与差异检查，独立提交。

---

## 建议执行顺序

1. **A**（拆饿死）→ 立刻能验证图像识别是否恢复，也是 F 的前置。
2. **B**（时间戳）→ 与 A 同文件，紧接着做省一次上下文切换。
3. **C**（TTS 重复）→ 独立，可与 A/B 并行交给另一条链。第一步是定位，不要直接改。
4. **D1**（STT 后端）→ 独立，实测数据已在手，改动面清楚。
5. **D2**（管理面页）→ 等 D1 的配置字段定型，否则页面要返工。
6. **F**（主动性）→ 必须等 A 的真实通话复测数据，不要凭感觉调阈值。
7. **E**（声线）→ 范围已定，可在 D1 之后任意时点做。

外部依赖只有一处：C 的第一步需要你配合复现一次通话（判定是投递重复还是原文重复）。
其余各单都能独立推进。


