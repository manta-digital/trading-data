---
docType: slice-design
slice: data-cli-module-decomposition
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [905]
interfaces: []
dateCreated: 20260726
dateUpdated: 20260726
status: not_started
---

# Slice Design: `mt data` CLI module decomposition

## Overview

`src/manta_trading/cli/commands/data.py` is **3,371 lines** — more than ten
times the ~300-line guideline, and an order of magnitude larger than every
other module in `cli/commands/` (the next largest, `universes.py`, is 6.7 KB).
It was flagged as F009 during the slice 163 code review and deferred as a
standalone chore; slice 168's review re-raised file length as a category and
established the distinguishing rule: **file length over guideline is acceptable
when the excess is not code complexity.** `cagg_freshness.py` qualified for that
exemption because its bulk is docstrings and incident write-ups. `data.py` does
not. Its excess is executable code, so the exemption does not apply and the
file needs to be split.

The growth has a single structural cause: `data.py` hosts **eight Typer
sub-apps** plus the root `data_app`, and every group added since slice 900 was
appended to the same module rather than given its own. Measured footprint:

| Region | Lines | Commands |
|---|---|---|
| `data_app` (root) | ~1,417 | `init`, `status`, `extend`, `rechunk`, `get`, `pull` |
| `caggs_app` | ~674 | `refresh`, `status`, `verify`, `repair` |
| `ca_app` | ~367 | `update`, `show`, `list` |
| `instruments_app` | ~228 | `list`, `rebuild`, `populate-delisted-dates` |
| `lists_app` | ~166 | `ls`, `show`, `refresh-sp500` |
| `daemon_app` | ~152 | `run` |
| `migrate_app` | ~139 | `apply`, `status` |
| `calendars_app` | ~136 | `list`, `holidays` |
| module header | ~92 | imports, sub-app construction, `add_typer` wiring |

A ninth group, `universes_app`, already lives in its own module
(`cli/commands/universes.py`) and is attached with `data_app.add_typer(...)` at
[data.py:79](src/manta_trading/cli/commands/data.py#L79). **That is the
precedent this slice generalizes** — the pattern is proven in-tree, and the
test suite already mirrors it (`test/unit/cli/commands/test_data_pull.py`,
`test_data_caggs.py`, `test_data_ca.py`, `test_data_init.py`), so the target
layout matches how the tests are already organized.

There is a second, compounding cause worth separating from the first: command
bodies carry business logic rather than argument marshalling. The largest
entry points are far past the ~50-line function guideline — `data_pull` 244,
`caggs_status` 202, `data_get` 192, `data_extend` 175, `data_status` 165,
`caggs_verify` 151, `daemon_run` 147, `ca_update` 143. They open connections,
issue SQL, format tables, and branch on `--json` vs. human output inline. The
`_pull_*` helper family (`_pull_verify`, `_pull_fetch_inner`,
`_pull_query_unknown_gaps`, and seven more) shows the correct decomposition
already exists locally for one command; it was simply never applied to the
others.

**Scope boundary.** This slice performs the *file split only* — a mechanical,
behavior-preserving move. Pushing SQL and formatting logic down out of the
command bodies into `market/` and `data/` is a genuine refactor with real
regression risk against a live production daemon, and it is explicitly **out of
scope here**; it is recorded as follow-on work below. Splitting first is what
makes that later refactor reviewable at all, since each group's logic becomes
independently readable.

## Target layout

Convert the module to a package, one module per sub-app, with the package
`__init__.py` assembling the Typer tree and re-exporting `data_app` so that
[cli/app.py:10](src/manta_trading/cli/app.py#L10) (`from
manta_trading.cli.commands.data import data_app`) is untouched:

```
cli/commands/data/
  __init__.py      # constructs data_app, add_typer wiring, re-exports data_app
  core.py          # init, status, extend, rechunk, get
  pull.py          # pull + the _pull_* helper family
  caggs.py         # refresh, status, verify, repair + _resolve_minute_granularities
  ca.py            # update, show, list + _query_splits/_query_dividends/_ca_cursor
  instruments.py   # list, rebuild, populate-delisted-dates
  lists.py         # ls, show, refresh-sp500
  daemon.py        # run
  migrate.py       # apply, status
  calendars.py     # list, holidays
```

`universes.py` stays where it is and is wired in from the new `__init__.py`
exactly as it is today. Shared private helpers currently used across groups
(`_validate_credentials`, `_create_timescale_db`, `_create_instrument_registry`,
`_get_timescale_url`) move to a `_shared.py` in the package rather than being
duplicated — duplicating them would trade a size violation for a DRY violation.

Post-split every module lands within or near the guideline, with `core.py` and
`caggs.py` the largest and the only plausible candidates for a further split
once the logic-pushdown follow-on runs.

## Constraints

- **Behavior-preserving.** No command signature, option name, help text, output
  format, or exit code changes. The public CLI surface after this slice is
  byte-identical to before.
- **Import path stability.** `manta_trading.cli.commands.data.data_app` must
  keep resolving. Existing test imports must keep working, or be updated in the
  same commit as the move that breaks them — never left in a broken intermediate
  state.
- **Ordering vs. slice 905.** Slice 905 remediates lint/type debt across the
  tree and specifically calls out `F821` undefined names *in this file*
  (`datetime`, `psycopg` referenced on paths where they are not imported —
  latent `NameError`s). Doing 905 first means those latent bugs are diagnosed
  and fixed while the code is still in one place, and the split then moves
  known-clean code. Doing the split first would scatter the F821 sites across
  ten new modules mid-triage. **Hence dependency [905].** If the PM chooses to
  invert the order, the F821 findings must be resolved as part of this slice
  instead of being carried across the move.
- **Live daemon.** `daemon_run` is the production acquisition entry point
  running continuously on prod. Its move must be verified by an actual
  invocation, not by test suite alone.

## Verification

1. `uv run mt data --help` and `--help` on all nine sub-apps produce output
   identical to a pre-split capture (diff the captures; do not eyeball).
2. Per-subpackage test runs green with no change in pass count:
   `test/unit/cli/`, `test/unit/test_cli_data.py`, plus `test/unit/market/` and
   `test/unit/data/` for import-graph fallout. Baseline is the known
   pre-existing failure set on `main` (2 failures in `test_daily.py` /
   `test_outcomes.py`, 12 live-DB errors in `test_equity_universe.py`) — the
   split must not add to it.
3. `uv run --extra dev ruff check` clean on every touched file.
4. `mt data daemon run` starts and reaches its first acquisition cycle on prod.
5. No file in the new package exceeds ~650 lines; the count for each is
   recorded in the task file.

## Follow-on work (not this slice)

- **Logic pushdown.** Move SQL and table formatting out of the oversized
  command bodies into `market/` and `data/`, bringing entry points toward the
  ~50-line guideline. Should be scoped per-group after the split, and each
  group is then a small independent unit rather than one 3,300-line change.

## Success Criteria

1. `data.py` no longer exists as a single module; the package layout above is in
   place with `universes.py` wired unchanged.
2. `from manta_trading.cli.commands.data import data_app` resolves; `cli/app.py`
   is unmodified.
3. All nine `--help` captures diff clean against pre-split baselines.
4. Test suite pass count unchanged against the documented baseline.
5. Shared helpers exist in exactly one place (`_shared.py`), not duplicated.
6. Every new module is within or near the ~300-line guideline, none over ~650,
   with counts recorded.
