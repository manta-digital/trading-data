---
docType: slice-design
slice: archive-generalisation
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies: []
interfaces:
- 301
- 302
- 303
- 304
- 306
dateCreated: 20261028
dateUpdated: 20261030
status: complete
turns: 42
promptTokens: 4474005
cachedTokens: 4317927
cacheWriteTokens: 155992
completionTokens: 49166
durationSeconds: 465.5
costUsd: 3.095516
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-opus-5-5:
    rounds: 3
    costUsd: 3.0955157999999994
    completionTokens: 49166
revision_number: 2
---

# Slice Design: Archive Generalisation

## Overview

The futures archive, manifest and ingest were built for tick tiers only. The schema enum (`TickSchema`) feeds rules that assume every archived schema is a tier or the `definition` companion: the tier order (`TICK_TIERS`) is the supersession rank, `STORED_TIERS` is the set ingest selects, the preflight judges a tier-plus-definition bundle, and the ingest worker is hard-wired to `COPY` into `tick_trade`. The spend guard counts every request at list price, so a pull that a subscription already pays for would stop the history acquisition at its first request.

This slice removes those assumptions without adding a schema:

1. **Schema families.** Every archived schema belongs to a family (tick tiers and definitions today; bars and statistics arrive in 301 and 302). Supersession compares schemas within a family only.
2. **A projector registry.** Ingest looks up the writer for a unit's schema in one registry. The current `tick_trade` writer becomes the tick-tier family's projector.
3. **A plan-inclusion marker.** Each request row records whether a Databento subscription plan includes it, decided from one configuration table. The 30-day spend guard and the per-pass ceiling count marginal spend only. List-price estimates stay on the row.

The `tick` migration track, the `MT_TICK_*` settings and the `mt data tick` commands keep their names. With no new schema and no plan configured, the tick pipeline behaves exactly as it does today. The existing tick tests and a rebuild-parity run on the held files prove it.

## Value

- **Unblocks 301 and 302.** Adding `ohlcv-1m` or `statistics` becomes three things: a family entry, a projector and a migration widening the schema `CHECK`. Without this slice, a bar unit would be ranked against `tbbo`, selected as a tick tier and copied into `tick_trade`.
- **Makes a subscription usable.** Once a plan is configured, the guard stops counting what the plan includes. The ceilings still protect usage-billed purchases, such as 303's $25 purchase and ticks older than the rolling year.
- **Is safe by default.** With no plan configured, every request counts at its estimate, which is today's behaviour. The setting is the operator's statement that the account holds the plan, and nothing verifies it against the account. Leaving it unset only makes the guard stricter. Setting a plan the account does not hold makes the guard laxer, because usage-billed pulls inside the plan's history then count $0. So the setting is changed only when a subscription starts or ends (D5).
- **Developer-facing.** Tier rules live in one place per family instead of being repeated as `STORED_TIERS` filters in about ten queries.

## Technical Scope

**Included**

- `SchemaFamily` and one ordered table of family to schemas (lowest rank first) in a new `data/tick/schema_families.py` (`constants.py` is already 404 lines). `TICK_TIERS` stays the one statement of tier order in `constants.py`, and the table's tick-tier entry is that tuple, so there is still one source and no circular import.
- A family-scoped supersession predicate replacing `tier_rank_sql` in `ingest_select.py` and `ingest_plan.py`.
- `data/tick/projectors.py`: the `IUnitProjector` protocol and `INGEST_PROJECTORS`, the registry. `data/tick/tick_trade_projector.py`: the `tick_trade` `COPY`, count check, ledger rows and unit delete, moved out of `ingest_worker.py`. The worker becomes schema-agnostic.
- Ingest selection uses the registry's keys as "what ingest loads" instead of `STORED_TIERS`.
- `config/databento_plans.py`: `DatabentoPlan` and the inclusion table. `data/tick/plan_inclusion.py`: `is_plan_included(...)`, the one classification function.
- `Settings.tick_databento_plan` (`MT_TICK_DATABENTO_PLAN`), optional.
- Migration `tick_008_plan_inclusion`: `tick_request.is_plan_included`.
- Both request writers set the marker: `manifest_pass.insert_pending_request` and `adopt`.
- Spend guard: counts marginal planned, trailing and unheld spend only.
- `mt data tick estimate`: each row shows whether the configured plan includes it, and the bundle verdict judges marginal cost.
- A rebuild-parity run on the held files with the existing 226 and 228 harnesses (D10). No new script.

**Excluded**

- Any new schema, table or `CHECK` widening (301, 302).
- Moving `definition` projection out of the acquisition pass (D3).
- Status, coverage, health or accounting changes. They stay tick-tier-family surfaces; 301, 302 and 304 extend them.
- Renaming `tick_*` tables, the `tick` track, `MT_TICK_*` or `mt data tick` (architecture, "Generalising the archive beyond tick tiers").
- Splitting a request at a plan's inclusion boundary (D7).
- Counting the subscription fee itself. The guard covers per-request spend; the plan's fixed monthly fee is outside it.

## Dependencies

### Prerequisites

- Initiative 220's pipeline as merged: manifest (222), acquisition pass and spend guard (224), ingest (225), production wiring (234).
- The held archive (the two adopted jobs: ES `tbbo` 2024-08-30 to 2024-12-31, GC `tbbo` from 2024-11-04) and the 226 proof database (`trading_tick_proof`, with URLs `MT_PROOF_226_DB_URL` and `MT_PROOF_226_MAINTENANCE_URL` in `.env`), for the parity run.
- The Databento CME pricing page as read on 2026-10-28 (architecture, Overview) for the inclusion table's contents.

### Interfaces Required

- `ITickFileReader` / `DbnFile.iter_batches`: unchanged. Projectors receive decoded batches through it.
- `BatchJob.request` and `BatchJob.ts_received` from the provider's job list, so unheld jobs can be classified (D8).
- `UnitIngestPlan` (225): unchanged in shape. The projector reads the definitions, session frame and product from it.

## Architecture

### Component Structure

```
data/tick/schema_families.py   (archive structure only)
  SchemaFamily, FAMILY_SCHEMAS (tick-tier entry = constants.TICK_TIERS) ──► SCHEMA_FAMILY
  supersedes_sql(newer, older)       (replaces tier_rank_sql)

config/databento_plans.py      (configuration: what each plan includes)
  DatabentoPlan, PLAN_INCLUDED_HISTORY

data/tick/plan_inclusion.py    (the rule over that configuration)
  is_plan_included(plan, dataset, schema, start, end, at) -> bool

projectors.py       IUnitProjector (Protocol), INGEST_PROJECTORS: schema -> projector
tick_trade_projector.py   TickTradeProjector: load (COPY + ledger), stored_count, delete_unit

ingest_select.py    selects schemas in INGEST_PROJECTORS; outranked via supersedes_sql
ingest_plan.py      superseded units via supersedes_sql
ingest_worker.py    transaction, supersede transitions, projector calls, checks, mark ingested

manifest_pass.py / adopt.py   write is_plan_included
manifest_request_reads.py     trailing rows: marginal only
spend_guard.py      PlannedCost.included; marginal sums
purchase_plan.py    classifies planned, resubmitted and unheld items
estimate.py         per-row is_plan_included; verdict on marginal bundle
```

Rules that stay with the tick-tier family and are not generalised: the companion bundle (`COMPANION_SCHEMAS`, `_companion_wants`, `estimate._row`), `STORED_TIERS` (what the universe may name, and status's tier filter), and `tick_status.day_unit`.

### Data Flow

**Ingest, after this slice.** `select_units` takes the verified, current units whose schema is a key of `INGEST_PROJECTORS`. A unit is outranked only by a current unit of the same shape and day in the same family with a higher rank. `build_plan` finds the superseded units with the same predicate. On the worker, `ingest_unit` does the following in one transaction:

- `projector = INGEST_PROJECTORS[unit.schema]`;
- mark each superseded unit, and call `INGEST_PROJECTORS[old.schema].delete_unit(...)` for any that are ingested;
- `projector.load(cur, plan, path, reader, progress)`, which returns the decoded count, first and last event times, and the projector's own bookkeeping (for `tick_trade`, the ledger rows);
- check that the provider count, the decoded count and `projector.stored_count(...)` agree;
- `projector.finish(cur, unit_id, loaded)` (ledger insert for `tick_trade`);
- `mark_ingested`.

The error handling (`UnitCheckFailed`, `UniqueViolation`, decode, `ManifestTransitionError`) is unchanged.

**Purchase, after this slice.**

- The planner builds requests as today.
- For each planned item, `purchase_plan` calls `is_plan_included(settings.tick_databento_plan, dataset, schema, start, end, now)` and builds `PlannedCost(cost, counted, included)`.
- The trailing read returns marginal rows only.
- Each unheld job is classified with the same function, from its request and `ts_received`.
- `evaluate_spend` sums only what is not included.
- At submit, `insert_pending_request` writes the same `is_plan_included` value the guard used. Both come from one call, held on the plan item, so the guard and the row cannot disagree.
- `adopt` classifies an adopted job at its `ts_received`.

### State Management

- **New persisted state:** `tick_request.is_plan_included BOOLEAN NOT NULL`. `TRUE` means a plan includes the request; `FALSE` means it is usage-billed. It is written once at insert and never updated. A plan that lapses later does not change what a past row cost.
- **Configuration state:** both halves live in the configuration layer, as the architecture asks. The inclusion table is in the configuration package (`config/databento_plans.py`, beside `Settings`). Which plan the account holds is `MT_TICK_DATABENTO_PLAN` (absent means no subscription). The table is a typed module, not an environment value, because its contents change only when Databento's pricing changes and need a reviewed commit with a test.
- **No other state changes.** Manifest states, the ledger, `tick_trade` and `tick_definition` are written exactly as before.

## Technical Decisions

### D1 — Schema families, from one ordered table
Status: decided

`FAMILY_SCHEMAS: Mapping[SchemaFamily, tuple[TickSchema, ...]]` lists each family's schemas lowest rank first. It lives in `data/tick/schema_families.py` and has two entries now: `TICK_TIER: TICK_TIERS` (the existing tuple in `constants.py`, trades, tbbo, mbp-1) and `DEFINITION: (definition,)`. Referencing `TICK_TIERS` rather than restating it keeps one source for tier order, and avoids a circular import, since `schema_families.py` imports `TickSchema` from `constants.py`. `SCHEMA_FAMILY` (schema to family) is derived from the table. A unit test asserts that every `TickSchema` member is in exactly one family.

301 adds `BAR` and 302 adds `STATISTICS`, each with its schema. A family with no schema is not declared ahead of its slice, because an enum member nothing uses cannot be tested.

Rank is meaningful only within a family. `supersedes_sql(newer, older)` renders `family(newer) = family(older) AND rank(newer) > rank(older)` as SQL `CASE` expressions built from the table. Like `tier_rank_sql`, its arguments are column references or placeholders written in code, never input.

- *Rejected: one global rank over all schemas.* That is today's model, and it would let a bar unit supersede a `tbbo` unit, which the architecture forbids.
- *Rejected: a family column in the database.* Family is a property of the schema, so a stored copy could disagree with the code.

### D2 — A projector registry keyed by schema; the ledger belongs to the projector
Status: decided

`IUnitProjector` (a `Protocol`) has four members:

- `load(cur, plan, path, reader, progress) -> Loaded`
- `stored_count(cur, unit_id, first_ns, last_ns) -> int`
- `finish(cur, unit_id, loaded) -> None`
- `delete_unit(cur, unit_id, first_ns, last_ns) -> None`

`INGEST_PROJECTORS: Mapping[TickSchema, IUnitProjector]` is `{trades: TICK_TRADE, tbbo: TICK_TRADE}`. Its keys replace `STORED_TIERS` as the set ingest selects. A test asserts that the keys equal `STORED_TIERS` today, so the selection is unchanged.

The ledger (`tick_ingest_ledger`) records trade counts and volume per instrument and session, and its readers (`catalog_reads`, `get_plan`, `roll_inputs`, status) mean tick trades by it. So writing it is the `tick_trade` projector's job, not the worker's. 301 decides what a bar unit's bookkeeping is, without inheriting a tick ledger by default.

`SupersededUnit` keeps its ledger-derived `first_event_ns` and `last_event_ns`. They are `None` for a family without a ledger, and that family's `delete_unit` deletes by `unit_id` alone.

- *Rejected: a projector per family.* The registry is keyed by schema because two schemas in one family may need different writers (`ohlcv-1s` and `ohlcv-1m` may land in different tables).

### D3 — Definitions stay projected by the acquisition pass
Status: decided

`definition` units are projected by `project_definitions` in the acquisition pass, on the run's connection, with record-level comparison and no supersession. They are not entered in `INGEST_PROJECTORS`.

Moving them would change when definitions land relative to tier ingest. Tier ingest waits on definitions being *ingested*, which the acquisition pass guarantees today. That is a behaviour change this slice is meant not to make. 301 projects the full-range definitions through the same pass.

### D4 — Definitions are an ingest prerequisite for every schema; the bundle stays a tick-tier rule
Status: decided

Two rules both involve definitions and need to stay separate:

- **Ingest prerequisite.** `ingest_select`'s `has_definitions` check (a same-shape, same-day definition unit is ingested) applies to every schema in the registry. Every projector resolves `instrument_id` to a contract through `tick_definition`. Bars and statistics need that resolution too (architecture, "Contract identity needs definitions back to 2010").
- **Purchase bundle.** Buying a companion `definition` day with each tier day (`_companion_wants`), and judging the tier-plus-definition bundle in `mt data tick estimate`, stay tick-tier rules. They read the tick-tier family, not "every archived schema". 301 buys full-range definitions on their own.

### D5 — The plan setting
Status: decided

`Settings.tick_databento_plan: DatabentoPlan | None` (`DatabentoPlan` from `config/databento_plans.py`), environment variable `MT_TICK_DATABENTO_PLAN`. `DatabentoPlan` has one member now, `STANDARD = "standard"`, the only plan whose contents were read from the pricing page. Absent means no subscription, which is the account's real state today, not a fallback. In that state every request is usage-billed and counted.

Nothing checks the value against the account, so a plan set without a subscription lets the pulls it would include through uncounted. The setting changes only when a subscription starts or ends. An unknown value fails settings validation, so the preflight refuses to run. The existing `MT_TICK_` unknown-key refusal also covers a misspelt key name.

### D6 — One inclusion table, total over schemas
Status: decided

`PLAN_INCLUDED_HISTORY: Mapping[DatabentoPlan, Mapping[TickSchema, timedelta | None]]` lives in `config/databento_plans.py`. Each value is a trailing window, or `None` for the whole dataset. The plans are CME plans, so the table covers `GLBX.MDP3` (`CME_DATASET`) only, and a request on any other dataset is not included.

Each plan's map must name every `TickSchema`, and a unit test enforces it, so there is no absent-key path. Every schema that exists today is included in Standard. If a later plan or schema is not included, that is the point to add an explicit "not included" value.

Standard, from the pricing page on 2026-10-28:

| Schema | Included history |
|---|---|
| `trades`, `tbbo`, `mbp-1` | `timedelta(days=365)` (the page's "last 12 months") |
| `definition` | `None` (all history) |

301 and 302 add `ohlcv-1m` and `statistics` at all history. The table's comment cites the page and the date read.

### D7 — A request is included only if every day of it is
Status: decided

`is_plan_included(plan, dataset, schema, start, end_exclusive, at)` is true when a plan is configured, the dataset is `GLBX.MDP3`, and every day in `[start, end_exclusive)` lies inside the schema's window as of `at`, meaning on or after `at`'s UTC date minus the window. 365 days is never longer than 12 calendar months, so the window errs short.

A request that straddles the boundary counts in full at its estimate. The planner already cuts requests at month boundaries, so the most this overcounts is one month of one tier. That is conservative and needs no planner change. Splitting at the boundary is left until a measured overcount matters.

The exact boundary Databento applies to "last 12 months" has not been measured. A window shorter than Databento's is the safe side: the guard overcounts and only refuses. 365 days is shorter than any calendar reading of 12 months, but it is not proven shorter than Databento's actual window (for example, if Databento anchors the window to a billing date). If Databento's window is narrower than 365 days, a claim of inclusion could hide a charge. The Risk Assessment records the check against the first in-plan invoice.

### D8 — The guard counts marginal spend
Status: decided

- **Planned.** `PlannedCost` gains `included: bool`. The per-pass ceiling and the 30-day cap sum only items that are not included.
- **Trailing.** `trailing_spend_rows` returns rows with `NOT is_plan_included`.
- **Unheld provider jobs.** Each is classified from its own request and `ts_received` with the same function. A job the manifest does not hold is still spend the guard must see unless the plan includes it. Every job that reaches classification has a `TickSchema`, UTC-day bounds and a `ts_received`, because `parse_batch_job` raises on anything else. `BatchJob.ts_received` is not optional, so an absent `ts_received` never reaches classification. That failure already fails the job listing, and the pass refuses as a provider error before any spend. This slice keeps that behaviour. A job on another dataset, or seen with no plan configured, is not included and counts in full at its cost. Classification has no other outcome, so no job can fall out of the sum.
- **Report.** `SpendVerdict`'s shape and the pass render are unchanged. Its totals are now marginal. List prices stay where they already are: `estimated_cost_usd` on each row, and `mt data tick estimate` (D9).
- **Invoice.** Reconciliation (`actual_cost_usd` from the job) is unchanged. It is meaningful for usage-billed rows, and 303 compares it there.

### D9 — The estimate command reports inclusion
Status: decided

`SchemaEstimate` gains `is_plan_included` (from `is_plan_included` at the current time). The bundle verdict compares the *marginal* bundle with the ceiling, so `mt data tick estimate` and the pass's guard cannot disagree on the same request. `--json` adds `is_plan_included` per schema. The `cost_usd` and `bundle_cost_usd` figures stay at list price.

### D10 — Parity is proven by rebuilding the held files twice
Status: decided

No new script. Run the 226 harness's `rebuild` step (`scripts/proof_226_tick.py rebuild`) twice: once from a worktree at the slice's base commit, then once from the branch head. Each run rebuilds `trading_tick_proof` from empty: migrate, adopt both held jobs, `pass --estimate-only`, `ingest`. After each run, record two things with the existing 228 drill functions:

- the `tick_trade` fingerprint (`drill_228_fingerprint.fingerprint`);
- `drill_228_bookkeeping.table_md5` of `tick_definition` and `tick_ingest_ledger`.

Two rebuilds from empty adopt in the same order, so ids match and a whole-table hash compares directly. Equal fingerprints and hashes are the proof, because these are the tables the moved ingest code writes. The 226 harness's destructive-statement guard (`proof_226/guard.py`) applies. Nothing reads or writes the production tick database.

- *Rejected: comparing against production.* Production's manifest carries resets and history the rebuild does not reproduce, so differences would need explaining instead of being zero.

### D11 — No API change
Status: decided

Families, projectors and plan inclusion are acquisition internals, and no consumer reads them. The API question is answered "no" here. 305 answers it for the data these slices add.

## Implementation Details

### Migration Plan

| What moves | From | To |
|---|---|---|
| Tier order and rank | `TICK_TIERS` only | `TICK_TIERS` unchanged, referenced as `FAMILY_SCHEMAS[TICK_TIER]` |
| Rank SQL | `tier_rank_sql` | `supersedes_sql` (same family, higher rank); `tier_rank_sql` removed |
| `tick_trade` writer | `ingest_worker._copy`, `_check_counts`, `_insert_ledger`, the delete in `supersede` | `tick_trade_projector.py` |
| Ingest's schema set | `STORED_TIERS` in `ingest_select`, `ingest_plan`, `ingest_worker` | `INGEST_PROJECTORS` keys |

**Consumers updated in this slice:**

- `ingest_select.py`: outranked subquery and selection set.
- `ingest_plan.py`: `_SUPERSEDED`.
- `ingest_worker.py`: projector calls; `_other_current_units` uses the unit's family.
- `purchase_plan.py`, `spend_guard.py`, `manifest_request_reads.py`, `manifest_pass.py`, `adopt.py`, `estimate.py`, and the pass and estimate renderers.
- `config/__init__.py`.

**Consumers deliberately left on tick-tier names, since they mean tick tiers:**

- `status_reads.Shape.params`, `manifest_reads.owned_tier_days`, `tick_status.day_unit`, `universe`, `planner._companion_wants`, `estimate._row`'s bundle;
- the ledger readers (`catalog_reads`, `get_plan`, `roll_inputs`);
- `scripts/proof_226/rebuild.py`.

Each keeps `STORED_TIERS` or `TICK_TIER_RANK`, both of which still derive from `TICK_TIERS`. A grep for `STORED_TIERS` and `TICK_TIERS` at the end of the slice must show only these.

**Behaviour verification:** the unit, integration and load tick tests pass unchanged, except imports of moved private helpers, and the D10 parity diff is empty.

### Database / Storage Schema

`tick_008_plan_inclusion` (tick track):

```sql
ALTER TABLE tick_request ADD COLUMN IF NOT EXISTS is_plan_included BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tick_request ALTER COLUMN is_plan_included DROP DEFAULT;
```

- Existing rows get `FALSE`: both adopted jobs were usage-billed (free credit).
- The default is dropped after the backfill, so every `INSERT` must name the column, and a writer that forgets fails on `NOT NULL`.
- A boolean needs no `CHECK`, and a plan added later needs no migration.
- Applied in production with `mt data migrate apply --track tick` (`TICK_MIGRATE_COMMAND`). The preflight already refuses a pass while a tick migration is pending, so no pass can run against the old shape.
- The tick backup regime covers the column with no change.

## Integration Points

### Provides to Other Slices

- **301, 302:** to add a schema, add it to `TickSchema` and `FAMILY_SCHEMAS` under a new family, add a projector to `INGEST_PROJECTORS`, add a row to every plan map in `PLAN_INCLUDED_HISTORY`, and widen `tick_request_schema_check` in a migration. Supersession, selection and the guard need no further change.
- **303:** usage-billed rows carry `NOT is_plan_included`, so the invoice comparison runs over them alone.
- **304:** `SCHEMA_FAMILY` is the vocabulary for per-family status and coverage.
- **306:** with `MT_TICK_DATABENTO_PLAN=standard`, a full-history `ohlcv-1m`, `statistics` and `definition` pull counts $0 against the ceilings. Without it, the guard prints the real $94 marginal figure.

### Consumes from Other Slices

- 220's slices only (222, 224, 225, 234). Nothing from this plan.

## Success Criteria

### Functional Requirements

1. With no new schema and `MT_TICK_DATABENTO_PLAN` unset, `mt data tick pass`, `ingest`, `status`, `coverage` and `estimate` behave as before on the held files. The D10 parity diff is empty.
2. A `tbbo` unit supersedes a current `trades` unit of the same shape and day, as today. A `definition` unit and a tier unit of the same shape and day never outrank or supersede each other. Both are integration tests against the family-scoped SQL.
3. Ingest takes each unit's writer from `INGEST_PROJECTORS`. Ingest never selects `definition` units, because the acquisition pass projects them (D3). A unit named with `--unit-id` whose schema has no projector (today, only `definition`) is reported `not_selectable`, naming its schema, as it is today.
4. Every new `tick_request` row, from the pass or from adopt, has `is_plan_included` set by `is_plan_included(...)`. With no plan configured it is `FALSE`.
5. With `MT_TICK_DATABENTO_PLAN=standard`, a planned `tbbo` request inside the trailing 12 months adds $0 to the per-pass and 30-day sums. A `tbbo` request older than that, or straddling the boundary, adds its full estimate. A `definition` request of any age adds $0.
6. Trailing included rows and included unheld jobs are excluded from the 30-day sum. An unheld job that no rule includes counts in full.
7. `mt data tick estimate` shows each row's plan inclusion and judges the marginal bundle against the ceiling.
8. An unknown `MT_TICK_DATABENTO_PLAN` value refuses the preflight and names the allowed values.

### Technical Requirements

- Unit tests:
  - `FAMILY_SCHEMAS` is total and disjoint over `TickSchema`;
  - `PLAN_INCLUDED_HISTORY` is total over `TickSchema` for every plan;
  - `is_plan_included`: inside, outside, straddling, the 365th day, another dataset, plan `None`;
  - `evaluate_spend` with included planned, trailing and unheld items;
  - `INGEST_PROJECTORS` keys equal `STORED_TIERS`.
- Integration tests:
  - `tick_008` on an empty tick database and on one holding requests (existing rows become `FALSE`; an insert without the column fails);
  - family-scoped supersession (criterion 2);
  - both writers set the column.
- Moved code keeps its tests. `ingest_worker` tests now exercise the worker through the registry.
- `ruff`, `ruff format` (scoped to touched files), and `mypy`/`pyright` are clean.
- Every source file stays within about 300 lines. The family table lives in `schema_families.py`, not `constants.py` (already 404 lines), and the plan table in `config/databento_plans.py`.
- The API reference is unchanged (D11). The inclusion table's comment cites the pricing page and the date it was read.

### Integration Requirements

- 301 can add `ohlcv-1m` by following "Provides to Other Slices" with no change to selection, supersession or guard code.
- The production tick pass (`mt-run tick`) runs after `tick_008` is applied with the plan unset, and buys and spends nothing different.

### Verification Walkthrough

All commands are run from the checkout root with the test database URL exported, per the project's test setup.

1. **The tests.**
   ```bash
   uv run pytest test/unit/data/tick -q
   uv run pytest test/integration/data -q -k "tick"
   ```
   Expected: all pass. The new tests include `test_schema_families.py`, `test_plan_inclusion.py`, the extended `test_spend_guard.py`, and an integration `test_tick_008_plan_inclusion.py`.

2. **Parity on the held files** (D10). `trading_tick_proof` was retired by slice 227 (no pg_hba entry; `provision_tick_cluster.sh` no longer creates it), so `proof_226_tick.py rebuild` cannot run on this host. Build the held archive twice into throwaway databases on the test cluster instead: for each of the base commit (a worktree) and the branch head, create a database from `MT_TIMESCALE_TEST_URL`, point `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` at it and a scratch directory, and run `uv run mt data init --database tick`, `uv run mt data tick adopt --job-id <job> --source <archive>/<job>` for every job directory in sorted order, `uv run mt data tick pass --estimate-only --json` and `uv run mt data tick ingest --json`. Then record `drill_228_fingerprint.fingerprint` and `drill_228_bookkeeping.table_md5` of `tick_definition` and `tick_ingest_ledger`, and drop the database.
   Observed (2026-10-30, 10 job directories): both sides planned 0 requests, ingested 129 units with 0 failed, and held 31,722,710 `tick_trade` rows in 1,835 fingerprint groups. The fingerprints are equal, `tick_definition` md5 is `ffa1efe417c4c31e2be8b16517786d76` and `tick_ingest_ledger` md5 is `d649539179c7d46ebdcaa6453dd5c74f` on both. The full record is in the task file's "Parity result".

3. **The marker.** Run on the head database before dropping it: `SELECT schema, is_plan_included, count(*) FROM tick_request GROUP BY 1, 2`. Observed: `definition false 6`, `tbbo false 3`, `trades false 1`. Expected: every row `false` (no plan configured).

4. **Plan inclusion in the estimate** (free metadata calls only). The pass's guard with a plan is proven by the unit and integration tests above, because the production universe holds no in-plan days to plan.
   ```bash
   uv run mt data tick estimate --symbols ES.FUT --stype parent --start 2026-09-01 --end 2026-09-08 --json
   MT_TICK_DATABENTO_PLAN=standard uv run mt data tick estimate --symbols ES.FUT --stype parent --start 2026-09-01 --end 2026-09-08 --json
   ```
   Run it with `MT_TICK_SPEND_CEILING_USD=0.50` exported. Observed (2026-10-30): plan unset gives `is_plan_included: false` on every schema, `trades` (2.0809) and `tbbo` (3.4681) `over`; with `standard`, all four `true` and `trades` and `tbbo` `within`, `cost_usd` identical. Expected: the first run shows `is_plan_included: false` on every schema, and its verdicts match today's. The second shows `is_plan_included: true` on `trades`, `tbbo`, `mbp-1` and `definition`, and the bundle verdict is `within` even at the $0.50 ceiling. `cost_usd` is unchanged (list price) in both.

5. **Unknown plan refused.**
   ```bash
   MT_TICK_DATABENTO_PLAN=platinum uv run mt data tick pass --estimate-only
   ```
   Observed: a pydantic `ValidationError` at CLI startup, `Value error, MT_TICK_DATABENTO_PLAN='platinum' is not a plan; allowed: standard`, raised before any provider is built. Expected: refused before any provider call, naming `MT_TICK_DATABENTO_PLAN` and the allowed value `standard`.

6. **Production, after merge.** Apply `tick_008` with `mt data migrate apply --track tick`, then run the existing production check, `sudo mt-run data health`. Expected: the tick lines are unchanged, and the next `mt-run tick` firing reports the same plan as before, with its spend lines unchanged.

## Risk Assessment

### Technical Risks

- **A missed `STORED_TIERS` or rank use.** A query that still filters by the old set behaves identically today, because the sets are equal, and breaks only when 301 adds a schema. Parity cannot catch it.
- **The trailing-12-month boundary** may not be how Databento counts. If Databento's window is wider, the guard overcounts; if narrower, a plan-inclusion claim could hide a charge.

### Mitigation Strategies

- The end-of-slice grep in the Migration Plan lists every remaining `STORED_TIERS`, `TICK_TIERS` and `TICK_TIER_RANK` use, each justified as tick-tier-only. A unit test in `test_schema_families.py` builds the selection and supersession SQL with a test-only extra family and asserts that the family predicate is present.
- For the boundary: no plan is configured until a subscription starts. When it does, the first in-plan pull's invoice line (or its absence) is compared with the marker. That check belongs to the slice that first runs with a plan, and 303's reconciliation covers the usage-billed side.

## Implementation Notes

### Development Approach

Relative effort 4/5. Suggested order, with a commit at each:

1. `schema_families.py` and `supersedes_sql`; switch `ingest_select` and `ingest_plan`; family supersession integration test.
2. `projectors.py` and `tick_trade_projector.py`; the worker through the registry; selection by registry keys. Existing ingest tests stay green.
3. `config/databento_plans.py`, `is_plan_included`, the setting; unit tests.
4. `tick_008`; both writers; migration tests.
5. Guard, purchase plan, trailing read, unheld classification, renderers; estimate changes.
6. The parity run (D10), with the result recorded in the slice's Verification Walkthrough at Phase 6.

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit (project memory).
- The architecture sets no performance requirement for these paths, and this slice adds none. The changes add no query per unit or per request: `supersedes_sql` replaces one rank expression with another of the same shape, and `is_plan_included` is a pure function. Manifest-scale timing is measured in 304, before the history acquisition.
- `supersedes_sql` interpolates SQL built from constants. It keeps `tier_rank_sql`'s rule: arguments are column references or placeholders written in code, and every value is still bound.
- The tick-tier family name stays "tier" in operator text. "Family" is internal vocabulary, and no operator-visible string changes in this slice except the guard's marginal/list line and the estimate's inclusion column.
