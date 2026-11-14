# device-file-agent.v1

The current Reality character uses the existing Task Manager and Work Session to coordinate edits on an authenticated desktop. Each task freezes the owner, character, desktop installation, desktop process epoch, workspace and grant revision. Polling is independent of GUI Computer Use and desktop WebSocket ownership.

## Transport

`POST /device-file-agent/poll` and `POST /device-file-agent/result` require the desktop token with `ws.desktop` and `sensor.write`. Connections outside loopback require HTTPS. Polls advertise up to 16 opaque grants with aliases and read/write permissions. The command carries task ID, lease binding, server and device epochs, workspace/revision, sequence, random nonce and relative expiry. The server delivers each command once. The device journals write attempts before publication and resends durable receipts after reconnect. An old epoch can reconcile evidence but cannot authorize a new write.

## Files

`find` searches bounded relative names and text. `read` returns a bounded line page and full-byte SHA-256. `patch` replaces a unique span at the expected SHA-256, preserving UTF-8 BOM and newline style. `create` never overwrites. `undo` checks the current SHA-256 against the saved revision. `verify` reads back and matches a hash. Syntax, build and visual checks report their own states, including `unknown`.

Requests and results are limited to 64 KiB. The desktop accepts UTF-8 text files up to 256 KiB and returns pages up to 8 KiB. A task allows at most 30 file actions, 20 files, 600 seconds and three automatic repair attempts. A saved receipt requires a matching readback. No shell or package manager is exposed.

The optional unrestricted grant defaults off and requires explicit desktop consent. Its separate write permission defaults off. It accepts absolute paths on supported local drives and can return those paths to the planner transiently. Name searches start in the local Downloads, Desktop and Documents folders. File format and size bounds, OS permissions, path validation and symlink checks continue to apply. Turning the grant off invalidates its ID and revision at the desktop.

## Task results and diagnostics (312)

File tools are available only in the owner's private chat tool loop; autonomy schemas omit them and the dispatcher still rejects autonomous invocations. A tool request returning `queued` or `running` acknowledges submission or progress. Task failures, pauses and unknown writes must not appear as completed file work. Results include `request_status` and `task_status`; saved audit records retain the opaque task ID and business status/code without paths or excerpts.

The default legacy `edit` intent requires at least one saved write and matching verification before `succeeded`. Finding or reading alone cannot finish an edit. Explicit `inspect/search/read` intents complete with the corresponding device evidence and zero writes. Invalid JSON plans (`invalid_file_plan`) and invalid Action schemas (`invalid_file_action`) allow at most three repairs; invalid planning attempts do not spend the 30-device-action budget. True action exhaustion remains `action_budget_exceeded`.

Task status and `GET /observability/device-file-agent` expose bounded `progress` metadata: current and failure stages, plan attempts, completed device actions, last operation/sequence/result, timestamps and exception class/schema field names. Unknown input keys, exception text, prompts, file paths and contents are excluded. Historical receipts may have no progress fields. No new device protocol fields or client settings are required.

## Data boundary

The desktop holds grants, snapshots and the write journal in app data. The server retains task metadata and handles bounded excerpts transiently. Documents are untrusted data. Normal logs and public receipts omit file contents and paths. The folder and file picker grants exclude sensitive paths. The optional unrestricted grant can reach sensitive text files accessible to the desktop user.

## Atomic read operations (313 B)

New desktop polls advertise `operations`: `list_dir`, `stat`, `find_name`, `search_text`, `read_page`. Omitted operations mean the legacy client cannot accept these commands: the backend returns `unsupported_operation` before allocating work. Existing editing actions and their receipts retain their shape. Deploy the backend before the new desktop; roll back the desktop first. No grant migration or new settings are needed.

Model tools `device_file_list_dir`, `device_file_stat`, `device_file_find_name`, `device_file_search_text`, `device_file_read` have `effect=read`. They use the same task/work-session, authenticated poll, lease, nonce, grant and ACK path, with one action and no planning model. Success requires a current device result with operation-specific evidence. Zero writes and confirmed empty results are successful. Bad evidence returns `protocol_mismatch`. Evidence text is transient; durable receipts and the existing observation endpoint contain only safe progress/counters.

Each tool takes `device_id`, `workspace_id`, `path`, `query`, and `options` (a strict JSON object string). Options: `limit` 1–100 (default 50), `depth` 0–12 (default 6), `cursor`, `start` 1–262144, `mode`, `content_search`. Name matching supports exact/substring/glob; content matching supports literal/regex and requires `content_search=true`. Example: find `query="**/*.html"`, `options='{"mode":"glob"}'`; read `query=""`, `options='{"start":101}'`.

Directory/name results return workspace-relative entries, type/size/mtime, next cursor, matching and truncation metadata. Metadata may identify `config.json` and safe hidden filenames; standard grants still deny their sensitive contents and descent into excluded directories. Names alone never read bodies. Stat reports `not_found` distinctly. Read pages add `end` and `has_more` to the full-file hash and line range. Absolute local paths require the existing explicit grant; slash variants and ASCII Windows case differences are accepted. UNC/ADS and traversal stay denied.

Scans have hard limits: 4096 visited entries/results, depth 12, 1 second, 8 MiB body bytes, 256 KiB per text file, and 24 KiB serialized result entries per page. Results sort deterministically; cursors bind grant revision, operation, query/scope and the result snapshot. Changed snapshots return `stale_cursor`; invalid offsets return `invalid_cursor`. `truncated=true` also covers hard scan limits, even when no continuation cursor is available; `total=null` means the full total is unknown. A scan revalidates the root identity throughout; desktop grant changes serialize with the bounded scan, and backend revocation/cancellation rejects the result before exposing evidence. Mid-scan cooperative desktop cancellation remains a follow-up limitation.

## Typed task completion (313 C)

Every delivered command now includes required `operation_intent=inspect|search|read|edit|create`, frozen from the submitted task. Desktop execution independently rejects `create/patch/undo` for read intents before touching files or the journal, even with a writable grant. Create intent rejects patch/undo; changing intent on an existing task is rejected. Missing/unknown intent fails deserialization; it never defaults to edit. Deploy this backend before this desktop and roll back the desktop first. An old desktop does not provide this additional local intent guard; an updated desktop cannot execute commands from an old backend. These combinations are not equivalent security evidence.

`device_file_task_start` accepts optional `operation_intent=inspect|search|read|edit|create`; omission preserves legacy edit behavior. Intent freezes at submission and steering cannot elevate it. Read intents reject every write before device dispatch. Create rejects patch/undo. Delete/move/execute/computer_use are not supported file-planner intents: use their existing authorized executors or report the capability gap.

Read tasks need operation-specific, usable device evidence, including confirmed empty results. Edit/create require a saved ACK and matching verification for every written path. Premature `done` receives bounded feedback and then fails with the unmet completion condition. No-change certification and explicit compound required-target contracts remain open; a saved result proves only the reported, verified targets. Do not claim every natural-language subgoal has been satisfied solely from this receipt.

Results distinguish `request_status=accepted` (queued/running/paused or unknown outcome), `task_status`, immutable intent, `completion_reason=device_evidence_received|saved_and_verified`, safe `evidence_summary` counters, `error_code`, and `next_action`. Pending audit receipts remain pending. Query/control/cancel use the same task ID; unknown writes require reconciliation and are never replayed automatically. Pausing is a pending state, not completed work.

Planning prefers native tool calls; fallback accepts exactly one JSON action/finish/clarification or a complete JSON fence. Duplicate keys, mixed control/action objects, unknown tools and multiple calls are rejected. JSON syntax and schema failures retain distinct exception types; both consume the bounded model repair budget, never device action budget. At most 40 model calls, 30 device actions and the existing task time limit apply. No provider-specific output adapter is introduced.

Read evidence remains in a bounded in-memory cache (20 tasks, at most the original 600-second deadline) for later same-task status queries. Each return rechecks owner/character, grant revision, device epoch, online freshness and feature authorization. Revocation removes cached evidence; process restart discards it. Persistent receipts and observation never contain paths, excerpts or raw model responses. A restarted read must be explicitly requested again to obtain text; saved edit receipts survive independently.
