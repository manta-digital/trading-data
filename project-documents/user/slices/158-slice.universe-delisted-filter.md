---
docType: slice-design
slice: universe-delisted-filter
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [157]
interfaces: [159]
dateCreated: 20260512
dateUpdated: 20260512
status: complete
---

# Slice Design: `--universe` Delisted Filter + `--include-delisted` Flag

## Overview

Today `mt data pull --universe` calls `iter_active_instruments`, which
returns both active symbols **and** newly-delisted symbols that lack a
`delisted_date` (the "one final pass" logic for the daemon). For a pull
operation these semantics are wrong: the operator means "give me the active
universe," not "give me the active universe plus a handful of in-flight
delisted symbols."

This slice tightens `--universe` to return only fully-active instruments
(`delisted_at_eodhd = FALSE AND delisted_date IS NULL`) and adds an
`--include-delisted` opt-in flag that restores the full set (active +
delisted) for full-history pulls.

No migration. No schema change. Two narrow code edits and corresponding
tests.

## Value

Separates two distinct use cases that are currently conflated:

- **Active universe pull** (`--universe`): operator wants current live
  symbols only — ~12,935 after slice 157 removed preferred stock.
- **Full-history pull** (`--universe --include-delisted`): operator wants
  every symbol ever registered, for populating delisted bar history (slice
  159's prerequisite).

Without this distinction, slice 159's "fetch full history for all symbols"
step cannot be expressed cleanly — it would require a separate instrument
query or hand-curated list.

## Technical Scope

**Included:**
- Add `include_delisted: bool` parameter to `_resolve_symbols_for_pull`
  in `data.py`
- Add `--include-delisted` Typer option to `data_pull` command; pass
  through to `_resolve_symbols_for_pull`; update the command docstring
- In `_resolve_symbols_for_pull`, replace the call to
  `iter_active_instruments` with a direct SQL query that varies on
  `include_delisted`:
  - Default (`include_delisted=False`): `WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL`
  - Opt-in (`include_delisted=True`): no WHERE filter (all instruments)
- Add unit tests in `test_data_pull.py` (new class `TestDataPullUniverseDelistedFilter`)

**Excluded:**
- No change to `iter_active_instruments` — the daemon's "one final pass"
  semantics are correct for that context and must not be disturbed
- No change to `--list`, `--symbol`, or `--symbols` paths
- No change to the instruments schema, migrations, or DB state
- No interaction with `data_gaps` or `acquisition_state`

## Dependencies

### Prerequisites
- Slice 157 (preferred stock filter) — instruments table is clean; active
  count is ~12,935. Design assumes preferred rows are gone.

### Interfaces Required
- `iter_active_instruments` in `symbols.py` — **not modified**; the pull
  path diverges from daemon path here
- `_resolve_symbols_for_pull` in `data.py` — receives new parameter
- `data_pull` Typer command — receives new `--include-delisted` option

## Architecture

### Symbol Resolution in `_resolve_symbols_for_pull`

The `universe` branch currently calls `iter_active_instruments` with
`ordering="alphabetical"`. This function's scope includes the "one final
pass" clause for the daemon. Pull operations have no need for that clause.

The fix: replace the `iter_active_instruments` call with a direct inline
query whose WHERE clause is parameterized by `include_delisted`. This keeps
the daemon's function untouched and makes the pull path's intent explicit.

```sql
-- Default (include_delisted=False)
SELECT symbol FROM instruments
WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL
ORDER BY symbol ASC

-- With --include-delisted
SELECT symbol FROM instruments
ORDER BY symbol ASC
```

Both queries return `symbol` only (the pull path only needs symbols, not
the full `InstrumentRow`).

### CLI Change

`data_pull` gains one new Typer option:

```python
include_delisted: bool = typer.Option(
    False,
    "--include-delisted",
    help="Include delisted instruments. Only valid with --universe.",
)
```

`--include-delisted` without `--universe` is an error (clear message,
exit 1). This check is added to `_resolve_symbols_for_pull` before the
existing mutual-exclusivity guard.

`_resolve_symbols_for_pull` signature change:

```python
def _resolve_symbols_for_pull(
    *,
    symbol: str | None,
    symbols_opt: str | None,
    list_name: str | None,
    universe: bool,
    include_delisted: bool,   # NEW
    settings,
    config_path: "Path",
    json_output: bool,
) -> list[str]:
```

The `include_delisted` value is only consulted when `universe=True`.

## Integration Points

- `data.py` — `data_pull` and `_resolve_symbols_for_pull`
- `test/unit/cli/commands/test_data_pull.py` — new test class
  `TestDataPullUniverseDelistedFilter`
- `test/unit/data/acquisition/test_symbols.py` — no change needed;
  `iter_active_instruments` is unchanged

## Success Criteria

1. `mt data pull 1d --universe` queries only
   `delisted_at_eodhd = FALSE AND delisted_date IS NULL` (verified by
   SQL logged or test assertion on query text).
2. `mt data pull 1d --universe --include-delisted` queries all
   instruments with no delisted filter.
3. `mt data pull 1d --include-delisted` (without `--universe`) exits 1
   with a clear error message.
4. `iter_active_instruments` in `symbols.py` is **unchanged** — all
   existing daemon tests continue to pass.
5. All 1,246+ unit tests pass; zero new pyright errors.

## Tests

### New tests in `test_data_pull.py` — `TestDataPullUniverseDelistedFilter`

| Test | What it asserts |
|---|---|
| `test_universe_default_excludes_delisted` | SQL sent to DB contains `delisted_at_eodhd = FALSE AND delisted_date IS NULL` |
| `test_universe_include_delisted_removes_filter` | SQL sent to DB has no delisted WHERE clause |
| `test_include_delisted_without_universe_exits_error` | `--include-delisted` alone → exit 1, message references `--universe` |

All three tests mock the psycopg connection; no DB required.

### Regression

- `TestDataPullSymbolSelection.test_multiple_selectors_exits_with_error`
  continues to pass (existing test — verifies `--include-delisted`
  does not count as a second symbol selector).
- `test_symbols.py` — no change; all existing tests pass.

## Verification Walkthrough

Verified 2026-05-12 on prod DB (`postgresql://postgres:<password>@<db-host>:5432/trading`).

```bash
# 1. Confirm baseline active count
psql "postgresql://postgres:<password>@<db-host>:5432/trading" -c \
  "SELECT count(*) FROM instruments
   WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL;"
# count
# -------
#  12946

# 2. Default universe pull — dry run shows only active symbols
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data pull 1d --universe --dry-run
# Would fetch 0 gap(s) for 12946 symbol(s). 12441 cold symbol(s)...

# 3. Full universe pull — dry run shows all symbols including delisted
MT_TIMESCALE_DB_URL="postgresql://postgres:<password>@<db-host>:5432/trading" \
  mt data pull 1d --universe --include-delisted --dry-run
# Would fetch 0 gap(s) for 31688 symbol(s). 31183 cold symbol(s)...
# 31688 > 12946 ✓

# 4. --include-delisted without --universe errors cleanly
mt data pull 1d --include-delisted --symbol AAPL
# Error: --include-delisted requires --universe.
# Exit code: 1 ✓

# 5. All unit tests pass
uv run pytest test/unit -q
# 1249 passed, 12 skipped in 49.50s ✓
```
