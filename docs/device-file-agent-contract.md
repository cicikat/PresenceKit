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

This editing capability requires at least one saved write and matching verification before `succeeded`. Finding or reading alone ends with `no_write_performed`. Invalid JSON plans (`invalid_file_plan`) and invalid Action schemas (`invalid_file_action`) allow at most three repairs; invalid planning attempts do not spend the 30-device-action budget. True action exhaustion remains `action_budget_exceeded`.

Task status and `GET /observability/device-file-agent` expose bounded `progress` metadata: current and failure stages, plan attempts, completed device actions, last operation/sequence/result, timestamps and exception class/schema field names. Unknown input keys, exception text, prompts, file paths and contents are excluded. Historical receipts may have no progress fields. No new device protocol fields or client settings are required.

## Data boundary

The desktop holds grants, snapshots and the write journal in app data. The server retains task metadata and handles bounded excerpts transiently. Documents are untrusted data. Normal logs and public receipts omit file contents and paths. The folder and file picker grants exclude sensitive paths. The optional unrestricted grant can reach sensitive text files accessible to the desktop user.
