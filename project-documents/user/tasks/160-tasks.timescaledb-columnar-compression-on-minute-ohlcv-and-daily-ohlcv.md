---
docType: tasks
slice: timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv
project: trading
lld: user/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md
dependencies: [156]
projectState: >
  Slices 141–159 and 161 are complete. Migration chain runs through migration
  041. Both minute_ohlcv and daily_ohlcv are raw-only hypertables (adj_* columns
  dropped in slice 152). Compression has not been enabled on either table.
dateCreated: 20260515
dateUpdated: 20260515
status: complete
---

## Context Summary

- Working on slice 160: enable TimescaleDB columnar compression on `minute_ohlcv`
  and `daily_ohlcv`
- All work is in `src/manta_trading/market/schema/migrations/minute.py` — one
  Python callable + one migration entry; no application code changes elsewhere
- Migration id `042_enable_columnar_compression` requires `requires_autocommit=True`
  (TimescaleDB policy and compression DDL cannot run inside a transaction)
- Three internal steps: (1) enable compression settings, (2) install `compress_after=7d`
  policies, (3) backfill-compress all existing chunks older than 7 days
- Dependency: slice 156 (stable migration chain with `mt data init`)
- Validate on `trading_test` before applying to production

---

## Tasks

- [x] **T1 — Record baseline state on `trading_test`**
  - [x] Connect to `trading_test` and run the following; record the output for comparison after compression:
    ```sql
    SELECT hypertable_name,
           pg_size_pretty(hypertable_size(hypertable_name::regclass)) AS total_size,
           num_chunks
    FROM timescaledb_information.hypertables
    WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
    ```
  - [x] Record the row count for a known symbol in `minute_ohlcv` (e.g.
    `SELECT COUNT(*) FROM minute_ohlcv WHERE symbol = 'SPY'`) to use as a
    post-compression correctness reference
  - [x] Confirm no compression is currently active:
    ```sql
    SELECT hypertable_name, compression_enabled
    FROM timescaledb_information.hypertables
    WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
    ```
    Expected: both rows show `compression_enabled = f`

- [x] **T2 — Implement `_setup_and_backfill_compression` callable in `minute.py`**
  - [x] Add the callable above `MINUTE_MIGRATIONS` in
    `src/manta_trading/market/schema/migrations/minute.py`, following the same
    placement pattern as `_run_trading_sessions_population` and
    `_copy_splits_dividends_from_marketdb`
  - [x] The callable accepts a single `conn: Any` parameter (autocommit psycopg3
    connection) and imports `get_logger` locally as the other callables do
  - [x] **Step 1** — enable compression on both tables using `ALTER TABLE ... SET
    (timescaledb.compress, timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'time DESC')`. Log INFO after each table.
    See the migration skeleton in the slice design for the full SQL.
  - [x] **Step 2** — install compression policies (idempotent). For each table,
    check `timescaledb_information.jobs` for a row where
    `hypertable_name = <table>` AND `proc_name = 'policy_compression'`
    before calling `add_compression_policy(<table>, INTERVAL '7 days')`.
    Log INFO when a policy is installed or already exists.
  - [x] **Step 3** — backfill-compress existing chunks. Query
    `timescaledb_information.chunks` for chunks where
    `hypertable_name IN ('minute_ohlcv', 'daily_ohlcv')`,
    `range_end < NOW() - INTERVAL '7 days'`, and `is_compressed = false`.
    Call `compress_chunk('<schema>.<name>')` for each. Log INFO at count
    intervals (every 50 chunks and on completion). An empty result set
    (fresh DB) is a valid no-op.
  - [x] Success: callable compiles without import errors; type annotations are
    consistent with the `conn: Any` contract used by other callables in the file
  - [x] Run `uv run pyright src/manta_trading/market/schema/migrations/minute.py`;
    zero new errors

- [x] **T3 — Add migration entry `042_enable_columnar_compression` to `MINUTE_MIGRATIONS`**
  - [x] Append the entry at the end of the `MINUTE_MIGRATIONS` list (after
    `041_create_universe_members`):
    - `id`: `"042_enable_columnar_compression"`
    - `description`: concise one-liner covering both tables, settings, policy,
      and backfill (see slice design §Migration Entry for the full text)
    - `requires_autocommit`: `True`
    - `python_fn`: `_setup_and_backfill_compression`
    - No `sql` key (the callable handles all SQL)
  - [x] Confirm `_setup_and_backfill_compression` is referenced by name (not an
    inline lambda) consistent with the other two Python callables in the list
  - [x] Success: the list entry is syntactically correct Python; `MINUTE_MIGRATIONS`
    now has 43 entries (up from 42)

- [x] **T4 — Apply migration to `trading_test` and verify basic success**
  - [x] Run: `MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading_test" mt data migrate apply`
  - [x] Confirm log output shows:
    - `Applied migration: 042_enable_columnar_compression`
    - `compression enabled on minute_ohlcv`
    - `compression enabled on daily_ohlcv`
    - `compression policy installed on minute_ohlcv` (or "already exists" variant)
    - `compression policy installed on daily_ohlcv`
    - `backfill: N chunk(s) to compress` (N ≥ 0; zero is valid for a sparse test DB)
  - [x] Confirm `042_enable_columnar_compression` appears in `schema_migrations`:
    ```sql
    SELECT migration_id, applied_at FROM schema_migrations
    WHERE migration_id = '042_enable_columnar_compression';
    ```
    Returns one row
  - [x] Success: migration applies without Python exceptions or SQL errors

- [x] **T5 — Full verification on `trading_test`**
  - [x] Compression enabled on both tables:
    ```sql
    SELECT hypertable_name, compression_enabled
    FROM timescaledb_information.hypertables
    WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
    ```
    Expected: both rows `compression_enabled = t`
  - [x] Compression policies installed:
    ```sql
    SELECT hypertable_name, compress_after
    FROM timescaledb_information.compression_settings
    WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
    ```
    Expected: two rows, `compress_after = 7 days` on each
  - [x] No uncompressed old chunks remain:
    ```sql
    SELECT hypertable_name,
           SUM(CASE WHEN NOT is_compressed
                     AND range_end < NOW() - INTERVAL '7 days'
                    THEN 1 ELSE 0 END) AS uncompressed_old
    FROM timescaledb_information.chunks
    WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv')
    GROUP BY hypertable_name;
    ```
    Expected: `uncompressed_old = 0` (or query returns no rows if DB is empty)
  - [x] Query correctness: SPY row count matches the baseline recorded in T1
    ```sql
    SELECT COUNT(*) FROM minute_ohlcv WHERE symbol = 'SPY';
    ```
    Expected: same count as T1 baseline
  - [x] Run `mt data get SPY 1m --from 2024-01-02 --to 2024-01-05 --raw`;
    returns OHLCV bars with plausible values (no zeros, no NULLs in OHLCV columns)
  - [x] Re-apply is a no-op:
    ```bash
    MT_TIMESCALE_DB_URL="..." mt data migrate apply
    ```
    Expected: output shows `0 new migrations applied`
  - [x] Idempotency check: if compression is already enabled, re-running the
    callable must not raise an error. Manually call `mt data migrate apply`
    a second time; confirm clean exit.
  - [x] Record before/after disk sizes using `hypertable_compression_stats()` per
    the slice design §Functional Requirement 4 query; note the compression ratio
  - [x] **Assert compression ratio (FR4):** `minute_ohlcv` compressed ratio should
    be ≥ 80% on any chunks that were actually compressed. On `trading_test` (~10
    symbols) there may be no eligible chunks — if `before_compression_total_bytes`
    is zero or NULL, record "no compressible data in test DB" and defer the ratio
    assertion to T8 (production). If data exists, a ratio below 80% is a **STOP**
    condition — investigate before proceeding to T6.

- [x] **T6 — Commit checkpoint (migration code + trading_test validation)**
  - [x] Stage `src/manta_trading/market/schema/migrations/minute.py`
  - [x] Commit: `feat: add migration 042 — enable columnar compression on OHLCV hypertables`

- [x] **T7 — Apply migration to production**
  - [x] Run:
    ```bash
    MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
      mt data migrate apply
    ```
  - [x] Monitor log output during backfill phase; confirm chunk progress lines
    are printed (this may run for several minutes on a large dataset)
  - [x] Confirm migration recorded in production `schema_migrations`
  - [x] Success: no exceptions; log ends with successful migration record

- [x] **T8 — Verify production**
  - [x] Repeat the SQL checks from T5 against the `trading` DB:
    - Compression enabled on both tables
    - Policies installed with `compress_after = 7 days`
    - No uncompressed old chunks (`uncompressed_old = 0` for both tables)
    - SPY row count unchanged (or another well-populated symbol in production)
  - [x] Run `mt data get SPY 1m --from 2024-01-02 --to 2024-01-05 --raw` against
    production; returns bars with plausible OHLCV values
  - [x] Record production compression ratio from `hypertable_compression_stats()`
  - [x] **Assert compression ratio ≥ 80% for `minute_ohlcv` (FR4):** production has
    sufficient data; a ratio below 80% is a **STOP** condition. Record the actual
    ratio in this task file for the T10 doc update.
  - [x] Success: all checks pass; production mirrors the trading_test outcome

- [x] **T9 — Run existing test suite**
  - [x] `uv run pytest test/ -q`
  - [x] Expected: all pre-existing tests pass; zero new failures
  - [x] `uv run pyright src/` — zero new type errors
  - [x] **Note on CI test coverage:** no new automated test is added for compression
    state — the slice ships no application code and the verification (T5/T8 SQL
    checks) requires a live TimescaleDB connection with real data. The manual
    T5/T8 walkthrough is the acceptance test for this slice. A permanent
    integration test fixture is deferred until a test-DB seeding harness that
    pre-populates compressible chunks exists.

- [x] **T10 — Final commit**
  - [x] If any documentation or the slice design walkthrough was updated with
    actual compression ratios, stage those files
  - [x] Commit: `docs: update slice 160 verification walkthrough with production compression ratios`
  - [x] If no documentation changes, skip this task
