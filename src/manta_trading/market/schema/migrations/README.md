---
docType: reference
project: trading
dateCreated: 20260425
dateUpdated: 20260425
status: active
---

# Schema Migrations

**Single source of truth:** all schema changes for this project go through the Python migration framework defined in this package. No SQL files outside this package are authoritative.

## Tracks

| Track | Module | Database |
|-------|--------|----------|
| `minute` | `minute.py` — `MINUTE_MIGRATIONS` | TimescaleDB (`MT_TIMESCALE_DB_URL`) |
| `daily` | `daily.py` — `DAILY_MIGRATIONS` | PostgreSQL MarketDB (`MT_MARKET_DB_URL`) |

Each track has its own `schema_migrations` table on its respective database.

## How to add a migration

1. Open the relevant track module (`minute.py` or `daily.py`).
2. Append a new dict to the `MINUTE_MIGRATIONS` or `DAILY_MIGRATIONS` list:

   ```python
   {
       "id": "010_trading_sessions",          # zero-padded, snake_case
       "description": "Create trading_sessions table",
       "sql": """
           CREATE TABLE IF NOT EXISTS trading_sessions (
               ...
           );
       """,
   },
   ```

3. The `id` must be lexicographically greater than the previous entry (they are applied in list order).
4. SQL must be idempotent — use `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, or `DO $$ ... END $$` guards.
5. Run `mt data migrate --db <track>` to apply the new migration.

## CLI commands

```bash
mt data migrate apply [--db minute|daily|all] [--json]   # apply pending migrations
mt data migrate status [--db minute|daily|all] [--json]  # show applied / pending state
```

## Bringing a new database under management

For a DB that already has a schema but no tracking table, run once:

```bash
mt data migrate apply --db <track>
```

The `001_schema_migrations` entry creates the tracking table; subsequent entries are no-ops or reconciliation markers that record the baseline. No live data is touched.

## Historical note

`database/migrations/*.sql` files (025, 750, 760, 770, 780) and `sql/01_setup_database.sql`
existed historically as remnants of archived work (Slice 750 and earlier). They were never
part of the tracked migration framework and were deleted in Slice 150. Git history preserves
them if needed.
