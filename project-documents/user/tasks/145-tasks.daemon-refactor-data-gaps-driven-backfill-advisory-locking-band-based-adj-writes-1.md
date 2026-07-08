---
docType: tasks
slice: 145-daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
project: trading
lld: user/slices/145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md
part: 1
partOf: 145-tasks.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes-2.md
dependencies:
  - 142-slice.schema-migration-and-cold-start
  - 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
  - 144-slice.trading-sessions-materialization-data-status-view-rewrite
projectState: >
  Slice 144 complete and merged to main (commit a6c5854). Trading_sessions
  populated, data_status view projects target_end_ts, TradingCalendar reads
  from the table, OutOfHorizonError raised on horizon misses. data_gaps
  table empty — no writer yet. acquisition_state table empty after slice 142
  TRUNCATE. 120-era daemon code (acquisition/daemon/{daily,minute,
  work_queue,minute_work_queue,symbol_sources}.py + acquisition/{daily,
  minute}/freshness.py) still in tree but not actively driven.
  Branch: 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
  (already created from main; design committed).
dateCreated: 20260502
dateUpdated: 20260503
status: complete
---

## Context Summary

- Slice 145 ships the load-bearing piece of the 140 initiative. Six new
  modules, one significant refactor (deletion of 120-era daemon path),
  meaningful concurrency surface.
- Three invariants: (1) `data_gaps` is the source of truth for what's
  missing, (2) PostgreSQL advisory locks serialize concurrent writers
  on `(symbol, granularity)` with daemon holding ≤ 1 lock at a time,
  (3) `adj_*` columns populate at ingest via band-based UPDATE.
- New constants this slice adds to `manta_trading.constants`:
  `DAEMON_LOCK_TIMEOUT = '30 seconds'`. No new schema migrations.
- Daily-cycle side-effects populate `instruments.first_data_date`
  (MIN of returned bars, one-time) and `instruments.delisted_date`
  (MAX of returned bars when `delisted_at_eodhd = true`).
- CA-detection drift loop and bulk-EOD steady-state are explicitly
  deferred to slice 146. This slice writes correct `adj_*` on
  initial fetch only.
- Failure modes for EODHD HTTP, advisory lock acquisition, and PG
  transactional writes are spelled out in the slice design's
  "Failure Modes" section. Every catch and classify in the
  implementation must match those tables.
- New CLI: `mt data daemon daily --once [--symbols X,Y,Z]` and
  `mt data daemon minute --once [--symbols X,Y,Z]`.
- Migration is in three steps within this slice: reconnaissance walk,
  build alongside, then switch + delete (T30, with explicit rollback
  discipline — checkpoint, tag, two commits).
- Test tiers: unit (mocked, default), integration (live test DB,
  skipif `MT_TIMESCALE_DB_URL` unset), load (skipif unset; T27a covers
  `update_data_gaps` p99 latency only — backfill end-to-end timing
  depends on EODHD wall-clock and is checked manually in T31).

---

## Tasks

- [x] **T1. Reconnaissance walk of 120-era daemon code** ⚠️ STOP-GATE
  - [x] Grep callers of every module slated for deletion:
    `acquisition/daemon/work_queue.py`,
    `acquisition/daemon/minute_work_queue.py`,
    `acquisition/daemon/symbol_sources.py`,
    `acquisition/daemon/daily.py`,
    `acquisition/daemon/minute.py`,
    `acquisition/daily/freshness.py`,
    `acquisition/minute/freshness.py`.
  - [x] For each, list in a temporary scratch comment: purpose, reads,
    writes, public callers (anything outside `acquisition/`), whether
    new path covers the same behavior (yes/no/partial).
  - [x] **STOP condition**: if any module has "no" or "partial"
    coverage — i.e. the new daemon path does not subsume its
    behavior, OR a non-`acquisition/` caller exists that the new
    path does not serve — halt task execution and surface the
    finding to the project manager. **Do not begin T2.** The
    migration scope grows in this case (new module needs to be
    written, or the caller needs to be repointed) and that is a
    design conversation, not an implementation one.
  - [x] Success: scratch list produced; only "yes" answers; project
    manager has not been pinged. The list is internal — discard
    after T30 confirms its accuracy. (Was previously labeled T17;
    correct anchor is T30 — the deletion commit.)

- [x] **T2. Add `DAEMON_LOCK_TIMEOUT` constant**
  - [x] Append `DAEMON_LOCK_TIMEOUT: str = '30 seconds'` to
    `src/manta_trading/constants.py` with a one-line docstring
    citing slice 145's failure-modes table.
  - [x] Success: constant importable from `manta_trading.constants`.
  - [x] Commit: `feat(145): add DAEMON_LOCK_TIMEOUT constant`

- [x] **T3. Create `manta_trading.data.locking` module**
  - [x] New file `src/manta_trading/data/locking.py`.
  - [x] Export `lock_key(symbol: str, granularity: str) -> int`
    computed as `hashtextextended(symbol || '|' || granularity, 0)`.
    Implementation: issue the SQL on the supplied connection and
    return the integer; cache by `(symbol, granularity)` per-process
    in a `lru_cache` with a sensible cap.
  - [x] Export `advisory_lock(conn, symbol, granularity, *,
    timeout: str | None = None)` context manager. Inside:
    `SET LOCAL lock_timeout = %s` if `timeout` is not None, then
    `SELECT pg_advisory_xact_lock(%s)` with the computed key.
    The context manager body just yields; the lock auto-releases
    on transaction end.
  - [x] Export `try_advisory_lock(conn, symbol, granularity)` that
    runs `SELECT pg_try_advisory_xact_lock(...)` and returns `bool`.
    For backtest read-path use later; tested here.
  - [x] **Single-lock-at-a-time invariant assertion** (slice design
    Risks). The `advisory_lock` context manager tracks held keys
    per-connection (e.g. `conn._mt_held_lock_keys: set[int]`). On
    enter: if the set is non-empty, raise `AssertionError` listing
    the currently-held key — the daemon was about to violate the
    "at most one lock at a time" discipline. On exit (txn end —
    deferred via `Session`-level cleanup or explicit reset): clear
    the set. Gate behind a module-level `_DAEMON_LOCK_ASSERTIONS`
    flag (default `True` for this slice and slice 146); operator
    can flip to `False` via env var `MT_DAEMON_DEBUG=0` once the
    invariant is proven stable in production.
    Note: this check applies to `advisory_lock` only. The
    backtest's `try_advisory_lock` is *intended* to be called with
    multiple held locks (sorted-acquisition discipline), so it
    skips the assertion.
  - [x] Success: module imports cleanly; no business-logic
    dependencies (only psycopg + manta_trading.constants);
    single-lock assertion fires when expected.

- [x] **T4. Unit + integration test — `locking` module**
  - [x] Unit test (mocked cursor): `lock_key` returns same int for
    same input across calls; different ints for different inputs.
  - [x] Integration test (`test/integration/test_locking.py`,
    skipif `MT_TIMESCALE_DB_URL` unset):
    - Two separate `psycopg.Connection`s; A holds
      `advisory_lock('AAPL', 'daily')` for 5 seconds; B's
      `advisory_lock('AAPL', 'daily')` waits ~5s before proceeding.
    - B's `advisory_lock('MSFT', 'daily')` (disjoint scope) returns
      in < 100ms while A still holds AAPL.
    - `advisory_lock('AAPL', 'daily', timeout='100ms')` raises
      `psycopg.errors.LockNotAvailable` (SQLSTATE `55P03`) when A
      holds the same key.
    - `try_advisory_lock` returns `False` on contention, `True` on
      free key.
  - [x] **Single-lock invariant test**: with assertions enabled,
    calling `advisory_lock(conn, 'AAPL', 'daily')` while still
    inside an outer `advisory_lock(conn, 'MSFT', 'daily')` block
    raises `AssertionError`. With assertions disabled (flag flipped),
    no error is raised.
  - [x] Success: all assertions pass; no deadlock.
  - [x] Commit: `feat(145): add advisory_lock primitives (manta_trading.data.locking)`

- [x] **T5. Create `manta_trading.data.gaps.compute_missing_ranges`**
  - [x] New package `src/manta_trading/data/gaps/` with
    `__init__.py` that re-exports the public surface.
  - [x] New file `gaps/compute_missing_ranges.py`.
  - [x] Implement per arch §"Gap function" steps 1–6:
    lifecycle-date clamping, ordered trading-session list lookup
    from `trading_sessions`, set-difference vs stored bars in the
    data table, contiguous-run grouping. Return list of
    `GapRange(symbol, granularity, gap_start_utc, gap_end_utc)`.
  - [x] `GapRange` is a frozen dataclass.
  - [x] Function signature:
    `compute_missing_ranges(conn, symbol, granularity, from_ts, to_ts) -> list[GapRange]`.
    Pure read; no writes. Reads `instruments`, `trading_sessions`,
    and `daily_ohlcv`/`minute_ohlcv`.
  - [x] Returns empty list for symbols with both
    `first_listing_date` and `first_data_date` NULL (cannot compute
    range; surfaced as STALE later by data_status).
  - [x] Success: function exists; pure read.

- [x] **T6. Unit test — `compute_missing_ranges`**
  - [x] Test file: `test/unit/data/gaps/test_compute_missing_ranges.py`.
  - [x] Mocked DB cursor; fixtures cover: pre-listing clamp, delisted
    clamp, fully-covered range (returns []), single-day gap,
    Friday-missing + weekend + Monday-missing yields ONE range
    spanning Fri→Mon (the weekend isn't in the trading-session
    list), multi-week scattered gaps, mid-range hole.
  - [x] Both granularities (daily + minute).
  - [x] Success: every fixture's expected range list matches.

- [x] **T7. Create `manta_trading.data.gaps.next_trading_session_after`**
  - [x] New file `gaps/next_trading_session_after.py`.
  - [x] Implementation:
    `SELECT MIN(session_date) FROM trading_sessions WHERE calendar_id = %s AND session_date > %s`.
    Returns `date | None`.
  - [x] Signature:
    `next_trading_session_after(conn, calendar_id: str, after_date: date) -> date | None`.
  - [x] Success: function exists; reads only.

- [x] **T8. Unit test — `next_trading_session_after`**
  - [x] Test file: `test/unit/data/gaps/test_next_trading_session_after.py`.
  - [x] Mocked cursor: returns `(date(2024, 11, 26),)` after Black
    Friday → Monday gap; returns `(None,)` past horizon end.
  - [x] Success: assertions pass.

- [x] **T9. Create `manta_trading.data.gaps.update_data_gaps`**
  - [x] New file `gaps/update_data_gaps.py`.
  - [x] Implement per arch §"update_data_gaps" steps 1–7:
    snapshot prior rows, force-reset terminal (if flagged), delete
    intersecting rows, recompute via `compute_missing_ranges`,
    insert with carried-forward `attempt_count`, promote to
    `RETRY_EXHAUSTED` past `MAX_RETRY_COUNT`, update
    `acquisition_state.last_attempt_ts` and
    `last_attempt_outcome`.
  - [x] Signature:
    `update_data_gaps(conn, symbol, granularity, from_ts, to_ts,
    fetch_status_for_unfilled, *, force_reset_terminal=False, outcome) -> UpdateResult`.
    Caller supplies `outcome` (one of `LastAttemptOutcome` enum
    values from slice 142). The arch's documented signature is
    extended with `outcome` per slice design's note in §Outputs.
  - [x] Returns a `UpdateResult` dataclass:
    `(gaps_inserted: int, gaps_promoted_exhausted: int,
    terminal_rows_reset: int)`.
  - [x] Acquires `advisory_lock(conn, symbol, granularity)` for the
    duration. Caller already inside a transaction; this function
    does not start its own — ensures caller's atomicity guarantee.
    Document this clearly in the docstring.
  - [x] Success: function exists; honors arch algorithm exactly.

- [x] **T10. Unit test — `update_data_gaps` algorithm correctness**
  - [x] Test file: `test/unit/data/gaps/test_update_data_gaps.py`.
  - [x] Mocked cursor + mocked `compute_missing_ranges`. Fixtures
    cover:
    - First attempt (no prior rows): inserts with
      `attempt_count = 1`.
    - Repeat attempt with same `fetch_status_for_unfilled`:
      `attempt_count` carries forward (`max_prior + 1`).
    - Repeat attempt with different `fetch_status_for_unfilled`:
      treated as first attempt (`attempt_count = 1`).
    - `attempt_count` reaches `MAX_RETRY_COUNT` →
      `fetch_status` promoted to `RETRY_EXHAUSTED`.
    - `force_reset_terminal=True` clears `PROVIDER_HOLE` /
      `RETRY_EXHAUSTED` rows in scope before carry-forward
      consultation.
    - Partial-fill split: 30-day gap with 10 days filled in middle
      → original row deleted, two new rows (head + tail) inserted.
    - `success` outcome with no unfilled ranges: zero new gap
      rows; `acquisition_state` row updated with
      `outcome = success`.
  - [x] Success: every fixture's expected DB-mutation set matches.

- [x] **T11. Integration test — `update_data_gaps` transactional behavior**
  - [x] Test file: `test/integration/test_update_data_gaps.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Seed `instruments`, `trading_sessions`, `daily_ohlcv` with
    a small fixture. Drive `update_data_gaps` from one connection
    inside an explicit transaction. Roll back the transaction
    mid-function (simulate connection drop): assert no
    `data_gaps` rows visible from a second connection.
  - [x] Concurrent test: two connections call `update_data_gaps`
    on same `(symbol, granularity)`; second blocks on advisory
    lock until first commits. Disjoint scopes proceed in parallel.
  - [x] Success: no torn writes; no deadlock.

- [x] **T12. Create `manta_trading.data.gaps.coalesce_data_gaps`**
  - [x] New file `gaps/coalesce_data_gaps.py`.
  - [x] Implement per arch §"coalesce_data_gaps": single-pass O(n)
    sorted-list accumulator; adjacency = same `fetch_status` AND
    `next_trading_session_after(A.gap_end) == B.gap_start`.
  - [x] On any merge: `last_attempt_ts = MIN(...)`,
    `attempt_count = MAX(...)`. Idempotent (no-op when nothing
    merges).
  - [x] Acquires `advisory_lock(conn, symbol, granularity)`.
    Caller transaction discipline same as `update_data_gaps`.
  - [x] Returns count of rows merged (0 on idempotent re-run).

- [x] **T13. Unit test — `coalesce_data_gaps` adjacency cases**
  - [x] Test file: `test/unit/data/gaps/test_coalesce_data_gaps.py`.
  - [x] Fixtures: zero-row scope (no-op), one-row scope (no-op),
    two adjacent same-status rows (merged), two adjacent
    different-status rows (not merged), two rows with a non-trading
    day between (merged via `next_trading_session_after`),
    two rows with a trading day between (NOT merged), idempotent
    re-run.
  - [x] Success: every fixture's expected output matches.
  - [x] Commit: `feat(145): add manta_trading.data.gaps package (compute/update/coalesce)`

- [x] **T14. Create `manta_trading.data.adjustment.band_writer`**
  - [x] New file `src/manta_trading/data/adjustment/band_writer.py`.
  - [x] Implement per arch §"Band-based adjustment writes" steps
    1–5: identify ex-dates within `[range_start, range_end]` plus
    the immediately preceding ex-date (anchor for leading band);
    walk consecutive ex-date pairs as bands; for each band call
    `compute_k_factor(symbol, band_start - 1 day, ca_snapshot)`
    once; issue one UPDATE per band against the appropriate
    table (`daily_ohlcv` or `minute_ohlcv`); finally update
    `acquisition_state.last_adjusted_ca_snapshot_id =
    ca_snapshot.snapshot_id`.
  - [x] Signature: `apply_band_updates(conn, table: str, symbol,
    range_start, range_end, ca_snapshot) -> int` (returns the
    number of UPDATEs issued).
  - [x] Caller transaction discipline: must be inside an open
    transaction. Document.
  - [x] Success: function exists; mirrors arch pseudocode.

- [x] **T15. Unit test — `band_writer.apply_band_updates`**
  - [x] Test file: `test/unit/data/adjustment/test_band_writer.py`.
  - [x] Mocked cursor + capture `cur.execute` calls. Fixtures:
    - Zero ex-dates in range → exactly 1 UPDATE issued.
    - One ex-date in range (mid-range) → 2 UPDATEs.
    - N ex-dates in range → N+1 UPDATEs.
    - Ex-date exactly on `range_start` → still N+1 UPDATEs;
      leading band is empty but no special-case crash.
    - Ex-date on a non-trading-day calendar boundary handled
      correctly (band boundary is the date, not a session).
  - [x] Each UPDATE's WHERE clause covers the correct
    `[band_start, band_end)`.
  - [x] `compute_k_factor` is called exactly N+1 times for N
    ex-dates (once per band). Verify via mock call_count.
  - [x] Success: every fixture's UPDATE-count and parameter
    bindings match.
  - [x] Commit: `feat(145): add band_writer.apply_band_updates`

- [x] **T16. Create `manta_trading.data.acquisition.outcomes`**
  - [x] New file `src/manta_trading/data/acquisition/outcomes.py`.
  - [x] Implement `classify_outcome(response, range_start, range_end) -> LastAttemptOutcome`
    per slice design's Decision F. Inspects HTTP status and body
    shape (handles EODHD's 200-with-error-payload quirk). Maps
    to existing `LastAttemptOutcome` enum from slice 142.
  - [x] Implement `outcome_to_fetch_status(outcome) -> FetchStatus | None`
    per arch's outcome→fetch_status table:
    `success → None`, `partial → UNKNOWN`, `empty → PROVIDER_HOLE`,
    `transient_failure → FAILED_RETRYABLE`. `None` means "no
    unfilled rows; range is covered."
  - [x] HTTP 4xx (non-429) raises an uncaught
    `ProviderResponseError`. Document explicitly — daemon lets
    this propagate.
  - [x] Success: function exists; mapping table is exhaustive
    over `LastAttemptOutcome`.

- [x] **T17. Unit test — `classify_outcome` and `outcome_to_fetch_status`**
  - [x] Test file: `test/unit/data/acquisition/test_outcomes.py`.
  - [x] Fixtures cover every row of slice design's Decision F
    table: HTTP 5xx → transient; 429 → transient; 4xx other → raise;
    timeout/connection-error → transient; 200 with `{"error": ...}`
    → transient; 200 with empty list → empty; 200 partial → partial;
    200 full → success.
  - [x] `outcome_to_fetch_status` exhaustiveness: every enum value
    has a defined mapping.
  - [x] Success: parametrized test covers every row; no enum value
    silently falls through.
  - [x] Commit: `feat(145): add outcome classifier (HTTP/body → LastAttemptOutcome → fetch_status)`

---

Continued in [part 2](145-tasks.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes-2.md) — daemon plumbing helpers (T18–T21), cycle entry-points (T22–T26), live integration test (T27, T27a), CLI (T28–T29), migration boundary (T30), verification (T31), closeout (T32).
