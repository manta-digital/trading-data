---
docType: tasks
slice: archive-generalisation
project: trading-data
lld: user/slices/300-slice.archive-generalisation.md
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 222
- 224
- 225
- 234
interfaces:
- 301
- 302
- 303
- 304
- 306
projectState: Slice design committed (revision 2). The tick archive, manifest, ingest
  and spend guard (initiative 220) are merged. Ingest ranks every archived schema
  on one global tier order, hard-wires the `tick_trade` COPY into `ingest_worker.py`,
  and the spend guard counts every request at list price. Migration track `tick` ends
  at `tick_007_trade_columnstore`.
dateCreated: 20261030
dateUpdated: 20261030
status: not_started
turns: 21
promptTokens: 1360902
cachedTokens: 1285220
cacheWriteTokens: 75638
completionTokens: 18095
durationSeconds: 144.5
costUsd: 0.612393
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.6123927999999998
    completionTokens: 18095
revision_number: 2
---

# Tasks: Archive Generalisation

## Context Summary

- Working on slice 300. It removes the tick-tier-only assumptions from the futures archive without adding a schema: schema families scope supersession, a projector registry replaces the hard-wired `tick_trade` writer, and a plan-inclusion marker lets the spend guard count marginal spend only.
- Design: `user/slices/300-slice.archive-generalisation.md`. Decisions D1-D11 are referenced by number below; read the decision before the task that cites it.
- With no new schema and `MT_TICK_DATABENTO_PLAN` unset, the tick pipeline must behave exactly as today. Existing tick tests stay green (only imports of moved private helpers may change). The parity run (task group 8) proves it on the held files.
- Source files: `src/manta_trading/data/tick/`, `src/manta_trading/config/__init__.py`, migrations in `src/manta_trading/market/schema/migrations/tick.py`. Unit tests: `test/unit/data/tick/`. Integration tests: `test/integration/data/`.
- Rules for every task: no silent fallback values; keep files near 300 lines (`constants.py` is already over, add nothing to it beyond the one reference the design names); run `ruff format` on touched files only and check `git diff` for swept lines before each commit; pytest needs `MT_TIMESCALE_TEST_URL` exported from `.env` (quotes stripped).
- Work happens on branch `300-slice.archive-generalisation`. The last task ends at a commit on that branch.
- Next planned slice: 301.

Relative effort: 4/5.

## 1. Baseline

- [ ] **1.1 Record the base commit and baseline**
  - [ ] Run `uv run pytest test/unit/data/tick -q` and `uv run pytest test/integration/data -q -k tick` with the test DB URL exported.
  - [ ] Note any failures and match each against the known pre-existing failure list in project memory (integration-tier flakes, test cluster has no TimescaleDB scheduler, load-tier failures). The load tier runs once, in 8.2.
  - [ ] Write `git rev-parse HEAD` and the pass/fail counts under "Parity result" at the end of this file, and commit the file as `docs: record 300 baseline`.
  - [ ] Success: the hash and counts are committed before any code change; every failure is explained as pre-existing. Group 8 builds its base worktree from the hash.

## 2. Schema families and family-scoped supersession (D1)

- [ ] **2.1 Create `data/tick/schema_families.py`**
  - [ ] Define `SchemaFamily` (StrEnum) with `TICK_TIER` and `DEFINITION`.
  - [ ] Define `FAMILY_SCHEMAS: Mapping[SchemaFamily, tuple[TickSchema, ...]]`, lowest rank first. The `TICK_TIER` entry is the `TICK_TIERS` tuple imported from `constants.py`, not a restated list. `DEFINITION` is `(TickSchema.DEFINITION,)`.
  - [ ] Derive `SCHEMA_FAMILY: Mapping[TickSchema, SchemaFamily]` from the table. Fail with an explicit error at import if a schema appears in two families.
  - [ ] Success: the module imports without a circular import, and is under 100 lines.
- [ ] **2.2 Add `supersedes_sql(newer, older)` to `schema_families.py`**
  - [ ] Render `family(newer) = family(older) AND rank(newer) > rank(older)` as SQL `CASE` expressions built from `FAMILY_SCHEMAS`. Arguments are column references or bind placeholders written in code, never input. Document that rule in the docstring, as `tier_rank_sql` does.
  - [ ] Success: for `('tbbo', 'trades')` the rendered predicate is true, for `('trades', 'tbbo')` false, for `('definition', 'tbbo')` and `('tbbo', 'definition')` false (checked in 2.3).
- [ ] **2.3 Unit tests: `test/unit/data/tick/test_schema_families.py`**
  - [ ] `FAMILY_SCHEMAS` is total and disjoint over `TickSchema`: every member is in exactly one family.
  - [ ] `SCHEMA_FAMILY` agrees with the table; the `TICK_TIER` entry `is` the `TICK_TIERS` tuple.
  - [ ] `supersedes_sql` text contains the family comparison and binds no values (assert on the rendered string).
  - [ ] A test-only extra family (monkeypatched table with a fake schema) makes the rendered predicate contain the family comparison for the new schema. This is the design's mitigation for a missed rank use.
  - [ ] Success: the new tests pass.
- [ ] **2.4 Switch `ingest_select.py` and `ingest_plan.py` to `supersedes_sql`**
  - [ ] `ingest_select._SELECT` outranked subquery: replace the `tier_rank_sql` comparison with `supersedes_sql('p.schema', 'r.schema')`.
  - [ ] `ingest_plan._SUPERSEDED`: same replacement for the superseded-unit query.
  - [ ] Keep `STORED_TIERS` as the selection set for now; group 3 replaces it.
  - [ ] Success: both modules no longer import `tier_rank_sql`.
- [ ] **2.5 Remove `tier_rank_sql` and migrate its tests**
  - [ ] Delete `tier_rank_sql` from `constants.py`. Keep `TICK_TIER_RANK` (still used by tick-tier-only consumers).
  - [ ] Move or rewrite the `tier_rank_sql` assertions in `test/unit/data/tick/test_constants.py` (lines near 165-171) so the rank behaviour is covered by `test_schema_families.py`.
  - [ ] Success: `grep -rn tier_rank_sql src test scripts` returns nothing; unit tests pass.
- [ ] **2.6 Integration test: family-scoped supersession (success criterion 2)**
  - [ ] Extend `test_tick_ingest_select.py` and `test_tick_ingest_plan.py` (or add a sibling file if either would pass about 300 lines).
  - [ ] Case A: a `tbbo` unit outranks a current `trades` unit of the same shape and day. This is today's behaviour.
  - [ ] Case B: a `definition` unit and a tier unit of the same shape and day never outrank each other, in either direction, in `select_units` and in `build_plan`'s superseded set.
  - [ ] Success: all pass; the existing select and plan tests are unchanged and pass.
- [ ] **2.7 Commit: family-scoped supersession**
  - [ ] Format touched files only; check `git diff` for swept lines; run unit and integration tick tests.
  - [ ] Commit as `refactor: scope tick supersession to schema families`.

## 3. Projector registry and a schema-agnostic worker (D2, D3, D4)

- [ ] **3.1 Create `data/tick/projectors.py`**
  - [ ] Define the `IUnitProjector` Protocol with the four members from D2: `load`, `stored_count`, `finish`, `delete_unit`. Define the `Loaded` result type it returns (decoded count, first and last event times, projector bookkeeping), moving the worker's private `_Loaded` here.
  - [ ] Define `INGEST_PROJECTORS: Mapping[TickSchema, IUnitProjector]` in this module, filled with `{trades: TICK_TRADE, tbbo: TICK_TRADE}` once 3.2 exists. `definition` is not entered (D3).
  - [ ] Success: the module is import-clean and under 100 lines.
- [ ] **3.2 Create `data/tick/tick_trade_projector.py`**
  - [ ] Move `_copy`, `_check_counts` (the stored-count part), `_insert_ledger` and the unit delete out of `ingest_worker.py` into a `TickTradeProjector` class: `load` does the COPY and collects ledger rows, `stored_count` counts `tick_trade` rows for the unit, `finish` inserts the ledger rows, `delete_unit` deletes the unit's trade rows and ledger rows.
  - [ ] Export one instance as `TICK_TRADE`. Move code verbatim; change no SQL or behaviour.
  - [ ] Success: the file is under 300 lines and contains no reference to the worker module (no import cycle).
- [ ] **3.3 Rewire `ingest_worker.py` through the registry**
  - [ ] `ingest_unit` does the steps in the design's "Ingest, after this slice": look up `INGEST_PROJECTORS[unit.schema]`, mark superseded units and call each old unit's own projector `delete_unit`, `load`, compare provider count vs decoded count vs `stored_count`, `finish`, `mark_ingested`.
  - [ ] A unit whose schema has no projector raises an explicit error naming the schema. No default projector.
  - [ ] `_other_current_units` uses the unit's family (from `SCHEMA_FAMILY`) instead of tier names.
  - [ ] `SupersededUnit.first_event_ns` / `last_event_ns` may be `None`; `delete_unit` for the tick-tier family still receives them.
  - [ ] Error handling (`UnitCheckFailed`, `UniqueViolation`, decode, `ManifestTransitionError`) is unchanged.
  - [ ] Success: `ingest_worker.py` contains no `tick_trade` or `tick_ingest_ledger` SQL, and is shorter than before.
- [ ] **3.4 Select by registry keys**
  - [ ] `ingest_select.py` (params and `_not_selectable`) and `ingest_plan.py` use `INGEST_PROJECTORS` keys where they used `STORED_TIERS`. The not-selectable message names the schema and says it has no projector.
  - [ ] `has_definitions` stays applied to every schema in the registry (D4). Do not touch `COMPANION_SCHEMAS`, `_companion_wants` or `estimate._row`.
  - [ ] Success: criterion 3 holds: `--unit-id` of a `definition` unit is `not_selectable`, naming its schema.
- [ ] **3.5 Tests: registry and moved code**
  - [ ] Unit test (`test_projectors.py`): `INGEST_PROJECTORS` keys equal `STORED_TIERS`; `definition` is not a key; both tier schemas map to the same projector object.
  - [ ] Update imports in `test_tick_ingest_worker.py` and any other test that imported a moved private helper. Do not weaken assertions.
  - [ ] Add one `select_units` test (criterion 3): selecting a `definition` unit by `--unit-id` is reported `not_selectable`, and the reason names its schema and says it has no projector. Existing `not_selectable` tests only cover the "no such unit" reason.
  - [ ] Add one worker test that a unit with an unregistered schema fails with the explicit error.
  - [ ] Add one test that an ingested superseded unit is deleted through its own schema's projector (spy on the registry).
  - [ ] Success: `uv run pytest test/unit/data/tick -q` and `-k tick` integration tests are green.
- [ ] **3.6 Commit: projector registry**
  - [ ] Format touched files only; check `git diff` for swept lines.
  - [ ] Commit as `refactor: ingest through a projector registry`.

## 4. Plan configuration and `is_plan_included` (D5, D6, D7)

- [ ] **4.1 Create `config/databento_plans.py`**
  - [ ] `DatabentoPlan` StrEnum with one member `STANDARD = "standard"`.
  - [ ] `PLAN_INCLUDED_HISTORY: Mapping[DatabentoPlan, Mapping[TickSchema, timedelta | None]]` per D6: `trades`, `tbbo`, `mbp-1` at `timedelta(days=365)`; `definition` at `None`. Comment cites the Databento CME pricing page and the date read (2026-10-28).
  - [ ] Define the dataset the plans cover by referencing `CME_DATASET` (`GLBX.MDP3`); do not restate the string.
  - [ ] Import `TickSchema` and `CME_DATASET` from `data/tick/constants.py`. `config/__init__.py` already imports from `data.*`, and `constants.py` imports nothing from `config`, so there is no cycle.
  - [ ] Success: `uv run python -c "import manta_trading.config"` succeeds and the module is under 80 lines.
- [ ] **4.2 Add `Settings.tick_databento_plan`**
  - [ ] In `config/__init__.py`, add `tick_databento_plan: DatabentoPlan | None = None` next to the other `tick_` settings. Environment variable `MT_TICK_DATABENTO_PLAN`. Absent means no subscription (D5); this is not a fallback.
  - [ ] An unknown value fails settings validation with a message that names `MT_TICK_DATABENTO_PLAN` and the allowed values (criterion 8). Confirm the preflight surfaces that message and refuses before any provider call; fix the message path if it does not.
  - [ ] Success: unset gives `None`; `standard` gives `DatabentoPlan.STANDARD`; `platinum` is refused.
- [ ] **4.3 Create `data/tick/plan_inclusion.py`**
  - [ ] `is_plan_included(plan, dataset, schema, start, end_exclusive, at) -> bool` per D7: false when `plan` is `None` or the dataset is not `CME_DATASET`; a `None` window is included for any date; otherwise true only if every day in `[start, end_exclusive)` is on or after `at`'s UTC date minus the window. An empty or inverted range (`end_exclusive <= start`) raises `ValueError`; it is never reported as included.
  - [ ] Look up the window with a direct index into the plan's map. A missing schema raises `KeyError`; there is no default (D6).
  - [ ] Success: one pure function, no I/O, under 60 lines.
- [ ] **4.4 Unit tests: `test_plan_inclusion.py`**
  - [ ] `PLAN_INCLUDED_HISTORY` is total over `TickSchema` for every plan.
  - [ ] `is_plan_included` cases: inside the window; outside; straddling the boundary (false); the 365th day (included) and the 366th (not); `definition` of any age; another dataset; plan `None`; an empty range and an inverted range each raise `ValueError`.
  - [ ] Settings tests (extend the existing settings test file): the three cases from 4.2.
  - [ ] Preflight test (extend the existing preflight or pass test): with `platinum` set, the tick preflight refuses before any provider call (use a provider double that fails if called), and the message names `MT_TICK_DATABENTO_PLAN` and `standard` (criterion 8).
  - [ ] Success: all pass.
- [ ] **4.5 Commit: plan inclusion rule and setting**
  - [ ] Commit as `feat: add Databento plan inclusion table and rule`.

## 5. Migration `tick_008_plan_inclusion` and the request writers

- [ ] **5.1 Add the migration**
  - [ ] Append `tick_008_plan_inclusion` to the list in `market/schema/migrations/tick.py` after `tick_007_trade_columnstore`, using the SQL in the design's "Database / Storage Schema": add `is_plan_included BOOLEAN NOT NULL DEFAULT FALSE`, then drop the default.
  - [ ] Update the module docstring's migration list and any hard-coded "chain ends at" constant or doc that names the last tick migration (grep for `tick_007_trade_columnstore`).
  - [ ] Success: `uv run mt data migrate apply --track tick` against a scratch database applies it twice with no error.
- [ ] **5.2 Integration test: `test_tick_008_plan_inclusion.py`**
  - [ ] On an empty tick database: the column exists, is `NOT NULL`, and has no default.
  - [ ] On a database holding requests before 008: existing rows become `FALSE`.
  - [ ] An `INSERT` that omits the column fails on `NOT NULL`.
  - [ ] Success: all pass. Known pre-existing 051/052 migration failures are not caused by this change; confirm with the project-memory list if they appear.
- [ ] **5.3 Both request writers set the marker**
  - [ ] `manifest_pass.insert_pending_request` takes the `is_plan_included` value as a required argument and names the column in its `INSERT`. At this step the submit call site computes it with `is_plan_included(settings.tick_databento_plan, ...)` for the item being submitted; task 6.4 moves that single call onto the plan item so the guard and the row share one value.
  - [ ] `adopt.py` classifies each adopted job with `is_plan_included(settings.tick_databento_plan, ...)` at the job's `ts_received` and writes it. If `adopt` does not receive settings today, thread the plan through its existing call chain rather than reading the environment in place.
  - [ ] Fix every other `INSERT INTO tick_request` found by grep (tests, scripts) so each names the column.
  - [ ] Success: `grep -rn "INSERT INTO tick_request" src test scripts` shows every statement naming `is_plan_included`.
- [ ] **5.4 Integration tests: both writers**
  - [ ] Extend `test_tick_manifest_pass.py`: a pending request written with the plan unset has `is_plan_included = FALSE`; one written with an included item has `TRUE`.
  - [ ] Extend `test_tick_adopt.py`: an adopted job with no plan configured is `FALSE`; with `standard` and a recent `ts_received` inside the window it is `TRUE`.
  - [ ] Success: all pass (criterion 4).
- [ ] **5.5 Commit: marker column and writers**
  - [ ] Commit as `feat: record plan inclusion on tick requests`.

## 6. Spend guard counts marginal spend (D8)

- [ ] **6.1 `PlannedCost.included` and `evaluate_spend`**
  - [ ] Add `included: bool` to `PlannedCost` in `spend_guard.py`. Make it a required field; update every constructor call. Until 6.4, production call sites pass `included=False` (today's behaviour); only tests set `True`.
  - [ ] `evaluate_spend` sums only items with `included` false into the per-pass and 30-day totals. `SpendVerdict` shape is unchanged.
  - [ ] Success: `evaluate_spend` has no other new branches; the file stays under 300 lines.
- [ ] **6.2 Unit tests in `test_spend_guard.py`**
  - [ ] Included planned items add $0 to the per-pass and 30-day sums; non-included items add their cost.
  - [ ] An included resubmitted item (`counted`) is not double counted.
  - [ ] Included trailing rows and included unheld jobs are excluded; a non-included unheld job counts in full (criteria 5 and 6).
  - [ ] Existing tests pass after adding `included=False` to their fixtures.
  - [ ] Success: all pass.
- [ ] **6.3 Trailing read returns marginal rows only**
  - [ ] In `manifest_request_reads.trailing_spend_rows`, add `NOT is_plan_included` to the query.
  - [ ] Integration test (in the existing manifest-request-reads or pass test file): a trailing included row is absent, a usage-billed row is present.
  - [ ] Success: test passes.
- [ ] **6.4 `purchase_plan` classifies planned, resubmitted and unheld items**
  - [ ] For each planned item call `is_plan_included(settings.tick_databento_plan, dataset, schema, start, end, now)` once, build `PlannedCost(cost, counted, included)`, and hold the value on the plan item, and make the submit call site from 5.3 read it from there instead of calling `is_plan_included` itself.
  - [ ] Classify each unheld provider job from its own request and `ts_received` with the same function. A job on another dataset, or seen with no plan configured, is not included.
  - [ ] Do not add a default for an absent `ts_received`; per D8 it never reaches this code and the job listing already fails.
  - [ ] Success: no second call site computes inclusion; `purchase_plan.py` stays under 300 lines.
- [ ] **6.5 Tests: purchase plan classification**
  - [ ] Plan `standard`: a recent `tbbo` item is included; an item older than the window is not; a boundary-straddling item is not; a `definition` item of any age is included (criterion 5).
  - [ ] Unheld jobs through `purchase_plan` (not just `evaluate_spend`): with plan `standard`, a recent in-window job and a `definition` job are included; an out-of-window job and a job on another dataset are not and count in full (criterion 6).
  - [ ] No plan: every item and unheld job is not included, and the verdict equals today's.
  - [ ] The value written to the row by `insert_pending_request` equals the value the guard used for the same item.
  - [ ] Success: all pass, including the existing purchase-phase and pass tests.
- [ ] **6.6 Pass render shows marginal totals**
  - [ ] Update the pass renderer so the guard line says the totals are marginal and keeps list-price figures where they already are. This is the only operator-visible text change besides 7.1. Update any test that asserts the old line.
  - [ ] Success: render tests pass; `estimated_cost_usd` on each row is unchanged.
- [ ] **6.7 Commit: marginal spend guard**
  - [ ] Commit as `feat: count marginal spend in the tick spend guard`.

## 7. Estimate reports plan inclusion (D9)

- [ ] **7.1 `SchemaEstimate.is_plan_included`**
  - [ ] Add the field to `SchemaEstimate` in `estimate.py`, set from `is_plan_included(settings.tick_databento_plan, ...)` at the current time.
  - [ ] The bundle verdict compares the marginal bundle (sum of the bundle's non-included members) with the ceiling. `cost_usd` and `bundle_cost_usd` stay at list price.
  - [ ] `--json` output adds `is_plan_included` per schema; the human table gets an inclusion column.
  - [ ] Keep the bundle composition (`COMPANION_SCHEMAS`, `_row`) as it is (D4).
  - [ ] Success: `estimate.py` stays under 300 lines.
- [ ] **7.2 Tests in `test_estimate.py`**
  - [ ] No plan: every schema is `false`, and verdicts equal today's.
  - [ ] Plan `standard`, recent range: all four schemas `true`; the bundle verdict is `within` even at a tiny ceiling; `cost_usd` is unchanged.
  - [ ] Mixed bundle (an old range): the verdict counts only the non-included part.
  - [ ] JSON and table output each include the new field.
  - [ ] Success: all pass (criterion 7).
- [ ] **7.3 Commit: estimate inclusion**
  - [ ] Commit as `feat: show plan inclusion in tick estimate`.

## 8. Full validation and parity (D10)

- [ ] **8.1 Remaining-use grep (design Migration Plan)**
  - [ ] Run `grep -rnE "STORED_TIERS|TICK_TIERS|TICK_TIER_RANK" src scripts`.
  - [ ] Every remaining hit must be in the tick-tier-only list in the design (`status_reads`, `manifest_reads.owned_tier_days`, `tick_status.day_unit`, `universe`, `planner._companion_wants`, `estimate._row`, the ledger readers, `scripts/proof_226/`) or in `constants.py` / `schema_families.py`.
  - [ ] Any other hit is a missed use: convert it to family or registry, with a test.
  - [ ] Success: the final list is pasted under "Remaining tier-name uses" at the end of this file, each with a one-line reason.
- [ ] **8.2 Static checks and full tick tests**
  - [ ] Run `ruff check`, `ruff format --check` on touched files, and mypy with the project's kalshi paths and `test` in one invocation (a narrower run reports false errors).
  - [ ] Run `uv run pytest test/unit/data/tick -q`, `-k tick` integration tests, and the tick tests in the load tier (the design requires them to pass unchanged). Run each tier separately; re-run a failure in isolation before investigating. The full load tier takes about 77 minutes: give it a timeout of at least 90 minutes.
  - [ ] Success: clean, apart from the failures already recorded in 1.1.
- [ ] **8.3 File-size check and checkpoint commit**
  - [ ] `wc -l` on every new and touched source file; none above ~300 lines except files already above it before this slice.
  - [ ] If 8.1 or 8.2 changed any file, format touched files only, check `git diff` for swept lines, and commit as `refactor: convert missed tier-name uses` (or `fix:` for a defect), with the file-size list in the body. The parity runs in 8.4 and 8.5 must run on this commit.
  - [ ] Success: the file-size list is recorded and the working tree is clean.
- [ ] **8.4 Rebuild at the base commit**
  - [ ] Create a worktree at the hash recorded in 1.1 (outside the checkout, for example `../td-300-base`). Confirm `.env` supplies `MT_PROOF_226_DB_URL` and `MT_PROOF_226_MAINTENANCE_URL`.
  - [ ] From the worktree run `uv run python scripts/proof_226_tick.py rebuild`. The destructive-statement guard in `scripts/proof_226/guard.py` applies; confirm the target is `trading_tick_proof`, never the production tick database.
  - [ ] Record the `drill_228_fingerprint.fingerprint` of `tick_trade` and `drill_228_bookkeeping.table_md5` of `tick_definition` and `tick_ingest_ledger`, using the existing 228 functions.
  - [ ] Success: three values recorded under "Parity result".
- [ ] **8.5 Rebuild at the branch head and compare**
  - [ ] From the checkout run the same `rebuild`, then record the same three values.
  - [ ] Compare: `diff_fingerprints` returns no lines, and both table hashes are equal. A difference is a defect in this slice: find it before continuing.
  - [ ] Query the proof database: `SELECT schema, is_plan_included, count(*) FROM tick_request GROUP BY 1, 2`. Every row must be `false`.
  - [ ] Remove the base worktree with `git worktree remove`.
  - [ ] Success: equal values and an all-`false` marker recorded under "Parity result".
- [ ] **8.6 Verification walkthrough steps 4 and 5 (free metadata calls only)**
  - [ ] Run the two `mt data tick estimate ... --json` commands from the design's step 4, plan unset and `MT_TICK_DATABENTO_PLAN=standard`.
  - [ ] Run `MT_TICK_DATABENTO_PLAN=platinum uv run mt data tick pass --estimate-only`.
  - [ ] Success: outputs match the design's expected results (first run all `false`; second all `true` with `within` verdict and unchanged `cost_usd`; third refused before any provider call, naming `MT_TICK_DATABENTO_PLAN` and `standard`). Paste the key lines under "Parity result".
- [ ] **8.7 Record results and commit**
  - [ ] Fill in "Parity result" and "Remaining tier-name uses" below, and add the result to the slice design's Verification Walkthrough as the design asks.
  - [ ] Commit as `docs: record archive generalisation parity result`.

## Parity result

Base commit: `739305815ccf41693c282e1ee9dd3a28a93fa961`

Baseline (1.1, dev checkout, before any code change):
- `uv run pytest test/unit/data/tick -q`: 535 passed.
- `uv run pytest test/integration/data -q -k tick`: 425 passed, 2 failed. Re-run in isolation, same two fail: `test_health_tick.py::test_an_unreachable_tick_database_is_informational` and `::test_holed_and_reopened_units_never_fail_retention`. Cause: `FAIL backup  MT_BACKUP_ROOTS is not set` in the dev environment (health check fails on the backup line, not on anything tick). Environmental and deterministic, not related to this slice.
- Load tier runs once, in 8.2.

Results: _(recorded in 8.4-8.6)_

## Remaining tier-name uses

_(recorded in 8.1)_
