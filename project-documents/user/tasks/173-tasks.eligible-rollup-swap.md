---
docType: tasks
slice: eligible-rollup-swap
project: trading-data
lld: user/slices/173-slice.eligible-rollup-swap.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [172, 168, 169]
interfaces: [182, 186, 190]
projectState: >
  Slice 172 is built and merged (released as v0.24.1). Migration 063 creates
  minute_{5min,15min,hourly,4hour}_ohlcv_next and minute_coverage_next, WITH
  NO DATA; scripts/cutover_172_eligible_rollups.py builds and verifies them in
  production (Phase 7 of 172). The minute migration chain ends at 063. The
  current rollups and minute_coverage still aggregate raw values, and
  /api/v1/bars and get_minute_data default to prints=all (prints=eligible is
  1m only, via PRINTS_ELIGIBLE_1M_ONLY_MESSAGE).
dateCreated: 20261017
dateUpdated: 20261017
status: not_started
---

# Tasks: Eligible Rollup Swap

## Context Summary

- Working on slice 173. Slice 172 built the eligible `_next` rollups beside
  the current ones. This slice swaps them in by rename, makes
  `prints=eligible` the default for every granularity, adds a value-parity
  health check, and re-runs the NFR targets the swap touches.
- **The design is the specification.** Swap steps, failure table, API
  contract, health-check rules and NFR pass numbers: see the slice design's
  Technical Decisions. Tasks cite it rather than repeat it.
- Release gate (Phase 7, not a task here): the release goes out only after
  `mt data caggs verify --target next` exits 0 in production, and through the
  cutover script written in Section 5, not `upgrade_production.py`.
- Not in this slice: the `_next` build (172); daily-family rollups.
- Next planned slice: none named in the design; 182/186/190 consume the
  default change.

**Conventions for every task below**

- Run from the repository root. Confirm with `pwd` before file or shell work.
- **Target branch:** read `cf config get git.integration_branch`; empty means
  `main`. Work on `173-slice.eligible-rollup-swap`, forked from the target per
  the Git Rules.
- **Tests never read the production URL.** Integration and load tests need
  `MT_TIMESCALE_TEST_URL` exported from `.env` (strip the quotes). Run the
  unit, integration and load tiers separately. Never mix
  `test/unit/api_server` and `test/integration/api_server` in one run.
- `ruff format` only the files the task touched. Before each commit, run
  `git diff <target> --stat` and look for deletions in files you did not mean
  to change.
- mypy: one invocation covering the src kalshi paths plus tests, never a
  narrower run.
- View names are derived with `target_view_name` / `CAGG_NEXT_SUFFIX` /
  `CaggTarget` (172), never spelled out. Any new name or comparison value
  (the `_prev` suffix, the default print mode) is defined once in
  `src/manta_trading/constants.py`.
- Known failures on a clean base (migration chain-tip, test cluster has no
  TimescaleDB scheduler) are listed in project memory; do not investigate
  them as regressions.
- Commit messages use the project's semantic prefixes and end with the
  attribution lines in force for the session.

---

## Section 1 — Rename probe (effort 2/5)

- [ ] **1.1 Probe cagg rename on the test cluster (TimescaleDB 2.29.1)**
  - [ ] Write a throwaway script outside `src` (e.g. under `scripts/`, not
        committed) that, in a scratch database it creates itself, tests:
    1. `ALTER MATERIALIZED VIEW <cagg> RENAME TO ...` inside a transaction,
       then commit;
    2. the same rename on a cagg that has a hierarchical cagg on top of it
       (build a small 2-level pair like 4h → coverage);
    3. that the upper cagg still refreshes and reads after its parent is
       renamed, and that the policy jobs follow the rename.
  - [ ] Never touch the test database's real objects or production.
  - [ ] Record the result in the slice design under Technical Decisions →
        The swap: one line per probe (works / refused, with the error text).
  - [ ] Success: the design states which path 064 takes (transactional
        renames, or guarded autocommit fallback). **If either probe is
        refused, stop and tell the Project Manager before Section 2**, since
        the failure table and success criterion 2 change.

- [ ] **1.2 Commit:** `docs: record cagg rename probe results for 173`

## Section 2 — Migration 064 (effort 4/5)

- [ ] **2.1 Constants and swap helper**
  - [ ] In `constants.py`: `CAGG_PREV_SUFFIX: Final[str] = "_prev"` with a
        one-line comment naming slice 173.
  - [ ] Add a pure function beside `eligible_rollups_sql.py`'s helpers (new
        module `market/schema/eligible_swap_sql.py`, to keep `minute.py` from
        growing) returning the ordered list of swap pairs: the four rollups
        and `minute_coverage`, each as (current, next, prev) names, derived
        from the 172 helpers.
  - [ ] Success: unit test asserts the five triples; `ruff` and `mypy`
        clean.

- [ ] **2.2 Guarded rename and drop statements**
  - [ ] In `eligible_swap_sql.py`, render the swap per the probe's path.
        Transactional path: one transaction holding every rename (current →
        `_prev`, `_next` → current) and `CREATE OR REPLACE VIEW data_status`
        using the existing `data_status` creation helper from 169 (find it
        with `grep -rn "VIEW data_status" src`; do not copy its SQL). Fallback
        path: the same statements as separate guarded autocommit steps.
  - [ ] Every step guarded: a rename runs only if its source exists and its
        target name is free; the `data_status` replacement is idempotent.
  - [ ] Drops are separate statements after the commit: `IF EXISTS`,
        `minute_coverage_prev` first (it sits on the 4h `_prev`), then the
        four rollup `_prev` views.
  - [ ] Success: a unit test renders the statements and asserts order (all
        renames before drops; coverage dropped before the 4h rollup) and that
        no statement lacks a guard.

- [ ] **2.3 Add migration 064**
  - [ ] Append `064_minute_eligible_rollup_swap` to `MINUTE_MIGRATIONS`
        after 063, `requires_autocommit: True` if the probe path or the
        drops need it, with a description stating what it swaps, that the
        old rollups are dropped, and that it is applied by
        `scripts/cutover_173_eligible_rollup_swap.py`.
  - [ ] Bump the chain-tip test (`test_chain_ends_at_063` →
        `test_chain_ends_at_064`) and the migration count in
        `test/unit/test_schema_migrations.py`.
  - [ ] Success: `uv run mt data migrate apply` on the test database applies
        064; a second run is a no-op.

- [ ] **2.4 Migration 064 integration tests**
  - [ ] New `test/integration/test_migration_064_eligible_rollup_swap.py`:
    1. cold start (063 then 064 on empty objects): the five current names
       exist and hold eligible definitions; no `_next` or `_prev` remains;
       `data_status` reads;
    2. populated: a database holding both sets with data; after 064 the
       current names return the former `_next` rows, `_prev` and `_next`
       are gone, refresh and columnstore jobs exist on the current names;
    3. partial-apply rerun: stop after the renames (drop skipped), rerun,
       converges; and a rerun on a fully applied database is a no-op;
    4. reader gap (transactional path only): a thread reading `data_status`
       and the 4h rollup in a loop while 064 runs sees no missing-name
       error;
    5. counts unchanged: the `data_status` rows, the coverage index and the
       counts behind `mt data accounting` and `/api/v1/status` are equal
       before and after 064.
  - [ ] Success: the integration tier file passes. Re-run any flake alone
        before investigating.

- [ ] **2.5 Commit:** `feat: add migration 064 eligible rollup swap`

## Section 3 — Read path (effort 3/5)

- [ ] **3.1 Default print mode and the 5m+ rule**
  - [ ] In `constants.py` define the default once
        (`DEFAULT_PRINT_MODE: Final[PrintMode] = PrintMode.ELIGIBLE`) and
        replace `PRINTS_ELIGIBLE_1M_ONLY_MESSAGE` with
        `PRINTS_ALL_1M_ONLY_MESSAGE` ("prints=all is served at 1m only; roll
        up the 1m bars"). Update all six importing files found by
        `grep -rn "PRINTS_ELIGIBLE_1M_ONLY_MESSAGE" src test`.
  - [ ] Read `bars.py` around the `prints` parameter and decide, from the
        design, how `prints=all` behaves at daily and coarser: the design
        says `all` is valid at 1m only, so reject it there too. If the
        existing daily route makes that unclear, stop and ask the Project
        Manager.
  - [ ] Success: one definition of the default; `ruff`/`mypy` clean.

- [ ] **3.2 Rollup reads serve eligible values**
  - [ ] `timescale_minute_db.get_minute_data`: default `prints` is the 3.1
        constant. `ELIGIBLE` with an aggregation reads the rollup via
        `_get_aggregated_data`; `ALL` with an aggregation raises
        `ValueError(PRINTS_ALL_1M_ONLY_MESSAGE)`.
  - [ ] The aggregated query adds `AND open IS NOT NULL`, so a bucket of only
        all-marked minutes is absent, the same as an empty bucket. Comment why
        (`adjusted()` must never see a NULL price).
  - [ ] Update the method docstring (drop the "until slice 172" note).
  - [ ] Success: unit tests with a mocked pool assert the generated query
        contains the filter, and that `ALL` + aggregation raises.

- [ ] **3.3 Bars route and CLI**
  - [ ] `api_server/routes/bars.py`: `prints` defaults to the 3.1 constant;
        `prints=all` with `granularity != 1m` returns `422` with the new
        message; the route docstring and OpenAPI description state the new
        default.
  - [ ] `cli/commands/data.py` `get`: the `--prints` default and the 1m-only
        guard follow the same constant and rule, so the CLI and API agree.
  - [ ] Success: `uv run mt data get --help` shows the new default.

- [ ] **3.4 Read-path tests**
  - [ ] Update `test/integration/api_server/test_bars_prints.py`,
        `test/unit/api_server/test_bars.py` and
        `test/unit/cli/commands/test_data_get.py` for the new default and
        message; no test may still import the removed constant.
  - [ ] Add: default request at 5m returns `prints: "eligible"`; `prints=all`
        at 5m is 422 with the message; `prints=all` at 1m is unchanged; a
        bucket whose minutes are all marked is absent from the 5m response
        (use 171's off-tape fixtures in `test/fixtures/off_tape/`, mark, refresh).
  - [ ] Criterion 1: for the SPY, AVGO and MSFT cases in slice 171's success
        criteria, assert the off-tape values are absent from the 5m **and**
        the 1h bars, one assertion per granularity.
  - [ ] Success: unit tier and `test/integration/api_server` pass, run
        separately.

- [ ] **3.5 Commit:** `feat: default bars to prints=eligible at every granularity`

## Section 4 — Value-parity health check (effort 3/5)

- [ ] **4.1 Pure comparison rule**
  - [ ] New module `cli/commands/health_value_parity.py`, modelled on
        `health_off_tape.py`: a frozen dataclass for the sampled result and a
        pure `check_value_parity(...) -> HealthCheck` that fails with the
        reason and the first differing bucket for: a differing bucket; a
        differing NULL bucket (compared with `IS NOT DISTINCT FROM`); no
        marked minute in the session. Passes otherwise.
  - [ ] Success: unit tests for the three failure states and the pass state,
        in `test/unit/cli/commands/test_health_value_parity.py`.

- [ ] **4.2 Sampling and recompute (I/O)**
  - [ ] `fetch_value_parity(conn, judged_session)` in the same module:
    1. choose the symbol by query: most marked minutes in the judged session,
       ties broken by symbol;
    2. compare every 5m rollup row for that symbol and session, every column
       including `minute_count`, against the eligible 1m bars rolled up in SQL
       (render the expressions from `eligible_sql.py`; do not retype them).
  - [ ] Wire `gather_value_parity` into `health.gather` beside
        `gather_off_tape`, using the same judged session.
  - [ ] Success: `mt data health` lists a "value parity" line.

- [ ] **4.3 Health integration tests**
  - [ ] In `test/integration/`, on fixture minutes with marks: passes when the
        rollup matches; fails naming the bucket when a rollup row is altered
        (update one `_next`-origin row's high); fails with the no-marked
        reason on an unmarked session; the sample query picks the
        most-marked symbol, ties by symbol.
  - [ ] Success: tests pass; update `test_data_health.py` expectations for the
        extra check.

- [ ] **4.4 Commit:** `feat: add value-parity health check for eligible rollups`

## Section 5 — Cutover script (effort 4/5)

- [ ] **5.1 Write `scripts/cutover_173_eligible_rollup_swap.py`**
  - [ ] First read `scripts/cutover_172_eligible_rollups.py`,
        `cutover_172_build.py` and `cutover_common.py` (hold/release timers,
        install, `migrate`, `verify_migrate_target`,
        `restart_serve_if_active`). Reuse them; do not copy logic.
  - [ ] One command, `<ref>` argument, check-then-act steps in this order:
        precheck → hold timers → install → apply 064 → verify → restart
        mt-serve → release timers → report. Keep the file near 300 lines; put
        reusable pieces in a `cutover_173_checks.py` sibling like 172 did.
  - [ ] Precheck: every `_next` object exists and `caggs verify --target next`
        and `rebuild-coverage --verify --target next` pass. On failure exit
        non-zero naming the view; nothing is changed and timers are not held.
  - [ ] Swap-state detection: a rerun detects which of the current, `_next`
        and `_prev` objects exist and skips completed steps. A rerun after the
        swap committed does not re-run the precheck against `_next` (it no
        longer exists).
  - [ ] Failure handling exactly as the design's failure table: a
        rolled-back rename transaction releases the timers and exits
        non-zero; any later failure (autocommit fallback, a `_prev` drop,
        post-swap `verify`, coverage freshness) leaves the timers **held**
        and exits non-zero naming the step.
  - [ ] Post-swap checks: `caggs verify` on all four rollups, coverage
        freshness, the value-parity check (4.2), and that no `_next` or
        `_prev` object remains (query the catalog for the derived names).
  - [ ] Report to `project-documents/user/notes/<date>-173-cutover.md`: each
        step's outcome, row counts per rollup, and the verify output. Exit 0
        only when every step passed.
  - [ ] Success: `uv run python scripts/cutover_173_eligible_rollup_swap.py --help`
        runs; `ruff` and `mypy` clean.

- [ ] **5.2 Cutover failure-table tests**
  - [ ] Unit tests in `test/unit/test_cutover_173.py` (follow how the 172 script's tests
        stub the host commands): one case per row of the design's failure
        table asserting exit code, whether timers end held or released, and
        the step named in the message; plus a kill-and-rerun case that
        completes the swap.
  - [ ] Success: unit tier passes. Tests never run real `systemctl`.

- [ ] **5.3 Commit:** `feat: add cutover script for the eligible rollup swap`

## Section 6 — 5m bars load case (effort 2/5)

- [ ] **6.1 Add the 5m case to the bars tier**
  - [ ] In `test/load/test_187_api_nfr.py`, add one test beside
        `test_bars_request_latency_at_the_admission_ceiling`: a 5m request at
        the 5m admission ceiling (derive the window from the admission
        check's constant as the 1m test does), default `prints`, asserted
        under `_BARS_BOUND_S` and returning 200.
  - [ ] The fixture database needs eligible `_next`-style rollup values under
        the current names; apply 064 in the fixture via the migration chain
        (it already does once 064 is in `MINUTE_MIGRATIONS`).
  - [ ] Do not add 15m, 1h or 4h cases.
  - [ ] Success: the new test passes at the load tier (this file only; the
        full tier takes about 77 minutes and is run in Section 8).

- [ ] **6.2 Commit:** `test: add 5m bars case to the 187 API NFR tier`

## Section 7 — Documentation (effort 2/5)

- [ ] **7.1 Architecture amendment**
  - [ ] In `user/architecture/140-slices.data-quality-operations.md` record
        the three changes: the four minute caggs project tape-eligible
        values; `get_minute_data` and `/api/v1/bars` default to eligible;
        `verify` stays the count-parity detector with the value-parity
        health check guarding the definition.
  - [ ] Success: a reader of 140 alone learns the caggs are eligible.

- [ ] **7.2 API references**
  - [ ] Update `docs/api/reference.md` and `docs/api/agents.md`: new `prints`
        default, `all` valid at 1m only, the 422 message, NULL-bucket
        behavior, flagged as a client-visible change. Regenerate
        `docs/api/openapi.json` the way slice 190 documents, then run
        `uv run python scripts/check_api_docs.py`.
  - [ ] Success: `check_api_docs.py` exits 0.

- [ ] **7.3 CHANGELOG and runbook**
  - [ ] `CHANGELOG.md`: a `Changed` entry for the default (client-visible,
        raw still at 1m with `prints=all`) and the swap.
  - [ ] Runbook `100-production-operations.md`: add a section for
        `cutover_173_eligible_rollup_swap.py` in the 172 section's format
        (steps, failure table summary, rerun rule, report path); update the
        line that says `prints=all` is the default (near the existing
        "unaffected" note).
  - [ ] Success: `grep -rn "prints=all (the default)" docs project-documents/user/runbooks`
        returns nothing.

- [ ] **7.4 Commit:** `docs: document the eligible rollup swap and default change`

## Section 8 — Final validation and NFR re-runs (effort 3/5)

- [ ] **8.1 Static and tier checks**
  - [ ] `ruff check` on touched files; mypy in the single wide invocation;
        unit tier, then integration tier, each run separately and in full.
  - [ ] Success: all clean except the failures already known on the base
        (see conventions); list any that are new and investigate them with
        the actual error text first.

- [ ] **8.2 NFR re-runs, recorded**
  - [ ] Run `test/load/test_167_data_status_nfr.py`,
        `test_169_coverage_freshness_probe_nfr.py` and `test_187_api_nfr.py`
        (the load tier's known pre-existing failures are in project memory;
        never cap the run under 90 minutes). The load tier is a manual gate;
        wiring it into CI belongs to slice 907 (see slice 187, D9), so no
        wiring task is added here.
  - [ ] Record each result against its pass number from the design's NFR
        table, plus the `COVERAGE_BUCKET_LAG_BUDGET` check, in a new
        `project-documents/user/notes/173-nfr-results.md` with front matter.
  - [ ] Success: each target meets its pass number, or the file states the
        miss and the figure and the Project Manager is told.

- [ ] **8.3 Design walkthrough and traceability**
  - [ ] Walk the design's Success Criteria 1-5, Technical Requirements and
        Integration Requirements one by one against tests and notes; list any not covered by an automated
        test in the notes file (mt-ui's 5m-and-up charts showing no spikes is
        one: it is checked in the Phase 7 walkthrough), with how it is verified in Phase 7
        (production cutover).
  - [ ] Set the design's and this file's `status` per the process guide.
  - [ ] Success: every criterion maps to a test, a note, or a named Phase 7
        check.

- [ ] **8.4 Commit:** `docs: record 173 validation and NFR results`
