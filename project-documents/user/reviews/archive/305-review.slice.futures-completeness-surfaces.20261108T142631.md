---
docType: review
layer: project
reviewType: slice
slice: futures-completeness-surfaces
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/305-slice.futures-completeness-surfaces.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: ba3d33f53de2478545d55e19311644d3ad5c7dd9
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
effort: high
turns: 11
promptTokens: 718660
cachedTokens: 596779
cacheWriteTokens: 121863
completionTokens: 12375
durationSeconds: 107.5
costUsd: 0.671687
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "D1 departs from \"extends COVERAGE_SOURCE_TABLE\" and records why"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D1 — COVERAGE_SOURCE_TABLE gets no futures entries; families get FAMILY_SOURCE_EDGE"
  - id: F002
    severity: pass
    category: scope
    summary: "Plan-assigned scope is covered and boundaries hold"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#Technical Scope"
  - id: F003
    severity: concern
    category: correctness
    summary: "The statistics source edge is defined through \"the tick-tier edge\", which can falsely report caught up"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:155-163"
  - id: F004
    severity: concern
    category: performance-nfr
    summary: "The `/api/v1/status` bound comes from the CLI timeout, and the block runs a full-history cross-tier check on every request"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:248-257"
  - id: F005
    severity: concern
    category: error-handling
    summary: "Failure handling for the new read paths is only partly enumerated"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:195-212"
  - id: F006
    severity: concern
    category: under-specification
    summary: "`FamilyStatus` and `FamilyReport` are specified inconsistently, and `traded_vs_cleared` re-judges sessions the report already judged"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:94-102, 170, 188"
  - id: F007
    severity: concern
    category: proportionality
    summary: "Part of the cross-tier and edge machinery has no consumer yet"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:153, 181-189"
  - id: F008
    severity: note
    category: dependencies
    summary: "304 is a listed prerequisite but is not in the frontmatter dependencies"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:77"
  - id: F009
    severity: note
    category: operations
    summary: "Missing sessions stay `INFO` after acquisition finishes"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md:195-210"
---

# Review: slice — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] D1 departs from "extends COVERAGE_SOURCE_TABLE" and records why

The slice plan says 305 "extends `COVERAGE_SOURCE_TABLE`". The architecture says only that the new tiers need "their own definition of source edge and caught up", extended "in place; no parallel set". `FAMILY_SOURCE_EDGE` meets that. It keys on `SchemaFamily`, with a pin test against `FAMILY_LEDGER`. It also keeps the architecture's rule that completeness never comes from an aggregate, which an entry in the coverage-aggregate map would have broken. The slice plan wording is read in the open and the reason is recorded. That is the right handling.

### [PASS] Plan-assigned scope is covered and boundaries hold

Health, accounting, overview, the status block, the cross-tier layer with `ohlcv-1s` inactive, and the manifest-scale measurement are all present. The exclusions are sound: 306's serving and parity work, the `ohlcv-1s` schema, and `/api/v1/health` and `/api/v1/overview`. The dependency direction is correct: surfaces call `family_reports`, which calls scope, reads and `volume_check`. Nothing flows back, and no cycle appears among `family_edges`, `tick_status_scope` and `family_report`.

### [CONCERN] The statistics source edge is defined through "the tick-tier edge", which can falsely report caught up

The D2 table defines the tick-tier edge as `min(want end, dataset available end)`. It defines the `STATISTICS` edge as the last session whose publication window ends "at or before the tick-tier edge". It also says `BAR_1M` is "same as the tick tier".

The want end is per family. Production's tick want is only the held window, ending 2024-12-31. Its statistics want runs to 2026-11-01, and ES `ohlcv-1m` covers 2024 only. Read literally, the statistics and bar edges would be capped at the tick family's want end. Statistics sessions from 2025 on would then be `pending` and excluded from the judged set. `caught_up` would come out true with 22 months missing. This is exactly the failure the slice exists to prevent.

The Data Flow signature `source_edge(family, want_end, dataset_edge, calendar)` suggests the intent is each family's own want end. State it that way. The statistics rule should be built from `dataset_edge` and the statistics want end. It should not reference the tick family's edge. Add a pin test where tick and statistics want ends differ. Success criterion 2 currently pins only the unchanged tick and bar fixtures.

### [CONCERN] The `/api/v1/status` bound comes from the CLI timeout, and the block runs a full-history cross-tier check on every request

The bound is "≤ 5 s added latency … half the composed statement timeout". That timeout belongs to the operator CLI path. The API block runs the same `family_reports`, including `traded_vs_cleared` (D4 line 188). That check covers every checkable session of the range, once per volume family, and reads resolved cleared volume across 19.4 M rows. It would run on every `/api/v1/status` call. trading-ui and trading-engine consume this endpoint, and 231 set 0.25 s for the tick block. The slice relaxes that by 20× at full scale without tying 5 s to what those consumers need.

The architecture states no numeric NFR here, so the slice has to choose one with a consumer-based reason. Either:
- justify 5 s against how often the API consumers poll, or
- keep the API block off the full-range cross-tier read, for example by running `traded_vs_cleared` only in health, accounting and CLI, or by bounding it for the API.

D10 correctly refuses stored derived state in this slice. That makes the choice of what the API computes per request more important, not less.

### [CONCERN] Failure handling for the new read paths is only partly enumerated

Covered cases:
- whole tick database unreachable, in health (D5 line 212);
- tick outage in overview (D7);
- no dataset edge giving `caught_up: None` in status (D2).

Missing cases:
- **Statement timeout partway through.** The new reads run under the 10 s composed timeout, and the D9 table says a statement over 10 s turns "the line into 'unavailable'". Nothing says what health, accounting or the API do if `family_reports` succeeds for ES and times out on GC statistics, or if `volume_check` alone times out. A health row of `OK` or `INFO` built from partial results would be a silent fallback, which the project rules forbid. Say that the affected row and API record are `unavailable` and name the failed read. State whether an unavailable row counts toward the health exit code.
- **`caught_up: None` in health.** The D5 severity table has no state for `None`, whether from no edge or no wanted range. Its `INFO` row reads "not caught up and no defect", which does not describe `None`. The D8 docs define `null`, but the health row for it is unspecified.
- **Concurrent ingest.** Reads are taken during a running pass. State whether the per-surface read is one snapshot or whether a unit changing between the family reads is tolerated.

### [CONCERN] `FamilyStatus` and `FamilyReport` are specified inconsistently, and `traded_vs_cleared` re-judges sessions the report already judged

The Component Structure says `FamilyStatus` gains `source_edge`. D3 says `FamilyReport` "is today's `FamilyStatus` … plus `source_edge`, `records`, `cross_tier`". It is unclear whether there are two types or one, and which one `status` renders. Pick one: either a new type that wraps `FamilyStatus`, or `FamilyStatus` extended in place.

The data flow's step 2 judges sessions once through `family_scope`. Step 5 then calls 303's `checkable_sessions` "unchanged". That function re-runs `statistics_verdicts` and `session_verdicts` over the sessions for each volume family, as the code shows. So every product pays two or three extra full-range per-day judgement passes. This undercuts the claim that "no surface reads anything the report does not". It also lands on the path the measurement exists to protect. Either pass the already-computed verdicts into the check, or record that the extra reads are known and measured.

### [CONCERN] Part of the cross-tier and edge machinery has no consumer yet

The slice plan assigns "`ohlcv-1s` summed to minutes … wired but inactive". Registering the inactive entry with its reason fits that. Its status, accounting and API reporting is also in scope, since the architecture says an unavailable check must be visible.

The rest is built ahead of need:
- **`minute_sum_verdicts`.** It is written, with perturbation tests, before `ohlcv-1s` has a schema, reader or row format. The `second_bars` and `minute_bars` input shapes are guesses, and the `ohlcv-1s` slice will probably reshape them. The fixture is a 1s series derived from the same `tbbo` day as the 1m series, so agreement is close to tautological. It cannot check provider semantics, and the slice excludes that comparison (306). The architecture also prefers real DBN data over derived fixtures. Defer the function and fixtures to the `ohlcv-1s` slice, which has the real data.
- **Registry shape.** Two classes (`ActiveCheck` with a `families` predicate, and `InactiveCheck`) serve one active check and one inactive entry.
- **`EdgeRule`.** A dataclass with one `int` field would be a plain `Mapping[SchemaFamily, int]` today.
- **Extra measured items.** The recorded-only `coverage --schema statistics` timing and the optional production timing add effort without a bound or a decision attached.

If the `minute_sum_verdicts` work is cut, say that it moves to the `ohlcv-1s` slice. That way the deferral is explicit and not a quiet drop of plan-assigned scope.

### [NOTE] 304 is a listed prerequisite but is not in the frontmatter dependencies

The prerequisites list 304 as "merged", while the same entry says 305 does not need the purchase to have run. The frontmatter and the slice plan give `[302, 303]`, which matches the plan. Recast the 304 line as context for the production walkthrough only, so the prerequisite list and the frontmatter agree.

### [NOTE] Missing sessions stay `INFO` after acquisition finishes

D5 keeps "not caught up" informational because it is work not yet done. That holds before 307. After 307, a bought-but-missing session that is not a provider hole could still never turn health red. The gaps remain named in health, status and coverage, and 307 records them, so the architecture's "named finding" is met. Say in this design, or in 307's, that clearing the not-caught-up count after the acquisition is part of 307's exit criteria.

### Run Digest

- Response length: 10275 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 11
- Tokens — prompt / cached / cache write / completion / reasoning: 718660 / 596779 / 121863 / 12375 / not computed
- Duration: 107.5 s
- Cost: $0.67 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
