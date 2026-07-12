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

# Run tests (ALWAYS parallel — see Testing rules below)
pytest -n auto
pytest --testmon                     # partial changes: only affected tests
pytest tests/test_short_term.py -v   # single file
python tests/run_eval.py             # validate prompt tag/layer activation after tag_rules changes
```

No linting or formatting tooling is configured.

## Testing Rules

1. **NEVER run bare `pytest`** (full suite, single process). Always use `pytest -n auto`.
2. When only part of the code changed, prefer `pytest --testmon` or pass specific test paths instead of the full suite.

## Architecture

QQ, desktop, and scheduler-triggered messages share one Pipeline. `core/pipeline_registry.py` is the single owner; admin routes, post-process handlers, and the scheduler all read from it via `pipeline_registry.get()`. `main.py` registers it directly via `pipeline_registry.register()` (the old `scheduler.set_pipeline()` compat shim was removed in Brief 35).

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

> S6 layout (current, `_LAYOUT_REALITY="v1"`): per-user files live under
> `data/runtime/memory/{char_id}/{uid}/`. Legacy paths (`history/{uid}.json` etc.) were
> the pre-S6 layout; they are **migrated / historical** and must not be used in new code.

| Layer | File/Dir (S6 current) | Update |
|---|---|---|
| Short-term | `data/runtime/memory/{char_id}/{uid}/history.json` | Every turn (last 20 rounds) |
| Mid-term | `data/runtime/memory/{char_id}/{uid}/mid_term.json` | LLM compression per turn (12h expiry, 3 time buckets) |
| Episodic | `data/runtime/memory/{char_id}/{uid}/episodic.json` | mid_term eager/sweep promotion, strength decay, max 200 |
| User identity | `data/runtime/memory/{char_id}/{uid}/identity.yaml` | fixation pipeline consolidation (active long-term writer; `character_growth` module + `get_growth` tool removed entirely in Brief 35 — zero other readers of `character_growth.load()` confirmed) |
| Event log | `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md` | Every turn, daily files, 30-day search window |

**Memory consolidation** runs in the slow queue: `capture_turn → mid_term → episodic → consolidate_to_identity`.

**Tool system**: Tools declared in `_TOOL_REGISTRY` in `core/tool_dispatcher.py`. `info`/`desktop` tools fire via pre-pipeline probe; reply-side desktop intent parsing runs after generation. `memory` tools are registered but are not currently exposed to the main generation call.

**Garden system**: `core/garden/manager.py` maintains five mood-mapped flower slots under `data/garden/`. `garden_water` rolls automatic watering every 30 minutes, `garden_daily` scans harvest/vase state, `water_garden` handles user-prompted watering through the info-tool probe, and `GET /garden/state` exposes admin state. See `docs/garden.md`.

## 协作偏好

1. **用中文回复。**
2. **默认自主推进、替用户拍板**,不要逐项确认;只在不可逆决策(删数据、改契约、对外发布)时提问。
3. **不要全仓 grep**,先按 AGENTS.md / 架构文档定位到具体文件再精准搜索。
4. **交付物一次性批量输出**,多个工单/提示词要标注哪些可并行、哪些有前置依赖,减少一来一回。
5. **每张工单完成必须 commit，无需确认**：验收通过后立即按该工单的任务归属执行 `git add` + `git commit`（信息一行即可），不得以“用户没说 commit”为由跳过；多张工单必须分别提交。工作区有并行改动时，只暂存本工单文件，绝不混入他人的改动。当场固化，不留过夜、不攒大坨。这是预先授权，覆盖“仅在用户明确要求时才 commit”的默认行为。

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
