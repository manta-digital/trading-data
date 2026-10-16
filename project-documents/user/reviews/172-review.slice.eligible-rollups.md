---
docType: review
layer: project
reviewType: slice
slice: eligible-rollups
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/172-slice.eligible-rollups.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: 5a8267398a751bc977e3100b6879987661212c1c
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 10
turns: 11
promptTokens: 498408
cachedTokens: 405739
cacheWriteTokens: 92657
completionTokens: 4525
durationSeconds: 40.0
costUsd: 0.457216
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Alignment with the 140 architecture and slice plan entry"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#Technical Scope"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Read-path behavior during the swap window is not enumerated"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#Failure handling (cutover)"
  - id: F003
    severity: concern
    category: nfr
    summary: "NFR targets restated by reference, not with specific numbers"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#NFR targets touched by the swap"
  - id: F004
    severity: concern
    category: integration
    summary: "Freshness and status checks against `_next` views during phase 1 are unspecified"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#Interfaces Required"
  - id: F005
    severity: concern
    category: scope
    summary: "Value-parity check sample is narrow and depends on a constant not listed as a prerequisite"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#Standing value check"
  - id: F006
    severity: note
    category: proportionality
    summary: "Proportionality"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md"
  - id: F007
    severity: note
    category: integration
    summary: "Release sequencing of the default flip"
    location: "project-documents/user/slices/172-slice.eligible-rollups.md#Migration Plan"
---

# Review: slice — slice 172

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Alignment with the 140 architecture and slice plan entry

The design covers every item the 172 plan entry assigns: `_next` rollups, newest-first repair, the cutover script with a failure table, the eligible default with `prints=all` at 1m only, the value-parity check, the NFR re-runs and the 140 caggs amendment. Dependencies [171, 163, 168, 169] and interfaces [182, 186, 190] match the plan. The design keeps the arch's "caggs do not store adjusted prices" rule. It keeps `minute_count` counting every bar, so `verify`/`cagg_parity` (count parity) and coverage keep their meaning. Freshness stays asserted through slice 168's `assert_cagg_fresh`, as the arch requires. It consumes 171's `eligible_sql.py` and does not duplicate the renderer.

### [CONCERN] Read-path behavior during the swap window is not enumerated

Migration 064 drops `data_status`, `minute_coverage` and the four old rollups before the renames. The failure table covers the seeder and the timer. It does not say what the other consumers see: `/api/v1/bars` at 5m+, `mt data status`, and `/api/v1/status`. Success criterion 3 admits 5m+ is "absent only around 064 and the coverage rebuild", but the design never says whether those requests return an error or empty data. If 064 fails partway, the timer stays stopped and these consumers stay broken until someone reruns the script, so the outage can be long. Name the response (for example 503 with a clear message), the expected duration of the clean path, and whether the operator overview shows the outage. The arch rule that a stale verdict is reported rather than raised or silently presented as current applies here.

### [CONCERN] NFR targets restated by reference, not with specific numbers

The arch gives concrete bounds: a no-regression margin against the 7.8 s raw scan, with 2.636 s as the recorded absolute figure at the 7-day width, and `COVERAGE_BUCKET_LAG_BUDGET` entries of 8 d 4 h and 8 d 1 h. The slice restates them as "within the no-regression margin", "under the 25%-of-10 s ceiling" and "under their existing budgets". The bars-read targets are never named. The slice should state the pass threshold for each bar read (5m, 1h, 4h and the 171 eligible 1m case) so a re-run can pass or fail against a number. It should also say whether the 2.636 s figure is the baseline the data_status re-run compares against.

### [CONCERN] Freshness and status checks against `_next` views during phase 1 are unspecified

`_next` names are derived from `GRANULARITY_SOURCE`/`MINUTE_CAGG_GRANULARITIES`. The design does not say whether `mt data health`, `caggs status` and the freshness resolution (`_resolve_threshold` falls back to the generic formula for views without a `COVERAGE_BUCKET_LAG_BUDGET` entry) will see the `_next` views, or how they behave while those views are partly materialized (`WITH NO DATA`, history filling newest-first). A phase-1 `mt data health` failure on a view that is still materializing would teach operators to ignore it, which is the failure mode the arch amendments warn about. State explicitly that these checks exclude `_next` views until the swap, or that their staleness is expected.

### [CONCERN] Value-parity check sample is narrow and depends on a constant not listed as a prerequisite

The check uses `OFF_TAPE_VALUE_CHECK_SYMBOL` (SPY) over the last collected session. That constant and its provenance are not listed in the 171 prerequisites. One liquid symbol can pass while the rollup definition drifts only in the all-marked-minute or NULL-bucket paths. Those are the paths this slice added, and SPY may have none on the sampled session. Either confirm that 171 defines the constant, or state which sampled session and symbol are guaranteed to contain a marked bucket. The check is justified, but its power should be stated rather than implied.

### [NOTE] Proportionality

The mechanism count is high for effort 3/5: two migrations, a repair option, a cutover script, a health check, an API change and documentation across four runbooks and references. Each piece traces to a plan item or to the swap's safety requirement, so the design is proportionate. The "Rejected: keeping raw rollups beside eligible ones" paragraph restates a decision rather than the design and could be trimmed. The "Excluded: daily-family rollups" bullet is already implied by the minute-only scope.

### [NOTE] Release sequencing of the default flip

The plan orders the API default flip (step 4) after the swap (step 3), which is correct. Before step 3, `prints=eligible` at 5m+ would return raw-rollup values. The design should keep the release gate that the flip ships only after verification of step 3, and not only note that steps 3 and 4 "can ship in one release".

### Run Digest

- Response length: 5674 chars
- Response is newline-free: no
- Tool calls made: 10
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 11
- Tokens — prompt / cached / cache write / completion / reasoning: 498408 / 405739 / 92657 / 4525 / not computed
- Duration: 40.0 s
- Cost: $0.46 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
