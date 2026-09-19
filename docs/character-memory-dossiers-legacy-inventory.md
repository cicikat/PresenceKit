# Brief 258 legacy memory inventory

Inventory date: 2026-09-19. This is a review and routing record only. No
production data was migrated, rewritten, disabled, or deleted.

| Candidate path | Current role | Dossier coverage | Migration/fallback condition | Rollback |
|---|---|---|---|---|
| `core.memory.episodic_memory.write_episode` and `fixation_pipeline` reflection | generalized episodic conclusions and recall | dossier is the sole overlap output only for an explicitly owned topic; unowned topics retain episodic fallback | Brief 259 source inventory and per-topic ownership proof; do not bulk-convert episodic prose without lineage | disable dossier layer and resume episodic recall |
| `pipeline.fetch_context` event/episodic prompt layers | legacy prompt injection | overlapping prose is suppressed when a dossier hit exists; diagnostic traces remain | no migration needed for the fallback path; each topic needs a tested hit/miss boundary | disable `6b_memory_dossiers` and restore legacy layer injection |
| `core.memory.mid_term` and `summarize_to_midterm` | bounded 12-hour continuity view | independent short-horizon context, not a topic-understanding writer | no dossier takeover; retain for continuity and rollback | leave mid-term unchanged |
| `core.memory.event_log` capture/search and `event_log_salvage` | canonical evidence ledger and salvage before retention | dossier occurrences cite it; dossier never writes summary text back | evidence migration/revision checks belong to Brief 259; tombstones remain authoritative | preserve evidence and stop derived claims |
| `core.memory.storyline` and `storyline_weekly` | narrative arcs and weekly aggregation | independent narrative purpose; not a duplicate topic-understanding outlet | no deletion or automatic conversion; only explicit later brief can change it | disable dossier recall without touching storyline |
| `scheduler/triggers/memory_janitor.py` episodic duplicate merge | legacy bounded cleanup and vector consistency | does not merge dossier revisions or source evidence | remains needed while episodic fallback exists; reassess after scoped rollout | stop trigger and retain prior episodic files |
| `core.memory.action_trace` event-log echo | auditable tool-action trace | not a user-fact understanding; outside dossier ownership | keep behind its existing configuration and provenance rules | turn off echo only under its own approved change |

## Unique effective output

For an owned topic, the current prompt output is the dossier understanding plus
explicitly marked unreviewed late evidence. Event-log and episodic prose for the
same turn is suppressed, while retrieval traces stay available to diagnostics.
For an unowned topic or a dossier miss, legacy event/episodic recall remains the
identifiable fallback. This makes the routing boundary testable without a global
legacy shutdown.

## Deletion gate

No candidate is safe to delete from this inventory alone. A later approved
change must show 259 source coverage, reversible backup/restore, no remaining
fallback dependency, and a scoped rollback point; only then may the candidate's
dedicated tests, guards, configuration, and documentation be removed together.
`storyline`, `mid_term`, evidence capture, and audit traces are not deletion
candidates merely because dossiers exist.
