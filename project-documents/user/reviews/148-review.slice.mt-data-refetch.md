---
docType: review
layer: project
reviewType: slice
slice: mt-data-refetch
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/148-slice.mt-data-refetch.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260504
dateUpdated: 20260504
findings:
  - id: F001
    severity: concern
    category: architecture-alignment
    summary: "Missing `coalesce_data_gaps` for daily refetch"
    location: 148-slice.mt-data-refetch.md#run_daily_refetch
  - id: F002
    severity: concern
    category: error-handling
    summary: "Failure modes not enumerated for new I/O paths"
    location: 148-slice.mt-data-refetch.md#New_functions:_run_daily_refetch_/_run_minute_refetch
  - id: F003
    severity: concern
    category: consistency
    summary: "Advisory lock discipline is self-contradictory"
    location: 148-slice.mt-data-refetch.md#Dependencies
  - id: F004
    severity: concern
    category: nfr
    summary: "No NFR restatement for API credit consumption"
    location: 148-slice.mt-data-refetch.md
  - id: F005
    severity: pass
    category: architecture-alignment
    summary: "Core architectural alignment is strong"
    location: 148-slice.mt-data-refetch.md#Overview
---

# Review: slice — slice 148

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] Missing `coalesce_data_gaps` for daily refetch

The architecture states for `mt data refetch`: *"After all chunks process, runs `coalesce_data_gaps(symbol, granularity)` to merge contiguous gap rows"* — with no granularity qualification. The slice's `run_daily_refetch` behavior omits `coalesce_data_gaps` entirely, while `run_minute_refetch` includes it at step 5. Even though daily is a single-chunk operation (one `/eod` call), pre-existing adjacent same-status gap rows from prior operations would not be cleaned up. The architecture explicitly calls for coalesce after refetch, regardless of granularity.

---

### [CONCERN] Failure modes not enumerated for new I/O paths

The slice introduces interactive CLI-driven I/O paths (EODHD provider calls, database writes, advisory lock acquisition) but does not enumerate failure modes or handling strategies. Specifically missing: (1) EODHD API hang — no timeout value specified, and no strategy for how the CLI surfaces a hung provider call to the operator; (2) EODHD disconnect mid-response — no partial-write handling strategy; (3) `pg_try_advisory_lock` returns false (lock held by daemon or another process) — the data flow diagram shows this call but the behavior steps do not specify whether refetch should fail immediately, retry, or queue; (4) partial chunk success in minute refetch (some chunks succeed, some fail) — `CycleReport` is returned but the slice does not discuss how partial failure is presented or whether already-reset terminal rows are left in `UNKNOWN` state. The review criteria require explicit handling strategies, not implicit reliance on reused code.

---

### [CONCERN] Advisory lock discipline is self-contradictory

The dependencies table states: *"Advisory lock | Inherited — `update_data_gaps` acquires it | No new locking code"*. However, `run_daily_refetch` behavior step 2 says *"Acquire advisory lock on `(symbol, 'daily')`"*, and the data flow diagram shows explicit `pg_try_advisory_lock` / `pg_advisory_unlock` calls wrapping the entire operation. PostgreSQL advisory locks are reference-counted per session — if the refetch function acquires the lock externally and `update_data_gaps` (and `coalesce_data_gaps`) also acquire the same lock internally, a double/triple acquisition occurs requiring matching releases. The slice does not clarify whether: (a) the refetch-level lock replaces the per-function locks (requiring a `skip_lock` parameter), (b) the existing `_do_daily_symbol` already handles the lock and the refetch steps are merely describing that existing behavior, or (c) some other strategy. This ambiguity could lead to lock leaks or premature releases during implementation.

---

### [CONCERN] No NFR restatement for API credit consumption

The architecture defines NFR constants `EODHD_DAILY_QUOTA = 100_000` and `EODHD_PER_MINUTE_BURST = 1_000`, and the daemon implements token-bucket throttling against them. The refetch command consumes credits but: (1) does not restate the quota NFR with specific targets; (2) does not discuss whether refetch interacts with or bypasses the daemon's credit budget; (3) does not estimate credit cost for common operations (e.g., a full 22-year daily refetch = 1 credit, but a full 24-month minute refetch = ~6 chunks × 5 credits = 30 credits, potentially much more with retries). An operator running refetch concurrently with the daemon could exhaust the daily quota without warning, stalling automated data acquisition. The slice should at minimum document the credit interaction and whether any safeguards apply.

---

### [PASS] Core architectural alignment is strong

The slice correctly implements the architecture's refetch specification in its core mechanics: `force_reset_terminal=True` is passed to `update_data_gaps` as the architecture mandates, single-symbol scope matches the architecture's command shape (`mt data refetch --symbol X`), the minute refetch uses most-recent-first chunk ordering with `provider_max_chunk_days = 120` matching the architecture's backfill loop, existing `_do_*_symbol` functions are extended rather than duplicated (D2), re-adjustment is correctly excluded as an operator command (the daemon's CA-detection handles it), and the `--daily`/`--minute` flag convention is consistent with `mt data status`.
