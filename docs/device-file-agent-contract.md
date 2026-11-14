# device-file-agent.v1

The current Reality character uses the existing Task Manager and Work Session to coordinate edits on an authenticated desktop. Each task freezes the owner, character, desktop installation, desktop process epoch, workspace and grant revision. Polling is independent of GUI Computer Use and desktop WebSocket ownership.

## Transport

`POST /device-file-agent/poll` and `POST /device-file-agent/result` require the desktop token with `ws.desktop` and `sensor.write`. Connections outside loopback require HTTPS. Polls advertise up to 16 opaque grants with aliases and read/write permissions. The command carries task ID, lease binding, server and device epochs, workspace/revision, sequence, random nonce and relative expiry. The server delivers each command once. The device journals write attempts before publication and resends durable receipts after reconnect. An old epoch can reconcile evidence but cannot authorize a new write.

## Files

`find` searches bounded relative names and text. `read` returns a bounded line page and full-byte SHA-256. `patch` replaces a unique span at the expected SHA-256, preserving UTF-8 BOM and newline style. `create` never overwrites. `undo` checks the current SHA-256 against the saved revision. `verify` reads back and matches a hash. Syntax, build and visual checks report their own states, including `unknown`.

Requests and results are limited to 64 KiB. The desktop accepts UTF-8 text files up to 256 KiB and returns pages up to 8 KiB. A task allows at most 30 file actions, 20 files, 600 seconds and three automatic repair attempts. A saved receipt requires a matching readback. No shell or package manager is exposed.

The optional unrestricted grant defaults off and requires explicit desktop consent. Its separate write permission defaults off. It accepts absolute paths on supported local drives and can return those paths to the planner transiently. Name searches start in the local Downloads, Desktop and Documents folders. File format and size bounds, OS permissions, path validation and symlink checks continue to apply. Turning the grant off invalidates its ID and revision at the desktop.

## Data boundary

The desktop holds grants, snapshots and the write journal in app data. The server retains task metadata and handles bounded excerpts transiently. Documents are untrusted data. Normal logs and public receipts omit file contents and paths. The folder and file picker grants exclude sensitive paths. The optional unrestricted grant can reach sensitive text files accessible to the desktop user.
