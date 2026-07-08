---
docType: slice-design
slice: timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [156]
interfaces: []
dateCreated: 20260515
dateUpdated: 20260515
status: complete
---

# Slice Design: TimescaleDB Columnar Compression on `minute_ohlcv` and `daily_ohlcv`

## Overview

Enable TimescaleDB columnar compression on both OHLCV hypertables. A full SP500
universe of 2-year minute history (~1B rows) would occupy 200–300 GB uncompressed;
10–20× columnar compression brings this to 15–30 GB. Compression must be applied
before the 3B-row milestone. Reads and writes are transparent — no application
code changes are required post-152.

## Value

Disk savings of 85–95% on the historical OHLCV dataset. At production scale,
`minute_ohlcv` would otherwise require dedicated storage investment before the
full universe backfill completes. Compression also improves I/O throughput for
sequential scans (fewer pages to read per symbol range query). No operator
workflow changes.

## Technical Scope

**Included:**
- Migration `042_enable_columnar_compression`: configure compression on both
  hypertables, install compression policies, and backfill-compress all existing
  eligible chunks via a Python callable
- No Python application code changes — post-152 write path is already compatible
- Tests: verify compression is active, policies are installed, and a bounded
  per-symbol query returns correct results after compression

**Excluded:**
- No changes to caggs — compressed chunks are read natively; cagg refresh
  policies continue working unchanged
- No changes to the `data_status` view or any acquisition logic
- CA recomputation path — `adj_*` columns were dropped in slice 152; the concern
  noted in the slice plan entry is obsolete

## Dependencies

### Prerequisites
- Slice 156: cold-start integrity (migration chain is the single schema source of
  truth; `mt data init` bootstraps a clean DB correctly). Compression requires a
  stable migration chain.

## Architecture

### Compression Settings

Both tables use the same settings:

```sql
timescaledb.compress_segmentby = 'symbol'
timescaledb.compress_orderby   = 'time DESC'
```

`compress_segmentby = 'symbol'` preserves per-symbol scan performance: when a
query filters `WHERE symbol = 'AAPL'`, TimescaleDB skips compressed blocks whose
`symbol` segment does not match. Chunk pruning on `time` continues to work in
parallel.

`compress_orderby = 'time DESC'` stores data most-recent-first within each
compressed block. Most queries request a trailing window of bars, so this is the
lowest-I/O order.

Both choices satisfy the uniqueness requirement on `ux_minute_ohlcv_symbol_time`
(`symbol, time`) and `ux_daily_ohlcv_symbol_time` (`symbol, time`): all columns
in both unique indexes are covered by `segmentby` + `orderby`.

### Compression Policy

```
compress_after = INTERVAL '7 days'
```

Applied to both `minute_ohlcv` and `daily_ohlcv`. A chunk becomes eligible for
compression once all its timestamps are more than 7 days old.

For `minute_ohlcv` (4-hour chunks): the daemon writes to recent data (current
session or the prior session's catch-up bars, never older than a few hours). All
historical chunks — older than 7 days — are eligible. ~42 chunks (~7 days ÷ 4
hours) remain uncompressed at steady state.

For `daily_ohlcv` (7-day chunks): the daemon writes one bar per symbol per
completed session. The current week's chunk stays uncompressed; all prior chunks
compress automatically.

### Write Path Compatibility

Post-slice-152, the **only write path to both OHLCV tables** is:

```sql
INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume)
SELECT ... FROM staging_minute_ohlcv
ON CONFLICT (symbol, time) DO NOTHING
```

(`write_minute_data_bulk` in `timescale_minute_db.py` and its daily analog.)

`INSERT ... ON CONFLICT DO NOTHING` is safe on compressed chunks:
- If the row is new: written to the chunk's uncompressed staging area (DML buffer).
  TimescaleDB flushes this buffer and recompresses during the next compression
  policy run.
- If the row already exists: `DO NOTHING` requires no mutation of the compressed
  data. The conflict is detected via the maintained chunk index.

There is no `UPDATE` or `DELETE` path for OHLCV bar data post-152. The adj_*
columns (`adj_open`, `adj_high`, `adj_low`, `adj_close`, `k_factor`,
`adjusted_at`) and the CA recomputation band-writer were deleted in slice 152.
The concern about `UPDATE minute_ohlcv SET adj_* WHERE symbol = X AND time >=
ex_date` noted in the slice plan entry is fully obsolete.

`mt data pull --reset` only resets `data_gaps` status rows (in the
non-hypertable `data_gaps` table); it then dispatches the same
`INSERT ... ON CONFLICT DO NOTHING` write. For PROVIDER_HOLE and RETRY_EXHAUSTED
gaps (the only gaps `--reset` targets), no bars exist in those time ranges, so
compressed-chunk duplicates are not a risk.

### Migration Structure

Single migration `042_enable_columnar_compression` with `requires_autocommit =
True` and a Python callable. Autocommit is required because TimescaleDB's
`add_compression_policy()` and `compress_chunk()` interact with the TimescaleDB
background job system in ways that are incompatible with an enclosing transaction.

The Python callable performs three steps in sequence:

**Step 1 — Enable compression** (idempotent: `ALTER TABLE SET` is a no-op if
compression is already enabled with the same settings):

```sql
ALTER TABLE minute_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby   = 'time DESC'
);

ALTER TABLE daily_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby   = 'time DESC'
);
```

**Step 2 — Install compression policies** (idempotent: check
`timescaledb_information.jobs` before calling `add_compression_policy`):

```sql
SELECT add_compression_policy('minute_ohlcv', INTERVAL '7 days');
SELECT add_compression_policy('daily_ohlcv',  INTERVAL '7 days');
```

**Step 3 — Backfill-compress existing chunks** (Python loop with progress
logging). Queries `timescaledb_information.chunks` for chunks where
`range_end < NOW() - INTERVAL '7 days'` and `is_compressed = false`, then calls
`compress_chunk(chunk_schema || '.' || chunk_name)` for each, logging
`N/M chunks compressed` at INFO level. Already-compressed chunks satisfy
`is_compressed = true` and are excluded from the query — the loop is safe to
re-run.

On an empty DB (`trading_test` with ~10 symbols): the backfill loop completes in
seconds. On a production DB after a full backfill (potentially thousands of
chunks): this step may run for minutes to tens of minutes. The migration records
itself in `schema_migrations` only after all three steps complete, so a partial
run can be retried by re-running `mt data migrate apply`.

## Implementation Details

### Migration Callable Skeleton

```python
def _setup_and_backfill_compression(conn: Any) -> None:
    """Configure compression on OHLCV hypertables and compress existing chunks.

    Requires autocommit connection (TimescaleDB policy management restriction).
    """
    from manta_trading.logging import get_logger
    _log = get_logger(__name__)

    # Step 1: enable compression
    for table in ("minute_ohlcv", "daily_ohlcv"):
        conn.execute(
            f"ALTER TABLE {table} SET ("
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'symbol', "
            "timescaledb.compress_orderby = 'time DESC'"
            ")"
        )
        _log.info("compression enabled on %s", table)

    # Step 2: install policies (idempotent)
    with conn.cursor() as cur:
        for table in ("minute_ohlcv", "daily_ohlcv"):
            cur.execute(
                "SELECT 1 FROM timescaledb_information.jobs "
                "WHERE hypertable_name = %s "
                "  AND proc_name = 'policy_compression'",
                (table,),
            )
            if not cur.fetchone():
                conn.execute(
                    "SELECT add_compression_policy(%s, INTERVAL '7 days')",
                    (table,),
                )
                _log.info("compression policy installed on %s", table)

    # Step 3: backfill-compress existing chunks
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_schema, chunk_name, hypertable_name "
            "FROM timescaledb_information.chunks "
            "WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv') "
            "  AND range_end < NOW() - INTERVAL '7 days' "
            "  AND is_compressed = false "
            "ORDER BY hypertable_name, range_start"
        )
        chunks = cur.fetchall()

    total = len(chunks)
    _log.info("backfill: %d chunk(s) to compress", total)
    for i, (schema, name, table) in enumerate(chunks, 1):
        conn.execute(
            "SELECT compress_chunk(%s)",
            (f"{schema}.{name}",),
        )
        if i % 50 == 0 or i == total:
            _log.info("compressed %d/%d chunks (%s)", i, total, table)
```

The callable is defined above `MINUTE_MIGRATIONS` in `minute.py`, following the
same pattern as `_run_trading_sessions_population` and
`_copy_splits_dividends_from_marketdb`.

### Migration Entry

```python
{
    "id": "042_enable_columnar_compression",
    "description": (
        "Enable TimescaleDB columnar compression on minute_ohlcv and "
        "daily_ohlcv (slice 160). Segmentby=symbol, orderby=time DESC. "
        "compress_after=7 days policy. Backfill-compresses all existing "
        "chunks older than 7 days. Idempotent."
    ),
    "requires_autocommit": True,
    "python_fn": _setup_and_backfill_compression,
},
```

## Success Criteria

### Functional Requirements

1. Both `minute_ohlcv` and `daily_ohlcv` report compression enabled in
   `timescaledb_information.hypertables` (column `compression_enabled = true`).

2. Compression policies are installed for both tables:
   ```sql
   SELECT hypertable_name, compress_after
   FROM timescaledb_information.compression_settings;
   ```
   Returns one row per table with `compress_after = 7 days`.

3. All chunks older than 7 days are compressed after the migration:
   ```sql
   SELECT hypertable_name, COUNT(*) AS uncompressed_old_chunks
   FROM timescaledb_information.chunks
   WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv')
     AND range_end < NOW() - INTERVAL '7 days'
     AND is_compressed = false
   GROUP BY hypertable_name;
   ```
   Returns zero rows (or empty result set).

4. Compressed chunk disk savings are measurable:
   ```sql
   SELECT hypertable_name,
          pg_size_pretty(before_compression_total_bytes) AS uncompressed,
          pg_size_pretty(after_compression_total_bytes)  AS compressed,
          ROUND(
              (1 - after_compression_total_bytes::numeric
                   / NULLIF(before_compression_total_bytes, 0)) * 100, 1
          ) AS pct_saved
   FROM hypertable_compression_stats('minute_ohlcv')
   UNION ALL
   SELECT hypertable_name,
          pg_size_pretty(before_compression_total_bytes),
          pg_size_pretty(after_compression_total_bytes),
          ROUND(
              (1 - after_compression_total_bytes::numeric
                   / NULLIF(before_compression_total_bytes, 0)) * 100, 1
          )
   FROM hypertable_compression_stats('daily_ohlcv');
   ```
   Savings for `minute_ohlcv` should be ≥ 80% on the compressed portions (10×+
   ratio expected for columnar numeric data). Actual ratio validated against
   `trading_test` data during implementation.

5. A per-symbol bounded query returns correct, complete data after compression:
   ```sql
   SELECT COUNT(*), MIN(time), MAX(time)
   FROM minute_ohlcv
   WHERE symbol = 'SPY'
     AND time >= '2024-01-01'
     AND time <  '2024-02-01';
   ```
   Row count matches the pre-compression count for the same range.

6. Running `mt data migrate apply` a second time is a no-op (migration already
   recorded; callable is not re-invoked).

### Technical Requirements

- All existing unit and integration tests pass without modification.
- Migration is idempotent: running against a DB where compression was already
  enabled manually does not raise an error.
- The migration callable logs progress at INFO level; operators can observe
  chunk compression proceeding during the backfill.

## Verification Walkthrough

**Prerequisites:** access to the `trading_test` DB and (after test validation)
the production DB at `<db-host>:5432/trading`.

**1. Apply the migration on `trading_test`:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading_test" \
  mt data migrate apply
```
Expected: log shows `Applied migration: 042_enable_columnar_compression`.
The backfill log shows `backfill: N chunk(s) to compress` followed by chunk
progress lines.

**2. Verify compression is enabled:**
```sql
SELECT hypertable_name, compression_enabled
FROM timescaledb_information.hypertables
WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
```
Expected: both rows show `compression_enabled = t`.

**3. Verify compression policies are installed:**
```sql
SELECT hypertable_name, compress_after
FROM timescaledb_information.compression_settings
WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv');
```
Expected: two rows, each with `compress_after = 7 days`.

**4. Verify all old chunks are compressed:**
```sql
SELECT hypertable_name,
       SUM(CASE WHEN is_compressed THEN 1 ELSE 0 END) AS compressed,
       SUM(CASE WHEN NOT is_compressed
                 AND range_end < NOW() - INTERVAL '7 days'
                THEN 1 ELSE 0 END) AS uncompressed_old
FROM timescaledb_information.chunks
WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv')
GROUP BY hypertable_name;
```
Expected: `uncompressed_old = 0` for both tables.

**5. Check compression ratio:**
```sql
SELECT hypertable_name,
       pg_size_pretty(before_compression_total_bytes) AS before,
       pg_size_pretty(after_compression_total_bytes)  AS after
FROM hypertable_compression_stats('minute_ohlcv')
UNION ALL
SELECT hypertable_name,
       pg_size_pretty(before_compression_total_bytes),
       pg_size_pretty(after_compression_total_bytes)
FROM hypertable_compression_stats('daily_ohlcv');
```
Record before/after for the implementation notes.

**6. Verify query correctness post-compression:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading_test" \
  mt data get SPY 1m --from 2024-01-02 --to 2024-01-05 --raw
```
Expected: returns OHLCV bars matching the pre-compression row count for SPY on
those dates. No row-count change; no garbage values.

**7. Re-apply is a no-op:**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading_test" \
  mt data migrate apply
```
Expected: `0 new migrations applied`. Migration `042_enable_columnar_compression`
is already in `schema_migrations`.

**8. Apply to production (after `trading_test` validation):**
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data migrate apply
```
Monitor log output for chunk compression progress. Repeat steps 2–6 against
`trading` to confirm production matches test results.

**9. Run existing test suite:**
```bash
uv run pytest test/ -q
```
Expected: all tests pass.

## Implementation Notes

### Development Approach

1. Add `_setup_and_backfill_compression` callable to `minute.py` (above
   `MINUTE_MIGRATIONS`)
2. Add `042_enable_columnar_compression` entry to `MINUTE_MIGRATIONS`
3. Apply migration on `trading_test`, measure before/after sizes
4. Update this slice's verification walkthrough with actual compression ratios
5. Apply to `trading` prod

No application code changes outside `minute.py`.

### Effort

2/5 (per slice plan estimate — confirmed; this is DDL + one Python callable).
