---
docType: review
layer: project
reviewType: tasks
slice: long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-1.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260503
dateUpdated: 20260503
findings:
  - id: F001
    severity: concern
    category: testing
    summary: "Missing load-test tasks for NFRs restated in the slice design"
    location: unverified
  - id: F002
    severity: concern
    category: consistency
    summary: "T13 and T19 contradict each other on cycle function signature changes"
    location: src/manta_trading/data/acquisition/daemon/daily.py
  - id: F003
    severity: concern
    category: error-handling
    summary: "T17 `ca_update_due()` does not specify behavior when sentinel row is absent"
    location: src/manta_trading/data/acquisition/daemon/runner.py
  - id: F004
    severity: concern
    category: error-handling
    summary: "No task covers EODHD 429/retry-after defensive handling from Failure Modes"
    location: unverified
  - id: F005
    severity: pass
    category: testing
    summary: "Test-with pattern is consistently followed"
    location: unverified
  - id: F006
    severity: pass
    category: process
    summary: "Commit checkpoints are well distributed, not batched at end"
    location: unverified
  - id: F007
    severity: pass
    category: process
    summary: "Task sequencing respects dependency chains with no circular dependencies"
    location: unverified
  - id: F008
    severity: pass
    category: completeness
    summary: "CA-drift detection tasks comprehensively cover SC8 and SC9"
    location: unverified
  - id: F009
    severity: note
    category: completeness
    summary: "Part 1/2 split defers several success criteria by design"
    location: unverified
---

# Review: tasks — slice 146

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] Missing load-test tasks for NFRs restated in the slice design

The slice design restates several non-functional targets — throughput (~90s API time for SPY backfill), memory (RSS < 500 MB), SIGTERM-to-exit latency (≤ one symbol's processing time), token bucket overhead (< 1ms per `consume()`), and list resolution latency (< 100ms). None of these have a corresponding task creating a load test in `tests/load/`. The evaluation criteria require that when the parent slice restates an NFR, a load test task exists (or the breakdown explicitly adds one). SIGTERM latency is partially exercised by T20's integration test, but not as a load/perf benchmark; the others have no coverage at all. Part 2's listed scope (`mt data ca` CLI, `mt data daemon run` CLI, legacy deletions, closeout) does not appear to include load tests either.

### [CONCERN] T13 and T19 contradict each other on cycle function signature changes

T13 states: "No signature changes to `run_daily_cycle` / `run_minute_cycle` — the drift check is internal." T19 then adds: "accept an optional `should_continue: Callable[[], bool]` and check it at the top of each per-symbol iteration." Although the parameter is optional (backward-compatible), a junior AI executing T13 first will write code with no new parameters, then T19 requires modifying the same signatures. The tasks should either (a) acknowledge the signature change in T13 or (b) move the `should_continue` hook introduction into T13 so all cycle-function modifications happen in one place.

### [CONCERN] T17 `ca_update_due()` does not specify behavior when sentinel row is absent

T17 reads the sentinel row `('__bulk_ca__', 'daily')` from `acquisition_state` and checks `last_attempt_ts.date()`. If the row has never been inserted (first daemon run ever, or fresh DB), `last_attempt_ts` will be `NULL` and `.date()` will raise `AttributeError`. The task should explicitly state that a missing/NULL `last_attempt_ts` is treated as "never updated" (i.e., `ca_update_due()` returns `True`), or a sub-task should seed the sentinel row on first use.

### [CONCERN] No task covers EODHD 429/retry-after defensive handling from Failure Modes

The slice design's Failure Modes section specifies 429 handling: "treat as transient: log at WARNING, sleep the response's `Retry-After` (or 60s default), retry. After `MAX_RETRY_COUNT` 429s, escalate to ERROR and exit nonzero." No task in Part 1 or the listed Part 2 scope implements this 429-specific retry logic. The existing slice 145 transient-failure path may handle generic HTTP errors, but 429-specific `Retry-After` header parsing and the `MAX_RETRY_COUNT` escalation to nonzero exit appear to be new requirements with no implementation task.

### [PASS] Test-with pattern is consistently followed

Every implementation task has a corresponding test task immediately following it: T2→T3, T4+T5→T6, T7→T8, T9+T10→T11, T13→T14, T15+T16+T17→T18, T19→T20. No orphan tests or untested implementations.

### [PASS] Commit checkpoints are well distributed, not batched at end

Commits are placed after each logical unit: T3 (QuotaBucket), T6 (lists module), T8 (lists CLI), T12 (CA-drift), T14 (drift integration), T18 (runner predicates), T20 (SIGTERM). Seven commits across twenty tasks is a healthy cadence.

### [PASS] Task sequencing respects dependency chains with no circular dependencies

Dependencies flow correctly: T1→T2→T3, T1→T4→T5→T6, T9→T10→T11→T12, T9→T13→T14, T2→T15→T17→T18, T15→T19→T20. No task depends on a later task. Parallel tracks (QuotaBucket, Lists, CA-drift, Runner) are independent until T15/T16 consume T2's output.

### [PASS] CA-drift detection tasks comprehensively cover SC8 and SC9

SC8 (drift recompute fires) is covered by T9 (module), T11 (unit: stored≠current), T12 (integration: stale seed → UPDATEs → state advances → Stage A holds), T13/T14 (cycle integration). SC9 (no-op when snapshot matches) is covered by T11 (unit: stored==current → no band_writer call), T12 (second call: drift_detected=False), T14 (second cycle: zero band-UPDATEs). Both success criteria are thoroughly traced.

### [NOTE] Part 1/2 split defers several success criteria by design

SC1 (daemon runs forever), SC2 (scoped invocation), SC3 (--list exits), SC6 (ca update 200 credits), SC6a (daemon inline ca update — beyond the T15 placeholder), SC7 (ca update --symbol matches legacy), SC12 (old commands gone), and SC13 (no deadlock) are all deferred to Part 2. This is explicitly acknowledged in the document and is acceptable, but reviewers should verify Part 2 covers all of them when it lands.
