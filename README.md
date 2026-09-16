[English](README.md) | [简体中文](README.zh-CN.md)

# PresenceKit

A **single-user AI companion backend**. It owns persona, long-term memory, mood, tools, proactive contact, and an isolated dream runtime. A QQ bot is only one optional channel. [PresenceKit-desktop](https://github.com/cicikat/PresenceKit-desktop) and [PresenceKit-mobile](https://github.com/cicikat/PresenceKit-mobile) are thin clients: they render UI, capture sensors, and deliver messages. They **do not own** memory, persona, scheduling, or business data.

The current product train is **v1.1.0**. The desktop wire protocol remains frozen **v0.1** (`POST /desktop/chat` + `/ws/desktop`), not a new EventBus. Exact HTTP schemas live in the running `/openapi.json`. Topic docs under `docs/` are the product source of truth; root `ARCHITECTURE.md` / `DESIGN.md` / `AGENTS.md` are developer entry points.

---

## Three-repo relationship and ownership

```
PresenceKit (this repo — backend, sole business source of truth)
  ├── PresenceKit-desktop  Tauri desktop pet + admin-panel shell
  └── PresenceKit-mobile   Flutter Android client
```

| Question | Answer |
|---|---|
| Who stores memory, cards, dreams, garden, life records, tokens? | **Backend only** (`data/`, `userdata/`) |
| Who decides whether to speak unsolicited? | Backend `core/autonomy`; `talk_owner` is the only user-visible exit |
| Who renders bubbles, Live2D, notifications, heatmaps? | The matching client |
| Who captures screenshots, sensors, IME? | Clients capture; backend receives, judges, and may speak |
| Can the backend run alone? | Yes: admin-panel chat, QQ-only, or a single client |
| Can a client run alone? | No. There is no on-device persona/memory engine |

**Companion releases (this train)**

| Repo | Version | Notes |
|---|---|---|
| Backend PresenceKit | [v1.1.0](https://github.com/cicikat/PresenceKit/releases/tag/v1.1.0) | this repo |
| Desktop PresenceKit-desktop | [v1.0.1](https://github.com/cicikat/PresenceKit-desktop/releases/tag/v1.0.1) still works if desktop v1.1.0 is not out; frozen v0.1 protocol | render/ack on the client |
| Mobile PresenceKit-mobile | [v1.1.0](https://github.com/cicikat/PresenceKit-mobile/releases/tag/v1.1.0) already published | life-record UI, heatmap, dream chrome on the phone |

Cross-repo contracts and gaps: [docs/three-repo-doc-index.md](docs/three-repo-doc-index.md), [docs/three-repo-interface-catalog.md](docs/three-repo-interface-catalog.md). Feature flags, effective state, and scopes: [docs/feature-control-surface.md](docs/feature-control-surface.md).

---

## What this system is / is not

**Is**

- A single-owner local or protected-LAN companion runtime: one process, one `scheduler.owner_id`, one set of cards and memories.
- Two isolated realms: Reality writes memory; Dream uses a separate pipeline with only thin writeback (impressions / afterglow). Dreams must not be stored as real events.
- HTTP + WebSocket (default `http://127.0.0.1:8080`) plus optional NapCat / OneBot 11 QQ.
- The admin panel in this repo (`admin/static/`) is the operator, config, and observability surface.

**Is not**

- Multi-tenant SaaS, OAuth, or a public chatbot platform.
- A client-side LLM or client-side memory store.
- A guarantee that Android background delivery survives every OEM/Doze policy (relay is signal-only; bodies live on the poll queue).
- Automatic payment, automatic posting, or unbounded shell/filesystem power. Agent Runtime process/browser/workspace capabilities are bounded and off by default.
- A shipped unified EventBus or desktop WebSocket v1. Those remain historical / deferred: [docs/interaction-event-model.md](docs/interaction-event-model.md), [docs/v1-release-contract.md](docs/v1-release-contract.md).

Conservative defaults: scheduler, autonomy, MCP, hardware, IME, on-demand screenshots, character-readable life records, and Xiaohongshu reading are mostly **off**. Being able to chat does not mean the character will message first, capture the screen, or move hardware.

---

## What it can do now (by domain)

### Conversation and channels

- **Owner private chat**: QQ (optional), `POST /desktop/chat`, and `POST /mobile/chat` share `run_owner_chat_turn()` and the per-user lock in `core/conversation_gate.py`. Concurrent devices for the same owner do not enter `fetch_context → LLM → critical post-process` in parallel.
- **Scripts / hardware speaking as the owner**: `POST /v1/owner/turns` (`owner-input` profile, idempotent `client_turn_id`). See [docs/owner-turn-api.md](docs/owner-turn-api.md).
- **Proactive delivery**: desktop prefers `/ws/desktop` (file-queue fallback only for transient local failure; remote deploys do not write a local fallback); mobile uses durable `/mobile/poll` + `/mobile/ack`, optional ntfy/relay **signals only**; ESP32 uses `/ws/device` (no file fallback).
- **Cross-channel continuity**: switching channels injects a pickup hint. Canonical reply text is the memory source of truth; desktop `message_segments` is a narrative view only.
- **Media**: image recognition (named vision connections + OCR + phone override), upload ingest, optional STT (named remote connection or local Whisper), TTS (e.g. GPT-SoVITS), mood stickers (mutually exclusive with TTS; QQ image segment vs self-contained desktop/mobile sticker payload).
- **Thinking / monologue**: optional native-reasoning archive (not memory), prefixed monologue, character-voice style. Desktop can expand the bubble; mobile thinking UI is still roadmap. See [docs/thinking-voice.md](docs/thinking-voice.md), [docs/audio-perception.md](docs/audio-perception.md).

### Memory (backend-owned)

Parallel layers, not a single vector DB:

| Layer | Role |
|---|---|
| Short-term history | Sliding window; sanitized on read to avoid style collapse |
| Mid-term | ~12h compressed view, three time buckets |
| Episodic | Promoted from mid-term; strength decay, MMR, dedup |
| user_identity | Long-term read on the owner; capture → mid_term → episodic → identity |
| event_log | Daily ledger, keyword + intensity; salvage durable facts before expiry |
| Memory Event ledger | Reality dual-write evidence store; not in the prompt by default; Path C `search_events` tools when category `memory` is exposed |
| Vector store | Semantic recall; `web` sources are isolated like dreams and do not consolidate into identity |
| user_hidden_state | Hidden state + 12h decay / 7d baseline; Dream gets a read-only snapshot |
| storyline | Append-only narrative arcs + weekly aggregation; eviction is demotion, not hard delete |
| Life records | Separate SQLite; phone sync; character read requires a switch; no automatic long-term memory writes |
| LLM reasoning archive | Provider thinking stored separately, admin-only read, never returned to the prompt |

Forgetting is demotion / tombstone, not casual physical deletion of evidence. Concurrency: `uid_lock` + global mood lock + atomic writes. Details: [docs/memory.md](docs/memory.md), [docs/data-taxonomy.md](docs/data-taxonomy.md), [docs/vector-store.md](docs/vector-store.md), [docs/life-records.md](docs/life-records.md).

### Prompt and models

- Layered prompts with tag gating and quality-graded token trim. Reality layers include persona, source boundary, time, presence, lorebook, profile, mood, episodic/mid-term/events, material continuity `10.6–10.8`, action traces, rotating Author's Note. Canonical table: [docs/prompt-layers.md](docs/prompt-layers.md).
- Multi-preset routing: DeepSeek / OpenAI Chat Completions / Responses / Anthropic-compatible / local. Profiles map `chat` / `probe` / `intent` / `sensor_judge` / `ime_judge` / `monologue` / `rpg_kp` / `consolidation` and more. A character card may bind a whole profile. See [docs/model-presets.md](docs/model-presets.md).
- Bring your own API key. Embedding is optional; missing embedding falls back to keyword recall.

### Tools, MCP, Agent Runtime

- Every tool must be in `_TOOL_REGISTRY` and pass `execute(origin=...)`. Unknown origin fails closed.
- **Path A**: keyword fast-path + LLM probe (`info`/`desktop`). Ordinary probes are skipped when Path C is active.
- **Path C tool loop**: globally off by default; per-card `presence_ext.tool_loop` can override. Function-calling loop loads schemas by authorized category discovery.
- Example categories: time/weather/reminders, web search, diary/toy files, memory, life records, garden, desktop window actions, screen observation, Xiaohongshu read-only, sandboxed chat artifacts, hardware (frozen unless opted in).
- **MCP**: optional external tool transport, **not** the client protocol; off by default; local allowlist required. Experimental; must not block chat.
- **Danger mode**: `PATCH /system/meta-mode`. Shutdown/sleep still need a second confirmation. Mobile tokens **do not** include `hardware` or `admin`.
- **Agent Runtime** (Reality): durable tasks, bounded work sessions, controlled workspace, process runner, isolated browser worker. Leftover `running` records become `outcome_unknown` at startup and are not auto-replayed. Dream does not share this runtime. See [docs/agent-runtime-architecture.md](docs/agent-runtime-architecture.md), [docs/tools.md](docs/tools.md).

### Proactivity (scheduler + autonomy)

Scheduler and sensors emit **fact signals**, not prose. One tick merges into `autonomy-opportunity.v1`; `core/autonomy` evaluates; only an explicit `talk_owner` enters `turn_sink`. Legacy direct-send paths are retired. See [docs/autonomy.md](docs/autonomy.md), [docs/scheduler.md](docs/scheduler.md).

Candidate facts include morning/night/daytime chatter, weather, diary, multi-stage birthday, unfinished topics, proactive recall, holidays/time nodes, heart-rate/sleep, dream-exit greeting, garden events, IME activity, desktop reopen (`POST /desktop/wake` Path B enqueues a signal only), overflow, and letters. Maintenance jobs (decay, janitor, event salvage, hidden-state) stay silent. DND, active-user, Dream, budget, and the conversation lock can all suppress speech. High urgency (birthday / period / heart-rate) does **not** bypass autonomy gates.

### Dream, group chat, activities

- **Dream**: isolated D0–D10 stack; sandbox / scenario / mirror; soft exit + unstoppable hard exit. RPG Dream backend API exists (`dream_mode=rpg`); desktop dual-pane UI / mobile consumption are not done. Group Dream Stage is sandbox-only, zero writeback, absolute hard_exit.
- **Reality Stage**: multi-character group chat with rule arbitration (phases A/B/R/T), one owner lock, no background spontaneous LLM.
- **ActivitySession**: reading / gomoku / chess / dream_seed; explicit API lifecycle; not written to short-term memory.
- **Coplay**: desktop “watch me play” (observer, not input injector); session compresses to `game_log`, not the main memory chain.
- **Garden**: five mood plots, auto/tool watering; state is not injected into the prompt; events may become opportunities. Clients are read-only.

### Perception and the outside world

- Phone sensors, Watch heart-rate/sleep, desktop activity snapshots (TTL), on-demand screenshots (dual gate: backend flag + device consent, off by default).
- IME draft inbox: received ≠ read ≠ will speak.
- Obsidian journal, SMTP letters, spend-balance observation only (never auto-pay).
- External Companion: `POST /integrations/companion/events` (e.g. in-game invite / decaying drinking observation); backend decides whether to talk.
- Optional Xiaohongshu share reader (optional locally hosted reader service).
- Wake Bridge: durable inbox for forum-like sources, then the existing proactive chain.

### Admin panel

Open the backend in a browser. Surfaces include first-run setup, character cards, model routing, feature flags, scheduler/autonomy budget, dream settings, MCP, tokens, life records, and an observation center (memory, dream, tools, API ledger, runtime signals, agent browser, …). The secrets-book shortcut works on loopback only.

---

## Architecture and execution chain

```
QQ / NapCat                 → main.py → message_queue
Desktop POST /desktop/chat  ─┐
Mobile POST /mobile/chat    ─┼→ conversation_gate → Pipeline
Owner Turn API              ─┘
Scheduler / sensors         → autonomy-signal → opportunity → talk_owner? → turn_sink
Dream                       → isolated dream pipeline (no reality memory, no scheduler)
```

**Reality pipeline (`core/pipeline.py`)**

0. Probe / Path C category discovery  
1. `fetch_context()` concurrently loads memory, relations, lorebook, diary, vectors, …  
2. `prompt_builder.build()` with tag gating  
3. Main generation: tool loop or a single `llm_client.chat`  
4. `turn_sink`: millisecond local writes before send (history/event_log); mood, consolidation, TTS after send  

Output fans out through `channels.registry.broadcast()` or the HTTP response. QQ visible send uses the OneBot adapter; memory still goes through `record_assistant_turn()`.

Startup: [docs/runtime-lifecycle.md](docs/runtime-lifecycle.md) — load config → verify auth → load character → Pipeline → recover Agent tasks (stale `running` → unknown) → HTTP.

Data paths: `core/data_paths.py` + `sandbox.get_paths()`; **do not hardcode `data/`**. Private authored assets live under `userdata/characters/`; release-owned seeds under `bundled/`.

---

## Backend vs clients

| Capability | Backend | Desktop | Mobile |
|---|---|---|---|
| Persona / prompt / memory writes | Authority | Renders history | Renders history |
| Whether to speak unsolicited | Authority | Render / notify | Poll + notify; relay is wake-only |
| Window minimize and other actions | Emits allowlisted actions | Executes and acks (protocol v0.1) | None |
| Intiface hardware | Gates + tools | May hold `hardware` | **No `hardware` scope** |
| Screenshots / foreground observation | Policy, cooldown, injection | Capture + local consent | Revocable system setting |
| Life-record capture UI | Store / recognize / permission | Admin config | v1.1.0 capture/sync UI |
| Chat heatmap | `/chat-log/stats/calendar` | Roadmap | v1.1.0 profile page |
| Dream HUD / chrome | Dream API / state | HUD consuming backend | Background / type / wake confirm |
| Group Stage | Backend session | If the client wired it | Mobile group entry removed |
| Admin config / tokens | Authority | May embed the panel | Connects only; no admin |
| ESP32 firmware | `/ws/device` | — | — |

---

## Auth and capability envelope

Single owner, opaque Bearer tokens, **default-deny**. Implementation: [docs/security.md](docs/security.md). Threat model: [docs/security_model.md](docs/security_model.md).

**Scopes**: `admin` (superset), `chat`, `state.read`, `memory.read`, `sensor.write`, `integration.write`, `companion.write`, `diary.sync`, `life_records`, `activity`, `persona`, `hardware`, `ws.desktop`, `ws.device`.

**Typical profiles from `scripts/setup_auth.py`**

| Profile | Holder | Intentionally missing |
|---|---|---|
| `panel` | Admin web UI | — (admin) |
| `desktop` | Desktop pet | admin |
| `mobile` | Phone | hardware, admin, ws.desktop |
| `watch` | Watch shortcut | everything except sensor.write |
| `device` | ESP32 | everything except ws.device |
| `owner-input` | Scripts/hardware as owner | chat only |

Plaintext tokens appear only at create/rotate. 401 = unknown token, 403 = insufficient scope, 429 = 401 rate limit (in-memory; restart clears). Do not put the admin secret on the phone.

The LLM cannot execute system capabilities directly. Tools are further constrained by character permissions, category exposure, danger mode, confirmation, origin, and Dream/group exclusion rules.

---

## Configure, start, use

### Environment

- Python **3.10–3.12** (3.12 recommended; 3.13+ blocked by `rapidocr-onnxruntime`)
- Your own chat-model API; optional embedding, TTS, STT, NapCat, Docker (local Xiaohongshu reader)
- Default bind `127.0.0.1:8080`. LAN/remote access needs your own HTTPS reverse proxy. Do not expose plain HTTP plus the break-glass secret to the public internet.

### Windows zip / source shortcuts

1. `AA1安装并启动.bat` — uv installs Python 3.12, `.venv`, syncs `requirements.lock`; copies `config.example.yaml` → `config.yaml` if missing.
2. `AA2鉴权初始化.bat` — `python scripts/setup_auth.py`, writes gitignored `secrets.local.yaml`.
3. `AA3启动.bat` — `python main.py`. Set `standalone_mode: true` if you are not using QQ.
4. Open the panel with `admin_secret`. Fill the base chat model and `owner_id` (use your QQ number if you have one; a different id later starts a separate memory thread).
5. Create a real character card (bundled `default` is a placeholder named “角色名”).
6. Send the first message from the panel, desktop pet, or phone.

Updates: source trees use `AA更新.bat` (stop the service first). Unpacked release zips use the bundled updater: it replaces program files only and keeps `data/`, `userdata/`, `config.yaml`, `secrets.local.yaml`, `.venv/`, and `tools/uv*`. Automatic forward updates start at v1.0.0; v0.x requires backup + fresh install. See [docs/backend-upgrade-recovery.md](docs/backend-upgrade-recovery.md).

### macOS (arm64 / x64 release zips)

The zip includes the matching `tools/uv` binary. Unpack into an empty directory:

```bash
chmod +x tools/uv
tools/uv python install 3.12
tools/uv venv --python 3.12 .venv
tools/uv pip sync requirements.lock --python .venv/bin/python
cp config.example.yaml config.yaml
.venv/bin/python scripts/setup_auth.py
# edit config.yaml: standalone_mode: true if you skip QQ
.venv/bin/python main.py
```

If desktop/mobile should reach `http://<Mac-LAN-IP>:8080`, change `admin.host` to a reachable address and use scoped tokens. Prefer a reverse proxy; do not bind the raw admin port to the public internet.

### Source install (any platform)

```bash
git clone https://github.com/cicikat/PresenceKit.git
cd PresenceKit
pip install -r requirements.txt   # or: uv pip sync requirements.lock
cp config.example.yaml config.yaml
python scripts/setup_auth.py
python main.py
```

`config.yaml` is the only runtime config; the admin panel writes the same file. `config.example.yaml` is the commented template; key sets stay in sync via `scripts/gen_config_example.py`. Isolated tests: `python run_test.py` (writes under `data/test_sandbox/`).

Live character cards: `userdata/characters/cards/` (`.json` / `.txt` / `.md`). Template: `bundled/templates/character_template.json`. Private lore/dream assets live under `userdata/characters/`.

### Connecting clients

- **Desktop**: keep the backend running; URL + desktop token. Unsigned installer triggers SmartScreen. Protocol authority: desktop repo `docs/protocol-v0.md`.
- **Mobile**: LAN IP or `adb reverse`; mobile token. Foreground `/mobile/chat`; background poll/ack; optional `relay_*`.
- **QQ**: NapCat WebSocket (example port 3001) + `qq.enabled` + not standalone. Changing the QQ flag requires a **restart**.
- **Watch**: Shortcut `POST /watch/event` with a `watch` token.
- **ESP32**: flash `firmware/presence-device/`, connect `/ws/device` with a device token.

Cold start: [docs/v1-cold-start-single-user-deployment.md](docs/v1-cold-start-single-user-deployment.md). Token rotation: [docs/token-rotation.md](docs/token-rotation.md).

---

## Deployment shapes

| Shape | Notes |
|---|---|
| Local desktop pet | `standalone_mode: true`, bind loopback |
| Local + QQ | NapCat on the same machine, then the backend |
| Home server | Ubuntu systemd example in `docs/user-teach/ubuntu-single-user-deployment.md`; private network (e.g. Tailscale); do not publish 8080 |
| Backup | `python main.py backup-state create --output <protected-volume>`; restore does not swap the live directory by itself |

---

## Tests and CI

```bash
pytest                          # task-scoped is enough; full suite: pytest -n auto
python tests/run_eval.py        # after tag_rules changes
python tests/run_identity_eval.py
```

GitHub `tests.yml`: Python 3.10/3.12 smoke plus full pytest on `main` (public `config.example.yaml`, no private cards). Treat CI red as a release blocker.

---

## Docs map

| Doc | Contents |
|---|---|
| [docs/README.md](docs/README.md) | Backend docs entry |
| [docs/three-repo-doc-index.md](docs/three-repo-doc-index.md) | Cross-repo index by feature |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Overview and pipeline |
| [docs/channels.md](docs/channels.md) | QQ / desktop / mobile / device |
| [docs/api-reference.md](docs/api-reference.md) | HTTP/WS families |
| [docs/memory.md](docs/memory.md) | Memory |
| [docs/prompt-layers.md](docs/prompt-layers.md) | Prompt layers |
| [docs/tools.md](docs/tools.md) | Tools / MCP |
| [docs/scheduler.md](docs/scheduler.md) / [docs/autonomy.md](docs/autonomy.md) | Proactivity |
| [docs/dream.md](docs/dream.md) / [docs/stage.md](docs/stage.md) | Dream and group chat |
| [docs/security.md](docs/security.md) | Tokens / scopes |
| [docs/feature-control-surface.md](docs/feature-control-surface.md) | Flags and effective state |
| [docs/known-issues.md](docs/known-issues.md) | open / observe |
| [docs/v1-release-contract.md](docs/v1-release-contract.md) | v1 guarantees vs experimental |
| [docs/release-guide.md](docs/release-guide.md) | How to cut a release |

Dated investigation snapshots are not runtime truth.

---

## Notes

- Licensed under PolyForm Noncommercial 1.0.0: noncommercial use allowed; commercial use needs a separate grant.
- Bring your own models and character cards. This repo ships no copyrighted character assets.
- Do not commit real secrets, QQ numbers, phone numbers, or machine-absolute paths.
- Some compatibility paths may still mention historical `yexuan` field names; that is not a product binding to that character.

## Contributors

- cicikat — project author
- Codex — coding agent
- Claude Code — coding agent
- Grok — coding agent

## License

PolyForm Noncommercial License 1.0.0.
