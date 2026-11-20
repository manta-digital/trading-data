---
docType: tasks
slice: session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set
project: trading-data
lld: user/slices/174-slice.session-aligned-equity-bars-buckets-anchored-to-the-exchange-session-with-a-regular-hours-set.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies:
- 144
- 171
- 173
- 182
- 186
- 221
- 301
interfaces:
- 190
projectState: Slice design committed and its review (CONCERNS) answered. Equity bars
  wider than 1m come from four UTC-bucketed eligible caggs; nothing marks pre, regular
  or post minutes. NYSE/NASDAQ holidays come from a rule generator (seed_calendar.py)
  seeded 2020-2026, with 2021-12-31 wrongly closed and 2025-01-09 missing (#26). adjusted()
  picks the factor by UTC date (#28). Latest minute migration is 067 (#46, index symbols).
dateCreated: 20261119
dateUpdated: 20261120
status: in_progress
turns: 86
promptTokens: 7410456
cachedTokens: 7201913
cacheWriteTokens: 208437
completionTokens: 58828
durationSeconds: 622.8
costUsd: 4.285177
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-opus-5-5:
    rounds: 3
    costUsd: 4.285176999999999
    completionTokens: 58828
revision_number: 2
---

# Tasks: Session-Aligned Equity Bars

## Context Summary

- Working on slice 174. It adds `session=utc|regular|extended` to `GET /api/v1/bars/{symbol}`.
  Session bars are built at read time from eligible 1m bars, each phase (pre, regular, post)
  bucketed from its own start, with bounds from `trading_sessions`. The default (`utc`) is
  today's bars.
- It also fixes the NYSE/NASDAQ holiday data and reconciles the gap rows it moves (#26),
  picks the adjustment factor by exchange-local date on every intraday read (intraday half
  of #28), and documents the bar stamped at the extended close (#78).
- Prerequisites, all merged: 144 (`trading_sessions`, `OutOfHorizonError`), 221
  (`holidays_seeded_through`, `extend_calendar_sessions`), 301 (migration 066, the
  explicit-dated-rows pattern), 171/173 (`eligible_sql`, eligible default), 182 (bars
  response), 186 (admission, 20 s serving timeout, ceilings as constant plus `Settings`).
- Tooling: `uv run --extra dev ruff format <touched files>` and `ruff check <touched files>`
  (never a directory; grep `git diff main` for deletions before each commit); mypy with the
  src paths and the tests in one invocation; integration tests need `MT_TIMESCALE_TEST_URL`
  exported from `.env` (strip the quotes). Run the unit, integration and load tiers
  separately. Known pre-existing failures are in the project memory notes; re-run a failure
  in isolation before investigating it.
- Production SQL that needs a date never casts a timestamp: the production session timezone
  is America/Denver. Use `(time AT TIME ZONE 'UTC')::date`, the calendar's timezone, or
  `trading_sessions.session_date`.
- The served bar key is `timestamp` (`BarRecord`); use it in docs and checks.
- Module names below are suggestions; keep each file under about 300 lines. New tests go in
  new files: `test/unit/api_server/test_bars.py` is already 35 KB.
- Merge, push, release, the cutover and the production checks are not tasks; see "Phase 7
  handoff" at the end. Nothing here merges or pushes.
- Next planned slice: per the 140 slice plan.

## 1. What the close-stamp bar holds (#78)

Read-only on production. Runs first so the API docs carry the answer (design D4: the
close-stamp bar is outside every session bar).

- [x] **1.1 Investigate and write the note**
  - [x] A one-off script outside the repo, run with `uv run python` from the dev checkout,
    URLs from `Settings`, one read-only transaction (`default_transaction_read_only=on`).
    It writes nothing.
  - [x] Sample: at least 20 symbols (the 10 largest by minute rows plus 10 spread across the
    universe) over at least 3 months of full sessions, plus every early-close session held
    (from `trading_holidays` with `market_status='early_close'`, 2025 and 2026).
  - [x] For each symbol and session, find the bar stamped exactly at the extended close
    instant (20:00 ET, or the early-close day's extended close) and record: present or not;
    single-price or not; its close against the `daily_ohlcv` close; its volume against
    (a) the daily volume, (b) the sum of 04:00-20:00 ET minutes, (c) the sum of regular
    minutes, (d) the daily volume minus (c); and the 15:59 ET minute's volume against the
    median regular minute's (does the closing auction appear in the last regular minute?).
  - [x] Record which `utc` rollup bucket holds the bar today, and whether `prints=eligible`
    serves it (#78 work item 4).
  - [x] Write `user/notes/174-close-stamp-bar.md` (front matter as `309-time-bar-measurement.md`:
    `docType: notes`, `project`, `dateCreated`, `dateUpdated`, `status: complete`): the
    procedure, a summary table, the finding (what the bar holds, stated with the evidence,
    or "not established" with what was ruled out), and what it means for regular-session
    volume summed from session bars. If not established, 14.1 documents what was observed,
    says its meaning is unconfirmed, and tells clients it is outside every session bar;
    15.1 then writes `BLOCKED:` for design criterion 11 with the evidence, so the PM decides
    whether it closes.
  - [x] Add a comment to #78 linking the note's finding (`gh issue comment 78`). Do not
    close #78; its `closing_print` decision (work item 3) is follow-up work.
  - [ ] If the production database is unreachable from this checkout, write `BLOCKED:` and
    the reason here and continue with section 2; section 14 needs this note. (not needed: production was reachable)

  Finding: close price established; volume meaning not established (15.1 criterion 11 applies). Side finding: 2020-07-02 and 2026-07-02 are wrongly 13:00 early closes in the generator; see section 2.

**Commit**: `docs: record what the equity close-stamp bar holds (#78)`

## 2. NYSE/NASDAQ holidays as explicit dated rows

- [x] **2.1 The holiday table**
  - [x] First, fetch the source: NYSE's published holiday and early-close schedule. Record
    the source URL(s) in the module docstring. Early-close rows carry an extended close of
    17:00 ET. If the
    source cannot be fetched, or does not yet publish every 2027 or 2028 date, write
    `BLOCKED:` with what is missing and stop sections 2-6 and 9-13 (they need the migrated
    calendar); never write a row the fetched source does not show. Sections 1, 7, 8, 12.1,
    14.2 and 14.3 continue.
  - [x] New module `src/manta_trading/market/schema/nyse_calendar_history.py`: every NYSE
    full closure and early close for 2020-2028 as explicit dated rows (date, name, status,
    early close time, extended close time), on the pattern of the `seed_cme_*` data modules
    that `migrations/cme_calendar_history.py` imports. Nothing is generated from a rule.
  - [x] Separately, in a new module `market/schema/nyse_session_changes.py`, transcribe from
    the same source the literal `EXPECTED_SESSION_CHANGES`, in the plan's buckets (4.1):
    `opened` 2021-12-31; `closed` 2025-01-09 and each 2027-2028 full closure; `retimed` 2020-07-02 and 2026-07-02 (full days, not early closes) and each
    2027-2028 early close. It is typed from the source, not derived from the table, so the
    two transcriptions check each other.
  - [x] Define `NYSE_HOLIDAYS_SEEDED_THROUGH` (2028-12-31) once in this module. NASDAQ uses
    the same rows; expose one function that returns the rows for a given calendar id.
  - [x] Compare against `generate_holidays('NYSE', 2020, 2026)`. The only differences allowed
    are 2021-12-31 (open, not closed), 2025-01-09 (closed), and 2020-07-02 and 2026-07-02
    (full days, not the generator's 13:00 early closes). If the published schedule
    disagrees with any other 2020-2026 row, write `BLOCKED:` with the date and both values
    and stop: the design allows only the known defects to change.
  - [x] `generate_holidays` stays for migrations 007/008 (a fresh database replays them).
    Grep its callers; any caller other than those migrations switches to the new table.
  PM 2026-11-19: 2020-07-02 and 2026-07-02 were full days the generator made 13:00 early closes; fixed in the same migration as retimed (D8 amended). Generator diff is therefore four dates, not two.
- [x] **2.2 Tests** (`test/unit/market/schema/test_nyse_calendar_history.py`)
  - [x] 2021-12-31 absent, 2025-01-09 a full closure, every 2027 and 2028 year has its
    closures, every early-close row has a 17:00 extended close, no weekend dates, no
    duplicate dates, NYSE and NASDAQ rows identical apart from the calendar id.
  - [x] The difference from `generate_holidays` over 2020-2026 is exactly the four known
    dates (pins the comparison above).
  - [x] The table's 2027-2028 closures and early closes equal `EXPECTED_SESSION_CHANGES`
    for those years, date for date.
  Result: `test/unit/market/schema/test_nyse_calendar_history.py`, 9 pass.

No commit here: sections 2-4 commit together at the section 4 marker (the table changes no
behavior until the migration uses it).

## 3. Extended bounds in session population

- [x] **3.1 `populate_trading_sessions` computes the extended bounds**
  - [x] `src/manta_trading/data/base/session_population.py`: each row gains
    `extended_open_utc` and `extended_close_utc`, built with `session_interval` from the
    calendar's `extended_open`/`extended_close` (a holiday's `extended_close_time` overrides
    the close), so each side gets its own UTC offset across DST.
  - [x] A calendar with `has_extended_hours = false` gets `None` for both. A calendar with
    `has_extended_hours = true` and a missing extended time raises `ValueError` naming the
    calendar (no silent fallback).
  - [x] `session_extension.py`: `_read_calendar` and `_read_holidays` read the new inputs, and
    the session insert writes the two columns, so `extend_calendar_sessions` and auto-extend
    write them for new rows. The CME dated-hours path is unchanged (its bounds stay `None`).
  Note: `populate_trading_sessions` adds the extended bounds only when the calendar row carries `has_extended_hours` (callers without it get the old rows), and `extend_calendar_sessions` writes the two columns only when `has_extended_columns(conn)` is true, so migrations 058 and 066 still replay on a fresh database before 068.
- [x] **3.2 Tests** (extend `test/unit/data/base/` session population tests)
  - [x] A full winter and a full summer NYSE day: extended bounds 09:00Z/01:00Z next day and
    08:00Z/00:00Z next day. An early close: extended close 22:00Z (winter). Both DST change
    weekends (2025-11-02, 2026-03-08): the sessions either side carry the right offsets.
  - [x] A CME calendar row yields `None` bounds; an extended calendar missing its extended
    time raises.
  Result: `test/unit/data/base/test_session_population_extended.py` (new file), 11 pass.

No commit here: section 3 writes columns that section 4's migration adds.

## 4. The calendar migration and the change plan

- [x] **4.1 The change plan (dry check core)**
  - [x] New module `src/manta_trading/market/schema/nyse_session_plan.py` with
    `plan_nyse_session_changes(conn) -> SessionChangePlan`: reads current NYSE and NASDAQ
    `trading_sessions`, computes the new rows in Python with `populate_trading_sessions` over
    the explicit table from the calendar's first session to `NYSE_HOLIDAYS_SEEDED_THROUGH`,
    and returns per calendar: `closed` (an existing session row the new data removes),
    `retimed` (an existing row whose open, close or extended close changes), `opened` (a new
    session date at or before the current horizon) and `appended` (a new session date past
    it). Writes nothing. Production's NYSE/NASDAQ rows already run to 2028-12-29 (read
    2026-11-19), so the 2027-2028 closures are `closed`, their early closes `retimed`, and
    `appended` is empty.
- [x] **4.2 The migration**
  - [x] Read the last id in `MINUTE_MIGRATIONS` (`market/schema/migrations/minute.py`) and
    use the next free id; do not trust a number from the design.
  - [x] Its `python_fn` lives in a new `market/schema/migrations/nyse_session_bounds.py`
    (as `cme_calendar_history.py` does; `minute.py` is already 127 KB), in the runner's one
    transaction:
    - [x] 1. add `extended_open_utc`, `extended_close_utc` (nullable `TIMESTAMPTZ`) to
      `trading_sessions` and `extended_close_time` (`TIME`) to `trading_holidays`;
    - [x] 2. replace the NYSE and NASDAQ `trading_holidays` rows with the explicit set;
    - [x] 3. set `holidays_seeded_through` to `NYSE_HOLIDAYS_SEEDED_THROUGH` for both;
    - [x] 4. upsert NYSE and NASDAQ `trading_sessions` through `populate_trading_sessions`
      (open, close, both extended bounds; `ON CONFLICT ... DO UPDATE`), and delete the
      session row of every date the plan closes;
    - [x] 5. refuse (raise, rolling back) if any NYSE/NASDAQ session row is left with a NULL
      extended bound.
  - [x] The `description` says what changes and that gap rows are reconciled by the cutover.
  Note: the id is `068_nyse_session_bounds` in `minute.py`; step 4 calls `extend_calendar_sessions` (which runs `populate_trading_sessions` and upserts with `ON CONFLICT ... DO UPDATE`) after deleting the plan's closed dates.
- [x] **4.3 Tests** (`test/integration/test_migration_nyse_session_bounds.py`, throwaway DB)
  - [x] Pinned plan: on a database migrated to the previous id, with NYSE/NASDAQ sessions
    populated through 2028-12-31 from the old rule data (production's shape), the plan's
    `opened`, `closed` and `retimed` equal `EXPECTED_SESSION_CHANGES` and `appended` is
    empty, for both calendars.
  - [x] After the migration: 2021-12-31 is a session, 2025-01-09 and every 2027-2028 closure
    in `EXPECTED_SESSION_CHANGES` are not, an early-close session has a 17:00 ET extended
    close, every NYSE/NASDAQ row has both extended bounds, `holidays_seeded_through` is
    2028-12-31 (design criterion 10, calendar part). A second plan after the migration is
    empty.
  - [x] `extend_calendar_sessions` after the migration writes extended bounds for new rows.
  Updated for 068: `test_schema_migrations` count 71 and `chain_ends_at_068` (068 callable stubbed like 066); 057/058 NYSE-unchanged check now runs through 067; the seeded-through test (`test_calendar_extension.py`) expects 2028-12-31; 066 populated-DB dump scoped to CME calendars. Result: 2 pass.

**Commit**: `feat: store extended session bounds and explicit NYSE holidays (#26)`

## 5. Gap reconciliation over the changed dates

- [x] **5.1 `reconcile_calendar_gaps`**
  - [x] New module `src/manta_trading/data/gaps/calendar_reconcile.py`:
    `reconcile_calendar_gaps(conn, dates) -> ReconcileReport`, called with
    `EXPECTED_SESSION_CHANGES.reconciled_dates()` (`opened` | `closed`; not the live plan,
    which is empty once the migration is applied). Every write goes through `update_data_gaps`;
    no hand-written gap SQL (design D8).
  - [x] Window: each date's own America/New_York day (`day_window`), never widened to the rows
    that intersect it (PM decision 2026-11-20; D8 amended).
  - [x] Closed date: for each symbol with an `acquisition_state` row and a gap row contained in
    the day, `update_data_gaps` runs with no unfilled status and `force_reset_terminal`, which
    deletes the contained rows, terminal (`PROVIDER_HOLE`, `RETRY_EXHAUSTED`) ones included. The
    reset is scoped to the day (D8 amended): a multi-day row that spans the day keeps its status
    and attempts.
  - [x] Opened date (2021-12-31): for each symbol with an `acquisition_state` row, no bar on the
    session (minute: a minute bar from open to close; daily: the daily bar at the date) and no
    gap row over the session, the writer seeds `UNKNOWN` rows. Minute passes `precomputed_ranges`
    built directly as the session (open to close) clamped by `clamp_to_lifecycle`, not through
    `compute_missing_minute_sessions`: the bar check is exact, and the coverage cagg's freshness
    guard does not apply here. Daily lets the writer call `compute_missing_ranges`, which is
    lifecycle-clamped.
  - [x] Candidates are the `eodhd` acquisition rows (`ProviderType.EODHD`) of each granularity.
    Each write (symbol, granularity, day) runs in its own transaction under `advisory_lock` with
    `DAEMON_LOCK_TIMEOUT`, as the daemons take it. The row's current `last_attempt_outcome` is
    passed. A NULL outcome (never attempted; about 1,300 `acquisition_state` rows on production)
    is skipped and counted in the report, not written.
  - [x] Outcome and `last_attempt_ts` of every symbol written are left as found: the writer stamps
    `last_attempt_ts` with now, so the reconciler restores the prior value in the same transaction
    (the daily pass reads it as "attempted this pass"; the minute pass orders by it).
  - [x] Report (`ReconcileReport`): gap-row totals before and after per (date, granularity), each
    symbol whose count changed (`GapCount`: before, after), and the skipped counts.
  PM 2026-11-20: day-window only (see D8 amendment). Also found: daily compute_missing_ranges compares session_open_utc to 00:00Z daily stamps, so it reports every daily session missing — filed as #83; the opened-date daily seed only runs for symbols verified to lack the bar.
- [x] **5.2 Tests** (`test/integration/test_calendar_gap_reconcile.py`, throwaway DB; 1 test, passes)
  - [x] Closed day 2025-01-09: for symbol HOLE, a single-session minute row and a single-day daily
    row (both `UNKNOWN`, contained in the day) are removed; for JUDGED, a single-session
    `PROVIDER_HOLE` minute row contained in the day is removed; a multi-day `PROVIDER_HOLE` minute
    row over 2025-01-08..10 and a row on an unrelated date (2024-06-03) keep their status and
    attempts.
  - [x] Opened day 2021-12-31: MISS (no bars that day) gains a minute and a daily row; HAS (bars)
    gains none; LATE (listed 2023-03-01) gains none; NEVER (NULL outcome) gains none and is counted
    as skipped.
  - [x] `last_attempt_outcome` and `last_attempt_ts` are unchanged for every symbol and granularity;
    a second call changes nothing (`ReconcileReport.is_noop()`).
  - [x] Criterion 10, gap part: the gap rows contained in each changed day equal what a fresh seed
    gives (no contained row left on 2025-01-09; the 2021-12-31 rows for MISS only). Multi-day
    terminal rows that span a changed day stay, as D8 keeps them, and are outside this comparison.

No commit here: sections 5 and 6 commit together at the section 6 marker.

## 6. The cutover script

- [x] **6.1 `scripts/cutover_174_calendar.py`**
  - [x] Read `scripts/cutover_301_calendar_history.py` first and follow it: one command, a
    log and a report, check-then-act, no prompts.
  - [x] Preflight: the ref exists here and on origin; the checkout is the dev root (`deploy/`
    and `.env` present); the checkout's `MT_TIMESCALE_DB_URL` names the same database as
    production's env file (`verify_same_database`).
  - [x] Dry check, before any timer or install, while 068 is pending: print the plan for both
    calendars and log the gap rows over each reconciled date; raise unless the plan equals
    `EXPECTED_SESSION_CHANGES`. Once 068 is applied it prints "already applied" and goes on.
  - [x] Hold the acquisition timers (`hold_timers`), each after its pass ends.
  - [x] Install the ref (`install`, which runs `deploy/install-production.sh --ref`): the
    installed checkout must be at the ref's commit, and `mt` must report the ref's version.
  - [x] Migrate, when 068 is pending: `migrate("minute", "068_nyse_session_bounds")`, which first
    checks that the maintenance URL targets production. Then the re-plan must be empty.
  - [x] Reconcile: `reconcile_calendar_gaps` over the pinned dates (5.1). A migration applied by
    another route is not applied again; the reconcile still runs.
  - [x] Report: `project-documents/user/notes/cutover-174-calendar-<stamp>.md` (plan or "already
    applied", migration id, gap rows before and after, changed symbols), with the narration in
    `cutover-<ref>-<stamp>.log` beside it. A re-run reports no gap row changes.
  - [x] Restart `mt-serve.service` if it is active, and wait until it answers `/health`.
  - [x] Verify: 2021-12-31 is a NYSE session and 2025-01-09 is not (`mt-run data calendars
    sessions`); `mt-run data health` and `mt-run data overview` exit 0.
  - [x] Release the timers that were held. After a failed migrate they stay stopped on purpose,
    and the log says so. Exit 0 only when every check passed; a failure exits 1 with its cause
    logged.
  Note: the dry check was also run read-only against production on 2026-11-20: plan equals
  `EXPECTED_SESSION_CHANGES` for both calendars (opened 2021-12-31, closed 20, retimed 5,
  appended 0).
- [x] **6.2 Test** (`test/integration/test_cutover_174_calendar.py`, throwaway DB at migration
  067 with production-shaped sessions from the old rule data; database steps only, host steps
  are not run): 3 tests, pass.
  - [x] First run: the dry check prints the plan, 068 applies, 2021-12-31 gains a minute and a
    daily row for the symbol with no bars that day, and that symbol's 2025-01-09 minute gap row
    is removed. The next dry check reports "already applied", and a second run changes no gap row.
  - [x] Migration applied alone through the migration runner first: the cutover does not apply it
    again, still reconciles, and reports "already applied before this run".
  - [x] A plan that differs from the pinned set (a 2024-03-04 NYSE session row deleted) makes the
    dry check raise `CutoverError` before any write: 068 is not applied and the gap rows are
    unchanged.

**Commit**: `feat: reconcile NYSE gap rows through a calendar cutover (#26)`

## 7. Adjustment by exchange-local date (design D5)

- [x] **7.1 `adjusted()` takes the timezone its dates are read in**
  - [x] `src/manta_trading/data/adjustment/_adjusted.py`: a required keyword
    `dates_in: ZoneInfo`; each bar's factor date is its index timestamp converted to that
    zone, then `.date()`. No default.
  - [x] `TimescaleMinuteDataDB` passes the symbol's calendar timezone (`instruments.
    trading_calendar_id` → `trading_calendars.timezone`, one lookup per read). A symbol with
    no calendar raises an error naming the symbol.
  - [x] `TimescaleDailyDataDB`: read how daily bars are stamped and pass the zone that keeps
    each bar's session date unchanged (UTC if stamped at 00:00 UTC), with a one-line comment.
    Daily values must not change.
  - [x] Update the seven existing `adjusted()` calls in
    `test/unit/data/adjustment/test_adjusted.py` to pass `dates_in`.
  Note: a symbol with bars but no instrument row (468 such orphans on production, 2026-11-20, mostly units and warrants) raises `UnknownSymbolError` on adjusted reads, which the API maps to its unknown-symbol 404; `adjusted=false` still serves them. An instrument without a calendar raises `ValueError` naming the symbol (none on production). Tested in `test_adjustment_local_date.py`.
- [x] **7.2 Tests**
  - [x] Unit (`test/unit/data/adjustment/test_adjusted.py`): a winter minute at 00:30Z on the
    ex-date's UTC date (19:30 ET the evening before) takes the previous day's factor; in
    summer, the 20:00 EDT close stamp (00:00Z on the ex-date's UTC date) does too; a daily
    frame is unchanged. Run both cases against the unfixed code first and confirm they fail.
  - [x] Integration (throwaway DB): pick a winter Tuesday-to-Friday ex-date from
    `corporate_actions` fixtures or seed one; every adjusted `1m` bar of the evening session
    before it, 19:00-19:59 ET included, uses that session's factor (criterion 7, `utc` part;
    the `extended` part is in section 9). In the same setup, the `utc` `1h` bucket at 00:00Z
    (the 19:00-19:59 ET minutes) uses that session's factor, not the ex-date's.

**Commit**: `feat: pick the intraday adjustment factor by exchange-local date (#28)`

## 8. Session phases, enums and estimates

- [x] **8.1 Enums and the pure phase module**
  - [x] `constants.py`: `SessionMode` (`utc`, `regular`, `extended`) and `SessionPhase`
    (`pre`, `regular`, `post`) as `StrEnum`s, each defined once.
  - [x] New `src/manta_trading/market/session_phases.py`: `SessionBounds` (session date, the
    four UTC bounds); `phases_for(bounds, mode)` returns the half-open phase intervals the
    mode reads (pre `[extended_open, open)`, regular `[open, close)`, post `[close,
    extended_close)`; design D2: a bar never crosses a phase); `assign(ts, bounds, mode,
    width)` returns a minute's phase and bucket start (`anchor + floor((ts - anchor) / width)
    * width`, anchor = phase start), or `None` outside the phases read; `estimate_bars(bounds_list,
    mode, width)` sums `ceil(phase_minutes / width)` per phase; `estimate_minutes(...)` sums
    phase lengths. An `extended` call on bounds with a `None` extended side raises.
- [x] **8.2 Tests** (`test/unit/market/test_session_phases.py`)
  - [x] Full session at 1h: regular 7, extended 17 (pre 6, regular 7, post 4); extended 4h 5;
    early close (13:00) regular 1h 4. Minutes: 390 regular, 960 extended, 210 regular early
    close. DST weekend sessions give the same counts.
  - [x] Bounds with a `None` extended side raise for `extended` and work for `regular`.
  Note: the one-sided case is covered by `test_one_missing_extended_side_raises`.
  - [x] `assign` at full-day, early-close and DST boundaries: 09:29 ET is pre (bucket 09:00),
    09:30 regular (bucket 09:30), 10:29 bucket 09:30, 15:59 bucket 15:30, 16:00 post; on an
    early close 12:59 bucket 12:30 and 13:00 post; the extended close instant is `None`; the
    same ET times either side of a DST change give buckets an hour apart in UTC.

No commit here: sections 8 and 9 commit together at the section 9 marker.

## 9. Session SQL and `get_session_bars`

- [x] **9.1 Extract the eligible OHLCV expressions**
  - [x] In `eligible_rollups_sql.py`, move the `FIRST`/`MAX`/`MIN`/`LAST`/`SUM ... FILTER`
    forms of `eligible_rollup_select` into a helper the session statement can call. No
    behavior change.
  - [x] Test: the rendered `eligible_rollup_select` text for each granularity equals the text
    rendered before the change; the existing rollup and value-parity tests pass.

**Commit**: `refactor: extract the eligible OHLCV expressions for reuse` (stage only this
task's files; section 8's files wait for the section 9 marker)

- [x] **9.2 The statement**
  - [x] New `src/manta_trading/market/session_bars_sql.py`: render one statement that joins
    the symbol's `trading_sessions` rows (session dates in `[start, end]`) to `minute_ohlcv`
    rows in the phases the mode reads, assigns each minute its phase and bucket
    (`anchor + floor((time - anchor) / width) * width`, anchor = phase start), and groups by
    session date, phase and bucket.
  Note: deviation from the literal text. The statement does not join `trading_sessions` itself: `session_bounds` reads those rows in Python, and `phases_for` passes the phases as arrays, so the phase rule has one definition.
  - [x] Prices and volume use `eligible_value` / `has_eligible_price` and the same
    `FIRST`/`MAX`/`MIN`/`LAST`/`SUM ... FILTER` forms through the 9.1 helper. `prints=all` renders
    raw columns. A bucket with no eligible price has no bar.
  - [x] Regular mode references only `session_open_utc`/`session_close_utc` (section 10
    measures it on production before the migration lands there).
- [x] **9.3 `TimescaleMinuteDataDB.get_session_bars`**
  - [x] Beside `get_minute_data`: `(symbol, granularity, mode, start, end, *, adjusted,
    prints)`, returning a frame indexed by bucket start with OHLCV plus `session_date` and
    `phase`. Width comes from the existing granularity-to-minutes table in `constants.py`.
  - [x] Raises `OutOfHorizonError` when `end` is past the calendar's `holidays_seeded_through`.
    An `extended` read over a session row with a NULL extended bound raises an error that
    names the symbol and date (logged as a 500 by the global handler).
  Note: the 500 log is not exercised at this layer; the route (12.2) is where it can be checked.
  - [x] Adjustment inside, through `adjusted(..., dates_in=<calendar tz>)`, as
    `get_minute_data` does.
- [x] **9.4 Integration tests** (`test/integration/test_session_bars.py`, throwaway DB with
  migrated calendar rows and seeded minutes)
  - [x] Criteria 1-4: regular 1h full session 7 bars from 09:30 ET with `phase=regular`;
    2025-11-28 4 bars, last 12:30 ET; extended 6/7/4 with no bar crossing 09:30 or the close;
    09:30 ET anchoring either side of both DST changes.
  - [x] Criterion 5: for at least two seeded symbols (one with off-tape marks), 1m, 5m, 15m, 1h
    and 4h, both modes, each bar equals the aggregate of the eligible 1m bars it covers (open,
    high, low, close, volume), including a bucket with an off-tape-marked minute and an
    all-marked bucket (absent).
  - [x] Every seeded minute's SQL phase and bucket equal `assign`'s.
  Note: `test_every_minute_placed_as_assign_places_it` checks every eligible 1m minute at extended, for AAA and BBB.
  - [x] Criterion 6: a minute seeded at the extended close instant is in no session bar, at
    `1m` with `extended` (as walkthrough step 6 reads it).
  - [x] With the connection's `TIME ZONE` set to `America/Denver` (production's), a session
    whose post-market minutes cross UTC midnight returns the same `session_date` on every bar
    as under UTC.
  Note: reads on plain psycopg connections, because the serving pool forces UTC (`_configure_connection`); each zone is checked with `SHOW TimeZone`.
  - [x] Criterion 7, `extended` part: the same post-market minute gets the same factor from
    `get_minute_data` and `get_session_bars`.
  - [x] Bar counts equal `estimate_bars` for full, early-close and DST sessions (keeps the SQL
    and the estimator in agreement). `prints=all` returns raw values. Past the horizon raises
    `OutOfHorizonError`; a NULL extended bound raises.

**Commit**: `feat: build session-aligned equity bars at read time`

## 10. The session minute ceiling, measured on production

Done before anything reads the ceiling, so no commit carries an unmeasured default.

- [x] **10.1 Measure and set the default** (design D6, admission)
  - [x] A one-off script outside the repo, run with `uv run python`, URLs from `Settings`, one
    read-only transaction on production with the serving statement timeout. It writes nothing.
  - [x] Symbol: the one with the most regular-hours minute rows over the last year (from the
    coverage cagg). Time `get_session_bars` regular 1h over windows of 3, 6, 12, 24 and 36
    months ending at the latest held session, 20 runs each; record each window's minute
    estimate (`estimate_minutes`) and its p95. A run cancelled by the statement timeout
    counts as over 5 s.
  - [x] Default rule: the largest measured minute count whose p95 is within 5 s (a quarter of
    the 20 s timeout), rounded down to a round number. It must admit the 12-month window,
    whose p95 must be under 2 s (the load-test bound for the same read in 13.1). If either
    fails, write `BLOCKED:` with the timings, stop tasks 11, 12.2-12.3 and 13 and the
    measured numbers in 14.1, and continue everything else.
  - [x] Add `MAX_SESSION_MINUTES_PER_REQUEST` to `constants.py` beside
    `API_MAX_BARS_PER_REQUEST`, its docstring recording the date, symbol, windows and timings
    and naming `MT_API_MAX_SESSION_MINUTES`; add `api_max_session_minutes` to `Settings`
    (`config/__init__.py`, `gt=0`) and a dependency beside `get_max_bars`.
  - [x] Write `user/notes/174-session-minute-measurement.md` (front matter as the 309 note):
    procedure, table, the default chosen.
  - [ ] If production is unreachable, write `BLOCKED:` here, with the same stop list as above. (not needed: production was reachable)

No commit here: sections 10 and 11 commit together at the section 11 marker.

## 11. Session admission

- [x] **11.1 Refusals and estimates**
  - [x] `RefusalReason` gains `CALENDAR_HORIZON`, `MINUTE_CEILING`, `SESSION_GRANULARITY`.
  - [x] Static checks in `bars_admission.check_request` (no DB): `session` other than `utc`
    with `1d`, `1w`, `1mo`, `1q` → 422 `session_granularity`; `prints=all` allowed at every
    intraday size when `session` is not `utc`, unchanged (1m only) for `utc`.
  - [x] Session checks in a new `api_server/routes/bars_session_admission.py`, run in the
    executor after the static checks: look up the symbol's calendar (unknown symbol → the
    existing 404); `end` past `holidays_seeded_through` → 422 `calendar_horizon` with the
    bound; read the range's session bounds once and refuse over the row ceiling
    (`estimate_bars`, `row_ceiling` as today) or over the minute ceiling (`estimate_minutes`,
    422 `minute_ceiling` with `limit` and `estimate`). The 186 estimator stays for `utc`.
    Note: `admit_session_request` has no caller yet. `bars.py` still passes `SessionMode.UTC`
    to `check_request`; section 12 adds the `session` parameter and the executor call.
  - [x] With `limit`: unchanged (design D6), only the existing `limit` ≤ row ceiling check. A
    minute bound for session `limit` reads is deferred to issue #82.
- [x] **11.2 Tests** (`test/unit/api_server/test_bars_session_admission.py`, plus an
  integration test for the DB-backed checks)
  - [x] Each refusal's status, `reason` and fields; `prints=all` at 1h with `regular` admitted
    and at 1h with `utc` refused; a range ending 2028-12-31 admitted and 2029-01-02 refused.

**Commit**: `feat: admit session bar requests by minute ceiling and horizon`

## 12. Response models and the route

- [x] **12.1 Models** (`api_server/models/responses.py`)
  - [x] `SessionBarRecord` (`BarRecord` plus `session_date`, `phase: SessionPhase`) and a
    session response model whose bars are `SessionBarRecord`, built `from_dataframe` like
    `BarsResponse`. `BarsResponse`/`BarRecord` unchanged (design D9: session fields only on
    session responses).
  - [x] `AppliedEquityBarParams` gains `session: SessionMode`.
  Note: deviation. Session models are in `models/session_responses.py`, not `responses.py`: `responses.py` is already 369 lines at HEAD (371 now), over the ~300-line guideline. `BarsResponse`/`BarRecord` unchanged.
- [x] **12.2 Route** (`api_server/routes/bars.py`)
  - [x] `session: SessionMode = SessionMode.UTC` query parameter; its description says that in
    session mode `start`/`end` are session dates, and with `utc` UTC dates.
  - [x] `_fetcher` calls `get_session_bars` when `session` is not `utc`; the route stays a thin
    wrapper. No freshness probe in session mode (`is_stale` false, as for `1m`). `limit`
    reuses the same `fetch` closure.
  - [x] `responses=` declares both 200 bodies, so `openapi.json` shows both.
  Note: `routes/bars.py` grew from 255 to 305 lines, over the ~300-line guideline. Flagged, not split.
- [x] **12.3 Tests** (`test/unit/api_server/test_bars_session.py`, integration in
  `test/integration/api_server/`)
  - [x] Criterion 8: with no `session`, the body equals today's plus `applied.session = "utc"`,
    with no `session_date`/`phase` keys; the existing bars tests pass unchanged apart from
    `applied`. The test that pins `applied` keys to the route's parameters passes.
  - [x] Session responses carry `session_date` and `phase`; criterion 9's three refusals
    through the route; `limit` with `regular` returns the latest N session bars.
  Note: deviation. Route tests are integration tests in `test/integration/api_server/test_bars_session.py`, not the unit file `test/unit/api_server/test_bars_session.py` named in the heading. Reason: route behaviour needs session rows, so the tests run through the real app.

**Commit**: `feat: serve session-aligned bars on /api/v1/bars`

## 13. Load test

- [ ] **13.1 One-year regular 1h read** (`test/load/test_174_session_bars_nfr.py`, template
  `test/load/test_309_time_bars_nfr.py`)
  - [ ] Entry point: the HTTP route through `httpx.ASGITransport` against `create_app` on a
    test-cluster database, with the shipped minute ceiling, as the template does. Seed a year
    of SPY minutes; run the one-year `1h` `regular` read repeatedly while polling
    `/api/v1/health`.
  - [ ] Pass: the read is admitted, its p95 is under 2 s (20 s statement timeout as the hard
    limit), and `/health` p95 is under 0.5 s during it. Record the numbers in the 174
    measurement note.
  - [ ] Gate: `MT_RUN_LOAD_TESTS=1`, and the docstring gives the manual command, as the
    template does. CI runs no test job and GitHub-hosted runners cannot reach the test
    cluster. No CI wiring task here: the gate is deferred to issue #32 (and slice 907), and
    this test is recorded on #32.

**Commit**: `test: add the session bars load test`

## 14. Docs, OpenAPI, architecture and changelog

- [ ] **14.1 API references and OpenAPI**
  - [ ] `docs/api/reference.md` and `docs/api/agents.md`: `session` and its values; session
    dates in session mode; `session_date`, `phase`; the short last bar of a phase (its
    `timestamp` is its start); the three new 422 reasons; the close-stamp bar from the section
    1 note and what it means for regular-session volume; the D5 adjustment rule.
  - [ ] Regenerate `docs/api/openapi.json` as earlier slices did; `scripts/check_api_docs.py`
    and `test/integration/test_api_docs_examples.py` pass.
- [ ] **14.2 Architecture amendments** (design Included item 6)
  - [ ] `140-arch.data-quality-operations.md`: equity caggs section (session reads bypass the
    caggs); the `adjusted()` date rule (exchange-local date); the `prints=all` rule (every
    intraday size in session mode); the calendar contract (new `trading_sessions` and
    `trading_holidays` columns, explicit NYSE/NASDAQ rows).
  - [ ] `180-arch.data-serving.md`: the bars contract (`session`, `session_date`, `phase`, the
    new refusals, the session ceiling).
- [ ] **14.3 CHANGELOG** (Unreleased): the feature, the #26 calendar fix, and the D5
  correction called out as a client-visible change to winter post-market adjusted values.

**Commit**: `docs: document session-aligned equity bars and amend 140 and 180`

## 15. Full validation

- [ ] **15.1 Checks**
  - [ ] Unit, integration and load tiers run separately; no failure beyond the known
    pre-existing ones (each re-run in isolation).
  - [ ] `ruff check`, `ruff format --check` and mypy on touched files add no errors over main.
  - [ ] Walk the design's success criteria 1-11 and confirm each maps to a passing test or a
    written note; list any that does not under this task (criterion 11 when the 1.1 finding
    is "not established").

**Commit**: `test: validate slice 174` (only if this section changed files)

## Phase 7 (not in this file)

Release, the cutover, the production walkthrough and telling clients follow the `/release`
skill and the design's Verification Walkthrough after the code review passes. The cutover is
`uv run python scripts/cutover_174_calendar.py vX.Y.Z` (the `/release` new-migration step);
it applies the migration itself, so `mt data migrate apply` is not run first. Confirm the UI
agent's name (the design's mt-ui) with `ListAgents`. Then close #26 and comment on #28 that
its intraday half is done.
