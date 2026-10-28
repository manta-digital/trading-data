---
docType: review
layer: project
reviewType: slice
slice: archive-generalisation
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/300-slice.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261028
dateUpdated: 20261028
reviewedSha: ac511bf779911cd39715bce57990d76b972686b2
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 99620
cachedTokens: 11220
cacheWriteTokens: 88396
completionTokens: 3907
durationSeconds: 35.1
costUsd: 0.394533
costBasis: provider
runId: run-20261028-p4-fde8dd6d
squadronVersion: 0.24.6
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Scope and boundaries match the architecture"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Technical Scope"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Unheld-job classification does not say what happens when `ts_received` is absent"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D8 — The guard counts marginal spend"
  - id: F003
    severity: concern
    category: architecture-alignment
    summary: "The inclusion table is a code constant, but the architecture says \"kept in configuration\""
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#State Management"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Some mechanisms are heavier than the slice needs"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D5, D6"
  - id: F005
    severity: concern
    category: structure
    summary: "Module placement is inconsistent and mixes concerns"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Component Structure"
  - id: F006
    severity: pass
    category: error-handling
    summary: "Failure modes of the moved ingest path are covered"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#Data Flow"
  - id: F007
    severity: pass
    category: verification
    summary: "Parity and risk handling are proportionate"
    location: "project-documents/user/slices/300-slice.archive-generalisation.md#D10"
  - id: F008
    severity: note
    category: nfr
    summary: "The architecture has no NFR on these paths"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#Technical Considerations"
---

# Review: slice — slice 300

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Scope and boundaries match the architecture

The slice delivers each item the architecture assigns to it:
- Family-scoped supersession.
- A per-schema projector registry.
- A plan-inclusion marker with its configuration table.
- A guard that counts marginal spend only.

It keeps the bundle rule as a tick-tier rule (D4). It also leaves the `tick` track, `MT_TICK_*` and `mt data tick` names alone. Status, coverage and health stay out of scope, which fits the principle that "a tier ships with its completeness check", because this slice adds no tier. The "Provides to Other Slices" contract for 301, 302, 303, 304 and 306 matches what those slices need.

### [CONCERN] Unheld-job classification does not say what happens when `ts_received` is absent

D8 classifies each unheld provider job "from its own request and `ts_received`". It says `parse_batch_job` already fails the listing for a missing schema or non-UTC-day bounds. It does not say what `ts_received` holds for a job that is queued or processing. That is exactly the kind of job the guard most needs to count. Nothing says whether a missing value counts in full, is classified at the current time, or raises. This is the one new failure path in the guard. The slice's own claim that "no job can fall out of the sum" depends on the answer. The design should state the rule and add a test.

### [CONCERN] The inclusion table is a code constant, but the architecture says "kept in configuration"

The architecture's Cost accounting section says the marker is "set from one table (schema to included history) kept in configuration". The slice keeps it as a code constant (`PLAN_INCLUDED_HISTORY`) and gives a reasonable argument. It changes only when the pricing page changes and needs a reviewed commit with a test. The slice also keeps the deployment choice, which plan the account holds, as an environment setting. This is a defensible reading, but it departs from the architecture's wording. Record it as a deliberate interpretation, for example in the architecture's Cost accounting text, so later slices do not treat it as drift.

### [CONCERN] Some mechanisms are heavier than the slice needs

- **`NOT_INCLUDED` kind.** `IncludedHistory` has three kinds, but the only table entry (Standard on `GLBX.MDP3`) uses `ALL_HISTORY` and `TRAILING_MONTHS`. `NOT_INCLUDED` is used nowhere yet. Cut it until a plan or schema needs it. A pair with no entry already returns `None`.
- **`DatabentoPlan` enum and CHECK.** The enum has one member, and the column carries a rendered CHECK that every new plan must widen in a migration. A boolean marker would meet the architecture's requirement, which is only that the guard and the row agree. If the plan name on the row is worth keeping, say what consumes it. 303 only needs `IS NULL`.
- **Totality tests.** Totality over `TickSchema` for every plan map and a test that builds SQL with a synthetic extra family both guard against speculative additions. Keep the family-predicate test, since it covers the stated "missed `STORED_TIERS`" risk. Defer the per-plan-map totality test until a second plan exists.

### [CONCERN] Module placement is inconsistent and mixes concerns

Component Structure puts `DatabentoPlan`, `IncludedHistory` and `PLAN_INCLUDED_HISTORY` in `schema_families.py`, and also lists a separate `plan_inclusion.py`. Technical Requirements then say "the family and plan tables live in `schema_families.py`". Schema families and billing plans are unrelated concepts, and the file will grow toward the 300-line limit as 301 and 302 add entries.

`Settings` must also import `DatabentoPlan`. That makes `config` depend on `data/tick/schema_families`, which is an inverted dependency direction unless `config` already imports from the data layer. Put the plan enum and table in `plan_inclusion.py` and keep `schema_families.py` for families. State where the enum that `Settings` imports lives and why that import is acyclic.

### [PASS] Failure modes of the moved ingest path are covered

The move keeps the worker's single transaction and its existing handling (`UnitCheckFailed`, `UniqueViolation`, decode errors, `ManifestTransitionError`). The `not_selectable` behaviour for a unit with no projector is stated. An unknown plan value fails at settings validation, so the preflight refuses. The migration is gated by the existing pending-migration preflight. The remaining gap is the `ts_received` case in the first concern above.

### [PASS] Parity and risk handling are proportionate

Reusing the 226 and 228 harnesses for a double rebuild avoids a new script. It avoids the comparison against production, which the slice rejects because production's manifest history cannot be reproduced. The slice admits that parity cannot catch a missed `STORED_TIERS` use and covers that with a targeted test and an end-of-slice grep. It also states that the trailing-12-month boundary is unmeasured and chooses the conservative side.

### [NOTE] The architecture has no NFR on these paths

The architecture states no latency or throughput target for ingest or the guard. Its "Manifest scale" item asks for a measurement before history acquisition, and that belongs to a later slice. The new `included_in_plan IS NULL` predicate on the trailing read is not expected to affect it. No restatement is needed here.

### Run Digest

- Response length: 6615 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 99620 / 11220 / 88396 / 3907 / not computed
- Duration: 35.1 s
- Cost: $0.39 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
