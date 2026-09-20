# Character memory dossier validation (Brief 258 E)

Date: 2026-09-19. Scope: isolated test data only; production remains default-off.

## Baseline

Before Brief 258, there was no structured topic-dossier store, no evidence
membership/revision lineage, no bounded dossier detail tool, and no
`memory.consolidation` Task/Work Session. Legacy episodic/event-log recall could
surface related prose, but could not satisfy the dossier-specific semantic
checks below with a stable object and source chain. This baseline is an
architectural absence, not a synthetic quality score.

## Post-change semantic scenarios

| Scenario | Evidence |
|---|---|
| correct topic hit and identifiable legacy miss | prompt/tool tests assert a matching dossier owns overlap and a miss keeps legacy recall |
| complete evidence chain and demand lookup | detail/event tools page stable evidence references with a hard page cap of 50 |
| repeat mentions and duplicate counting | store tests preserve distinct mentions while idempotent operation IDs prevent duplicate active conclusions |
| canceled plan and counterexample | scenario tests retain contradictory evidence and avoid promoting an abandoned purchase plan |
| explicit correction and tombstone | invalidation tests suppress affected understanding immediately and mark recomputation without deleting evidence |
| late evidence | recall follows ingest sequence/watermark rather than occurrence time and marks unreviewed additions |
| token/input bounds | prompt recall is capped at 3 dossiers/1200 characters; worker input, output tokens and batch size are bounded |
| foreground yielding | worker tests assert idle/night admission, no next model claim after foreground activity, timeout and no conversation send/capture |
| crash and unknown outcome | transactional receipts recover committed work; an unknown outcome without a receipt is not replayed automatically |

## Verification boundary

The focused backend, admin, security, data-registry, prompt and static-asset
regressions are the executable evidence for E. Browser inspection uses an
isolated local config and a hard refresh. The exact command counts and any
environmental not-run items are recorded in the Brief 258 task evidence table
at commit time.

No production model call, memory migration, feature enablement, desktop/mobile
native build, or first-night claim was performed. Isolated tests now cover the
operator first-night bypass, conservation stop, incremental watermark and
closeout denominator. Those remain Brief 258 F after Brief 259 A-C and
explicit production authorization; Brief 259 D stays unchecked until a real
first-night plus next-morning run exists.
