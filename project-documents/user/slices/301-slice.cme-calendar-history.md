---
docType: slice-design
slice: cme-calendar-history
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies: []
interfaces:
- 302
- 303
- 304
- 305
- 306
- 307
- 308
dateCreated: 20261101
dateUpdated: 20261101
status: not_started
turns: 55
promptTokens: 5629483
cachedTokens: 5489362
cacheWriteTokens: 140007
completionTokens: 46073
durationSeconds: 497.8
costUsd: 3.140179
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-opus-5-5:
    rounds: 3
    costUsd: 3.1401789
    completionTokens: 46073
revision_number: 2
---

# Slice Design: cme-calendar-history

## Overview

The `CME_EQUITY` (ES) and `CME_METALS` (GC) calendars hold sessions from 2020-01-01 only. Databento's CME dataset starts on 2010-06-06. Today a 2010 to 2019 instant raises `OutOfPopulatedRangeError` in `TradingCalendar.sessions_between`. No bar, statistics record or definition from that range can be placed in a session, so 302, 303, 304 and 307 cannot ingest it.

This slice fills `[2010-06-06, 2020-01-01)` for both calendars from dated CME documents:

- **Dated regular hours.** The Globex daily close moved during the period, so regular hours move into a new table, `trading_calendar_hours`, with one row per era. Each row cites its source.
- **Pre-2020 exceptions.** Every holiday closure, early close, late open and unscheduled closure from 2010-06-06 to 2019-12-31 is one row in `trading_holidays`, and each row cites a dated CME document. Nothing is generated from a holiday rule (221 D5).

It also adds a free check, built only on Databento metadata calls, that runs every proposed pre-2020 session against the provider's `ohlcv-1m` record counts. Bars bought in 304 and 307 are then placed in sessions that have already been checked against real data, not found wrong after the money is spent.

Nothing is bought and no tier changes. Sessions from 2020-01-01 on stay byte-identical, and the migration proves it.

## Value

- **Unblocks the history slices.** 302 places 2010 to 2019 bars in sessions, 303 assigns statistics records to sessions, and 304 and 307 ingest that range. None of them needs a calendar change after this slice.
- **Session facts later slices read stay right for the whole dataset.** The 1d bar is the CME session, intraday roll-ups are clipped to the session open (306), and the tick-bar count restarts at each session open (308). All of them read session open and close per calendar and date from `trading_sessions`, which this slice extends.
- **Checked before money is spent.** The count check confirms that the provider has no `ohlcv-1m` record between two proposed sessions and that every proposed session traded, for every pre-2020 session, at no cost.
- **No behaviour change today.** NYSE, NASDAQ and every CME session from 2020-01-01 on are unchanged.

## Technical Scope

**Included**

- `CME_DATASET_START = date(2010, 6, 6)`, defined once (D5).
- `trading_calendar_hours`: dated eras (open, close, source) for `CME_EQUITY` and `CME_METALS`. The latest era of each equals today's `trading_calendars` hours (D1, D2).
- Session generation uses the era in force on each session's date. A dated calendar asked for a date before its first era raises an error naming the calendar and the date (D3).
- Pre-2020 exception rows for both calendars, each citing a dated CME document, in new seed modules (D4).
- One primary-track migration that checks its preconditions, creates the table, inserts eras and exceptions with a plain `INSERT`, fills the pre-2020 sessions and proves that 2020 onward is unchanged (D6).
- Migrations 058 and 059 keep producing exactly what they produce today on a fresh database (D7).
- `scripts/check_cme_history_counts.py`, a one-off free metadata check of every pre-2020 session (D8).
- A cutover script that puts the migration into production (D9).

**Excluded**

- Any schema, table or projector for bars, statistics or definitions (302, 303). Any purchase (304, 307).
- NYSE and NASDAQ. They have no eras and keep reading `trading_calendars`.
- Dates before 2010-06-06, and eras or exceptions on or after 2020-01-01. A documented hours change from 2020 on is recorded as a finding and not entered (D2).
- Intra-session halts. CME's 15:15–15:30 CT equity halt (removed in 2021, SER-8776) lies inside a session. It produces no records and does not move a session boundary. The model has no halt concept and gains none.
- The `TradingCalendar` ETH and ALL paths, which build hours from `trading_calendars` metadata. No CME caller uses them (`has_extended_hours` is false), and they stay as they are.
- `scripts/verify_cme_sessions.py`. It needs no change: it reads the populated range from `TradingCalendar.sessions_between` and has no 2020 bound in its code, so it accepts a pre-2020 job once the sessions exist. Its first real pre-2020 run is in 304, because no pre-2020 DBN file is held.

## Dependencies

### Prerequisites

- **221 and 232 session model** (complete): `trading_calendars`, `trading_holidays`, `trading_sessions`, `populate_trading_sessions`, `extend_calendar_sessions`, `SessionIndex`, `locate_sessions` and the `session_boundary` check.
- **Sources:** CME Globex holiday spreadsheets and PDFs, trading-hours notices, Special Executive Reports and contract-specification pages for 2010 to 2019, read through the Wayback Machine as 221 and 232 did (the live cmegroup.com refuses automated reads).
- **Databento metadata:** `metadata.get_record_count`, already wrapped by the tick adapter (`data/tick/databento/adapter.py`). It is free, and it needs the existing Databento API key.

### Interfaces Required

- `session_population.populate_trading_sessions` and `session_interval`, changed here to take an hours schedule (D3).
- `session_extension.extend_calendar_sessions` and `_read_calendar`, changed here to read eras for dated calendars.
- The callers of `populate_trading_sessions` and `extend_calendar_sessions`: migration 026 (`minute.py`, NYSE and NASDAQ), migrations 058 and 059, `mt data extend` (`cli/commands/data.py`), `auto_extend`, and `TradingCalendar._build_trading_hours`. Each is updated to pass or build a schedule, and each keeps its behaviour.
- `CmeCalendarSeed`, `closed_day` and `early_close_day` in `seed_cme_calendar.py`.
- `MINUTE_MIGRATIONS` and the migration runner. Each migration runs in its own transaction, so a raised check rolls the whole migration back.
- `scripts/cutover_common.py` (`preflight`, timer hold, `install`, `migrate`).

## Architecture

### Component Structure

```
market/schema/seed_cme_hours.py              CME_DATASET_START,
                                             the eras for both calendars, each with its source
market/schema/seed_cme_equity_history.py     CME_EQUITY exceptions 2010-06-06..2019-12-31
market/schema/seed_cme_metals_history.py     CME_METALS exceptions 2010-06-06..2019-12-31
data/base/session_hours.py                   HoursEra, HoursSchedule (era in force on a date;
                                             NoHoursEraError)
data/base/session_population.py              takes an HoursSchedule instead of fixed open/close
data/base/session_extension.py               builds the schedule: eras for a dated calendar,
                                             the trading_calendars row for any other
market/schema/migrations/minute.py           next free id: the calendar-history migration;
                                             058/059 pin their own hours (D7)
scripts/check_cme_history_counts.py          one-off free ohlcv-1m count check (D8)
scripts/cutover_301_calendar_history.py      production rollout (D9)
```

The two history seed modules are data. They are kept out of `seed_cme_calendar.py` (472 lines) and `seed_cme_metals_calendar.py` (391 lines), which are already past the size guideline. Code modules stay within about 300 lines.

### Data Flow

**Sourcing (implementation, before code):** documents are read, then entered as era and exception rows with their citations. The count check is then run over the proposed sessions. Each finding is resolved by a sourced row or recorded as unresolved, and never by a row the counts alone suggest.

**Migration (once per primary database):**

1. Check that no `CME_EQUITY` or `CME_METALS` row exists in `trading_holidays` or `trading_sessions` before 2020-01-01. If any does, abort and name the counts.
2. Read every CME session dated 2020-01-01 or later into memory.
3. Create `trading_calendar_hours`, add `trading_calendars.hours_dated` and set it for both CME calendars (D3), then insert the eras and the pre-2020 exceptions with a plain `INSERT`.
4. Check that each calendar's latest era equals its `trading_calendars` hours. Abort if not.
5. `extend_calendar_sessions(conn, cal, start=CME_DATASET_START, end=date(2019, 12, 31))` for each calendar.
6. Read the 2020-onward sessions again and compare them with step 2. Abort on any difference.

**After the migration:** every population call (forward extension by `mt data extend` and `auto_extend`, a repair) builds the calendar's schedule from `trading_calendar_hours` and uses the era in force on each session date. Forward extension always lands in the latest era, so it writes what it writes today. Readers (`TradingCalendar.sessions_between`, `get_trading_hours`, `SessionIndex`, `locate_sessions`) read `trading_sessions` and are unchanged. They see sessions from 2010-06-07 on, the first session, which opens on the evening of Sunday 2010-06-06.

### State Management

- **New table (primary database):** `trading_calendar_hours`, with one row per calendar and era.
- **Changed tables:** `trading_holidays` and `trading_sessions` gain `CME_EQUITY` and `CME_METALS` rows dated 2010-06-06 to 2019-12-31.
- **Changed column:** `trading_calendars.hours_dated boolean NOT NULL DEFAULT false`, true for `CME_EQUITY` and `CME_METALS` (D3).
- **Unchanged:** every row dated 2020-01-01 or later, every NYSE and NASDAQ row, the `trading_calendars` hours, and the tick database.
- **Privileges:** the table is created by the migrator role, so the default privileges from `provision_roles.sql` give the application role read access. No grant change is needed.

## Technical Decisions

### D1 — Regular hours become dated eras
Status: decided

The columns below assume no era's Sunday open differs from its daily reopen. Step 1 of the Implementation Notes sources that before any code is written. If a dated document shows a difference, D1 is reopened: the schema change goes into this document and passes review before code is written against it.

A new table:

```sql
CREATE TABLE trading_calendar_hours (
    calendar_id     text NOT NULL REFERENCES trading_calendars (calendar_id),
    effective_from  date NOT NULL,   -- first session date (the date it closes) in the era
    market_open     time NOT NULL,   -- previous day when after close
    market_close    time NOT NULL,
    source          text NOT NULL,
    PRIMARY KEY (calendar_id, effective_from)
);
```

An era is in force from its `effective_from` until the next era's. Eras are keyed by session date, the date a session closes, which matches how `trading_sessions` dates sessions. When CME announces a change "effective Sunday X for trade date X+1", `effective_from` is X+1.

An era carries one open time, used for every session including the one that opens on Sunday. The forum lead for the pre-2015 ES schedule suggests a daily reopen that differed from the Sunday open, but a lead is not a source (D2). Whether any era's Sunday open differs from its daily reopen is the first thing sourced (Implementation Notes, step 1). If a dated document confirms a difference, a Sunday-open column is added to this table in the same migration, before any code is written against it. Otherwise there is no such column. An exception row's `late_open_time` still overrides an era's open, as today.

- *Rejected: era hours as exception rows.* An exception models one day's closure, early close or late open. A different regular close for five years is none of those, and entering it that way would make every pre-2015 weekday an exception.
- *Rejected: hours known only to a CME routine, with no table.* `mt data extend` and the repair paths rebuild from the tables, so hours held outside them would be overwritten by the first rebuild.
- *Rejected: a Sunday open or Friday close distinct from the daily times, built now.* No document read so far shows one. If the sourcing finds one, it is added then, as above, not built speculatively now.

### D2 — Which eras are entered
Status: decided

- Eras are searched for and entered only for 2010-06-06 to 2019-12-31, plus one current era per calendar that carries today's hours and is checked equal to its `trading_calendars` row (migration step 4 and a unit test). The current era starts at the first session after the last pre-2020 change, or at `CME_DATASET_START` if there was none.
- A documented regular-hours change on or after 2020-01-01 is not entered, because 2020 onward stays untouched. It is recorded as a finding in the slice's implementation notes for the PM.
- An era is entered only from a dated CME document: a notice, a Special Executive Report, or a contract-specification page on Wayback whose snapshot date bounds the change. The report that the ES close moved from 16:15 CT to 16:00 CT around September 2015 is a forum lead, not a source. ES and GC are sourced independently.
- If no dated document can be found for an era, the fill stops there (D5) and the slice says so. A plausible value is never entered.

### D3 — Population takes an hours schedule; no era is an error
Status: decided

`session_hours.py` holds `HoursEra(effective_from, market_open, market_close)` and `HoursSchedule`, an ordered tuple of eras with one method: the era in force on a date. `populate_trading_sessions(calendar_id, start, end, timezone, schedule, holidays_rows)` replaces the fixed open and close pair with the schedule. `session_interval` is unchanged and still the one place the open-after-close rule lives.

- **Dated calendars.** A calendar is dated when its `trading_calendars.hours_dated` is true, which the migration sets for `CME_EQUITY` and `CME_METALS`. `_read_calendar` already reads that row, so the runtime code in `data/base` imports nothing from `market/schema` (the dependency runs the other way today). For these, `_read_calendar` builds the schedule from `trading_calendar_hours` only. If the calendar has no era rows, it raises. A session date before the first era raises `NoHoursEraError` naming the calendar and the date. There is never a fallback to the `trading_calendars` hours.
- **Every other calendar** gets `HoursSchedule.undated(open, close)`, built from its `trading_calendars` row: one era that covers every date. NYSE and NASDAQ output is unchanged, and a unit test compares it to the current output.
- Callers that pass hours they already hold (`TradingCalendar._build_trading_hours`, migration 026) build an undated schedule from those hours, which keeps their behaviour.

Whether a calendar is dated is stated explicitly, not inferred from whether era rows exist. If rows went missing, inferring would silently revert the pre-2020 sessions to today's hours. `CME_DATASET_START` stays in `seed_cme_hours.py`. Its consumer, 302, is tick code, which already imports the CME seed modules (`data/tick/constants.py`).

### D4 — Pre-2020 exceptions are sourced rows
Status: decided

Every closure, early close and late open from 2010-06-06 to 2019-12-31 is entered as one row per day. Each row carries a `# source:` comment, in the format of the existing seed modules, citing the document, its product-group row and its URL (Wayback where the original is gone). The rows sit in `seed_cme_equity_history.py` and `seed_cme_metals_history.py`, which use the existing `closed_day` and `early_close_day` helpers. A `late_open_day` helper is added only if a document shows a late open.

- Unscheduled closures are included where CME documents them. For example, the 2012-10-29 and 10-30 Hurricane Sandy closures and the 2018-12-05 national day of mourning are checked per calendar and entered as the documents state.
- ES and GC are read from their own product-group rows. Metals differ from equity and differ between years (see the `seed_cme_metals_calendar.py` docstring), so nothing is copied from one calendar to the other.
- Times are entered in America/Chicago. A document stated in ET is converted, and the comment says so.
- A unit test fails any era or exception row that has no source, and any exception row dated outside `[CME_DATASET_START, 2020-01-01)`.

### D5 — `CME_DATASET_START` is the first supported date
Status: decided

`CME_DATASET_START = date(2010, 6, 6)` is defined once, in `seed_cme_hours.py`. It is a Sunday. The first session populated is dated Monday 2010-06-07 and opens that Sunday evening. 302's history wants refuse a start before it, so this constant is the single definition of where supported history begins.

If an era cannot be sourced, the constant moves later, to the `effective_from` of the earliest sourced era, so it never runs ahead of the populated sessions. Each calendar's earliest era must start on or before it, which a unit test checks. The unsupported range is written in the implementation notes.

`CME_EQUITY_SEED_START` and `CME_METALS_SEED_START` stay at 2020-01-01. They mark where 058 and 059 seeded, and changing them would change those migrations' output on a fresh database.

### D6 — One migration, plain inserts, a before-and-after proof
Status: decided

One primary-track migration, `python_fn` style like 058, takes the next free id. Read the last id in `MINUTE_MIGRATIONS` at implementation (today it is `065_instruments_name`) rather than trusting a number in a plan. It runs the six steps in Data Flow inside the runner's transaction, so any failed check rolls everything back.

- The precondition check (step 1) and the plain `INSERT` (no `ON CONFLICT DO NOTHING`) mean a stored row can never silently win over a seed row. A conflict fails the migration.
- Step 5 upserts through `extend_calendar_sessions`, and step 1 guarantees there is nothing to overwrite.
- The 2020-onward comparison (steps 2 and 6) runs inside the migration on every database it is applied to, production included. It is not only a test.
- The migration is not idempotent by design. It records itself as applied, and a re-run is refused by the runner. The fresh-database and populated-database paths are both tested.
- Migration chain tests that name the last id are updated, as every primary migration does.

### D7 — Migrations 058 and 059 pin their own hours
Status: decided

On a fresh database, 058 and 059 run before `trading_calendar_hours` exists, and the CME calendars are dated (D3). If they still called `extend_calendar_sessions`, it would fail for lack of eras. Instead, they build an undated schedule from their own seed constants (`CME_EQUITY_CALENDAR`, `CME_METALS_CALENDAR`, 17:00 to 16:00) and write sessions through the same population function and upsert helper. That is exactly what they produce today. Their output still depends on `populate_trading_sessions`, as 058's does now, so a regression test pins it: `test_058_059_fresh_output_unchanged` (in `test_migration_057_058_cme_calendar.py`) applies the chain to 059 on a fresh database and compares every CME session and holiday row with a snapshot captured before this slice's code change. A later change to `populate_trading_sessions` or the upsert helper must keep it passing. The new migration's step 5 has the same dependency, and its integration tests cover it. The upsert statement and the holiday-bound clamp are factored out of `extend_calendar_sessions` so that 058 and 059 reuse them rather than copy them.

On a live database where the new code is installed but the migration is not yet applied, extending a CME calendar fails loudly and names the missing eras. The cutover (D9) holds the timers across install and migrate, and they stay held if migrate fails, so this window is not reached in production.

### D8 — A free count check against provider data
Status: decided

A one-off verification run, kept in `scripts/` as the record of how the evidence was gathered (like `verify_cme_sessions.py`). No later slice uses it: 305 builds its own completeness verdicts from the manifest and ledger. `scripts/check_cme_history_counts.py --calendar CME_EQUITY --parent ES.FUT [--start] [--end]` reads the calendar's sessions in the range and makes two metadata calls per session through the adapter's `get_record_count` on `GLBX.MDP3`, `ohlcv-1m`, `stype_in=parent`:

- **Inside the session** `[open, close)`: expected greater than 0. A zero is a finding: either an undocumented closure or a provider data gap.
- **In the gap to the next session** `[close, next_open)`: expected 0. A non-zero count is a finding: either a wrong boundary (an hours era, an early close, a missed late open) or a provider anomaly. Every bar in such a gap would fail `session_boundary` at ingest.

`ohlcv-1m` stamps a bar at its minute's open, so a bar inside the session never starts at or after the close.

Volume: about 2,400 sessions per calendar from 2010-06-07 to 2019-12-31, so about 4,800 calls per calendar and 9,600 in total. Calls are made one at a time, with no concurrency, which is enough for a one-off run. A Databento rate limit shows up as a 429 and is paced by the backoff below. A run can be split by year with `--start` and `--end`. The script prints one line per finding with its window and count, and exits non-zero if there was any finding or failed call.

Failure handling: each call has a timeout (the SDK's metadata timeout, confirmed from its source and set explicitly). A call that raises the adapter's `ProviderTransientError` (429, 5xx, connection, timeout) is retried a bounded number of times with backoff. A call that still fails is printed as a failed session and is never counted as zero. Any other provider error (auth, bad request) stops the run. There is no resume state: `--start` and `--end` rerun only the failed or remaining range. Metadata calls are free (the architecture's cost table was measured the same way), so the check buys nothing. Counts are evidence, never a source. A finding is resolved by a sourced row (D4, D2) or recorded as unresolved in the implementation notes, naming the date, the window and the count.

Window semantics (end exclusive, nanosecond precision) are confirmed against the databento-python SDK source, not the client-rendered docs site.

- *Rejected: wait for 304 and 307 to find wrong days at ingest.* That finds them after money is spent, one bought day at a time.
- *Rejected: generate exceptions from zero-count days.* That would be a holiday rule derived from data, and it cannot tell a closure from a provider gap.

### D9 — Production rollout is a cutover script
Status: decided

`upgrade_production.py` cannot carry this release: it is for code-only releases and stops when an install brings a new migration. The project's convention is a cutover script per release that has a migration (922, 0244). So the release that ships 301 gets one, assembled from the `cutover_common` and `upgrade_production` helpers with no new mechanism. If the release carries other migrations, the same script applies them, as 0244 did. `scripts/cutover_301_calendar_history.py`, in the pattern of `cutover_0244_symbol_names.py` and built on `cutover_common`, runs these steps: preflight, hold timers, install the release, migrate the primary track to the new id with the target verified, verify, release timers. As in 0244, timers are released as found after a failed verify, and stay held after a failed migrate, because extending a CME calendar fails until the migration is applied (D7). The narration says so, and a rerun after the fix releases them. Verify means: both CME calendars' first session is dated 2010-06-07 (or the moved `CME_DATASET_START`), `sudo mt-run data health` passes, and `mt data overview` exits 0. Claude runs it after merge. No root step and no PM host step is needed.

## Implementation Details

### Database / Storage Schema

`trading_calendar_hours` as in D1, in the primary database. Plus one column on `trading_calendars` (D3). The changes to `trading_holidays` and `trading_sessions` are rows only, with no DDL. Expected volume is a handful of era rows, a few hundred exception rows, and about 2,500 sessions per calendar.

## Integration Points

### Provides to Other Slices

- **302:** `CME_DATASET_START` as the single dataset start. History wants refuse anything earlier, and sessions from 2010-06-07 place 2010 to 2019 bars through `locate_sessions`.
- **303:** the same sessions, for placing a statistics record by reference time and trading date.
- **304 and 307:** pre-2020 sessions that the count check has already verified against provider data. `verify_cme_sessions.py` runs on the first bought pre-2020 jobs unchanged. A wrong day found at ingest is fixed by a sourced row and `mt data tick reset` of the failed units.
- **305:** the pre-2020 sessions its per-session completeness verdicts read. A zero-count finding recorded here as a provider gap is a known input to those verdicts.
- **306 and 308:** session open and close per calendar and date in `trading_sessions`, read through `TradingCalendar`, for the 1d bar, roll-up clipping and the tick-bar reset.

### Consumes from Other Slices

- **221 and 232:** the session model, extended backwards. No other slice in this plan is needed.

## Success Criteria

### Functional Requirements

1. Both CME calendars have sessions from 2010-06-07 (or the first session on or after a moved `CME_DATASET_START`), with no gaps except weekends and sourced closures.
2. Every CME session dated 2020-01-01 or later is identical before and after the migration. This is checked inside the migration and in an integration test.
3. Every era and every pre-2020 exception row cites a dated source.
4. Each calendar's latest era equals its `trading_calendars` hours. No era starts on or after 2020-01-01 except the current era, when the last pre-2020 change takes effect on the first 2020 session.
5. A session inside an earlier era carries that era's open and close. For a dated calendar, a date before the first era raises `NoHoursEraError` naming the calendar and the date.
6. `locate_sessions` places an instant inside a pre-2020 era's hours and refuses one in that era's daily break with `session_boundary`, on both sides of each era change.
7. NYSE and NASDAQ sessions are unchanged. Migrations 058 and 059 produce today's rows on a fresh database.
8. `check_cme_history_counts.py` has been run over the full pre-2020 range for both calendars. Every finding is resolved by a sourced row or recorded as unresolved, with date, window and count.

### Technical Requirements

- Unit tests:
  - schedule lookup: before the first era, on an era's first date, across a change, and a late-open exception overriding the era's open;
  - undated schedule output equal to today's for NYSE, NASDAQ and the current CME hours;
  - sources present, exception dates inside the range, `CME_DATASET_START` not after any calendar's first era, latest era equal to the calendar row;
  - a dated calendar with no eras raises;
  - pre-2020 era-boundary instants, in the pattern of `test_cme_equity_boundaries.py` and its JSON fixture;
  - the count-check window logic against a stubbed adapter.
- Integration tests:
  - the migration on a fresh database (the full chain, 058 and 059 before it) and on a database populated as production is;
  - each precondition and the 2020-onward comparison abort with a clear message;
  - `extend_calendar_sessions` forward-extends a CME calendar exactly as before.
- `ruff check`, `ruff format` on touched files only, and `mypy` clean. Code modules stay within about 300 lines.

### Integration Requirements

- 302 and 303 place 2010 to 2019 data with no calendar change.
- After the cutover, production's 2020-onward behaviour is unchanged, and `mt data extend` and `auto_extend` extend both CME calendars as before.

### Verification Walkthrough

Run from the checkout root, with the test database URL exported from `.env` (the project's test setup).

1. **Tests.**
   ```bash
   uv run pytest test/unit/data/base test/unit/test_seed_cme_calendar.py \
       test/unit/test_seed_cme_metals_calendar.py test/unit/test_schema_migrations.py -q
   uv run pytest test/integration -q -k "calendar or session or migration"
   ```
   Expected: all pass, including the new schedule, source, boundary and migration tests.

2. **Free count check** (needs the Databento key; costs nothing).
   ```bash
   uv run python scripts/check_cme_history_counts.py --calendar CME_EQUITY --parent ES.FUT
   uv run python scripts/check_cme_history_counts.py --calendar CME_METALS --parent GC.FUT
   ```
   Expected: exit 0 with no findings or failed calls, or a non-zero exit with each finding matched by an entry in the implementation notes.

3. **Sessions on a migrated test database.**
   ```sql
   SELECT calendar_id, min(session_date), count(*) FROM trading_sessions
    WHERE calendar_id IN ('CME_EQUITY', 'CME_METALS') GROUP BY 1;
   SELECT * FROM trading_calendar_hours ORDER BY calendar_id, effective_from;
   ```
   Expected: both calendars start at 2010-06-07, and the eras list ends in the current 17:00 to 16:00 era with every row sourced.

4. **A held 2024 job still places.**
   ```bash
   uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY \
       /data/market-data/databento/GLBX-20240930-USM7UXXJBA
   ```
   Expected: exit 0, the same output as before the slice.

5. **Production** (after merge).
   ```bash
   uv run python scripts/cutover_301_calendar_history.py <release-tag>
   ```
   Expected: exit 0. The narration in `project-documents/user/notes/` shows the migration applied, the first CME sessions dated 2010-06-07, health passing and the timers released.

## Risk Assessment

### Technical Risks

- **Source coverage.** Ten years of holidays, unscheduled closures and hours eras for two calendars, from archived documents. Older years may survive only as PDFs or as notices, not the spreadsheet zips 221 used.
- **The ES close history** rests on a forum lead until a CME document dates it.

### Mitigation Strategies

- The count check (D8) tests every proposed session against provider data before anything is bought, so a missed day surfaces now rather than at 304 or 307.
- A gap that no document resolves moves `CME_DATASET_START` later (D5) or is recorded as unresolved. It never becomes a guessed row.
- The 2020-onward comparison inside the migration bounds the change to the new range.

## Implementation Notes

### Development Approach

Relative effort 3/5. Suggested order, committing at each section:

1. Source the hours eras for ES and GC (D2), first establishing for each era whether the Sunday open differs from the daily reopen (D1). This fixes the table's columns before any code is written.
2. `session_hours.py`, the schedule-taking `populate_trading_sessions`, the dated-calendar reader in `session_extension.py`, the caller updates, and 058 and 059 pinned (D3, D7), with `test_058_059_fresh_output_unchanged` and unit tests that prove NYSE, NASDAQ and current CME output are unchanged.
3. `check_cme_history_counts.py` (D8). Running it against the current 2020-onward sessions first is a free calibration: it should report no findings there, since those days were already checked against trade counts in 221 and 232.
4. Each calendar's pre-2020 exceptions (D4), with the source tests.
5. The migration (D6) and its integration tests. Then run the count check over the pre-2020 range on a migrated test database and resolve or record every finding.
6. The cutover script (D9).

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit.
- Findings to record for the PM, in a section of this document added at implementation: any documented hours change from 2020 on (D2), any unsourced era and the resulting `CME_DATASET_START` (D5), and every unresolved count finding (D8).
- If the sources for an era or a day cannot be found, stop and say so rather than entering a plausible value.
