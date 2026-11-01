---
docType: slice-design
slice: cme-calendar-history
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies: []
interfaces: [302, 303, 304, 306, 307]
dateCreated: 20261101
dateUpdated: 20261101
status: not_started
---

# Slice Design: cme-calendar-history

## Overview

This slice extends the `CME_EQUITY` and `CME_METALS` calendars back to 2010-06-06, the start of the Databento CME dataset. Today both calendars hold sessions only from 2020-01-01. A bar, statistics record or definition from 2010 to 2019 cannot be placed in a session, so ingest fails it on `session_boundary`.

Two things are needed, and both come from dated CME documents:

- **Dated regular hours.** The Globex daily close moved during the period, so a calendar's regular hours become a dated table instead of one fixed pair.
- **Pre-2020 exceptions.** Every holiday closure, early close and unscheduled closure from 2010-06-06 to 2019-12-31 is one sourced row.

Nothing is bought, no tier changes, and 2020 onward is untouched. The slice is pure calendar data and the small code change that lets sessions follow dated hours.

This slice was split out of the `ohlcv-1m` design (302). That design found the gap, and the PM decided the calendar is its own unit of work: it is the riskiest part, it rests on archived documents, and it is needed by every schema read from 2010 to 2019, not just bars.

## Value

- **Architectural enablement.** 302 (`ohlcv-1m` and definition history) places bars in sessions. 303 (statistics) assigns a record to a session from its reference time and trading date. 304 and 307 buy and ingest the 2010 to 2019 range. None can start on that range without this slice.
- **Later slices read the same facts.** The 1d bar is the CME session, intraday roll-ups are clipped to the session open (306), and the tick-bar count restarts at each session open. All three read session open and close per calendar and date. This slice keeps them queryable and correct for the whole dataset.
- **A wrong day cannot hide.** A mis-seeded day fails ingest loudly with `session_boundary`, naming the sessions either side. It is never stored in the wrong session.
- **No spend and no behaviour change today.** Sessions from 2020-01-01 are byte-identical before and after.

## Technical Scope

**Included**

- `CME_DATASET_START = 2010-06-06`, defined once.
- A primary-track migration that creates `trading_calendar_hours`, inserts the dated hours eras, inserts the pre-2020 holiday and early-close rows, and extends sessions from `CME_DATASET_START` to the day before each calendar's current seed start (D1 to D3).
- `populate_trading_sessions` (and so `extend_calendar_sessions`) uses the hours era in force on each session's date, for calendars that have eras.
- Sources: a dated CME document cited for every era and every exception row (D2).
- A check that 2020 onward is unchanged, and that each calendar's latest era equals its `trading_calendars` row.
- `scripts/verify_cme_sessions.py` runs unchanged against held jobs, and is extended to accept dates before 2020.

**Excluded**

- Any schema, table or projector for bars, statistics or definitions (302, 303).
- Any purchase (304, 307).
- Calendars other than `CME_EQUITY` and `CME_METALS` (NYSE and NASDAQ keep `trading_calendars` as today).
- Hours or holidays before 2010-06-06.
- Anything generated from a holiday rule. Every row is entered from a document (221 D5).

## Dependencies

### Prerequisites

- **221 and 232 session model:** `trading_calendars`, `trading_holidays`, the materialised sessions table, `populate_trading_sessions`, `extend_calendar_sessions` and `SessionIndex`. This slice extends them backwards and adds no new concept except dated hours.
- **Sources:** CME holiday schedules, trading-hours notices and contract-specification pages for 2010 to 2019, read through the Wayback Machine as 221 did. A forum report that the ES close was 16:15 CT until about September 2015 is a lead, not a source (D1).

### Interfaces Required

- `populate_trading_sessions` and `extend_calendar_sessions` (`data/base/session_population.py`, `session_extension.py`), changed here to read eras.
- `SessionIndex`, `locate_sessions` and the `session_boundary` check, unchanged. They read the sessions table, so they follow the new data.
- The existing CME seed modules (`market/schema/seed_cme_calendar.py`, `seed_cme_metals_calendar.py`), which hold the 2020 onward rows.

## Architecture

### Component Structure

```
market/schema/seed_cme_calendar.py          CME_DATASET_START; pre-2020 exceptions for CME_EQUITY
market/schema/seed_cme_metals_calendar.py   pre-2020 exceptions for CME_METALS
market/schema/seed_cme_hours.py             dated hours eras per calendar, each with its source
data/base/session_population.py             a calendar with eras uses the era in force on the date
data/base/session_extension.py              extends over the pre-2020 range
market/schema/migrations/                   one primary-track migration (D3)
scripts/verify_cme_sessions.py              accepts pre-2020 dates
```

### Data Flow

The migration runs once on a primary database:

1. Create `trading_calendar_hours`.
2. Insert the eras for `CME_EQUITY` and `CME_METALS`.
3. Insert the pre-2020 rows into `trading_holidays` (`ON CONFLICT DO NOTHING`).
4. Call `extend_calendar_sessions` for each calendar from `CME_DATASET_START` to its seed start minus one day.

From then on, `populate_trading_sessions` reads `trading_calendar_hours` for these two calendars and uses the era in force on each date. A bar arriving at ingest is placed by `locate_sessions` against the stored sessions, exactly as today.

### State Management

- **New table (primary database):** `trading_calendar_hours (calendar_id, effective_from, market_open, market_close, source)`.
- **Changed tables:** `trading_holidays` gains rows from 2010-06-06 to 2019-12-31 for the two CME calendars. The sessions table gains their sessions over the same range.
- **Unchanged:** sessions from 2020-01-01, every other calendar, and every tick-track table.

## Technical Decisions

### D1 — Regular hours become dated
Status: decided

A new table, `trading_calendar_hours (calendar_id, effective_from, market_open, market_close, source)`, with primary key `(calendar_id, effective_from)`. `populate_trading_sessions` uses the era in force on each session's date when a calendar has eras. A calendar with no eras (NYSE, NASDAQ) uses `trading_calendars` as today.

The latest era of each CME calendar must equal its `trading_calendars` row, and a unit test enforces this, so the two cannot disagree.

This is needed because the Globex close moved. An unofficial forum report puts the CME equity daily maintenance moving to start at 16:00 CT on a Monday, 21 September (consistent with 2015). Before that, ES closed at 16:15 CT. A bar at 16:05 CT in 2014 would otherwise fail `session_boundary`. The forum report is a lead, not a source. Eras are entered only from dated CME documents (notices, contract specification pages on Wayback), one `source` per era, for ES and GC independently. If no dated document can be found for a change, the slice stops at that era and says so, instead of entering a guess.

- *Rejected: era hours as exception rows.* An exception models a closure or an early close. A later regular close is neither, and stretching the vocabulary would make every pre-2015 weekday an "exception".
- *Rejected: populating pre-2020 sessions in a CME-only routine with no table.* `mt data extend` and the session repair paths rebuild from the tables. Hours known only to one routine would be overwritten the first time either ran.

### D2 — Pre-2020 exceptions are sourced rows
Status: decided

Every holiday closure and early close from 2010-06-06 to 2019-12-31, one row per day, each citing the CME holiday schedule it was read from, in the existing seed modules. Unscheduled closures are included where CME documents them, for example the 2012-10-29 and 10-30 Hurricane Sandy closure and the 2018-12-05 national day of mourning. Nothing is generated from a holiday rule (221 D5). ES and GC are checked independently, because their holiday schedules can differ.

### D3 — One migration, and the 2020 seed is not touched
Status: decided

`CME_EQUITY_SEED_START` and `CME_METALS_SEED_START` stay at 2020-01-01. The existing minute-track migration seeds from them, and moving them would change what that migration produces on a fresh database. Instead, `CME_DATASET_START = 2010-06-06` is a new constant, and the new primary-track migration fills `[CME_DATASET_START, SEED_START)` for each calendar.

The migration checks that sessions from 2020-01-01 on are identical before and after, by comparing them, and aborts if not. Migration chain pins that name the last id are updated, as every primary migration does. It is applied by `mt data migrate apply`.

### D4 — Verification: loud failure and a held-file check
Status: decided

A mis-seeded day cannot pass silently. Ingest fails any bar outside a session with `session_boundary`, naming the sessions either side. `scripts/verify_cme_sessions.py` runs any held job through the calendar and now accepts dates before 2020. At 304 and 307 every bought day goes through the same check. A fix for a wrong day is a sourced seed row, then `mt data tick reset` of the failed units.

## Implementation Details

### Database / Storage Schema

Primary-track migration: `CREATE TABLE trading_calendar_hours` with the primary key above; insert the eras; insert the 2010-06-06 to 2019-12-31 exceptions (`ON CONFLICT DO NOTHING`); extend sessions over that range. It touches only `CME_EQUITY` and `CME_METALS`. The migration is applied in production with `mt data migrate apply`. No root step is needed.

### Sources

For each calendar, a short list in the seed module: the document, its date and its URL (Wayback where the original is gone). An era or exception row without a source fails a unit test. The source list is part of the deliverable and is read in review.

## Integration Points

### Provides to Other Slices

- **302:** sessions from 2010-06-06, so a 2010 to 2019 bar is placed. 302's `HistoryWant` refuses a start before `CME_DATASET_START`, so this constant is the single definition of the dataset start.
- **303:** the same sessions, for placing a statistics record by reference time and trading date.
- **304 and 307:** the check that a bought pre-2020 day is placed (`verify_cme_sessions.py`).
- **306:** session open and close per calendar and date, queryable, for the 1d bar and for clipping roll-ups to the session open. The tick-bar count also restarts at each session open, and reads the same facts.

### Consumes from Other Slices

- **221 and 232:** the session model this slice extends backwards.

## Success Criteria

### Functional Requirements

1. `CME_EQUITY` and `CME_METALS` have sessions from 2010-06-06 (first session on or after that date) through today, with no gaps other than weekends and sourced exceptions.
2. Sessions from 2020-01-01 on are byte-identical to the sessions before the migration.
3. Every era and every pre-2020 exception row names a source document and its date.
4. The latest hours era of each CME calendar equals its `trading_calendars` row.
5. A session on a date inside an earlier era carries that era's open and close. A date after the era change carries the later pair.
6. A bar in a pre-2020 daily break fails `session_boundary`. A bar inside the era's regular hours is placed.
7. `scripts/verify_cme_sessions.py` accepts a pre-2020 date and exits 0 for a placed day and non-zero for an unplaced one.
8. NYSE and NASDAQ sessions are unchanged.

### Technical Requirements

- Unit tests:
  - era lookup by date (before, on and after a change);
  - latest era equals `trading_calendars`;
  - every era and exception row cites a source;
  - `CME_DATASET_START` is the start of the sessions the migration creates.
- Integration tests:
  - the migration on an empty and on a populated primary database;
  - 2020-and-later sessions identical before and after;
  - a bar placed or refused by `locate_sessions` on both sides of an era change;
  - `verify_cme_sessions.py` on a pre-2020 date.
- `ruff`, `ruff format` scoped to touched files, and `mypy` clean. Source files stay within about 300 lines. The pre-2020 rows live in their own module if the seed modules would pass that size.

### Integration Requirements

- 302 and 303 need no calendar change to place 2010 to 2019 data.
- Production behaviour for 2020 onward is unchanged after the migration is applied.

### Verification Walkthrough

Run from the checkout root, with the test database URL exported (project test setup).

1. **Tests.**
   ```bash
   uv run pytest test/unit/market test/unit/data/base -q
   uv run pytest test/integration/data -q -k "calendar or session"
   ```
   Expected: all pass, including the new era, source and migration tests.

2. **Sessions on a migrated test primary database.**
   ```sql
   SELECT calendar_id, min(session_date), count(*) FROM trading_sessions
    WHERE calendar_id IN ('CME_EQUITY', 'CME_METALS') GROUP BY 1;
   ```
   Expected: both start on or before 2010-06-07 (the first session after the dataset start).

3. **A held day still places.**
   ```bash
   uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY /data/market-data/databento/GLBX-20240930-USM7UXXJBA
   ```
   Expected: exits 0, as before this slice.

4. **2020 onward unchanged.** The integration test `test_cme_history_2020_unchanged` compares the sessions before and after the migration. Expected: pass.

5. **Production, after merge.** Apply the migration with `mt data migrate apply`, then run `sudo mt-run data health`. Expected: the existing lines are unchanged, and the query in step 2 on the production primary database shows the new start.

## Risk Assessment

### Technical Risks

- **Source accuracy.** Ten years of holidays, unscheduled closures and hours eras for two calendars, read from archived documents. A missed day only shows when bars for it are ingested (304, 307).
- **The ES close history.** One lead is a forum post. If CME's own documents do not date a change, the era is not entered, and the slice records which date range is unsupported.

### Mitigation Strategies

- A mis-seeded day fails ingest loudly, naming the neighbouring sessions, and is never stored in a wrong session (D4). The "2020 onward unchanged" test bounds the change to the new range.
- The source list is part of the deliverable, so a reviewer can check any row.

## Implementation Notes

### Development Approach

Relative effort 3/5. Suggested order, committing at each step:

1. Sources first: find and record the dated documents for hours changes and for each year's holidays, for ES and GC.
2. `trading_calendar_hours`, the era lookup in `populate_trading_sessions`, and the unit tests.
3. The pre-2020 exceptions in the seed modules, with their source tests.
4. The migration, the "2020 onward unchanged" test and the `verify_cme_sessions.py` change.

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit.
- The existing minute-track migration keeps seeding from the unchanged `*_SEED_START` constants.
- If the sources for an era cannot be found, stop and say so rather than entering a plausible value.
