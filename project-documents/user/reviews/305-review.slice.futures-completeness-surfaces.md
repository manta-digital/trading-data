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
reviewedSha: aefac6e5f2f0628749b66eb2311924547760b04d
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
effort: high
turns: 7
promptTokens: 518430
cachedTokens: 406167
cacheWriteTokens: 112251
completionTokens: 6789
durationSeconds: 62.2
costUsd: 0.558306
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Surfaces extended in place through one shared report"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D3 — One per-family report, used by every surface"
  - id: F002
    severity: pass
    category: architecture-alignment
    summary: "Deviation from \"extends COVERAGE_SOURCE_TABLE\" is justified and consistent with the architecture"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D1 — COVERAGE_SOURCE_TABLE gets no futures entries"
  - id: F003
    severity: pass
    category: error-handling
    summary: "Failure modes and the health severity split are specified"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D5 — Health: one row per product and family, counted only for defects"
  - id: F004
    severity: pass
    category: nfr
    summary: "Manifest-scale NFR is restated with specific, fixed bounds"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D9 — The manifest-scale measurement"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Design carries speculative and contingent mechanism that can be cut or deferred"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D10 — A missed bound is fixed in this slice"
  - id: F006
    severity: concern
    category: scope
    summary: "The plan's \"wired but inactive\" ohlcv-1s check is reduced to a name and a reason string"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D4 — The cross-tier checks"
  - id: F007
    severity: note
    category: integration
    summary: "Statistics `caught_up` semantics change is in scope but touches 303 behaviour"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D2 — Source edge and caught up per family"
  - id: F008
    severity: note
    category: error-handling
    summary: "Health's counted-FAIL-on-timeout rule ties a health verdict to query performance"
    location: "project-documents/user/slices/305-slice.futures-completeness-surfaces.md#D5 — Health: one row per product and family, counted only for defects"
---

# Review: slice — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Surfaces extended in place through one shared report

The architecture says the slice that adds a tier extends the surfaces "in place; no parallel set". D3 does that. `_families` is extracted into `family_reports`, and status, health, accounting and the API all call it. Surfaces never call `family_scope` directly, so the four cannot disagree. Success criterion 3 tests this by building all four from one seeded database. Dependencies point from the CLI and API layers into `data/tick`, and `constants.py` only gets a docstring change, so layer boundaries hold.

### [PASS] Deviation from "extends COVERAGE_SOURCE_TABLE" is justified and consistent with the architecture

The slice plan says 305 "extends `COVERAGE_SOURCE_TABLE`". The architecture only requires that "tick and the new tiers need their own definition of source edge and caught up". It also says completeness never comes from an aggregate. `COVERAGE_SOURCE_TABLE` keys on coverage caggs in the market database, so a futures entry would have nothing to key on. D1 records the reading, adds `FAMILY_PUBLICATION_LAG` with `source_edge()`, and pins `FAMILY_PUBLICATION_LAG.keys() == FAMILY_LEDGER.keys()`. The deviation is explicit and the architecture goal is met.

### [PASS] Failure modes and the health severity split are specified

- **Failure modes:** each new read path (health, accounting, API, overview) has a stated outcome for a database that doesn't answer, a statement timeout partway, and a concurrent pass or ingest. The error propagates through a single `futures_completeness` call. `ReadProgress` names the failing step, and no row is built from partial results.
- **Severity table:** defect versus work-not-done is stated for every case. This matches the architecture's "a gap is a named finding" and keeps health actionable.
- **Connection claims:** I checked these against the source. The composed connection is autocommit with a 10 s statement timeout, and `read_tick`'s `_reset` clears the isolation level. The design's claims about both hold.

### [PASS] Manifest-scale NFR is restated with specific, fixed bounds

The architecture requires one manifest-scale measurement before history acquisition. D9 gives:
- the full shape (about 30,000 units, 19.4 M statistics rows, compressed chunks);
- five operations, each with a numeric bound and a rationale;
- 231's 0.25 s bound for the API block, held unchanged;
- a 2× margin against the 10 s timeout;
- a recorded note and a guarded throwaway database.

D10 says a missed bound is fixed in this slice and never raised.

### [CONCERN] Design carries speculative and contingent mechanism that can be cut or deferred

The slice is a 3/5 effort entry. The design is about 430 lines and restates 302, 303 and 231 behaviour in several places. Three things are heavier than the requirement needs:
- **D10's pre-specified `/api/v1/status` cache.** This is a process-local 60 s cache for a bound miss that has not happened. D10 already says a miss is fixed by a query rewrite or an index. The cache adds a stale-completeness window to an API that trading-ui and trading-engine use to decide whether a range is usable. Cut it and decide only if the measurement actually misses 0.25 s.
- **`REPEATABLE READ` snapshot.** D5 adds an isolation-level change and a first-ever transaction on two connection types. It covers a mid-read ingest that, at worst, changes one informational figure for one read. The architecture does not ask for cross-product snapshot consistency. A plain read-only pass is the lighter option. If it stays, the design should name the requirement it meets.
- **The `ReadProgress` step recorder in `futures_completeness`.** It is a new threaded-through object, used only to decorate an error string. Naming the product and family from the exception context would be lighter. The cost is that it is a stated criterion-4 behaviour, so this is the weakest of the three cuts.

The overview lines (D7), the API `load_complete_only` field and the accounting JSON `families` list are small, and each traces to an architecture surface.

### [CONCERN] The plan's "wired but inactive" ohlcv-1s check is reduced to a name and a reason string

The 305 plan entry assigns "the remaining cross-tier content check (`ohlcv-1s` summed to minutes against `ohlcv-1m` is wired but inactive until that schema exists)". The architecture lists this check as part of content-completeness. D4 delivers only an `INACTIVE_CROSS_TIER_CHECKS` mapping entry that status and accounting print. It has no check function, no signature and no input contract. D4's reasoning is sound: no schema or row format exists, so a comparison now would only be tested on fixtures that agree by construction. But the plan assigned something more than a string, and the design does not record the reduction as a deviation.
- **Record it as a deviation.** Say so in the design, as D1 does for `COVERAGE_SOURCE_TABLE`.
- **Name what the later slice must provide.** State the input shape the comparison will take, so the replacement is a drop-in. Issue #71 is mentioned, but the contract is not.

I'd keep the deferral. It is a documentation gap, not a reason to build the check now.

### [NOTE] Statistics `caught_up` semantics change is in scope but touches 303 behaviour

Moving `_judged` and bounding it by the family edge changes `mt data tick status` for statistics. A session whose publication window is open is now `pending` and no longer counts against caught up. The design says this is not a pure refactor. It shows no existing 303 expectation is affected, and it adds pin tests, including the production case where the tick want ends in 2024 and the statistics want ends in 2026. Dependency direction is fine: 305 changes 303's consumer logic and no 303 interface. This is acceptable as designed.

### [NOTE] Health's counted-FAIL-on-timeout rule ties a health verdict to query performance

An indeterminate read counts as FAIL by the 168 D3 precedent, and the `tick database` row stays informational per 234. D5 states the difference and backs it with a bound: ≤ 5 s measured against a 10 s timeout. The operator response is also stated. That is coherent. The residual risk is that a cold cache or a loaded host in production trips the FAIL with no data defect. The bound is measured on a test cluster, not on production hardware.

### Run Digest

- Response length: 7777 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 518430 / 406167 / 112251 / 6789 / not computed
- Duration: 62.2 s
- Cost: $0.56 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8

## Resolution

- **F001 [pass] Surfaces extended in place through one shared report:** no change needed.
- **F002 [pass] Deviation from "extends COVERAGE_SOURCE_TABLE" is justified:** no change needed.
- **F003 [pass] Failure modes and the health severity split are specified:** no change needed.
- **F004 [pass] Manifest-scale NFR is restated with fixed bounds:** no change needed.
- **F005 [concern] Speculative and contingent mechanism:**
  - Cut the pre-specified `/api/v1/status` in-memory cache from D10. D10 now treats any stored or cached derived state as beyond the slice.
  - Cut the `REPEATABLE READ` snapshot. D5 now states that the read takes no snapshot across products and families, why that is acceptable, and that no requirement asks for it. The `tick_composed` and `tick_access` component lines are removed with it.
  - Kept `ReadProgress`. It implements success criterion 4, the timeout row that names its step. The lighter alternative, exception notes, would not reach the unavailable text that `read_line` and `read_tick` build.
- **F006 [concern] "wired but inactive" reduced to a name and a reason:** D4 now records this as a deviation from the slice plan. It names the contract the `ohlcv-1s` slice must provide: the signature, the sessions, the per-minute inputs and the result. Building the check stays deferred to issue #71.
- **F007 [note] Statistics `caught_up` semantics change:** no change. The review found it acceptable as designed.
- **F008 [note] Health's counted FAIL on timeout:** no change. D5 already states the 2× margin and the operator response. The production-hardware risk the review names is accepted, and a timeout FAIL after release is handled as a D10 miss.
