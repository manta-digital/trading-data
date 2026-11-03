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
status: complete
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
- Session extension uses the era in force on each session's date, by splitting a requested range at era boundaries and populating each segment with that era's hours. A dated calendar asked for a date before its first era raises an error naming the calendar and the date (D3).
- Pre-2020 exception rows for both calendars, each citing a dated CME document, in new seed modules (D4).
- One primary-track migration that checks its preconditions, creates the table, inserts eras and exceptions with a plain `INSERT`, fills the pre-2020 sessions and proves that 2020 onward is unchanged (D6).
- Migrations 058 and 059 are not edited and keep producing exactly what they produce today on a fresh database, pinned by a regression test (D7).
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

- `session_population.populate_trading_sessions` and `session_interval`, unchanged. They keep taking one fixed open and close.
- `session_extension.extend_calendar_sessions` and `_read_calendar`, changed here to read eras for dated calendars and populate one segment per era (D3). Its callers (migrations 058 and 059, `mt data extend`, `auto_extend`) are not edited.
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
data/base/session_extension.py               for a dated calendar, reads the eras, splits the range
                                             at era boundaries and populates each segment with the
                                             era's hours; NoHoursEraError
market/schema/migrations/minute.py           next free id: the calendar-history migration
                                             (058/059 unchanged)
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

**After the migration:** every extension call (forward extension by `mt data extend` and `auto_extend`, a repair) reads the calendar's eras from `trading_calendar_hours` and populates each era's segment of the range with that era's hours. Forward extension always lands in the latest era, so it writes what it writes today. Readers (`TradingCalendar.sessions_between`, `get_trading_hours`, `SessionIndex`, `locate_sessions`) read `trading_sessions` and are unchanged. They see sessions from 2010-06-07 on, the first session, which opens on the evening of Sunday 2010-06-06.

### State Management

- **New table (primary database):** `trading_calendar_hours`, with one row per calendar and era.
- **Changed tables:** `trading_holidays` and `trading_sessions` gain `CME_EQUITY` and `CME_METALS` rows dated 2010-06-06 to 2019-12-31.
- **Changed column:** `trading_calendars.hours_dated boolean NOT NULL DEFAULT false`, true for `CME_EQUITY` and `CME_METALS` (D3).
- **Unchanged:** every row dated 2020-01-01 or later, every NYSE and NASDAQ row, the `trading_calendars` hours, and the tick database.
- **Privileges:** the table is created by the migrator role, so the default privileges from `provision_roles.sql` give the application role read access. No grant change is needed.

## Technical Decisions

### D1 — Regular hours become dated eras
Status: decided (Sunday-open column added after sourcing, see Era sources)

Sourcing found an era whose Sunday open differs from its daily reopen: before 2012-11-19 the ES trading day began at 15:30 CT Monday to Thursday but at 17:00 CT on Sunday (SER-6465). So the table carries `sunday_open`, as this decision provided for.

A new table:

```sql
CREATE TABLE trading_calendar_hours (
    calendar_id     text NOT NULL REFERENCES trading_calendars (calendar_id),
    effective_from  date NOT NULL,   -- first session date (the date it closes) in the era
    market_open     time NOT NULL,   -- previous day when after close
    market_close    time NOT NULL,
    sunday_open     time NOT NULL,   -- open of a session whose open falls on a Sunday
    source          text NOT NULL,
    PRIMARY KEY (calendar_id, effective_from)
);
```

An era is in force from its `effective_from` until the next era's. Eras are keyed by session date, the date a session closes, which matches how `trading_sessions` dates sessions. When CME announces a change "effective Sunday X for trade date X+1", `effective_from` is X+1.

An era carries a daily open and a Sunday open. `sunday_open` is used for a session whose open falls on a Sunday (for an open-after-close era, the Monday session); every other session uses `market_open`. It is always stated, equal to `market_open` where the documents show no difference, so there is no null meaning "same as daily". An exception row's `late_open_time` still overrides both, as today. Extension applies `sunday_open` by passing it as the late open of each such session to `populate_trading_sessions`, which stays unchanged (D3).

- *Rejected: era hours as exception rows.* An exception models one day's closure, early close or late open. A different regular close for five years is none of those, and entering it that way would make every pre-2015 weekday an exception.
- *Rejected: hours known only to a CME routine, with no table.* `mt data extend` and the repair paths rebuild from the tables, so hours held outside them would be overwritten by the first rebuild.
- *Rejected: a Friday close distinct from the daily close.* No document read shows one.
- *Rejected: every pre-2012-11-19 Monday as a late-open exception row.* That is the era-as-exception shape rejected above, about 125 rows per calendar standing in for one fact.

### D2 — Which eras are entered
Status: decided

- Eras are searched for and entered only for 2010-06-06 to 2019-12-31, plus one current era per calendar that carries today's hours and is checked equal to its `trading_calendars` row (migration step 4 and a unit test). The current era starts at the first session after the last pre-2020 change, or at `CME_DATASET_START` if there was none.
- A documented regular-hours change on or after 2020-01-01 is not entered, because 2020 onward stays untouched. It is recorded as a finding in the slice's implementation notes for the PM.
- An era is entered only from a dated CME document: a notice, a Special Executive Report, or a contract-specification page on Wayback whose snapshot date bounds the change. The report that the ES close moved from 16:15 CT to 16:00 CT around September 2015 is a forum lead, not a source. ES and GC are sourced independently.
- If no dated document can be found for an era, the fill stops there (D5) and the slice says so. A plausible value is never entered.

### D3 — Extension splits the range at era boundaries; no era is an error
Status: decided

`populate_trading_sessions` and `session_interval` are unchanged: they take one fixed open and close, and `session_interval` stays the one place the open-after-close rule lives. Sessions are keyed by the date they close, and eras are keyed the same way (D1), so a range split at an era's `effective_from` has no session spanning two segments. `extend_calendar_sessions` does the splitting: for a dated calendar it reads the eras, cuts `[start, effective_end]` at each `effective_from`, and calls `populate_trading_sessions` once per segment with a calendar row carrying that era's open and close. Where an era's `sunday_open` differs from its `market_open`, each session in the segment whose open falls on a Sunday is given `sunday_open` as its late open, unless its exception row already carries one (D1).

- **Dated calendars.** A calendar is dated when `trading_calendars.hours_dated` is true, which the migration sets for `CME_EQUITY` and `CME_METALS`. How dated is read matters because migrations 058 and 059 call `extend_calendar_sessions(conn, calendar_id, start=..., end=...)` directly (`minute.py`, `_seed_cme_equity_calendar` and `_seed_cme_metals_calendar`), and on a fresh database they run before `hours_dated` exists. Adding the column to `_read_calendar`'s existing `SELECT` would fail there with an undefined-column error. So a small helper, `_is_dated(conn, calendar_id)`, first asks `information_schema.columns` whether `trading_calendars.hours_dated` exists (the same introspection `minute.py` already uses, for example in the migration near line 2408). If the column is absent it returns false without reading it. If present, it returns the row's flag. A database without the column cannot have eras, so "not dated" is a schema-version fact, not a fallback, and extension behaves exactly as today. In the new migration, step 3 adds the column and sets the flag inside the same transaction as step 5, so step 5 sees them and takes the dated path. For a dated calendar the eras come from `trading_calendar_hours` only. If it has no era rows, extension raises. A session date before the first era raises `NoHoursEraError` naming the calendar and the date. There is never a fallback to the `trading_calendars` hours.
- **Every other calendar** is not dated and takes the existing single-segment path. NYSE and NASDAQ output is unchanged, and a unit test compares it to the current output.
- **No caller changes.** `mt data extend`, `auto_extend`, migrations 026, 058 and 059 and `TradingCalendar._build_trading_hours` keep calling what they call today.

Whether a calendar is dated is stated explicitly, not inferred from whether era rows exist. If rows went missing, inferring would silently revert the pre-2020 sessions to today's hours. `CME_DATASET_START` stays in `seed_cme_hours.py`. Its consumer, 302, is tick code, which already imports the CME seed modules (`data/tick/constants.py`).

- *Rejected: an hours schedule object passed into `populate_trading_sessions`.* It changes a shared signature and every caller, including the NYSE and NASDAQ paths this slice must leave unchanged, for a need that only dated calendars have (slice review).

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

### D7 — Migrations 058 and 059 are unchanged and pinned by a test
Status: decided

On a fresh database, 058 and 059 run before `trading_calendars.hours_dated` and `trading_calendar_hours` exist, so extension takes the undated path (D3) and writes exactly what it writes today. They need no edit. A regression test pins it: `test_058_059_fresh_output_unchanged` (in `test_migration_057_058_cme_calendar.py`) applies the chain to 059 on a fresh database and compares every CME session and holiday row with a snapshot captured before this slice's code change. The new migration's step 5 goes through the dated path, and its integration tests cover it. Design-time check, done by reading `minute.py`: 058 and 059 reach the sessions only through `extend_calendar_sessions`, so the undated path in D3 is the one they take, and nothing else in them needs changing.

On a live database where the new code is installed but the migration is not yet applied, extension of a CME calendar takes the undated path and produces what it produces today, which is correct for forward extension because the latest era equals those hours.

### D8 — A free count check against provider data
Status: decided

A throwaway verification run, kept in `scripts/` only as the record of how the evidence was gathered (like `verify_cme_sessions.py`), and deliberately not polished. No later slice uses it: 305 builds its own completeness verdicts from the manifest and ledger. `scripts/check_cme_history_counts.py --calendar CME_EQUITY --parent ES.FUT [--start] [--end]` reads the calendar's sessions in the range and makes two metadata calls per session through the adapter's `get_record_count` on `GLBX.MDP3`, `ohlcv-1m`, `stype_in=parent`:

- **Inside the session** `[open, close)`: expected greater than 0. A zero is a finding: either an undocumented closure or a provider data gap.
- **In the gap to the next session** `[close, next_open)`: expected 0. A non-zero count is a finding: either a wrong boundary (an hours era, an early close, a missed late open) or a provider anomaly. Every bar in such a gap would fail `session_boundary` at ingest.

`ohlcv-1m` stamps a bar at its minute's open, so a bar inside the session never starts at or after the close.

Volume: about 2,400 sessions per calendar from 2010-06-07 to 2019-12-31, so about 4,800 calls per calendar and 9,600 in total. Calls are made one at a time, with no concurrency and no pacing logic, which is enough for a one-off run. A run can be split by year with `--start` and `--end`. The script prints one line per finding with its window and count, and exits non-zero if there was any finding or failed call.

Failure handling: each call has a timeout (the SDK's metadata timeout, confirmed from its source and set explicitly). A call that raises is printed as a failed session with its error and is never counted as zero. An authentication or bad-request error stops the run. There is no retry and no resume state: `--start` and `--end` rerun only the failed or remaining range. Metadata calls are free (the architecture's cost table was measured the same way), so the check buys nothing. Counts are evidence, never a source. A finding is resolved by a sourced row (D4, D2) or recorded as unresolved in the implementation notes, naming the date, the window and the count.

Window semantics (end exclusive, nanosecond precision) are confirmed against the databento-python SDK source, not the client-rendered docs site.

- *Rejected: wait for 304 and 307 to find wrong days at ingest.* That finds them after money is spent, one bought day at a time.
- *Rejected: generate exceptions from zero-count days.* That would be a holiday rule derived from data, and it cannot tell a closure from a provider gap.

### D9 — Production rollout is a cutover script
Status: decided

`upgrade_production.py` cannot carry this release: it is for code-only releases and stops when an install brings a new migration. The project's convention is a cutover script per release that has a migration (922, 0244). So the release that ships 301 gets one, assembled from the `cutover_common` and `upgrade_production` helpers with no new mechanism. If the release carries other migrations, the same script applies them, as 0244 did. `scripts/cutover_301_calendar_history.py`, in the pattern of `cutover_0244_symbol_names.py` and built on `cutover_common`, runs these steps: preflight, hold timers, install the release, migrate the primary track to the new id with the target verified, verify, release timers. As in 0244, timers are released as found after a failed verify, and stay held after a failed migrate, so no extension runs against a half-migrated database. The narration says so, and a rerun after the fix releases them. Verify means: both CME calendars' first session is dated 2010-06-07 (or the moved `CME_DATASET_START`), `sudo mt-run data health` passes, and `mt data overview` exits 0. Claude runs it after merge. No root step and no PM host step is needed.

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
5. A session inside an earlier era carries that era's open and close, including across an era change inside one extension call. For a dated calendar, a date before the first era raises `NoHoursEraError` naming the calendar and the date.
6. `locate_sessions` places an instant inside a pre-2020 era's hours and refuses one in that era's daily break with `session_boundary`, on both sides of each era change.
7. NYSE and NASDAQ sessions are unchanged. Migrations 058 and 059 produce today's rows on a fresh database.
8. `check_cme_history_counts.py` has been run over the full pre-2020 range for both calendars. Every finding is resolved by a sourced row or recorded as unresolved, with date, window and count.

### Technical Requirements

- Unit tests:
  - era segmentation in `extend_calendar_sessions`: a range before the first era, a range starting on an era's first date, a range across a change, and a late-open exception overriding the era's open;
  - output for NYSE, NASDAQ and the current CME hours equal to today's;
  - sources present, exception dates inside the range, `CME_DATASET_START` not after any calendar's first era, latest era equal to the calendar row;
  - a dated calendar with no eras raises;
  - `_is_dated` is false on a database without the `hours_dated` column, without error, and true for `CME_EQUITY` and `CME_METALS` after the migration, and false for NYSE and NASDAQ after it;
  - pre-2020 era-boundary instants, in the pattern of `test_cme_equity_boundaries.py` and its JSON fixture;
- Integration tests:
  - the migration on a fresh database (the full chain, 058 and 059 before it) and on a database populated as production is;
  - each precondition and the 2020-onward comparison abort with a clear message;
  - `extend_calendar_sessions` forward-extends a CME calendar exactly as before.
- `ruff check`, `ruff format` on touched files only, and `mypy` clean. Code modules stay within about 300 lines.

### Integration Requirements

- 302 and 303 place 2010 to 2019 data with no calendar change.
- After the cutover, production's 2020-onward behaviour is unchanged, and `mt data extend` and `auto_extend` extend both CME calendars as before.

### Verification Walkthrough

Run from the checkout root, with the test database URL exported from `.env` (`set -a; eval "$(grep -E '^MT_TIMESCALE_TEST_URL=' .env | tr -d '"')"; set +a`). Verified 2026-11-01.

1. **Tests.**
   ```bash
   uv run pytest test/unit/data/base test/unit/test_seed_cme_calendar.py \
       test/unit/test_seed_cme_metals_calendar.py test/unit/test_seed_cme_hours.py \
       test/unit/test_seed_cme_history.py test/unit/test_schema_migrations.py \
       test/unit/test_cutover_301.py -q
   uv run pytest test/integration -q -k "calendar or session or migration"
   ```
   Expected: unit `326 passed`. Integration `278 passed, 52 skipped` with exactly two failures, both pre-existing on main and unrelated: `test_migration_051_052.py::TestChainAppliesOnColdStart::test_chain_ends_at_052` and `::TestCoverageFreshnessOnRealHistory::test_pre_167_cagg_keeps_the_generic_budget`. The 066 tests are in `test/integration/test_migration_066_cme_calendar_history.py` (19 tests).

2. **Free count check** (needs the Databento key; costs nothing). Point it at a migrated throwaway database, since production has no pre-2020 sessions until the cutover. A full run takes hours; split by year and run the ranges side by side:
   ```bash
   MT_TIMESCALE_DB_URL=<migrated throwaway db> uv run python scripts/check_cme_history_counts.py \
       --calendar CME_EQUITY --parent ES.FUT --start 2010-06-07 --end 2011-12-31
   # ... and the other ranges, and CME_METALS with GC.FUT
   ```
   Expected: a non-zero exit, with every finding matching a row of `user/notes/301-count-check-findings.md` (215 equity, 47 metals) and its group in Findings. Databento's metadata endpoint returns occasional HTTP 500/504 or read timeouts; those print as `FAILED` and are rerun with `--start`/`--end`. Calibration on 2024 against production (read-only): `259 sessions, 0 findings` for each calendar.

3. **Sessions on a migrated test database.**
   ```sql
   SELECT calendar_id, min(session_date), count(*) FROM trading_sessions
    WHERE calendar_id IN ('CME_EQUITY', 'CME_METALS') GROUP BY 1;
   SELECT * FROM trading_calendar_hours ORDER BY calendar_id, effective_from;
   ```
   Expected (full chain, holiday bound 2027-12-31): `CME_EQUITY 2010-06-07 4540` and `CME_METALS 2010-06-07 4535`. Eras: equity 2010-06-06 15:30/15:15 Sunday 17:00, 2012-11-19 17:00/16:15, 2015-09-21 17:00/16:00; metals 2010-06-06 17:00/16:15, 2015-09-21 17:00/16:00; every row sourced.

4. **A held 2024 job still places.**
   ```bash
   uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY \
       /data/market-data/databento/GLBX-20240930-USM7UXXJBA
   ```
   Expected: exit 0, output identical to the same command on main (checked by running both and diffing).

5. **Production** (after merge).
   ```bash
   uv run python scripts/cutover_301_calendar_history.py <release-tag>
   ```
   Expected: exit 0. The narration in `project-documents/user/notes/` shows the migration applied, both CME calendars' first session dated 2010-06-07, `mt data health` and `mt data overview` passing, and the timers released. `--help` prints the steps.

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
2. The dated-calendar reader and era segmentation in `session_extension.py` (D3), with `test_058_059_fresh_output_unchanged` (D7) and unit tests that prove NYSE, NASDAQ and current CME output are unchanged.
3. `check_cme_history_counts.py` (D8). Running it against the current 2020-onward sessions first is a free calibration: it should report no findings there, since those days were already checked against trade counts in 221 and 232.
4. Each calendar's pre-2020 exceptions (D4), with the source tests.
5. The migration (D6) and its integration tests. Then run the count check over the pre-2020 range on a migrated test database and resolve or record every finding.
6. The cutover script (D9).

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit.
- Findings to record for the PM, in a section of this document added at implementation: any documented hours change from 2020 on (D2), any unsourced era and the resulting `CME_DATASET_START` (D5), and every unresolved count finding (D8).
- If the sources for an era or a day cannot be found, stop and say so rather than entering a plausible value.

## Era sources

Read 2026-11-01. All times America/Chicago. Each era is in force from its `effective_from` (a session date, the date the session closes) until the next era's.

### CME_EQUITY (ES)

| effective_from | market_open | market_close | sunday_open | Source |
|---|---|---|---|---|
| 2010-06-06 | 15:30 | 15:15 | 17:00 | ES contract specifications, "MON-THURS: 5:00 p.m.-3:15 p.m. & 3:30 p.m.-4:30 p.m. (Daily maintenance shutdown 4:30 p.m.-5:00 p.m.) SUN: 5:00 p.m.-3:15 p.m.", Central Time, identical in Wayback snapshots 2010-04-16, 2011-07-08, 2011-11-09, 2012-06-16, 2012-10-06: https://web.archive.org/web/20100416105946/http://www.cmegroup.com/trading/equity-index/us-index/e-mini-sandp500_contract_specifications.html . SER-6465 confirms the trading day started at 3:30 p.m. CT until 2012-11-18 ("Moving the start of the ... trading day from 3:30 p.m. to 5:00 p.m."). The 16:30-17:00 maintenance shutdown lies inside the session (15:30 previous day to 15:15) and is not modelled, like the later 15:15-15:30 halt. |
| 2012-11-19 | 17:00 | 16:15 | 17:00 | CME Special Executive Report S-6465, 2012-11-14, "New Trading Hours: Equity Index Futures & Options on Equity Index Futures on Monday, November 19, 2012": trading begins Sunday 5:00 p.m. CT, continues to 4:15 p.m. CT, halt 3:15-3:30 p.m., closed 4:15-5:00 p.m., re-opens 5:00 p.m. Monday-Thursday for the new trade date, Friday session to 4:15 p.m. https://web.archive.org/web/20130412064716/http://www.cmegroup.com/rulebook/files/ser-6465_Equity_Index_Futures_Options_on_Equity_Index_Futures_20121114.pdf |
| 2015-09-21 | 17:00 | 16:00 | 17:00 | CME Globex Notice 2015-09-07, "Changes to Daily Maintenance Period for CME Globex": "Effective Monday, September 21 ... the closing times for the following markets will now occur 15 minutes earlier Monday through Friday at 16:00 CT. CME Equity, CBOT Equity, COMEX, NYMEX, DME. All products will continue to open at their normally scheduled times." https://web.archive.org/web/20150912161604/http://www.cmegroup.com/tools-information/lookups/advisories/electronic-trading/20150907.html . Bounded by ES contract specifications at 16:15 (Wayback 2015-09-06) and 16:00 (Wayback 2015-10-08). This is the current era, equal to the `trading_calendars` row. |

### CME_METALS (GC)

| effective_from | market_open | market_close | sunday_open | Source |
|---|---|---|---|---|
| 2010-06-06 | 17:00 | 16:15 | 17:00 | Gold futures contract specifications, "CME Globex: Sunday – Friday 6:00 p.m. – 5:15 p.m. (5:00 p.m. – 4:15 p.m. Chicago Time/CT) with a 45-minute break each day beginning at 5:15 p.m. (4:15 p.m. CT)", identical in Wayback snapshots 2010-04-02, 2011-07-28, 2012-10-10, 2013-01-16, 2014-09-24, 2015-09-06: https://web.archive.org/web/20100402122213/http://www.cmegroup.com/trading/metals/precious/gold_contract_specifications.html . Sunday open equals the daily open. |
| 2015-09-21 | 17:00 | 16:00 | 17:00 | CME Globex Notice 2015-09-07 (COMEX listed), as for ES above. Bounded by gold contract specifications at 16:15 (Wayback 2015-09-06) and 16:00 with a 60-minute break (Wayback 2015-10-09). This is the current era, equal to the `trading_calendars` row. |

The forum lead (ES close 16:15 to 16:00 around September 2015) is now dated by the 2015-09-07 notice. It also moved GC, which the lead did not mention.

## Findings

- **Hours changes from 2020 on (D2):** none found. The 2021 removal of the 15:15-15:30 CT equity halt (SER-8776) is inside a session.
- **Unsourced eras (D5):** none. `CME_DATASET_START` stays 2010-06-06.
- **Documents that disagree (D4):**
  - GC 2011-05-27 (Friday before Memorial Day): `2011-memorial-day-done.pdf` gives a regular 16:15 close; `Book1.xls` in the same 2011 zip gives an early 15:15 close. Free `ohlcv-1m` count for [15:15, 16:15) CT that day: 145 bars, so the PDF is right and no row is entered.
  - ES MLK 2013: `2013-martin-luther-king-done.docx` (2012 zip) gives 15:15 regular closes, written before the 2012-11-19 hours change; the 2013 zip's PDF and XLS give 16:15. The 2013 documents are used (no extra row).
- **Documents missing (D4):** no Globex trading-hours document for Columbus and Veterans Day 2018 and 2019, only settlement notices saying "All products will settle at their normal times". No rows (regular days); the count check confirms. The 2014 Presidents Day and Good Friday PDFs are corrupt; the matching XLS files were used.
- **Unscheduled closures (D4):** Hurricane Sandy ES rows 2012-10-29 (close 08:15), 2012-10-30 (open 17:00, close 08:15), 2012-10-31 (open 17:00) cite CME's 2012-10-28 media alert, clearing advisory 12-464 and the 2012-10-30 press release; metals traded normally (12-464, "All other CME Group futures ... remain open"). 2018-12-05: ES closes 08:30 (SER-8289); metals normal hours (same SER).
- **Count check findings (D8).** Calibration on 2024 (both calendars, 259 sessions each): no findings. The pre-2020 run covered 2010-06-07 to 2019-12-31 on a migrated throwaway database: 2,472 `CME_EQUITY` and 2,470 `CME_METALS` sessions, two calls each. 56 calls failed (HTTP 500/504, read timeouts) and each was rerun until it returned a count. Every finding, with date, window and count, is listed in `user/notes/301-count-check-findings.md` (215 equity, 47 metals). No finding was resolved by a new document, so no row was added; they fall into four groups:
  - **Bars at the close minute (234 findings, 200 equity, 34 metals).** One to three `ohlcv-1m` bars stamped exactly at the session close, in every era (15:15 and 16:15 equity closes, 16:15 and 16:00 metals and equity closes, holiday early closes). For each one the rest of the gap, from close plus one minute to the next open, holds no bar, except the two below. `SessionIndex.locate_ns` treats a session as a closed interval (`ts <= close`), so these bars place in their session and do not fail `session_boundary`. The trades inside such a minute may fall after the close: 304 checks this on the first bought trade-level days. Not a boundary error, so no era or exception changes.
  - **Bars before the Sunday open on 2018-08-05 (unresolved).** `CME_EQUITY` 1 bar and `CME_METALS` 3 bars in [16:01, 17:00) CT Sunday 2018-08-05, before the 17:00 open. No document explains them; they would fail `session_boundary` for the 2018-08-06 session at ingest. Provider anomaly, recorded for 304/307.
  - **Holiday sessions with no bars (provider gaps, unresolved).** Both calendars: 2012-11-22, 2013-01-21, 2013-02-18, 2013-05-27, 2013-07-04, 2013-09-02, 2013-11-28, 2014-01-20, 2014-02-17. Each holiday schedule shows a Sunday or evening open and a midday halt, and Databento marks every one of these dates `available`, so this is data missing at the provider, not a closure. A known input to 305's completeness verdicts.
  - **Weekdays with no bars (provider gaps, unresolved).** `CME_EQUITY` 2014-06-12, 2014-06-13; both calendars 2014-09-23, 2014-09-24, 2014-09-25, 2014-12-31. Databento marks 2014-06-11..13 and 2014-09-22..25 `degraded`; 2014-12-31 is marked `available`. CME documents show regular trading. A known input to 305.
- **Final `CME_DATASET_START`:** 2010-06-06, unchanged.

