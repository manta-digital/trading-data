---
docType: review
layer: project
reviewType: slice
slice: serving-the-new-tiers
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/306-slice.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: e9423f1d781dbe192fcf66c299cb302be65d835e
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
effort: high
turns: 7
promptTokens: 512131
cachedTokens: 403995
cacheWriteTokens: 108124
completionTokens: 9506
durationSeconds: 86.3
costUsd: 0.56875
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Bar-source rule, named source, parity-only tick bars and 1h/1d check match the architecture"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md#Overview"
  - id: F002
    severity: concern
    category: scope-creep
    summary: "Real purchase, throwaway database and paired cut go beyond the architecture's spend statement and the slice plan entry"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:273-282"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Statistics read has no enforced bound and its default range fails"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:235-243"
  - id: F004
    severity: concern
    category: failure-modes
    summary: "Parity command and the D10 bound are under-specified as new I/O paths"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:251-261, 284-288"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Proportionality: the `get_plan` signature and the granularity rename are cuttable"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:188-212"
  - id: F006
    severity: note
    category: correctness
    summary: "Close-stamped provider bar becomes a one-minute stub bucket at 1h, 15m and 5m"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:453"
---

# Review: slice — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Bar-source rule, named source, parity-only tick bars and 1h/1d check match the architecture

- Provider `ohlcv-1m` is the only source for 1m and for the rolled-up coarser sizes.
- Ticks never produce served time bars, including where no provider bar is held.
- The tick-derived one-minute SQL survives only as the parity check, as the "Never splice tiers silently" principle and "Which source serves bars" require.
- Every bars response names its source schema.
- D1 records the verification of `ohlcv-1h`/`1d`/`eod` bucketing that the slice plan asked for at design time.
- Statistics are served through routes (D6), not left as an open decision.
- Reads go through 303's `resolved` query and the verdict readers, so dependencies point from serving down to the stored tiers.

### [CONCERN] Real purchase, throwaway database and paired cut go beyond the architecture's spend statement and the slice plan entry

- The architecture says "Only the purchase test spends money before a subscription, apart from the $0.05 statistics window slice 303 buys". D9 adds a third spend, a Databento `ohlcv-1m` purchase.
- The slice plan entry for 306 assigns no purchase. The architecture's purchase test is 304.
- The mechanism is heavy for what it buys:
  - a throwaway database and a scratch archive,
  - a wrapper universe,
  - a `get_cost` check and a spend-guard interaction,
  - `adopt` of the held `tbbo` files,
  - a new paired-cut mode in `cut_tick_fixtures.py`,
  - a `SOURCES.md` entry.
- The slice already schedules the authoritative parity measurement for Phase 7, over the full ES window once 304's bars are ingested.
- It also says Phase 6 does not depend on 304.
- Cut one of these:
  - Use the DBN sample files and the existing derived bar day for the reader and rollup tests. Take the first parity measurement and any purchased fixture from 304's ingested file in Phase 7.
  - Keep D9, and amend the architecture's spend statement so the slice is not silently ahead of its parent.

### [CONCERN] Statistics read has no enforced bound and its default range fails

- D6 makes one `resolved` call over `[first, last]` of the planned sessions and then pivots in Python. It says rows "are counted against the same row ceiling". It does not say whether the ceiling aborts the read or only runs after the rows are materialised.
- The bars path uses `read_series` with `on_batch` to stop early, so the two are not equivalent.
- The product statistics route has no roll schedule, so the 1,300-session `TICK_ROLL_MAX_SESSIONS` cap does not apply to it. Nothing else bounds the range before the read.
- Omitted bounds default to all held statistics sessions. The doc's own figure is that 75,000 rows is about 12 years of GC at about 25 outrights per session. A bare request for GC will therefore return 422 once full history is held.
- A default that is guaranteed to fail is a contract defect.
- Specify:
  - where the ceiling is enforced for statistics, preferably an upper-bound session count or a count before the read,
  - a default range that fits under the ceiling, or an explicit rule that the bounds are required on the product route.

### [CONCERN] Parity command and the D10 bound are under-specified as new I/O paths

- The API routes list their failure statuses (404, 422, 503, 504). `mt data tick parity` says only "the usual error codes otherwise". Parity runs one tick statement and one bar statement per window. It has no stated handling for a statement timeout or an unreachable database mid-run.
- It also says nothing about whether a partial per-session report is printed before a failure. That decides whether an exit code 3 finding can be confused with an aborted run.
- D10's pass bar is "under half of the API's gateway timeout", with no value. Phase 6 cannot decide pass or fail against an unnamed number.
- State the timeout in seconds, or point at the setting that holds it.
- Give the parity failure handling explicitly: exit code, partial output, and whether the run is retryable.

### [CONCERN] Proportionality: the `get_plan` signature and the granularity rename are cuttable

- D3 adds both `family` and `verdicts_of` to `contract_plan` and `product_plan`. The caller table shows the verdict reader is determined by the family. Two parameters that must agree invite mismatched pairs, for example `BAR_1M` with `statistics_verdicts`. Derive the reader from the family inside the plan module. If `calendar_url` is the only reason for the callable, pass that instead.
- D4 renames `TickBarGranularity` to `FuturesBarGranularity`, which changes a published OpenAPI component name. It does so for no current consumer, and the slice plan entry does not assign it. The stated reason is that "tick bar" will mean an N-trade bar from 308. Defer the rename to 308, where the ambiguity first appears. That avoids a contract change and CHANGELOG entry now.
- The 4h re-anchoring (D5, issue #47) is consistent with the architecture's session rollup rule and can stay.

### [NOTE] Close-stamped provider bar becomes a one-minute stub bucket at 1h, 15m and 5m

- The window is closed, so the bar stamped at `close_ns` is served. With buckets anchored at the open, that bar sits alone in a bucket whenever the session length is a multiple of the bucket width.
- A normal 23-hour session gives a one-minute 1h bar, and the same happens for 15m and 5m.
- The doc accepts this until issue #70 is answered, and a fix is a `locate_ns` change outside this slice. The behaviour is not covered by a success criterion or a CHANGELOG line.
- Add it to the CHANGELOG and `reference.md` conventions text so consumers do not read the stub bar as a data fault.

### Run Digest

- Response length: 6761 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 512131 / 403995 / 108124 / 9506 / not computed
- Duration: 86.3 s
- Cost: $0.57 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
