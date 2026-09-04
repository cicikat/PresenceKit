# Agent Runtime Architecture Contract (Brief 229)

> Status: frozen architecture target with Briefs 230-233 implemented. Client protocols,
> process/browser capabilities, visible completion notification, and EventBus remain unchanged.

## Scope and non-goals

The future Agent Runtime is the single architectural home for durable character work: task
lifecycle, bounded Agent work sessions, explicit capability adapters, recovery, and optional owner
notification. Existing code remains authoritative until the later briefs land:

- `EventContext` identifies one accepted Reality ingress and turn/evidence chain. It is not a task
  envelope, universal event envelope, dispatcher, or EventBus.
- `event_context_observer` remains a content-free soak observer. `enforcing` retains its existing
  readiness gate; Runtime must not treat it as globally enforcing.
- `core.autonomy` owns proactive signals, opportunities, short-lived evaluator jobs/leases and runs.
  Those records are not a general Task Store.
- `TRIGGER_MIGRATION_STATUS` is scheduler lifecycle authority: migrated sources emit signals,
  maintenance sources stay silent, and retired executors remain unavailable.
- `_TOOL_REGISTRY` plus `execute(origin=...)` remains the current tool boundary.

There is no universal event bus, `kind=task/tool/activity`, arbitrary filesystem or shell authority,
new client protocol, or Reality/Dream shared runtime in this brief.

## Implemented Task Manager foundation (Brief 230)

`core/agent_runtime/task_manager.py` now owns the Reality-only durable lifecycle. Records are scoped
by `uid + char_id + realm`, created idempotently, written atomically, and use the states `created`,
`queued`, `running`, `succeeded`, `failed`, `canceled`, `expired`, and `outcome_unknown`. Claims carry
an attempt ID and expiring lease. Retry defaults to `never`; callers must explicitly declare a safe
retry policy and bounded attempt count.

Startup recovery scans the separate `runtime/agent_runtime/reality` root before workers or scheduler
producers may claim work. Any `running` record owned by a prior process becomes `outcome_unknown` and
is never automatically replayed. Dream principals are rejected before storage access; a future Dream
runtime must add its own root, manager, worker pool, and capability allowlist.

Receipts and `GET /observability/agent-runtime-tasks` expose metadata only. Raw idempotency keys and
causation IDs are persisted only as digests; lease tokens, user IDs, content, prompts, full paths, and
raw tool output are excluded from observation. Task lifecycle code imports neither `capture_turn()`
nor Memory Event writers. A future visible completion notification must create a fresh Reality
`EventContext` through the existing ingress adapter and use only the bounded causation reference.

## Planes and ownership

```text
Clock / Trigger -> due facts, signals, task registration (never prose)
       -> Task -> durable lifecycle, lease, TTL, cancel, retry, receipt
       -> Agent -> bounded non-chat LLM work session and authored output
       -> Capability -> manifest, grant, policy, resource limits, adapter
       -> Interaction -> new Reality ingress/turn and turn_sink only for visible notification

Dream Runtime = separate realm, stores, workers, manifests, and capability set
```

| Plane | Owns | Explicitly cannot own |
|---|---|---|
| Clock/Trigger | Time and external facts; signal/task creation | LLM prose, assistant turns |
| Task | `task_id`, state, lease, TTL, cancellation, retry/recovery, metadata receipt | Prompt, memory evidence, fanout |
| Agent | `work_session_id`, bounded context, planning, authored artifact | Arbitrary paths, task identity, turn/memory writers |
| Capability | Manifest, authorization, limits, adapter outcome | Scheduling or implicit cross-realm access |
| Interaction | Reality ingress, `turn_id`, serialization, visible delivery | Background lifecycle and worker logs |
| Dream | Dream-only task/capability lifecycle if separately approved | Reality task store, capabilities, login state |

Pure helpers (locking, atomic serialization, validation, redaction, limits) may be shared; stores,
workers, runtime objects, grants, and writers are realm-owned.

## Identity and causation

`ingress_event_id`, `turn_id`, `task_id`, `work_session_id`, and `attempt_id` are disjoint namespaces.
Equal strings never make identities interchangeable.

| ID | Owner | Meaning | May appear in |
|---|---|---|---|
| `ingress_event_id` | Reality ingress | accepted external/user-visible ingress | `EventContext`, bounded causation |
| `turn_id` | Interaction | one canonical visible Reality turn | `EventContext`, bounded causation |
| `task_id` | Task Manager | one durable work unit | receipt, attempts, work session |
| `work_session_id` | Agent Plane | one bounded non-chat LLM session | task/artifact provenance |
| `attempt_id` | worker lease | one lease-owned execution attempt | worker log, receipt |

`causation_ref` is optional, immutable, bounded lineage metadata (typed source, opaque reference,
digest). It is not evidence or authority and contains no text, prompt, tool data, secret, full path,
or `EventContext` payload. It cannot restore the source turn's scope, permissions, or lock.

```text
I1 -> EventContext(I1) -> T1 -> create K1(causation_ref=T1)
K1 -> receipt/worker log only; no EventContext, evidence, short_term, event_log
notify -> new ingress I2 -> EventContext(I2) -> new turn T2 -> turn_sink
```

Task success and notification success are separate outcomes. Notification retries never rerun the
capability task and always pass the normal Reality Dream Guard and scope checks.

## Task and receipt contract (implemented by Brief 230)

```text
created -> queued -> running -> succeeded | failed | canceled | expired | outcome_unknown
                      \-> queued (only when retry policy permits)
```

Tasks are immutable in `uid + char_id + realm`; duplicate idempotency keys resolve to one task.
Leases are owned by `attempt_id`; stale workers cannot complete or notify. Durable cancellation is
checked at bounded checkpoints. After restart, side-effecting orphan `running` becomes
`outcome_unknown` and is never auto-replayed. Read-only/idempotent retry must be declared by the
capability. Receipts and logs contain only bounded IDs, state, error code, counters, truncation and
artifact metadata: never prompts, content, raw tool output, secrets, full paths, or memory payloads.
Task observation has its own read-only store/endpoint and does not reuse EventContext, perceive-event,
Memory Event, `short_term`, `event_log`, action-trace, or autonomy counters.

## Scheduler trigger mapping

Every name in `core.scheduler.gating.TRIGGER_MIGRATION_STATUS` has one target below. Aliases emitted
inside modules resolve to the registered lifecycle name before admission.

| Current names | Status | Target owner and invariant |
|---|---|---|
| `hr_critical`, `hr_high`, `heart_rate` | migrated/active | Clock -> bounded health signal; autonomy may evaluate, never direct speech |
| `birthday_midnight`, `birthday_eve`, `birthday_afternoon`, `birthday_night` | migrated | Clock -> calendar signal with bounded urgency |
| `period_reminder` | migrated | Clock -> signal; missing owner date means no queue |
| `morning_greeting`, `night_reminder`, `good_night`, `midday`, `random_message` | migrated | Clock -> routine signal; source switches authoritative |
| `daily_journal` | migrated | Clock -> optional proactive signal; distinct from authored diary |
| `diary_reminder`, `diary_share_reminder`, `sensor_aware`, `sleep_end`, `weather_alert` | migrated | Clock -> bounded signal, no direct channel path |
| `topic_followup`, `spontaneous_recall`, `topic_reactivation`, `memory_reactivation` | migrated/active | Agent evaluator, read-only candidate; success only after delivery |
| `timenode`, `festival`, `holiday_boost` | migrated | Clock -> calendar signal |
| `garden_bloom`, `garden_harvest_expired`, `garden_handle_gift`, `garden_handle_self`, `garden_vase_wilted` | migrated | Clock -> garden signal; state is not speech |
| `reminders` | migrated | Scheduler capability -> due signal; Brief 235 owns durable schedule |
| `overflow`, `presence_nag`, `overflow_autonomy` | migrated/active | Clock -> bounded score signal; normal gates apply |
| `dream_exit` | migrated | Dream exit -> new Reality signal/turn; no shared Dream state |
| `letter_writer` | migrated | Task/Agent authored artifact plus separate delivery signal |
| `coplay_commentary` | migrated | Session fact -> optional signal, no direct executor |
| `practice_help` | migrated | Practice stall fact -> optional autonomy signal; the `practice` maintenance worker remains silent |
| `desktop_wake`, `restart` | active | One-shot bounded signal; no direct assistant turn |
| `interval`, `schedule` | active | Native clocks; produce signal/task, never prose |
| `dream_postcards` | active | Due authored postcard delivery; calls the bounded mail artifact adapter, never the assistant speech outlet |
| `activity_switch`, `coplay_watch`, `diary_inject` | maintenance-only | Silent Task worker/state maintenance |
| `episodic_decay`, `episodic_sweep` | maintenance-only | Existing memory maintenance writer; no speech |
| `inner_diary_write` | maintenance-only | Task -> Agent work session -> authored diary; 23:00 silent |
| `dlq_monitor`, `log_maintenance` | maintenance-only | Operational cleanup only |
| `garden_water`, `garden_daily` | maintenance-only | Task -> garden capability; silent state mutation |
| `hidden_state_decay`, `hidden_state_consolidate` | maintenance-only | Hidden-state maintenance; no notification |
| `storyline_weekly` | maintenance-only | Task -> Agent session -> storyline artifact |
| `event_log_salvage`, `memory_janitor` | maintenance-only | Memory maintenance; no proactive speech |
| `event_edge_proposer` | maintenance-only | Task -> bounded Memory Event candidate-edge worker; never speech/prompt/accepted evidence |
| `private_exchange` | maintenance-only | Isolated Agent session; existing relationship artifact only |
| `spend_monitor` | maintenance-only | Read-only balance task/manual notice proposal; never payment |
| `interest_seed`, `practice` | maintenance-only | Agent authored growth work; help is separate signal |
| `scheduler_pipeline_send`, `manual_direct_trigger` | retired | No executor; adapters may only queue/test and must not restore speech |

Module/loop aliases `morning`, `night`, `weather`, `birthday`, `watch_hr_critical`, `watch_hr_high`,
`watch_sleep_end`, `weather_alert_light`, and `weather_alert_heavy`
are compatibility labels, not additional lifecycle owners. Brief 231 must either register each actual
producer name with one of the four lifecycle states or normalize it to the canonical registered name.
The implemented registry does so; an unregistered label cannot enter the compatibility speech path.

## Autonomy mapping

| Existing object | Current meaning | Target |
|---|---|---|
| `Signal` / `pending_signals` | bounded proactive candidate fact | Clock/Trigger signal, never task/evidence |
| `Opportunity` | per-tick merged evaluator input | Agent evaluator input, bounded/non-authoritative |
| `Job(source=autonomy)` | short-lived evaluator lease/TTL/retry | Agent adapter; not a general Task alias |
| `Run` | evaluation/tool/talk audit | Agent run audit linked to future task, prompt still admin-protected |
| `interval`, `schedule`, `overflow` | native evaluation sources | Clock due facts |
| `desktop_wake`, `restart`, `heart_rate` | runtime/external facts | bounded/one-shot signals |
| `spontaneous_recall`, `topic_followup` | memory candidate evaluation | Agent read-only evaluator |
| `talk_owner` | sole proactive delivery outlet | Interaction adapter; creates new ingress/turn |
| `manage_self_capability` | autonomy-only management gateway | Capability policy adapter, separate origin |

## Tool to capability mapping

Current tools remain in `_TOOL_REGISTRY` and use `execute(origin=...)`; future adapters intersect all
existing origin, role, danger, confirmation, deployment, MCP, and enablement gates.

| Tools | Target capability | Constraint |
|---|---|---|
| `get_time`, `weather`, `web_search` | clock/network information | foreground bounded read/untrusted output |
| `add_reminder` | scheduler | structured durable task (Brief 235) |
| `read_diary`, `search_diary`, `read_watch`, `get_profile`, `get_episodic` | memory/document | Reality-scoped read |
| `search_events`, `expand_event_window`, `get_related_events` | memory evidence | explicit Reality read; receipts never indexed |
| `revise_memory`, `forget_episodic`, `clear_midterm`, `revise_user_profile` | memory mutation | explicit owner/provenance policy |
| `search_documents`, `read_document`, `search_character_notes` | memory/document | scoped character library (Brief 228) |
| `desktop_minimize`, `desktop_open_url`, `desktop_play_pause`, `desktop_notify`, `play_song` | desktop actuator | Reality/local; remote requires client ack |
| `toy_invite`, `dream_invite` | UI invitation | invitation does not grant cross-realm access |
| `peek_screen_content` | desktop observation | explicit grant/cooldown, bounded |
| `device_shutdown`, `device_sleep`, `exit_yandere` | local system actuator | dangerous/confirmation; remote unavailable |
| `phone_control_start` | mobile delegated task | separate mobile adapter until migration |
| `water_garden` | garden mutation | sandboxed allowlist and policy |
| `toy_vibrate`, `toy_stop`, `toy_pattern`, `toy_job_status` | hardware job | existing hardware manager remains owner |
| `read_toy_file`, `write_toy_file` | fixed authored document | enum targets, not workspace |
| `fs_list`, `fs_read` | workspace read | Brief 233 adapter; existing allow-roots |
| `workspace_list`, `workspace_read`, `workspace_create`, `workspace_update`, `workspace_delete`, `workspace_undo` | workspace capability | Brief 233; explicit roots/operation grants, Reality-only, bounded receipts |
| `process_run` | bounded process capability | Brief 234; local-only, workspace program, structured args, allowlisted interpreter, no shell/network, bounded resources |
| `manage_self_capability` | capability policy mutation | grant/revision/idempotency and dedicated origin |
| dynamic `mcp__*` | MCP transport adapter | transport is not permission; intersect local policy |

The workspace capability is implemented by Brief 233. Process, browser, and network capabilities are
not enabled by this table; Briefs 234 and 236 must independently satisfy their contracts.

## Store mapping

| Existing store | Owner | Target classification | Never reuse as |
|---|---|---|---|
| `autonomy_state(uid,char)` | autonomy | evaluator compatibility state | general Reality/Dream Task Store |
| `scheduler_cooldowns.json`, `scheduler_user_state.json`, `trigger_state.jsonl` | scheduler | clock/interaction state and audit | task/receipt/evidence |
| `gating_shadow.jsonl`, `execute_dryrun.jsonl` | scheduler | forensic decision logs | task completion proof |
| in-memory `defer_queue` | gating | short-lived signal deferral | durable queue |
| `proactive_ledger.json` | delivery policy | cooldown/budget history | task ledger |
| reminder file | reminder tool | scheduler compatibility state | general task queue |
| `hardware_jobs.json` | hardware manager | hardware capability state | Task Plane authority |
| `phone_control_tasks.json` | mobile runtime | mobile capability state | general Reality task state |
| `agent_actions.json` | desktop channel | transport fallback queue | receipt/audit |
| `core.message_queue` | QQ ingress | process-local Interaction input queue | durable task queue |
| `core.post_process.slow_queue` and DLQ | memory post-process | memory-owned deferred work/recovery | general task or capability receipt |
| owner-turn/companion receipts | Interaction ingress services | request idempotency and bounded response metadata | Task Manager receipt |
| wake delivery ledger / Wake Bridge state | Interaction/wake bridge | delivery/recovery state | Task Store or memory |
| Self Capability state/audit | self-management | capability grants, revisions, and policy audit | task lifecycle or business output |
| spend/mail execution ledgers | spend/mail subsystem | domain action audit | general task or memory evidence |
| character document index/stats/blobs | document library | memory/document store | task lifecycle |
| Memory Event, `short_term`, `event_log`, episodic, identity | memory | Reality evidence/memory | receipt/worker log |
| activity/reading/coplay/Stage stores | their session runtimes | domain session and transcript state | general Task Store |
| Dream state/archive/impression/RPG/Dream Stage stores | Dream | Dream-only state | Reality state |
| runtime service state and channel/mobile queues | process/channel owners | health/transport state | task completion or evidence |
| future Reality Task Store | Brief 230 | task/receipt authority | memory or Dream state |
| future Dream Task Store | separately approved | Dream-only authority | Reality store alias |

## Realm and deployment denial

Capability admission checks realm, deployment mode, authenticated scope, availability, grant,
character policy, task manifest, operation policy, and resources. Denials are stable and fail closed.
Dream cannot create/read/cancel Reality tasks, invoke Reality capabilities, or access Reality browser
login state. A shared UID/character does not weaken this rule. `remote_server` disables server-local
workspace, process, browser, desktop, shutdown/sleep and file fallback as a class; the server is never
treated as the owner's desktop. Mobile may consume only a future bounded user-visible result and does
not inherit desktop OS capabilities.

## Required negative-test design

Briefs 230-237 must provide evidence for each case:

| Case | Required assertion |
|---|---|
| silent task lifecycle | create/run/retry/cancel/expire leaves EventContext ingress/turn/evidence counters unchanged |
| receipt/memory separation | no Memory Event, `short_term`, `event_log`, episodic, identity, or fixation row |
| notification identity | task from `I1/T1` notifies through new `I2/T2`; only bounded causation links them |
| delivery failure isolation | successful task remains succeeded; Dream/offline delivery is separate; no task replay |
| namespace confusion | copied/equal ingress/turn/task IDs are rejected by schema boundary |
| Dream create/read/cancel/capability denial | no Reality store mutation, metadata leak, adapter call, prompt, or receipt |
| realm store alias denial | same UID/character resolves to distinct realm roots and workers |
| remote local capability | rejected before filesystem/process/browser adapter execution |
| restart side effect | orphan running becomes `outcome_unknown`, never automatic replay |
| stale lease and duplicate request | stale worker cannot mutate; concurrent idempotency creates one task/attempt |
| `217 soak invariance` | observer disabled/observe/not-ready enforcing preserves thresholds, metrics, and fallback |

## Migration and control-surface rules

Brief 230 adds Task Manager/observability first; 231 keeps trigger adapters and closes currently
unregistered producer labels such as `event_edge_proposer`; 232 adds non-chat Agent
work sessions; 233-236 add independently gated capabilities; 237 connects tools/autonomy/scheduler,
proves coverage, then removes old paths together with guards, tests, settings, and docs. No brief may
rewrite scheduler in one shot or restore `_pipeline_send` as a second speech path. `inner_diary_write`
and `daily_journal` retain separate names, counters, and lifecycles.

Future durable state requires a same-change read-only backend observation endpoint and, where consumed,
admin/desktop/mobile catalog updates. Brief 229 itself adds no endpoint, setting, or client field.

## Brief 232 Agent Work Sessions

`core/agent_runtime/work_sessions.py` provides the Reality-only non-chat LLM work boundary. A session
has its own `work_session_id`, references exactly one Task Manager `task_id`, accepts bounded context,
and permits only manifest artifact kinds (`authored_diary`, `document_summary`, or
`workspace_artifact`). Its lifecycle and metadata-only observation are independent from EventContext,
`turn_sink`, short-term history, `event_log`, episodic memory, and identity. The migrated
`inner_diary_write` scheduler task creates and claims both records, invokes the existing fact/feeling
generator, and completes the authored artifact without creating an assistant turn. `daily_journal`
remains a proactive signal. Work-session failures and unknown outcomes never become user facts; only
an explicit later fixation flow may promote an artifact.

## Brief 233 workspace capability

The workspace adapter is separate from the legacy read-only `fs_access` tools. It accepts only explicit
configured roots, rejects project `data/`, sensitive names, symlinks, unsupported text types, and
`remote_server` mode. `read`, `list`, `create`, `update`, and `delete` are independent permissions;
writes use atomic replacement and bounded file/total/concurrency limits. Mutating tool calls create a
Reality Task Manager receipt and expose only metadata (`task_id`, operation, size, digest, version),
never content or absolute paths. Delete requires an explicit confirmation flag. Dream has no adapter or
access to the Reality workspace.
