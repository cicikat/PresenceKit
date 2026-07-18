[English](README.md) | [简体中文](README.zh-CN.md)

# PresenceKit

An AI companion backend with long-term memory, emotional state, and the ability to reach out to you first. A QQ bot is just one of several optional channels — the core is a persona/memory/scheduling engine you can talk to over HTTP/WebSocket from any client.

---

## Repo relationship

```
PresenceKit (this repo, backend)
  ├── PresenceKit-desktop  Tauri desktop pet + admin panel client
  └── PresenceKit-mobile   Flutter mobile client
```

The backend is the single source of truth: long-term memory, emotional state, the proactive scheduler, the tool system, and the persona all live here. Desktop and mobile are **thin clients** — they render UI and forward user input, they don't own any business data. All three talk over HTTP/WebSocket; you can run the backend with only one client connected, both, or neither (pure QQ-bot mode).

---

## Features

### Memory system (five layers, running in parallel)

- **Short-term history**: sliding window of the last 20 turns, sanitized on read to avoid style self-feedback collapse
- **Mid-term summary**: compressed view of the last 12 hours, rendered into three time buckets (just now / a few hours ago / earlier), LLM-compressed with a rule-based fallback
- **Episodic memory**: structured fragments promoted from mid-term via eager/sweep, with strength decay, MMR diversity recall, and emotion-texture dedup
- **Stable behavior patterns** (user_identity): the character's long-term read on you, driven by a four-stage consolidation pipeline (capture → midterm → episodic → identity); survives restarts
- **Event log**: daily-sharded, keyword-searchable, with intensity decay scoring — low-intensity entries older than 7 days are skipped automatically

`character_growth` is kept as legacy-compatible data but is no longer the primary long-term context source for the live prompt.

### Emotional state system

- After every turn, an LLM detects the character's reply emotion and writes it to `mood_state`
- Drift formula: `new_intensity = old_intensity × 0.7 + new_emotion_intensity × 0.3`; a state switch needs two consecutive confirming turns
- The current emotional undertone is injected into the prompt as a soft hint, described at three intensity tiers
- Emotion feeds back into episodic recall scoring — memories that get recalled more often become more durable

### Prompt architecture (12+ layers)

- Layered prompt construction with tag gating, token estimation, and quality-graded trimming
- Lorebook / character card / user profile / realtime state / emotional undertone / episodic memory / mid-term summary / activity state / character diary / rotating Author's Note
- Probe mechanism: a keyword fast-path plus a minimal LLM probe runs ahead of the main turn to pre-判断 info/desktop tool calls
- Layer 11 (Author's Note): rotating personality traits plus corrective injection appended when `consistency_check` flags an issue
- When the token budget is exceeded, layers are trimmed lowest-quality-first

### Dream system

- An isolated Dream Session pipeline: doesn't enter the waking-conversation post-process, doesn't write to waking history/memory, doesn't trigger the scheduler
- A snapshot of the waking context is frozen on dream entry; dreams run their own D0–D10 prompt layer stack, world pack, and lorebook
- Supports a soft exit and an unstoppable hard exit; on exit the raw dream text is archived and a low-weight dream impression is distilled
- The waking prompt only ever receives the scene-stripped `6g_dream_impression`, so dream content can't be misremembered as reality

### Proactive trigger scheduler

- Good morning / good night / random daytime small talk (drawn from past messages carrying emotional words)
- Weather-linked messages, a daily diary entry the character writes itself, natural memory decay
- Multi-stage birthday triggers: night-before warmup, midnight greeting, afternoon check-in, evening wind-down
- Follow-ups on unfinished topics, proactive-recall triggers
- Holiday awareness, calendar-moment awareness, faster cadence over long holidays
- Periodic episodic-memory sweep/promotion (30-minute cooldown)
- Do-not-disturb module (implemented, pluggable)
- High-priority triggers (birthday / period / heart-rate alert) force-send even while the do-not-disturb window is active
- Cooldown state is persisted and survives restarts

### Emotional garden

- The character has its own flower plot: auto-watering, user-prompted watering, blooming, and post-harvest handling
- Garden state is exposed to the admin panel; key events can feed into the proactive scheduler

### Real-world data awareness

- **Apple Watch**: abnormal heart-rate alerts (low priority above 100bpm, high priority above 120bpm), sleep awareness and reports (pushed via an iPhone Shortcut)
- **Obsidian journal**: read by date, keyword-searchable over the last 30 days, marked as shared once read
- **Menstrual cycle awareness**: tag-gated during and near the cycle, auto-injects a care layer
- **Phone sensors**: steps / battery / location / screen-on count, injected whenever same-day data exists
- **Desktop pet screen-activity snapshot**: 5-minute TTL, injected on tag match

### Conversation capabilities

- Image recognition (GLM / Gemini / OpenAI Vision)
- TTS speech synthesis (GPT-SoVITS, reference audio switches with emotion)
- Sticker sending (emotion-linked, mutually exclusive with TTS)
- Tool calls: weather lookup, reminders, web search (DuckDuckGo), desktop control; memory-category tools are registered but not yet wired into automatic main-LLM tool calling
- Desktop intent parsing: the character saying "let me close that game for you" actually minimizes the window
- Three channels — QQ, desktop pet, mobile polling — with WebSocket preferred for proactive desktop pushes and a file-queue fallback
- The desktop WebSocket supports a segmented `message_segments` narrative view; the raw reply remains the source of truth for memory
- Cross-channel continuity awareness — switching channels injects a pick-up-where-we-left-off hint

### Engineering quality

- Data paths are unified through `core/data_paths.py`, governance metadata is registered via `core/data_registry.py`, `core/sandbox.py` provides the singleton glue, and `core/migration.py` handles compatibility reads during migrations
- Test mode redirects all data writes to `data/test_sandbox/{session_id}/`, keeping production data untouched
- Atomic writes (`safe_write`, cross-platform `os.replace`)
- LLM output validation with up to 3 retries; on failure, old data is preserved
- Post-process is split into a critical path (lock-holding) and a slow queue (single worker, backoff retry), avoiding lock starvation
- Failed slow tasks go to a dead-letter queue (DLQ), monitored periodically by the scheduler
- Concurrency protection: per-uid locks plus a global emotional-state lock

---

## Tech stack

Python · FastAPI · NapCat (OneBot 11, optional) · DeepSeek / any OpenAI-compatible LLM API · GPT-SoVITS (optional)

---

## Quickstart

**Requirements**

- Python 3.10–3.12 (3.12 recommended; 3.13+ not yet supported — `rapidocr-onnxruntime`
  caps at `<3.13`)

**Install**

```bash
git clone https://github.com/cicikat/PresenceKit.git
cd PresenceKit
pip install -r requirements.txt
```

**Windows shortcut**: instead of the manual steps below, double-click
`AA1安装并启动.bat` (installs deps, generates `config.yaml`), fill in
`config.yaml`, then `AA2鉴权初始化.bat` (auth init, required before the
first run) and `AA3启动.bat` (start). `AA2` finishes by auto-opening the
secrets file and the admin panel — the panel lands on the "配置"
(Setup) page on first launch; fill in the two red required fields
(① base chat model, ② `owner_id`) and you're ready to chat.
`AA更新.bat` does `git pull` + reinstall deps for later updates.

**Configure**

```bash
cp config.example.yaml config.yaml
```

Fill in the required fields per the comments in `config.example.yaml`: your LLM API key, an admin-panel secret, and `scheduler.owner_id`; add a QQ number only if you're using the QQ bot. You can also skip editing the yaml directly and fill both required fields from the admin panel's "配置" (Setup) page.

For `owner_id`, use your QQ number if you have one — using a different id here means connecting QQ later will start a separate memory thread that won't merge with the desktop-pet memories. Leaving it empty makes the proactive-message scheduler silently skip all triggers.

Drop character card files into `characters/`; the loader currently supports `.json`, `.txt`, and `.md` — see `examples/character_template.json`. The repo ships a neutral `default` character card that works out of the box.

**Initialize auth** (before the first run)

```bash
python scripts/setup_auth.py
```

This generates the admin-panel secret and per-device tokens, and writes them to a local secrets file `secrets.local.yaml` (already gitignored). See [docs/token-rotation.md](docs/token-rotation.md).

**Run**

```bash
# Using only the desktop pet or mobile client? Set standalone_mode: true in config.yaml to skip NapCat.
python main.py
```

To use the QQ bot: start NapCat first, make sure QQ is logged in and its WebSocket server is listening on port 3001, then run `python main.py`.

Test mode redirects all writes to an isolated sandbox, leaving production data untouched:

```bash
python run_test.py
```

Admin panel: `http://127.0.0.1:8080`

---

## Optional integrations

- **QQ / NapCat**: see "Run" above; skip it entirely with `standalone_mode: true` if you don't need the QQ bot.
- **Desktop client**: [PresenceKit-desktop](https://github.com/cicikat/PresenceKit-desktop) — requires the backend to be running.
- **Mobile client**: [PresenceKit-mobile](https://github.com/cicikat/PresenceKit-mobile) — connects over a LAN IP or `adb reverse`.
- **TTS**: GPT-SoVITS; see the TTS-related fields in `config.example.yaml`.
- **Apple Watch**: push heart-rate/sleep data to the backend via an iPhone Shortcut; see the relevant fields in `config.example.yaml` and [docs/known-issues.md](docs/known-issues.md).

---

## Testing

```bash
pytest
python run_test.py
```

If you touch `tag_rules`-related logic, also run the eval suite:

```bash
python tests/run_eval.py
```

---

## Docs

| Doc | Content |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture overview, the four-stage pipeline, data directory layout |
| [docs/memory.md](docs/memory.md) | The five-layer memory subsystem design and concurrency protection |
| [docs/prompt-layers.md](docs/prompt-layers.md) | Prompt layer structure, tag gating, token trimming |
| [docs/tools.md](docs/tools.md) | Tool system, probe mechanism, desktop action execution |
| [docs/scheduler.md](docs/scheduler.md) | Full list of scheduler triggers and their cooldown design |
| [docs/channels.md](docs/channels.md) | QQ / desktop-pet channels, WebSocket, file fallback, cross-channel continuity |
| [docs/garden.md](docs/garden.md) | Emotional garden, auto/manual watering, post-harvest handling, admin panel state API |
| [docs/dream.md](docs/dream.md) | Dream Session isolation boundary, independent prompt stack, world pack and impression writeback |
| [docs/data-taxonomy.md](docs/data-taxonomy.md) | Current datapath layout, governance metadata, migration-era compatibility reads |
| [docs/assistant-turn-sink.md](docs/assistant-turn-sink.md) | Unified assistant-turn writes, broadcast, and the narrative-segment protocol |
| [docs/security_model.md](docs/security_model.md) | Admin panel, desktop-pet WebSocket, and client-secret boundaries |
| [docs/security.md](docs/security.md) | Auth model (scoped tokens): scope/profile tables, token management API |
| [docs/token-rotation.md](docs/token-rotation.md) | First-time setup, per-device token rotation commands, 401/403/429 troubleshooting |
| [docs/fresh-clone-testing.md](docs/fresh-clone-testing.md) | How to correctly test a fresh clone (avoid connecting to a stale backend process/data) |
| [docs/known-issues.md](docs/known-issues.md) | Current tech debt and verified fixes |

---

## Notes

- Personal/learning use only.
- Bring your own LLM API key (DeepSeek is recommended if you're in mainland China — direct connect, no proxy needed).
- Bring your own character card; see `characters/` for the format. This project ships no copyrighted character material.
- The project uses "他" (a male original character) as its example persona. The display name is configurable via `character.name` in `config.yaml`; some defaults, compatibility paths, and older docs still say `yexuan` internally — this doesn't affect functionality, and will be unified in a later version.

---

## License

This project is licensed under the PolyForm Noncommercial License 1.0.0.

Noncommercial use is permitted. Commercial use is not permitted without separate permission from the author.
