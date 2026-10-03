---
docType: review
layer: project
reviewType: slice
slice: proof-on-existing-data
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/226-slice.proof-on-existing-data.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261003
dateUpdated: 20261003
reviewedSha: dd398ea8ea8493aea5df19169480dee775453975
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
durationSeconds: 56.5
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Contention run has no abort criteria or interruption handling on a production host"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 7: contention is measured against the Kalshi pass"
  - id: F002
    severity: concern
    category: cost-guard
    summary: "Proof-database `pass` can spend money invisibly to the 30-day cap"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Consumes from Other Slices"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Other new I/O and failure paths are not enumerated"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 2: one harness, one report per step, in the proof database"
  - id: F004
    severity: concern
    category: architecture-alignment
    summary: "Space partitioning is rejected in the design, ahead of the measurement the architecture assigns it to"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 4: physical grouping, from two measured layouts"
  - id: F005
    severity: note
    category: architecture-alignment
    summary: "Mixed-tier state is left standing in one table"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 9: the go/no-go document, and ES's tier and range"
  - id: F006
    severity: note
    category: scope
    summary: "Documented deviations from the architecture are PM-directed and recorded"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Architecture and plan statements this design supersedes"
  - id: F007
    severity: note
    category: scope
    summary: "The bad-header fix in 225's code is scope-adjacent but justified"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 8: a bad file header fails the unit, not the pass"
  - id: F008
    severity: note
    category: documentation
    summary: "Frontmatter `dependencies` omits prerequisites the body relies on"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md:6"
  - id: F009
    severity: pass
    category: nfr
    summary: "Throughput NFR restated with specific targets"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 3: what is measured, and what passes"
  - id: F010
    severity: pass
    category: architecture-alignment
    summary: "Layering, dependency direction and the realtime-paths check are respected"
    location: "project-documents/user/slices/226-slice.proof-on-existing-data.md#Technical Decision 10: what this changes outside the proof"
---

# Review: slice — slice 226

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Contention run has no abort criteria or interruption handling on a production host

The `contention` step loops reset → ingest against the proof database across three Kalshi firings, on the production host. The architecture's isolation goal says tick trouble must never reach production. The Risk section notes `Committed_AS` was 62.7 GiB against a 66.4 GiB `CommitLimit`. The step only records data. It names no stop condition if any of these happen:
- `MemAvailable` falls below a floor.
- An overlapped Kalshi run exceeds some hard multiple of its 324 s maximum, or hangs.
- The harness process dies mid-loop. The text does not say whether the loop, its connections or its ingest workers are torn down.
- The Kalshi timer never fires because it is disabled or has moved. The step has no wait timeout.

"Measurable contention" is only a post-hoc verdict, not a guard. Each new I/O path needs an explicit handling strategy: add abort thresholds, a deadline on the wait, and a guaranteed-teardown rule for the loop.

### [CONCERN] Proof-database `pass` can spend money invisibly to the 30-day cap

The architecture sums the rolling 30-day cap from the manifest. The harness runs `rebuild` with a real `pass` in `trading_tick_proof`, which has an empty manifest and is dropped at the end. If a definition job is missing, the slice says `pass` plans a purchase under the $0.50 ceiling. A purchase recorded only in the disposable database never reaches the production manifest, so the cap understates committed spend. Also, "Nothing is bought" and "Every provider call … is free metadata" contradict this fallback path. Run `pass` in the proof database as `--estimate-only`, or with the spend ceilings forced to 0 or unset (which the architecture says refuses purchase), so a purchase can only happen in `trading_tick`. Alternatively, state that any proof-database purchase is reconciled into the production manifest.

### [CONCERN] Other new I/O and failure paths are not enumerated

Only the provisioning script has explicit failure handling (port, disk, version checks, re-runnable). Several other paths are left implicit:
- `jobs` and `estimate` call the provider's free metadata API. Timeout, auth or outage behaviour is unstated.
- A step that dies mid-run (for example `layouts` between compress and decompress) leaves the proof database in an intermediate state. Whether re-running is safe is not said.
- Production `final` compression fails or is interrupted partway.
- The tick cluster is down or restarting, for example after the memory re-run.
- Production rebuild interruption: `adopt` is all-or-nothing and ingest is per-unit, so it is probably resumable, but the walkthrough never says to re-run.

Add a short failure-mode table (hang, timeout, peer disconnect, partial completion) with a handling strategy for each.

### [CONCERN] Space partitioning is rejected in the design, ahead of the measurement the architecture assigns it to

The architecture says physical grouping, space partitioning and compression layout together, is "one class of decision … made once, from the proof's measurements". The slice decides space partitioning in the design doc, citing the tool guide and 225's observed skew, and then records measured skew afterward as "the evidence". The reasoning is plausible, but the decision precedes the data. Either make the `size` step's skew measurement an explicit gate that can reopen the decision, or note that this is a deliberate narrowing of the architecture's wording and add it to the Revision Log.

### [NOTE] Mixed-tier state is left standing in one table

Setting ES's `TICK_UNIVERSE` to `tbbo` over 2024-11-01 → 2025-01-01 leaves the `trades` Aug–Sep units loaded outside the wanted range. The architecture says tiers are rarely mixed and builds no tier-mixing features, only "one tier field". How `status` and `coverage` treat loaded units outside the configured range is not stated. Criterion 2 requires `coverage` `ok` on both ranges, but criterion 8 is scoped to the configured range. State the expected status output for the orphaned range, or say it is intentionally out of scope.

### [NOTE] Documented deviations from the architecture are PM-directed and recorded

The slice replaces minute-pass contention with Kalshi-pass contention (PM, 2026-10-03). It also replaces the first-submitted-job timing with account job records. The "Cross-source arbitration" text and the Anticipated Slices entry still say minute-pass. The Revision Log update is listed under Docs, which keeps this on track. It should name the Cross-source arbitration paragraph and the Proof entry in Anticipated Slices explicitly. The architecture's own steady-state slice should also inherit the minute-overlap measurement (the slice assigns it to 232).

### [NOTE] The bad-header fix in 225's code is scope-adjacent but justified

The fix touches `dbn_file.py` and `verify.py`, which belong to the 225 pass. It is small and independent, and it protects the harness from aborting. It also preserves the architecture's unit-grain failure model, and keeping the `iter_batches` configuration error fatal is correct. It is acceptable, and the design says to do it first.

### [NOTE] Frontmatter `dependencies` omits prerequisites the body relies on

`dependencies: [225]` is listed, but the Prerequisites section also relies on 223, 224 and 923. The `interfaces` list (227, 228, 231, 232) omits 229, 230 and 233, which the Integration Points section names as consumers. List them all so the dependency graph tooling reads the document correctly.

### [PASS] Throughput NFR restated with specific targets

The architecture's ingest-throughput pass/fail (a day's sessions far faster than a day of market time, and a month within an operator's working session) is restated as concrete bounds. The slowest unit must take ≤ 120 s against 24 h of market time. The 78-unit proof set must ingest in ≤ 2 h. The worker, batch and checkpoint decisions hang off those bounds. The query bounds (Q1–Q4 ≤ 1 s, planning ≤ 50 ms) are slice-level targets the architecture does not constrain.

### [PASS] Layering, dependency direction and the realtime-paths check are respected

The slice keeps the tick → production dependency direction. It only reads `pass_runs` and the calendar from production, and `pg_stat_database` read-only. It adds no new `mt` surface (the harness is a script), and it imports nothing across sources. It carries the required realtime-paths check and the Kalshi contract diff as a named task, and it stays inside the architecture's decided placement (second cluster, TCP by host name, matching PostgreSQL and TimescaleDB versions, own memory settings). Credentials come from `.env` with a mode test, and the destructive-statement guard follows the project's database protection rule. Backup stays with 227, which matches the architecture's deferral.

### Run Digest

- Response length: 8900 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 56.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
