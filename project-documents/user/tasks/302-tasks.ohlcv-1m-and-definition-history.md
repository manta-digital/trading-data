---
docType: tasks
slice: ohlcv-1m-and-definition-history
project: trading-data
lld: user/slices/302-slice.ohlcv-1m-and-definition-history.md
dependencies:
- 300
- 301
projectState: 300 (archive generalisation) and 301 (CME calendar history) are merged;
  last tick migration is tick_008_plan_inclusion; ohlcv-1m is not an archived schema;
  production universe has no history wants
dateCreated: 20261103
dateUpdated: 20261103
status: not_started
turns: 20
promptTokens: 1319940
cachedTokens: 1233879
cacheWriteTokens: 86023
completionTokens: 19649
durationSeconds: 138.3
costUsd: 0.664349
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.6643493000000003
    completionTokens: 19649
revision_number: 2
---

## Context Summary

- Working on the `ohlcv-1m-and-definition-history` slice (302). Design: `user/slices/302-slice.ohlcv-1m-and-definition-history.md`. Decision references (D1–D11) point into it.
- This slice adds Databento `ohlcv-1m` as an archive schema in its own family (`BAR_1M`), stored in a new hypertable `tick_ohlcv_1m` with its own ledger `tick_bar_ledger`. It adds universe `HistoryWant`s for `ohlcv-1m` and `definition`, and reports the bar tier in `mt data tick status` and `coverage`.
- Nothing is bought. Code is built on the DBN project's sample files and one derived real-shape bar day computed from the held 2024-12-03 `tbbo` file. Production configures no history wants (`history=()`), so the production pass plans exactly what it plans today.
- Excluded: any purchase, `statistics` (303), `health`/`accounting`/`overview` (305), serving bars through the API (306, D11), `ohlcv-1s`.
- Ground rules: SQL fragments built from constants are written in code and every value is bound. A failing unit fails loudly; no silent fallbacks. Source files stay within about 300 lines (`bar_constants.py` holds the bar storage constants, not `constants.py`). Run `ruff format` on touched files only and check `git diff` for swept lines before each commit. Run mypy with the kalshi src paths and tests in one invocation. Export the test DB URL from `.env` before running integration tests.
- Known pre-existing failures to ignore: see memory notes on migration 051/052 chain tests and the test cluster's missing TimescaleDB scheduler.
- Next slices: 303 (statistics, open-interest roll rule), then 304 (first purchase).
- Overall relative effort: 4/5.

## Tasks

### 1. Schema, family, plan row (D1)

- [x] **1.1 Add `TickSchema.OHLCV_1M` and archived membership**
  - [x] Read `src/manta_trading/data/tick/constants.py` and `test/unit/data/tick/test_constants.py` first.
  - [x] Add `TickSchema.OHLCV_1M = "ohlcv-1m"` only. Do not add it to `ARCHIVED_SCHEMAS` yet (task 3.2 does that with the migration, so the constant never runs ahead of the database check constraint) or to `ESTIMATE_SCHEMAS` (task 11.1 does that with its row and tests, so `mt data tick estimate` output does not change before then). Do not touch `STORED_TIERS`, `TICK_TIERS` or `COMPANION_SCHEMAS`.
  - [x] Success: the existing constants tests pass (update any that enumerate the schemas), and `OHLCV_1M` exists and is in no schema collection.

- [x] **1.2 Add `SchemaFamily.BAR_1M` and `FAMILY_LEDGER`**
  - [x] In `schema_families.py`, add `BAR_1M: (OHLCV_1M,)` to `FAMILY_SCHEMAS` and a `FAMILY_LEDGER` mapping: `TICK_TIER` to `tick_ingest_ledger`, `BAR_1M` to `tick_bar_ledger`. `DEFINITION` is deliberately absent (projected by the acquisition pass).
  - [x] Success: families stay total and disjoint over `TickSchema`.
  
  Note: `BAR_LEDGER_TABLE` is created in `bar_constants.py` here (3.1 adds the rest); `TICK_LEDGER_TABLE` is in `constants.py`.

- [x] **1.3 Add the plan-inclusion row**
  - [x] In `config/databento_plans.py` set `STANDARD[OHLCV_1M] = None` (all history).
  - [x] Success: the plan table is total over `TickSchema`; `is_plan_included` answers for `ohlcv-1m`.

- [x] **1.4 Tests for 1.1–1.3** (extend `test_constants.py`, `test_schema_families.py`, `test_plan_inclusion.py`)
  - [x] Families total and disjoint; `BAR_1M` holds `ohlcv-1m` only.
  - [x] `FAMILY_LEDGER` has a key for every family that is a key of `INGEST_PROJECTORS`'s schemas (`TICK_TIER`, `BAR_1M`) and none for `DEFINITION`.
  - [x] Plan table total over `TickSchema`.
  - [x] Success: `uv run pytest test/unit/data/tick -q` passes.

  Note: two existing tests changed because the reader now accepts ohlcv-1m: `test_schema_values_are_provider_spellings` lists it, and `test_unknown_schema_raises_naming_it` uses an ohlcv-1s header.

**Commit**: `feat: add ohlcv-1m schema, BAR_1M family and plan row`

### 2. Reader fixtures and decode tests (D7, part one)

- [x] **2.1 Copy the DBN samples and record them**
  - [x] Fetch `test_data.ohlcv-1m.v1.dbn.zst` and `test_data.ohlcv-1m.v3.dbn.zst` from `databento/dbn` `tests/data/` at tag `v0.70.0` (use `gh api`), unmodified, into `test/fixtures/databento/` beside the existing samples.
  - [x] Add both to `test/fixtures/databento/SOURCES.md`, in its existing format, with the tag and the check date.
  - [x] Success: both files exist; `SOURCES.md` lists them.

- [x] **2.2 Reader decode tests**
  - [x] Extend `test_dbn_file.py`: the reader decodes both samples as 2 bar records (instrument 5482, `GLBX.MDP3`), `ts_event` is the minute's open (13:00:00 and 13:01:00 UTC on 2020-12-28), and field names and price scale are as the projector will read them. If the reader cannot decode the bar record, fix the reader here.
  - [x] Success: the new tests pass for v1 and v3.

  Note: the samples' prices are 100x the trades samples' (open 372025.0); the test asserts the sample's own value.

**Commit**: `test: add ohlcv-1m DBN sample fixtures and reader tests`

### 3. Storage migration `tick_009_ohlcv_1m` (D2)

- [x] **3.1 Create `bar_constants.py`**
  - [x] Constants for `tick_ohlcv_1m`: table name, chunk interval (7 days, in ns), compress-after (14 days), segment-by column (`instrument_id`), order-by column (`ts_event`). Also the `tick_bar_ledger` table name.
  - [x] Success: no storage value for the bar table is defined outside this file.

- [x] **3.2 Write the migration**
  - [x] Read `market/schema/migrations/tick.py` (`tick_004_trades`, `tick_005_ingest_ledger`, `tick_007_trade_columnstore`, `tick_008_plan_inclusion`) and confirm the last migration id before naming this one.
  - [x] Add `OHLCV_1M` to `ARCHIVED_SCHEMAS` in `constants.py` in this task.
  - [x] Append `tick_009_ohlcv_1m`: drop and re-add `tick_request_schema_check` rendered from `ARCHIVED_SCHEMAS`; create `tick_ohlcv_1m` with the D2 columns and `PRIMARY KEY (instrument_id, ts_event)`; `create_hypertable` (`create_default_indexes => FALSE`, `if_not_exists => TRUE`); `set_integer_now_func('tick_ohlcv_1m', 'tick_now_ns')`; columnstore `ALTER` on the empty table; `add_columnstore_policy`; create `tick_bar_ledger` with `tick_ingest_ledger`'s columns, key and `CHECK`s plus the foreign key to `tick_archive_unit`.
  - [x] Update the module docstring's migration list.
  - [x] Success: `mt data migrate apply --track tick` on a throwaway tick database creates both tables.

  Note: the module docstring and migrations README list tick_009; BAR_SCHEMAS (constants.py) is the new set joined into ARCHIVED_SCHEMAS.

- [x] **3.3 Migration tests** (`test/integration/data/test_tick_009_ohlcv_1m.py`, modelled on `test_tick_008_plan_inclusion.py` and `test_tick_columnstore_migration.py`)
  - [x] Applies on an empty tick database and on a populated one (existing `tick_request` and `tick_trade` rows survive).
  - [x] `tick_request` accepts `ohlcv-1m` and still rejects an unknown schema.
  - [x] `tick_ohlcv_1m` is a hypertable with the primary key, the 7-day chunk interval and a columnstore policy; a duplicate `(instrument_id, ts_event)` insert is refused.
  - [x] Re-applying is a no-op.
  - [x] Success: the new tests pass. Update any test pinning the latest tick migration id.

- [x] **3.4 Drill tables and fingerprint** (D9; the drill pin test fails as soon as 3.2 lands, so fix it here)
  - [x] Add `tick_bar_ledger` to `drill_228_bookkeeping.BOOKKEEPING_TABLES`; add a `tick_ohlcv_1m` fingerprint beside `tick_trade`'s in `drill_228_fingerprint.py`.
  - [x] Success: `test_drill_228_bookkeeping.py` and `test_drill_228_fingerprint.py` pass (the pin test asserting drill tables equal the migrated tick tables passes again).

  Note: `diff_databases` in drill_228_fingerprint.py compares every fingerprinted row table; the two drill steps call it instead of the tick_trade-only pair.

- [x] **3.5 Roles** (D9)
  - [x] Add `tick_ohlcv_1m` and `tick_bar_ledger` to the enumerated write surface in `scripts/provision_tick_roles.sql`. Extend `test_tick_role_privileges.py` to cover both. Do not re-run the script in production.
  - [x] Success: the role privilege test passes.

**Commit**: `feat: add tick_009 migration, drill tables and roles for bar storage`

### 4. Bar projector, `unit_day` check, ingest wiring (D3, D4)

- [x] **4.1 Add `IngestCheck.UNIT_DAY`**
  - [x] In `ingest_checks.py` add the check and `unit_day_reason(count, unit_date, first_ts)` producing `unit_day: N records outside <date>; first at <ts>`.
  - [x] Success: the check is reachable by name from the projector.

  Note: the check function `check_unit_day` lives in the new `unit_day_check.py` (ingest_records.py is already past 300 lines); 4.2's boundary tests are in test_ingest_checks.py.

- [x] **4.2 Test `unit_day_reason`** (extend `test_ingest_checks.py`)
  - [x] Exact text for one and for several records; boundaries `[date 00:00 UTC, date + 1 day)` (a bar at 00:00 is inside, at the next midnight is outside).
  - [x] Success: tests pass.

- [x] **4.3 Implement `Ohlcv1mProjector`**
  - [x] Read `tick_trade_projector.py`, `projector_types.py`, `projectors.py` and `ingest_records.py` first. Create `data/tick/ohlcv_1m_projector.py` following `TickTradeProjector`'s shape.
  - [x] `load` per decoded batch: check every `ts_event` is in the unit's UTC day (`unit_day`); `resolve` every `instrument_id`; `locate_sessions`; `LedgerAccumulator.add` with `volume` as the size; one `COPY` into `tick_ohlcv_1m` (columns per D2, `unit_id` set, `publisher_id` as delivered).
  - [x] `stored_count` and `delete_unit` bounded by the unit's first and last bar times. `finish` writes `tick_bar_ledger` rows. Table name is a code constant from `bar_constants.py`; every value is bound.
  - [x] Reuse the checks `resolution`, `session_boundary`, `overlap`, `shape`, `decode`. A key collision is the existing `overlap` failure, never `ON CONFLICT`.
  - [x] Success: module under about 300 lines, mypy clean.

  Note: the ledger insert and the first/last/decoded bookkeeping are shared with the trade projector through `ledger_write.write_ledger` and `Loaded.note_batch`; TickTradeProjector now uses both with unchanged behaviour.

- [x] **4.4 Register the projector and read superseded units by family**
  - [x] Add `OHLCV_1M_PROJECTOR` to `INGEST_PROJECTORS` in `projectors.py`.
  - [x] In `ingest_plan.py` read a superseded unit's times from the ledger of the unit's family via `FAMILY_LEDGER`, not always `tick_ingest_ledger`.
  - [x] Success: `select_units` can select `ohlcv-1m` units; a tick-tier unit's superseded read is unchanged.

  Note: `superseded_sql(schema)` builds the query with the unit's family ledger; a family with no ledger (definition) has nothing to supersede and returns ().

- [x] **4.5 Projector and superseded-read tests** (integration, on a migrated throwaway tick database)
  - [x] Seed one `tick_definition` row and one calendar session, then load a small hand-built batch through `Ohlcv1mProjector`: rows land in `tick_ohlcv_1m`, `stored_count` matches, `finish` writes the `tick_bar_ledger` row, `delete_unit` removes both.
  - [x] A superseded bar unit's first and last times are read from `tick_bar_ledger`; a superseded tick-tier unit's from `tick_ingest_ledger`.
  - [x] Success: tests pass.

  Note: `delete_unit` removes the bars but keeps the ledger rows, as `tick_trade`'s does (225 TD5); the family-ledger read is tested by moving tbbo into BAR_1M above ohlcv-1m, since the real family holds one schema.

- [x] **4.6 Archive path pin** (D9)
  - [x] Unit test (new file or `test_adopt_files.py`) that an `ohlcv-1m` unit's archive path resolves under `MT_TICK_ARCHIVE_DIR`.
  - [x] Success: the test passes.

**Commit**: `feat: add ohlcv-1m projector, unit_day check and registry entry`

### 5. Derived bar fixture (D7, part two)

- [ ] **5.1 Extend `scripts/cut_tick_fixtures.py`**
  - [ ] Build `test/fixtures/databento/real/glbx-mdp3-20241203.ohlcv-1m.dbn.zst`: the held 2024-12-03 `tbbo` header with the schema field set to `ohlcv-1m`; trade-only one-minute bars (open/high/low/close/volume per instrument and minute, `ts_event` at the minute's open) computed from the day's full held `tbbo` file. Follow how the script derives the existing `trades` fixture.
  - [ ] Success: the file exists, decodes through the reader as `ohlcv-1m` records, and includes bars on both sides of the daily break and spread instrument ids.

- [ ] **5.2 Record it**
  - [ ] Add it to `SOURCES.md`, marked **Not purchased**, as 225's derived `trades` fixture is, stating it makes no claim of provider parity.
  - [ ] Success: `SOURCES.md` lists the two samples and the derived day.

**Commit**: `test: derive ohlcv-1m real-shape fixture from held tbbo day`

### 6. Bar ingest end to end (D3, D4, D10; criteria 1–4, 7, 11)

- [ ] **6.1 Test helper for recording units**
  - [ ] Create `test/integration/data/tick_bar_helpers.py` (or the project's existing helper location): record a definition file and a bar file as units of an `ES.FUT` parent request in a throwaway tick database. Definition units are projected by the acquisition pass, not by ingest (300 D3): read `definitions.py`, `acquisition_pass.py` and `test_tick_definitions.py`, and mark the definition unit ingested by calling the same definition-projection function the acquisition pass calls. Do not mark it with a direct status update. Only bar units go through `mt data tick ingest`. Also set up the primary database migrated through 301 for calendars. The walkthrough in the slice design says `test_bar_ingest_e2e` contains this helper; it lives in `tick_bar_helpers.py` and the test imports it. Update that walkthrough sentence to name `tick_bar_helpers.py`.
  - [ ] Success: a smoke test records a definition unit and a bar unit and reads both back with the expected statuses.

- [ ] **6.1a DBN writer helper**
  - [ ] In the same helper module add a function that writes a small DBN file to `tmp_path` from hand-built records with the DBN Python writer (model it on the existing helpers in `test_tick_definitions.py` and `test_dbn_files_helper.py`); 6.4 to 6.6 build their inputs with it.
  - [ ] Success: a test writes a file of two bars and the reader decodes both back.

- [ ] **6.2 `test_bar_ingest_e2e`**
  - [ ] Ingest the 2024-12-03 definition fixture then the derived bar fixture: bar unit is `ingested`, one `tick_ohlcv_1m` row per bar, `tick_bar_ledger` rows per instrument and session on both sides of the daily break, provider = decoded = stored.
  - [ ] Success: the test passes.

- [ ] **6.3 Prerequisite and supersession tests**
  - [ ] A bar unit is skipped as `awaiting definitions` until its same-shape, same-day definition unit is ingested.
  - [ ] A tick-tier unit and an `ohlcv-1m` unit of the same shape and day both stay current after ingest. The tick-tier unit comes from the derived `trades` fixture of the same day, recorded as a unit through the same 6.1 helper and ingested through the existing trade projector.
  - [ ] Success: tests pass.

- [ ] **6.4 Failure tests** (one test per failure)
  - [ ] Bar outside the unit's UTC day fails `unit_day`.
  - [ ] Bar with no definition fails `resolution`.
  - [ ] Bar in a daily break fails `session_boundary`.
  - [ ] A second current unit holding the same key fails `overlap`. Setup: ingest one bar unit, then record a second `ohlcv-1m` unit that supersession does not retire (read `supersedes_sql` in `schema_families.py` for what retires a unit; use a different shape or day key so the first stays current) carrying a bar for the same instrument and minute; ingesting the second fails.
  - [ ] Success: each failure names its check and leaves no rows in `tick_ohlcv_1m` or `tick_bar_ledger`.

- [ ] **6.5 Pre-2020 placement test** (`test_bar_pre2020`)
  - [ ] Inputs: seed one `tick_definition` row for an ES instrument active in 2012, and write a bar file (via the 6.1 writer) with one bar at 2012-03-06 15:00 UTC (a Tuesday, inside regular hours), with matching definition and bar units for that day.
  - [ ] On a primary database migrated through 301, the bar ingests into its `CME_EQUITY` session.
  - [ ] For the failing case, use a second throwaway primary database from the same fixture and delete its `CME_EQUITY` sessions dated before 2020 (the fixture created it, so the delete is permitted); the same bar then fails with `session_boundary` naming the sessions either side.
  - [ ] Success: `uv run pytest test/integration/data -q -k "bar_pre2020"` passes.

- [ ] **6.6 Pre-2020 definition failure test** (D10, criterion 11)
  - [ ] Inputs: write a definition file (via the 6.1 writer) with one record whose activation or expiration is the DBN undefined-timestamp value, plus a bar unit of the same day and shape.
  - [ ] The definition unit fails naming the instrument, and the bar unit stays `awaiting definitions`.
  - [ ] If the existing definition code (222 F007) does not already fail this way, fix it here.
  - [ ] Success: the test passes.

**Commit**: `test: add bar ingest, failure and pre-2020 placement tests`

### 7. `HistoryWant` and validation (D5)

- [ ] **7.1 Implement `HistoryWant` and `TickUniverseEntry.history`**
  - [ ] Read `data/tick/universe.py` and `test_universe.py`. Add `HistoryWant(schema, start, end)` (UTC days, end exclusive, `end=None` = up to the availability edge) and `history: tuple[HistoryWant, ...] = ()` on the entry.
  - [ ] Import-time validation, each error naming the entry and field: schema is `ohlcv-1m` or `definition`; at most one want per schema; `start` not before `CME_DATASET_START` (import it from `market/schema/seed_cme_hours.py`); the entry has an explicit `tier`; an `ohlcv-1m` want needs a `definition` want whose range contains it (open `end` needs open `end`).
  - [ ] Success: production `TICK_UNIVERSE` still imports with `history=()`.

- [ ] **7.2 Validation tests** (extend `test_universe.py`; one test per rule)
  - [ ] Valid entry with both wants. Rejected: bar want outside definition range; bar want with no definition want; `start` before `CME_DATASET_START`; repeated schema; `tier=None`; schema other than the two allowed; open bar `end` with closed definition `end`.
  - [ ] Success: each rejection names the entry and field.

**Commit**: `feat: add HistoryWant and universe history validation`

### 8. Planner history wants (D5)

- [ ] **8.1 Plan history wants beside tier wants**
  - [ ] Read `planner.py` and `test_planner.py`. Rename `_tier_wants` to `_wants` over the tier and the history wants. Each produces `(shape, days)` through the same classification, month runs and ordering. A want's days are the product's session days inside its range. A definition history want is a want of its own, not a companion; the union of it and the companion definition set is what gets planned.
  - [ ] Definitions order before every other schema within a day (224 TD4).
  - [ ] Success: with `history=()` the planner output is byte-identical to before.

- [ ] **8.2 Planner tests** (extend `test_planner.py`)
  - [ ] Union of a definition want with companion definitions (no duplicate request); definitions ordered before bars within a day; month runs for a multi-month want; covered days dropped; empty `history` leaves existing plans unchanged.
  - [ ] Success: tests pass.

- [ ] **8.3 Production plan unchanged check**
  - [ ] Run `uv run mt data tick pass --estimate-only --json` on the branch, then on its base commit in a temporary worktree (`git worktree add <dir outside the repo> $(git merge-base HEAD <target>)`, same environment, remove the worktree afterwards); compare planned requests and spend lines.
  - [ ] Success: identical (0 requests for today's universe). Record the result as a note under this task.

**Commit**: `feat: plan universe history wants beside tier wants`

### 9. Status per family (D6)

- [ ] **9.1 Family-aware reads**
  - [ ] Read `status_reads.py`, `tick_status.py`, `tick_status_build.py`. `Shape.params` takes a family's schemas instead of `STORED_TIERS`; ledger reads take the family's ledger from `FAMILY_LEDGER`; `ledger_totals` and contract lines get the family from the caller. `day_unit` ranks within the unit's family via `FAMILY_SCHEMAS` instead of `TICK_TIER_RANK`.
  - [ ] Success: the tick tier's result is unchanged; existing status tests pass.

- [ ] **9.2 Tests for 9.1** (extend `test_tick_status.py`)
  - [ ] `day_unit` ranks within each family; a bar unit never outranks or masks a tick-tier unit of the same day.
  - [ ] Success: tests pass.

- [ ] **9.3 One verdict set per wanted family**
  - [ ] `tick_status_build` builds the tick-tier verdicts as today and `BAR_1M` verdicts when the entry has an `ohlcv-1m` want, over that want's range. Day conditions come from `tick_day_condition`. A session whose units are ingested is `complete` however few bars exist; a `missing` day with no unit is `provider_hole`.
  - [ ] Unit test (in `test_tick_status.py`): an entry with no bar want produces no bar verdicts; an entry with one produces `BAR_1M` verdicts over the want's range.
  - [ ] Success: the test passes.

- [ ] **9.4 Status rendering and JSON**
  - [ ] In `cli/commands/tick_status_render.py` print one section per wanted family; the `ohlcv-1m` section is headed `load-complete only` and shows session counts by status, condition findings and `caught up`. A family with no want is not printed.
  - [ ] `--json` gains a `families` list; existing top-level product fields stay the tick tier's.
  - [ ] Golden-output test: text and `--json` output for today's universe (no bar want) equal the pre-change output, captured from the base commit before this task's edit.
  - [ ] Success: the golden test passes.

- [ ] **9.5 Status integration tests**
  - [ ] Using the 6.1 helper and a test universe with a bar want: separate `ohlcv-1m` section headed `load-complete only`; sparse-bar session is `complete`; `missing` day with no unit is `provider_hole`; entry without a bar want prints no bar section; `--json` top-level fields unchanged and `families` present.
  - [ ] Success: tests pass.

**Commit**: `feat: report ohlcv-1m family in mt data tick status`

### 10. Coverage per schema (D6)

- [ ] **10.1 Family-aware coverage**
  - [ ] Read `tick_coverage.py` and `cli/commands/tick*.py` coverage command. `build_coverage(..., family)`; add `--schema` to `mt data tick coverage`, defaulting to the entry's tier (today's output). `--schema ohlcv-1m` reports per instrument and session: status, condition, unit ids, ledger bars and volume, raw bars and volume (`count(*)`, `sum(volume)` over `tick_ohlcv_1m`, one scan bounded by the range), and the check. A mismatch exits 3.
  - [ ] Reject a `--schema` the entry does not want, with an explicit error (tested in 10.2).
  - [ ] Success: output without `--schema` is identical to today's.

- [ ] **10.2 Coverage tests**
  - [ ] Ledger equals raw gives `check: ok` and exit 0 for the 2024-12-03 and 2024-12-04 sessions. Deleting or adding a raw bar gives a mismatch and exit 3. Without `--schema` the output matches the pre-change golden output. A `--schema` the entry does not want exits with an explicit error naming the schema.
  - [ ] Success: tests pass.

**Commit**: `feat: add coverage --schema for ohlcv-1m`

### 11. Estimate row (D8)

- [ ] **11.1 Show `ohlcv-1m` in the estimate**
  - [ ] Read `estimate.py` and `test_estimate.py`. Append `OHLCV_1M` to `ESTIMATE_SCHEMAS`; ensure its row shows list cost, records, bytes and `is_plan_included`, judged alone against the ceiling and kept out of the tick-tier bundle (`_row` bundle stays for `STORED_TIERS`).
  - [ ] Success: the tick-tier rows and bundle verdict are unchanged.

- [ ] **11.2 Estimate tests** (extend `test_estimate.py`)
  - [ ] The `ohlcv-1m` row is present with `is_plan_included: false` under no plan and `true` under STANDARD; its verdict ignores the tier bundle; the bundle total excludes it.
  - [ ] Success: tests pass.

**Commit**: `feat: add ohlcv-1m row to tick estimate`

### 12. Final validation

- [ ] **12.1 Full checks**
  - [ ] `ruff check` and `ruff format --check` on touched files; mypy in one invocation with the kalshi src paths and tests; confirm touched source files are within about 300 lines.
  - [ ] `git diff <target>` (the `cf config get git.integration_branch` value, or `main` when empty) shows no swept lines (deletions of unrelated code) from `ruff format`.
  - [ ] Success: all clean.

- [ ] **12.2 Full tick test tiers**
  - [ ] Run `uv run pytest test/unit/data/tick -q`, then `uv run pytest test/integration/data -q -k "tick or drill_228"` as separate runs. Re-run any failure in isolation before investigating; known pre-existing failures are listed in memory notes.
  - [ ] Success: only documented pre-existing failures remain.

- [ ] **12.3 Walkthrough pass**
  - [ ] Run walkthrough steps 1–3 and 5 from the slice design; step 4 (production plan unchanged) was done in 8.3. Step 6 (production after merge) is not part of this task file.
  - [ ] Add a CHANGELOG entry under the unreleased section describing the new schema, tables, `history` wants and `coverage --schema`; update `README.md` tick command docs if they list `status` and `coverage` options.
  - [ ] Success: each step's expected output is observed; the slice design's success criteria 1–11 each map to a passing test or walkthrough step.

**Commit**: `docs: add 302 changelog and validation notes`
