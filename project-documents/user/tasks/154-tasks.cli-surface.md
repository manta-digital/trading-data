---
docType: tasks
slice: 154-cli-surface
project: trading
lld: user/slices/154-slice.cli-surface.md
dependencies:
  - 153-slice.adjusted-on-read-core
projectState: >
  Slice 153 complete: Granularity enum, adjusted() function,
  TimescaleDailyDataDB, and adjusted=True kwarg on minute reader all
  in place. Old CLI commands (daily *, minute *, refetch) still exist.
  No mt data get or mt data pull yet. Branch: 154-slice.cli-surface
  (create from main after 153 merges).
dateCreated: 20260505
dateUpdated: 20260506
status: complete
---

## Context Summary

- Slice 154 adds `mt data get`, `mt data pull`, and `mt data caggs`, then
  deletes the old `daily *`, `minute *`, and `refetch` commands.
- Also adds the daemon's bulk-EOD daily steady-state path.
- Full command specs are in `154-slice.cli-surface.md`; this file is
  implementation tasks only.
- Delete tasks come after their replacement is implemented and tested.

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Verify `main` is current: `git branch --show-current`
  - [x] Create: `git checkout -b 154-slice.cli-surface`
  - [x] Success: branch from clean main

- [x] **T02 — Implement mt data get**
  - [x] Add `get` command to `data_app` in `data.py`
  - [x] Positional args: `symbol`, `granularity` (both required)
  - [x] Validate granularity against `Granularity` enum; clear error on
    unknown token
  - [x] Route to `TimescaleDailyDataDB` for `1d/1w/1mo/1q`; to
    `TimescaleMinuteDataDB` for minute-grain tokens
  - [x] `--raw` → `adjusted=False`; default `adjusted=True`
  - [x] `--start` / `--end` optional; defaults per slice design
  - [x] Output: Rich table default; `--json`; `--csv`
  - [x] `KeyError` from missing `prev_close` → named error message +
    non-zero exit (do not propagate raw exception to user)
  - [x] Success: `mt data get AAPL 1d --start 2024-01-01` returns
    adjusted bars; `--raw` returns unadjusted

- [x] **T03 — Test: mt data get**
  - [x] Unit test: unknown granularity → clear error, non-zero exit
  - [x] Unit test: `--raw` passes `adjusted=False` to DB call
  - [x] Unit test: minute token routes to `TimescaleMinuteDataDB`
  - [x] Unit test: daily token routes to `TimescaleDailyDataDB`
  - [x] Integration test (skipif `MT_TIMESCALE_DB_URL` unset): end-to-end
    via typer test runner; rows present; adjusted by default
  - [x] Success: tests pass

- [x] **T04 — Implement mt data pull**
  - [x] Add `pull` command to `data_app`
  - [x] Positional arg: `granularity` (required; only `1d` and `1m`
    accepted; error clearly for cagg tokens)
  - [x] Symbol selection: `--symbol`, `--symbols`, `--list`, `--universe`
    mutually exclusive; no default; missing selection → clear error
  - [x] `--start` / `--end` optional
  - [x] Default mode: fetch gaps, skip terminal gaps
  - [x] `--verify`: report gaps, no fetch
  - [x] `--reset`: reset terminal markers to `UNKNOWN`; confirmation
    prompt unless `--yes` or `--json`
  - [x] `--dry-run`: preview only
  - [x] `--verify` + `--dry-run` together → error
  - [x] Success: `mt data pull 1d --symbol AAPL` fetches forward;
    `--verify` reports without fetching; `--reset` prompts

- [x] **T05 — Test: mt data pull**
  - [x] Unit test: cagg granularity token → error
  - [x] Unit test: no symbol selector → error
  - [x] Unit test: `--verify` + `--dry-run` → error
  - [x] Unit test: `--reset` without `--yes` triggers confirmation prompt
  - [x] Integration test: `--verify` on symbol with known gaps reports
    correctly
  - [x] Success: tests pass

- [x] **T06 — Commit: get + pull checkpoint**
  - [x] `uv run pyright` — zero errors
  - [x] `uv run pytest test/` — all pass
  - [x] Commit: `feat: add mt data get and mt data pull commands`
  - [x] Success: clean build; both new commands work

- [x] **T07 — Implement mt data caggs subgroup**
  - [x] Add `caggs_app` typer subgroup; register on `data_app`
  - [x] `caggs refresh [--granularity <tokens>] [--start] [--end]`:
    calls `CALL refresh_continuous_aggregate(...)` per cagg; defaults
    to all 7 caggs, full history
  - [x] Validate any granularity tokens against `Granularity` enum
  - [x] `caggs status`: query `timescaledb_information.jobs` and
    `continuous_aggregates`; render per-cagg: last refresh, policy
    schedule, materialized row count
  - [x] Success: `mt data caggs status` shows 7 caggs with policy info;
    `mt data caggs refresh` completes without error

- [x] **T08 — Test: mt data caggs**
  - [x] Unit test: unknown token in `--granularity` → clear error
  - [x] Integration test: `caggs status` returns rows for all 7 caggs
  - [x] Success: tests pass

- [x] **T09 — Delete old CLI commands**
  - [x] Remove `mt data daily {update, update-all, update-file, verify,
    coverage, migrate, symbols}` from `data.py`
  - [x] Remove `mt data minute {update, update-all, backfill, status,
    metrics}` from `data.py`
  - [x] Remove `mt data refetch` command
  - [x] Remove `daily_app` and `minute_app` subgroups (now empty) and
    their `add_typer` registrations
  - [x] Remove all tests for the deleted commands
  - [x] Success: `mt data --help` shows no `daily` or `minute` subgroups;
    `mt data refetch` absent; `grep -n 'daily_app\|minute_app' data.py`
    returns nothing

- [x] **T10 — Test: CLI smoke after deletion**
  - [x] `mt data --help` renders without error; lists `get`, `pull`,
    `caggs`, and surviving commands; no `daily`, `minute`, `refetch`
  - [x] `uv run pytest test/` — all pass (no orphaned tests for deleted
    commands)
  - [x] Success: clean help output; test suite green

- [x] **T11 — Daemon: bulk-EOD daily steady-state**
  - [x] Add `DailyMode` StrEnum (`BACKFILL`, `STEADY_STATE`) to
    `manta_trading.constants`
  - [x] In daemon daily cycle: if all scope members have no `UNKNOWN`
    gaps (caught-up check), use `STEADY_STATE` path —
    one `/eod-bulk-last-day/US` call; parse response, route per symbol
    through existing `update_data_gaps` and ingest path
  - [x] Otherwise `BACKFILL` path: per-symbol `/eod` (existing behavior)
  - [x] Per-symbol `/eod` also retained as the path for `pull --reset`
  - [x] Success: daemon daily cycle in caught-up state issues one bulk
    call; in backfill state issues per-symbol calls

- [x] **T12 — Test: daemon bulk-EOD**
  - [x] Unit test: caught-up scope → bulk endpoint called once, per-symbol
    endpoint not called
  - [x] Unit test: one symbol not caught up → bulk call + one per-symbol
    call for the straggler
  - [x] Success: tests pass

- [x] **T13 — Final build and commit**
  - [x] `uv run pyright` — zero errors
  - [x] `uv run pytest test/` — all pass
  - [x] Manual smoke: `mt data get AAPL 1d`, `mt data pull 1d --symbol AAPL
    --verify`, `mt data caggs status`
  - [x] Commit: `feat(154): add CLI surface; delete old commands; bulk-EOD`
  - [x] Success: branch ready for PM review and merge

---

## Post-merge bug fixes (on main)

- [x] **P01 — Symbol case-fold + QuotaBucket + empty-gap false-success** (committed a8fa4cf)
- [x] **P02 — Minute cold-symbol: seed gaps with UNKNOWN not None** (committed 6aa370a)
- [x] **P03 — Minute per-chunk commit: prevent Ctrl-C data loss** (committed 1f7dc63)
- [x] **P04 — Accept None volume from EODHD (treat as 0)** (committed 66c9288)
- [x] **P05 — STEADY_STATE mode requires bar data, not just no UNKNOWN gaps** (committed a0a234e)
- [x] **P06 — Replace row-by-row execute with executemany** (committed a8fa4cf)
  - [x] `_insert_minute_bars`: was 86k individual INSERTs per chunk → single executemany
  - [x] `_insert_daily_bars`: was per-bar loop → single executemany
  - [x] `upsert_splits` / `upsert_dividends`: was SELECT+INSERT per row → executemany with RETURNING
  - [x] `coalesce_data_gaps._insert_rows`: per-row loop → executemany

- [x] **P07 — Skip compute_missing_ranges for minute granularity**
  - [x] `update_data_gaps`: for `granularity == "minute"`, do not call
    `compute_missing_ranges` — it fetches all stored timestamps (86k rows
    per chunk) and compares against daily session timestamps, which is
    both slow and semantically wrong for sub-daily data
  - [x] For minute seed (`fetch_status_for_unfilled` is not None): insert
    one gap row covering the full window
  - [x] For minute post-fetch success (`fetch_status_for_unfilled` is None):
    insert nothing — window is covered, gap row already deleted in step 3
  - [x] Update unit tests for `update_data_gaps` to cover minute path
  - [x] Verify: full 24-month backfill for priority1 completes in ~10 min
  - [x] Commit: `perf: skip compute_missing_ranges for minute granularity`

- [x] **P08 — Minute history depth: per-symbol floor, not global month cap** (GH #14)
  - [x] Add `EODHD_INTRADAY_HORIZON = date(2004, 1, 1)` constant
  - [x] Add `MT_MINUTE_HISTORY_START` env var (`Settings.minute_history_start: date | None`)
  - [x] Add `_resolve_minute_history_start(conn, symbol, *, operator_floor)`
    helper computing `max(EODHD_INTRADAY_HORIZON, operator_floor,
    instruments.first_listing_date or instruments.first_data_date)`
  - [x] Update `_do_minute_symbol` and `run_minute_refetch` to use the
    resolver (replaces the old global `MINUTE_HISTORY_MONTHS * 30 days`
    cap)
  - [x] Remove dead `_MINUTE_HISTORY_LITERAL` in `migrations/minute.py`
  - [x] Delete `MINUTE_HISTORY_MONTHS` constant — **deferred**: legacy
    `data/acquisition/minute/orchestrator.py` and
    `data/acquisition/minute/freshness.py` modules still import it.
    Those modules are unreachable from current CLI paths but still
    have unit/integration tests. Constant marked DEPRECATED in-source;
    deletion goes with the legacy-module cleanup.
  - [x] Tests: 9 new in `test_resolve_minute_history_start.py`; existing
    `test_minute.py` updated to patch the resolver instead of
    `_first_data_date` and assert against `EODHD_INTRADAY_HORIZON`.
  - [x] Commit: `feat: per-symbol minute history floor; add MT_MINUTE_HISTORY_START`

- [x] **P09 — Minute gap shrink: per-chunk gap arithmetic, not full-window replace**
  - Symptom: minute backfill makes one chunk of progress per symbol per
    daemon run, then loops re-fetching the same trailing chunk. After many
    runs `data_gaps` shows the same gap row with `attempt_count` climbing
    while `minute_ohlcv` rowcount stays flat (or grows by exactly one
    chunk's worth of bars).
  - Root cause: in `_do_minute_symbol`'s chunk loop, `update_data_gaps` is
    called with the **chunk** bounds, not the **gap** bounds. Inside
    `update_data_gaps`, `_delete_intersecting` uses containment
    (`gap_start >= from_ts AND gap_end <= to_ts`) — misnamed; it should
    be called `_delete_contained`. When the picked gap spans (e.g.) 240
    months and the chunk covers only the trailing 120 days, the gap is
    not contained in the chunk, so the delete misses it. On SUCCESS,
    `fetch_status_for_unfilled is None`, so no replacement row is
    inserted either. Result: the gap is left untouched, the same chunk
    is picked next iteration, and the loop never converges. On
    PARTIAL/EMPTY, the chunk-bounded row that gets inserted does not
    represent the unfetched portion of the original gap.
  - Fix: replace the `update_data_gaps` call inside the chunk loop with
    a `_advance_minute_gap(picked_gap, chunk_start, chunk_end, outcome)`
    helper that operates on the **picked gap row directly**:
    - SUCCESS: shrink the picked row — if `chunk_start <= gap.gap_start`,
      DELETE; else UPDATE `gap_end = chunk_start`. (Loop fetches
      newest-first; chunk is always the trailing slice of the gap.)
    - PARTIAL / TRANSIENT_FAILURE: the chunk portion stays UNKNOWN /
      FAILED_RETRYABLE with carried-forward attempt_count; the
      pre-chunk portion stays UNKNOWN. Implement as: shrink the picked
      row's `gap_end` to `chunk_start`, then INSERT a new row for
      `[chunk_start, chunk_end]` with the new status and incremented
      attempt count. If `chunk_start <= gap.gap_start`, just UPDATE the
      picked row's status/attempt_count in place.
    - EMPTY (PROVIDER_HOLE): same shape as PARTIAL but with terminal
      status on the chunk portion.
    - The seed `update_data_gaps` call before the loop stays as-is — it
      establishes the initial gap window. The chunk loop replaces only
      the inner `update_data_gaps` call.
  - Keep acquisition_state upsert: extract a small `_record_attempt`
    helper or call the existing `_update_acquisition_state` directly.
  - Cost: per chunk, one indexed PK UPDATE (or DELETE) on `data_gaps`
    plus at most one INSERT for failure cases. Bar COPY dominates;
    gap-row work is not on the hot path.
  - No fixed-period anchors anywhere in the new helper — it operates on
    whatever bounds the picked gap row carries. The helper must be
    correct for gap windows from one day to 240+ months.
  - [x] Add `_advance_minute_gap` in
    `src/manta_trading/data/acquisition/daemon/minute.py`
  - [x] Replace inner `update_data_gaps` call in `_do_minute_symbol`
  - [x] Unit tests covering: SUCCESS shrink-to-DELETE, SUCCESS
    shrink-to-UPDATE, PARTIAL split (UPDATE + INSERT), PARTIAL when
    chunk covers full gap (in-place UPDATE), EMPTY (PROVIDER_HOLE),
    TRANSIENT_FAILURE
  - [x] **Trailing-weekend tolerance:** `classify_outcome` returned
    PARTIAL whenever `latest_bar_ts.date() < range_end.date()`. With
    `chunk_end = gap.gap_end = now_midnight (UTC)`, a Sun chunk_end
    means EODHD never has a bar at that date — every retry produced
    duplicates and attempt_count climbed without termination. Daemon
    now overrides PARTIAL→SUCCESS when bars were received and
    `chunk_end - latest_bar.date() <= 4 days` (covers Fri→Mon and
    long-weekend holidays). Helper: `_latest_bar_dt`.
  - [x] **Defense in depth:** `_advance_minute_gap` promotes UNKNOWN
    (PARTIAL outcome) as well as FAILED_RETRYABLE to RETRY_EXHAUSTED
    at MAX_RETRY_COUNT, so a previously-unforeseen mode that produces
    PARTIAL on every retry can never spin the chunk loop forever.
  - [x] Manual verification: AAPL (single symbol, two adjacent gaps
    spanning ~360 days) converged from 80,834 bars → 467,947 bars,
    `data_gaps` empty, "Pull complete: 1 succeeded, 0 failed".
  - [x] Commit: `fix: minute backfill convergence — shrink gap rows; tolerate trailing weekends`
