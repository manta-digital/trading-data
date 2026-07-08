---
docType: task-breakdown
slice: universe-delisted-filter
project: trading
sliceRef: user/slices/158-slice.universe-delisted-filter.md
dependencies: [157]
dateCreated: 20260512
dateUpdated: 20260512
status: complete
---

# Tasks: `--universe` Delisted Filter + `--include-delisted` Flag

## Context

Slice 158 narrows `mt data pull --universe` to active-only instruments
(`delisted_at_eodhd = FALSE AND delisted_date IS NULL`) and adds
`--include-delisted` as an opt-in to include the full set.

Two files change: `src/manta_trading/cli/commands/data.py` and
`test/unit/cli/commands/test_data_pull.py`. `symbols.py` and its tests
are **not touched**.

Branch: `158-slice.universe-delisted-filter`

---

## Tasks

### T1 — Create branch

- [x] From `main`, create and switch to branch `158-slice.universe-delisted-filter`
- [x] Confirm working directory is `/Users/manta/source/repos/manta/trading`

---

### T2 — Add `include_delisted` parameter to `_resolve_symbols_for_pull`

File: `src/manta_trading/cli/commands/data.py`

- [x] Add `include_delisted: bool` keyword argument to `_resolve_symbols_for_pull`
- [x] In the `universe` branch, replace the `iter_active_instruments` call
  with a direct psycopg query:
  - Default (`include_delisted=False`):
    `SELECT symbol FROM instruments WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL ORDER BY symbol ASC`
  - Opt-in (`include_delisted=True`):
    `SELECT symbol FROM instruments ORDER BY symbol ASC`
- [x] Add guard before the existing mutual-exclusivity check: if
  `include_delisted=True` and `universe=False`, call `print_error` with a
  message referencing `--universe` and raise `typer.Exit(1)`
- [x] Remove the `iter_active_instruments` import from the `universe` branch
  (it is no longer called from this path)
- [x] Update the docstring for `_resolve_symbols_for_pull` to reflect the
  new parameter
- [x] Confirm `symbols.py` was not modified: `git diff src/manta_trading/data/acquisition/symbols.py`
  must return empty

### T3 — Test: `_resolve_symbols_for_pull` universe branch (inline)

- [x] These are tested via the CLI layer in T5; no separate unit test for the
private function is needed. Confirm this is consistent with the existing
test pattern (existing tests invoke through `runner.invoke`).

---

### T4 — Add `--include-delisted` option to `data_pull` command

File: `src/manta_trading/cli/commands/data.py`

- [x] Add Typer option to `data_pull`:
  ```python
  include_delisted: bool = typer.Option(
      False,
      "--include-delisted",
      help="Include delisted instruments. Only valid with --universe.",
  )
  ```
- [x] Pass `include_delisted=include_delisted` through to
  `_resolve_symbols_for_pull`
- [x] Update the `data_pull` command docstring to mention `--include-delisted`
  and its `--universe` requirement

### T5 — Test: `--include-delisted` CLI behaviour

File: `test/unit/cli/commands/test_data_pull.py`

Add class `TestDataPullUniverseDelistedFilter` with three tests. All mock
the psycopg connection; no real DB required.

- [x] `test_universe_default_excludes_delisted` — mock `psycopg.connect`,
  invoke `["data", "pull", "1d", "--universe"]`, assert the SQL executed
  contains `delisted_at_eodhd = FALSE` and `delisted_date IS NULL`
- [x] `test_universe_include_delisted_removes_filter` — same mock, invoke
  with `--universe --include-delisted`, assert executed SQL does **not**
  contain `delisted_at_eodhd` in the WHERE clause
- [x] `test_include_delisted_without_universe_exits_error` — invoke with
  `--include-delisted --symbol AAPL` (no `--universe`), assert
  `exit_code == 1` and output contains `--universe`
- [x] Run new tests in isolation: `uv run pytest test/unit/cli/commands/test_data_pull.py -q`
  — all pass

---

### T6 — Regression: existing pull and symbols tests

- [x] Run `uv run pytest test/unit/cli/commands/test_data_pull.py -q` — all
  pre-existing tests pass
- [x] Run `uv run pytest test/unit/data/acquisition/test_symbols.py -q` —
  all pass (confirms `iter_active_instruments` is untouched)

---

### T7 — Static analysis

- [x] Run `uv run pyright` — zero new errors

---

### T8 — Full unit test suite

- [x] Run `uv run pytest test/unit -q` — all 1,246+ tests pass

---

### T9 — Commit

- [x] `git add` changed files
- [x] Commit: `feat: narrow --universe to active-only; add --include-delisted flag`

---

### T10 — Verification walkthrough

- [x] Confirm active count on prod:
  ```bash
  psql $MT_TIMESCALE_DB_URL -c \
    "SELECT count(*) FROM instruments
     WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL;"
  ```
  Expected: ~12,935
- [x] Dry-run default universe — confirm symbol count matches active count:
  `mt data pull 1d --universe --dry-run`
- [x] Dry-run with flag — confirm symbol count exceeds active count:
  `mt data pull 1d --universe --include-delisted --dry-run`
- [x] Confirm error path: `mt data pull 1d --include-delisted --symbol AAPL`
  exits 1 with message referencing `--universe`
