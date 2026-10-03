---
docType: reference
project: trading
dateCreated: 20260425
dateUpdated: 20260927
status: active
---

# Schema Migrations

**Single source of truth:** all schema changes for this project go through the Python migration framework defined in this package. No SQL files outside this package are authoritative.

## Tracks

Each track belongs to one database, stated once in `TRACK_REGISTRY`
(`__init__.py`). `mt data migrate` resolves the URL for the track's database —
the application credential for `status`, the maintenance credential for `apply`
— and never falls back to another variable (slice 923).

| Track | Module | Database | Application variable | Maintenance variable |
|-------|--------|----------|----------------------|----------------------|
| `minute` | `minute.py` — `MINUTE_MIGRATIONS` | primary (`trading`) | `MT_TIMESCALE_DB_URL` | `MT_TIMESCALE_MAINTENANCE_URL` |
| `daily` | `daily.py` — `DAILY_MIGRATIONS` | primary (`trading`) | `MT_TIMESCALE_DB_URL` | `MT_TIMESCALE_MAINTENANCE_URL` |
| `kalshi` | `kalshi.py` — `KALSHI_MIGRATIONS` | primary (`trading`) | `MT_TIMESCALE_DB_URL` | `MT_TIMESCALE_MAINTENANCE_URL` |
| `tick` | `tick.py` — `TICK_MIGRATIONS` | tick | `MT_TICK_DB_URL` | `MT_TICK_MAINTENANCE_URL` |

- **One ledger per database.** The primary's `schema_migrations` is shared by
  `minute`, `daily` and `kalshi`; the tick database has its own, holding the
  bootstrap and `tick_*` ids only.
- **The tick track (slice 222)** is `tick_001_extensions` (TimescaleDB,
  btree_gist), `tick_002_manifest` (`tick_request`, `tick_archive_unit`),
  `tick_003_definitions` (`tick_definition`), `tick_004_trades` (the
  `tick_trade` hypertable on integer nanosecond `ts_event`) and
  `tick_005_ingest_ledger` (`tick_ingest_ledger`). Slice 223 adds
  `tick_006_availability`: `tick_dataset_edge`, `tick_day_condition` (CHECK
  rendered from `DatasetCondition`) and `tick_archive_unit.reopened_at`, which
  a CHECK refuses on any unit holding a file. It contains no `GRANT`:
  `scripts/provision_tick_roles.sql` enumerates the application role's write
  surface.
  Slice 226 adds `tick_007_trade_columnstore`: `tick_now_ns()` as the
  hypertable's integer-now function, the columnstore layout from
  `TICK_TRADE_SEGMENT_BY` / `TICK_TRADE_ORDER_BY`, and a columnstore policy
  at `TICK_TRADE_COMPRESS_AFTER`. `ALTER ... SET` fails while compressed
  chunks exist, so it applies to an empty or uncompressed `tick_trade`.
- **Misroute guard.** Before applying, `apply` reads the target ledger and
  refuses if it holds ids of a track routed to another database (for example a
  tick maintenance URL that points at `trading`). It checks the ledger, not
  the URL, so host aliases cannot defeat it. The shared `001_schema_migrations`
  bootstrap and ids in no current track are ignored.
- **Id prefixes.** Every id after the bootstrap is unique across the tracks of
  a database; `kalshi` ids are `kalshi_NNN_*` and `tick` ids are `tick_NNN_*`.
- **Adding a track** means one `TRACK_REGISTRY` entry; `--track` choices,
  routing and the guard follow from it.

## How to add a migration

1. Open the relevant track module.
2. Append a new dict to its migrations list:

   ```python
   {
       "id": "010_trading_sessions",          # tick: "tick_NNN_*", kalshi: "kalshi_NNN_*"
       "description": "Create trading_sessions table",
       "sql": """
           CREATE TABLE IF NOT EXISTS trading_sessions (
               ...
           );
       """,
   },
   ```

3. Migrations are applied in list order; append, never reorder.
4. SQL must be idempotent — use `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, or `DO $$ ... END $$` guards.
5. Run `mt data migrate apply --track <track>` to apply it.

## CLI commands

```bash
mt data migrate apply  [--track minute|daily|kalshi|tick] [--json]  # apply pending (maintenance credential)
mt data migrate status [--track minute|daily|kalshi|tick] [--json]  # applied / pending (application credential)
mt data init [--database primary|tick] [--validate-only] [--json]   # cold start: primary → minute, tick → tick
```

`--track` defaults to `minute` and `--database` to `primary`, so the
no-argument forms act on the primary database exactly as before slice 923.

## Bringing a new database under management

On a bare database, `apply` (or `init`) runs the track's first entry,
`001_schema_migrations`, which creates the ledger; the guard passes because
there is no ledger yet. For a database that already has a schema but no
ledger, the same command creates the ledger and records the baseline; no live
data is touched.

## Historical note

`database/migrations/*.sql` files (025, 750, 760, 770, 780) and `sql/01_setup_database.sql`
existed historically as remnants of archived work (Slice 750 and earlier). They were never
part of the tracked migration framework and were deleted in Slice 150. Git history preserves
them if needed.
