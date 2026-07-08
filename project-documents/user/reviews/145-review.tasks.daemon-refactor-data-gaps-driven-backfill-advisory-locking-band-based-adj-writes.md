---
docType: review
layer: project
reviewType: tasks
slice: daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
project: squadron
verdict: CONCERNS_ADDRESSED
sourceDocument: project-documents/user/tasks/145-tasks.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260502
dateUpdated: 20260502
findings:
  - id: F001
    severity: concern
    category: test-coverage
    summary: "SC5 (Stage B audit) has no verification task"
    location: unverified
  - id: F002
    severity: concern
    category: load-testing
    summary: "No load test for restated NFRs"
    location: unverified
  - id: F003
    severity: concern
    category: test-coverage
    summary: "SC12 (data_status health) lacks automated verification"
    location: unverified
  - id: F004
    severity: note
    category: test-coverage
    summary: "SC3 (delisted_date) not covered in integration test"
    location: unverified
  - id: F005
    severity: note
    category: risk-mitigation
    summary: "Risk mitigation (lock-held assertion) not explicitly tasked"
    location: unverified
  - id: F006
    severity: note
    category: commit-discipline
    summary: "Commit checkpoints sparse in mid-slice tasks"
    location: unverified
  - id: F007
    severity: pass
    category: test-discipline
    summary: "Test-with pattern is consistently followed"
    location: unverified
  - id: F008
    severity: pass
    category: sequencing
    summary: "Task sequencing respects all dependencies"
    location: unverified
---

# Review: tasks — slice 145

**Verdict:** CONCERNS_ADDRESSED
**Model:** z-ai/glm-5.1

## Resolution Summary (2026-05-02)

- **F001 (CONCERN — Stage B audit)**: addressed. T27 extended
  with explicit Stage B assertion block (re-fetch `/eod` for the
  3 active fixture symbols, compute `published_k = adjusted_close
  / close` per session, assert
  `abs(stored_k_factor - published_k) < ADJUSTMENT_DRIFT_EPSILON`).
- **F002 (CONCERN — load test)**: addressed. New T27a load test
  for `update_data_gaps` p99 ≤ 200ms (the testable NFR). Daily
  end-to-end (≤ 75 min) and minute per-symbol (≤ 5 min) NFRs are
  explicitly deferred to T31's manual walkthrough — documented in
  T27a's docstring requirement and the Context Summary's test-tier
  paragraph.
- **F003 (CONCERN — `data_status.health` automated)**: addressed.
  T27 extended with a `data_status.health` assertion: assert `OK`
  for fully-covered fixture symbols, then artificially DELETE
  bars to create a gap and assert `GAPS` for that symbol while
  others remain `OK`.
- **F004 (NOTE — `delisted_date` integration coverage)**:
  addressed. T27's fixture set extended from 3 to 4 symbols (3
  active + 1 delisted with `delisted_at_eodhd = true`). New
  assertion confirms `delisted_date` is populated end-to-end after
  the cycle for the delisted fixture symbol.
- **F005 (NOTE — lock-held assertion not tasked)**: addressed.
  Single-lock-at-a-time runtime assertion captured in T3 (the
  locking module itself, so it inherits across all callers
  automatically). New T4 sub-test verifies the assertion fires on
  the violating sequence and is silenced when the
  `_DAEMON_LOCK_ASSERTIONS` flag is off. T22/T25 reference T3's
  assertion rather than duplicating it.
- **F006 (NOTE — sparse commit checkpoints)**: addressed. Added
  explicit commit lines at T4 (locking), T13 (gaps package), T15
  (band writer), T17 (outcomes), T21 (selector helpers), T26
  (cycles), T29 (CLI). Mid-slice rollback granularity is now
  comparable to T30's two-commit pattern.

## Findings

### [CONCERN] SC5 (Stage B audit) has no verification task

Success criterion 5 requires verifying `abs(stored_k_factor - (response.adjusted_close / response.close)) < ADJUSTMENT_DRIFT_EPSILON` — i.e., that the daemon's adjustment matches the vendor's published numbers. This is an *external* consistency check, distinct from SC4's *internal* Stage A check (`adj_close = close * k_factor`). T27 (integration test) verifies Stage A but not Stage B. T15 (band_writer unit test) checks UPDATE counts and `compute_k_factor` call counts but not k_factor values against vendor data. No task in the breakdown asserts Stage B. The integration test T27 already hits the real EODHD API (skipif `MT_EODHD_API_KEY` unset) and could compare the stored `k_factor` against `response.adjusted_close / response.close` from the fetched bars with a single additional assertion block.

### [CONCERN] No load test for restated NFRs

The slice design explicitly restates four NFRs from the parent architecture: daily backfill ≤ 75 min, minute backfill ≤ 5 min, `update_data_gaps` p99 ≤ 200 ms, and lock contention < 1%. No task creates a load test in `tests/load/` covering any of these. While the end-to-end timing targets depend on EODHD API speed and may not be suitable for CI gating, the `update_data_gaps` p99 target is fully testable against a local TimescaleDB with a seeded fixture. A load test task should be added — at minimum for the `update_data_gaps` latency target — along with a CI wiring task that gates on it.

### [CONCERN] SC12 (data_status health) lacks automated verification

Success criterion 12 requires `data_status` to show `health = GAPS` for a symbol with an intentionally-missing session and `health = OK` for others. T31 (manual verification walkthrough) checks this in step 10, but no automated test task covers it. T27 (integration test) already drives a full daemon cycle and asserts DB state; adding a `SELECT ... FROM data_status` assertion for the fixture symbols would cover SC12 automatically and provide regression protection.

### [NOTE] SC3 (delisted_date) not covered in integration test

T23 (unit test for daily cycle) correctly asserts that `delisted_date` UPDATE is issued only when `delisted_at_eodhd = true`. However, T27 (integration test) seeds only active symbols (AAPL, MSFT, GOOGL) and doesn't verify `delisted_date` population end-to-end for a delisted symbol. This is partially covered by the unit test but a small integration assertion would strengthen confidence.

### [NOTE] Risk mitigation (lock-held assertion) not explicitly tasked

The slice design's Risks section calls for "a runtime assertion in the daemon's lock acquisition path that checks no other lock is currently held by this connection" and a "debug-mode toggle." No task (T22, T25, or otherwise) explicitly implements this assertion. T22 and T25 implement the cycle entry-points but don't mention the single-lock invariant check. If this risk mitigation is intended to ship with the slice, it should be captured in a task or as a sub-item of T22/T25.

### [NOTE] Commit checkpoints sparse in mid-slice tasks

Only T2, T30 (two commits), T31, and T32 specify explicit commit messages. The 27 tasks between T2 and T30 have no explicit commit boundaries. While each task is independently completable and could be committed individually, explicit commit guidance for logical groupings (e.g., after the gaps package T5–T13, after band_writer T14–T15) would improve rollback granularity and review tractability for a slice rated 4/5 effort.

### [PASS] Test-with pattern is consistently followed

Every implementation task has its test task immediately following it (T3→T4, T5→T6, T7→T8, T9→T10, T12→T13, T14→T15, T16→T17, T18→T19, T20→T21, T22→T23/T24, T25→T26, T28→T29). Integration tests follow unit tests for the same component (T10→T11). This is excellent discipline.

### [PASS] Task sequencing respects all dependencies

Locking primitives (T3–T4) precede consumers. Gap functions build bottom-up: `compute_missing_ranges` (T5) before `update_data_gaps` (T9), `next_trading_session_after` (T7) before `coalesce_data_gaps` (T12). Cycle entry-points (T22, T25) come after all their dependencies. The destructive switch+delete (T30) is last before verification. No circular dependencies exist.
