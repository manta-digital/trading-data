---
docType: review
layer: project
reviewType: slice
slice: daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
project: squadron
verdict: CONCERNS_ADDRESSED
sourceDocument: project-documents/user/slices/145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260502
dateUpdated: 20260502
findings:
  - id: F001
    severity: concern
    category: failure-modes
    summary: "Failure modes not enumerated for new I/O paths"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Approach
  - id: F002
    severity: note
    category: nfr
    summary: "Architecture throughput expectations not restated as slice-level targets"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Overview
  - id: F003
    severity: note
    category: interface-extension
    summary: "`outcome` parameter added to `update_data_gaps` signature"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Outputs
  - id: F004
    severity: pass
    category: alignment
    summary: "Core algorithm alignment with architecture"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Data-Flows
  - id: F005
    severity: pass
    category: dependency-direction
    summary: "Dependency direction and module boundaries are correct"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Approach
  - id: F006
    severity: pass
    category: integration
    summary: "Integration points match downstream slice expectations"
    location: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md#Cross-Slice-Dependencies
---

# Review: slice — slice 145

**Verdict:** CONCERNS_ADDRESSED
**Model:** z-ai/glm-5.1

## Resolution Summary (2026-05-02)

- **F001 (CONCERN — failure modes)**: addressed. Added a "Failure
  Modes" section to the slice design with explicit per-path tables
  for EODHD HTTP, advisory-lock acquisition, and PostgreSQL
  transactional writes. Each row enumerates the failure, detection
  mechanism, classification, and action. Adds one new constant
  `DAEMON_LOCK_TIMEOUT = '30 seconds'` and codifies the
  `httpx.Timeout` configuration. Daemon supervisor (catch-and-continue)
  is explicitly out of scope for this slice.
- **F002 (NOTE — NFR targets)**: addressed. Added a "Non-Functional
  Targets" section restating the arch's planning estimates as
  slice-level targets (≤ 90 min daily backfill cold start, ≤ 5 min
  per-symbol minute backfill, ≤ 200ms p99 `update_data_gaps`,
  expected lock contention < 1%).
- **F003 (NOTE — `outcome` signature divergence)**: addressed.
  Annotated the `update_data_gaps` signature in Outputs with an
  explicit note explaining why `outcome` is added (arch step 7
  requires it; arch's signature under-specifies) and that slice 148
  consumes the extended signature.

## Findings

### [CONCERN] Failure modes not enumerated for new I/O paths

The slice introduces or modifies several I/O paths — EODHD HTTP calls, PostgreSQL advisory lock acquisition, and PostgreSQL transactional writes (INSERT/UPDATE/DELETE across `data_gaps`, `acquisition_state`, `daily_ohlcv`/`minute_ohlcv`, `instruments`) — but does not systematically enumerate failure modes (hang, timeout, peer disconnect mid-send) with explicit handling strategies.

Specific gaps:

- **EODHD HTTP hang / connection drop mid-response**: Decision F classifies HTTP status codes and mentions `httpx.TimeoutException` in the verification walkthrough, but no explicit timeout configuration or mid-stream disconnect handling strategy is stated. What timeout value is used? Is a partial JSON response detected and classified, or could it be misinterpreted?
- **PostgreSQL advisory lock indefinite block**: `pg_advisory_xact_lock` blocks the caller until the lock is available. For the daemon path (Decision C), there is no lock-acquisition timeout. If a long-running refetch or backtest holds the lock, the daemon blocks indefinitely. The Risks section mentions serialization under contention but does not state a handling strategy for unbounded wait (e.g., a lock_timeout GUC, a statement_timeout, or an application-level deadline).
- **PostgreSQL connection loss mid-transaction**: The `update_data_gaps` writer performs a multi-step transaction (snapshot → delete → compute → insert → promote → update acquisition_state). If the PG connection drops after the DELETE but before the INSERTs, the transaction rolls back — but the slice does not enumerate this failure mode or confirm that psycopg's autocommit/isolation-level settings ensure atomic rollback. Similarly, `apply_band_updates` issues multiple UPDATEs within one transaction; a mid-transaction disconnect leaves a partially-adjusted state that the next cycle must recover from.
- **PostgreSQL deadlock with internal locks**: Although the one-lock-at-a-time discipline prevents application-level deadlocks, PostgreSQL internal lock conflicts (e.g., between the advisory lock and a concurrent DDL or vacuum) are not enumerated.

The Risks section partially addresses contention and classification ambiguity, but does so unsystematically. Each I/O path should have its failure modes enumerated with handling strategies (retry, backoff, fail-fast, escalate to operator, etc.), per the review criteria.

### [NOTE] Architecture throughput expectations not restated as slice-level targets

The architecture states concrete throughput constraints and capacity expectations for paths this slice modifies: EODHD rate limits of 1,000 calls/min and 100k/day, daily backfill completing in ~57 minutes, and minute backfill in ~4 minutes (arch §"Refetch"). The slice touches these paths directly (it drives the backfill loops) but does not restate these targets. While the architecture frames these as planning estimates rather than strict SLAs, the review criteria ask that any NFR from the parent architecture be restated with a specific target in the slice doc. Including them would make the slice self-contained for verification and prevent accidental regression if rate-limit handling changes.

### [NOTE] `outcome` parameter added to `update_data_gaps` signature

The architecture's `update_data_gaps` signature is:

```
update_data_gaps(symbol, granularity, from_ts, to_ts,
                 fetch_status_for_unfilled,
                 force_reset_terminal=False)
```

The slice adds an `outcome` parameter and a return type `UpdateResult`:

```
update_data_gaps(symbol, granularity, from_ts, to_ts, fetch_status_for_unfilled, *, force_reset_terminal=False, outcome) -> UpdateResult
```

This is a reasonable extension: the architecture's algorithm step 7 requires writing `acquisition_state.last_attempt_outcome` ("Set last_attempt_outcome to the caller's outcome"), which cannot be done without the caller providing the outcome. The architecture's signature is under-specified; the slice correctly fills the gap. This is not a violation but is worth noting as a signature divergence that downstream slices (148) must consume.

### [PASS] Core algorithm alignment with architecture

The slice faithfully implements all core algorithms specified in the architecture:

- **`compute_missing_ranges`**: matches arch §"Gap function" algorithm steps 1–6, including lifecycle-date clamping and session-granular gap detection.
- **`update_data_gaps`**: implements the full 7-step algorithm (snapshot → force-reset → delete → recompute → insert with carry-forward → promote → update acquisition_state), including `force_reset_terminal` for refetch.
- **`coalesce_data_gaps`**: matches the O(n) sorted-list accumulator algorithm with adjacency defined by matching `fetch_status` and `next_trading_session_after`.
- **Band-based adjustment writes**: matches the arch §"Band-based adjustment writes" algorithm — one `compute_k_factor` call per ex-date band, one UPDATE per band, zero-ex-date case produces one UPDATE.
- **Daemon lock discipline**: Decision E confirms one transaction per (symbol, granularity); the architecture's "at most one lock at a time" constraint is enforced with a runtime assertion (Risks section).
- **Outcome classification mapping**: Decision F maps HTTP responses to the architecture's `last_attempt_outcome` enum exactly as specified in the arch's mapping table.
- **Minute backfill loop**: most-recent-actionable-gap-first, chunked fetches, actionable = `UNKNOWN | FAILED_RETRYABLE`, terminal states not retried — all match arch §"Minute" backfill spec.
- **Daily backfill**: per-symbol `/eod` with `output_size=full`, `first_data_date`/`delisted_date` side-effects — matches arch §"Daily" backfill path. Bulk-EOD steady-state correctly deferred to slice 146 per arch §"120-arch dependency".

### [PASS] Dependency direction and module boundaries are correct

Decision A correctly places gap functions in `manta_trading.data.gaps` (not inside the daemon), ensuring the daemon imports from it rather than the reverse. Decision B correctly places the band-writer in `manta_trading.data.adjustment.band_writer` as a pure function callable by both daily/minute writers and slice 146's CA-detection path, avoiding duplication. The locking module is a standalone utility with no business-logic dependency. All dependency directions flow inward (daemon → gaps, daemon → band_writer, daemon → locking) with no circular or upside-down imports.

### [PASS] Integration points match downstream slice expectations

- **Slice 146**: consumes the daemon cycle plumbing and `apply_band_updates` for CA-detection drift recompute — both are exported and documented.
- **Slice 147**: reads `data_status`, which depends on populated `data_gaps` — the slice makes `health` meaningful by writing gap rows.
- **Slice 148**: calls `update_data_gaps(force_reset_terminal=True)` — the flag is implemented here and the behavior is tested (success criterion 10).
- **Slice 150**: reads `adj_*` columns — the slice populates them via `apply_band_updates` on every ingest.
- **Backtest read-path** (future): all primitives (`advisory_lock`, `pg_try_advisory_xact_lock`, `update_data_gaps`, `coalesce_data_gaps`) are shipped; consumer wiring is explicitly deferred with clear rationale.
