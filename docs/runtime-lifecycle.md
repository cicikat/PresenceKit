# Runtime Lifecycle

> Emerald-Presence runtime startup order and long-lived component ownership.
>
> This document describes the current runtime topology.
> It is not a future architecture proposal.

---

## 1. Process Entry

Backend entry point:


python main.py


`main.py` owns application startup.

No subsystem should independently create global runtime instances or background services outside the startup path.

---

# 2. Backend Startup Sequence

Current startup order:


main.py
|
+-- load configuration
|
+-- validate admin authentication
|
+-- load active character
|
+-- initialize lore engine
|
+-- create Pipeline
|
+-- register Pipeline
|
+-- register slow handlers
|
+-- cleanup pending state
|
+-- start background services
|
+-- start HTTP/admin service


---

## 3. Core Runtime Owners

### Pipeline

Owner:


main.py


Creation:


Pipeline(character, lore_engine, active_character_id)


Registration:


pipeline_registry.register()


Lifetime:


process lifetime


Responsibilities:

- conversation processing
- prompt construction
- memory interaction
- LLM execution coordination

Other modules should access Pipeline through the registry instead of creating their own instance.

---

## 4. Scheduler Lifecycle

Owner:


main.py


Startup:


scheduler.start()


Lifetime:


background asyncio task


Responsibilities:

- periodic checks
- proactive proposals
- trigger evaluation
- gating
- execution coordination


Scheduler structure:


scheduler loop
|
+-- trigger modules
|
+-- proposer registry
|
+-- gating
|
+-- execution
|
+-- turn sink / output


Important:

Scheduler depends on Pipeline being initialized first.

Trigger execution before Pipeline registration is invalid.

---

# 5. Registry Ownership

The system currently uses several registries.

They solve different problems and should not be merged without a clear reason.

---

## Pipeline Registry

Purpose:

Store the active Pipeline instance.

Flow:


main.py
|
v
Pipeline creation
|
v
pipeline_registry.register()


---

## Tool Registry

Purpose:

Register available tools.

Flow:


tool module
|
v
tool dispatcher registry
|
v
LLM/tool execution


---

## Scheduler Proposer Registry

Purpose:

Allow trigger modules to submit proposals.

Flow:


trigger module
|
v
register_proposer()
|
v
scheduler gating
|
v
execution


---

# 6. Long-lived Components

| Component | Owner | Lifetime |
|---|---|---|
| Pipeline | main.py | process lifetime |
| Lore Engine | main.py | process lifetime |
| Scheduler Task | scheduler.start() | process lifetime |
| FastAPI Admin Server | main.py | process lifetime |
| Sensor workers | corresponding runner | process lifetime when enabled |

---

# 7. Background Workers

Background services must have:

- explicit owner
- startup location
- shutdown behavior

Current examples:


scheduler task
sensor runner
visual observation runner
hardware workers


A module should not silently create a background task during import.

---

# 8. Import Rules

Importing a module should not:

- start threads
- create network connections
- create global runtime objects
- launch asyncio tasks

Initialization belongs to startup code.

---

# 9. Event Flow

Current message flow:


Input
|
v
Channel / API
|
v
Pipeline
|
v
LLM + tools + memory
|
v
Turn Sink
|
v
Output


Proactive flow:


Sensor / Scheduler / Trigger
|
v
Proposal
|
v
Gating
|
v
Execution
|
v
Pipeline / Output


---

# 10. EventBus Status

Current system does not use a universal EventBus.

Existing mechanisms:

- pipeline registry
- tool registry
- proposer registry
- perceive_event
- turn sink

These currently represent different boundaries.

A future EventBus should only be introduced when there is a clear requirement for:

- cross-module asynchronous notification
- multiple independent consumers
- lifecycle ownership

Do not replace existing registries with a generic event bus only for abstraction.

---

# 11. Known Gaps

## Missing shutdown contract

Current lifecycle documentation focuses on startup.

Future work:

- service shutdown order
- task cancellation
- resource cleanup


## Missing runtime topology view

Future improvement:

Document:

- running workers
- owned tasks
- persistent state owners
- communication paths

