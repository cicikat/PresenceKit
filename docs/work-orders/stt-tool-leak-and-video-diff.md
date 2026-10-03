# 语音链路 · 工具文本泄漏 · 视频帧差分 工单组

本组 8 张工单，覆盖五块需求：语音专有名词提示泄漏进转写结果、语音转文字偏慢
（含本地 STT 引擎可自选）、工具发现层描述泄漏到聊天、视频通话逐帧重复描述、
error/warning 日志改造。每张独立验收、独立提交。

开工前先读「零、调查结论与约束」。结论 1 和结论 3 会改变你对需求的理解：
需求 1「专有名词提示被拼进 prompt」与代码实际不符，根因是 ASR 模型的 prompt 回声；
需求 3「工具调用泄漏」不是系统把工具结果当回复发出，是模型复述了它在 system 里看到的工具目录。

**文末「已拍板的决定」里的四条取舍已由用户授权定下，实现时按它执行，不要再回头问。**

依赖与并行：

| 工单 | 主题 | 依赖 | 可并行 |
|---|---|---|---|
| A | 消除 ASR 专有名词提示回声 | 无 | 与 B、C、D 并行 |
| B | STT 可观测性 + 本地模型预热 | 无 | 与 A、C、D 并行 |
| C | 工具 meta 文本泄漏闸门 + xml_fallback 根因 | 无 | 与 A、B、D 并行 |
| D | 视频通话首帧全量 + 后续差分 + 变化触发 | 无 | 与 A、B、C 并行 |
| E | error.log 轮转 + 脱敏 + 台账统一 | 无 | 与 A、B、C、D 并行 |
| F | 外部依赖与轻量判定失败降噪 | **E 先落地更好**（降噪效果要在轮转后的台账上验） | 可与 A-D 并行 |
| G | DLQ 积压止血 + 积压可观测 | 无 | 与 A-F 并行 |
| H | 本地 STT 引擎可路由自选 + 接入 sherpa-onnx | 无（B 并行即可） | 与 A-G 并行 |

---

## 零、调查结论与约束

### 结论 1：专有名词文案不是被拼进 prompt，是 Whisper prompt 回声（影响 A）

需求描述是「专有名词会直接给模型 prompt：以下是语音中的专有名词：」。前提不成立，
但症状是真的，根因在 ASR 侧：

- `core/stt_vocabulary.py:38-43` 的 `prompt()` 生成整句中文提示
  「以下是语音中的专有名词，请按实际听到的内容转写：{canonical}（读音或常见误写：{heard}）；…」。
- 这段文本设计上只作为 ASR 引擎的 biasing hint，有两条消费路径，语义都正确：
  - 本地 faster-whisper：`admin/routers/transcribe.py:48-54`，传 `initial_prompt=hint or None`。
  - 远程 OpenAI 兼容 API：`core/audio_perception.py:219-222`，塞进 multipart 的 `prompt` 字段。
- **它从不进聊天 prompt。** 它是作为「转写结果」出现的：Whisper 家族在音频短、含静音、
  口齿不清或置信度低时，解码器会把 `initial_prompt` 当成「已经说过的上文」直接复读进输出。
- 两条路径都**没有回声检测**：远程侧 `core/audio_perception.py:238-254` 拿到 `text` 后直接
  `correct()` 当用户发言；`correct()`（`core/stt_vocabulary.py:53-65`）只做 `heard→canonical`
  替换，不认识模板文案本身。本地侧 `admin/routers/transcribe.py:47-66` 只按
  `no_speech_prob`/`avg_logprob` 过滤低置信度段（`:58`），不比对 `hint`。

这解释了用户观察到的全部三个现象：

| 现象 | 原因 |
|---|---|
| 内容一字不差地重复 | 词表不变时 `hint` 字符串完全一样，每次回声注入同一段文字 |
| 真正清晰的语音反而不带 | 音频质量好、置信度高时解码器走正常转写，不触发回声 |
| 像「跨轮残留」 | 污染文本被当成合法用户发言写入 history，走的是和打字完全相同的路径 |

注意：「空词表时仍保留前缀」这个假设在当前代码里**不成立**，`prompt()` 第 43 行
`if pairs else ""` 判空正确。真正的触发条件是「词表非空 + 本次音频质量差」。

### 结论 2：STT 没有模型重复加载问题，真正缺的是观测（影响 B）

两套互相独立的实现，都不存在「每次重新加载模型」：

| 路径 | 入口 | 实现 |
|---|---|---|
| QQ 语音消息 | `main.py:384-388` → `core/media_processor.py:681-698` | 远程 HTTP API（`stt_presets`），`core/audio_perception.py:214-235` |
| 桌宠/手机录音 | `POST /transcribe`（`admin/routers/transcribe.py:76-149`） | 若配了 `stt_presets` 走远程（`:92`），否则本地 faster-whisper |

- **本地模型已常驻**：`core/stt_local.py:225-246` `get_backend()` 用模块级 `_active` 按
  `(model_size, device, compute_type)` 缓存。默认 `small` / `auto` / `int8` / `beam_size=5`
  （`:32-38`）。真正的冷启动只发生在进程重启后第一次调用：`_build()`（`:165-222`）构造
  `WhisperModel` 并跑一次 `_smoke()` 真实推理，那一次请求的用户会明显感觉慢。
- **VAD 已开**：`admin/routers/transcribe.py:52` `vad_filter=True`，静音跳过已是最佳实践。
- **没有多余 ffmpeg 转码**：远程直传原始字节（`core/audio_perception.py:215-217`），
  本地由 faster-whisper 自带解码。唯一的解码点 `_decode_short_speech_wav`
  （`core/audio_perception.py:307-322`）只在 `speech_analysis` 开关启用时跑，限 2MB、超时 2s，
  且在转写**之后**执行、fail-open，不影响 text。
- **不被 conversation_gate 串行化**：QQ 侧 STT 在 `conversation_lock` 之前完成
  （`main.py:419-423`）；桌宠/手机侧 STT 是独立的 `/transcribe` 请求，前端拿到文字再发聊天。
- 转写结果要拼进 `content`（`main.py:414`）才能进 prompt，属于「回复生成所必需」的 await，
  **不违反 `AGENTS.md` 规则 10**。

观测缺口是本块的核心发现：

- 本地路径**有**埋点：`admin/routers/transcribe.py:40-44` 调
  `core.api_call_log.append(caller="stt", purpose="transcribe_local", duration_ms=...)`，
  可从 `GET /observability/api-calls` 看。
- 远程路径**完全没有埋点**：`core/audio_perception.py:214-268` 不写 `api_call_log`，
  且异常被 `except Exception: return None`（`:255-257`）静默吞掉、不打日志。
  **当前无法区分「网络慢」还是「供应商慢」，任何调优都是猜测。**

另：两条路径都是「整段音频等齐才开始转写」，没有流式/分段识别
（`core/media_processor.py:688-696` 用 `iter_chunked` 攒完整段才传）。这是长语音延迟的
结构性上限，但改动涉及三仓协议，本组**不做**，只记入 known-issues。

### 结论 3：工具文本泄漏是模型复述 system 里的工具目录，不是系统误发（影响 C）

泄漏文本来源确认为 `core/tool_discovery.py:58-63`，是分类折叠发现给 `load_tools_<category>`
生成的**函数 schema `description` 字段**：

```python
"description": (f"加载{description}的工具定义。{self._contents(category)}"
                "只发现工具，不执行任何业务操作；下一轮才能调用具体工具。")
```

**两条路径都存在，不是单一原因。**

**路径一（强泄漏源）：xml_fallback 把 schema 元数据 literal 注入 system 消息。**
`core/llm_client.py:488-509`，当目标模型 `tool_call_mode == "xml_fallback"` 时调
`_build_xml_tool_desc(tools)`（`:981-1000`），逐条把 `name`/`params`/`description`
格式化成文本行（`:999` `lines.append(f"- {name}({param_str}): {desc}")`）拼进 system 正文。
对这类模型来说，那段话**不是元数据，是货真价实出现在 prompt 里的可读文本**。模型没能正确
输出 `<tool_call>` 包裹的 JSON 时，`core/llm_client.py:667-668` 的 fallback 是**原样返回**：

```python
if '<tool_call' not in response and '</tool_call>' not in response and not response.startswith('__TOOL_CALL__:'):
    return ChatTurn(response, [], {'role': 'assistant', 'content': response})
```

这完全匹配用户看到的症状，也解释了「没有成功调用」——模型压根没发出合法工具调用。

**路径二：原生 function_calling 的解析失败兜底同样放行。**
即便 `tools=` 走 API 原生字段、模型理论上看不到描述文本，某些 OpenAI 兼容网关/模型仍会把
function 定义当「要讲给用户听的能力介绍」。此时 `core/pipeline.py:1503-1584` 的
`if not turn.tool_calls:` 分支里，`parse_tail_brace()`（`core/control_markers.py:44-63`）
只认尾部 `{true:...}`/`{false}` 协议标记，对工具描述自然语言毫无感知，直接
`outcome = ("natural", display_text)` 放行。后续 `guard_completion_claim()`
（`core/tool_grounding.py:82-100`）只拦「虚假已完成」声明（`_COMPLETION_CLAIM_RE`，`:15-21`），
`strip_control_markers()` 只管 `{true/false}`。**没有任何一层检测「这段文本其实是工具 meta」。**

**确认排除的两项：**

- **不是** `_sanitize_assistant_message`（`core/memory/short_term.py:199-239`）绕过脱敏——
  它只做括号动作描写裁剪和第三人称过滤，且只在**写入 history 时**生效（`:378`），
  在回复已发出之后才跑，不在发送路径上，加检测也堵不住。
- **不是**系统把 tool observation 当 final answer 发出——`_tool_message_content()`
  （`core/pipeline.py:1372-1389`）和 `discovery.load()` 的返回值只会追加成 `role: "tool"`
  消息进 `loop_msgs` 参与下一轮推理，不会成为 `outcome`。触发发送的始终是模型自己的
  `turn.content`。

### 结论 4：视频逐帧重复是「固定周期 + 固定全量 prompt + 零跨帧状态」（影响 D）

- 桌面端按约 4 秒间隔 `POST /video-call/observe`（`admin/routers/video_call.py:142-153`）。
  另有按需单帧工具链 `poll_camera`/`accept_camera_frame`/`observe_fresh_camera`
  （`admin/routers/video_call.py:109-139`、`core/video_call.py:138-171`）。
  这是视频通话**专用**通道，不是 desktop_ws 聊天通道，也不是 screen 截图链路
  （`docs/visual-perception.md:1-3` 明确区分 camera / screen 两条独立路由）。
- 调用点 `core/video_call.py:262-330` `observe()`，每帧都用**完全相同的固定 prompt**
  `OBSERVATION_PROMPT`（`core/video_call.py:25-33`，要求「描述当前摄像头画面」），
  **没有任何「只描述变化」的变体**。
- 结果**不走 `_layer` 注入**：产出 TTL 45 秒的一次性 `receipt`（`:327-330`），用户下一次
  聊天时 `admin/routers/chat.py:865-883` 若带 `video_observation_id` 就 `consume()` 取出，
  用 `observation_prefix()`（`core/video_call.py:343-347`）**前置拼进用户消息文本**。
- **当前完全没有跨帧 diff 或相似度比较。** 唯一跨帧状态是 `session["description"]`
  （`core/video_call.py:54,278,319`），每次成功后被新描述覆盖，用途仅限：视觉资源被本地 TTS
  占用时，`_signal_from_last_description()`（`:194-216`）复用这份过时描述补一次 signal。
- 两处节流都不是「按画面变化」：主动 signal 的 60 秒 `CAMERA_SIGNAL_INTERVAL_SECONDS`
  （`:48,78-99`）限制的是「多久塞一次 signal」；视觉模型调用频率完全由客户端固定 4 秒轮询驱动，
  后端 `observe()` 无节流、无去重、无相似度判断。

会话生命周期已经现成，**不需要新建会话模型**：

| 阶段 | 位置 |
|---|---|
| 会话对象 | `_camera_sessions: dict[(uid, char_id), dict]`（`core/video_call.py:54`），纯内存、进程内、无持久化 |
| 开始 | `observe()` 中无现存 session 或 token_label 变化即视为新会话（`:275-279`） |
| 活跃判定 | `camera_session()` 用 `seen_at` < `CAMERA_ACTIVE_SECONDS=15秒`（`:47,57-61`），断帧 15 秒自动视为结束 |
| 显式关闭 | `POST /video-call/close` → `close_camera()`（`:64-76`）：弹出 session、取消 pending future、清空未消费 receipts、`discard_pending_signals_by_source(..., {"video_call_camera"})` |

stimulus 侧已有现成链路 `_queue_camera_signal()`（`core/video_call.py:78-99`）：
`source="video_call_camera"`、`action_mode=REFLECT`、`priority=0.35`、`confidence=0.6`、
`expiry=CAMERA_SIGNAL_TTL_SECONDS=10分钟`、`dedupe_key` 分钟粒度。
**注意它不经 `core/perceive_event.py`**（没有 Dream Guard、没有幂等去重），而音乐先例
`core/music_playback_stimulus.py:199-218` 是先过 `receive_perceive_event()` 再 enqueue。
camera 路径的简化版可能是历史遗留。

### 约束 1：没有现成的跨帧差分/相似度工具可复用（影响 D）

已排查：`core/memory/` 下没有 `difflib`/`SequenceMatcher`/余弦相似度等通用文本相似度函数。
`memory_janitor` 的 episodic 近似重复合并是**文本记忆去重**，与「两帧描述是否变化很大」
不是同一场景（后者本质是语义变化检测）。

所以 D 不新建相似度基础设施，用模型输出自身做判定（见工单 D）。

另：screen 路由**已经**要求客户端「按固定周期在内存中采样并作场景变化比对，只在显著变化时
上传」（`docs/visual-perception.md:15`），camera 路由目前**没有对等要求**。这是更彻底的
省成本方向，但属跨仓改动，本组不做。

### 约束 2：视频通话主动性 gating 不可绕过（影响 D）

`core/video_call_presence.py:1-32` 的 `video_call_presence` 默认 `enabled=false`；开启后用
`max_talks_per_call`（硬上限）、`min_gap_seconds`、`silence_seconds` 代替全局节流，但
**DND / Dream Guard / unanswered-talk hard stop / circuit breaker / ledger accounting
均不放宽**（`:16-19` 注释明确列出）。接入点 `talk_check()` / `record_talk()`（`:94-118`）。

两个容易漏的联动点：

1. `job_is_call_scoped()`（`core/video_call_presence.py:75-79`）靠 `source == "video_call_camera"`
   判断是否适用通话内放宽规则。**新 source 换名字就要同步这里**，否则吃不到放宽。
2. `close_camera()`（`core/video_call.py:75`）的 `discard_pending_signals_by_source` 集合也要
   加上新 source 名，否则通话结束后残留 signal 会延迟触发。

### 约束 3：A 的治本方案与现有 hotwords 能力

`core/stt_vocabulary.py:46-50` 已有 `hotwords()`，返回逗号分隔的纯词表、没有模板句子，
回声风险远低于整句 `prompt()`。远程路径目前**完全没用它**，只用了 `prompt()`
（`core/audio_perception.py:219-222`）。这是 A 的治本方向。

---

## 工单 A：消除 ASR 专有名词提示回声

**需求来源**：「专有名词会直接给模型 prompt：以下是语音中的专有名词：，以下是语音里的
（这句话经常胡乱出现且重复，但是真的语音输入时有时候又不带）。很奇怪。」

**前提已修正**，先读「零、结论 1」。它不是 prompt 拼接 bug，是 Whisper prompt 回声被当成
转写结果采纳。

**依赖**：无。可与 B、C、D 并行。

### 必读

- 本文件「零、结论 1、约束 3」
- `core/stt_vocabulary.py`：`prompt()`（38-43）、`hotwords()`（46-50）、`correct()`（53-65）
- `core/audio_perception.py`：`_request()`（214-235）、`ingest_audio_bytes()`（238-268）
- `admin/routers/transcribe.py`：`_run_backend()`（47-66）、`_transcribe_sync()`（29-44）

### 改什么

1. **治本：远程路径改用 hotwords，不再发整句模板。**
   `core/audio_perception.py:219-222` 现在把 `prompt()` 的整句中文提示塞进 multipart
   `prompt` 字段。改为优先用 `hotwords()`（纯词表，`core/stt_vocabulary.py:46-50`）。
   - 需确认目标供应商是否支持 hotwords 语义的字段；OpenAI 兼容 `/audio/transcriptions`
     标准只有 `prompt`。**若只能用 `prompt` 字段，就传纯词表字符串而不是带「以下是语音中的
     专有名词，请按实际听到的内容转写：」前缀的整句**——去掉自然语言模板句本身就大幅
     降低复读概率。
   - 本地路径 `admin/routers/transcribe.py:52,65` 的 `initial_prompt` 同样处理。
2. **兜底：两处转写结果都加 prompt 回声剔除。**
   在拿到 `text` 之后、`correct()` 之前插入检测：
   - 远程：`core/audio_perception.py:238-254`（`ingest_audio_bytes`）。
   - 本地：`admin/routers/transcribe.py:47-66`（`_run_backend`，按段检测，`accepted.append`
     之前）。
   - 判定建议两条并用：命中固定字面串（如「以下是语音中的专有名词」「读音或常见误写」）
     直接丢弃；再加一道与 `hint` 的相似度比较（`difflib.SequenceMatcher` 即可）超阈值丢弃。
     真实语音几乎不可能原样说出系统提示模板。
   - 判定为回声时**走「未能听清」分支（返回空/None），不要把它当有效 text**。
3. **即使治本生效也要保留第 2 步。** 换成纯词表只是降低概率，不是消除——词表本身仍可能
   被复读（表现为转写结果变成一串专有名词）。相似度兜底能同时覆盖这种情况。
   **这一条已拍板为硬性要求**（见文末「已拍板的决定」第 1 条），不因为前缀去掉了就省掉。
4. **最后一道退路：若纯词表仍明显诱发回声，直接关掉该路径的 prompt 注入**，
   专有名词完全交给 `correct()` 事后纠正。已拍板的取舍是
   **准确率让一点，也不要让系统旁白漏进对话**——旁白是用户直接看见的破坏，
   转写偏差只是质量损失。做这一步时在报告里说明触发它的实际观察。

### 边界

- **不动 `prompt()` 的判空逻辑**（`core/stt_vocabulary.py:43` `if pairs else ""`）——
  它是对的，不是 bug 来源。
- **不要**为此关掉专有名词功能或默认禁用 `stt_vocabulary`。词表对转写准确率有真实价值，
  本单修的是提示注入方式和结果采纳，不是功能本身。
- `correct()` 的 `heard→canonical` 替换逻辑不动。
- 回声被丢弃时的用户可见行为要明确：是静默忽略该条语音，还是回一句「没听清」。
  **按现有「转写失败」路径的既有行为走，不要新造一种**。

### 验收

- 构造复现：录一段很短的、含大量静音或含糊不清的语音，在词表非空时反复发送，
  确认不再出现模板文案被当成用户发言。**这是本单唯一有意义的验收**，
  代码改动本身无法证明症状消失。若难复现，先用旧代码建立基线。
- 正常清晰语音确认转写质量未退化，专有名词仍被正确识别或纠正。
- 本地与远程两条路径各验一次（切换 `stt_presets` 配置）。
- 跑语音/音频相关既有回归；覆盖不足才补最小测试（回声判定函数适合补单测）。
- 行为变化，按 `AGENTS.md` 同步 `docs/audio-perception.md` 相关章节。

---

## 工单 B：STT 可观测性 + 本地模型预热

**需求来源**：「语音转文字响应得有点太慢了吧，有没有提速方法。」

**先读「零、结论 2」**：本地模型已常驻、VAD 已开、没有多余转码、不被锁串行化。
真正能确定的问题是**远程路径完全没有计时埋点**，以及**进程重启后首次调用的冷启动**。

**本单刻意不做盲目调参。** 没有埋点数据之前，改 `beam_size` 或换 provider 都是猜测。

**依赖**：无。可与 A、C、D 并行。

### 必读

- 本文件「零、结论 2」
- `core/audio_perception.py`：`_request()`（214-235）、`ingest_audio_bytes()`（238-268）
- `core/api_call_log.py`，以及 `admin/routers/transcribe.py:40-44` 的既有埋点写法
- `core/stt_local.py`：`get_backend()`（225-246）、`_build()`/`_activate()`（165-222）
- `docs/audio-perception.md`

### 改什么

1. **给远程 STT 补 `api_call_log` 埋点**（优先做，是后续一切判断的前提）。
   - 位置：`core/audio_perception.py:238-268`，围住 `_request()` 调用计时。
   - 字段对齐本地路径既有写法（`admin/routers/transcribe.py:40-44`）：
     `caller="stt"`、`purpose` 用可区分远程的值、`provider`、`duration_ms`、`ok`。
   - 可从现有 `GET /observability/api-calls` 查询，**不要新建端点**。
2. **异常不再静默**：`core/audio_perception.py:255-257` 的 `except Exception: return None`
   补上失败日志与失败埋点（`ok=False` 加失败原因）。现在转写失败在后端看不到任何痕迹。
3. **本地模型启动预热**：`core/stt_local.py` 的 `_active` 已做到常驻，但进程重启后第一个
   真实请求要承担 `_build()` + `_smoke()` 的构造开销（`:165-222`）。改为服务启动时主动预热。
   - **必须异步丢后台任务，不 await 完成**，不能阻塞主服务启动。
   - **必须 `try/except` 包裹**：未安装 faster-whisper、或纯远程部署（只配 `stt_presets`）时
     预热会走到 `ImportError`/`_build_legacy`，不能让它炸掉启动流程。
   - **只在配置实际选择本地 STT 时预热**，避免纯远程部署白白加载模型占内存。
4. **埋点落地后再谈调参**。在报告里给出实测的延迟分布（远程 P50/P95、本地首次 vs 后续），
   据此判断瓶颈在网络、供应商推理还是本地算力，并给出下一步建议。
   `beam_size` 已是可配项（`core/stt_local.py:36` 默认 5，`validate()` 允许 1-10），
   **降到 1 不需要改代码，只需调配置**——若实测表明本地推理是瓶颈，在报告里给出调整建议
   而不是直接改默认值。

### 边界

- **不做流式/分段转写。** 两条路径都是整段等齐才转写，这是长语音延迟的结构性上限，但改动
  涉及前端录音协议与三仓接口对齐，风险与体量都超出本单。按 `AGENTS.md` 记入
  `docs/known-issues.md`，跨仓部分同时记入 `docs/three-repo-interface-catalog.md` 标 `open`。
- **不动** `vad_filter=True`（`admin/routers/transcribe.py:52`），已是最佳实践。
- **不动** `_decode_short_speech_wav` 与 `speech_analysis` 开关——它在转写之后跑、fail-open，
  不是延迟来源。
- **不改 `beam_size` / `model_size` 默认值**，除非埋点数据明确支持，且在报告里给出前后实测对比。
- 远程路径做本地 VAD 预裁剪（减少上传体积）是候选方向，**本单不做**：收益取决于供应商延迟
  构成（若主要是排队/推理时间则裁剪收益有限），没有埋点数据前无法判断。在报告里列为待评估项。

### 验收

- 发若干条语音，确认 `GET /observability/api-calls` 能看到远程 STT 的 `duration_ms` 记录。
- 人为让远程 STT 失败（如改错 base_url），确认失败有日志且埋点记为失败，不再静默。
- 重启服务后立刻发一条语音，对比预热前后的首次转写耗时。
- 确认纯远程配置（不装 faster-whisper 或不配 `stt_local`）下服务正常启动、预热不报错。
- 跑音频相关既有回归。
- 新增观测能力，按 `AGENTS.md` 同步 `docs/audio-perception.md`；
  若 `docs/feature-control-surface.md` 记录了相关项一并更新。

---

## 工单 C：工具 meta 文本泄漏闸门 + xml_fallback 根因

**需求来源**：「模型有好几次调用工具的时候直接泄露到聊天里来了，并且没有成功调用，
比如『加载电脑桌面与应用操作的工具定义。含：desktop_minimize、…只发现工具，不执行任何
业务操作；下一轮才能调用具体工具。』」

**先读「零、结论 3」**。这是模型复述了它在 system 消息里看到的工具目录描述，
**不是**系统把工具执行结果当回复发出。两条路径都存在。

**依赖**：无。可与 A、B、D 并行。

### 必读

- 本文件「零、结论 3」
- `core/tool_discovery.py`：`_contents()`（37-48）、schema 组装（58-63）、`load()` 回执（66-73）
- `core/llm_client.py`：`_prepare_chat` 的 xml_fallback 注入（488-509）、
  `_build_xml_tool_desc()`（981-1000）、`chat_turn` 的 xml_fallback 分支（655-673）、
  `parse_probe_response()`（918-955）
- `core/pipeline.py`：`_run_steps` 无 tool_calls 分支（1503-1519、1580-1585）、
  最终发送前（1660-1670）
- `core/control_markers.py:44-63`、`core/tool_grounding.py:15-21,82-100`
- `docs/tools.md`、`docs/model-presets.md`

### 改什么

#### C1 发送前过滤闸门（优先做，一处覆盖两条路径）

1. 在 `core/pipeline.py:1660-1670`，即 `_anti_collapse_prefix_retry` 与
   `guard_completion_claim` 之后、`return _single_chunk(final_text) if stream else final_text`
   之前，加一道「工具 meta 文本泄漏」检测。
2. **判定用具体短语组合，不要用单个宽泛关键词。** 建议命中任一即判定泄漏：
   - 含 `core/tool_discovery.py` 的固定短语（如「只发现工具，不执行任何业务操作」
     「下一轮才能调用具体工具」「的工具定义」）；
   - 出现 `load_tools_` 前缀；
   - `_TOOL_REGISTRY` 中的工具名密集出现（如一句话里列举 ≥3 个注册工具名）。
3. **命中后的处理**：走一次静默重试（建议重新生成一次，并在该次请求里显式要求不得提及
   工具内部结构），重试仍泄漏则走兜底话术。**不要把泄漏文本发给用户。**
4. 泄漏判定次数要能观测（计数或日志），否则无法判断这个闸门到底在不在起作用、
   以及后续 C2 做完有没有真正降低触发率。

#### C2 xml_fallback 根因修复

5. `_build_xml_tool_desc()`（`core/llm_client.py:981-1000`）不应把分类折叠发现的 schema
   描述原样塞进文本提示。那段措辞（「下一轮才能调用具体工具」）是写给「决定调不调用」的
   模型逻辑看的 API 元数据，不是写给人类用户看的说明文。两条路选一：
   - 为 `load_tools_` 前缀的发现类工具在 xml_fallback 分支单独生成更简短、
     更像内部指令而非说明文的描述；
   - 或**对 xml_fallback 模式直接不启用分类折叠发现**，改为一次性暴露全部已筛选工具——
     折叠发现本来就是为 function_calling 的 schema token 预算设计的优化，
     xml_fallback 下它既不省 token 又引入泄漏面。
     选这条前先确认该模式下 prompt token 预算是否紧张。
6. **可选、低风险，建议与 C1 一起做**：把 `core/tool_discovery.py:58-63` 和 `:66-73`
   的文案改得更「内部指令化」（例如用类似现有 `{true/false}` 尾标记的控制符包裹），
   让它一旦泄漏更容易被 C1 的规则稳定识别，也更不像「该转述给用户的信息」。

### 边界

- **不要**只在 `_sanitize_assistant_message`（`core/memory/short_term.py:199-239`）或
  `core/turn_sink.py` 加检测。这两处都在「已经决定发送」之后执行，架构上不是做「是否发送」
  判断的位置，`_sanitize_assistant_message` 还只对 history 落盘生效，堵不住发给用户的那条。
- **不动** `guard_completion_claim`（`core/tool_grounding.py:82-100`）的虚假完成声明规则与
  `parse_tail_brace`（`core/control_markers.py:44-63`）的 `{true/false}` 协议——
  它们和本次泄漏无关，新检测是并列的一道，不是改它们。
- C1 的正则有误杀风险（正常对话也可能提到「工具」「不执行」）。**必须补测试用例覆盖
  正常对话不被误杀**，用具体短语组合而非单词。
- C2 碰的是 xml_fallback 这条相对少测的兼容路径，**必须对当前配置里实际在用 xml_fallback
  的 preset 跑一次真实回归**（见 `docs/model-presets.md`）。

### 验收

- 复现侧：用实际在用 xml_fallback 的 preset，反复触发需要工具的请求，确认不再出现工具目录
  文本被发到聊天里。**这是本单唯一有意义的验收**；若难稳定复现，先用旧代码建立基线。
- 确认正常的工具调用链路（function_calling 的 Path C `run_agentic_loop` 与 xml_fallback 两条）
  功能未退化：工具仍能被正确发现与执行。
- 确认正常对话不被闸门误杀（含有意提到「工具」的对话）。
- 确认泄漏判定计数/日志可见。
- 跑工具系统与 llm_client 相关既有回归；补上误杀回归测试。
- 同步 `docs/tools.md`；若改了 xml_fallback 的工具暴露策略，同步 `docs/model-presets.md`。

---

## 工单 D：视频通话首帧全量 + 后续差分 + 变化触发

**需求来源**：「视频电话时图像模型反复输出重复内容不仅导致输出注意力跑偏，还很占 token，
我在想能不能单独给视频模型里的图像模型做一个一次性（单次视频通话使用）的小记忆系统？
然后只有第一次输入齐全，后面只输出摄像头变化的内容，并且在变化内容大（比如人物做了什么动作，
就主动传入主模型主动触发的因素里面）。」

**先读「零、结论 4、约束 1、约束 2」**。好消息：会话生命周期、stimulus 框架、feature flag
与观测端点全部现成，改动可集中在 `core/video_call.py` 单文件，**不需要新建基础设施，
也不需要动 Emerald-client**。

**依赖**：无。可与 A、B、C 并行。

### 必读

- 本文件「零、结论 4、约束 1、约束 2」
- `core/video_call.py`：`OBSERVATION_PROMPT`（25-33）、`_counts`（43-45）、
  `_camera_sessions`（54）、`camera_session()`（57-61）、`close_camera()`（64-76）、
  `_queue_camera_signal()`（78-99）、`_signal_from_last_description()`（194-216）、
  `observe()`（262-330）、`observation_prefix()`（343-347）
- `core/video_call_presence.py`：`job_is_call_scoped()`（75-79）、`talk_check()`/`record_talk()`（94-118）
- `core/music_playback_stimulus.py:199-218`（stimulus 接入的完整先例）
- `core/perceive_event.py`、`admin/routers/video_call.py:61-65,99-106,142-153`
- `admin/routers/settings_feature_flags.py:18-44`
- `docs/visual-perception.md`、`docs/channels.md`、`docs/feature-control-surface.md`

### 改什么

#### D1 per-call frame memory

1. 在现有 session dict（`core/video_call.py:54`）上加字段，**不要新建会话对象、不要持久化**：
   ```python
   session["frame_memory"] = {
       "last_description": "",   # 上一帧描述
       "frame_index": 0,         # 0 = 首帧全量
       "last_change_at": 0.0,    # 上次判定显著变化的 monotonic 时间
   }
   ```
2. 生命周期**完全复用现有机制**：新会话创建（`:275-279`）、15 秒断帧自动失效
   （`CAMERA_ACTIVE_SECONDS`）、`close_camera()` 整条弹出（`:64-76`）。
   `frame_memory` 作为子字段天然随之清理，**清理侧不需要额外改动**。
   这正好满足需求里「一次性、单次通话」的要求。

#### D2 首帧全量 + 后续差分 prompt

3. `OBSERVATION_PROMPT`（`:25-33`）改为按 `frame_index` 选择两套：
   - **首帧**（`frame_index == 0`）：沿用现有全量 prompt，产出完整场景描述，
     存入 `frame_memory["last_description"]`。
   - **后续帧**：新 prompt，把 `last_description` 作为上下文传入，要求只输出相对上次的
     **变化**；无显著变化时输出约定的短 token。同时把输出上限收紧（如 100 字量级），
     直接降低每帧 token 开销。
4. **变化幅度判定不新建相似度基础设施**（见「零、约束 1」），用模型输出自身：
   - 约定无变化时输出固定字面量（如 `NO_CHANGE`）；
   - 想更可靠，让模型在描述前加结构化幅度前缀（如 `[minor]` / `[major]`），
     服务端解析前缀，而不是对整段做语义判断。**推荐这个**，因为 D3 的触发条件要用到幅度。
   - 缺点要知情：依赖模型遵循约定。解析不出前缀时**按 minor 处理（fail-safe，不触发主动）**。

#### D3 显著变化触发 stimulus

5. 复用 `_queue_camera_signal()`（`:78-99`）的框架，触发条件从「每 60 秒心跳式必发」
   改为「检测到 `[major]` 且距上次信号超过最小间隔」。`priority` 可略高于心跳信号
   （如 0.5），因为是动作触发而非定时。
6. **补上 camera 路径当前缺的 Dream Guard**：新信号先过
   `core/perceive_event.py` 的 `receive_perceive_event()`（`trust="low_trust"`、
   `require_dream_guard=True`）再 `enqueue_signal()`，对齐音乐先例
   （`core/music_playback_stimulus.py:199-218`）。
7. **`dedupe_key` 不能再用分钟粒度**（那是心跳式设计），改用变化内容指纹，
   避免同一变化被连续两帧重复触发。
8. **新 source 名的两个联动点必须同步**（见「零、约束 2」，这是最容易漏的）：
   建议用 `video_call_camera_change` 以便在观测里区分心跳与变化触发，但必须同时更新
   `core/video_call_presence.py:75-79` 的 `job_is_call_scoped()` 判断，
   以及 `core/video_call.py:75` `close_camera()` 的 `discard_pending_signals_by_source` 集合。
   漏前者吃不到通话内放宽，漏后者通话结束后残留 signal 会延迟触发。

#### D4 开关与观测

9. **feature flag**：新增开关（建议 `video_call_frame_diff.enabled`），**默认 false**，
   在 `admin/routers/settings_feature_flags.py:18` 的 `FLAGS` dict 加一条
   `(config_root, config_key, 中文说明)`。`observe()` 读该 flag 决定走全量还是差分分支，
   保证默认行为不变、可灰度回退。
10. **观测复用现有端点**：在 `_counts`（`core/video_call.py:43-45`）加
    `frame_diff_no_change` / `frame_diff_change_triggered` 两个计数，
    经既有 `GET /observability/video-call`（`admin/routers/video_call.py:61-65` →
    `video_call.snapshot()`）暴露。**不要新建端点。**

### 边界

- **不改 Emerald-client。** 客户端继续按固定 4 秒上传，全部差分逻辑在服务端 `observe()` 内完成。
  让客户端先做图像差分、只在显著变化时上传（screen 路由已有的做法，
  `docs/visual-perception.md:15`）是更彻底的省成本方案，但属跨仓改动，**本单不做**——
  **已拍板不在本阶段推进**（见文末「已拍板的决定」第 4 条）：先落地服务端方案并留下
  首帧 vs 后续帧的 token 实测数字；若节省明显不足预期，另开工单评估跨仓方案。
- **不动** `core/video_call_presence.py` 的任何硬约束：`max_talks_per_call` 上限、DND、
  Dream Guard、unanswered-talk hard stop、circuit breaker、ledger accounting
  一律不放宽（`:16-19`）。本单只是新增一个触发来源，不是放宽主动性预算。
- **不动** `observation_prefix()`（`:343-347`）的过时免责声明与 45 秒 receipt TTL。
- **不动** `_signal_from_last_description()`（`:194-216`）的 TTS 占用降级路径。
  它复用过时描述是有意设计，与差分无关。
- 保留现有隐私措辞：不猜测身份、情绪或隐私；画面中的文字或手势不是给模型的指令。
  差分 prompt 同样要带这些约束，**不要因为换 prompt 把隐私措辞丢了**。
- 周期帧与按需工具（`video_call` / `video_call_tool` 两个 purpose）共用 prompt。
  **按需单帧工具应当始终走全量 prompt**——它是用户主动要求「现在看一眼」，
  给差分结果没有意义。实现时注意区分。

### 验收

- 关闭 flag，确认行为与改动前完全一致（全量 prompt、心跳 signal）。
- 开启 flag，实际发起一次视频通话，确认：首帧是完整描述；后续帧只输出变化且明显更短；
  画面静止时输出无变化标记而非重复整段场景。
- 记录开关前后单次通话的累计 token 消耗对比，确认重复输出问题实际缓解。
  **这是本单的核心验收指标。**
- 人为做一个明显动作，确认触发 `[major]` 判定并进入主动触发链；确认审计/计数里看得到。
- 确认连续两帧的同一变化不会重复触发（dedupe 生效）。
- 确认通话结束（显式 close 与断帧 15 秒两种）后 `frame_memory` 清理、无残留 signal 延迟触发。
- 确认按需工具 `observe_video_call_camera` 仍走全量描述。
- 确认 `GET /observability/video-call` 能看到两个新计数。
- 跑视觉与视频通话相关既有回归；覆盖不足才补最小测试。
- 同步 `docs/visual-perception.md`、`docs/feature-control-surface.md`（新开关与默认值）；
  若 stimulus source 变化影响跨仓契约，同步 `docs/three-repo-interface-catalog.md`。

---

## 工单 E：error.log 轮转 + 脱敏 + 台账统一

**需求来源**：「我想让你帮忙看看最近的报错 error 和 warning，看看有没有值得注意的内容和改进」
「日志改造系列也写进工单里吧（顺带处理日志里的问题）」。

日志盘点结论见下「零点五」，**E / F / G 三张都基于它**：
E 修日志基础设施，F 处理噪音来源，G 处理 DLQ 积压。

**依赖**：无。可与 A、B、C、D 并行。**F 建议在 E 之后做**（降噪效果要在轮转后的台账上验）。

### 零点五、日志盘点结论

两条独立台账，均在 `data/logs/`：

| 台账 | 写入方 | 轮转/上限 | 脱敏 |
|---|---|---|---|
| `error.log` | `core/error_handler.py::_write_error_log()` / `log_error()`，经 `core/sandbox.get_paths().error_log()` | **无，纯追加**。当前 20.5MB / 308,697 行，跨 2026-08-08 ~ 2026-10-01 | **无** |
| `runtime_warnings-YYYY-MM-DD.jsonl` | `core/runtime_warning_log.py` 的 `RuntimeWarningJsonlHandler`（挂 root logger，捕获 WARNING+） | 按天轮转 + 保留 14 天 + 单日 8MB / 总量 48MB | 有，`admin/log_filter.py` 的 `RedactingFormatter` / `UrlRedactionFilter` |

另有功能观测台账（`gating_shadow.jsonl`、`fixation.jsonl`、`trigger_state.jsonl`、
`execute_dryrun.jsonl`），不是通用错误日志。控制台日志走 `main.py` 的 `logging.basicConfig`，
仅 StreamHandler、不落盘。

**三个配置缺口：**

1. `error.log` 从不轮转、无上限。两个月已 20MB，会无限增长。而
   `core/runtime_warning_log.py` 已有成熟的「按天轮转 + 保留 + 硬上限」方案没被复用。
2. `error.log` 不是系统 ERROR 的完整视图。直接走 `logging.error()` 的调用
   （如 `core.scheduler.loop` 的 `[scheduler] autonomy tick failed`）**不经过**
   `_write_error_log()`，只进 `runtime_warnings-*.jsonl`。排障必须两边都查，否则会漏。
3. `error.log` 无脱敏。`_write_error_log()` 直接写 `traceback.format_exc()` 原文，
   外部 API 报错若原样回传请求参数就可能带 URL/token。本次抽样未发现实例，但机制上无防护。

**按频次的问题清单：**

| # | 问题 | 频次 | 位置 | 性质 |
|---|---|---|---|---|
| 1 | wttr.in 证书过期 + 不可达 | 证书 612 + 连接 480 + 超时 195，约 error.log 总量 1/3 | `core/tools/weather.py:103` | 退化降级，有 fallback 文案 |
| 2 | `detect_affection` 轻量判定失败 | 2214（单模块最高）：超时 1320 + APITimeout 494 + `breaker_open` 185 + 403 144 | `core/llm_client.py:1257-1297` | 噪音为主，fail-open 返回 False |
| 3 | `event_edge_proposer` scope 超时 | 近 3 天 1121（WARNING 单项最高） | `core/scheduler/triggers/event_edge_proposer.py:271-275` | 设计内容错分支 |
| 4 | `empty_completion` 空补全 | 近 3 天 706 | `core/llm_client.py` | **已有 known-issues 专项跟踪** |
| 5 | `sensor_judge` 调用超时 | 近 3 天 101 | `core/scheduler/sensor_judge.py` | 噪音，fail-open |
| 6 | DLQ 积压 93 个失败任务 | 近 3 天报 3 次 | `core/scheduler/triggers/time_based.py` `_check_dlq_monitor` | **值得关注** |

积压明细：`consolidate_to_identity: 47`、`reflect_to_episodic: 41`、`practice_session: 4`、
`toy_autogrow: 1`，落在 `data/logs/dead_letter_queue/`。

**已修复的历史噪音，不要再去动：**

- `cannot access local variable 'time'`（45 次，9-25 / 9-30）：`core/autonomy/policy.py`
  `admission()` 内局部 `import time` 遮蔽模块级导入。已由 commit `b66d633` 修复。
- `_write_trigger_audit_log() got an unexpected keyword argument 'delivery_kind'`（2026-09-02，49 次）：当前签名已兼容。
- `_get_current_time() got an unexpected keyword argument '_noargs'`（2026-09-11，14 次）：当前定义无此冲突。

### 必读

- 上面「零点五」
- `core/error_handler.py`：`_write_error_log()` / `log_error()`
- `core/runtime_warning_log.py`：`RuntimeWarningJsonlHandler`（轮转与上限的参照实现）
- `admin/log_filter.py`：`RedactingFormatter` / `UrlRedactionFilter`
- `core/sandbox.py`：`get_paths().error_log()`
- `core/scheduler/triggers/time_based.py`：`_check_dlq_monitor`
- `docs/known-issues.md`

### 改什么

#### E1 error.log 轮转 + 脱敏

1. **给 `error.log` 加轮转与上限**，复用 `core/runtime_warning_log.py` 已有的方案
   （按天轮转 + 保留天数 + 单日/总量硬上限），不要另造一套参数体系。
   - **方案已拍板：走 (a)，让 `_write_error_log()` 自己按日轮转，保留独立台账。**
     不要合并进 `RuntimeWarningJsonlHandler`（理由见文末「已拍板的决定」第 2 条：
     纯文本 tail 与结构化 jsonl 查询用途不同，合并会让最高频的排障动作变绕）。
   - 轮转天数、单日上限、总量上限**直接取 `runtime_warning_log` 的同一组值**，
     不要另起一套数字。
2. **给 `error.log` 接上脱敏**：复用 `admin/log_filter.py` 的
   `RedactingFormatter` / `UrlRedactionFilter`，不要新写脱敏规则。
3. **现存的 20MB `error.log`：归档压缩，不删不留原样**（已拍板，见文末第 3 条）。
   - 重命名为带日期后缀的归档名并 gzip，留在同一日志目录，由新接入的保留策略
     按天数规则自然淘汰。
   - **归档前要过一遍脱敏**，不要只压缩了事——里面是两个月未脱敏的异常栈原文。
   - 这一步**作为一次性迁移动作**实现（脚本或启动时的幂等迁移均可），
     不要做成每次启动都扫一遍历史文件的逻辑。
4. **明确两套机制的覆盖范围**（缺口 2）。既然保留两套并存，
   必须在 `docs/` 里写清「哪类错误进哪个台账、排障要查哪里」，
   否则下一个人仍会漏掉只走 `logging.error()` 的那一半。

#### E2 台账覆盖面统一（缺口 2）

5. **让 ERROR 的分工可查。** 现在 `logger.error()` 与 `log_error()` 落在不同台账，
   排障要查两处，已经实际导致漏看（`[scheduler] autonomy tick failed` 那个真 bug
   只在 WARNING 台账里，`error.log` 完全没有）。
   既然已拍板保留两套（E1 第 1 条），本条的交付是**让分工显式可见**：
   - 在 `docs/` 写清「哪类错误进哪个台账、排障查哪里」。
   - 在每个新轮转出的 `error.log` 文件头部写一行指向另一台账的说明，
     让直接 tail 文件的人也能看到「这里不是全量」。
6. **只读观测**：确认日志台账有无按需查询入口。若排障只能靠登录看文件，
   按 `AGENTS.md` 规则 7 考虑补一个只读端点（可复用现有 observability），
   scope 按敏感度选——**日志含异常栈，敏感度偏高，不要选过宽的 scope**。

### 边界

- **不改** `core/runtime_warning_log.py` 现有的轮转参数与保留策略。它工作正常，本单是复用它。
- **不碰** #4 `empty_completion`，已有 known-issues 专项跟踪。
- **不在本单改任何业务代码的日志级别或调用逻辑**——wttr.in、`detect_affection`、
  `event_edge_proposer` 全部归 F。本单只碰日志基础设施。
- **不删任何历史日志内容。** 那个 20MB 的 `error.log` 按 E1 第 3 条脱敏后归档压缩，
  **不是删除**——F 的降噪判断依赖它作为历史证据。
- `error.log` 路径必须继续经 `core/sandbox.get_paths()`（`AGENTS.md` 强制规则 1）。
- 已修复的三项历史噪音（见「零点五」）**不要记入** known-issues，也不要去"修"——
  代码已变，留痕无意义。

### 验收

- 轮转：人为写入超过单日上限的错误，确认按预期轮转、旧文件按保留策略清理、总量上限生效。
- 脱敏：构造一条带 URL 凭据或 token 样式字符串的异常，确认落盘后已脱敏。
- 历史文件：确认 20MB `error.log` 已脱敏后归档压缩、内容未丢失，
  且迁移是幂等的（重复执行不会再处理一次或产生重复归档）。
- 覆盖面：确认文档明确写出了两套台账的分工，且新轮转文件头部有指向说明；
  抽查 `[scheduler] autonomy tick failed` 这类只走 `logging.error()` 的错误，
  确认按文档描述能在 `runtime_warnings` 里查到。
- 跑 error_handler / 日志相关既有回归。
- 日志落盘位置或排障方式有变化时同步相关文档。

---

## 工单 F：外部依赖与轻量判定失败降噪

**需求来源**：「顺带处理日志里的问题」。

E 修的是日志基础设施，本单修**产生噪音的源头**。盘点见「零点五」的问题清单：
#1 wttr.in 占 error.log 总量约 1/3，#2 `detect_affection` 单模块 2214 次，
#3 `event_edge_proposer` 近 3 天 1121 次。

**这三项的共同特征是 fail-open、不影响主链路**，所以此前一直被容忍。
但它们把真问题埋在噪音里——E 的轮转做完后，若不降噪，有效日志窗口会被这些条目吃掉。

**依赖**：建议 E 先落地（降噪效果要在轮转后的台账上验证）。可与 A-D 并行。

### 必读

- 本文件「零点五」问题清单 #1 #2 #3 #5
- `core/tools/weather.py:103` 及其 fallback 文案路径
- `core/llm_client.py:1257-1297`（`detect_affection`）、电路熔断器实现
- `core/scheduler/triggers/event_edge_proposer.py:271-275`
- `core/scheduler/sensor_judge.py`
- `core/api_call_log.py`、`docs/known-issues.md`

### 改什么

#### F1 外部依赖故障不再灌 ERROR（#1 wttr.in）

1. **把外部第三方服务的网络/证书故障降级为 WARNING**，不再写入 `error.log`。
   wttr.in 是免费第三方服务，证书过期（`[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired`）
   与连接失败完全不可控，已有「天气查询出错」fallback 文案，**不是本系统的错误**。
   - 改动点：`core/tools/weather.py:103` 的异常处理。
   - **保留可观测**：降级不等于静默。失败仍要进 WARNING 台账或 `api_call_log`，
     否则"天气一直不可用"会变成无人知晓。
2. **加成功结果缓存**，减少失败暴露面：wttr.in 可用时缓存上次成功结果，
   失败时返回带时间标注的缓存值而不是直接报错。
   - TTL 由你定（天气数据 30-60 分钟量级合理），**缓存必须带时间标注**，
     让模型知道这是多久前的天气，不要让它把缓存当实时。
   - 缓存落盘须经 `core/sandbox.get_paths()`（`AGENTS.md` 规则 1）；纯内存缓存也可接受，
     在报告里说明选择。

#### F2 轻量判定调用的失败止损（#2 `detect_affection`、#5 `sensor_judge`）

3. **先核实配置，再改代码。** 2214 次失败里有 144 次 `PermissionDeniedError`（403，
   模型分组无可用渠道）和 185 次 `breaker_open`，强烈提示 `detect_emotion` call_category
   当前路由的 preset / 供应商分组**可能一直不可用**。
   - **这是本单的第一步，必须先做**：核实该 preset 路由的实际可用性（手动打一次该 preset）。
   - 若确认不可用：这是配置问题，**修配置而不是改代码**，在报告里说明，
     并确认修好后失败率是否归零。
   - 若配置正常、只是超时：走第 4 步。
4. **改为按次采样调用，而非每轮必调。** `detect_affection` 与 `sensor_judge` 都是
   fail-open 的轻量判定，每轮必调在上游不稳时就是每轮打一次空炮。
   - 采样率可配，默认值按实际价值定（这两个判定的产出若只影响软提示，低采样率足够）。
   - **熔断器打开期间不要继续尝试**：185 次 `breaker_open` 是熔断器在正常工作，
     但这些被拒绝的调用仍在写日志。熔断期内应直接跳过并静默（或只记一次聚合计数），
     不要每次都留一条 ERROR。
5. **失败日志聚合**：同一类失败在短窗口内连续发生时，写一条带计数的聚合记录，
   而不是每次一条。这条对 #2 #3 #5 都适用，是降噪收益最高的一项。

#### F3 scope 超时：先判定性质再决定（#3 `event_edge_proposer`）

6. 近 3 天 1121 次，**必须先确认是所有 char_id 持续性超时还是偶发**。
   - 若是**持续性**的：说明底层调用（可能是 LLM）系统性过慢，
     正确做法是提高 `_scope_timeout_seconds` 阈值或排查上游延迟，
     **不是继续吞警告**。排查上游延迟可参照 B 的埋点结论。
   - 若是**偶发**的：维持现有容错分支，只按 F2 第 5 步做日志聚合。
7. **不要在没判定性质前就调阈值。** 盲目加大超时会把系统性过慢藏得更深。

### 边界

- **不改** wttr.in 的 fallback 文案与工具契约——用户可见行为不变，本单只改日志级别与缓存。
- **不改** 电路熔断器本身的阈值与恢复逻辑。它在正常工作，本单只是不再为它的每次拒绝写 ERROR。
- **不动** #4 `empty_completion`，已有专项跟踪。
- **不碰** `detect_emotion` 主路径。`detect_affection` 复用它的 preset，但本单只改
  `detect_affection` / `sensor_judge` 这两个判定的调用频率，
  **不要顺手改 `detect_emotion` 自身的调用时机**（它在 `post_process_slow` 里，
  受 `AGENTS.md` 规则 10 约束，改它是另一件事）。
- 降噪**不等于静默**。每一处降级都要保留可查询的痕迹（WARNING 台账、聚合计数或
  `api_call_log`）。把错误彻底藏起来比噪音更危险。

### 验收

- 跑一段时间（或人为制造失败），确认 `error.log` 的新增条目里 wttr.in 与熔断拒绝
  **不再出现**，但在 WARNING 台账或 `api_call_log` 里**仍能查到**。
- 对比降噪前后单位时间的 error.log 增长量，给出实测数字。
  **这是本单的核心验收指标**（盘点时 #1 占总量约 1/3、#2 是单模块最高）。
- 天气缓存：断网或改错 base_url，确认返回带时间标注的缓存值而非报错；
  确认模型拿到的文本里有「多久前」的信息。
- `detect_emotion` preset 路由核实结论写进报告；若是配置问题，确认修复后失败率变化。
- 采样生效后确认 `detect_affection` / `sensor_judge` 的功能产出仍可用（不是被改成永不调用）。
- `event_edge_proposer` 超时性质的判定结论写进报告，并说明是否动了阈值及依据。
- 跑 weather / llm_client / scheduler 相关既有回归。
- 日志级别与采样是行为变化，同步 `docs/known-issues.md`（关闭或更新对应条目）；
  新增配置字段时同步 `docs/feature-control-surface.md`。

---

## 工单 G：DLQ 积压止血 + 积压可观测

**需求来源**：「顺带处理日志里的问题」。

盘点发现 `data/logs/dead_letter_queue/` 积压 **93 个失败任务**：
`consolidate_to_identity: 47`、`reflect_to_episodic: 41`、`practice_session: 4`、
`toy_autogrow: 1`。前两个是记忆固化与情景反思，积压近 90 个意味着这两条管线
可能持续性失败，而不是偶发。

`_check_dlq_monitor` 近 3 天只报了 3 次 WARNING——**信号重但几乎看不见**。

**依赖**：无。可与 A-F 并行。

### 必读

- 本文件「零点五」#6
- `core/scheduler/triggers/time_based.py`：`_check_dlq_monitor`
- `data/logs/dead_letter_queue/` 实际文件（形如 `<ts>_reflect_to_episodic.json`）
- `core/memory/fixation_pipeline.py`：`consolidate_to_identity` / `reflect_to_episodic` 的 handler
- `docs/memory.md`、`docs/known-issues.md`、`AGENTS.md` 规则 6（记忆写入点的 provenance 义务）

### 改什么

#### G1 先诊断：积压是什么原因

1. **抽样读 DLQ 文件里的实际失败原因**，归类判断是：
   - 与 F 的 #2/#3/#5 同源的底层模型调用超时级联，
   - 还是这两条记忆管线自身的 bug（schema 不匹配、并发锁、数据格式）。
2. **诊断结论决定后续，不要跳过这一步直接改管线。**
   - 若是**模型超时级联**：本单不改记忆管线，在报告里指向 B（埋点）与 F（降噪止损）的结论，
     并在 G2/G3 做可观测与重试治理。
   - 若是**管线自身 bug**：**单独开工单修**，不要在本单顺手改。
     `consolidate_to_identity` 与 `reflect_to_episodic` 都是记忆写入点，
     受 `AGENTS.md` 规则 6 约束（必须同步 `provenance_log.append()`），
     改动需要配套，体量超出本单。本单只负责定位与止血。

#### G2 积压可观测（按 `AGENTS.md` 规则 7）

3. **补 DLQ 积压的只读查询入口。** 现在排障无法按需查看积压现状，只能等
   `_check_dlq_monitor` 偶尔报一次 WARNING。
   - 可复用现有 observability 端点，**不必新建**。
   - 至少暴露：按任务类型的积压计数、最早积压时间、最近失败原因样本。
   - **scope 按敏感度选**：DLQ 内容含记忆固化的待处理数据，敏感度高，不要选过宽的 scope。
4. **让监控信号更可见**：近 3 天只报 3 次说明阈值或频率不合适。
   积压持续超过阈值时应有稳定可见的信号，而不是偶尔一条 WARNING。
   具体做法由你定（提高检查频率、或按积压增量而非绝对值告警）。

#### G3 积压止血

5. **处理现存 93 个积压任务**。两条路，**选哪条取决于 G1 的诊断结论**：
   - 若失败原因已消除（如配置修好）：提供可重入的重试入口，小批次重放，
     **必须 dry-run 默认**（参照 `scripts/migrate_memory_events.py` 的既有做法，
     见 `AGENTS.md` 关键文件速查表 Brief 205 条目）。
   - 若失败原因未消除：**不要重放**，重放只会再失败一次并加倍积压。
     此时只做 G2 的可观测，并在报告里明确说明积压保留原因。
6. **DLQ 不应无界增长**。确认有无保留上限或过期清理；若无，加一个
   （与 E 的日志轮转同理）。
   - **过期不等于物理删除**：DLQ 里是待处理的记忆数据，
     参照 `AGENTS.md` 的「遗忘=降级而非删除」原则，
     过期处理方式需在报告里说明，**不要擅自物理删除失败任务数据**。

### 边界

- **不改记忆管线本身**（`consolidate_to_identity` / `reflect_to_episodic` 的 handler）。
  本单只做诊断、可观测、止血。确认是管线 bug 就另开工单。
- **不擅自批量删除或重放 DLQ 数据。** 重放必须 dry-run 默认、小批次、可重入；
  删除需要明确授权（`AGENTS.md`：数据删除属需确认操作）。
- **不改** `_check_dlq_monitor` 的任务分类逻辑，只改它的可见性。
- 新增端点或重放脚本涉及的路径必须经 `core/sandbox.get_paths()`。

### 验收

- G1 诊断结论写进报告：按任务类型给出失败原因归类，明确是级联超时还是管线 bug。
- 观测端点能查到按类型的积压计数与最近失败样本；确认 scope 不过宽。
- 若做了重放：dry-run 先验证，再小批次实放，确认可重入（重复执行不产生重复写入）、
  确认重放成功的任务从 DLQ 移除。
- 若未重放：报告明确说明原因与保留决定。
- 确认 DLQ 有保留上限或过期策略，且过期处理不物理删除证据。
- 跑 scheduler 与 memory 相关既有回归。
- 积压现状与诊断结论记入 `docs/known-issues.md`；若确认是管线 bug，
  在 known-issues 里留下指向新工单的条目。

---

## 工单 H：本地 STT 引擎可路由自选 + 接入 sherpa-onnx

**需求来源**：「STT 不能用本地的吗，`<本机 IME 仓库>` 这个我手机输入法都带得动
（但有时候单字会重复不知道是啥原因）」「STT 也做个路由自选呗。不准没关系，我觉得输入法
那个还 ok，反而是当前这个又慢误差又大。」

**用户已明确拍板**：接入 sherpa-onnx 并做成可自选，**不要再做可行性评估或准确率论证**。
现有 faster-whisper 的实际体验是「又慢误差又大」，这是来自真实使用的判断，
比任何基准测试都更有权重。本单是**实现工单**，不是 spike。

**依赖**：无。可与 A-G 并行（B 的埋点能顺带给出前后对比，但不阻塞本单）。

### 零点八、前置调查结论

**先纠正一个前提**：现有后端**已经有本地 STT**，不是只能用远程。
`core/stt_local.py` 跑 faster-whisper（默认 `small` / `int8`，模型已常驻，见「零、结论 2」）。
只有配了 `stt_presets` 时才优先走远程（`admin/routers/transcribe.py:92`）。
所以这个需求的实质不是"能不能本地"，而是**"能不能换一个更快的本地方案"**。

用户参考的输入法用的是另一套技术栈：

| 项 | 内容 |
|---|---|
| 引擎 | **sherpa-onnx**（k2-fsa），非 whisper 系 |
| 模型 | `csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`（HuggingFace，commit `98590b7`） |
| 架构 | **streaming zipformer / pruned transducer（RNN-T 类）**，非 CTC、非 whisper、非 paraformer |
| 文件 | `encoder/decoder/joiner-epoch-99-avg-1.int8.onnx` + `tokens.txt`，int8 量化，合计约 40-70MB |
| 语言 | 中英双语混合（bilingual zh-en），**不是多语言通用模型** |
| 流式 | **是**（online），边说边出字 |
| Android 侧运行时 | 官方预编译 `sherpa-onnx.aar` v1.13.7，非手写 JNI |

**关键可行性结论：这套方案可以直接复用到 Windows + Python。**
sherpa-onnx 是 C++ 核心 + 多语言 binding，Python 侧有官方 `pip install sherpa-onnx`，
API 与 Kotlin 侧一一对应（`sherpa_onnx.OnlineRecognizer.from_transducer(...)`）。
模型文件从同一个 HuggingFace repo 直接下载即可，**不需要从输入法仓库里扒，也不绑定 Android**。

**现有代码已经具备引擎可切换的骨架，本单是沿用而非新建**（这是开工前必须看清的）：

| 现成的东西 | 位置 |
|---|---|
| `stt_local` 配置块 + 严格 `validate()` + 坏配置降级默认值 | `core/stt_local.py:49-90` |
| 热切换：`get_backend()` 按 key 复用、`reload()` 失败时保留旧实例 | `core/stt_local.py:225-250` |
| `_build()` 已返回带 `backend` 字段的 dict（`"faster_whisper"` / `"whisper"`） | `core/stt_local.py:163-205` |
| 已有 openai-whisper 作为第二后端的先例 `_build_legacy()` | `core/stt_local.py:195-205` |
| 调用侧已按 `backend["backend"]` 分支 | `admin/routers/transcribe.py:47-66` |
| 硬件探测端点（只读、不加载模型） | `core/stt_local.py:111-133` → `GET /settings/local-runtime/hardware` |
| 设置 API：先热切换、成功才落盘 | `admin/routers/settings_local_runtime.py:51-69` |
| 管理面页面 | `admin/static/pages/local-model-runtime.html` + `admin/static/js/local-runtime.js` |

即「本地 STT 引擎可自选」的**配置、校验、热切换、落盘、管理面、观测埋点全都已存在**，
本单要做的是把 `backend` 从隐式推断（装了什么用什么）变成**显式可选项**，并新增一个引擎实现。

**顺带解释用户的「单字重复」**：输入法仓库里没有任何相关问题记录或修复
（查了全部 6 个 voice/ASR 相关 commit 与 docs/cc-tasks），但代码层面有三条可解释线索：

1. `enableEndpoint = false`（输入法 `voice/LocalVoiceInput.kt:36`）——**主动关掉了端点检测**。
   整段语音在同一个 stream 里持续增量解码，没有"说完一句切一刀"的机制。
   transducer 类解码器在静音段或音量弱处容易重复输出同一 token，
   这是 **RNN-T/transducer 架构的已知通病，不是那个仓库的 bug**。
2. 上层只做「整句文本变化即回调」的朴素 diff（同文件 `:75-99`），
   **没有任何重复字符的后处理去重**，底层吐重复就原样显示。
3. 用的是 sherpa-onnx 默认贪心解码（greedy search），没有 beam 宽度或 repetition penalty 配置——
   而这些正是 transducer 重复问题的常见调节点。

**这三点是本单的硬性实现要求**：接入 sherpa-onnx 时
**必须开端点检测 + 必须做重复 token 后处理 + 不用纯贪心解码**，
否则会把用户已经抱怨过的「单字重复」原样搬到后端。

**另一个前置纠正**：本地路径**已经在传 `hotwords()`**
（`admin/routers/transcribe.py:52` 的 `"hotwords": hotwords() or None`），
不是只传 `initial_prompt`。所以工单 A 的「治本」只针对**远程路径**
（`core/audio_perception.py:219-222` 确实只用了 `prompt()`）与本地的 `initial_prompt` 参数。
本单换引擎后，专有名词偏置要改走 sherpa-onnx 自己的 contextual biasing（见下 H4）。

### 必读

- 上面「零点八」、本文件「零、结论 2」
- `core/stt_local.py` 全文（本单主要改动面）：`MODEL_SIZES`/`DEVICES`/`COMPUTE_TYPES`/
  `DEFAULTS`（29-38）、`validate()`（49-77）、`settings()`（80-90）、`_key()`（92-93）、
  `probe_hardware()`（111-133）、`_build()`（163-193）、`_build_legacy()`（195-205）、
  `_activate()`（207-221）、`get_backend()`（225-246）、`reload()`（249-）
- `admin/routers/transcribe.py`：`_transcribe_sync()`（29-44）、`_run_backend()`（47-66）、
  `_timeout_seconds()`（69-71）、远程优先分支（90-100）
- `admin/routers/settings_local_runtime.py`：`GET /settings/local-runtime`（33）、
  `hardware`（38-40）、`PUT /settings/local-runtime/stt`（51-69）
- `admin/static/pages/local-model-runtime.html`、`admin/static/js/local-runtime.js`
- `core/stt_vocabulary.py`：`hotwords()`（46-50）、`correct()`（53-65）
- sherpa-onnx 官方 Python 文档与模型列表（见 H1）
- `docs/audio-perception.md`、`docs/dev-environment.md`、`docs/feature-control-surface.md`

### 改什么

#### H1 选模型（不要照搬输入法那一版）

1. **先查 sherpa-onnx 官方当前的中文/双语流式模型列表。**
   输入法用的是 **2023-02-20 的较早模型**，之后官方已发布更新的同类模型
   （streaming paraformer、更大的 zipformer 等）。它的选型理由是「手机能跑」，
   而后端跑在 PC CPU 上，预算完全不同——**优先选更新、更大的档位**。
2. 至少接入一个明确的推荐默认模型。**模型文件不进 git**（40-70MB onnx）：
   走首次使用时下载 + SHA-256 校验，落盘路径必须经 `core/sandbox.get_paths()`
   （`AGENTS.md` 规则 1）。输入法仓库的 `tools/voice_assets.json` 是可参照的清单写法。
3. 下载失败、文件缺失或校验不通过时 **fail-loud**，不要静默回落到 faster-whisper——
   用户显式选了这个引擎，静默换引擎会让「为什么还是很慢」变成无法排查的问题。

#### H2 把 backend 变成显式可选项

4. **`core/stt_local.py` 新增 `engine` 配置项**（`stt_local.engine`），
   枚举至少 `faster_whisper` / `sherpa_onnx`，默认值**保持 `faster_whisper`**
   （不改变现有部署的行为；用户自己在管理面切换）。
   - 加进 `validate()`（`:49-77`）做严格校验，沿用现有「坏配置降级默认值」的
     `settings()` 策略（`:80-90`）。
   - **`_key()`（`:92-93`）必须把 `engine` 纳入**，否则切换引擎不会触发重建，
     会继续用着上一个引擎的常驻实例。这是本单最容易漏的一处。
5. **参数按引擎分组校验。** 现有 `model_size`/`device`/`compute_type`/`beam_size`
   是 faster-whisper 的词汇表，sherpa-onnx 用的是另一套
   （模型目录、`num_threads`、`decoding_method`、端点检测参数等）。
   - **不要把两套参数硬塞进同一组扁平字段**。按引擎分块，
     `validate()` 只校验当前 `engine` 相关的那一组。
   - 切换引擎时另一组参数应当保留而不是被清空，方便来回切。
6. **新增 `_build_sherpa()`**，与 `_build()`（`:163-193`）/`_build_legacy()`（`:195-205`）
   并列，由 `_activate()`（`:207-221`）按 `engine` 分派。
   - 返回 dict 要与现有结构兼容：至少带 `backend`（用 `"sherpa_onnx"`）、`model`、
     `device`、`model_size`（或等价的模型标识）、`compute_type`、`fallback`，
     因为 `admin/routers/transcribe.py:41-44` 的埋点直接读这几个字段。
     **字段缺失会让埋点报 KeyError**。
   - 沿用现有的 smoke test 思路（`_smoke()`，`:146-152`）：加载成功不等于能推理，
     必须真跑一次极短推理再接受这个实例。
   - 未安装 `sherpa-onnx` 包时抛 `SttLocalError` 并给出可操作提示
     （参照 `_build_legacy()` 的 `"pip install ..."` 措辞）。
7. **`admin/routers/transcribe.py:47-66` 的 `_run_backend()` 加 sherpa 分支。**
   现在是 `if backend["backend"] == "faster_whisper": ... else: <openai-whisper>`，
   新引擎要显式分支，**不要落进 openai-whisper 的 else**。
   - 输出仍要经 `correct()`（`core/stt_vocabulary.py:53-65`）保持专有名词纠正一致。
   - 仍要保留 A 的回声剔除兜底（见下「边界」）。

#### H3 必须规避「单字重复」（用户已明确抱怨过）

8. **开端点检测。** 不要复刻输入法的 `enableEndpoint = false`（见「零点八」线索 1）。
   后端是整段音频离线转写，更应该按端点切分，而不是一个 stream 跑到底。
9. **不用纯贪心解码。** 输入法用的是 sherpa-onnx 默认 greedy search（线索 3）。
   本单改用 `modified_beam_search`（或当前版本的等价选项），并暴露为可配参数。
10. **加重复 token 后处理。** 这是最后一道防线（线索 2：输入法上层没做任何去重）。
    - 检测并折叠异常重复（同一字/词连续重复超过阈值）。
    - **阈值要留余地**：中文存在合法叠字（「好好」「慢慢」「看看」），
      不要把正常叠词也折叠掉。建议只处理连续 3 次以上的重复，
      并在报告里说明阈值选择依据。
    - 这个后处理函数应可单测，补最小单测覆盖「合法叠字不被折叠」与「异常重复被折叠」两类。

#### H4 专有名词偏置改走 sherpa-onnx 的机制

11. 现状：本地 faster-whisper 路径**已经在传 `hotwords()`**（`admin/routers/transcribe.py:52`）。
    sherpa-onnx 的对等能力是 **contextual biasing / hotwords**（需要在 recognizer 构造时
    提供热词列表与 boost 分数），机制与 Whisper 的 `initial_prompt` 完全不同。
12. 复用现有的 `core/stt_vocabulary.py:46-50` `hotwords()` 词表，**不要新造一套词表配置**。
    - sherpa-onnx 侧不要传整句中文模板（`prompt()`），它没有 `initial_prompt` 语义，
      传了也无意义——这顺带消灭了工单 A 那类回声的来源。
13. 若当前 sherpa-onnx 版本的 hotwords 接口需要特定格式（如分词后的 token 序列），
    在适配层转换，**不要改 `stt_vocabulary.py` 的词表数据结构**（远程路径还在用它）。

#### H5 管理面可选 + 观测

14. **管理面加引擎选择。** 现成页面 `admin/static/pages/local-model-runtime.html` +
    `admin/static/js/local-runtime.js`，经 `PUT /settings/local-runtime/stt`
    （`admin/routers/settings_local_runtime.py:51-69`，已是「先热切换、成功才落盘」）。
    - 引擎下拉 + 按引擎显示对应参数组（选 sherpa 时不该显示 `compute_type` 这种
      faster-whisper 专属项）。
    - `GET /settings/local-runtime`（`:33`）的 `options` 要带上引擎枚举
      （现在只给 `model_sizes`/`devices`/`compute_types`，见 `:27-30`）。
15. **硬件/依赖探测扩展。** `probe_hardware()`（`core/stt_local.py:111-133`）现在只探
    faster-whisper 与 CUDA。补上 sherpa-onnx 是否已安装、模型文件是否就位，
    让用户在切换前就知道缺什么，而不是切换失败才知道。
    **保持只读、不加载模型**的现有约定。
16. **埋点沿用现有的**（`admin/routers/transcribe.py:40-44` 已写 `api_call_log`），
    确认 `provider`/`model` 字段能区分两个引擎，便于在
    `GET /observability/api-calls` 里对比两者实际延迟。**不要新建端点。**
17. 静态资源改动按 `AGENTS.md`「Admin Static Asset Cache」更新 `?v=` 与
    `ADMIN_UI_FRAGMENT_VERSION`，并完成一次浏览器实测验收。

### 边界

- **默认值不变**：`engine` 默认 `faster_whisper`。本单交付的是「可以切换」，
  不是「已经换掉」——换不换由用户在管理面决定。
- **不删 faster-whisper 与 openai-whisper 两个现有后端。** 这是加法：
  `_build_legacy()` 的 openai-whisper 兼容路径保留原样。
- **不动远程 `stt_presets` 路径**（`core/audio_perception.py`）。
  注意 `admin/routers/transcribe.py:92` 的「配了 `stt_presets` 就优先走远程」逻辑不变——
  想用本地引擎的用户需要不配远程，这是既有行为，**本单不改它**，但要在文档里写明，
  否则用户会疑惑「我选了 sherpa 怎么没生效」。
- **不要把模型文件提交进仓库**，也不要放进 `data/` 之外的硬编码路径。
- **保留 A 的回声剔除兜底**。transducer 没有 `initial_prompt` 回声问题，
  但那道检测防的是通用的「提示被当成结果」，不要因为换引擎就删掉它。
- **不改 `core/stt_vocabulary.py` 的词表数据结构**（远程路径共用）。
- 环境约束按 `docs/dev-environment.md`：项目支持 Python 3.10-3.12（推荐 3.12）。
  **先确认 `sherpa-onnx` 在目标版本有可用 wheel**；若只有部分版本有，在文档里写清。
- **不做流式**。sherpa-onnx 支持 streaming，但后端当前是「整段音频 → 一次转写」的
  请求响应模型，接流式要改前端录音协议与三仓接口（见 B 的边界说明）。
  本单只用它做离线转写，流式留作后续。

### 验收

- 默认配置（未改 `engine`）下行为与改动前完全一致，faster-whisper 路径未退化。
- 管理面切到 sherpa-onnx、保存、实际发一条语音，确认走的是新引擎
  （从 `GET /observability/api-calls` 的 `provider`/`model` 字段确认，不靠猜）。
- **切换引擎后确认模型真的被重建**（验证 `_key()` 已含 `engine`）：
  连续切换 faster-whisper → sherpa → faster-whisper，每次都确认生效。
- 重启服务后确认引擎选择持久（配置已落盘）。
- **单字重复专项验收**：用会触发重复的音频（含静音、含糊、长句）各测几条，
  确认输出无异常重复；同时确认正常叠字（「好好」「慢慢」）**没有被后处理误折叠**。
  这是用户明确抱怨过的点，**必须单独验**。
- 延迟对比：记录同一批音频在两个引擎下的 `duration_ms`，给出实测数字。
  用户的诉求是「现在又慢又不准」，**报告里要给出实际改善幅度**。
- 专有名词：确认 hotwords 在 sherpa 路径下生效（词表里的词能被正确识别或纠正）。
- 依赖缺失路径：未装 `sherpa-onnx` 时切换该引擎，确认报出可操作的错误提示而非崩溃；
  模型文件缺失或校验失败时确认 fail-loud、不静默回落。
- 探测端点能显示 sherpa-onnx 的安装与模型就位状态。
- 补最小单测：重复 token 后处理函数（合法叠字 / 异常重复两类）、
  `validate()` 对新 `engine` 字段与分组参数的校验。
- 跑音频/STT 与 settings 相关既有回归。
- 浏览器实测：打开本地模型运行页，核对引擎下拉、参数分组显示、保存与回显。
  若浏览器被环境阻断，报告里明确写「浏览器实测未完成」并记录替代验证。
- 新增配置字段与用户可见行为，同步 `docs/audio-perception.md`、
  `docs/feature-control-surface.md`；在文档里写明「配了 `stt_presets` 时远程优先」
  这条既有优先级，避免用户误以为引擎选择没生效。

---

## 交付与提交

每张工单在相关测试通过、差异检查完成后立即独立提交，再开始下一张（`AGENTS.md`）。
只暂存本任务改动；注意工作区当前已有未提交改动（本组无关的 `AA3启动 - 副本.bat`、
`tests/local_runtime_browser.py`，以及同目录的另一组工单文档
`docs/work-orders/perception-prompt-and-presence.md`），**不要连带提交，也不要回滚**。
同一文件有他人改动时按 hunk 暂存。

冲突风险点：**A 与 B 都碰 `core/audio_perception.py`**（A 改 prompt 传参与结果检测，
B 加埋点），两张若并行需按 hunk 暂存，开工前先 `git diff` 看清边界。
**E 与 F 都碰日志路径**（E 改落盘与轮转，F 改业务侧日志级别）——E 先落地可避开。
**H 与 A 在专有名词偏置上有接口交集**：A 收拾远程路径的 `prompt()` 回声，
H 给新引擎接 hotwords/contextual biasing。两者改的是不同分支、不同文件，可并行；
但 **A 先落地更稳**（A 的回声兜底是 H 要保留的既有行为），H 开工前先确认 A 的检测位置。
H 不改远程路径，与 A 没有同文件冲突。
D 与上一组工单的 A（摄像头 prompt 专门化 + 放开 token 上限）**都碰
`core/video_call.py` 的 prompt 常量**——若上一组 A 尚未落地，D 开工前先确认当前 prompt 形态，
两者的「全量 prompt」应当是同一份。

提交信息附加：

```
Co-authored-by: Codex <codex@openai.com>
```

### 已拍板的决定（实现者按此执行，不要再问）

原先留给用户的四个取舍点已由用户授权代拍。**这些是决定，不是建议**：

1. **工单 A — 远程只支持 `prompt` 时怎么办**：
   **采用「传纯词表字符串」折中**，去掉 `以下是语音中的专有名词：` 这类自然语言前缀，
   只传逗号分隔的词表。
   理由：前缀本身就是被回声复述出来的那句话，去掉它即使仍被回声，
   吐出来的也只是一串词而不是一句可读的中文旁白，对用户的观感损害大幅下降。
   **但第 2 步的相似度兜底必须实现，不能因为前缀没了就省掉**——
   折中只降低概率，不消除机制。
   若供应商连纯词表都明显诱发回声，**直接关掉该路径的 prompt 注入**，
   专有名词完全交给 `correct()` 事后纠正；准确率让一点，不要让旁白漏进对话。

2. **工单 E — error.log 的归属**：
   **选 1a，保留独立台账 + 补齐轮转、上限与脱敏。**
   理由：`error.log` 是人直接 `tail` 看的纯文本，`runtime_warnings` 是结构化
   jsonl 台账，用途不同；合并会让「出事了先看哪个文件」这个最高频的排障动作
   变得更绕。缺的是轮转和脱敏，不是存在的必要性。
   轮转/上限/脱敏三项**直接复用 `runtime_warnings` 已有的实现**
   （同样的 rotation + 14 天保留 + 8MB/48MB 上限 + `RedactingFormatter`），
   不要新写一套参数体系。

3. **工单 E — 现存历史 error.log 的处置**：
   **归档压缩，不删不留原样。**
   重命名为带日期后缀的归档文件并 gzip，放在同一日志目录下，
   由新接入的保留策略按 14 天规则自然淘汰。
   理由：里面有本组工单引用过的历史证据（F 的降噪判断依赖它），
   现在删掉会让实现者无法复核；但 20MB 未脱敏明文也不该继续裸躺。
   **归档文件同样要过一遍脱敏**，不要只压缩了事。

4. **工单 D — 是否推进跨仓客户端差分**：
   **本阶段不推进，D 只做服务端差分。**
   理由：服务端差分的收益先要被实测验证，客户端差分要动 Emerald-client
   的采集链路与三仓协议，成本和回归面都大一个量级，不该在收益未知时先付。
   **但 D 的验收必须留下可判断的数字**（首帧与后续帧的 token 实测对比），
   这样后续要不要上客户端差分有依据而不是靠感觉。
   若 D 落地后实测节省明显不足预期，**另开工单**评估跨仓方案，不在 D 里扩张。



