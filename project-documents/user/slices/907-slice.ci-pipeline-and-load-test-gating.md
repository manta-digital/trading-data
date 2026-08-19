---
docType: slice-design
slice: ci-pipeline-and-load-test-gating
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [905]
interfaces: [146, 167, 917]
dateCreated: 20260726
dateUpdated: 20260819
status: not_started
review: none
---

# Slice Design: CI pipeline and load-test gating

## Overview

**This repo has no CI gate.** `.github/workflows/ci.yml` does exist, but it is
publish-only: it triggers on `v*` tags and runs `uv build` plus a PyPI publish
(slice 908). It is *named* "CI" while gating nothing, which is worse than an
empty directory — a reader assumes coverage the repo does not have, and the
first task of this slice is to make the name honest. Every quality gate the
project has adopted — ruff, mypy, pytest, and the load tier — still runs only
when someone remembers to run it locally.

The gap surfaced twice, in the same shape. Slice 146 added
`test/load/test_146_part1_nfrs.py` and `test_146_part2_nfrs.py`, both gated on
`MT_RUN_LOAD_TESTS=1` with a docstring stating "CI must enable" — nothing ever
did, so those NFR assertions have never run automatically. Slice 167's task
review (F002) caught the same claim being made a second time for its
sub-second `data_status` load test, and the PM ruled the CI work out of 167's
scope and into this chore rather than let a second NFR ship with an aspirational
gate.

This slice stands up the runner so the gates that already exist actually fire.
It is deliberately **not** a testing-strategy slice: it adds no new tests and
changes no thresholds. Everything it runs is already written and already
passing locally.

## Current state (re-verified 2026-08-19)

| Gate | Configured | Runs automatically |
|---|---|---|
| `ruff` (`[tool.ruff.lint]`, `E,F,W,I,UP,BLE,ASYNC,B`) | yes | no |
| `mypy` (`[tool.mypy]`) | yes | no |
| `pytest` unit / integration | yes | no |
| `test/load/` NFR tier (`MT_RUN_LOAD_TESTS=1`) | yes | **no — never once** |
| PyPI publish (`ci.yml`) | yes | yes — on `v*` tags only |

Two known constraints the pipeline must respect, both discovered during
slices 163–168:

- **Whole-`test/` collection is broken**, and the cost is now measured.
  `pytest test/ --collect-only` gives **25 collection errors and 1,838 tests
  collected**, against **1,991 unit tests** when the suites are invoked
  per-subpackage — so roughly 150 tests silently do not collect. The cause is
  missing `__init__.py` at `test/`, `test/unit/`, `test/integration/`,
  `test/performance/`, and `test/fixtures/` (only `test/load/` has one), which
  leaves pytest deriving colliding module names such as
  `data.acquisition.daily.test_freshness`. Either fix the packaging or encode
  the per-subpackage invocation — a CI step that silently collects a subset is
  the precise failure this slice exists to end.
- **The failure baseline has changed completely since this design was written.**
  The 2026-07-26 list (`test_daily.py::...test_4xx_non_429_propagates`,
  `test_outcomes.py::...[404]`, 12 errors in `test_equity_universe.py`) is
  obsolete — quarantining it would silence tests that now pass while missing the
  ones that fail. The baseline verified at slice 169's close, 2026-08-18, is:
  **unit 1,991 passed / 0 failed; integration 171 passed with 2 failures; load 2
  passed** (the load tier needs `MT_RUN_LOAD_TESTS=1`). Both integration
  failures are in `test_cli_lists.py`, which asserts on an operator-config
  symbol list named `priority1`; `config/symbol-lists.yaml` defines only `smoke`
  and `sp500`. A pipeline that goes red on day one gets ignored, so these must be
  explicitly quarantined (xfail/skip with reason, or excluded with a tracking
  note) rather than left to fail. **Re-verify the baseline at implementation
  time** rather than trusting this paragraph — that it went stale once is the
  reason it is called out.
- **A rotating flake will make the pipeline intermittently red for reasons
  unrelated to the code under test.** One test per full integration run errors
  with `psycopg.errors.InternalError_: tuple concurrently updated/deleted`
  during DDL — a *different* test each run (observed in
  `test_data_status_equivalence`, `test_migration_050`,
  `test_migration_051_052`), all passing in isolation. It is a catalog race
  between ephemeral test-database DDL and the TimescaleDB background workers
  serving production, which share one cluster on this host. **Slice 917 owns the
  fix** (a dedicated test cluster) and should land before this slice; do not
  quarantine the flake here, because the affected test is not stable enough to
  name.
- **`test/integration/` carries ~865 pre-existing ruff errors.** Lint the tree
  the project actually keeps clean, not that one, or scope the lint step
  accordingly.

## Scope

1. **CI config** (`.github/workflows/`) running on push and PR: install via
   `uv`, then ruff → mypy → pytest per-subpackage.
2. **Load-tier job**: a separate job or step that sets `MT_RUN_LOAD_TESTS=1` so
   `test/load/` executes. This is the point of the slice — the 146 and 167 NFRs
   get a real gate. It must not require prod: the load tests run against a
   fixture or ephemeral DB, never `192.168.1.144`.
3. **DB-dependent tests**: decide per-suite whether CI provisions an ephemeral
   TimescaleDB service container or skips them. Skipping is acceptable if
   recorded; silently passing because a fixture DB was absent is not. If slice
   917 has landed, the dedicated test cluster is the third option and changes
   this decision — settle the sequencing before breaking this into tasks.
4. **Baseline quarantine** of the known failures above, each with a reason and a
   tracking reference.
5. **Retire the aspirational docstrings** in `test_146_part1_nfrs.py` /
   `test_146_part2_nfrs.py` (and 167's, once it lands) — replace "CI must
   enable" with what CI actually does.

## Out of scope

- New tests, new NFR thresholds, or changes to existing assertions.
- Fixing the pre-existing failures — quarantine and track; fixing them is
  slice 905's and their owning slices' business.
- Separating the test databases from the production cluster — that is slice 917,
  and this slice consumes the result rather than doing the work.
- Release/publish automation, coverage reporting, or branch protection rules.

## Constraints

- **No secrets in config.** The DB URL and any credentials come from repository
  secrets or an ephemeral service container, never committed. `.env` values are
  double-quoted in this project — anything reading them must de-quote.
- **Never point CI at the production DB.** `192.168.1.144` is this host: it is
  production, the developer checkout, and the only PostgreSQL cluster, all at
  once. A CI job must not connect to it, and must not be capable of connecting
  to it by misconfiguration. (The acquisition daemon is *not* continuously
  running — it is invoked by hand and exits on `--stop-when-done` — but the
  4.4-billion-row hypertable is live regardless, so the constraint is unchanged.)
- The load tier asserts latency, so runner variance matters: if a shared runner
  cannot hold the assertions reliably, say so and gate the tier on a
  self-hosted runner or a manual trigger rather than loosening the thresholds.

## Verification

1. A deliberately-introduced ruff violation fails the pipeline.
2. A deliberately-introduced type error fails the pipeline.
3. A deliberately-broken assertion in a unit test fails the pipeline.
4. `test/load/` is confirmed to have **executed**, not skipped — assert on the
   run summary, since a silently-skipped load tier is the exact failure this
   slice exists to end.
5. A clean `main` produces a green run with the quarantined baseline documented.

## Success Criteria

1. CI runs on push and PR and is red when any gate fails.
2. `test/load/` executes under `MT_RUN_LOAD_TESTS=1`, with evidence it ran.
3. Slices 146 and 167 no longer claim CI gating they do not have — the
   docstrings match reality.
4. No credential or DB URL is committed; CI cannot reach the production DB.
5. The pre-existing failure baseline is quarantined with reasons, so a green run
   means something.
