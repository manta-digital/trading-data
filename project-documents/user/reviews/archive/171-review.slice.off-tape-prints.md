---
docType: review
layer: project
reviewType: slice
slice: off-tape-prints
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/171-slice.off-tape-prints.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261015
dateUpdated: 20261015
reviewedSha: 94bfff72a1fa0d1702b862bd8a88ed1efa5721c0
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
turns: 8
promptTokens: 475500
cachedTokens: 362158
cacheWriteTokens: 113332
completionTokens: 9043
durationSeconds: 72.5
costUsd: 0.580733
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Reconciliation with the 120 \"no outlier removal at storage time\" rule"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Special Considerations"
  - id: F002
    severity: concern
    category: architectural-alignment
    summary: "Parent architecture (140) is contradicted but not amended"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Technical Scope (item 8)"
  - id: F003
    severity: concern
    category: nfr
    summary: "NFR-bearing paths are touched without restating their targets"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#The swap: build beside, then rename"
  - id: F004
    severity: concern
    category: correctness
    summary: "Insert-path detector sees different context than the sweep"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Data Flow"
  - id: F005
    severity: concern
    category: under-specification
    summary: "Eligible rollups can emit NULL-price rows, and volume differs between modes"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Rollup SQL (verified on the test cluster, TimescaleDB 2.29.1)"
  - id: F006
    severity: concern
    category: error-handling
    summary: "Failure modes of the new operational paths are not enumerated"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Sweep mechanics"
  - id: F007
    severity: concern
    category: scope
    summary: "Proportionality: the slice is overweight, and the split is left as a hedge"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Development Approach"
  - id: F008
    severity: pass
    category: architectural-alignment
    summary: "Design deviates from the plan's leading candidate, with justification"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Eligible bar values: clamp into what the bar's own eligible values allow"
  - id: F009
    severity: pass
    category: architectural-alignment
    summary: "Provider independence, raw immutability and database safety"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md#Patterns and Conventions"
  - id: F010
    severity: note
    category: dependencies
    summary: "Dependency list is incomplete"
    location: "project-documents/user/slices/171-slice.off-tape-prints.md:6"
---

# Review: slice — slice 171

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Reconciliation with the 120 "no outlier removal at storage time" rule

Raw `open/high/low/close/volume` stay immutable, with a per-chunk checksum success criterion. Marks are annotations in the "validation-flags" home the non-goal names. Serving defaults to eligible values, and raw stays available at 1m. The amendment is explicit, and the design says what stays forbidden (storage-time removal). Its justification is that the exchanges' own tape rules exclude these prints, not a strategy's opinion. It also carries a PM sign-off gate, which is still open.

### [CONCERN] Parent architecture (140) is contradicted but not amended

Item 8 amends only 120's non-goal section. The 140 architecture document is authoritative for the post-152 schema, and this slice changes it in three places:
- **Continuous aggregates:** 140 says "7 caggs … all projecting raw OHLCV". After this slice the four minute caggs project tape-eligible values.
- **Read API:** 140 specifies `get_minute_data(adjusted=True)`. The slice adds a `prints` choice, so the read contract changes.
- **`mt data caggs verify`:** 140 describes it as the source-parity detector. It compares only `SUM(minute_count)`, so it cannot detect a wrong eligible definition. Criterion 5 is the only guard, and it is a one-time check.

140 records every comparable change as a dated "Architecture amendment". Add the amendment to this slice's scope, and state what standing check replaces parity for value correctness.

### [CONCERN] NFR-bearing paths are touched without restating their targets

Migration 064 drops and recreates `data_status` and `minute_coverage`, and then `mt data caggs rebuild-coverage` rematerializes coverage. Parent 140 states several targets for this path:
- the full-universe `data_status` read, now a no-regression margin against the 7.8 s raw scan and tracked by `test_167_data_status_nfr.py`;
- the freshness-probe ceiling of 25% of 10 s;
- the per-view `COVERAGE_BUCKET_LAG_BUDGET`.

None of these is restated, and no success criterion re-runs them after the swap. Criterion 6 and the Integration Requirements only say the counts read the same. Add the targets and a post-swap check. The new 1m `eligible` read path and `/api/v1/bars` gain extra column reads and clamp expressions, and no read-latency expectation is stated for them.

### [CONCERN] Insert-path detector sees different context than the sweep

The detector works on an ET day (04:00–20:00) with K bars on each side. The insert path runs it on "one symbol, whole UTC days" frames.
- In EST, 20:00 ET falls at 01:00 UTC the next day. The after-hours tail, where marks cluster, is therefore split across frames.
- Tail bars would be judged one-sided at insert and two-sided in the sweep.
- The design says "at the day's edges only the available side is used", but a frame edge is not a day edge.
- Newly fetched latest bars may have no "after" side at all.

The same bar can get different masks depending on the path, and the health check's marked-share bounds are measured against a mix of the two. Specify how the insert path gets context. Options include fetching neighbouring stored bars and aligning frames to ET days. Add a test that insert-path and sweep masks agree on the same bars.

### [CONCERN] Eligible rollups can emit NULL-price rows, and volume differs between modes

The `FILTER` clauses sit only on `open/high/low/close`, while `SUM(volume)` and `COUNT(*)` are unfiltered.
- A bucket holding only all-marked minutes still yields a row, with NULL prices and non-zero volume.
- The design says all-marked minutes are absent from eligible 1m responses. The API and `adjusted()` handling of NULL-price rollup rows is not specified.
- Criterion 5 (eligible 1m rolled up equals the 5m rollup) then holds for prices but not for volume. The 5m bar includes volume the eligible 1m response omits.

State the rule: drop NULL-price buckets in the read path, or define the volume contract for eligible mode. Add a fixture with an all-marked minute inside a bucket and one with a bucket made only of such minutes.

### [CONCERN] Failure modes of the new operational paths are not enumerated

Sweep and cutover are new I/O paths. Only some of their failures are handled.
- **Sweep lock contention:** it holds a chunk lock while it decompresses, updates and recompresses. Oldest-first order eventually reaches the chunk the live minute pass inserts into. The design says writers "wait", with no `lock_timeout`, no statement timeout, and no stated behaviour for the acquisition pass if it blocks or times out. Backfill hanging on a held chunk lock is exactly the failure to name.
- **Cutover script crash or abort:** it stops the minute timer, applies 064, runs `rebuild-coverage`, then restarts the timer. The design does not say what happens if 064 fails or `rebuild-coverage` fails. It does not say whether the timer restarts, or whether the failure leaves the seeder running against missing or half-built coverage. Nor does it say whether 064 is one transaction, given the drop and rename of caggs and policies.
- **Worker failure:** with `OFF_TAPE_SWEEP_WORKERS`, one worker's failure is not specified (fail fast, or continue and report).

Add a short failure table: trigger, effect and handling for each.

### [CONCERN] Proportionality: the slice is overweight, and the split is left as a hedge

The slice bundles the detector, a full-history rewrite of an 82 GB hypertable, a parallel sweep, three migrations, a four-cagg build-and-swap, a cutover script, a repair-loop change, API changes, health checks and documentation. The design itself says "if this does not fit one session, split it there".
- **The split is unresolved.** The slice plan assigns the rollup rebuild to this slice (scope item 2). Deferring it silently would drop assigned scope. Leaving the choice open also leaves task breakdown without a stable boundary. Decide now, with the PM, whether to re-plan into two slices.
- **Open and close marking beyond the plan.** The plan's scope concerns the off-tape high and/or low. Marking open and close adds the four-bit mask, the clamp rule, the all-marked exclusion, and the NULL-bucket issue above. The design justifies it by 722 all-four-marked bars (0.02%). If a high/low-only mask meets the requirement, it is the lighter option.
- **Parallel workers.** `OFF_TAPE_SWEEP_WORKERS` and its configuration may be deferred until a single-process sweep proves too slow. The single-core detector figure alone does not establish that.
- **Sections that restate other documents.** The long evidence section and the Verification Walkthrough (steps 6 and 7 repeat criteria) could be shortened.

### [PASS] Design deviates from the plan's leading candidate, with justification

The plan suggested using the marked bar's body. The design instead clamps each value into the range of the bar's own eligible values. This needs nothing outside the bar, so it can live inside a cagg definition. It preserves the eligible-1m-rolled-up equals eligible-5m invariant, and it rejects inventing neighbour-median prices. The reasoning is explicit and consistent with the rollup constraints that were verified on the test cluster.

### [PASS] Provider independence, raw immutability and database safety

The detector is a pure function on bars, so a switch to Databento drops in. Bit values, modes and thresholds are defined once in `constants.py`, with the SQL rendered from them. Detector failures at insert fail the symbol's write with `logger.exception` and no unevaluated inserts. The sweep takes the maintenance URL as an explicit argument rather than reading the environment, and the tests use throwaway databases. These all match the project's rules.

### [NOTE] Dependency list is incomplete

The frontmatter lists `[163, 168, 169]`, but the body also consumes slice 921 (the "last collected session" definition for the health checks). It also depends on release 0.23.2's migration 061 constants. The plan entry lists only 163 and 168. Add 921 to the dependencies, and note the 169 and 0.23.2 additions in the plan.

### Run Digest

- Response length: 9964 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 8
- Tokens — prompt / cached / cache write / completion / reasoning: 475500 / 362158 / 113332 / 9043 / not computed
- Duration: 72.5 s
- Cost: $0.58 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
