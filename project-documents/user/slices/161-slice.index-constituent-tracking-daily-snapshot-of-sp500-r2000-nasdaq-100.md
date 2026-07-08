---
docType: slice-design
slice: 161
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [141]
interfaces:
  - universe_members table (consumed by slice 130)
dateCreated: 20260514
dateUpdated: 20260514
status: complete
effort: 2
---

# Slice 161 — Index Constituent Tracking: Daily Snapshot of SP500, R2000, NASDAQ-100

## Overview

Without point-in-time index membership data, an "SP500 backtest" silently uses today's
constituents for all historical dates — a form of look-ahead bias. This slice builds the
tracking infrastructure that produces reliable constituent history going forward from deploy
date.

The approach is additive: fetch each index's current constituent list once per calendar day,
diff against yesterday's snapshot in `universe_members`, and write changes (additions and
removals). Historical reconstitution data is not pursued — it is expensive to source and out
of scope per the slice plan.

The tracking start date is derivable as `MIN(added_date)` per universe. Slice 130's API will
surface this as the reliability horizon so callers know when the data becomes trustworthy.

## Value

- Provides the `universe_members` table that slice 130's `equity_universe(as_of_date)` query
  consumes for index-aware filtering
- Closes the look-ahead bias gap for SP500/R2000/NASDAQ-100 index strategies from deploy
  date onward
- Low cost: 30 EODHD credits/day (3 indices × 10 credits each)

## Technical Scope

- Schema: new `universe_members` table + migration
- EODHD fetch layer: pull `Components` array from `/fundamentals/{CODE}.INDX`
- Diff logic: compare today's fetched set against active rows; INSERT additions, UPDATE
  departures
- Seed logic: first-run seed populates today's full snapshot as the start-of-history marker
- Daemon integration: once-per-calendar-day hook in the existing daily daemon cycle
- CLI: `mt data universes` command group for operator inspection and manual refresh
- Constants: tracked-universe definitions centralised in one module (no magic strings)

## Dependencies

### Prerequisites

- **Slice 141** — `instruments` table and EODHD-based universe in place. `universe_members`
  references symbols consistent with EODHD normalisation already applied to `instruments`.

### Interface Requirements from Other Slices

- **Slice 130** reads `universe_members (universe_name, symbol, added_date, removed_date)`
  to filter index membership at backtest time. This slice is the sole writer.
  - Note: slice 130's design doc uses column names `added_on/removed_on`; the authoritative
    names are `added_date/removed_date` as specified here. Slice 130 must be updated at task
    time to use the names from this table.

## Architecture

### Table: `universe_members`

```sql
CREATE TABLE universe_members (
    universe_name  TEXT  NOT NULL,
    symbol         TEXT  NOT NULL,
    added_date     DATE  NOT NULL,
    removed_date   DATE,
    PRIMARY KEY (universe_name, symbol, added_date)
);

CREATE INDEX idx_universe_members_active
    ON universe_members (universe_name, symbol)
    WHERE removed_date IS NULL;
```

The composite PK `(universe_name, symbol, added_date)` allows a symbol to be added,
removed, and re-added over time (e.g. a company that leaves and rejoins the S&P 500).
`removed_date IS NULL` means currently active. `removed_date = today` means departed as of
today's comparison.

No FK to `instruments` — storing `symbol` as TEXT is intentional. This avoids coupling to
`instrument_id` lifecycle and is consistent with how the rest of the system identifies
instruments by symbol.

### Tracked Universe Constants

A single module (`manta_trading/data/universe/constants.py`) defines the supported universe
names and their EODHD index codes. Nothing outside this module embeds the string `GSPC`,
`RUT`, or `NDX`.

```python
# Mapping: universe_name → EODHD index code for /fundamentals/{code}
TRACKED_UNIVERSES: dict[str, str] = {
    "sp500":     "GSPC.INDX",
    "r2000":     "RUT.INDX",
    "nasdaq100": "NDX.INDX",
}
```

Changing which indices are tracked requires editing exactly one place.

### Core Tracking Logic

Module: `manta_trading/data/universe/tracking.py`

Key operations:
1. **`fetch_constituents(client, eodhd_code) -> set[str]`** — calls
   `/fundamentals/{eodhd_code}` and extracts the `Components` dict keys (EODHD symbols).
   Returns an empty set and logs ERROR if the payload is malformed. Does not silently return
   stale data.

2. **`get_active_members(conn, universe_name) -> set[str]`** — SELECT WHERE
   `removed_date IS NULL` for the given universe.

3. **`apply_universe_diff(conn, universe_name, fetched, as_of_date)`** — computes:
   - `additions = fetched - active`: INSERT with `added_date = as_of_date`, `removed_date = NULL`
   - `departures = active - fetched`: UPDATE SET `removed_date = as_of_date`
   Both writes are idempotent for the same `as_of_date` (re-running today is safe).

4. **`is_refreshed_today(conn, universe_name, today) -> bool`** — guards against running
   multiple times per calendar day. Checks for any row with `added_date = today` OR any
   update with `removed_date = today`. Returns True if already refreshed.

5. **`refresh_universe(conn, client, universe_name, eodhd_code, today)`** — composes the
   above; skips if already refreshed today; seeds on first run (empty table for this
   universe).

### Data Flow

```
Daily daemon cycle completes
        │
        ▼
refresh_all_universes(conn, client, today)
  for each (universe_name, eodhd_code) in TRACKED_UNIVERSES:
    if is_refreshed_today(conn, universe_name, today): skip
    constituents ← fetch_constituents(client, eodhd_code)   [10 credits]
    if table empty for this universe: seed (INSERT all, added_date=today)
    else: apply_universe_diff(conn, universe_name, constituents, today)
    log: "universe {name}: +{added} -{removed} members as of {today}"
```

Total credits per day: 30 (3 × 10). Runs after the daily OHLCV cycle completes so it does
not compete for credits during the main backfill window.

### Daemon Integration

The existing daily daemon (`manta_trading/data/acquisition/daemon.py`) calls a
`run_daily_cycle()` function. Universe tracking is appended as a post-cycle step, not
embedded in the per-symbol loop. The daemon logs a structured line for each universe
refreshed.

### CLI: `mt data universes`

New sub-app under `mt data`, parallel to `mt data lists` and `mt data ca`.

| Command | Description |
|---|---|
| `mt data universes ls` | Show tracked universes with active member count and last-refresh date |
| `mt data universes as-of --date YYYY-MM-DD --name NAME` | List members of universe NAME as of DATE |
| `mt data universes refresh [--name NAME]` | Force an immediate refresh (bypasses today-guard); all universes if `--name` omitted |

`as-of` is the primary operator inspection command. It queries:
```sql
SELECT symbol FROM universe_members
WHERE universe_name = :name
  AND added_date <= :date
  AND (removed_date IS NULL OR removed_date > :date)
ORDER BY symbol;
```

## Technical Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Column names | `added_date` / `removed_date` | Matches slice plan spec; clearer than `added_on` |
| Symbol storage | TEXT (no FK) | Consistent with rest of system; avoids instrument_id coupling |
| EODHD endpoint | `/fundamentals/{CODE}.INDX` `Components` | Already used by `refresh-sp500`; 10 credits per call |
| Re-run guard | check for existing row on today's date | Idempotent; safe to re-run |
| Daemon hook | post-cycle, not per-symbol | Once-per-day operation; wrong granularity for symbol loop |
| Constants location | `universe/constants.py` | Single place to change tracked universes |

## Migration

New Alembic migration (e.g. `migrations/versions/xxxx_add_universe_members.py`):
- `CREATE TABLE universe_members ...` (schema above)
- `CREATE INDEX idx_universe_members_active ...`
- Down: `DROP TABLE universe_members`

No data migration needed on deploy; the first daemon run seeds the table.

## Success Criteria

1. `universe_members` table exists in the trading DB after migration.
2. After the first daemon run post-deploy, each of the three universes has a row count
   consistent with the current EODHD constituent list (SP500 ≈ 503, R2000 ≈ 2000,
   NASDAQ-100 = 100).
3. `mt data universes ls` shows all three universes with correct member counts and last-
   refresh date = today.
4. `mt data universes as-of --date <today> --name sp500` returns ≈503 symbols.
5. Simulated removal: manually UPDATE a row to set `removed_date = yesterday`; verify that
   `as-of <yesterday>` includes the symbol and `as-of <today>` excludes it.
6. Re-running `mt data universes refresh` on the same day is idempotent (no duplicate rows,
   no changed counts).
7. Daemon does not consume more than 30 credits for the universe-tracking step on any day.
8. Slice 130 can query `universe_members` with the `added_date / removed_date` column names
   without error.

## Verification Walkthrough

Verified 2026-05-14 against `trading` DB (<db-host>:5432).

**Data source change from original design:** EODHD `/fundamentals` is not available on
the current plan. SP500 history is sourced from the fja05680/sp500 GitHub CSV instead,
giving full history back to 1996-01-02. R2000 and NASDAQ-100 are deferred.

### 1. Deploy the migration

```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" mt data init
```

Expected: `Applied this run: 1`, `Total applied: 44`, `Pending remaining: 0`.

Verify schema:
```bash
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c "\d universe_members"
```
Expected: columns `universe_name`, `symbol`, `added_date`, `removed_date`; PK on all three;
partial index `idx_universe_members_active` WHERE `removed_date IS NULL`.

### 2. Import historical data

```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" mt data universes refresh
```

Expected output:
```
sp500: 2705 change-rows imported, 0 already up-to-date
```

History spans 1996-01-02 → 2026-01-14 (latest CSV update).

### 3. Inspect active membership

```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" mt data universes ls
```

Expected:
```
┃ Universe ┃ Members ┃ Last Refresh ┃
│ sp500    │     503 │ 2026-01-14   │
```

Point-in-time queries:
```bash
# Current members (503):
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data universes as-of --date 2024-01-01 --name sp500 | wc -l
# → 503

# Members on 2000-01-03 (491 — different composition):
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data universes as-of --date 2000-01-03 --name sp500 | wc -l
# → 491
```

### 4. Verify idempotence

```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" mt data universes refresh
```

Expected:
```
sp500: 0 change-rows imported, 2705 already up-to-date
```

### 5. Verify as-of query semantics (manual)

```bash
# Confirm AAPL is active today:
psql "postgresql://postgres:<password>@<db-host>:5432/trading" \
  -c "SELECT added_date, removed_date FROM universe_members WHERE universe_name='sp500' AND symbol='AAPL' AND removed_date IS NULL;"

# Confirm a historically removed symbol (e.g. ABMD removed 2023-01-06) is absent after that date:
psql "postgresql://postgres:<password>@<db-host>:5432/trading" \
  -c "SELECT symbol, added_date, removed_date FROM universe_members WHERE universe_name='sp500' AND symbol='ABMD';"
```

### 6. Update cadence

When the GitHub repo publishes a new dated CSV (typically monthly), re-run:
```bash
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" mt data universes refresh
```
The command auto-detects the latest versioned file via the GitHub API and applies only new rows.
