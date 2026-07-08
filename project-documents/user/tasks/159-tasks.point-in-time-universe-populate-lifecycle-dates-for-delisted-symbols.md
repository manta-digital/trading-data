---
docType: tasks
slice: point-in-time-universe-populate-lifecycle-dates-for-delisted-symbols
project: trading
lld: user/slices/159-slice.point-in-time-universe-populate-lifecycle-dates-for-delisted-symbols.md
dependencies: [158]
projectState: >
  Slice 158 complete and merged to main. instruments table: ~12,946 active,
  ~18,742 delisted (delisted_at_eodhd=true), all 18,742 have delisted_date IS NULL.
  --universe excludes delisted; --include-delisted re-includes them. No
  populate-delisted-dates command exists yet.
dateCreated: 20260514
dateUpdated: 20260514
status: complete
---

## Context Summary

- Slice 159: populate `instruments.delisted_date` for all ~18,742 delisted symbols
- Core mechanism: fetch 1 bar per symbol (`/eod/{SYM}.US?limit=1&order=d`) to
  find last trading day; write that date as `delisted_date`. 1 credit each.
- Finnhub `first_listing_date` enrichment uses existing `instruments rebuild`
  command — no code change needed.
- New code: `src/manta_trading/data/universe/populate_delisted_dates.py` (core
  function + report dataclass) and CLI command in
  `src/manta_trading/cli/commands/data.py`.
- Test location: `test/unit/universe/` (existing dir with `conftest.py` and
  `__init__.py`; no new directory needed).
- Quota pattern: CLI creates a `QuotaBucket`, sets `QUOTA_BUCKET_VAR`, calls
  core function, resets contextvar on exit. Same pattern as `data_pull` and
  `data_ca_update` in `data.py`.
- No migration. No schema change. No `data_gaps` or daemon changes.
- Next planned slice: 160 (TimescaleDB columnar compression).

---

- [x] **T1 — Branch setup**
  - [x] Confirm on `main`: `git branch --show-current`
  - [x] Create and checkout: `git checkout -b 159-slice.point-in-time-universe-populate-lifecycle-dates-for-delisted-symbols`
  - [x] Success: branch name matches above exactly.

- [x] **T2 — New module: `PopulateDelistedDatesReport` dataclass**
  - Create `src/manta_trading/data/universe/populate_delisted_dates.py`.
  - [x] Add module docstring explaining purpose (lifecycle date population for
        delisted instruments via EODHD single-bar fetch).
  - [x] Add `from __future__ import annotations` and typed imports.
  - [x] Define `PopulateDelistedDatesReport` as a `@dataclass(frozen=True)` with
        fields: `total: int`, `updated: int`, `skipped_empty: int`, `error_count: int`.
  - [x] Success: `python -c "from manta_trading.data.universe.populate_delisted_dates
        import PopulateDelistedDatesReport; print(PopulateDelistedDatesReport
        (total=1, updated=1, skipped_empty=0, error_count=0))"` exits 0.

- [x] **T3 — Core function `populate_delisted_dates`**
  - Continue in `src/manta_trading/data/universe/populate_delisted_dates.py`.
  - [x] Define module-level `_EODHD_BASE = "https://eodhd.com/api"` constant.
  - [x] Implement `_normalise_symbol(symbol: str) -> str`: if `"."` in symbol
        return unchanged; else return `f"{symbol}.US"`. (Same rule as
        `EODHDDailyProvider._normalise_symbol` in `data/acquisition/daily/providers/eodhd.py`.)
  - [x] Implement `populate_delisted_dates(conn, *, api_key, dry_run=False,
        on_progress=None) -> PopulateDelistedDatesReport` following the algorithm
        in the slice design §"Core Function":
    1. Query `SELECT symbol FROM instruments WHERE delisted_at_eodhd = true
       AND delisted_date IS NULL ORDER BY symbol ASC`; collect as a list.
    2. For each symbol: build URL
       `f"{_EODHD_BASE}/eod/{_normalise_symbol(sym)}?api_token={api_key}&fmt=json&order=d&limit=1"`.
    3. Call `eodhd_get(http_client, url, CallType.EOD)` (the caller provides an
       `httpx.Client`; see signature note below). Parse response as JSON list.
    4. Empty list → `skipped_empty++`; call `on_progress` with `None`; continue.
    5. Extract `response[0]["date"]`; parse with `date.fromisoformat(...)`.
    6. If not `dry_run`: `UPDATE instruments SET delisted_date = %s WHERE symbol = %s`.
       Each UPDATE is a standalone statement, not batched.
    7. `updated++`; call `on_progress(processed, total, sym, last_bar_date)`.
    8. HTTP 4xx (non-429): log `_logger.error(...)`; `error_count++`; continue.
    9. `KeyError` or `ValueError` on parsing: log `_logger.error(...)`; `error_count++`; continue.
  - [x] Function signature: `populate_delisted_dates(conn: psycopg.Connection, http:
        httpx.Client, *, api_key: str, dry_run: bool = False, on_progress:
        Callable[[int, int, str, date | None], None] | None = None)`. The caller
        owns both `conn` and `http` (same lifetime pattern as daemon cycle functions).
  - [x] Success: `python -c "from manta_trading.data.universe.populate_delisted_dates
        import populate_delisted_dates"` exits 0 (import clean).

- [x] **T4 — Unit tests for core function**
  - Create `test/unit/universe/test_populate_delisted_dates.py`.
  - Use `unittest.mock.patch` to mock `eodhd_get` and `psycopg.Connection`.
  - [x] `test_happy_path`: mock DB returns `["SYM1"]`; mock `eodhd_get` returns
        response with `[{"date": "2003-07-15", "close": 1.23}]`; assert
        `report.updated == 1`, `report.skipped_empty == 0`, `report.error_count == 0`.
        Assert the UPDATE was called with `("2003-07-15", "SYM1")` (or equivalent).
  - [x] `test_empty_response`: mock `eodhd_get` returns response with `[]`; assert
        `report.updated == 0`, `report.skipped_empty == 1`, `report.error_count == 0`.
        Assert no UPDATE was issued.
  - [x] `test_http_error`: mock `eodhd_get` raises `httpx.HTTPStatusError` (4xx);
        assert `report.error_count == 1`, `report.updated == 0`. Function must not
        re-raise.
  - [x] `test_dry_run`: mock DB returns `["SYM1"]`; `eodhd_get` returns a valid bar;
        pass `dry_run=True`; assert `report.updated == 0` and no UPDATE was issued
        against the DB cursor mock.
  - [x] `test_progress_callback`: pass a list-collecting callback; assert it receives
        `(1, 1, "SYM1", date(2003, 7, 15))` on the happy-path case.
  - [x] Success: `uv run pytest test/unit/universe/test_populate_delisted_dates.py -v`
        — all 5 tests pass, zero warnings.

- [x] **T5 — CLI command `instruments populate-delisted-dates`**
  - Open `src/manta_trading/cli/commands/data.py`.
  - [x] Add `@instruments_app.command("populate-delisted-dates")` with:
    - `dry_run: bool = typer.Option(False, "--dry-run")` 
    - `verbose: bool = typer.Option(False, "--verbose", "-v")`
  - [x] Command body pattern (mirrors `data_ca_update` in the same file):
    1. Load `Settings()`; fail with `print_error` if `timescale_db_url` or
       `eodhd_api_key` is missing.
    2. Create `QuotaBucket()`; set `QUOTA_BUCKET_VAR`; wrap work in `try/finally`
       to reset the contextvar.
    3. Open `ConnectionPool` and `httpx.Client`; call `populate_delisted_dates(...)`.
    4. Define an `on_progress` callback that prints per-symbol lines only when
       `verbose=True`. Format: `"{symbol}: {date}" or "{symbol}: EMPTY"`.
    5. After the function returns: print summary line matching the format in the
       slice design (`"Done. updated=N skipped_empty=N errors=N"` or the dry-run
       variant).
    6. Exit 1 via `raise typer.Exit(code=1)` if `report.error_count > 0`.
  - [x] Success: `mt data instruments populate-delisted-dates --help` shows
        `--dry-run` and `--verbose` / `-v` options and exits 0.

- [x] **T6 — CLI unit tests**
  - Create `test/unit/cli/commands/test_data_instruments_populate_delisted_dates.py`.
  - Use `typer.testing.CliRunner` and `unittest.mock.patch`.
  - [x] `test_help_exits_zero`: invoke with `["data", "instruments", "populate-delisted-dates", "--help"]`;
        assert exit code 0 and `"--dry-run"` in output.
  - [x] `test_missing_env_exits_error`: mock `Settings` with `timescale_db_url=None`;
        assert exit code non-zero and error message in output.
  - [x] `test_dry_run_flag_passed_through`: mock `populate_delisted_dates` to return
        `PopulateDelistedDatesReport(total=5, updated=0, skipped_empty=0, error_count=0)`;
        invoke with `--dry-run`; assert `"DRY RUN"` or `"dry run"` appears in output
        and exit code 0.
  - [x] `test_error_count_nonzero_exits_one`: mock function returns a report with
        `error_count=2`; assert exit code 1.
  - [x] Success: `uv run pytest test/unit/cli/commands/test_data_instruments_populate_delisted_dates.py -v`
        — all 4 tests pass.

- [x] **T7 — Pyright and ruff clean**
  - [x] Run `uv run pyright src/manta_trading/data/universe/populate_delisted_dates.py` — zero errors.
  - [x] Run `uv run ruff check src/manta_trading/data/universe/populate_delisted_dates.py` — zero errors.
  - [x] Run `uv run pyright src/manta_trading/cli/commands/data.py` — zero errors
        (no regressions in existing file).
  - [x] Fix any type errors before proceeding to T8.

- [x] **T8 — Full unit test suite**
  - [x] Run `uv run pytest test/unit -q`.
  - [x] Assert: all previously passing tests still pass; zero new failures.
  - [x] New test count is 9 higher than pre-slice baseline (5 core + 4 CLI tests).

- [x] **T9 — Commit**
  - [x] `git add src/manta_trading/data/universe/populate_delisted_dates.py`
  - [x] `git add src/manta_trading/cli/commands/data.py`
  - [x] `git add test/unit/universe/test_populate_delisted_dates.py`
  - [x] `git add test/unit/cli/commands/test_data_instruments_populate_delisted_dates.py`
  - [x] Commit: `feat: add instruments populate-delisted-dates command`
  - [x] Success: `git log --oneline -1` shows the commit message.

- [x] **T10 — Verification on `trading_test` DB**
  - [x] Record baseline:
        `psql $TEST_DB -c "SELECT COUNT(*) FROM instruments WHERE delisted_at_eodhd=true AND delisted_date IS NULL"`.
  - [x] Run dry-run against test DB:
        `MT_TIMESCALE_DB_URL=$TEST_DB MT_EODHD_API_KEY=$KEY mt data instruments populate-delisted-dates --dry-run`
        — exits 0, prints `"DRY RUN"` line, baseline count unchanged after run.
  - [x] Run real command (use a small symbols scope by manually passing 2–3
        known delisted symbols from the test DB to spot-check the output);
        confirm `delisted_date` is set to a plausible non-NULL date for those symbols.
  - [x] Second run against same symbols produces `updated=0 skipped_empty=0 errors=0`.
  - [x] Note: Full prod run (`trading` DB) is an operator step documented in the
        slice design §"Operator Sequence" — not performed as part of this task.
