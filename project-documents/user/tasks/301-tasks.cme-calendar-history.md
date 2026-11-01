---
docType: tasks
slice: cme-calendar-history
project: trading-data
lld: user/slices/301-slice.cme-calendar-history.md
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies: []
interfaces:
- 302
- 303
- 304
- 305
- 306
- 307
projectState: 'Slice design committed and revised after slice review (67b477c is the
  latest). CME_EQUITY and CME_METALS hold sessions from 2020-01-01 only, seeded by
  minute-track migrations 058 and 059 from CME_EQUITY_SEED_START and CME_METALS_SEED_START.
  The minute chain ends at 064. Regular hours are one fixed pair per calendar in trading_calendars
  (17:00 -> 16:00 America/Chicago).

  '
dateCreated: 20261101
dateUpdated: 20261101
status: not_started
turns: 34
promptTokens: 1415242
cachedTokens: 1318819
cacheWriteTokens: 96385
completionTokens: 23566
durationSeconds: 171.3
costUsd: 0.753438
usageRounds: 2
pricedRounds: 2
usageByModel:
  claude-sonnet-5-5:
    rounds: 2
    costUsd: 0.7534386999999999
    completionTokens: 23566
revision_number: 1
---

# Tasks: CME Calendar History

## Context Summary

- Working on the `cme-calendar-history` slice (301). Design:
  `user/slices/301-slice.cme-calendar-history.md`. Decisions D1 to D4 below mean
  that document's D1 to D4.
- Goal: `CME_EQUITY` (ES) and `CME_METALS` (GC) get sessions from
  `CME_DATASET_START = 2010-06-06`. Today a 2010 to 2019 record fails ingest on
  `session_boundary`.
- Two kinds of data, each entered from a dated CME document and never generated
  from a holiday rule: **dated regular-hours eras** (new table
  `trading_calendar_hours`) and **pre-2020 exception rows** (closures and early
  closes in `trading_holidays`).
- One code change: `populate_trading_sessions` uses the era in force on each
  session's date, for a calendar that has eras. One new primary-track
  migration, `065_cme_calendar_history`, in `MINUTE_MIGRATIONS`.
- Sessions from 2020-01-01 on, NYSE and NASDAQ, and all tick-track tables stay
  unchanged.
- Next slice: 302 (`ohlcv-1m` and definition history) places bars in these
  sessions.

## Working Rules (apply to every task)

- Work on the slice branch `301-slice.cme-calendar-history`. Commit at each
  section's checkpoint; the last task ends at a commit.
- Pytest needs the test database URL exported from `.env` (quotes stripped).
  Never read the production URL variable in a test.
- Run `ruff format` on touched files only, then `git diff` and check for
  deleted or rewritten lines you did not mean to touch before each commit.
- Run mypy with the `src` paths and the `test` paths in one invocation; narrower
  runs report false errors.
- Source files stay near 300 lines. `seed_cme_calendar.py` and
  `seed_cme_metals_calendar.py` are already over that, so every new data
  module in this slice is its own file.
- **Sources.** The live cmegroup.com refuses automated reads; read documents
  through the Wayback Machine as slice 221 did (see the header and `# source:`
  comments in `seed_cme_calendar.py`). A forum post is a lead, never a source.
  If no dated CME document can be found for a row or era, do not enter a
  plausible value: follow the stop rule in task 1.3 (eras) or 3.4 (exceptions).
- **One dataset start.** `CME_DATASET_START` is the latest of the supported
  starts across both calendars and both kinds of data (eras and exceptions).
  A calendar with an earlier supported start is trimmed to match, and the trim
  is recorded in the slice's Implementation Notes. Every stop rule below moves
  the constant by this rule only.
- **Unrelated uncommitted work.** The tree holds other changes in files this
  slice edits (`migrations/minute.py`, `test/unit/test_schema_migrations.py`):
  issue #60's `065_instruments_name` migration and its pin updates. Never
  revert, reformat or stage them. Stage only your own hunks (`git add -p`, or
  explicit paths for files only you touched) and check `git diff --cached`
  before each commit.
- **Migration number.** Issue #60 has taken `065`. Before 4.1, read the last id
  in `MINUTE_MIGRATIONS` and use the next number wherever this file says `065`
  or `064` for the chain tip; `grep` for the real ids rather than trusting the
  numbers written here.
- **Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 — Dated hours eras (D1)

- [ ] **1.1 Add `CME_DATASET_START` and the `HoursEra` record**
  - [ ] In `market/schema/seed_cme_calendar.py`, add
        `CME_DATASET_START: Final = date(2010, 6, 6)` beside the seed-start
        constants, with a one-line comment: the start of the Databento CME
        dataset, and the only definition of it.
  - [ ] In `data/base/session_population.py`, add a frozen dataclass
        `HoursEra(effective_from: date, market_open: time, market_close: time,
        source: str)`.
  - [ ] Add `hours_era_in_force(calendar_id, eras, session_date) -> HoursEra`:
        the era with the greatest `effective_from <= session_date`. When none
        qualifies it raises `ValueError` naming the calendar id and the date.
        No fallback to `trading_calendars` hours.
  - [ ] Success: mypy clean
  - [ ] Effort: 1

- [ ] **1.2 Tests for era lookup**
  - [ ] New `test/unit/data/base/test_hours_era.py`, using small literal eras
        (two eras, so one change date).
  - [ ] Cases: a date before the first era raises and the message holds the
        calendar id and the date; the change date itself returns the later era;
        the day before it returns the earlier era; a date far after the last
        era returns the last era; eras given out of order still resolve.
  - [ ] Success: the file passes
  - [ ] Effort: 1

- [ ] **1.3 Find the dated sources for ES regular hours, 2010-06-06 to 2019-12-31**
  - [ ] Using Wayback copies of CME contract-specification pages, trading-hours
        pages and Globex notices, establish the regular open and close for each
        stretch of the range, and the date each change took effect. Start with
        the lead in the design (a close of 16:15 CT moving to 16:00 CT in
        September 2015) and confirm or refute it from CME documents only.
  - [ ] Write each era straight into the new `market/schema/seed_cme_hours.py`
        as it is found (create the module with `CME_EQUITY_HOURS_ERAS` here):
        a `HoursEra` whose `source` is the document title, date and URL. No
        separate note file.
  - [ ] Stop rule: if an era's start or hours cannot be shown by a dated CME
        document, do not enter it. Set the first era's `effective_from` to the
        earliest date that is documented, move `CME_DATASET_START` (1.1) by the
        Working Rules, and write the unsupported range, and why, into the
        "Implementation Notes" of the slice design. Continue with the rest of
        the slice.
  - [ ] Eras are only for 2010-06-06 to 2019-12-31. A documented change on or
        after 2020-01-01 is not entered; write it into the slice's
        Implementation Notes as a finding instead.
  - [ ] Success: every era boundary has a cited document, or the stop rule is
        applied and recorded
  - [ ] Effort: 3

- [ ] **1.4 Find the dated sources for GC regular hours, 2010-06-06 to 2019-12-31**
  - [ ] Same method and stop rule as 1.3, for GC independently, entering
        `CME_METALS_HOURS_ERAS` in `seed_cme_hours.py`. Do not assume GC
        changed on ES's dates.
  - [ ] Success: every era boundary has a cited document, or the stop rule is
        applied and recorded
  - [ ] Effort: 3

- [ ] **1.5 Finish `seed_cme_hours.py`**
  - [ ] The last era of each tuple is the current hours; its open and close
        equal the calendar's current values (reference them from
        `CME_EQUITY_CALENDAR` and `CME_METALS_SEED`, not retyped).
  - [ ] The first `effective_from` is on or before `CME_DATASET_START`.
  - [ ] Add a builder returning the rows to insert into
        `trading_calendar_hours` (calendar id, effective_from, open, close,
        source), so the migration does not unpack eras itself.
  - [ ] Success: mypy clean
  - [ ] Effort: 2

- [ ] **1.6 Tests for the eras**
  - [ ] New `test/unit/market/test_seed_cme_hours.py`.
  - [ ] Cases: every era's `source` is non-empty and contains a URL (`http`)
        (D1); eras are strictly
        ascending and unique by date; no `effective_from` is on or after
        2020-01-01; the first `effective_from` is on or before
        `CME_DATASET_START`; the last era of each calendar equals its
        `trading_calendars` row (open and close); the builder's rows have the
        table's column order.
  - [ ] Success: the file passes
  - [ ] Effort: 2

- [ ] **1.7 Commit checkpoint**
  - [ ] Run `ruff`, `ruff format` on touched files and mypy; run
        `uv run pytest test/unit/data/base test/unit/market -q`.
  - [ ] Commit: `feat: add dated CME hours eras and era lookup`
  - [ ] Success: tests pass; commit made
  - [ ] Effort: 1

---

## Section 2 — Sessions follow the era in force (D1)

- [ ] **2.1 `populate_trading_sessions` takes eras**
  - [ ] Add an optional `hours_eras: Sequence[HoursEra] | None` parameter,
        after `holidays_rows`. `None` means the calendar has no eras and
        `calendars_row` hours apply, exactly as today (NYSE, NASDAQ).
  - [ ] When eras are given, each date's regular open and close come from
        `hours_era_in_force`. An exception row's early close or late open still
        overrides the regular value for that date.
  - [ ] Update the docstring: the new parameter, and that a date before the
        first era raises.
  - [ ] Success: the existing `test_session_population.py` passes unchanged
  - [ ] Effort: 2

- [ ] **2.2 Tests for era-aware population**
  - [ ] Extend `test/unit/data/base/test_session_population.py`.
  - [ ] Cases: a range across an era change gives the earlier close before and
        the later close from the change date (compare UTC instants, including
        one date on each side of a DST change); an early-close exception inside
        an old era overrides that era's close; a range starting before the
        first era raises with the calendar id and date; `hours_eras=None`
        output is identical to the output before this task for a NYSE-shaped row.
  - [ ] Success: the file passes
  - [ ] Effort: 2

- [ ] **2.3 `extend_calendar_sessions` reads the eras**
  - [ ] Create `trading_calendar_hours` only in the migration (Section 4); here
        the extension reads it. In `session_extension.py` add `_read_eras`:
        returns the calendar's rows as `HoursEra`, ordered by `effective_from`,
        and `None` when the calendar has no rows.
  - [ ] The table does not exist until migration 065, and migrations 058 and 059
        call this routine on a fresh database before it. Guard with
        `to_regclass('trading_calendar_hours')` (the way migration 056 branches
        on a missing object): when the table is absent, return `None`. Write a
        comment saying why: the table is created by 065, after the seeds that
        also extend.
  - [ ] Pass the result to `populate_trading_sessions`.
  - [ ] Success: mypy clean; `test_calendar_extension.py` passes unchanged
  - [ ] Effort: 2

- [ ] **2.4 Test the extension against a database**
  - [ ] Extend `test/integration/test_calendar_extension.py` (same fixtures as
        today). Create `trading_calendar_hours` by hand in the throwaway
        database and insert two eras for a calendar.
  - [ ] Cases not covered by 4.3 and 4.4: with the table absent, the extension
        behaves as before; a calendar with no rows in the table behaves as
        before; a range before the first era raises. (Hours on each side of a
        change are checked in 4.3.)
  - [ ] Success: the file passes
  - [ ] Effort: 2

- [ ] **2.5 Commit checkpoint**
  - [ ] Run `ruff`, `ruff format` on touched files, mypy, and
        `uv run pytest test/unit/data/base -q` and the extension integration
        test.
  - [ ] Commit: `feat: populate sessions from the hours era in force`
  - [ ] Success: tests pass; commit made
  - [ ] Effort: 1

---

## Section 3 — Pre-2020 exceptions (D2)

These rows go in new modules and in their own tuples. They must not be added to
`CME_EQUITY_EXCEPTIONS` or the metals tuple: migrations 058 and 059 insert those
on a fresh database, and migration 065's precondition (task 4.2) aborts if any
CME holiday row exists before the seed start.

- [ ] **3.1 Create the two exception modules and the shared source test helper**
  - [ ] New `market/schema/seed_cme_equity_history.py` with
        `CME_EQUITY_PRE2020_EXCEPTIONS` and new
        `market/schema/seed_cme_metals_history.py` with
        `CME_METALS_PRE2020_EXCEPTIONS`, both starting as empty tuples built
        with the existing `closed_day` and `early_close_day` helpers (import
        them; do not copy).
  - [ ] Each module's docstring names the document family it reads and states
        that nothing is generated from a holiday rule.
  - [ ] The existing `test_every_entry_is_preceded_by_a_source_comment` in
        `test/unit/test_seed_cme_calendar.py` checks that a `# source:` comment
        precedes each entry. Move its parsing into a helper both that test and
        the new tests call; do not copy it.
  - [ ] Success: existing `test_seed_cme_calendar.py` and
        `test_seed_cme_metals_calendar.py` pass unchanged in behaviour
  - [ ] Effort: 2

- [ ] **3.2 ES exceptions, 2010-06-06 to 2014-12-31**
  - [ ] For each year, read CME's Globex holiday schedule (the Equity Products
        row) from Wayback, as 221 did. Enter one row per closure and early
        close, one `# source:` comment above each group naming the document,
        its date and its URL.
  - [ ] Include unscheduled closures CME documents in the range, for example
        2012-10-29 and 2012-10-30 (Hurricane Sandy).
  - [ ] Only weekdays and only dates on or after `CME_DATASET_START`; the early
        close time is the document's, in `America/Chicago`.
  - [ ] Success: the tuple covers every year in the range, each with a cited
        document
  - [ ] Effort: 3

- [ ] **3.3 ES exceptions, 2015-01-01 to 2019-12-31**
  - [ ] Same method as 3.2. Include 2018-12-05 (national day of mourning) if
        CME's documents show ES was affected.
  - [ ] Success: the tuple covers every year in the range, each with a cited
        document
  - [ ] Effort: 3

- [ ] **3.4 Tests for the ES exceptions**
  - [ ] New `test/unit/market/test_seed_cme_history.py`, reusing the shared
        helper from 3.1 and parametrised by calendar (the ES case here; 3.7 adds
        GC).
  - [ ] Cases: every entry has a `# source:` comment; dates are unique weekdays
        between `CME_DATASET_START` and 2019-12-31; statuses and times are
        consistent (closed rows carry no times, early closes carry a close
        time); every year 2010 to 2019 has at least one entry (2010 from
        its start date); no entry duplicates a date in `CME_EQUITY_EXCEPTIONS`.
  - [ ] Stop rule: if a year's schedule cannot be found, enter nothing for it.
        If the unsupported range is the start of the dataset, move
        `CME_DATASET_START` to the first date after it by the Working Rules,
        write the gap and its reason into the slice's Implementation Notes, and
        set the test's covered-year expectation to match. A gap in the middle of the
        range is not allowed to stand: search again or, if no document exists,
        record it in the notes and mark that year in the test as unsupported by
        name so the gap is visible in code.
  - [ ] Success: the file passes
  - [ ] Effort: 2

- [ ] **3.5 GC exceptions, 2010-06-06 to 2014-12-31**
  - [ ] Same method as 3.2, reading the metals row of each schedule. Do not copy
        ES rows; GC's schedule can differ.
  - [ ] Success: the tuple covers every year in the range, each with a cited
        document
  - [ ] Effort: 3

- [ ] **3.6 GC exceptions, 2015-01-01 to 2019-12-31**
  - [ ] Same method as 3.3, for GC.
  - [ ] Success: the tuple covers every year in the range, each with a cited
        document
  - [ ] Effort: 3

- [ ] **3.7 Tests for the GC exceptions**
  - [ ] Add the GC parameter to `test_seed_cme_history.py`
        (`CME_METALS_PRE2020_EXCEPTIONS` against `CME_METALS_EXCEPTIONS`), with
        the same stop-rule handling. No second copy of the cases.
  - [ ] Success: the file passes
  - [ ] Effort: 1

- [ ] **3.8 Insert builders for the exception tuples**
  - [ ] Add one builder per module that returns the `trading_holidays` insert
        for its tuple. `cme_holidays_insert` takes a `CmeCalendarSeed`, so pass
        it the calendar's seed through `dataclasses.replace(seed,
        exceptions=<pre-2020 tuple>)`, reusing its column contract. The seed
        module's `_insert_sql` ends in `ON CONFLICT DO NOTHING`; the pre-2020
        insert must be a plain `INSERT` with no `ON CONFLICT` (D3), so a
        conflicting stored row is an error. Give `_insert_sql` a keyword to omit
        the clause rather than writing a second SQL builder.
  - [ ] Test in `test_seed_cme_history.py`: the insert's SQL has no
        `ON CONFLICT` and its columns match `trading_holidays`. Row counts are
        covered end to end in 4.3.
  - [ ] Success: that test file passes; mypy clean
  - [ ] Effort: 2

- [ ] **3.9 Commit checkpoint**
  - [ ] Run `ruff`, `ruff format` on touched files, mypy and
        `uv run pytest test/unit/market test/unit/test_seed_cme_calendar.py
        test/unit/test_seed_cme_metals_calendar.py -q`.
  - [ ] Commit: `feat: add sourced pre-2020 CME exceptions`
  - [ ] Success: tests pass; commit made
  - [ ] Effort: 1

---

## Section 4 — Migration 065 (D3)

- [ ] **4.1 Register `065_cme_calendar_history` and create the table**
  - [ ] Append a migration to `MINUTE_MIGRATIONS` in
        `market/schema/migrations/minute.py` after
        `064_minute_eligible_rollup_swap`, with a `python_fn`
        (`_cme_calendar_history`) and a description in the style of 058/059.
  - [ ] The function first runs
        `CREATE TABLE IF NOT EXISTS trading_calendar_hours (calendar_id,
        effective_from, market_open, market_close, source)` with primary key
        `(calendar_id, effective_from)`; `calendar_id` references
        `trading_calendars`, times are `TIME NOT NULL`, `source` is
        `TEXT NOT NULL`.
  - [ ] Check `sql.md` in the rules directory and the application role's grants
        (`scripts/provision_*`) for whether a new table needs a read grant for
        the application role; add what existing calendar tables have.
  - [ ] Success: mypy clean; the migration module imports
  - [ ] Effort: 2

- [ ] **4.2 Preconditions, inserts, extension and the 2020 check**
  - [ ] Before any insert, for `CME_EQUITY` and `CME_METALS`: abort with a
        `RuntimeError` naming the calendar and the offending rows if any
        `trading_holidays` row or any `trading_sessions` row exists dated before
        the calendar's seed start (D3).
  - [ ] Read the calendar's sessions from its seed start on into memory.
  - [ ] Insert the eras, then the pre-2020 exceptions with the plain builders
        from 3.8 (no `ON CONFLICT`).
  - [ ] Call `extend_calendar_sessions` for each calendar from
        `CME_DATASET_START` to its seed start minus one day.
  - [ ] Read the sessions from the seed start on again and compare with the
        copy taken before. Raise `RuntimeError`, naming the first differing
        date, if they differ (D3). The raise rolls back the migration.
  - [ ] Idempotence: a second run hits the precondition and aborts. That is
        intended and the description says so; the runner records the id after
        the first success and never reruns it.
  - [ ] Success: mypy clean
  - [ ] Effort: 3

- [ ] **4.3 Integration tests for the migration**
  - [ ] New `test/integration/test_migration_065_cme_calendar_history.py`,
        modelled on `test_migration_059_cme_metals_calendar.py` (throwaway
        databases from the conftest fixtures; read-only checks share the
        session-scoped migrated database).
  - [ ] Cases:
    - [ ] On an empty database migrated through 065: both calendars' first
          session is the first weekday on or after `CME_DATASET_START` that is
          not a sourced closure, none is dated earlier, and the sessions have
          no gaps except weekends and the sourced exceptions. The pre-2020
          `trading_holidays` row count per calendar equals its tuple length.
    - [ ] `test_cme_history_2020_unchanged`: sessions from 2020-01-01 on, for
          all four calendars, are identical to a database migrated through 064
          only (dump both, compare). NYSE and NASDAQ untouched.
    - [ ] On a populated database (migrated through 064, then 065 applied):
          the same result as the empty path.
    - [ ] A session in each side of an era change carries that era's open and
          close; a date inside the first era carries the first era's pair.
    - [ ] The precondition aborts: a stray `trading_holidays` row before the
          seed start makes 065 fail and leaves no `trading_calendar_hours` rows
          and no new sessions.
  - [ ] Success: the file passes
  - [ ] Effort: 3

- [ ] **4.4 Placement tests through `locate_sessions`**
  - [ ] In the same file or a sibling, using the migrated database and
        `SessionIndex`/`locate_sessions`: a timestamp at 16:05 CT inside the
        first era's regular hours is placed; a timestamp in the daily break of
        that era fails `session_boundary` and the error names the sessions either
        side; a timestamp in the later era's break fails and one inside its
        hours is placed. Choose the timestamps from the eras entered in 1.5; if
        the eras have a single hours pair, test one pre-2020 in-hours and one
        in-break timestamp.
  - [ ] Success: the tests pass
  - [ ] Effort: 2

- [ ] **4.5 Update chain pins and migration docs**
  - [ ] Run `grep -rn` for the id that was the chain tip before 4.1 (`064_minute_eligible_rollup_swap`, or `065_instruments_name` if issue #60 has landed) over `test src scripts` and
        update every pin that names the *last* migration id (the one in
        `test/unit/test_schema_migrations.py` near line 1326 and the migration
        count comment near line 225), as every primary migration does. Do not
        touch tests about 064 itself or `scripts/cutover_173_*`.
  - [ ] Add a paragraph for 065 to `market/schema/migrations/README.md`, in the
        style of the tick-track notes: what it adds and that it aborts on a
        stored pre-seed row.
  - [ ] Add an entry under the unreleased section of `CHANGELOG.md`. Do not
        bump a version or tag.
  - [ ] Success: `uv run pytest test/unit/test_schema_migrations.py -q` passes
  - [ ] Effort: 1

- [ ] **4.6 Commit checkpoint**
  - [ ] Run `ruff`, `ruff format` on touched files, mypy, then
        `uv run pytest test/unit/market test/unit/data/base test/unit/test_schema_migrations.py -q`
        and `uv run pytest test/integration -q -k "calendar or session or migration_065"`.
        Integration tier: run it by itself; on a known-flaky failure re-run that
        test in isolation before investigating.
  - [ ] Commit: `feat: add migration 065 for CME calendar history`
  - [ ] Success: tests pass; commit made
  - [ ] Effort: 1

---

## Section 5 — Held-file check on pre-2020 dates (D4)

- [ ] **5.1 Make `verify_cme_sessions.py` accept pre-2020 dates**
  - [ ] Read `run()` and the calendar read in `scripts/verify_cme_sessions.py`
        and find what, if anything, limits the span to 2020 onward (the
        populated-range check in `TradingCalendar.sessions_between` follows the
        stored sessions, so it may need no change). Change only what rejects a
        pre-2020 day; state in the task's commit message body what that was, or
        that nothing did.
  - [ ] Update the docstring's exit-code text only if behaviour changed.
  - [ ] Success: mypy clean
  - [ ] Effort: 2

- [ ] **5.2 Test the script on a pre-2020 day**
  - [ ] There is no existing script test directory with a verify-sessions test;
        add `test/integration/test_verify_cme_sessions_pre2020.py`. Build a tiny
        DBN job in a temp directory (manifest plus one file) using the same
        helpers `test/fixtures/calendar/` and the 220 reader tests use, with
        records on a pre-2020 weekday: all inside a session, then one in the
        daily break.
  - [ ] Run `run()` against the migrated throwaway database: exit 0 for the
        placed day, exit 1 for the unplaced one.
  - [ ] Success: the file passes; the 2024 held-job check from the design's
        walkthrough step 3 still exits 0 if the job directory exists on the host
  - [ ] Effort: 3

- [ ] **5.3 Commit checkpoint**
  - [ ] Run `ruff`, `ruff format` on touched files, mypy and the new test.
  - [ ] Commit: `feat: verify CME sessions on pre-2020 dates`
  - [ ] Success: tests pass; commit made
  - [ ] Effort: 1

---

## Section 6 — Full validation

- [ ] **6.1 Walk the design's verification walkthrough**
  - [ ] Run steps 1 to 4 of the design's Verification Walkthrough against a
        throwaway migrated database (steps 1 and 4 are the test commands; step
        2 is the SQL; step 3 is the held-job command if the directory exists).
        Step 5 is the production apply; it is not part of this task list. The
        walkthrough's `test/integration/data` path does not hold this slice's
        tests (they sit directly under `test/integration`); run
        `uv run pytest test/integration -q -k "calendar or session or cme_calendar_history"`
        instead.
  - [ ] Check every Success Criterion 1 to 8 in the design against a test that
        covers it; add a missing test rather than skipping the criterion.
  - [ ] Success: all pass; each criterion maps to a test
  - [ ] Effort: 2

- [ ] **6.2 Final lint and type pass, and the notes**
  - [ ] `ruff check`, `ruff format --check` on touched files, and mypy over all
        touched files, clean.
  - [ ] Confirm each new source file is within about 300 lines.
  - [ ] Update the slice design's Implementation Notes with: the eras entered,
        any unsupported range and its reason, any post-2019 hours change found,
        and the migration id.
  - [ ] Commit: `docs: record 301 implementation notes`
  - [ ] Success: lint and types clean; commit made
  - [ ] Effort: 1
