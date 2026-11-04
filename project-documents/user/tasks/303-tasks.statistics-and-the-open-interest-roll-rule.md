---
docType: tasks
slice: statistics-and-the-open-interest-roll-rule
project: trading-data
lld: user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md
dependencies:
- 300
- 301
- 302
projectState: 300, 301 and 302 are merged; last tick migration is tick_009_ohlcv_1m;
  statistics is not an archived schema; no open interest is stored; production universe
  has no history wants
dateCreated: 20261103
dateUpdated: 20261103
status: not_started
turns: 21
promptTokens: 1482293
cachedTokens: 1397865
cacheWriteTokens: 84384
completionTokens: 21433
durationSeconds: 156.8
costUsd: 0.692043
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.6920434000000001
    completionTokens: 21433
revision_number: 2
---

## Context Summary

- Working on the `statistics-and-the-open-interest-roll-rule` slice (303). Design: `user/slices/303-slice.statistics-and-the-open-interest-roll-rule.md`. Decision references (D1–D13) point into it. Source paths are under `src/manta_trading/`.
- Adds Databento `statistics` as an archive schema in its own family (`STATISTICS`), stored in hypertable `tick_statistics` with ledger `tick_statistics_ledger`. Records are assigned to a session from `ts_ref`, and the value read is resolved per contract, statistic and session by receive time (D5). Ships with its status verdict, content check and cross-tier volume check.
- Adds the open-interest roll rule `n` (D9), the volume rule's per-session basis (tick tier, else bars; D10), and `n` in the API (D13).
- One real purchase (about $0.05, usage-billed, inside the existing ceiling) on throwaway databases: ES and GC `statistics` plus October definitions for 2024-08-30 to 2025-01-01. It is pre-authorized and runs through estimate and the spend guard. If the guard refuses, record the reason in the evaluation note and stop that task; never force it.
- Excluded: serving open interest or settlement through the API (306), `health`/`accounting`/`overview` (305), interpreting `stat_flags`, any production universe change (`history=()` stays), pre-2020 statistics, making `n.0` a default.
- Ground rules: SQL fragments built from constants are written in code and every value is bound. No silent fallbacks: undefined values are NULL, never DBN sentinels; a failing unit fails loudly. Source files stay within about 300 lines. Run `ruff format` on touched files only and check `git diff` for swept lines before each commit. Run mypy with the kalshi src paths and tests in one invocation. Export `MT_TIMESCALE_TEST_URL` from `.env` (quotes stripped) before integration tests. Never touch the production database; tests use throwaway databases.
- Known pre-existing failures to ignore: migration 051/052 chain tests, the test cluster's missing TimescaleDB scheduler, `test_health_tick` pair needing `MT_BACKUP_ROOTS`.
- Pattern to follow throughout: slice 302 (`ohlcv_1m_projector.py`, `bar_constants.py`, `tick_009_ohlcv_1m`, `test_tick_009_ohlcv_1m.py`, `test_bar_ingest_e2e.py`, `test_tick_coverage_bars.py`).
- Next slice: 304 (first large purchase; adopts this slice's jobs).
- Overall relative effort: 4/5.

## Tasks

### 1. Schema, family, plan row, constants (D1, D2)

- [x] **1.1 Create `statistics_constants.py`**
  - [x] New `data/tick/statistics_constants.py`, modelled on `bar_constants.py`: table names (`tick_statistics`, `tick_statistics_ledger`), column names, primary key, chunk interval (7 days in ns), columnstore segment/order/compress-after values, `REFERENCED_STAT_TYPES` (settlement price 3, cleared volume 6, open interest 9), `REQUIRED_STAT_TYPES` (the same three), `STATISTICS_PUBLICATION_SESSIONS = 1` with a comment citing the roll lag constant's note (`VOLUME_RANK_LAG_SESSIONS`; task 10 renames it, so cite it by description, not name) and 220's pinned timing.
  - [x] Stat types are defined once as an `IntEnum` (or constants) holding the DBN `StatType` values; nothing else in the code compares to a bare number.
  - [x] Success: module imports; no value is duplicated elsewhere.

- [x] **1.2 Add `TickSchema.STATISTICS` and membership**
  - [x] Read `data/tick/constants.py` and `test_constants.py` first. Add `TickSchema.STATISTICS = "statistics"`; add it to `ARCHIVED_SCHEMAS`, `HISTORY_SCHEMAS` and `ESTIMATE_SCHEMAS`. This task owns all three memberships; task 7.1 adds only the want validation. Do not touch `STORED_TIERS`, `TICK_TIERS` or `COMPANION_SCHEMAS`. `constants.py` takes only this (it is 442 lines).
  - [x] Success: constants tests pass, updated where they enumerate schemas.
  - Note: `estimate.py`'s `_row` treats statistics like the bar schemas, with its own marginal cost. `STATISTICS_SCHEMAS` is a new set in `constants.py`, beside `BAR_SCHEMAS`; `HISTORY_SCHEMAS`, `ARCHIVED_SCHEMAS` and `ESTIMATE_SCHEMAS` are built from it.
  - Note: `test_estimate.py` loads a recorded fixture for every `ESTIMATE_SCHEMAS` entry, so the three `get_{record_count,billable_size,cost}.statistics.json` files were recorded with `scripts/record_databento_fixtures.py --schema statistics` (free calls). The row-count and verdict assertions in `test_estimate.py` were extended for this; 7.3 still confirms the estimate.

- [x] **1.3 Add `SchemaFamily.STATISTICS` and `FAMILY_LEDGER`**
  - [x] In `schema_families.py` add `STATISTICS: (STATISTICS,)` to `FAMILY_SCHEMAS` and `FAMILY_LEDGER[STATISTICS] = "tick_statistics_ledger"` (name from `statistics_constants.py`).
  - [x] Success: families stay total and disjoint over `TickSchema`.

- [x] **1.4 Add the plan-inclusion row**
  - [x] In `config/databento_plans.py` set `PLAN_INCLUDED_HISTORY[STANDARD][STATISTICS] = None` (all history).
  - [x] Success: the plan table is total over `TickSchema`; `is_plan_included` answers for `statistics`.

- [x] **1.5 Tests for 1.1–1.4** (extend `test_constants.py`, `test_schema_families.py`, `test_plan_inclusion.py`; the estimate tests are updated in 7.3)
  - [x] Families total and disjoint; `STATISTICS` holds `statistics` only; `FAMILY_LEDGER` has a key for every family that is a key of `INGEST_PROJECTORS`'s schemas except those not yet registered (the projector is registered in task 4); plan table total.
  - [x] Success: `uv run pytest test/unit/data/tick -q` passes.

**Commit**: `feat: add statistics schema, family, constants and plan row`

### 2. Migration `tick_010_statistics` (D2)

- [x] **2.1 Write the migration**
  - [x] Read `market/schema/migrations/tick.py` and confirm the last id (expected `tick_009_ohlcv_1m`). Add `tick_010_statistics`: drop and re-add `tick_request_schema_check` rendered from `ARCHIVED_SCHEMAS`; `CREATE TABLE IF NOT EXISTS tick_statistics` with the columns, NULL rules and CHECKs of D2 (`calendar_id`/`session_date` both NULL or both set; `session_date` set implies `ts_ref` set); primary key `(instrument_id, stat_type, ts_recv, sequence, record_ordinal)`; `create_hypertable` on `ts_recv` (integer ns, 7-day chunks, no default indexes, `if_not_exists`); `set_integer_now_func('tick_statistics', 'tick_now_ns')`; columnstore `ALTER` on the empty table; `add_columnstore_policy`; index on `(session_date, stat_type)`.
  - [x] `CREATE TABLE IF NOT EXISTS tick_statistics_ledger` per D2: key `(unit_id, instrument_id, session_date, stat_type)`, foreign key to `tick_archive_unit`.
  - [x] Success: migration registered after `tick_009`; all values come from `statistics_constants.py`.

- [x] **2.2 Migration integration tests** (new `test/integration/data/test_tick_010_statistics.py`, modelled on `test_tick_009_ohlcv_1m.py`)
  - [x] Applies on an empty tick database and on a populated one (existing rows untouched); re-apply is a no-op.
  - [x] A `tick_request` with schema `statistics` is accepted and an unknown schema is still rejected.
  - [x] Both CHECKs reject violating rows; duplicate key rejected; the hypertable has the expected chunk interval and columnstore policy.
  - [x] Success: tests pass against the throwaway test cluster; `test_tick_columnstore_migration.py` still passes.
  - Note: `test_drill_228_bookkeeping_pins.py::test_bookkeeping_list_plus_row_tables_is_every_tick_table` and `test_tick_role_privileges.py::test_artifact_enumerates_every_tick_table` now fail only because the two new tables are not yet enumerated; task 7.4 owns both.

**Commit**: `feat: add tick_010_statistics migration`

### 3. Sample decoding (D3 prerequisite)

- [ ] **3.1 Copy the DBN statistics samples and record them**
  - [ ] Fetch `test_data.statistics.v1.dbn.zst` and `test_data.statistics.v3.dbn.zst` from `databento/dbn` `tests/data/` at tag `v0.70.0` (use `gh api`), unmodified, into `test/fixtures/databento/`.
  - [ ] Add both to `test/fixtures/databento/SOURCES.md` in its existing format, with the tag and the check date.
  - [ ] Success: both files exist; `SOURCES.md` lists them.

- [ ] **3.2 Reader decode tests**
  - [ ] Extend `test_dbn_file.py`: the reader decodes both samples as 2 records (instrument 146945, `GLBX.MDP3`, stat types `LOWEST_OFFER` and `TRADING_SESSION_HIGH_PRICE`, `ts_ref` undefined), exposing `ts_recv`, `ts_ref`, `price`, `quantity`, `sequence`, `ts_in_delta`, `stat_type`, `channel_id`, `update_action`, `stat_flags`. v1's narrower quantity is covered. If the reader cannot decode the statistics record, fix the reader here. Add the schema to the reader's accepted-header tests.
  - [ ] Success: both versions decode; `uv run pytest test/unit/data/tick -q` passes.

**Commit**: `test: add statistics DBN samples and decode tests`

### 4. Projector and `stat_reference` (D3, D4)

- [ ] **4.1 Add the `stat_reference` check**
  - [ ] In `ingest_checks.py` add `IngestCheck.STAT_REFERENCE` and its reason text. A failure names the instrument, stat type, `ts_ref` and the reason (`undefined ts_ref`, `not a session`, `after the unit`).
  - [ ] Success: the enum and reason map are total; `test_ingest_checks.py` updated and passing.

- [ ] **4.2 Pure session assignment** (in `statistics_projector.py`, or a small helper module if the projector would exceed ~300 lines)
  - [ ] A function mapping `(stat_type, ts_ref, unit_date, calendar)` to `(calendar_id, session_date)`, `None`, or a `stat_reference` failure, per D4: non-referenced type gets no session; referenced type with undefined `ts_ref` fails; the UTC date of `ts_ref` must be a calendar session and not after the unit's date; a date before `CME_DATASET_START` gets no session and is counted.
  - [ ] Branching on stat type is only membership of `REFERENCED_STAT_TYPES`.
  - [ ] Success: function is pure (no connection).

- [ ] **4.3 Tests for 4.2** (new `test_statistics_session.py`)
  - [ ] Each outcome: settlement/volume/open interest with a valid session date; other type with defined `ts_ref` stays sessionless; undefined `ts_ref` on a referenced type fails; a weekend/holiday date fails `not a session`; a date after the unit fails `after the unit`; a pre-2010-06-06 date is sessionless and counted; a `ts_ref` at 00:00 and at 23:59 UTC of a session date gives the same session.
  - [ ] Success: tests pass.

- [ ] **4.4 Implement `StatisticsProjector`**
  - [ ] New `data/tick/statistics_projector.py` implementing `IUnitProjector`, in the shape of `Ohlcv1mProjector`: per batch, `unit_day` on `ts_recv`, `resolution` of `instrument_id`, session assignment (4.2), `record_ordinal` via `OrdinalCarry` with `stat_type` folded into the key, referenced records into the ledger accumulator keyed `(instrument_id, session_date, stat_type)`, one `COPY` into `tick_statistics`. Undefined `ts_ref`, `price` and `quantity` become NULL, never DBN sentinels.
  - [ ] `stored_count` and `delete_unit` bounded by the unit's first and last receive times; `finish` writes `tick_statistics_ledger` through `ledger_write.py`. `session_boundary` is not applied. The unit result carries the pre-2010 count.
  - [ ] Register it in `projectors.py` as `INGEST_PROJECTORS[STATISTICS]`.
  - [ ] Success: `select_units` selects verified `statistics` units and waits for the same-day definition unit (`awaiting definitions`).

- [ ] **4.5 Projector tests** (new `test_statistics_projector.py` unit; new `test/integration/data/test_tick_statistics_ingest.py`)
  - [ ] Unit: undefined values to NULL; `record_ordinal` increments for a repeated key across a batch boundary; ledger rows hold referenced records only; the registry includes `STATISTICS`.
  - [ ] Integration, on seeded definitions and a synthetic statistics file built with the project's file-writing test helpers: provider = decoded = stored; a unit skipped as `awaiting definitions` until its definition unit ingests; failures for `stat_reference`, `unit_day`, `resolution`, and `overlap` (a key held by two current units); a record whose `ts_ref` precedes `CME_DATASET_START` is stored with NULL `session_date` and `calendar_id`, and the unit result's pre-2010 count is 1; re-ingest after `delete_unit` is idempotent.
  - [ ] Success: tests pass; `test_tick_ingest_families.py` still passes.

**Commit**: `feat: add statistics projector and stat_reference check`

### 5. Resolved read (D5)

- [ ] **5.1 Implement `statistics_reads.resolved`**
  - [ ] New `data/tick/statistics_reads.py`: `resolved(conn, shape, stat_types, first, last)` returns one row per `(instrument_id, stat_type, session_date)` over current units of the shape (`SHAPE_WHERE`), the record with the greatest `(ts_recv, sequence, record_ordinal)`; a winning `DELETE` yields no row. One `DISTINCT ON` written in code, all values bound; rows carry the winner's `unit_id`.
  - [ ] Success: this is the only place a statistic value is chosen.

- [ ] **5.2 Tests for 5.1** (new `test/integration/data/test_tick_statistics_resolved.py`)
  - [ ] Latest receive time wins across units; ties broken by `sequence` then `record_ordinal`; a `DELETE` in a later unit removes the value; a `NEW` after a `DELETE` restores it; non-current (superseded) units are ignored; records with NULL `session_date` never appear.
  - [ ] Success: tests pass.

**Commit**: `feat: add resolved statistics read`

### 6. Status and coverage for the family (D7, D8)

- [ ] **6.1 Session verdict and content findings**
  - [ ] New `data/tick/statistics_status.py`: for session D, the window is the UTC days from D through the close day of the session one after D (`STATISTICS_PUBLICATION_SESSIONS`, read from the calendar); status is 225's `classify_session` over the statistics units of those days (a `missing` condition with no unit is `provider_hole`). In a load-complete session, every listed outright (definition window `activation_ns ≤ open`, `expiration_ns ≥ close`) must have ledger records for each `REQUIRED_STAT_TYPES`; a contract expiring inside the session is exempt; spreads are never required. Each gap is a finding naming the contract and missing types. Reads only manifest, `tick_statistics_ledger` and `tick_definition`.
  - [ ] Success: reuses the 302 per-family status plumbing (`status_reads.py`, `tick_status_build.py`); no copy of `classify_session`.

- [ ] **6.2 Wire into `mt data tick status`**
  - [ ] `tick_status.py` / `tick_status_scope.py` print a `statistics` section for an entry with a statistics want (session counts by status, content findings, `caught up`); `--json`'s `families` gains it. An entry without the want shows no section.
  - [ ] Success: output for entries without a statistics want is byte-identical to before.

- [ ] **6.3 Tests for 6.1–6.2** (new `test_statistics_status.py` unit; extend `test_tick_status.py`)
  - [ ] Publication window from the calendar, including a Friday session (window reaches Monday's close day) and a holiday; a session with its units in only one of the two days is not complete; a missing required type, a contract expiring inside the session, and a spread each behave per D7; `provider_hole`.
  - [ ] Status section appears only with a statistics want, in text and JSON.
  - [ ] Success: tests pass.

**Commit**: `feat: add statistics session verdict and status section`

- [ ] **6.4 Volume check** (in `statistics_reads.py`)
  - [ ] Pure `classify_volume(cleared, traded)` returning `agree`, `under`, `over` or `no_trades` per D8, as an enum (not strings scattered in code). Connection-bound `volume_check(conn, shape, family, first, last) -> tuple[VolumeVerdict, ...]`: one resolved read of cleared volume and one ledger read of the given family through `FAMILY_LEDGER`, bounded by `[first, last]`, for listed outrights. A session is checked against a volume family only when it is load-complete in `statistics` and in that family; the statuses come from `statistics_status.py` and 302's existing per-family status reads (this task owns that combination; task 11 reuses it).
  - [ ] If the module would exceed ~300 lines, put the volume check in `statistics_volume.py`.
  - [ ] Success: one implementation parameterised by family.

- [ ] **6.5 Tests for 6.4**
  - [ ] Unit: each `classify_volume` verdict including V = C = 0 and `under` with its V/C ratio.
  - [ ] Integration (new `test_tick_statistics_volume.py`): seeded ledger and statistics rows for both `TICK_TIER` and `BAR_1M`; both complete means both reported separately; a session incomplete in either family is skipped.
  - [ ] Success: tests pass.

**Commit**: `feat: add statistics volume check`

- [ ] **6.6 `coverage --schema statistics`**
  - [ ] In `tick_coverage.py`, report per session: status, condition, unit ids, ledger and raw record counts per required stat type (mismatch exits 3), and the volume verdicts per contract and family. `over` and `no_trades` exit 3; `under` does not.
  - [ ] Success: existing coverage output for other schemas is unchanged.

- [ ] **6.7 Tests for 6.6** (new `test/integration/data/test_tick_coverage_statistics.py`, modelled on `test_tick_coverage_bars.py`)
  - [ ] Count mismatch exits 3; `over` exits 3; `no_trades` exits 3; `under` exits 0; `--json` shape.
  - [ ] Success: tests pass.

**Commit**: `feat: add coverage for the statistics family`

### 7. History wants, estimate row, drill and roles (D6, D12)

- [ ] **7.1 `statistics` as a `HistoryWant` schema**
  - [ ] In `universe.py`, `HISTORY_SCHEMAS` already carries `statistics` (task 1.2); add only the validation, which follows the bar want's: one want per schema, start no earlier than `CME_DATASET_START`, explicit tier, range inside the entry's `definition` want. The refusal message names the entry and field. Confirm the planner plans a statistics want like a bar want (session days in range, covered days dropped, month runs, definitions first within a day) and fix `planner.py` only if it does not.
  - [ ] Success: production `history=()` unchanged.

- [ ] **7.2 Tests for 7.1** (extend `test_universe.py`, `test_planner.py`)
  - [ ] A statistics want outside the definition want, before `CME_DATASET_START`, repeated, or on an entry with `tier=None` fails naming the field; a valid want plans the expected requests with definitions first.
  - [ ] With the production universe the plan equals the pre-slice plan (pin on the existing planner fixtures).
  - [ ] Success: tests pass.

- [ ] **7.3 Estimate row**
  - [ ] Confirm `mt data tick estimate` prints a `statistics` row judged on its own marginal cost (via `ESTIMATE_SCHEMAS`, task 1.2) and update `test_estimate.py` for it.
  - [ ] Success: other rows unchanged.

- [ ] **7.4 Backup and roles (D12)**
  - [ ] `tick_statistics_ledger` joins `drill_228_bookkeeping.BOOKKEEPING_TABLES`; `drill_228_fingerprint.py` gains a `tick_statistics` fingerprint; both tables join the enumerated write surface in `scripts/provision_tick_roles.sql` and the privilege test (`test_tick_role_privileges.py`; find its path with `find test -name 'test_tick_role_privileges.py'`).
  - [ ] Update the drill pin tests (`test_drill_228_bookkeeping_pins.py`, `test_drill_228_fingerprint.py`).
  - [ ] Success: those tests pass.

**Commit**: `feat: add statistics history wants, estimate row, drill and role coverage`

### 8. The purchase and ingest on throwaway databases (D11)

- [ ] **8.1 Prepare the throwaway run**
  - [ ] Create a primary and a tick database on the test cluster (names that this process creates; destructive statements only touch these), point `MT_TIMESCALE_DB_URL`, `MT_TIMESCALE_MAINTENANCE_URL`, `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` at them and at a scratch directory that is kept. Leave `MT_TICK_DATABENTO_PLAN` unset. Write a wrapper `CLI` (302's walkthrough method) with a universe giving ES and GC `definition` and `statistics` wants over 2024-08-30 to 2025-01-01. Production's manifest and universe are untouched.
  - [ ] Run `mt data init` and `mt data init --database tick`; confirm the tick track reaches `tick_010_statistics`.
  - [ ] Success: wrapper and databases exist; migration applied.

- [ ] **8.2 Estimate, buy, download, ingest**
  - [ ] `CLI data tick pass --estimate-only --json`: expect statistics about $0.0127 (ES) and $0.0242 (GC), October definitions about $0.0014 and $0.0129, plan-included false, verdict within the ceiling. Then `CLI data tick pass --json` (buy, download, verify, project definitions), then `CLI data tick ingest --json`.
  - [ ] If the guard refuses, write the reason into `user/notes/303-statistics-evaluation.md`, add a `BLOCKED:` line under this task naming it, leave the task unchecked and stop.
  - [ ] Success: every statistics unit ingests; no `stat_reference` failure. If one fails, diagnose from the error text and fix the projector or design assumption (tests first) before continuing.

- [ ] **8.3 Record job ids and charges**
  - [ ] Create `user/notes/303-statistics-evaluation.md` (front matter per `file-naming-conventions.md`). Record the job ids, each request's estimate from the manifest, and the charge Databento reports for the jobs. Also record what later tasks (14.2, 15.1, 15.3) reuse: the primary and tick database names, the wrapper's path, the scratch archive path, and the names (not values) of the environment variables set in 8.1.
  - [ ] Success: note exists with the job ids; the note's "adopt in 304" instruction names the scratch archive path.

- [ ] **8.4 Measure and record the data facts**
  - [ ] Write a throwaway script under `scripts/` (named `measure_303_statistics.py`, kept only if re-runnable against any tick database) that reads the throwaway database and prints: observed `ts_ref` instants and `stat_flags` values per required stat type; the number of distinct reference dates checked against the calendar; the latest receive time of each required statistic relative to its session's close, against `STATISTICS_PUBLICATION_SESSIONS`; V / C per contract and session for ES and GC (tick volume against cleared volume) and any `over` or `no_trades`; the resolved-read timing over the window.
  - [ ] Record every item in the evaluation note.
  - [ ] Success: note holds each item; `over`/`no_trades` findings, if any, are explained or the defect fixed.

- [ ] **8.5 Check Risk: back-month records**
  - [ ] Run `CLI data tick status --product GC` and `--product ES`, and `CLI data tick coverage --product GC --schema statistics --start 2024-11-04 --end 2025-01-01 --json` (and ES). Expect counts equal, no `over` or `no_trades`, exit 0; record the output summary in the evaluation note. If listed back months show content findings for missing settlement or open interest, narrow `REQUIRED_STAT_TYPES` handling to the contracts CME reports (tests first), with the evidence in the note. Never patch with zeros.
  - [ ] Success: status shows complete sessions and either no findings or findings explained in the note.

**Commit**: `feat: ingest the purchased statistics window and record its evaluation`

### 9. File fixtures from the purchase (D11)

- [ ] **9.1 Copy the real files**
  - [ ] Copy the purchased ES statistics files for 2024-12-03 and 2024-12-04 whole into `test/fixtures/databento/real/` (session 2024-12-03's window is those two days). If either is above 1 MB, cut it with `scripts/cut_tick_fixtures.py` to the outrights and both days' referenced records.
  - [ ] Record the job in `test/fixtures/databento/SOURCES.md`.
  - [ ] Success: files exist; sizes are under 1 MB.

- [ ] **9.2 Integration tests on the real files** (extend `test_tick_statistics_ingest.py`; new `test_tick_statistics_real.py` if needed)
  - [ ] Ingest the two real files with the held 2024-12-03 definitions fixture (and the 2024-12-04 definitions if the purchase supplies them); every record stored, referenced records assigned the session found in 8.4.
  - [ ] Revisions as delivered resolve to one value per contract, statistic and session.
  - [ ] The volume check against the held `tbbo` fixture (tick tier) and the derived 2024-12-03 bar day (`BAR_1M`) gives no `over` or `no_trades`.
  - [ ] Status and coverage for the family on this data, including the exit-3 path via a deliberately tampered copy.
  - [ ] Success: tests pass; note records any V / C `under` share on these files.

**Commit**: `test: add real statistics fixtures and ingest tests`

### 10. Rename the shared lag constant (D9)

- [ ] **10.1 Rename `VOLUME_RANK_LAG_SESSIONS` to `RANK_LAG_SESSIONS`**
  - [ ] Rename in `constants.py` (value stays 2) and every reference found by `grep -rn VOLUME_RANK_LAG_SESSIONS src test scripts docs` (`roll_resolve.py`, `active_contract.py`, `test_roll_provider_equivalence.py`, and any others). Update the constant's comment: both the `v` and `n` rules rest on CME publishing D's statistics during D + 1.
  - [ ] Success: grep finds no old name; `uv run pytest test/unit/data/tick -q` passes unchanged in behaviour.

**Commit**: `refactor: rename volume rank lag to RANK_LAG_SESSIONS`

### 11. Volume basis per session (D10)

- [ ] **11.1 Types: basis family on answers**
  - [ ] In `roll_types.py` add `basis_family` to `ActiveContract` (`tick_tier`, `bar_1m`, `statistics` as an enum shared with `SchemaFamily` values; NULL for `c`). `RollSchedule.segments()` also breaks a run where the basis family changes.
  - [ ] The field exists; the calendar rule sets NULL; the volume rule sets `tick_tier` for now so existing constructors compile (11.3 makes the choice per session, 12.4 sets `statistics` for `n`).
  - [ ] Success: existing roll tests pass. That every resolver sets the field and only the calendar rule leaves it NULL is checked in 11.4 and 12.5.

- [ ] **11.2 Tests for 11.1** (extend `test_roll_next.py` or a new `test_roll_types_basis.py`)
  - [ ] Segments split at a basis change even when the contract is unchanged; calendar answers carry NULL.
  - [ ] Success: tests pass.

- [ ] **11.3 Per-family volume read and basis choice**
  - [ ] In `roll_inputs.py`, `session_volumes(conn, shape, family, ...)` reads that family's ledger through `FAMILY_LEDGER`, with the per-family session statuses read as in task 6.4 (no new status query). Callers compile; tick behaviour unchanged.
  - [ ] In `roll_resolve.py` and `active_contract.py`: for each session L the volume rule needs, basis is `TICK_TIER` when the tick session is `complete`, else `BAR_1M` when the bar session is `complete`, else the session is `Unresolved` naming both statuses. A session never mixes families. If a bar session lacks a listed contract's ledger row, raise the I13 error naming it; never read 0. The answer records `basis_family` and its `basis_unit_ids` come from that family.
  - [ ] Success: `roll_resolve.py` stays pure (the choice is made from inputs it is handed).

- [ ] **11.4 Tests for 11.3** (extend `test_roll_resolve_volume.py`; extend `test_tick_active_contract.py`)
  - [ ] Ticks complete means tick basis, even when bars are also complete; tick incomplete and bars complete means bar basis; neither means unresolved with both statuses in the reason; a bar session missing a contract row raises I13.
  - [ ] Every `v.0`/`v.1` answer over the recorded 229/232 fixtures is unchanged (the equivalence test still passes).
  - [ ] Every `c` answer has a NULL `basis_family`; every `v` answer has `tick_tier` or `bar_1m`.
  - [ ] Success: tests pass.

**Commit**: `feat: choose the volume rule's basis per session`

### 12. The open-interest rule `n` (D9)

- [ ] **12.1 Rule vocabulary**
  - [ ] Add `RollMethod.OPEN_INTEREST = "n"`. The grammar is unchanged: `n.0` and `n.1` parse; `n.0+2s` is refused (offset is calendar only). `DEFAULT_ROLL_RULE` and `STATUS_ROLL_RULES` do not change.
  - [ ] Success: `RollRule.parse` handles `n`.

- [ ] **12.2 Tests for 12.1** (extend `test_roll_rules.py`)
  - [ ] `n.0`, `n.1` parse; `n.0+2s` refused with the parser's message; defaults unchanged.
  - [ ] Success: tests pass.

- [ ] **12.3 Inputs**
  - [ ] `roll_types.py` adds `SessionOpenInterest` (contract, session, value, unit id). `RollInputs` gains `open_interest` and `statistics_statuses`, read only when the rule is `n`. `roll_inputs.py` adds the open-interest read over `statistics_reads.resolved` and the statistics session statuses from `statistics_status.py`. `active_contract.py` reads exactly the inputs the rule needs: nothing more for `c`, volume for `v`, open interest and statuses for `n`, with the read window padded by `RANK_LAG_SESSIONS` as the volume path does.
  - [ ] Success: `c` and `v` read nothing extra.

- [ ] **12.4 Resolver**
  - [ ] `roll_resolve.py` adds `_open_interest`, mirroring the volume rule: L is `RANK_LAG_SESSIONS` before S; candidates are eligible at S and listed in L (229's predicate); ranked by resolved open interest at L, ties to the earlier expiration. S is `Unresolved` when L's statistics status is not `complete` (reason names L and its status) or when a candidate has no resolved open interest at L (reason names the contract and L); these are answers, not `RollInputError`. `basis_unit_ids` are the units of the records read; `basis_family` is `statistics`. The forecast follows the volume rule's (exact for the lag sessions, then the bound at the active contract's expiration). Never look at S's own open interest.
  - [ ] Success: if `roll_resolve.py` would pass ~300 lines, move the open-interest resolver to `roll_resolve_oi.py`.

- [ ] **12.5 Tests for 12.3–12.4** (new `test_roll_resolve_open_interest.py`; extend `test_tick_active_contract.py`)
  - [ ] Unit, synthetic inputs: the highest open interest wins at lag; ties go to the earlier expiration; a candidate not listed at L is excluded; missing value gives `Unresolved` naming contract and L; incomplete lag session gives `Unresolved` naming L and status; the rule never reads S's own open interest (a value seeded only at S changes nothing); `n.1` is the second-ranked.
  - [ ] Integration: `active_contract` with seeded statistics reads exactly the inputs `n` needs; `c` and `v` run without any statistics rows.
  - [ ] Every `n` answer has `basis_family` `statistics`.
  - [ ] Success: tests pass.

**Commit**: `feat: add the open-interest roll rule n`

### 13. CLI, API and documentation (D10, D13)

- [ ] **13.1 CLI output**
  - [ ] `mt data tick active --rule n.0 [--start --end] [--json]` works; JSON answers carry `basis_family`; text output names the basis.
  - [ ] Success: existing output for `c` and `v` gains only the basis field.

- [ ] **13.2 Tests for 13.1** (extend `test_tick_active_cli.py`)
  - [ ] `--rule n.0` schedule and latest line; `--json` carries `basis_family`; `n.0+2s` exits with the parser's message.
  - [ ] Success: tests pass.

- [ ] **13.3 API `basis` on segments**
  - [ ] `SegmentRecord` (`api_server/models/futures.py`) gains `basis` (`tick_tier`, `bar_1m`, `statistics` or null), filled from the segment's basis family in the schedule, ticks and bars responses. `RULE_DESCRIPTION` in `routes/futures_params.py` lists `n.0`. No route changes: every route takes `rule` through `RollRule.parse`.
  - [ ] Success: `n.0+2s` returns `422` with the parser's message.

- [ ] **13.4 Tests for 13.3** (extend `test_futures_models.py`; extend the futures route tests found by `grep -rln "rule=" test/unit/api_server test/integration`)
  - [ ] `basis` appears on every segment in each of the three responses; `rule=n.0` answers on seeded data; `rule=n.0%2B2s` is `422`.
  - [ ] Success: tests pass.

- [ ] **13.5 Documentation and OpenAPI**
  - [ ] `docs/api/reference.md` and `docs/api/agents.md` describe `n` beside `v` and `c` (ranked on an earlier session's open interest, never looking ahead) and the segment `basis`. Regenerate `docs/api/openapi.json` with the project's existing generation command (find it in `README.md` or `scripts/`; do not hand-edit).
  - [ ] Success: the openapi diff contains only `basis` and the rule description; an existing test that pins the schema, if any, passes.

**Commit**: `feat: serve n.0 and segment basis through the CLI and API`

### 14. Recorder and equivalence on the purchased window (D11)

- [ ] **14.1 Extend `record_229_roll_fixture.py`**
  - [ ] Also write `{product}_open_interest.json` (resolved outright open interest per session, with unit ids, and the statistics verdicts) and add `.n.0` and `.n.1` to `{product}_databento.json` through free `symbology.resolve`. Update `test/tick_support/roll_fixture.py` to load the new files.
  - [ ] Success: script still runs for existing outputs; its new output is exercised by 14.2 and 14.3.

- [ ] **14.2 Record the fixtures**
  - [ ] Run `scripts/record_229_roll_fixture.py --product ES` and `--product GC` against the throwaway databases from task 8, writing under `test/fixtures/tick/roll/`.
  - [ ] Success: four new/changed fixture files per product exist and are valid JSON; commit sizes are reasonable.

- [ ] **14.3 Equivalence test for `n.0` and `n.1`**
  - [ ] Extend `test_roll_provider_equivalence.py` with `n.0` and `n.1` for ES and GC against the recorded fixtures. A session where our `n.0` and Databento's `.n.0` differ is fixed by the lag (one lag for both products, never per roll) or listed in `KNOWN_DIVERGENCE` with the evidence. The pinned `v` equivalence still passes with the same `RANK_LAG_SESSIONS`.
  - [ ] Success: `env -i HOME=$HOME PATH=$PATH uv run pytest test/unit/data/tick/test_roll_provider_equivalence.py -q` passes.

- [ ] **14.4 Roll dates and timing in the evaluation note**
  - [ ] With `CLI data tick active --product GC --rule n.0` (and `v.0`, `c.0`) over 2024-11-04 to 2025-01-01, confirm GC `n.0` rolls around 2024-11-21 and `v.0` around 2024-11-29; ES `n.0` and `v.0` roll on the same sessions. Record per product the roll dates of `c.0`, `v.0`, our `n.0` and Databento's `.n.0` and `.v.0`, and any `KNOWN_DIVERGENCE` entries. Add the `statistics` read cost per session over the window.
  - [ ] Success: the note holds every item D11 lists; the divergence list is stated for the completion summary.

**Commit**: `feat: record open-interest fixtures and test n.0 equivalence`

### 15. Final validation

- [ ] **15.1 Production pass unchanged**
  - [ ] On a throwaway tick database holding a copy of production's manifest and availability rows (302's method), the base commit's and this branch's `mt data tick pass --estimate-only --json` are identical apart from run id and timings.
  - [ ] Success: diff is empty after masking those fields; paste the comparison into the evaluation note.

- [ ] **15.2 Full checks**
  - [ ] `ruff check` on the repo; `ruff format` on touched files only, then `git diff main` for swept deletions; `mypy` in one invocation with the kalshi src paths and tests; every touched source file within about 300 lines.
  - [ ] Run `uv run pytest test/unit/data/tick -q`, then `uv run pytest test/integration/data -q -k "tick or drill_228"` and the API tests, with `MT_TIMESCALE_TEST_URL` exported. Re-run isolated any known flake before investigating.
  - [ ] Success: all pass apart from the known pre-existing failures.

- [ ] **15.3 Evaluation note complete**
  - [ ] `user/notes/303-statistics-evaluation.md` holds every D11 item, the job ids, and the divergence list. Drop the throwaway databases; keep the scratch archive for 304 to adopt.
  - [ ] Success: each D11 bullet maps to a section of the note.

**Commit**: `docs: complete 303 statistics evaluation note`
