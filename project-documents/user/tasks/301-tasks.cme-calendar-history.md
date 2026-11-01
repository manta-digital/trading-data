---
docType: tasks
slice: cme-calendar-history
project: trading-data
lld: user/slices/301-slice.cme-calendar-history.md
dependencies: []
projectState: CME_EQUITY and CME_METALS sessions start 2020-01-01; last primary migration
  is 065_instruments_name; 058/059 seed the 2020 range through extend_calendar_sessions
dateCreated: 20261101
dateUpdated: 20261101
status: in_progress
turns: 15
promptTokens: 826489
cachedTokens: 753110
cacheWriteTokens: 73349
completionTokens: 20109
durationSeconds: 137.9
costUsd: 0.570153
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.5701529000000001
    completionTokens: 20109
revision_number: 2
---

## Context Summary

- Working on the `cme-calendar-history` slice (301). Design: `user/slices/301-slice.cme-calendar-history.md`. Decision references (D1–D9) point into it.
- Today `CME_EQUITY` (ES) and `CME_METALS` (GC) hold sessions from 2020-01-01. Databento CME data starts 2010-06-06, so pre-2020 instants raise `OutOfPopulatedRangeError`.
- This slice adds dated regular-hours eras (`trading_calendar_hours`), sourced pre-2020 exceptions, era-aware session extension, one primary-track migration, a free `ohlcv-1m` count check, and a cutover script.
- Nothing is bought. Sessions dated 2020-01-01 or later stay identical, and the migration proves it.
- Ground rules: an era or exception is entered only from a dated CME document (never a plausible value, never a forum lead). Counts are evidence, not a source. `ruff format` touched files only, and check `git diff` for swept lines before each commit. Code modules stay within about 300 lines.
- Run tests with the test DB URL exported from `.env`. Run mypy with the kalshi src paths and tests in one invocation (narrower runs report false errors).
- Next slices: 302 and 303 (place 2010–2019 data in sessions), then 304 and 307 (ingest).
- Overall relative effort: 3/5.

## Tasks

### 1. Source the hours eras (D1, D2)

- [x] **1.1 Source ES regular-hours eras, 2010-06-06 to 2019-12-31**
  - [x] Write everything sourced here into a new "Era sources" section of the slice design as you go (one row per era: `effective_from`, open, close, Sunday open if different, citation URL and snapshot date). Task 2.1 enters the seed from that section only, so it must be complete enough to work from in a fresh session.
  - [x] First read `src/manta_trading/market/schema/seed_cme_calendar.py` and `seed_cme_metals_calendar.py` (docstrings, `CmeCalendarSeed`, `closed_day`, `early_close_day`, the `# source:` comment format) and how 221 and 232 cited Wayback snapshots. Reuse that citation format. Today's hours live in the `trading_calendars` rows seeded by migrations 058 and 059.
  - [x] Find dated CME documents (notice, Special Executive Report, or a contract-spec page on Wayback whose snapshot date bounds the change) giving ES open and close in each era.
  - [x] For each era, establish whether the Sunday open differs from the daily reopen. Record the answer with its source.
  - [x] Record each era as: `effective_from` (the session date the era first closes on), open, close (America/Chicago), source citation.
  - [x] A forum post or other undated lead about an hours change is not a source. Enter a change only if a dated document confirms it.
  - [x] Note any documented hours change on or after 2020-01-01 as a finding. Do not enter it.
  - [x] Success: the "Era sources" section lists every ES era with a dated document. Any era with no document is listed there as unsourced.

- [x] **1.2 Source GC regular-hours eras, 2010-06-06 to 2019-12-31**
  - [x] Same method as 1.1, from the metals product-group documents, written into the same "Era sources" section. Do not copy from ES.
  - [x] Success: the "Era sources" section lists every GC era with its source and the Sunday-open answer, or lists the era as unsourced.

- [x] **1.3 Decide the table shape and `CME_DATASET_START`**
  - [x] If 1.1 or 1.2 found an era whose Sunday open differs from its daily reopen, D1 is reopened: add the Sunday-open column to the D1 schema in the slice design and note it in the slice document. Stop coding against the table until that edit is in the document.
  - [x] If no difference was found, the table keeps the D1 columns.
  - [x] If any era is unsourced, `CME_DATASET_START` moves later to the `effective_from` of the earliest sourced era (D5). Write the unsupported range in a new "Findings" section of the slice design.
  - [x] Write the Findings section header now, with entries for: hours changes from 2020 on (D2), unsourced eras and the resulting start (D5), and unresolved count findings (D8, filled in later).
  - [x] Success: the table columns and `CME_DATASET_START` are fixed and written down in the slice design.

  Sunday open differs for ES before 2012-11-19 (SER-6465); `sunday_open time NOT NULL` added to D1. `CME_DATASET_START` stays 2010-06-06.

**Commit**: `docs: record 301 sourced CME hours eras and table shape`

### 2. Era seed module (D1, D2, D5)

- [x] **2.1 Create `market/schema/seed_cme_hours.py`**
  - [x] Define `CME_DATASET_START` once, with the value fixed in 1.3.
  - [x] Define an era dataclass (calendar id, `effective_from`, open, close, source) and the era tuples for `CME_EQUITY` and `CME_METALS`, entered from the slice design's "Era sources" section. The latest era of each carries today's hours.
  - [x] Every era has a non-empty `source` citing its document.
  - [x] If 1.3 added a Sunday-open column, the era dataclass carries it too.
  - [x] Success: module imports, under about 300 lines, contains no hours that lack a source.

- [x] **2.2 Tests for the era seed**
  - [x] Add `test/unit/test_seed_cme_hours.py`.
  - [x] Every era has a non-empty source that names a URL.
  - [x] Each calendar's earliest era starts on or before `CME_DATASET_START`.
  - [x] Each calendar's latest era equals today's `trading_calendars` hours (the values the 058 and 059 seeds insert).
  - [x] No era starts on or after 2020-01-01 except the current era when the last pre-2020 change took effect on the first 2020 session.
  - [x] Success: tests pass.

**Commit**: `feat: add CME hours era seed and dataset start constant`

### 3. Pin 058/059 output before touching extension (D7)

- [x] **3.1 Capture the snapshot**
  - [x] On the unmodified `session_extension.py`, apply the migration chain to 059 on a fresh test database and export every `CME_EQUITY` and `CME_METALS` row from `trading_sessions` and `trading_holidays` to a snapshot fixture file under the test fixtures directory.
  - [x] Success: fixture file committed, produced before any change to `session_extension.py`.

- [x] **3.2 Add `test_058_059_fresh_output_unchanged`**
  - [x] Add it to `test/integration/test_migration_057_058_cme_calendar.py`. It applies the chain to 059 on a fresh database and compares all CME session and holiday rows to the snapshot.
  - [x] Success: passes on the current code.

**Commit**: `test: pin 058/059 fresh-database CME output before era extension`

### 4. Era-aware extension (D3)

- [x] **4.1 Add `_is_dated` and `NoHoursEraError`**
  - [x] First read `src/manta_trading/data/base/session_extension.py` (`extend_calendar_sessions`, `_read_calendar`), `session_population.py` (`populate_trading_sessions`, `session_interval`), and how `minute.py` introspects `information_schema.columns`. Confirm no caller of `extend_calendar_sessions` needs an edit.
  - [x] `_is_dated(conn, calendar_id)` first asks `information_schema.columns` whether `trading_calendars.hours_dated` exists. If absent, return false without reading it. If present, return the row's flag.
  - [x] Define `NoHoursEraError`, whose message names the calendar and the date.
  - [x] Success: no change to the existing `_read_calendar` `SELECT`, so 058 and 059 on a fresh database still run.

- [x] **4.2 Era reader and segmentation**
  - [x] For a dated calendar, read eras from `trading_calendar_hours` only. No era rows raises. A session date before the first era raises `NoHoursEraError`. There is no fallback to `trading_calendars` hours.
  - [x] Cut `[start, effective_end]` at each era `effective_from` and call `populate_trading_sessions` once per segment with a calendar row carrying that era's open and close.
  - [x] If 1.3 added a Sunday-open column, read it with the era and use it for the session that opens on Sunday, with a unit test in 4.3.
  - [x] Non-dated calendars take the existing single-segment path unchanged. `populate_trading_sessions` and `session_interval` are not edited.
  - [x] Keep the module within about 300 lines. Extract a helper module if it does not fit.
  - [x] Success: code written, mypy clean on the touched file.

- [x] **4.3 Unit tests for extension**
  - [x] Segmentation: a range before the first era (raises `NoHoursEraError`; assert the message contains the calendar id and the date), a range starting on an era's first date, a range across a change, and a late-open exception overriding the era's open.
  - [x] A dated calendar with no era rows raises.
  - [ ] `_is_dated` is false, without error, on a database lacking the `hours_dated` column. After setting the flag it is true for `CME_EQUITY` and `CME_METALS` and false for NYSE and NASDAQ.
    Note: True-after-flag half moves to 7.3 (needs the migration's column).
  - [x] NYSE, NASDAQ and the current CME hours produce output equal to the pre-change output.
  - [x] Success: tests pass and `test_058_059_fresh_output_unchanged` still passes.

**Commit**: `feat: split session extension at dated hours era boundaries`

### 5. Free count check script (D8)

- [ ] **5.1 Create `scripts/check_cme_history_counts.py`**
  - [ ] First confirm from the databento-python source (not the docs site) the `metadata.get_record_count` window semantics (end exclusive, nanosecond precision) and its timeout setting, and read `data/tick/databento/adapter.py` for its `get_record_count` wrapper and API key loading. Write the semantics in the script's header comment.
  - [ ] Connection: sessions come from `TradingCalendar.sessions_between` against the database `MT_TIMESCALE_DB_URL` points to, as in `scripts/verify_cme_sessions.py`. The Databento key is loaded the way the adapter loads it. The script only reads.
  - [ ] Arguments: `--calendar`, `--parent`, optional `--start`, `--end`.
  - [ ] Read the calendar's sessions in range. Per session make two calls on `GLBX.MDP3`, `ohlcv-1m`, `stype_in=parent`: inside `[open, close)` expecting more than 0, and in the gap `[close, next_open)` expecting 0.
  - [ ] Print one line per finding with its window and count. Exit non-zero on any finding or failed call.
  - [ ] A call that raises prints as a failed session with its error and is never counted as zero. Authentication and bad-request errors stop the run. Set the metadata timeout explicitly. No retry, no resume state, no concurrency.
  - [ ] Keep the script plain: it is the record of how the evidence was gathered, not a tool.
  - [ ] Success: `--help` works and the script runs against a few sessions.

- [ ] **5.2 Calibration run on 2020-onward sessions**
  - [ ] Using the database `MT_TIMESCALE_DB_URL` already points to (read-only), run the script for each calendar over a recent range of existing sessions (for example one year each, using `--start` and `--end`).
  - [ ] Success: no findings expected. If findings appear, record each in the Findings section of the slice design with date, window and count, and decide whether the script or the window semantics is wrong before continuing.

**Commit**: `feat: add free CME history count check script`

### 6. Pre-2020 exceptions (D4)

- [ ] **6.1 Source CME_EQUITY exceptions, 2010-06-06 to 2014-12-31**
  - [ ] Per year, read the CME Globex holiday documents for the ES product-group row. Cover holiday closures, early closes, late opens, and unscheduled closures as each document states for equity.
  - [ ] Convert ET-stated times to America/Chicago and say so in the comment.
  - [ ] Success: a working list of dated rows, each with a document, product-group row and URL.
  - [ ] A year with no document: record it as unresolved in the Findings section and move `CME_DATASET_START` (and the era seed) to the first date after the latest unsourced year, so supported history stays contiguous up to 2020 (D5). Never enter a guessed row.

- [ ] **6.2 Source CME_EQUITY exceptions, 2015-01-01 to 2019-12-31**
  - [ ] Same method and success criteria as 6.1, including its unsourced-year rule: a year with no document moves `CME_DATASET_START` and updates the era seed and its task 2 tests.

- [ ] **6.3 Create `market/schema/seed_cme_equity_history.py`**
  - [ ] Enter each row from 6.1 and 6.2 with `closed_day` or `early_close_day` from `seed_cme_calendar.py`, each with a `# source:` comment in the existing format. Add a `late_open_day` helper only if a late open was found.
  - [ ] Success: module imports and stays within about 300 lines, or is split by year range.

- [ ] **6.4 Tests for the equity history seed**
  - [ ] Every row has a non-empty source, a date in `[CME_DATASET_START, 2020-01-01)`, and no duplicate date.
  - [ ] Success: tests pass.

**Commit**: `feat: add sourced pre-2020 CME_EQUITY exception seed`

- [ ] **6.5 Source CME_METALS exceptions, 2010-06-06 to 2014-12-31**
  - [ ] Same method as 6.1 from the metals product-group row. Metals differ from equity and between years (see the `seed_cme_metals_calendar.py` docstring). Copy nothing from equity.
  - [ ] Success: same as 6.1, including its unsourced-year rule: a year with no document moves `CME_DATASET_START` and updates the era seed and its task 2 tests.

- [ ] **6.6 Source CME_METALS exceptions, 2015-01-01 to 2019-12-31**
  - [ ] Same method and success criteria as 6.5, including the unsourced-year rule: a year with no document moves `CME_DATASET_START` and updates the era seed and its task 2 tests.

- [ ] **6.7 Create `market/schema/seed_cme_metals_history.py`**
  - [ ] Same pattern as 6.3, from the rows in 6.5 and 6.6.
  - [ ] Success: module imports and stays within about 300 lines.

- [ ] **6.8 Tests for the metals history seed**
  - [ ] Same checks as 6.4 for `CME_METALS`.
  - [ ] Success: tests pass.

**Commit**: `feat: add sourced pre-2020 CME_METALS exception seed`

### 7. The migration (D6)

- [ ] **7.1 Add the migration**
  - [ ] First read the last id in `MINUTE_MIGRATIONS` in `market/schema/migrations/minute.py` and migration 058's `python_fn` style. The new id is the next free one after what the file says.
  - [ ] Implement the six steps of the design's Data Flow in one `python_fn`:
    1. Abort, naming the counts, if any `CME_EQUITY` or `CME_METALS` row exists before 2020-01-01 in `trading_holidays` or `trading_sessions`.
    2. Read every CME session dated 2020-01-01 or later into memory.
    3. Create `trading_calendar_hours` (columns as fixed in 1.3, including any Sunday-open column), add `trading_calendars.hours_dated boolean NOT NULL DEFAULT false`, set it true for both CME calendars, then plain `INSERT` (no `ON CONFLICT`) the eras and the pre-2020 exceptions.
    4. Abort if a calendar's latest era differs from its `trading_calendars` hours.
    5. `extend_calendar_sessions(conn, cal, start=CME_DATASET_START, end=date(2019, 12, 31))` for each calendar.
    6. Re-read the 2020-onward sessions and abort on any difference from step 2.
  - [ ] Put the migration body in its own module if `minute.py` would grow past reason, and register it in `MINUTE_MIGRATIONS`.
  - [ ] Success: the migration runs on a fresh test database through the full chain.

- [ ] **7.2 Update chain tests that name the last id**
  - [ ] Update `test_schema_migrations.py` and any other test that asserts the last migration id.
  - [ ] Success: those tests pass.

- [ ] **7.3 Integration tests: fresh database**
  - [ ] Fresh full chain: both calendars' first session is the first one on or after `CME_DATASET_START`, eras present with sources, `hours_dated` true for both CME calendars and false for NYSE and NASDAQ.
  - [ ] Session continuity (success criterion 1): for each calendar, consecutive pre-2020 session dates have no gap except weekends and dates with a `trading_holidays` closure row.
  - [ ] Forward-extending a CME calendar after the migration produces exactly what it produced before.
  - [ ] Boundary instants, in the pattern of `test/unit/data/base/test_cme_equity_boundaries.py` and its JSON fixture: `locate_sessions` places an instant inside a pre-2020 era's hours and refuses one in its daily break with `session_boundary`, for each calendar on both sides of each era change.
  - [ ] Success: all pass.

- [ ] **7.4 Integration tests: populated database and aborts**
  - [ ] A database populated as production is (2020-onward CME sessions present): migration applies and the 2020-onward rows are identical afterwards.
  - [ ] Each precondition aborts with a clear message: pre-2020 holiday row, pre-2020 session row.
  - [ ] The 2020-onward comparison aborts when a 2020 session is changed during the migration (for example by seeding a deliberately wrong era).
  - [ ] A latest era that differs from the calendar row aborts.
  - [ ] Success: all pass. Re-run a failure in isolation before investigating (known flakes: see the integration-tier notes).

**Commit**: `feat: add CME calendar history migration with 2020-onward proof`

### 8. Run the count check over the pre-2020 range (D8)

- [ ] **8.1 Run both calendars on a migrated test database**
  - [ ] Create a throwaway database on the test cluster, apply the full migration chain to it with the same helper the task 7 integration tests use, and set `MT_TIMESCALE_DB_URL` to it for the run. Do not use production, which has no pre-2020 sessions until the cutover.
  - [ ] Run `check_cme_history_counts.py` for `CME_EQUITY` with `ES.FUT` and `CME_METALS` with `GC.FUT` over 2010-06-07 to 2019-12-31, split by year with `--start` and `--end`. Rerun only failed or remaining ranges.
  - [ ] Success: every year of both calendars has run to completion, with every failed call rerun.

- [ ] **8.2 Resolve or record each finding**
  - [ ] For each finding, look for a dated CME document. If found, add the sourced row (equity or metals history seed) or era, extend the tests, and rerun that window.
  - [ ] If no document resolves it, record it in the slice design's Findings section as unresolved, with date, window and count. Never add a row that counts alone suggest.
  - [ ] If a finding leaves a whole early era unsourceable, move `CME_DATASET_START` later per D5 and update the era seed, the migration inputs and the tests.
  - [ ] After any seed change, rerun the unit and integration tests from tasks 2, 6 and 7; all pass.
  - [ ] Success: the script exits 0, or every remaining finding has an entry in the Findings section. The Findings section also states any 2020-onward hours change (D2) and the final `CME_DATASET_START`.

**Commit**: `feat: apply count-check findings to CME history seeds and record unresolved ones`

### 9. Cutover script (D9)

- [ ] **9.1 Create `scripts/cutover_301_calendar_history.py`**
  - [ ] First read `scripts/cutover_0244_symbol_names.py`, `scripts/cutover_common.py` (`preflight`, timer hold, `install`, `migrate`) and the `upgrade_production.py` helpers.
  - [ ] Steps: preflight, hold timers, install the release, migrate the primary track to the new migration id with the target verified, verify, release timers.
  - [ ] Timers are released as found after a failed verify and stay held after a failed migrate. The narration says so, and a rerun after the fix releases them.
  - [ ] Verify: both CME calendars' first session is dated (or the first session on or after the final `CME_DATASET_START`), `sudo mt-run data health` passes, and `mt data overview` exits 0.
  - [ ] No new mechanism and no root or PM host step beyond what `cutover_common` already uses.
  - [ ] Success: `--help` works and the script type-checks.

- [ ] **9.2 Test the script's decision logic**
  - [ ] Add `test/unit/test_cutover_301.py` in the pattern of `test/unit/test_cutover_234.py` (every cutover script ships one; `cutover_common` does not test timer release). Cover: verify fails when a CME calendar's first session is not the expected date, and timers are released when verify fails and stay held when migrate fails.
  - [ ] Success: tests pass.

**Commit**: `feat: add 301 calendar history cutover script`

### 10. Final validation

- [ ] **10.1 Quality gates**
  - [ ] `ruff check` and `ruff format` on touched files only. Run `git diff` against the target (`cf config get git.integration_branch`, or `main` if empty) and check for swept or deleted pre-existing lines.
  - [ ] mypy in one invocation with the kalshi src paths and tests.
  - [ ] Success: all clean. No touched code module exceeds about 300 lines.

- [ ] **10.2 Verification walkthrough**
  - [ ] Run the slice's Verification Walkthrough steps 1 to 4: unit tests, integration tests (`-k "calendar or session or migration"`), the count check result from task 8, the migrated-database queries, and `verify_cme_sessions.py` on the held 2024 job with output identical to before.
  - [ ] Success: all pass, and the Findings section is complete.

**Commit**: `docs: complete 301 findings and verification notes`
