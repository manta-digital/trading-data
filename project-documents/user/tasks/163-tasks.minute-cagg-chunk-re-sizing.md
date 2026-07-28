---
docType: tasks
slice: minute-cagg-chunk-re-sizing
project: trading-data
lld: user/slices/163-slice.minute-cagg-chunk-re-sizing.md
dependencies: [152, 166]
projectState: >
  Slice 166 complete: minute_ohlcv rechunked to 7-day chunks (1,204 chunks),
  raw table healthy at 4,405,379,285 rows exact. That rechunk invalidated all
  four minute caggs: they hold ~20.8% of raw overall (~79% under-materialized)
  and ARE the serving path (materialized_only=true). The caggs are also
  over-chunked ~40x (1.67-day interval, ~4,236 chunks each). This slice fixes
  both with one sweep and blocks slice 167. TimescaleDB 2.23.0 / PostgreSQL
  17.7 on prod trading DB (192.168.1.144).
dateCreated: 20260721
dateUpdated: 20260725
status: complete
---

## Context Summary

- Working on **163 — minute-cagg chunk re-sizing + full re-materialization
  repair**. Two defects, one repair path (design §Overview): 70-day chunk
  interval on the four minute caggs' mat hypertables, then a windowed
  drop→refresh→compress sweep that rebuilds materialization from raw.
- Mechanism (design D1): per 70-day epoch-grid window, oldest→newest, one cagg
  at a time: `drop_chunks` → `refresh_continuous_aggregate(..., force => true)`
  → `compress_chunk`. **Not transactional** (refresh cannot run in a txn
  block); resumability is **parity-derived**: a window is DONE iff cagg
  `SUM(minute_count)` == raw bounded `COUNT(*)`. No stage/lock — cagg data is
  derived; 166's lock discipline deliberately does not transfer.
- Deliverables: constants, migrations 044/045, parity/repair core modules,
  `mt data caggs verify` and `mt data caggs repair` subcommands (joining the
  existing slice-154 group), the prod repair execution, and the standing
  post-restructuring verify/repair rule in help text.
- Key constraints (from design + standing project rules):
  - Every prod query under explicit `statement_timeout`; cancel the
    server-side backend on client interrupt (journal 20260720; review F005).
  - Per-cagg job pause is a **correctness** control (D4); pre-flight refuses
    if target jobs are unpaused. Daemon may keep running.
  - Columnstore on the mat hypertables is **mandatory** (D3) — full
    uncompressed materialization (~300 GB) does not fit the cluster.
  - Availability during repair = bounded per-window zero-coverage gaps
    (design D1/F003); prod sweep runs outside market hours.
  - Chunk interval and compress_after defined **once** in `constants.py`.
- Blocks slice 167 (cagg-backed data_status). Interfaces 162 (coverage reads
  the 4h cagg — regression check at the end) and 164/182 (inherit fast reads).
- Next planned slice after 163: **167**.

## Anchors (verified 2026-07-21, do not re-derive)

- Migrations are dicts in `MINUTE_MIGRATIONS`
  (`src/manta_trading/market/schema/migrations/minute.py`); highest id is
  `043_minute_chunk_interval_7d` (line ~1567); **next ids are `044`, `045`**.
  043 is the `set_chunk_time_interval` precedent (renders SQL from a
  constant); 042 (`_setup_and_backfill_compression`, `requires_autocommit:
  True`, python_fn) is the columnstore-enable precedent.
- `test/unit/test_schema_migrations.py:142` asserts
  `len(MIGRATIONS) == 46` → becomes **48**.
- Maintenance-tool precedent: `src/manta_trading/market/maintenance/rechunk.py`
  (364 lines) — `WindowState` StrEnum, `Window` dataclass, `PreflightError`,
  `_window_start` (epoch-grid alignment), `_resolve_paused_job_violations`
  (catalog-based job checks), `run_rechunk(url, dry_run=...)`. Reuse its
  shapes; do not copy-paste logic that can be shared.
- `MINUTE_CAGG_GRANULARITIES` already exists at `rechunk.py:45` — reuse it
  (relocate to a shared module only if the import direction is wrong).
- CLI: `caggs_app` at `cli/commands/data.py:59`; existing subcommands
  `refresh` (line ~2693) and `status` (line ~2829). New subcommands join this
  group (design D5, review F001).
- Constants live in `src/manta_trading/constants.py`
  (`MINUTE_OHLCV_CHUNK_INTERVAL: timedelta = timedelta(days=7)` at line 42 is
  the naming precedent; `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT` at line 81
  is the statement-timeout-constant precedent).
- Refresh-policy jobs observed 2026-07-20: 1007 (5min), 1008 (15min),
  1002 (hourly), 1003 (4h) — **always resolve at runtime from
  `timescaledb_information.jobs`, never hardcode** (design §Baseline).
- Mat hypertables: mat_3 (5min) … mat_6 (4h) — resolve by **view name** via
  the catalog, never by mat table name.
- Whole-`test/` collection is broken (missing `__init__.py`); run tests
  per-subpackage. `uv run pytest/mypy/ruff` require `--extra dev`.
- No `tool-guides/timescaledb/` exists; curated knowledge = journal entries
  (adjacency, cagg-collision, 20260720 query discipline) + slice 166 design.
- Prod psql access: extract `MT_TIMESCALE_DB_URL` from `.env` with grep/cut —
  never shell-source `.env`.

---

## Phase A — Constants and migrations (effort: 2)

### Task A1: Add cagg chunk-interval and compression constants
- [x] In `src/manta_trading/constants.py` add
      `MINUTE_CAGG_CHUNK_INTERVAL: timedelta = timedelta(days=70)` with a
      comment citing the journal wall-clock rule (span ÷ target count; 22.5 y
      / 70 d ≈ 117 chunks — design D2).
- [x] Add `MINUTE_CAGG_COMPRESS_AFTER: timedelta` for the columnstore
      policies (design D3). Value must be **greater than** the refresh
      policies' `start_offset` (1 day) so the policy never compresses inside
      the actively-refreshed head; use 7 days (mirrors the raw-table 042
      policy precedent) and record the >1-day constraint in the comment.
- [x] Add `MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT: str` for verify/repair
      prod queries (precedent: `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`).
- [x] Success: constants importable; no other file defines these values;
      `uv run --extra dev ruff check` and `mypy` clean on the touched file.

### Task A2: Migration 044 — cagg chunk interval 70 d
- [x] Add `044_minute_cagg_chunk_interval_70d` to `MINUTE_MIGRATIONS`
      following 043's pattern: `set_chunk_time_interval` on each of the four
      minute caggs, interval rendered from `MINUTE_CAGG_CHUNK_INTERVAL`.
- [x] Resolve mat hypertables **by continuous-aggregate view name** from
      `timescaledb_information.continuous_aggregates` (never `mat_N`
      literals). A DO-block or `python_fn` is acceptable if plain SQL can't
      express the lookup cleanly.
- [x] Description documents: idempotent; affects future chunks only; existing
      1.67-day chunks are rewritten by `mt data caggs repair`; cold-start
      no-op (post-043 source at 7 d → 10× = 70 d automatic, design D2);
      manual revert statement.
- [x] Success: migration registered; id sequence and description conventions
      match 040–043.

### Task A3: Unit tests for migration 044
- [x] In `test/unit/test_schema_migrations.py`: update the count assertion
      (46 → 47 at this point), assert 044's id/ordering, and assert the
      rendered interval derives from `MINUTE_CAGG_CHUNK_INTERVAL` (test must
      fail if someone hardcodes `'70 days'` divorced from the constant —
      pattern: the existing 043 interval test).
- [x] Success: `uv run --extra dev pytest test/unit/test_schema_migrations.py`
      passes.

### Task A4: Migration 045 — cagg columnstore enable + policies
- [x] Add `045_minute_cagg_columnstore` following 042's pattern (`python_fn`,
      `requires_autocommit: True` as needed): for each of the four caggs,
      enable columnstore with `segmentby = symbol`,
      `orderby = time_bucket DESC`, and add a compression policy with
      `compress_after` rendered from `MINUTE_CAGG_COMPRESS_AFTER`.
- [x] Idempotent: re-running must not error when settings/policies already
      exist (042 precedent).
- [x] Unlike 042, **no backfill-compress** of existing chunks: existing
      wrong-interval chunks are dropped by repair; the sweep compresses new
      chunks itself (compress-behind-frontier, design D3). Record this
      difference in the description.
- [x] Success: migration registered; four caggs covered; no `mat_N` literals.

### Task A5: Unit tests for migration 045
- [x] Update count assertion to **48**; assert 045's id/ordering,
      autocommit flag, and that `compress_after` derives from the constant.
- [x] Success: schema-migrations test file passes.

### Task A6: Commit checkpoint
- [x] `uv run --extra dev pytest` on `test/unit` (per-subpackage as needed),
      `ruff`, `mypy` on touched files; commit
      (`feat: add minute-cagg chunk interval and columnstore migrations 044/045`).

## Phase B — Parity core and `mt data caggs verify` (effort: 3)

### Task B1: Parity computation module
- [x] New module `src/manta_trading/market/maintenance/cagg_parity.py`
      (~300-line file budget; split only if actually needed):
  - [x] Window enumeration: 70-day epoch-grid windows (1970-01-01 + k×70 d)
        covering the raw table's `[min(time), max(time)]` — reuse/share
        `rechunk.py`'s `_window_start` grid logic rather than duplicating.
  - [x] Per-window parity: cagg `SUM(minute_count)` vs raw bounded
        `COUNT(*)` over the same window; result carries both counts and a
        derived state (reuse or mirror `WindowState`).
  - [x] Per-year rollup for report mode (verify's default view; per-window is
        `--detail` — design D5).
  - [x] Chunk-count and `chunk_time_interval` summary per cagg from the
        catalog.
  - [x] Every query runs under
        `MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT`; on client interrupt or
        timeout, `pg_cancel_backend` the server-side backend before raising
        (journal 20260720 discipline; review F005).
- [x] Success: module importable; zero mutation paths (read-only by
      construction); ruff/mypy clean.

### Task B2: Unit tests for parity module
- [x] New `test/unit/market/test_cagg_parity.py` (pattern:
      `test_rechunk.py`): grid alignment (straddling range → two windows),
      parity state derivation (equal → DONE; 0 vs n → PENDING; partial →
      PENDING), per-year rollup math, granularity filtering. Mock the DB
      boundary; test logic with real numbers from the design's baseline
      table (e.g. 2019: 208,673,609 raw vs 43,440,140 cagg → 20.8%).
- [x] Success: tests pass; parity states cover the three D1 crash-window
      outcomes.

### Task B3: `mt data caggs verify` subcommand
- [x] Add `verify` to `caggs_app` (`cli/commands/data.py`) with
      `--granularity 5m|15m|1h|4h|all` (default all) and `--detail`
      (per-window instead of per-year). Reuse the granularity-token parsing
      already used by `caggs refresh`.
- [x] Output: per-year (or per-window) cagg-vs-raw counts, coverage %, parity
      pass/fail; per-cagg chunk count + interval summary; non-zero exit code
      when any parity failure exists (script-friendly detector).
- [x] Help text states the **standing rule**: run `verify` after any raw
      `minute_ohlcv` restructuring; on parity failure run
      `mt data caggs repair` (design D5).
- [x] Success: `mt data caggs verify --help` accurate; read-only; timeout
      discipline inherited from the parity module.

### Task B4: Unit tests for verify CLI
- [x] CLI-level tests (pattern: existing `test_data_ca.py` /
      caggs-command tests): granularity parsing, exit-code behavior on
      parity failure vs full parity, `--detail` switch. Mock the parity
      module boundary.
- [x] Success: tests pass.

### Task B5: Commit checkpoint + prod baseline capture
- [x] Commit (`feat: add cagg parity core and mt data caggs verify`).
- [x] Run `mt data caggs verify` against prod (read-only; walkthrough
      step 1). Expected: ~20.8% overall coverage, parity failures across all
      years/granularities — the corruption made visible for the first time.
- [x] Save the full output to
      `project-documents/user/notes/163-baseline-verify-20260721.md` (or
      dated as run) together with the saved single-symbol 4h `EXPLAIN
      ANALYZE` baseline (~2 s, from 162 prep — re-capture if not on file).
- [x] Success: baseline artifact committed
      (`docs: capture 163 pre-repair parity and EXPLAIN baselines`).

## Phase C — Repair sweep and `mt data caggs repair` (effort: 4)

### Task C1: Pre-flight checks
- [x] New module `src/manta_trading/market/maintenance/cagg_repair.py`,
      pre-flight section (refuse — don't warn — on any failure, raising a
      `PreflightError`-style typed error; 166 pattern):
  - [x] Target cagg's refresh policy **and** (post-045) columnstore policy
        are paused — job IDs resolved from `timescaledb_information.jobs`
        by cagg/mat-hypertable association at runtime; refusal message
        prints the exact job IDs and the pause command.
  - [x] Mat hypertable `chunk_time_interval` equals
        `MINUTE_CAGG_CHUNK_INTERVAL` (i.e. migration 044 applied), read from
        the catalog.
  - [x] Disk headroom on the DB host sufficient for the sweep's peak
        (roughly one uncompressed window per cagg plus existing footprint —
        design D3); refuse with measured numbers. Derive free space via SQL
        available to the connection; if no reliable SQL source
        exists, require an explicit `--assume-headroom-gb` operator input
        rather than silently skipping the check.
- [x] Raw-table jobs (e.g. columnstore 1009) are **out of scope** — assert
      the pre-flight never touches them.
- [x] Success: each check individually testable; refusal messages actionable.

### Task C2: Unit tests for pre-flight
- [x] Tests: unpaused refresh job → refuse; unpaused columnstore policy →
      refuse; wrong interval (044 unapplied) → refuse; insufficient headroom
      → refuse; all-clear → pass. Mock catalog responses.
- [x] Success: tests pass.

### Task C3: Window sweep
- [x] Sweep section of `cagg_repair.py`, per design D1 exactly — for one
      cagg, over 70-day grid windows oldest → newest:
  - [x] Parity check first (reuse `cagg_parity`); window at parity → skip
        (this is the resumability and the incremental-repair property).
  - [x] `drop_chunks()` on the cagg over the window.
  - [x] `refresh_continuous_aggregate(cagg, start, end, force => true)` —
        `force` because invalidation entries were already consumed; document
        in a comment. **No enclosing transaction** — the three steps commit
        independently (D1 crash-window enumeration).
  - [x] `compress_chunk()` on the window's chunk(s) (compress-behind-
        frontier; a grid-straddling table edge may yield two chunks —
        handle by compressing all uncompressed chunks in the window).
  - [x] Per-window progress output (window bounds, raw count, elapsed) —
        the operator watches a multi-hour sweep.
  - [x] Ctrl-C safe: interrupt cancels the server-side backend, exits
        cleanly; next invocation resumes via parity skip.
- [x] Multi-granularity: sweep one cagg at a time in fixed order
      (4h → 1h → 15m → 5m: smallest first, matching the walkthrough).
- [x] Success: function signature mirrors `run_rechunk` (url, granularities,
      dry_run); no bookkeeping table — state is parity-derived only.

### Task C4: Unit tests for sweep
- [x] New `test/unit/market/test_cagg_repair.py`: parity-skip (window at
      parity → no drop/refresh calls), full-rebuild path ordering
      (drop → refresh → compress), resume-after-kill simulation (first run
      dies after drop_chunks → second run rebuilds that window),
      kill-before-compress (parity passes → only compression re-attempted),
      dry-run performs zero mutations. Mock the DB boundary; assert call
      order, not SQL text.
- [x] Success: tests pass; the three D1 crash windows each have a test.

### Task C5: `mt data caggs repair` subcommand
- [x] Add `repair` to `caggs_app` with `--granularity` (as verify) and
      `--dry-run` (prints planned windows and per-window parity states, no
      mutation — design D5).
- [x] Help text documents: pre-flight requirements (paused jobs, migrations,
      headroom), bounded per-window serving gaps + off-hours guidance
      (design D1/F003), resumability, and the **standing rule** (same wording
      as verify's).
- [x] Success: command wired; typed exit codes (pre-flight refusal vs
      completion vs interrupt) following the `data rechunk` precedent.

### Task C6: Unit tests for repair CLI
- [x] CLI-level tests: dry-run flag propagation, granularity parsing,
      pre-flight refusal surfaces as the documented exit code. Mock
      `cagg_repair` boundary.
- [x] Success: tests pass.

### Task C7: Commit checkpoint
- [x] Full per-subpackage unit-test run, ruff, mypy; commit
      (`feat: add mt data caggs repair windowed re-materialization sweep`).

## Phase D — Prod execution and verification (effort: 3)

All prod psql work under `SET statement_timeout` per standing discipline.
Run the repair sweeps outside market hours (design D1 availability note).
The minute daemon **may keep running** throughout (design D4).

### Task D1: Apply migrations to prod
- [x] Apply 044/045 via the standard migration path.
- [x] Confirm via `timescaledb_information.dimensions`: all four mat
      hypertables report 70-day interval; compression settings + policies
      exist for all four.
- [x] Success: catalog output recorded (walkthrough step 2).

### Task D2: Dry run and pre-flight refusal check
- [x] `mt data caggs repair --dry-run` → planned windows per cagg with
      parity states; verify zero mutation (chunk counts unchanged).
- [x] Run repair with jobs **unpaused** → confirm pre-flight refuses with
      actionable message listing job IDs (walkthrough step 4).
- [x] Pause the four refresh-policy jobs + four columnstore policies using
      the IDs the tool printed.
- [x] Success: refusal observed, then pre-flight passes with jobs paused.

### Task D3: Repair 4h cagg first, with kill/resume exercise
- [x] `mt data caggs repair --granularity 4h` (smallest, ~9 GB full);
      watch per-window progress.
- [x] Once mid-run: Ctrl-C, confirm clean exit and backend cancelled
      (`pg_stat_activity`), re-run, observe parity-skip fast-forward to the
      interrupted window (success criterion 5).
- [x] On completion: `mt data caggs verify --granularity 4h` → full parity
      within the trailing refresh-lag bound.
- [x] Success: 4h cagg at 70-day chunks (~117), compressed, full parity.

### Task D4: Capture the query win
- [x] `EXPLAIN ANALYZE` single-symbol `minute_4hour_ohlcv` query (same query
      as the B5 baseline): expect chunk fan-out collapse and ~2 s →
      sub-100 ms order (success criterion 3).
- [x] Append before/after to the B5 baseline artifact.
- [x] Success: comparison committed
      (`docs: record 163 4h-cagg repair EXPLAIN before/after`).

### Task D5: Repair remaining granularities
- [x] `repair --granularity 1h`, then `15m`, then `5m` — separate runs,
      disk monitored between runs (compress-behind-frontier should hold
      peak bounded; abort and reassess if footprint approaches headroom).
- [x] `mt data caggs verify` (all) → full parity every year, every
      granularity (success criterion 2).
- [x] Record final chunk counts (~117/cagg, success criterion 1) and total
      minute-cagg footprint (expected ~30–40 GB, not ~300 GB — success
      criterion 4; design D3 estimate verified).
- [x] Success: all measurements recorded in the baseline artifact.

### Task D6: Resume jobs and steady-state check
- [x] Resume the eight paused jobs; verify each `last_run_status = 'Success'`
      after next scheduled run (success criterion 6); daemon uninterrupted.
- [x] Next trading day: `mt data caggs verify` still at full parity within
      refresh lag (trailing policy healing works — walkthrough step 8).
- [x] Success: job statuses + next-day verify output recorded.

### Task D7: 162 regression and cold-start verification
- [x] Re-run slice 162's coverage query path (4h-cagg reads): correct,
      complete-data results (success criterion 8; results *changed for the
      better* where prior reads touched corrupted regions — design
      §Interfaces).
- [x] Cold-start: run the existing cold-start integration flow against a
      throwaway DB; confirm 70-day mat intervals + compression from
      migrations alone, repair tool never invoked (success criterion 7).
- [x] Success: both outcomes recorded.

### Task D8: Close-out
- [x] Audit all eight design success criteria against recorded evidence;
      note any deviation for PM.
- [x] Add journal entry (`user/notes/000-process-journal.md`): repair
      executed, parity restored, standing verify/repair rule now live for
      the ~2026-07-23 raw rechunk re-run (walkthrough step 10 happens there).
- [x] Update slice/task doc statuses via task-checker; final commit; slice
      branch ready for merge per git rules.
- [x] Success: slice complete; 167 unblocked.
