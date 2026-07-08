---
docType: review
layer: project
reviewType: tasks
slice: compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
project: squadron
verdict: PASS
sourceDocument: project-documents/user/tasks/143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260501
dateUpdated: 20260501
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All 12 success criteria are traced to tasks"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Test-with pattern correctly applied"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints are distributed, not batched at end"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F004
    severity: note
    category: test-coverage
    summary: "Two grep checks collectively satisfy SC2, but T10's pattern has a narrow targeting gap"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F005
    severity: pass
    category: uncategorized
    summary: "No load test task needed — slice design restates no NFR requiring one"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F006
    severity: pass
    category: task-granularity
    summary: "T16 is appropriately scoped as a validation-only checkpoint"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F007
    severity: pass
    category: cross-reference-consistency
    summary: "D4's `fetched_at` exclusion is correctly reflected; T9 need not extend SELECTs"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
  - id: F008
    severity: pass
    category: uncategorized
    summary: "T2 correctly identifies the test file via discovery pattern"
    location: 143-tasks.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
---

# Review: tasks — slice 143

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] All 12 success criteria are traced to tasks

Each success criterion maps to one or more tasks:

| SC | Tasks |
|---|---|
| SC1 (exports + signatures) | T3, T4, T6, T11 |
| SC2 (grep: only alias line remains) | T10 (call-site grep), T16 (import-alias grep) |
| SC3 (cross-process determinism) | T5 (`test_compute_snapshot_id_stable_across_processes`) |
| SC4 (ordering + fetched_at invariance) | T5 (`test_compute_snapshot_id_ordering_invariant`, `test_compute_snapshot_id_ignores_fetched_at`) |
| SC5 (EODHD parity AAPL/MSFT/GOOGL) | T14 |
| SC6 (issue #10 staleness → resolution) | T15 |
| SC7 (migration 023 hypertable) | T1, T2 |
| SC8 (migration 024 + EXPLAIN plan) | T1, T2, T16 |
| SC9 (zero-diff guard) | T8, T12 |
| SC10 (1162 baseline tests pass) | T7, T12, T16 |
| SC11 (I1 closeable) | T16 (final validation of SSOT) |
| SC12 (data_status sub-second) | T16 |

No success criteria are unaccounted for.

---

### [PASS] Test-with pattern correctly applied

The slice design mandates that T5 gates T6 (zero-diff guard) and that T7 gates T8–T10. The task breakdown enforces this:

- **T5** ← gate for T6: `test_casnapshot_*` + `test_compute_snapshot_id_*` must pass before `compute_k_factor` rename begins.
- **T7** ← gate for T8–T10: `compute_k_factor` rename tests must pass before any call-site edits.
- **T12** ← call-site migration + zero-diff guard test: runs the T8 guard test unchanged as the gate before the final checkpoint.

This sequencing correctly prevents numeric drift from propagating into call sites.

---

### [PASS] Commit checkpoints are distributed, not batched at end

Four checkpoint tasks are placed at natural logical boundaries:

| Task | Checkpoint |
|---|---|
| T2 | `feat(143): add migrations 023/024 (daily_ohlcv hypertable + view refresh)` |
| T7 | `feat(143): add CaSnapshot, compute_snapshot_id, rename k_factor` |
| T12 | `refactor(143): migrate call sites to compute_k_factor + CaSnapshot` |
| T15 | `test(143): add integration tests (EODHD parity, issue-10 regression)` |
| T16 | `feat(143): complete — compute_k_factor SSOT + daily_ohlcv hypertable` |

No commit batching at the end; each logical unit is committed as soon as its gate tests pass.

---

### [NOTE] Two grep checks collectively satisfy SC2, but T10's pattern has a narrow targeting gap

**T10** uses `grep -rn "k_factor(" src/` which catches function-call invocations but would **not** flag import-alias usages of the form `from manta_trading.data.adjustment import k_factor` without a following `(`.

**T16** uses `grep -rn "from manta_trading.data.adjustment import.*\bk_factor\b" src/` which catches import statements.

Together these two grep calls cover both the call-site and import layers and satisfy SC2. However, if someone were to `import k_factor` and call it as `k_factor(...)` (bypassing the alias at the `compute_k_factor` level), T10's pattern would miss it because it looks for the bare call pattern after a possible `=` re-export. This is low risk because:
1. `k_factor` is re-exported as an alias, so direct imports of the bare name would be from `__init__.py`.
2. T16's import-alias grep catches the canonical location.

This is informational — no action required, but worth noting if future reviewers wonder why two separate grep commands exist.

---

### [PASS] No load test task needed — slice design restates no NFR requiring one

The slice design's only performance constraint is the latency NFR (SC12: `data_status` sub-second, verified via `\timing` in T16). No throughput, concurrency, or load-based NFRs are introduced by this slice that would require a `tests/load/` task. CI wiring for load tests is therefore not applicable.

---

### [PASS] T16 is appropriately scoped as a validation-only checkpoint

T16 aggregates 9 validation steps: `pytest` unit baseline, `pyright --strict`, two grep assertions, DB migration, hypertable inspection, latency NFR check, and EXPLAIN plan verification. This is a valid final-gate pattern — no code is being written in T16; it is purely confirmatory. The step density is acceptable because T16 is the last task, making it a natural integration checkpoint.

---

### [PASS] D4's `fetched_at` exclusion is correctly reflected; T9 need not extend SELECTs

The slice design's "Files to modify" table includes "Extend the SELECTs to include `fetched_at`" for `context.py`, but this contradicts D4, which explicitly resolves the risk and states the SELECT does **not** need to include `fetched_at`. The task breakdown (T9) correctly omits the SELECT extension — the architectural decision (D4) is the authoritative spec. No inconsistency between T9 and the architecture as implemented.

---

### [PASS] T2 correctly identifies the test file via discovery pattern

T2 uses `grep -rl "MINUTE_MIGRATIONS\|023\|data_status" tests/` to locate the existing migration test file rather than hard-coding a filename. This is the correct pattern for a codebase where exact test file names are not always predictable. The assertions on migration 023 (`id`, `create_hypertable`, `ux_daily_ohlcv_symbol_time`) and migration 024 (non-empty body, references pre-rendered constant) correctly trace to D5's idempotency requirement.
