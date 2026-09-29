---
docType: tasks
slice: cme-session-model-and-data-correctness-amendment
project: trading-data
lld: user/slices/221-slice.cme-session-model-and-data-correctness-amendment.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: []
interfaces: [222, 225, 228, 231]
projectState: >
  Slice design committed; slice review PASS (993fab4). 220 (DBN reader) and
  923 (multi-database plumbing, minute track routed to primary) are released
  in 0.19.0. The calendar tables hold NYSE and NASDAQ only; the minute
  migration chain ends at 056. NYSE holiday defects are filed as issue #26
  and are out of scope.
dateCreated: 20260928
dateUpdated: 20260928
status: complete
part: 1
---

# Tasks: CME Session Model and Data-Correctness Amendment

## Context Summary

- This slice adds the `CME_EQUITY` calendar (for ES) behind the existing
  calendar surface that invariant I4 governs. It also writes the frame of the
  data-correctness contract's tick amendment.
- It delivers:
  - the open-after-close rule, held in one helper (`session_interval`);
  - `SessionIndex`, a pure session lookup with scalar and vectorized forms;
  - `trading_calendars.holidays_seeded_through`, and one shared extension
    routine that respects it;
  - a dated CME exception table, and migrations 057 and 058;
  - `TradingCalendar.sessions_between` and `session_containing`;
  - the `mt data calendars sessions` command, and a fix to `mt data calendars
    list`;
  - `scripts/verify_cme_sessions.py`, run over the two free-credit ES jobs
    on disk;
  - the contract amendment (I11–I14, the I7 exception, vocabulary rules, and
    the sequence-gap move).
- Read LLD decisions D1–D10 before starting. Every rule referenced below is
  stated there.
- Constraints that bind every task:
  - **NYSE and NASDAQ `trading_sessions` rows must not change.**
  - Nothing is bought.
  - No tick database is touched.
  - `pandas_market_calendars` is never added to `pyproject.toml`.
- Next slice: 222 (tick storage track), or 225 once 224 lands. 225 consumes
  `sessions_between`, `SessionIndex.locate_ns`, `extend_calendar_sessions`,
  and `FUTURES_PRODUCT_CALENDAR`.

**Test environment.** Export `MT_TIMESCALE_TEST_URL` from `.env` with the
quotes stripped before any integration run. Integration tests use a throwaway
database created by the existing fixtures, never a production URL. Run mypy on
`src` and `test` in one invocation. Run the unit and integration tiers
separately. Re-run a failure in isolation before investigating it, because
known pre-existing flakes exist.

**Walkthrough database (never production).** `MT_TIMESCALE_DB_URL` in `.env`
is the production `trading` database, so a migration applied from a slice
branch would land there. Steps that run `mt` commands against a migrated
database (5.2's strict check, 8.2 and 10.2) use a throwaway database: create
`t221_walkthrough` on the test cluster (the host in `MT_TIMESCALE_TEST_URL`),
point `MT_TIMESCALE_DB_URL` and `MT_TIMESCALE_MAINTENANCE_URL` at it for those
commands only, run `mt data migrate apply`, and drop it when done. Only a
database this process created may be dropped (`sql.md`).

**Commits.** Commit at the end of each section with a semantic message (for
example `feat: add session interval rule`). Scope `ruff format` to touched
files, and check `git diff` for swept pre-existing lines before each commit.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 0 — Baseline

- [x] **0.1 Freeze the pre-change NYSE and NASDAQ session output**
  - [x] Before editing any source file, write a short script in the
        scratchpad. For `NYSE` and for `NASDAQ`, it calls the current
        `populate_trading_sessions` over 2020-01-01 → 2028-12-31, using the
        calendar metadata from `seed_calendar.py` and the holidays from
        `generate_holidays(cal, 2020, 2026)`, the exact inputs migrations
        007, 008 and 026 use.
  - [x] Save the rows as JSON at
        `test/fixtures/calendar/nyse_nasdaq_sessions_pre_221.json`. Store
        ISO dates and UTC timestamps, and record in a top-level
        `provenance` key the commit SHA the output came from.
  - [x] Success: the file exists, holds one entry per session for both
        calendars, and is committed before any change under `src/`
  - [x] Effort: 1

---

## Section 1 — Open-after-close rule (LLD D3)

- [x] **1.1 Add `session_interval` and route `populate_trading_sessions`
      through it**
  - [x] In `data/base/session_population.py`, add
        `session_interval(session_date, open_t, close_t, tz) -> tuple[datetime, datetime]`.
        It works as follows:
    1. Close is `close_t` on `session_date`.
    2. Open is `open_t` on `session_date` when `open_t < close_t`, and on
       `session_date - 1 day` when `open_t > close_t`.
    3. It raises `ValueError` naming the date when `open_t == close_t`.
    4. It builds both values in `tz`, then converts them to UTC.
  - [x] Replace the two inline `datetime.combine(...)` calls in
        `populate_trading_sessions` with one call. Holiday overrides are still
        resolved first. The weekday loop is unchanged.
  - [x] Update the docstring to state the rule and that it applies to every
        calendar.
  - [x] Success: the module imports cleanly, and existing
        `test/unit/data/base/test_session_population.py` passes unchanged
  - [x] Effort: 2

- [x] **1.2 Unit tests for `session_interval` and CME population**
  - [x] In `test_session_population.py`, cover `session_interval` for:
    1. NYSE hours: 09:30 → 16:00, same day.
    2. CME hours: 17:00 → 16:00, open on the previous day.
    3. A CME early close at 12:00, which still opens the previous day at 17:00.
    4. Equal times, which raise.
    5. Both 2024 DST weekends (March and November), where the open's UTC
       offset differs from the close's.
  - [x] Add a test that populates CME hours over a week containing a `closed`
        date and an `early_close` date. Check that the closed date is absent,
        and that the next session opens at 17:00 local on the closed date.
  - [x] Add a synthetic `late_open` case: a CME-hours date with
        `late_open_time` 08:30 opens 08:30 on the same day, because the
        open is no longer after the close (LLD criterion 2).
  - [x] Add the NYSE/NASDAQ identity test: regenerate the 0.1 inputs and
        require output equal to `nyse_nasdaq_sessions_pre_221.json`. This is
        the unit-tier half of the LLD's "NYSE regression test". The
        integration-tier half is `test_nyse_sessions_unchanged` in 5.2.
  - [x] Success: `uv run pytest test/unit/data/base/test_session_population.py -q`
        passes
  - [x] Effort: 2

---

## Section 2 — Session index (LLD D7)

- [x] **2.1 Create `data/base/session_index.py`**
  - [x] A frozen `Session` dataclass: `calendar_id`, `session_date`,
        `open_utc` and `close_utc`, all tz-aware UTC.
  - [x] `SessionIndex(sessions)`:
    - it raises `ValueError` when the input is not sorted by `open_utc`,
      when any session overlaps the next, or when `open_utc >= close_utc`.
      It never reorders its input: a caller handing it sessions out of order
      has a bug, and the LLD API contract requires the error;
    - it keeps int64 nanosecond arrays of opens and closes.
  - [x] `locate(ts)` returns the `Session` whose closed interval
        `[open_utc, close_utc]` contains `ts`, or `None`. A naive `ts` raises
        `ValueError`.
  - [x] `locate_ns(ts_ns: np.ndarray) -> np.ndarray` finds the position with
        `np.searchsorted(opens, ts, side="right") - 1`, checks `ts <= close`,
        and returns `-1` where there is no session. Its docstring says that
        callers must have handled the populated-range check already (D7).
  - [x] `locate` is implemented through the same position logic, so the two
        forms cannot diverge.
  - [x] Success: imports cleanly; mypy clean
  - [x] Effort: 2

- [x] **2.2 Unit tests for `SessionIndex`**
  - [x] Create `test/unit/data/base/test_session_index.py`. Build three
        synthetic CME-shaped sessions, one of them after a closed day.
  - [x] Cover:
    - exactly at an open, and exactly at a close (both included);
    - 1 ns before an open, and 1 ns after a close (`None`);
    - a timestamp in the daily break, and one on the closed day (`None`);
    - a naive datetime (raises);
    - overlapping input, inverted input, and unsorted input (all raise).
  - [x] Add a property-style test: over 1,000 random timestamps spanning the
        sessions, `locate_ns` agrees with `locate` element by element.
  - [x] Success: the test file passes. **Commit Sections 0–2.**
  - [x] Effort: 2

---

## Section 3 — Holiday bound and shared extension (LLD D6)

- [x] **3.1 Migration `057_calendar_holidays_seeded_through`**
  - [x] Append it to the minute list in `market/schema/migrations/minute.py`
        with the SQL in LLD "Database / Storage Schema". The `2026-12-31`
        literal is rendered from `_SEED_END_YEAR`, never typed.
  - [x] Its description states why the value is 2026-12-31: that is where
        migrations 007 and 008 stopped.
  - [x] Success: `uv run python -c "import manta_trading.market.schema.migrations.minute"`
        succeeds, and the migration id is unique
  - [x] Effort: 1

- [x] **3.2 Create `data/base/session_extension.py`**
  - [x] A frozen `CalendarExtension` dataclass: `calendar_id`,
        `rows_upserted`, `horizon_after`, `holidays_seeded_through` and
        `clamped_by_holiday_bound`.
  - [x] `extend_calendar_sessions(conn, calendar_id, *, start, end)` works as
        follows:
    1. It reads the calendar row, including `holidays_seeded_through`. An
       unknown calendar raises `ValueError` naming it.
    2. It reads the calendar's holidays.
    3. It sets `effective_end = min(end, holidays_seeded_through)` and
       records whether the clamp applied.
    4. When `start > effective_end`, it returns 0 rows.
    5. Otherwise it calls `populate_trading_sessions`, then upserts with the
       existing `ON CONFLICT (calendar_id, session_date) DO UPDATE` statement.
    6. It does not commit; the caller owns the transaction.
  - [x] `horizon_after` is `MAX(session_date)` read after the upsert.
  - [x] Success: imports cleanly; mypy clean
  - [x] Effort: 2

- [x] **3.3 Integration tests for 057 and `extend_calendar_sessions`**
  - [x] New file `test/integration/test_calendar_extension.py`, run on a
        throwaway database migrated through 057.
  - [x] Cover:
    - after 057, the column is `NOT NULL`, and NYSE and NASDAQ read
      `2026-12-31`;
    - the extension clamps: on a test-created calendar with a bound 30 days
      out, the latest session written is at or before the bound, and
      `clamped_by_holiday_bound` is true;
    - an unknown calendar raises;
    - a second call is a no-op (0 new rows).
  - [x] Success: `uv run pytest test/integration/test_calendar_extension.py -q`
        passes
  - [x] Effort: 2

- [x] **3.4 Move `mt data extend` onto the shared routine**
  - [x] In `cli/commands/data.py` `data_extend`, replace the inline
        holidays-and-populate-and-upsert loop with a call to
        `extend_calendar_sessions` per calendar. Keep the exit codes and
        `--strict` semantics unchanged.
  - [x] Each calendar's line adds `holidays seeded through <date>`. When
        `clamped_by_holiday_bound` is true, the line says the bound limits the
        horizon and that the next year's schedule must be seeded (LLD D6
        wording).
  - [x] Delete the now-unused local imports.
  - [x] Update `test/unit/cli/commands/test_data_extend.py` mocks to the new
        call, and add a test that a clamped result prints the bound message.
  - [x] Success: `uv run pytest test/unit/cli/commands/test_data_extend.py -q`
        passes, and `grep -n populate_trading_sessions src/manta_trading/cli/commands/data.py`
        returns nothing
  - [x] Effort: 2

- [x] **3.5 Move auto-extend onto the shared routine**
  - [x] In `data/maintenance/auto_extend.py`, replace the per-calendar
        populate-and-upsert body with `extend_calendar_sessions`. Keep the 24 h
        gate, the error handling (`logger.exception`, then continue), and
        `AutoExtendResult`.
  - [x] Add `clamped: dict[str, date]` (calendar → bound) to
        `AutoExtendResult`.
  - [x] In `cli/rendering/status_table.py`, render clamped calendars with the
        bound message.
  - [x] Update `test/unit/data/maintenance/test_auto_extend.py`, and add a
        clamped case.
  - [x] Success: the auto-extend and status-table unit tests pass, and
        `populate_trading_sessions` is now called only from
        `session_extension.py`, `trading_calendar.py` and migration 026's
        callable (check with grep)
  - [x] Effort: 2
  - [x] **Commit Section 3.**

---

## Section 4 — CME exception table (LLD D2, D5)

- [x] **4.1 Compile the CME Globex equity schedule from CME sources**
  - [x] Confirm the regular hours, Sunday–Friday 17:00–16:00 America/Chicago,
        across 2020-01-01 → today from CME's E-mini S&P 500 contract specs and
        notices. If the hours changed inside that range, **stop and ask the
        PM** (D2). The 2021 removal of the 15:15–15:30 halt does not count.
  - [x] Build the list of exception dates for 2020 through the last year CME
        has published:
    - current and next year from cmegroup.com/tools-information/holiday-calendar.html;
    - past years from CME's per-holiday trading-hours notices.
  - [x] For each date, record `market_status` (`closed`, `early_close` or
        `late_open`), the time, the holiday name, and the source URL or notice
        id.
  - [x] A year that cannot be sourced from CME is reported to the PM. It is
        never filled from memory or from a rule.
  - [x] Record the bound as the last day of the last fully published year.
  - [x] Success: a working list in the scratchpad with a source for every row
  - [x] Effort: 4

- [x] **4.2 Cross-check against an independent derivation**
  - [x] In the scratchpad only, run
        `uv run --with pandas_market_calendars python <script>`. The script
        lists the `CME Globex Equity` calendar's holidays and early closes
        over the same range, then diffs them against 4.1.
  - [x] Resolve each difference from a CME notice.
  - [x] Add an `## Implementation Findings` section to the slice design with
        a table of the differences: date, 4.1 value, library value, resolution
        and source.
  - [x] Success: every difference is resolved or escalated, and
        `pyproject.toml` and `uv.lock` are unchanged
  - [x] Effort: 2

- [x] **4.3 Create `market/schema/seed_cme_calendar.py`**
  - [x] Add `CME_EQUITY_CALENDAR_ID = "CME_EQUITY"`, defined once. It is the
        only place the literal appears. Other modules import it (6.3).
  - [x] Add `CME_EQUITY_CALENDAR` metadata with the D2 values,
        `CME_EQUITY_SEED_START = date(2020, 1, 1)`, and
        `CME_EQUITY_HOLIDAYS_SEEDED_THROUGH` from 4.1.
  - [x] Add `CME_EQUITY_EXCEPTIONS`: one explicit dict per 4.1 row, in date
        order, each preceded by a `# source:` comment. No rule functions.
  - [x] Add INSERT builders for this calendar row, including
        `holidays_seeded_through`, and for its holidays, with `ON CONFLICT DO
        NOTHING`. Do not modify `seed_calendar.generate_calendar_insert_sql`
        (LLD Patterns).
  - [x] Success: imports cleanly; mypy clean
  - [x] Effort: 2

- [x] **4.4 Unit tests for the seed table**
  - [x] New file `test/unit/test_seed_cme_calendar.py`. Cover:
    - every exception date is a weekday and unique, falls between
      `CME_EQUITY_SEED_START` and `CME_EQUITY_HOLIDAYS_SEEDED_THROUGH`, and
      has a valid `MarketStatus`;
    - every `early_close` time is before 16:00, and a `closed` row has no
      times;
    - reading the module source, every exception entry is preceded by a
      `# source:` comment;
    - the INSERT builders produce SQL with the expected column list.
  - [x] Success: the file passes. **Commit Section 4**, including the 4.2
        findings in the slice design.
  - [x] Effort: 2

---

## Section 5 — Seed migration (LLD Database / Storage Schema)

- [x] **5.1 Migration `058_seed_cme_equity_calendar`**
  - [x] Python callable in the form of 026 (`_run_trading_sessions_population`).
        It works as follows:
    1. Execute the 4.3 calendar and holiday INSERTs.
    2. Call `extend_calendar_sessions` for `CME_EQUITY_CALENDAR_ID` from
       `CME_EQUITY_SEED_START` to Dec 31 of `current_year +
       TRADING_SESSIONS_EXTENSION_YEARS`.
  - [x] It does not touch NYSE or NASDAQ.
  - [x] Retarget `test_chain_ends_at_056` in
        `test/unit/test_schema_migrations.py` to `test_chain_ends_at_058`, and
        keep its docstring's reasoning.
  - [x] Success: the schema-migrations unit tests pass
  - [x] Effort: 2

- [x] **5.2 Integration tests for migration 058**
  - [x] New file `test/integration/test_migration_057_058_cme_calendar.py`,
        run on a throwaway database. Cover:
    - the `CME_EQUITY` row matches D2, with its bound;
    - the exception rows equal `CME_EQUITY_EXCEPTIONS`;
    - the session count equals the weekdays from 2020-01-02 through the
      bound, minus the `closed` dates;
    - the LLD's functional criterion 3 dates (Labor Day 2024, 2024-09-03,
      2024-12-25 absent, 2024-12-26 opening 2024-12-25 17:00 CT);
    - re-applying the migrations is a no-op.
  - [x] **NYSE unchanged:** dump NYSE and NASDAQ `trading_sessions` after
        migrating through 056, then again after 057 and 058, then again after
        `extend_calendar_sessions` runs for both over their current range.
        All three dumps must be identical. Name the test
        `test_nyse_sessions_unchanged`. This is the integration-tier half of
        the LLD's "NYSE regression test". The unit-tier half is in 1.2.
  - [x] **Strict exit 4:** on this throwaway database, set the CME bound 30
        days out, and assert that `mt data extend --calendar CME_EQUITY
        --strict` exits 4 and prints the bound message.
  - [x] Success: the file passes. **Commit Section 5.**
  - [x] Effort: 3

---

Continued in `221-tasks.cme-session-model-and-data-correctness-amendment-2.md` (Sections 6–10).
