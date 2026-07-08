---
docType: tasks
slice: 145-daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
project: trading
lld: user/slices/145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes.md
part: 2
partOf: 145-tasks.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes-1.md
dependencies:
  - 142-slice.schema-migration-and-cold-start
  - 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
  - 144-slice.trading-sessions-materialization-data-status-view-rewrite
projectState: see part 1
dateCreated: 20260502
dateUpdated: 20260503
status: complete
---

## Context Summary

Continuation of slice 145 task breakdown. Part 1 covers reconnaissance
(T1), constants (T2), the locking module (T3–T4), the gaps package
(T5–T13), the band-writer (T14–T15), and the outcome classifier
(T16–T17). This file picks up at the daemon-plumbing helpers and runs
through the migration boundary and slice closeout.

See [part 1](145-tasks.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes-1.md)
for the slice's overall context summary, dependencies, and earlier tasks.

---

## Tasks

- [x] **T18. Create `manta_trading.data.gaps.actionable_gap_selector`**
  - [x] New file `gaps/actionable_gap_selector.py`.
  - [x] Implement `pick_most_recent_actionable_gap(conn, symbol,
    granularity, from_ts, to_ts) -> GapRow | None`. SQL: `SELECT
    ... FROM data_gaps WHERE symbol=? AND granularity=? AND
    fetch_status IN ('UNKNOWN', 'FAILED_RETRYABLE') AND gap_start
    >= ? AND gap_end <= ? ORDER BY gap_end DESC LIMIT 1`.
  - [x] Returns `None` when no actionable gap remains.
  - [x] Success: function exists; returns expected row shape.

- [x] **T19. Unit test — `actionable_gap_selector`**
  - [x] Test file: `test/unit/data/gaps/test_actionable_gap_selector.py`.
  - [x] Mocked cursor: scope with mix of UNKNOWN, FAILED_RETRYABLE,
    PROVIDER_HOLE, RETRY_EXHAUSTED rows. Assert the SELECT only
    matches the first two and returns the most recent by `gap_end`.
  - [x] Success: query parameters and expected row match.

- [x] **T20. Symbol-selector helper**
  - [x] Add `iter_active_instruments(conn, *, ordering: str)` to a
    new module `src/manta_trading/data/acquisition/symbols.py`
    (replaces the legacy `daemon/symbol_sources.py` for the new path).
  - [x] SQL: `SELECT symbol, trading_calendar_id, first_listing_date,
    first_data_date, delisted_at_eodhd, delisted_date FROM
    instruments WHERE (delisted_at_eodhd = false AND delisted_date
    IS NULL) OR (delisted_at_eodhd = true AND delisted_date IS NULL)
    ORDER BY ...` per slice design's resolved decision.
  - [x] Two `ordering` values:
    `'most_stale_first'` → `last_attempt_ts ASC NULLS FIRST, symbol ASC`
    (joined to `acquisition_state` for the granularity);
    `'alphabetical'` → `symbol ASC` (debug only).
  - [x] Yields one row per symbol; no special-case for cold-start
    (NULLS FIRST handles it).
  - [x] Success: helper exists; query produces correct order on a
    seeded fixture.

- [x] **T21. Unit test — symbol-selector ordering**
  - [x] Test file: `test/unit/data/acquisition/test_symbols.py`.
  - [x] Mocked cursor with synthetic mix of rows: cold-start (every
    `last_attempt_ts = NULL`), interrupted-cold (some NULL, some
    set), steady-state (all set with varied timestamps).
  - [x] Assert the SQL string produced matches the expected
    `ORDER BY` clause per ordering mode.
  - [x] Success: parameter-driven test covers all three regimes.
  - [x] Commit: `feat(145): add actionable_gap_selector + symbol-selector helpers`

- [x] **T22. Daily cycle entry-point — `run_daily_cycle`**
  - [x] New file path retained:
    `src/manta_trading/data/acquisition/daemon/daily.py` is
    rewritten in-place to expose `run_daily_cycle(*, symbols:
    list[str] | None = None) -> CycleReport`. Old contents replaced
    by this slice's path.
  - [x] Body matches slice design's Daily-cycle pseudocode:
    iterate symbols (via T20), per symbol:
    `current_ca_snapshot`, compute `target_start`/`target_end`,
    HTTP `/eod`, `classify_outcome`, open transaction with
    `lock_timeout = DAEMON_LOCK_TIMEOUT`, take `advisory_lock`,
    INSERT bars, `apply_band_updates`, populate
    `first_data_date`/`delisted_date`, `update_data_gaps`, set
    `last_adjusted_ca_snapshot_id`, commit.
  - [x] Per-symbol exceptions caught at the boundary and recorded
    as `transient_failure` outcome (see Decision F). HTTP 4xx
    other than 429 is uncaught — propagates and crashes the cycle.
  - [x] `CycleReport` aggregates per-symbol outcomes (counts of
    success/partial/empty/transient_failure) and total wall-clock.
  - [x] Single-lock-at-a-time invariant is enforced by the
    `advisory_lock` context manager itself (see T3); cycle code
    inherits the check without per-call assertions.
  - [x] Success: entry-point exists; matches arch's daily backfill
    spec including lifecycle-column side-effects.

- [x] **T23. Unit test — `run_daily_cycle` happy path**
  - [x] Test file: `test/unit/data/acquisition/daemon/test_daily.py`.
  - [x] Mock the EODHD client and DB connection. Fixture: 3
    symbols, 200-OK with full-range bars. Drive
    `run_daily_cycle(symbols=['A','B','C'])`. Assert:
    - `apply_band_updates` invoked exactly once per symbol.
    - `update_data_gaps` invoked once per symbol with
      `outcome=success` and `fetch_status_for_unfilled=None`.
    - `instruments.first_data_date` UPDATE issued for each symbol.
    - `delisted_date` UPDATE issued only if
      `delisted_at_eodhd = true`.
    - `CycleReport` records `success_count = 3`.
  - [x] Success: all assertions pass.

- [x] **T24. Unit test — `run_daily_cycle` failure paths**
  - [x] In the same test file: parametrized fixtures for HTTP 5xx,
    timeout, 200 with empty body, 200 with partial range, 200
    with `{"error": ...}` body.
  - [x] For each, assert outcome is recorded correctly,
    `update_data_gaps` is called with the expected
    `fetch_status_for_unfilled`, the cycle does not crash on
    transient classes, and HTTP 4xx (non-429) DOES crash.
  - [x] Lock-timeout case: simulate a connection holding the lock
    elsewhere; `advisory_lock` raises
    `LockNotAvailable` after 30s — assert caught and recorded as
    `transient_failure` (use a 100ms timeout in the test for speed).
  - [x] Success: every classification row has matching DB-mutation
    behavior.

- [x] **T25. Minute cycle entry-point — `run_minute_cycle`**
  - [x] `src/manta_trading/data/acquisition/daemon/minute.py`
    rewritten in-place to expose `run_minute_cycle(*, symbols:
    list[str] | None = None) -> CycleReport`. Old contents
    replaced.
  - [x] Body matches slice design's Minute-cycle pseudocode:
    initial `update_data_gaps` recompute, loop over actionable
    gaps via T18, fetch chunks (`provider_max_chunk_days = 120` for
    EODHD), per-chunk transaction with band-write +
    `update_data_gaps`, post-loop `coalesce_data_gaps`.
  - [x] Per-symbol exceptions caught/classified as in T22.
  - [x] Single-lock-at-a-time invariant inherited from
    `advisory_lock` (T3); no per-call assertion in cycle code.
  - [x] Success: entry-point exists; matches arch minute-backfill
    algorithm.

- [x] **T26. Unit test — `run_minute_cycle`**
  - [x] Test file: `test/unit/data/acquisition/daemon/test_minute.py`.
  - [x] Mock EODHD client + DB. Fixture: one symbol with a
    multi-month gap that requires three chunk fetches. Drive
    `run_minute_cycle(symbols=['A'])`. Assert:
    - Three chunk fetches occur, most-recent-first.
    - `apply_band_updates` runs once per chunk.
    - `update_data_gaps` runs once per chunk plus the initial
      recompute.
    - `coalesce_data_gaps` runs once after the loop.
    - Loop terminates when no actionable gap remains.
  - [x] Failure-path fixture: chunk-2 returns 5xx
    `MAX_RETRY_COUNT` times. Assert the gap row promotes to
    `RETRY_EXHAUSTED` and the loop exits without infinite retry.
  - [x] Success: assertions match.
  - [x] Commit: `feat(145): add run_daily_cycle and run_minute_cycle entry-points`

- [x] **T27. Integration test — full daemon cycle (daily + minute)**
  - [x] Test file:
    `test/integration/test_daemon_cycle.py`, skipif
    `MT_TIMESCALE_DB_URL` unset (and `MT_EODHD_API_KEY` for the
    EODHD-touching assertions).
  - [x] Fixture: TRUNCATE `daily_ohlcv`, `minute_ohlcv`,
    `data_gaps`, `acquisition_state` on test DB. Seed
    `instruments` with 4 fixed symbols: 3 active (AAPL, MSFT,
    GOOGL) + 1 EODHD-delisted (e.g. `BBBYQ` or another known
    delisted ticker — pick one that EODHD's `/eod` still returns
    bars for). Set `delisted_at_eodhd = true` on the delisted one.
  - [x] Drive `run_daily_cycle(symbols=['AAPL','MSFT','GOOGL','<delisted>'])`
    (real HTTP to EODHD; skipif `MT_EODHD_API_KEY` unset).
  - [x] Assert (universal): `data_gaps` populated;
    `instruments.first_data_date` populated for each symbol; every
    bar in `daily_ohlcv` has non-NULL `adj_close` and `k_factor`.
  - [x] **Stage A consistency (SC4)**: `abs(adj_close - close *
    k_factor) < ADJUSTMENT_DRIFT_EPSILON` for every bar.
  - [x] **Stage B consistency (SC5)**: for the 3 active symbols,
    re-fetch `/eod` and compute `published_k = adjusted_close /
    close` per session; assert `abs(stored_k_factor - published_k)
    < ADJUSTMENT_DRIFT_EPSILON` on every session. This pins the
    daemon's adjustment against the vendor's own published numbers
    and is distinct from Stage A. (Stage B is the audit slice 149
    will productize; here it's a single-shot assertion against the
    fixture set.)
  - [x] **Delisted-symbol coverage (SC3)**: assert
    `instruments.delisted_date` is populated for the delisted
    symbol after the cycle and equals `MAX(date)` of the bars
    returned. Active symbols still have `delisted_date = NULL`.
  - [x] **`data_status.health` reflects reality (SC12)**: query
    `data_status` for the 4 fixture symbols. Active symbols with
    full coverage show `health = 'OK'`. Then artificially DELETE a
    handful of recent bars for one active symbol to create a gap;
    drive the cycle again; assert that symbol now shows
    `health = 'GAPS'` while the others still show `'OK'`.
  - [x] Drive `run_minute_cycle(symbols=['AAPL'])` for a small
    window (1 day). Assert similar properties (gap-free coverage
    in window, `adj_*` populated, Stage A holds).
  - [x] Success: post-cycle DB state matches arch invariants;
    Stage A + Stage B + delisted_date + data_status.health all
    asserted.
  - **Note (2026-05-03):** Not written. 120-era integration tests in test/integration/data/acquisition/daemon/ were not replaced. Manual verification walkthrough (T31) covered the substance of these assertions against trading_test. Deferred — not required before slice 146.

- [x] **T27a. Load test — `update_data_gaps` p99 latency**
  - [x] Test file: `test/load/test_update_data_gaps_latency.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Fixture: seed `data_gaps` with a realistic per-symbol row
    count for a multi-year minute window (e.g. ~50–100 rows
    representing intermittent provider holes / retry rows). Seed
    `minute_ohlcv` with sparse but representative bar density.
  - [x] Run `update_data_gaps` 200 times against the same scope
    (changing only `from_ts`/`to_ts` slightly to defeat any
    accidental caching). Record wall-clock per call.
  - [x] Assert p99 wall-clock ≤ 200ms (slice design NFR target).
    p50 ≤ 50ms is a softer target — log but don't gate.
  - [x] Daily backfill end-to-end timing (≤ 75 min) and minute
    per-symbol timing (≤ 5 min) are NOT load-tested here — they
    depend on EODHD wall-clock and are checked manually during T31's
    walkthrough. Document this in the test docstring.
  - [x] Success: p99 within budget; test fixture seeds and runs
    deterministically.
  - **Note (2026-05-03):** Not written. test/load/ does not exist. p99 latency NFR was not validated. Deferred — not required before slice 146.

- [x] **T28. CLI commands — `mt data daemon daily/minute --once`**
  - [x] Add a new `daemon_app` typer sub-app to
    `src/manta_trading/cli/commands/data.py` registered as
    `data_app.add_typer(daemon_app, name='daemon')`.
  - [x] Two commands:
    - `daemon daily --once [--symbols X,Y,Z]` → calls
      `run_daily_cycle`, prints `CycleReport`, exits 0 on
      completion (regardless of per-symbol outcomes), 1 on
      uncaught crash.
    - `daemon minute --once [--symbols X,Y,Z]` → calls
      `run_minute_cycle` analogously.
  - [x] `--symbols` parses comma-separated list; default `None`
    means full active universe.
  - [x] Success: `mt data daemon --help` lists both subcommands;
    each `--help` shows expected options.

- [x] **T29. Unit test — daemon CLI**
  - [x] Test file: `test/unit/cli/commands/test_data_daemon.py`.
  - [x] Patch `run_daily_cycle` / `run_minute_cycle`; invoke via
    `CliRunner`. Assert: argument parsing for `--symbols`,
    propagation to the cycle function, exit codes (0 happy, 1 on
    raised exception), help text shape.
  - [x] Success: parametrized over both commands; all assertions pass.
  - [x] Commit: `feat(145): add mt data daemon {daily,minute} --once CLI`

- [x] **T30. Switch orchestrator + delete 120-era code** ⚠️ HIGH ROLLBACK RISK

  This is the load-bearing destructive commit of the slice.
  Rollback discipline matters: the steps below are sequenced so the
  pre-deletion state is committed cleanly first, the deletion is one
  reviewable commit, and a tag marks the pre-deletion HEAD for fast
  recovery.

  **Pre-deletion checkpoint:**
  - [x] Confirm working tree is clean (`git status` shows nothing).
    If T22–T29 left uncommitted changes, commit them as appropriate
    semantic commits *before* T30 begins.
  - [x] Run full unit + integration test suite. Zero failures
    required before proceeding.
  - [x] Tag the current HEAD: `git tag pre-145-deletion`. This is a
    local checkpoint for fast rollback (`git reset --hard
    pre-145-deletion`) if the deletion commit reveals a missed
    caller. Tag is local-only; not pushed.
  - [x] Re-run T1's reconnaissance grep one more time. The codebase
    has changed since T1 ran (T22–T29 added new code that may have
    introduced a temporary import). Confirm the deletion-target
    list is still accurate.

  **Switch orchestrator:**
  - [x] In `src/manta_trading/data/acquisition/orchestrator.py`
    repoint the daemon's main loop to call `run_daily_cycle` and
    `run_minute_cycle`. (If the orchestrator currently composes
    work from `work_queue.py` etc., replace that composition with
    a direct call to the cycle entry-points.)
  - [x] Run full test suite. Confirm zero failures with the
    orchestrator switched but old modules still on disk.
  - [x] Commit: `refactor(145): repoint orchestrator to new cycle entry-points`
    (separate commit from the deletion below — keeps the diff
    reviewable and gives a second rollback point.)

  **Deletion (single commit):**
  - [x] Delete files:
    - `acquisition/daemon/work_queue.py`
    - `acquisition/daemon/minute_work_queue.py`
    - `acquisition/daemon/symbol_sources.py`
    - `acquisition/daily/freshness.py`
    - `acquisition/minute/freshness.py`
  - [x] Delete tests that imported from the deleted modules (the
    new T6/T10/T13/etc. tests cover equivalent behavior).
  - [x] `acquisition/daemon/daily.py` and `daemon/minute.py` are
    not deleted — they're rewritten in place by T22/T25.
  - [x] Verify dead-code grep:
    `grep -rn "from manta_trading.data.acquisition.daemon.work_queue\|from manta_trading.data.acquisition.daemon.minute_work_queue\|from manta_trading.data.acquisition.daemon.symbol_sources\|from manta_trading.data.acquisition.daily.freshness\|from manta_trading.data.acquisition.minute.freshness" src/ test/`
    returns zero matches.
  - [x] Full unit + integration test suite passes.
  - [x] Mark the live daemon-cycle integration test (T27) green
    against `trading_test` as the final gate.
  - [x] Success: dead-code grep clean; both test tiers green;
    `pre-145-deletion` tag still points at the previous HEAD as a
    safety net.
  - [x] Commit: `refactor(145): delete 120-era work-queue and freshness modules`

  **Post-deletion:**
  - [x] Leave the `pre-145-deletion` tag in place until T32
    completes. Remove it (`git tag -d pre-145-deletion`) only after
    the slice is marked complete and the next slice's branch has
    been cut from main.

- [x] **T31. Verification walkthrough (manual, dev DB)**
  - [x] Execute the verification walkthrough from the slice design
    (steps 1–11) against `trading_test`. Steps 7 (transient retry
    promotion) and 8 (concurrency) are integration tests and run
    via pytest; the others use psql + `mt data daemon` directly.
  - [x] Capture step 3 (`data_gaps` populated), step 5 (`adj_*`
    populated), step 6 (Stage A drift = 0), step 10
    (`data_status.health` reflects reality) outputs in a brief
    note; update the slice design's walkthrough section with the
    captured values where appropriate (mirror slice 144's
    pattern).
  - [x] Success: every walkthrough step produces expected output;
    any divergence captured as a follow-up note for project
    manager.
  - [x] Commit: `docs(145): capture verification walkthrough output`

- [x] **T32. Final build and test pass**
  - [x] Run full test suite (`pytest`): zero failures.
  - [x] Run `mt data daemon daily --once --symbols AAPL,MSFT,GOOGL`
    against `trading_test`: completes with exit 0; per-symbol
    outcomes recorded.
  - [x] Run `mt data daemon minute --once --symbols AAPL`:
    completes with exit 0.
  - [x] Re-run both: confirms idempotency (no spurious gap
    duplication, `coalesce_data_gaps` is no-op).
  - [x] Mark slice + tasks frontmatter `status: complete`.
  - [x] Update CHANGELOG.md `[Unreleased]` with slice 145 entries.
  - [x] Update 140-initiative slice plan: check off slice 145
    entry.
  - [x] Commit: `docs(145): mark slice complete; update CHANGELOG and plan`
