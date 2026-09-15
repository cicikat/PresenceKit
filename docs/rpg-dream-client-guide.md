# RPG Dream Client API Guide

This guide is for Emerald-client and mobile implementers. The RPG Dream
backend is complete for Briefs 219-222; desktop dual-column UI and mobile
consumption remain unimplemented. Use only these HTTP endpoints and the live
`/openapi.json`. Never read `data/runtime` or infer RPG messages from
WebSocket frames.

## Admin Placement

Put RPG in the existing Dream Settings page as a fourth mode beside `sandbox`,
`scenario`, and `mirror`. The RPG panel should select an existing Scenario
`script_id`, show the `rpg/v1` capability, and expose enter/exit plus read-only
session status. It should not add a second script format or editors for dice,
DC, seed, KP prompts, or hidden facts. Scenario authoring remains the existing
Scenario management surface; RPG references its script. `rpg_kp` is a backend
model-routing category selectable in admin Routing Profiles, not a client toggle.

## Auth and Discovery

Send `Authorization: Bearer <activity-capable-token>` on every RPG request.
Business endpoints require `activity`; `GET /observability/dream-rpg` requires
`state.read` and is for admin observability, not gameplay.

Start and reconnect with `GET /dream/capabilities`; do not hard-code the mode
list:

```json
{
  "supported_modes": ["sandbox", "scenario", "mirror", "rpg"],
  "rpg": {"available": true, "contract_version": "rpg/v1", "max_primary_characters": 1}
}
```

Hide RPG when it is absent or unavailable. Unknown future modes must degrade to
unavailable without affecting other Dream modes.

## Lifecycle

1. Read `GET /dream/state`; when `dream_mode` is `rpg`, read
   `GET /dream/rpg/state`.
2. Enter through `POST /dream/enter` with
   `{"dream_mode":"rpg","script_id":"<script-id>"}`. The returned
   `dream_id`, `script_id`, and `dream_mode` are frozen for the session. This
   endpoint is a shared legacy Dream route: its current OpenAPI request/response
   schema is intentionally generic, so clients must send only the documented
   fields (`dream_mode`, `script_id`, and optional `entry_reason`) and validate
   the returned fields defensively.
3. Restore the active branch with
   `GET /dream/rpg/transcript?dream_id=<id>&limit=50`.
4. End with `POST /dream/exit`. This is an immediate hard exit and does not wait
   for a KP or character call. Repeated exit follows the existing idempotent
   Dream exit behavior.

The safe state projection contains `session` (or `null`) and `scene`. Session
fields are `dream_id`, `char_id`, `script_id`, `status`, `round_status`,
`active_round_id`, `active_branch_id`, `scene_revision`, `since`,
`last_error_code`, and `session_health`. Treat `uncertain` or non-`ok` health as
read-only recovery state; never continue a round from it.

## Two-Lane Turns

`POST /dream/rpg/turn` request:

```json
{
  "dream_id": "dream_demo",
  "request_id": "turn_demo_001",
  "lane": "character",
  "message": "I inspect the marks beside the door.",
  "expected_scene_revision": 0
}
```

`lane` is `character` or `kp`. Character actions are visible to the active
character and may produce a character reply. KP actions are private player
actions or rules questions: they must not enter the character prompt, but may
be shown in the player's KP column.

The response is typed by `/openapi.json` and includes `dream_id`, `round_id`,
`request_id`, `status` (`completed|partial|failed`), `scene_revision`,
`entries`, `character_reply_generated`, `dice_roll_ids`, and `error`.
Each entry has `entry_id`, `lane` (`character|kp|shared`), `kind`, `content`,
`ts`, and `correlation_id`. Current kinds include `user_action`, `resolution`,
`character_reply`, `character_check`, `character_followup`, and `correction`;
unknown kinds should use a generic text renderer. Put character replies in the
character column, KP entries in the KP column, and shared entries in a common
area. Treat `dice_roll_ids` as opaque IDs; no dice-detail endpoint exists.

The backend strips valid `<C>...</C>` markers before returning visible text.
Do not execute control tags in the client. A `partial` response keeps its
entries and error visible; do not recursively process markers from a follow-up.

## Idempotency and Recovery

- Generate a new opaque `request_id` per user submission and reuse the exact
  body for network retries.
- Same ID and same body returns the original response. Same ID with different
  body returns `RPG_IDEMPOTENCY_CONFLICT`; stop retrying.
- `RPG_ROUND_BUSY` is retryable with the original body after a short backoff.
- On `RPG_REVISION_CONFLICT`, refresh state/transcript and require a new action
  confirmation; do not silently replay with a new ID.
- `RPG_KP_OUTPUT_INVALID` and `RPG_SESSION_UNCERTAIN` are failure/read-only
  states, never a guessed resolution.

## Transcript, Corrections, and Archive

Restore with `GET /dream/rpg/transcript?dream_id=<id>&before=<entry-id>&limit=50`.
The response has `items`, `next_before`, `has_more`, `partial_read`,
`scene_revision`, and `active_branch_id`. On `partial_read=true`, keep readable
items and show a partial-recovery state.

Corrections use `POST /dream/rpg/corrections`:

```json
{
  "dream_id": "dream_demo",
  "request_id": "correction_demo_001",
  "operation": "clarify",
  "target_round_id": "round_1",
  "text": "Add that the lock has old scratches.",
  "reason": "",
  "expected_scene_revision": 1
}
```

`operation` is `clarify`, `retcon`, or `branch`. Clarify does not reroll;
retcon/branch preserve old branches and dice and only change the active branch.
Corrections use the same request-ID and revision-CAS rules.

Replay uses `GET /dream/archive` and `GET /dream/archive/<dream_id>` with the
optional `char_id`. RPG archives contain only player-visible entries and
metadata; they do not remount a session, call a model, broadcast, or expose
KP-private facts, prompts, DC, seed, dice faces, or paths. These two shared
legacy archive routes also currently expose generic response schemas in
OpenAPI; consume only the documented fields and tolerate additional fields.
Preserve each
entry's `lane`, `kind`, and `correlation_id` during replay.

## Error Codes

Branch on `detail.code`, never on localized `message`:

| Code | Client action |
|---|---|
| `RPG_ENDPOINT_REQUIRED` | Do not call `/dream/chat`; switch to RPG UI |
| `RPG_NOT_ACTIVE` | Return to Dream entry or refresh state |
| `RPG_DREAM_ID_MISMATCH` | Discard stale page state and reload session |
| `RPG_ROUND_BUSY` | Back off and retry the same request |
| `RPG_IDEMPOTENCY_CONFLICT` | Stop; report local request ID conflict |
| `RPG_REVISION_CONFLICT` | Refresh and ask the user to confirm again |
| `RPG_INVALID_LANE` | Client bug; do not retry |
| `RPG_KP_OUTPUT_INVALID` | Show failure; never invent a result |
| `RPG_CHARACTER_GENERATION_FAILED` | Keep world result and show partial state |
| `RPG_SESSION_UNCERTAIN` | Read-only recovery state |

RPG does not use `/dream/chat`, `/desktop/chat`, `/mobile/chat`, ordinary tools,
tool loop, Reality turn sink, scheduler, QQ/mobile broadcast, or RPG-specific
WebSocket frames.
