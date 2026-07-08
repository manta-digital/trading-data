---
docType: slice-design
slice: 144-trading-sessions-materialization-data-status-view-rewrite
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies:
  - 142-slice.schema-migration-and-cold-start
interfaces:
  - 145-slice.daemon-refactor                 # consumes target_end + session lookups
  - 147-slice.mt-data-status                  # data_status now populates target_end_ts
  - 150-slice.rebuild-minute-caggs            # unrelated, but both depend on minute_ohlcv stability
relatedReference: user/architecture/140-arch.data-quality-operations.md
dateCreated: 20260502
dateUpdated: 20260502
status: complete
---

# Slice Design: 144 — `trading_sessions` Materialization + `data_status` View Rewrite + `TradingCalendar` Consolidation

## Overview

Resolve slice 142's `target_end_ts` deferral by landing the materialized
`trading_sessions` table that the architecture's `data_status` view
already assumes. Then rewrite the view's `exchange_completed_close` CTE
to project `target_end_ts` from the table, and refactor Python
`TradingCalendar` to read session boundaries from the same table —
giving Python and SQL one source of truth.

This slice ships **no daemon work**. It exists because:

1. Slice 145 (daemon refactor) needs a per-symbol `target_end` lookup.
2. Slice 147 (`mt data status`) needs `target_end_ts` populated to be
   useful at universe scale.
3. Slice 142's view ships `target_end_ts = NULL` (see slice 142 design's
   "target_end_ts deferral" note and the docstring at
   [`minute.py:134`](src/manta_trading/market/schema/migrations/minute.py#L134)).
4. Python `TradingCalendar` recomputes session boundaries on every call
   from `trading_calendars` + `trading_holidays`. SQL would have to
   reimplement the same logic in a CTE. Materializing the result lets
   both read the same rows.

## Value

1. **`data_status.target_end_ts` populates.** `mt data status` (slice
   147) shows the actual completed-session boundary per exchange
   instead of "—". Same with any view consumer.
2. **One source of truth for session boundaries.** Python
   `TradingCalendar.get_trading_hours(date)` and the SQL view's
   `exchange_completed_close` CTE both read the same `trading_sessions`
   rows. No risk of Python and SQL drifting on early-close handling, DST
   edges, or holiday application.
3. **Single index lookup, no per-row function calls.** The view's CTE
   becomes a small `MAX(session_close_utc) WHERE session_close_utc +
   LATE_BAR_GRACE_PERIOD < NOW()` over a per-calendar index range. Slice
   142's universe-scale latency budget (sub-second) holds.
4. **Cheap operator inspection.** `psql -c "SELECT * FROM
   trading_sessions WHERE calendar_id='NYSE' AND session_date BETWEEN
   '2026-11-25' AND '2026-12-26'"` shows you exactly what NYSE thinks
   the schedule looks like over Thanksgiving + early-close + Christmas
   closure. Today this requires a Python REPL with a configured DB
   pool.

## Non-Goals

- **The daemon's `target_end` consumer.** Slice 145 wires it up.
- **CA detection / band-based UPDATE / `data_gaps` ops.** Slice 145.
- **Bulk EOD steady-state.** Slice 146.
- **Backfilling `trading_sessions` from history before slice 102's
  earliest seeded year.** Out of scope; horizon starts at the earliest
  seeded year and extends forward.
- **Adding new calendars.** This slice consumes whatever calendars +
  holidays exist in `trading_calendars` / `trading_holidays`.
- **Replacing `TradingCalendar.get_expected_bar_count` with a
  table-backed implementation.** The bar-count math stays in Python; it
  iterates per-day, and per-day session bounds will come from the new
  table.

## Inputs

- `trading_calendars` table (one row per calendar: timezone,
  market_open, market_close, extended_open/close, has_extended_hours).
- `trading_holidays` table (one row per (calendar_id, holiday_date):
  `market_status` ∈ `{'closed', 'early_close', 'late_open'}`,
  `early_close_time`, `late_open_time`).
- `manta_trading.constants.LATE_BAR_GRACE_PERIOD` (default 30 min).
- Slice 142's `data_status` view definition (in
  [`minute.py:134`](src/manta_trading/market/schema/migrations/minute.py#L134),
  currently projects `target_end_ts = NULL::TIMESTAMPTZ`).

## Outputs

- New table `trading_sessions(calendar_id, session_date,
  session_open_utc, session_close_utc)` with rows for every trading day
  in the configured horizon, per calendar.
- New maintenance entry-point that extends the horizon (idempotent).
- Rewritten `data_status` view (migration 028) that projects
  `target_end_ts` from `trading_sessions`.
- `TradingCalendar` Python class refactored to read session bounds from
  `trading_sessions` (with fallback for dates outside the horizon).

## Approach: Option A (materialized table)

The arch document (line 124–137 of `140-arch.data-quality-operations.md`)
and the slice plan describe two options:

- **A — materialized `trading_sessions` table.** Maintenance job
  extends a per-year horizon. Both Python and SQL read from the table.
- **B — in-view SQL replacement** computing session bounds from
  `trading_calendars` + `trading_holidays` inline (CASE expressions for
  weekends/holidays/early-close, timezone math via `AT TIME ZONE`).

**Decision: Option A.** Justification:

| | A (materialized) | B (in-view SQL) |
|--|--|--|
| Source of truth | One — Python and SQL both read `trading_sessions` | Two — view computes inline; Python keeps `_build_trading_hours` |
| `data_status` query latency | Single index lookup | CASE/JOIN against `trading_holidays` per query |
| Operator inspection | `SELECT * FROM trading_sessions` | Have to run the view |
| Failure mode | Maintenance job stops → horizon eventually exhausted → daemon halts on missing session | Always current |
| Test surface | Inspect rows; compare to Python output for parity | Compare view output to Python output for parity |

A loses on maintenance: the horizon must be kept ahead of "today + N
years." But the maintenance is idempotent and one query — easier than
keeping two SQL/Python implementations in lockstep. We also detect
horizon exhaustion early (verification walkthrough step 6), which makes
the failure mode loud rather than silent.

B remains documented as a fallback but is not implemented.

## Schema

```sql
CREATE TABLE trading_sessions (
    calendar_id        VARCHAR(32) NOT NULL
        REFERENCES trading_calendars(calendar_id),
    session_date       DATE        NOT NULL,
    session_open_utc   TIMESTAMPTZ NOT NULL,
    session_close_utc  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (calendar_id, session_date)
);

CREATE INDEX idx_trading_sessions_close
    ON trading_sessions (calendar_id, session_close_utc);
```

Notes:

- One row per **trading day** (closed-holiday and weekend dates are
  *not* present — absence means non-trading).
- `session_open_utc` and `session_close_utc` are RTH bounds with
  early-close / late-open overrides applied. ETH is **not** stored here
  (out of scope; existing Python `get_trading_hours(SessionType.ETH)`
  remains a calendar-metadata derivation since no consumer of `data_status`
  cares about ETH).
- `(calendar_id, session_close_utc)` index supports the view's
  `MAX(session_close_utc) WHERE session_close_utc + grace < NOW()`.
- Primary key supports the per-(calendar, date) lookup
  `TradingCalendar.get_trading_hours(date)` will use.

## Maintenance: Horizon Population

A pure function — call it `populate_trading_sessions(calendar_id,
start_date, end_date)` — emits an idempotent SQL statement:

```sql
INSERT INTO trading_sessions (calendar_id, session_date,
                              session_open_utc, session_close_utc)
VALUES ...
ON CONFLICT (calendar_id, session_date) DO UPDATE
   SET session_open_utc  = EXCLUDED.session_open_utc,
       session_close_utc = EXCLUDED.session_close_utc;
```

The row generation logic (Python, run as part of the maintenance job):

1. For each date in `[start_date, end_date]`:
   1. Skip weekends.
   2. Look up `trading_holidays` row for `(calendar_id, date)`. If
      `market_status = 'closed'`, skip.
   3. Otherwise compute open/close from `trading_calendars.market_open`
      / `market_close` with `early_close_time` / `late_open_time`
      override applied.
   4. Convert to UTC using `trading_calendars.timezone`.
   5. Emit row.

This is the same logic `TradingCalendar._build_trading_hours` already
runs (see
[`trading_calendar.py:283`](src/manta_trading/data/base/trading_calendar.py#L283))
— factored into a pure function so both the maintenance job and the
refactored Python class consume the same algorithm. **No second
implementation.**

### Horizon policy

- **Initial backfill (slice 144 application):** earliest seeded year in
  `trading_holidays` (slice 102's seed pre-dates this; today's seed
  starts 2024 per migration 007) through `current_year + 2`.
- **Maintenance trigger:** slice 144 ships an `mt data --extend
  [--calendar X]` CLI command. Extending the horizon is operator-
  initiated this slice; auto-extension is deferred to slice 147 (see
  Open Questions).
- **Horizon health check:** the same CLI emits a warning if any
  calendar's max `session_date` is within 90 days of `today`. The
  warning is loud (non-zero exit code under `--strict`) so an operator
  noticing it acts before sessions run out.

### Holiday-update propagation

If an operator adds a row to `trading_holidays` retroactively (e.g.,
"NYSE will close early on 2027-12-24, didn't realize earlier"), the
existing `trading_sessions` rows for that date are stale. Resolution:
re-run `mt data refresh-sessions --calendar NYSE` for the affected
year. The `ON CONFLICT DO UPDATE` clause overwrites stale rows. No
manual delete required.

## View Rewrite (Migration 028)

Replace slice 142's `target_end_ts = NULL` projection with a real CTE.

Migration 028 re-executes the same DO-block pattern slice 142's
migration 021 uses (see [`minute.py:134`](src/manta_trading/market/schema/migrations/minute.py#L134))
but with `_build_data_status_view_sql` updated to project a real
`target_end_ts`. The new SQL shape, per arch §"Performance pattern":

```sql
WITH exchange_completed_close AS (
  SELECT calendar_id,
         MAX(session_close_utc) AS completed_close_ts
    FROM trading_sessions
   WHERE session_close_utc + INTERVAL '30 minutes' < NOW()
   GROUP BY calendar_id
)
SELECT s.symbol, s.granularity, ...,
       ec.completed_close_ts AS target_end_ts,
       ...
  FROM symbols s
  JOIN instruments i ON i.canonical_id = s.symbol
  LEFT JOIN exchange_completed_close ec
    ON ec.calendar_id = i.trading_calendar_id
  ...
```

Key points:

- The grace-period interval `'30 minutes'` literal in the CTE is
  emitted from `manta_trading.constants.LATE_BAR_GRACE_PERIOD` at
  view-build time (the existing `_interval_literal()` helper in
  `minute.py:81`). View definition rebuild on constant change is a
  follow-up migration; not free, but rare.
- Join is `i.trading_calendar_id`, not `i.venue` — same rationale as
  arch line 138–146.
- The CTE returns ~5 rows per query; the index on `(calendar_id,
  session_close_utc)` makes the WHERE-and-MAX a bounded range scan per
  calendar.
- LEFT JOIN ensures symbols whose `trading_calendar_id` is unknown to
  `trading_sessions` (i.e., a calendar with no rows yet — bug surface
  during initial deployment) still appear, with
  `target_end_ts = NULL`. Health rules don't depend on it.

## Python `TradingCalendar` Refactor

Goal: route `get_trading_hours(date)` and `is_trading_day(date)` through
`trading_sessions` so Python and SQL agree.

### Surface change

[`trading_calendar.py:152`](src/manta_trading/data/base/trading_calendar.py#L152)
(`is_trading_day`) and
[`trading_calendar.py:215`](src/manta_trading/data/base/trading_calendar.py#L215)
(`get_trading_hours`) currently query `trading_holidays` for closure
status and compute session bounds in `_build_trading_hours`.

After refactor:

- `is_trading_day(date)` → `EXISTS(SELECT 1 FROM trading_sessions
  WHERE calendar_id=? AND session_date=?)`. One round-trip, cached.
- `get_trading_hours(date, RTH)` → `SELECT session_open_utc,
  session_close_utc FROM trading_sessions WHERE calendar_id=? AND
  session_date=?`. Returns `TradingHours(session_start=open_utc,
  session_end=close_utc, session_type=RTH, is_trading_day=True)` or
  `None`.
- `get_trading_hours(date, ETH | ALL)` keeps its current path through
  `_build_trading_hours` (calendar-metadata + holiday override). ETH
  not stored in `trading_sessions`; preserved as Python-derived.
- `get_holidays(year)` is unchanged — still queries `trading_holidays`
  directly.
- `get_expected_bar_count(start, end, ...)` is unchanged at the API
  surface but its internal per-day loop now hits `trading_sessions`
  for RTH bounds (one query per day, cache-backed).

### Out-of-horizon behavior

If a caller asks `get_trading_hours(2032-06-15)` and the horizon ends
2028-12-31, the lookup misses. Options:

- **(chosen) Raise `OutOfHorizonError(calendar_id, date,
  current_horizon_end)`** — fail loud, not a silent fallback. The
  exception message names the maintenance command to run.
- (rejected) Fall through to inline computation. Reintroduces the dual
  source-of-truth we just eliminated.
- (rejected) Auto-extend on miss. Hides horizon exhaustion; the
  maintenance command exists explicitly so this is operator-controlled.

## Migration Plan

Three migrations, all on the minute (timescale) DB, applied via
`mt data migrate-cold-start`:

### Migration 025 — `trading_sessions` table

`CREATE TABLE trading_sessions (...)` per the schema above + index.
Nothing seeded — population is the maintenance step's job, run
post-migration.

### Migration 026 — Initial horizon population

Idempotent INSERT-on-conflict-update for every calendar in
`trading_calendars` over `[earliest_seeded_year, current_year + 2]`.

This is a migration (not just a CLI step) because:
- Cold-start without it leaves `data_status` projecting NULL
  `target_end_ts` for every symbol (slice 145's daemon would still
  work, but slice 147 would lose value).
- It's idempotent — re-applying via `mt data migrate-cold-start` is a
  no-op.

### Migration 027 — `mt data --extend` CLI surface

Adds the operator command. Pure code change, no DB DDL — strictly,
this could ship outside the migration sequence, but bundling keeps
"one PR, one operator-visible end state."

(If preferred, fold this into a non-migration code change in the same
slice; the `MINUTE_MIGRATIONS` array gets entries 025 and 026 only,
and the CLI command lands as ordinary code. Either way works; decide
at task breakdown.)

### Migration 028 — `data_status` view rewrite

Re-executes `_build_data_status_view_sql` with the rewritten CTE that
projects `target_end_ts` from `trading_sessions`. Same DO-block
pattern as migrations 021/024. Branches on
`to_regclass('trading_sessions')` — if the table exists, the new view
is installed; otherwise the slice-142-shape view (`target_end_ts =
NULL`) remains. This makes the migration safe to apply on a DB where
025/026 didn't land for any reason.

### Consumer Updates

- [`src/manta_trading/data/base/trading_calendar.py`](src/manta_trading/data/base/trading_calendar.py)
  — `is_trading_day`, `get_trading_hours(SessionType.RTH)`,
  `get_expected_bar_count` route through `trading_sessions`. New
  exception class `OutOfHorizonError`. Existing tests adapt.
- [`src/manta_trading/market/schema/migrations/minute.py:134`](src/manta_trading/market/schema/migrations/minute.py#L134)
  (`_build_data_status_view_sql`) — replace `NULL::TIMESTAMPTZ AS
  target_end_ts` with the real CTE projection.
- New CLI command in `src/manta_trading/cli/commands/data.py` —
  `--extend [--calendar X] [--strict]`.
- New tests:
  - Unit: `populate_trading_sessions` row generation matches
    `_build_trading_hours` output for a sample of NYSE / NASDAQ days
    including weekend / closed holiday / early-close / late-open / DST
    spring-forward / DST fall-back. (This is the **parity test** that
    pins Python/SQL agreement.)
  - Unit: `_build_data_status_view_sql(include_daily_branch=True)`
    contains the new CTE shape.
  - Integration: cold-start applies 025/026/028; `SELECT
    target_end_ts FROM data_status WHERE symbol='AAPL'` returns
    non-NULL.
  - Integration: `is_trading_day` and `get_trading_hours` against a
    seeded calendar match `trading_sessions` rows for sample dates.
  - Integration: `get_trading_hours` for an out-of-horizon date raises
    `OutOfHorizonError`.

## Cross-Slice Dependencies

- **Slice 142** (complete): provides `data_status` view stub that 028
  rewrites; provides `LATE_BAR_GRACE_PERIOD` constant.
- **Slice 145** (next): consumes per-symbol `target_end` for daemon's
  gap selector. Reads `data_status.target_end_ts` directly, or the
  underlying `trading_sessions` table via `TradingCalendar`.
- **Slice 147** (downstream): consumes populated `target_end_ts` in
  `data_status`. Without this slice, `mt data status` shows "—" for
  target_end on every row.
- **Slice 102 / migrations 004-007** (existing): provides
  `trading_calendars` + `trading_holidays` schema + NYSE seed.

## Risks

- **Horizon exhaustion if maintenance lapses.** Mitigation: the
  `--extend` command warns within 90 days of horizon end with
  `--strict` exit code; operators can wire to a CI / cron alert.
  Out-of-horizon `get_trading_hours` raises rather than silently falls
  back. Daemon (slice 145) crashes loudly rather than producing wrong
  gap calculations.
- **Holiday seed gaps.** `trading_holidays` is the input; if it's
  missing a closure (e.g., next year's Good Friday isn't seeded yet),
  the populated `trading_sessions` will incorrectly mark that day as
  trading. This is a pre-existing risk in slice 102's seed; the
  refactor doesn't introduce it but does propagate any defect there
  into both Python and SQL identically (which is the point — drift is
  worse than a shared bug). Mitigation: hold seed-completeness as a
  separate operator concern (annual review), not a blocker for this
  slice.
- **`current_date` skew between maintenance host and DB host.** The
  CTE filters `session_close_utc + grace < NOW()` on the DB; the
  maintenance command computes the row set on the maintenance host.
  Both should be UTC; verified by `SHOW timezone` in the verification
  walkthrough.
- **DB connection failure in `get_trading_hours` / `is_trading_day`.**
  Connection errors propagate as standard `asyncpg` / `psycopg`
  exceptions up through the call stack — no swallowing. Callers
  (daemon, CLI) handle them at the process boundary. No special
  per-method handling required.
- **Maintenance job interrupted mid-batch.** The `ON CONFLICT DO
  UPDATE` upsert is idempotent: re-running after any interruption
  (crash, timeout, peer disconnect) re-emits the same rows with no
  duplicate or partial state. Recovery is simply re-running `mt data
  --extend`.
- **`data_status` CTE timeout under load.** The NFR target (sub-second
  at ~57k symbols) is validated by the verification walkthrough step 6.
  If violated in production, the mitigation path is materializing
  `data_status` (documented as a future-work option in the slice plan
  Notes); no in-slice handling beyond the index design and NFR check.

## Success Criteria

1. Migration 025 creates `trading_sessions(calendar_id, session_date,
   session_open_utc, session_close_utc)` with the documented index;
   primary key on `(calendar_id, session_date)`.
2. Migration 026 populates rows for every calendar in
   `trading_calendars` from earliest-seeded-year through
   current_year + 2.
3. Weekend dates and `market_status='closed'` holidays are absent from
   `trading_sessions`. Early-close days are present with
   `session_close_utc` reflecting the override.
4. Migration 028 rewrites `data_status` to project `target_end_ts`
   from `trading_sessions`; query latency at full universe scope stays
   sub-second (slice 142's NFR holds).
5. `mt data --extend` is idempotent (re-running a populated range
   produces zero changes); `--strict` exits non-zero when any
   calendar's max `session_date` is within 90 days.
6. Python `TradingCalendar.is_trading_day` and `get_trading_hours(date,
   RTH)` return values that match the corresponding `trading_sessions`
   row for a battery of test dates: weekday open, weekend, closed
   holiday (Christmas), early-close (Black Friday or July 3rd),
   late-open (none currently seeded — test infrastructure-only),
   DST-spring-forward, DST-fall-back.
7. `get_trading_hours(date, RTH)` for a date past the populated horizon
   raises `OutOfHorizonError` carrying the calendar_id, date, and
   current horizon end.
8. `data_status.target_end_ts` is non-NULL for every symbol whose
   `instruments.trading_calendar_id` exists in `trading_sessions`. For
   a synthetic calendar with no rows, target_end_ts is NULL; symbols
   on that calendar still appear in the view (LEFT JOIN preserved).
9. Unit test pinning a parity check between
   `populate_trading_sessions` row output and
   `TradingCalendar._build_trading_hours` output passes.
10. Cold-start (`mt data migrate-cold-start --skip-probe --yes`) is
    idempotent: re-application skips already-applied migrations
    025/026/028.

## Verification Walkthrough

Operator demo script. Run from project root with `MT_TIMESCALE_DB_URL` /
`MT_MARKET_DB_URL` set.

**Status (verified 2026-05-02 against trading_test DB):** all read-only
steps confirmed working as designed; sample output captured below. The
destructive cold-start step (`mt data migrate-cold-start`) is the only
remaining verification; it TRUNCATEs bar tables and is operator-gated.

The integration test suites at `test/integration/test_migrations_025_026.py`
(9 tests) and `test/integration/test_migration_028.py` (5 tests) automate
the apply + idempotency + latency assertions:

```bash
pytest test/integration/test_migrations_025_026.py test/integration/test_migration_028.py
# 14 passed in 5.91s
```

### 1. Pre-state snapshot

```bash
psql "$MT_TIMESCALE_URL" -c "
  SELECT to_regclass('trading_sessions') AS table_exists;
  SELECT view_definition FROM information_schema.views
   WHERE table_name = 'data_status' LIMIT 1;
"
```

Expect: `table_exists = NULL`; view definition contains `NULL::timestamptz
AS target_end_ts` (slice 142 stub).

### 2. Apply migrations

```bash
mt data migrate-cold-start --skip-probe --yes
```

Expect log lines: `applied migration 025_trading_sessions_table`,
`applied migration 026_trading_sessions_initial_population`, `applied
migration 028_data_status_view_target_end_ts`.

### 3. Inspect populated table

```bash
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT calendar_id, COUNT(*) AS sessions,
         MIN(session_date) AS first_date,
         MAX(session_date) AS last_date
    FROM trading_sessions
   GROUP BY calendar_id ORDER BY calendar_id;
"
```

Expect: one row per seeded calendar; `last_date` ≥ end of
current_year + 2 - 1.

**Verified 2026-05-02 (trading_test):**
```
NASDAQ:  2280 sessions (2020-01-02 → 2028-12-29)
NYSE:    2280 sessions (2020-01-02 → 2028-12-29)
```

### 4. Verify holiday handling

```bash
psql "$MT_TIMESCALE_URL" -c "
  SELECT session_date, session_close_utc
    FROM trading_sessions
   WHERE calendar_id='NYSE'
     AND session_date IN ('2026-12-25','2026-11-27','2026-07-03');
"
```

Expect:
- `2026-12-25` (Christmas, closed) — **absent**.
- `2026-11-27` (Black Friday, early close) — present, `session_close_utc`
  reflects 13:00 ET = 18:00 UTC (or 19:00 UTC depending on EST/EDT).
- `2026-07-03` (early close before July 4) — present, `session_close_utc`
  reflects early close.

**Verified 2026-05-02 against 2024 dates** (current seed range is 2020-2026;
2026 holidays not all seeded yet — re-verify against 2026 dates after seed
extension. The 2024 dates exhibit the same logic):
```
2024-07-03 close=2024-07-03 13:00 ET (early close, 17:00 UTC)
2024-11-29 close=2024-11-29 13:00 ET (Black Friday, 18:00 UTC)
2024-12-24 close=2024-12-24 13:00 ET (Christmas Eve, 18:00 UTC)
2024-12-25                            — absent (Christmas, closed) ✓
```

### 5. View target_end_ts

```bash
psql "$MT_TIMESCALE_URL" -c "
  SELECT symbol, target_end_ts
    FROM data_status
   WHERE symbol IN ('AAPL','MSFT','GOOGL') LIMIT 5;
"
```

Expect: non-NULL `target_end_ts`. Value matches the most recent
session close + grace that's already past `NOW()`. Symbols on different
calendars (NYSE vs NASDAQ) get different values.

**Caveat:** AAPL is `trading_calendar_id='NASDAQ'` while MSFT and GOOGL
are `'NYSE'` (per the slice 141 instruments seed). This is correct —
AAPL is listed on NASDAQ. NYSE and NASDAQ share an identical RTH
schedule, so the surfaced `target_end_ts` will typically match across
all three.

**Verified 2026-05-02 (trading_test):**
```
AAPL   daily/minute  cal=NASDAQ  target_end_ts=2026-05-01 20:00:00+00 (NYSE close + 30m grace)
GOOGL  daily/minute  cal=NYSE    target_end_ts=2026-05-01 20:00:00+00
MSFT   daily/minute  cal=NYSE    target_end_ts=2026-05-01 20:00:00+00
```

### 6. Latency check

```bash
psql "$MT_TIMESCALE_URL" -c "\timing
  SELECT COUNT(*) FROM data_status;
"
```

Expect sub-second over the ~57k symbol universe (slice 142 NFR).

**Verified 2026-05-02 (trading_test, 65,554 rows): 31 ms** — well within
the sub-second NFR.

### 7. Python parity

```bash
python -c "
from datetime import date
from manta_trading.data.base.trading_calendar import TradingCalendar
import os
cal = TradingCalendar('NYSE', os.environ['MT_TIMESCALE_URL'])
for d in [date(2026,12,25), date(2026,11,27), date(2026,7,4), date(2026,7,3)]:
    print(d, cal.is_trading_day(d), cal.get_trading_hours(d))
"
```

Expect (against the 2024 seed, since 2026 holidays not all seeded):
- `2024-12-25 False None` (Christmas)
- `2024-11-29 True TradingHours(...end=13:00 ET / 18:00 UTC...)`
- `2024-07-04 False None` (Independence Day)
- `2024-07-03 True TradingHours(...end=13:00 ET / 17:00 UTC...)`

Then verify each `trading_hours.session_end` matches the
`session_close_utc` from step 4.

**Verified 2026-05-02 (trading_test):**
```
2024-12-25  is_trading_day=False  trading_hours=None
2024-11-29  is_trading_day=True   session_end=2024-11-29 13:00 ET ✓
2024-07-04  is_trading_day=False  trading_hours=None
2024-07-03  is_trading_day=True   session_end=2024-07-03 13:00 ET ✓
```

### 8. Maintenance command

```bash
mt data --extend --calendar NYSE
psql "$MT_TIMESCALE_URL" -c "
  SELECT MAX(session_date) FROM trading_sessions WHERE calendar_id='NYSE';
"
```

Expect: `session_date` extended by the fixed calendar-based amount.

Re-run is a no-op:

```bash
mt data --extend --calendar NYSE
```

Expect log: `0 sessions inserted, 0 updated`.

### 9. Strict horizon check

```bash
mt data --extend --calendar NYSE --strict
echo "exit: $?"
```

Expect: exit 0 if max `session_date` > today + 90 days; exit non-zero
otherwise with a clear "horizon ends in N days" message.

**Verified 2026-05-02 (trading_test):**
```
NASDAQ  max_date=2028-12-29 (972 days remaining) [OK]
NYSE    max_date=2028-12-29 (972 days remaining) [OK]
```
Strict-mode warning behavior is also covered by unit tests
(`test/unit/cli/commands/test_data_extend.py::TestExtendStrict`).

### 10. Out-of-horizon error

```bash
python -c "
from datetime import date
from manta_trading.data.base.trading_calendar import TradingCalendar
import os
cal = TradingCalendar('NYSE', os.environ['MT_TIMESCALE_URL'])
try:
    cal.get_trading_hours(date(2099, 6, 15))
except Exception as e:
    print(type(e).__name__, e)
"
```

Expect: `OutOfHorizonError` with the calendar_id, date, and current
horizon end in the message.

**Verified 2026-05-02 (trading_test):**
```
OutOfHorizonError: Date 2099-06-15 is beyond the populated trading_sessions
horizon for calendar 'NYSE' (horizon ends 2028-12-29). Run 'mt data --extend'
to extend the horizon.
```

### 11. Idempotency

```bash
mt data migrate-cold-start --skip-probe --yes
```

Expect: `migration 025 already applied, skipping` (× 3 for 025/026/028).

## Open Questions

1. **Horizon auto-extension trigger.** Slice 144 ships only operator-
   driven `mt data --extend`. Slice 147 will add automated horizon
   extension so operators never need to run the command manually.
   The 90-day warning surfaced by `--strict` serves as a safety net
   during the gap between 144 and 147 landing.
2. **CLI placement.** Resolved: `mt data --extend [--calendar X]
   [--strict]`. The fixed extension amount (calendar-based, not a
   user-supplied year) is defined as a constant; decide the value at
   task breakdown.

## Effort

Relative effort: **2 / 5**. One small table, one well-bounded view
rewrite, a Python class refactor that mostly shrinks the class, and a
parity test that's the architectural keystone. No daemon work, no
concurrency, no API contracts changing.
