# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start Here

Read `AGENTS.md` before every task — it maps task types to the specific doc you must read before touching code.

## Commands

```bash
# Run the bot (QQ + NapCat mode)
python main.py

# Run in standalone mode (HTTP only, no QQ)
# Set standalone_mode: true in config.yaml, then:
python main.py

# Test mode (data-isolated sandbox, won't touch production data/)
python run_test.py

# Run tests
pytest
pytest tests/test_short_term.py -v   # single file
python tests/run_eval.py             # validate prompt tag/layer activation after tag_rules changes
```

No linting or formatting tooling is configured.

## Architecture

QQ, desktop, and scheduler-triggered messages share one Pipeline. `core/pipeline_registry.py` exposes the instance to admin routes and post-process handlers; the scheduler also receives its own reference through `scheduler.set_pipeline()`.

```
QQ message      → main.py
Desktop message → admin/routers/chat.py
Scheduler       → core/scheduler/loop.py
                         ↓
                  core/pipeline.py
                         ↓
                  LLM (DeepSeek)
                         ↓
                  channels.registry broadcast
```

**Pipeline steps:**
1. **Pre-pipeline** (`main.py`): keyword fast path + lightweight LLM probe for `info`/`desktop` tools via `get_probe_prompt()`, topic tag extraction via `get_tags()`
2. **`fetch_context()`**: concurrently loads all memory layers
3. **`build_prompt()`**: assembles 12+ layer `messages[]` with tag gating; hard limit 20k chars triggers pruning (order: `event_search` → `mid_term` → `diary` → `episodic` → `lore`)
4. **`run_llm()`**: calls LLM with retry
5. **`post_process()`** (non-blocking `create_task`): critical path writes under `uid_lock`; slow-queue single-worker handles memory consolidation

**Five memory layers** (all under `data/`, all paths via `core/sandbox.get_paths()`):
| Layer | File/Dir | Update |
|---|---|---|
| Short-term | `history/{uid}.json` | Every turn (last 20 rounds) |
| Mid-term | `mid_term/{uid}.json` | LLM compression per turn (12h expiry, 3 time buckets) |
| Episodic | `episodic_memory/{uid}.json` | mid_term eager/sweep promotion, strength decay, max 200 |
| Character growth | `character_growth/角色_{uid}.md` + `.felt.md` + `.fingerprint.txt` | fixation pipeline consolidation |
| Event log | `event_log/{uid}/` | Every turn, daily files, 30-day search window |

**Memory consolidation** runs in the slow queue: `capture_turn → mid_term → episodic → character_growth`.

**Tool system**: Tools declared in `_TOOL_REGISTRY` in `core/tool_dispatcher.py`. `info`/`desktop` tools fire via pre-pipeline probe; reply-side desktop intent parsing runs after generation. `memory` tools are registered but are not currently exposed to the main generation call.

**Garden system**: `core/garden/manager.py` maintains five mood-mapped flower slots under `data/garden/`. `core/scheduler/triggers/garden_water.py` rolls automatic watering every 30 minutes, and `GET /garden/state` exposes read-only admin state. See `docs/garden.md`.

## Hard Rules

1. **All `data/` paths must go through `core/sandbox.get_paths()`** — never hardcode.
2. **New tools** must be registered in `_TOOL_REGISTRY` with `examples` and `keywords` fields.
3. **New prompt layers** must include a `_layer` field or the token pruning logic won't see them.
4. **After changing `tag_rules.py`** run `python tests/run_eval.py` to verify layer activation.
5. **Before touching assistant message write/truncate logic**, read `_sanitize_assistant_message()` in `core/memory/short_term.py` — bypassing it causes style feedback collapse.

## Doc Sync Hook

`.claude/hooks/` contains two hooks wired into Claude Code:
- **PostToolUse**: records every edited file to `.claude/.cache/edits_{session}.json`
- **Stop**: before ending a response, checks if any edited code file has a matching doc that wasn't also updated; blocks with a reminder if so

If you get blocked: either update the relevant doc, or explicitly state "no doc update needed: \<reason\>" and the next stop will pass.
