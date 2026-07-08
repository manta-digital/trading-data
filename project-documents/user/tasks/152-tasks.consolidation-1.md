---
docType: tasks
slice: 152-consolidation
project: trading
lld: user/slices/152-slice.consolidation.md
part: 1
partOf: ~
dependencies:
  - 145-slice.daemon-refactor
  - 146-slice.daemon-scheduling
  - 147-slice.status-command
  - 148-slice.mt-data-refetch
projectState: >
  Slices 145–148 complete and merged to main. Branch 149 exists but will
  not merge — its work is superseded by 152. adjusted-on-write pipeline
  (band_writer, ca_drift, adj_* columns) still live in the tree.
  MarketDB still active alongside TimescaleDB. AlphaVantage code still
  present. 11 legacy minute caggs exist (some as _v2 variants).
  daily_ohlcv has no caggs. Branch: 152-slice.consolidation (create
  from main before starting).
dateCreated: 20260505
dateUpdated: 20260505
status: complete
---

## Context Summary

- Slice 152 eliminates adjusted-on-write, MarketDB, AlphaVantage, and
  the audit/drift machinery (~3000 lines deleted). Replaces them with
  adjusted-on-read via one ~80-line function.
- Part 1 covers: architecture amendment, branch setup, splits/dividends
  migration, schema column removals, cagg rebuild, and all demolition.
- Part 2 covers: `adjustment.py` module, `TimescaleDailyDataDB`,
  programmatic `adjusted=True` kwarg, new CLI verbs (`get`, `pull`,
  `caggs`), old CLI deletion, daemon bulk-EOD path, and final validation.
- First task is the architecture amendment — the arch doc must never
  say "we use adjusted-on-write" while the code says otherwise.
- Demolition tasks come after their replacements are in place and tested.
- Branch 149 must NOT be merged. Drop stash@{0} on that branch when
  convenient (`git stash drop`).

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Verify current branch is `main`: `git branch --show-current`
  - [x] Create branch: `git checkout -b 152-slice.consolidation`
  - [x] Confirm branch created from clean main (no outstanding changes)
  - [x] Success: `git branch --show-current` prints `152-slice.consolidation`

- [x] **T02 — Architecture amendment**
  - [x] Open `project-documents/user/architecture/140-arch.data-quality-operations.md`
  - [x] Mark every adjusted-on-write section as superseded by slice 152
  - [x] Add a new section describing adjusted-on-read: one function, no
    daemon recompute, no stored adj_* columns
  - [x] Commit: `docs: amend 140-arch to describe adjusted-on-read (152)`
  - [x] Success: arch doc describes the post-152 architecture; no section
    still claims adj_* columns are written at ingest without a
    "superseded" marker

- [x] **T03 — Slice plan entry**
  - [x] Open `project-documents/user/architecture/140-slices.data-quality-operations.md`
  - [x] Add slice 152 entry with title, effort, status: planned
  - [x] Mark slices 150 and 151 as superseded by 152
  - [x] Commit: `docs: add 152 to slice plan; mark 150/151 superseded`
  - [x] Success: plan lists 152; 150 and 151 each have a superseded note

- [x] **T04 — Migration: create splits/dividends in TimescaleDB**
  - [x] Locate migration registry in
    `src/manta_trading/market/schema/migrations/`
  - [x] Write a new migration that creates `splits` and `dividends`
    tables in TimescaleDB (same column shapes as MarketDB originals —
    verify by reading `marketdb.py` for the schema)
  - [x] Migration must be idempotent (skip if tables already exist)
  - [x] Register migration in the appropriate `*_MIGRATIONS` list
  - [x] Success: `mt data migrate-cold-start --skip-probe --yes` applies
    the migration cleanly; re-running is a no-op

- [x] **T05 — Test: splits/dividends migration**
  - [x] Write an integration test (skipif `MT_TIMESCALE_DB_URL` unset)
    that applies the migration to a test DB and asserts both tables
    exist with the correct column set
  - [x] Assert idempotency: apply twice, no error
  - [x] Success: `pytest` passes for this test with a live test DB

- [x] **T06 — Migration: copy splits/dividends rows from MarketDB**
  - [x] Write a one-shot data migration (separate from T04's schema
    migration) that SELECTs all rows from MarketDB `splits` and
    `dividends` and INSERTs them into the TimescaleDB equivalents
  - [x] Use ON CONFLICT DO NOTHING (idempotent)
  - [x] Migration must guard against MarketDB being unreachable: if
    `MT_MARKET_DB_URL` is unset or connection fails, log a warning
    and skip (data may already be migrated)
  - [x] Success: after running, row counts in TimescaleDB
    `splits`/`dividends` match MarketDB originals (verify manually
    with psql)

- [x] **T07 — Test: data copy migration**
  - [x] Write an integration test that inserts sentinel rows into a
    test MarketDB `splits`/`dividends`, runs the migration, and
    asserts the rows appear in TimescaleDB
  - [x] Assert ON CONFLICT DO NOTHING: re-running does not duplicate rows
  - [x] Success: pytest passes

- [x] **T08 — Migration: drop adj_* columns from daily_ohlcv**
  - [x] Write a migration that drops `adj_open`, `adj_high`, `adj_low`,
    `adj_close`, `k_factor`, `adjusted_at` from `daily_ohlcv`
  - [x] Drop `last_adjusted_ca_snapshot_id` from `acquisition_state`
  - [x] Migration must be idempotent (DROP COLUMN IF EXISTS)
  - [x] Success: migration applies cleanly; columns absent from schema
    (`\d daily_ohlcv` shows no adj_* or k_factor columns)

- [x] **T09 — Migration: drop adj_* columns from minute_ohlcv**
  - [x] Write a migration that drops `adj_open`, `adj_high`, `adj_low`,
    `adj_close`, `k_factor`, `adjusted_at` from `minute_ohlcv`
  - [x] Migration must be idempotent
  - [x] Success: migration applies cleanly; columns absent

- [x] **T10 — Test: column removal migrations**
  - [x] Write integration tests for T08 and T09: apply migrations,
    assert columns absent, assert idempotency
  - [x] Success: pytest passes

- [x] **T11 — Commit: migrations checkpoint**
  - [x] Run `mt data migrate-cold-start --skip-probe --yes` against dev DB
  - [x] Verify all migrations T04/T08/T09 applied
  - [x] Commit: `feat: add splits/dividends to timescale; drop adj_* columns`
  - [x] Success: clean build, all migration tests pass

- [x] **T12 — Cagg rebuild: drop legacy minute caggs**
  - [x] Write migration to `DROP MATERIALIZED VIEW IF EXISTS ... CASCADE`
    for all 11 legacy caggs:
    `minute_5min_ohlcv`, `minute_5min_ohlcv_v2`,
    `minute_15min_ohlcv`, `minute_15min_ohlcv_v2`,
    `minute_hourly_ohlcv`, `minute_hourly_ohlcv_v2`,
    `minute_4hour_ohlcv`, `minute_4hour_ohlcv_v2`,
    `minute_daily_ohlcv`, `minute_weekly_ohlcv`, `minute_monthly_ohlcv`
  - [x] CASCADE removes associated policy jobs automatically
  - [x] Success: migration applies; zero caggs remain on `minute_ohlcv`

- [x] **T13 — Cagg rebuild: create 4 raw minute caggs**
  - [x] Write migration creating `minute_5min_ohlcv`, `minute_15min_ohlcv`,
    `minute_hourly_ohlcv`, `minute_4hour_ohlcv` over `minute_ohlcv`
  - [x] Projection: raw `FIRST(open)/MAX(high)/MIN(low)/LAST(close)/SUM(volume)/COUNT(*)`
  - [x] No WHERE filter — every row participates
  - [x] See slice design "Continuous aggregates" section for SQL shape
  - [x] Success: 4 caggs exist; each view definition references `minute_ohlcv`
    with raw column projection

- [x] **T14 — Cagg rebuild: create 3 daily caggs over daily_ohlcv**
  - [x] Write migration creating `daily_weekly_ohlcv`, `daily_monthly_ohlcv`,
    `daily_quarterly_ohlcv` over `daily_ohlcv`
  - [x] Same raw projection shape as T13
  - [x] Success: 3 caggs exist; each references `daily_ohlcv`

- [x] **T15 — Cagg rebuild: install refresh policies**
  - [x] Write migration adding one `add_continuous_aggregate_policy` per
    cagg (7 total)
  - [x] Minute caggs: `start_offset` wide enough to absorb late raw bars
    from backfill; `end_offset` = bucket size; schedule = bucket size
  - [x] Daily caggs: `start_offset = 7 days`, `end_offset = 1 day`,
    `schedule_interval = 1 day`
  - [x] Success: `timescaledb_information.jobs` shows 7 policy rows,
    one per cagg

- [x] **T16 — Update AGGREGATION_VIEWS and timescale_init**
  - [x] Update `TimescaleMinuteDataDB.AGGREGATION_VIEWS` in
    `src/manta_trading/market/timescale_minute_db.py` — drop `_v2`
    suffixes; keys remain `5min/15min/1hour/4hour`
    (granularity token rename is Part 2)
  - [x] Update or remove `timescale_init.create_continuous_aggregations`
    so cold-start bootstrap no longer creates stale raw-projection
    caggs under the old names; align with migration output
  - [x] Success: `TimescaleMinuteDataDB.AGGREGATION_VIEWS` has no `_v2`
    strings; cold-start bootstrap test passes

- [x] **T17 — Test: cagg migrations**
  - [x] Integration test: apply T12–T15 migrations; assert exactly 4
    minute caggs and 3 daily caggs exist; assert 7 policy rows
  - [x] Unit test: `AGGREGATION_VIEWS` map has no `_v2` keys
  - [x] Success: pytest passes

- [x] **T18 — Commit: cagg checkpoint**
  - [x] Verify `mt data migrate-cold-start --skip-probe --yes` applies
    all cagg migrations cleanly
  - [x] Run a manual `CALL refresh_continuous_aggregate` for one minute
    cagg and one daily cagg; assert rows return
  - [x] Commit: `feat: rebuild caggs — raw projection, daily caggs added`
  - [x] Success: clean build, cagg tests pass

- [x] **T19 — Delete adjustment package**
  - [x] Remove `src/manta_trading/data/adjustment/band_writer.py`
  - [x] Remove `src/manta_trading/data/adjustment/verify.py`
  - [x] Remove `src/manta_trading/data/adjustment/verify_eod.py`
  - [x] Remove `src/manta_trading/data/adjustment/audit.py`
  - [x] Remove `src/manta_trading/data/adjustment/context.py`
  - [x] Update `src/manta_trading/data/adjustment/__init__.py` to remove
    re-exports of the above (file stays as stub for Part 2's new module)
  - [x] Remove all unit and integration tests for these modules
  - [x] Success: no import of `band_writer`, `verify`, `verify_eod`,
    `audit`, or `context` anywhere in `src/` or `test/`

- [x] **T20 — Delete ca_drift**
  - [x] Remove `src/manta_trading/data/acquisition/daemon/ca_drift.py`
  - [x] Remove `MINUTE_CAGGS` and `DAILY_CAGGS` constants (were in
    ca_drift; verify no other consumers)
  - [x] Remove `ADJUSTMENT_DRIFT_EPSILON` from `manta_trading/constants.py`
  - [x] Remove all tests for `ca_drift`
  - [x] Remove any import of `ca_drift` from the daemon cycle
  - [x] Success: `grep -r ca_drift src/ test/` returns nothing

- [x] **T21 — Delete backtest directory**
  - [x] Remove `src/manta_trading/backtest/` entire directory
  - [x] Remove all tests under `test/` that import from `backtest`
  - [x] Success: `grep -r backtest src/ test/` returns nothing

- [x] **T22 — Delete AlphaVantage code**
  - [x] Run `grep -rl 'alphavantage\|AlphaVantage\|ALPHAVANTAGE' src/ test/`
    to get the full list
  - [x] Delete each identified file or remove AV-specific sections
    (e.g. config fields, provider registrations)
  - [x] Remove AV from `pyproject.toml` dependencies if present
  - [x] Success: `grep -r 'alphavantage\|AlphaVantage\|ALPHAVANTAGE' src/ test/`
    returns nothing

- [x] **T23 — Delete MarketDB modules**
  - [x] Remove `src/manta_trading/market/marketdb.py`
  - [x] Remove `src/manta_trading/market/symbol_list_manager.py`
  - [x] Remove `src/manta_trading/market/instrument_seed.py`
    (any seeding logic worth keeping to be moved in Part 2)
  - [x] Remove all tests for the above
  - [x] Success: `grep -r 'marketdb\|MarketDB\|MT_MARKET_DB_URL' src/ test/`
    returns nothing (config field for URL may be left as deprecated
    with a comment; confirm with PM if uncertain)

- [x] **T24 — Build verification after demolition**
  - [x] Run `uv run pyright` — zero errors
  - [x] Run `uv run pytest test/unit/` — all pass
  - [x] Fix any broken imports introduced by deletions
  - [x] Commit: `refactor: delete adjustment, ca_drift, backtest, AV, MarketDB`
  - [x] Success: clean pyright + unit test run; commit on branch
