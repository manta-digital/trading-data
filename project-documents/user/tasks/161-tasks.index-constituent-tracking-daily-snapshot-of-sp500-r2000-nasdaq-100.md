---
docType: tasks
slice: index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100
project: trading
lld: user/slices/161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100.md
dependencies: [141]
projectState: >
  Slice 141 is complete. The `instruments` table is populated with ~31k symbols
  from EODHD. The daemon (slices 145/146) runs daily/minute cycles with quota
  tracking via `CallType`/`QuotaBucket`. The `lists.py` module already parses
  the EODHD `/fundamentals/GSPC.INDX` `Components` payload for `refresh-sp500`.
  No `universe_members` table or `FUNDAMENTALS` CallType exists yet.
dateCreated: 20260514
dateUpdated: 20260514
status: complete
---

## Context Summary

- Implementing slice 161: daily point-in-time constituent tracking for SP500, R2000,
  NASDAQ-100 into a new `universe_members` table
- Slice 130 (survivorship-bias-free universe, not started) consumes this table
- Key files to create/modify:
  - `src/manta_trading/constants.py` — add `EODHD_FUNDAMENTALS_CALL_COST`
  - `src/manta_trading/data/acquisition/quota.py` — add `CallType.FUNDAMENTALS`
  - `src/manta_trading/market/schema/migrations/minute.py` — add migration `041`
  - `src/manta_trading/data/universe/constants.py` — NEW: `TRACKED_UNIVERSES`
  - `src/manta_trading/data/universe/tracking.py` — NEW: core tracking logic
  - `src/manta_trading/data/acquisition/daemon/runner.py` — add universe refresh hook
  - `src/manta_trading/cli/commands/universes.py` — NEW: `mt data universes` sub-app
  - `src/manta_trading/cli/commands/data.py` — register `universes_app`
- Test files:
  - `test/unit/data/acquisition/test_quota.py` — extend for new CallType
  - `test/unit/data/universe/test_tracking.py` — NEW
  - `test/unit/cli/commands/test_data_universes.py` — NEW
- Slice 130's design doc uses `added_on/removed_on` column names; authoritative names
  from this slice are `added_date/removed_date` — slice 130 tasks must use these names
- **F007 — Deferred:** Success criterion 8 ("Slice 130 can query `universe_members` with
  `added_date/removed_date` without error") cannot be verified until slice 130 is
  implemented. It is slice 130's responsibility to use the correct column names at task
  time. This criterion is intentionally deferred and does not block slice 161 completion.

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Confirm on `main` or create branch `161-slice.index-constituent-tracking-daily-snapshot-of-sp500-r2000-nasdaq-100`
  - [x] Confirm clean working tree before starting

- [x] **T02 — Add `FUNDAMENTALS` CallType and cost constant**
  - [x] In `src/manta_trading/constants.py`, add `EODHD_FUNDAMENTALS_CALL_COST: int = 10`
    adjacent to the other `EODHD_*_CALL_COST` constants
  - [x] In `src/manta_trading/data/acquisition/quota.py`:
    - Add `FUNDAMENTALS = "fundamentals"` to `CallType` StrEnum
    - Import `EODHD_FUNDAMENTALS_CALL_COST` from `manta_trading.constants`
    - Add `CallType.FUNDAMENTALS: EODHD_FUNDAMENTALS_CALL_COST` to `CALL_COSTS`
  - [x] Success: `CallType.FUNDAMENTALS` exists; `QuotaBucket.cost_for(CallType.FUNDAMENTALS) == 10`

- [x] **T03 — Test: `FUNDAMENTALS` CallType**
  - [x] In `test/unit/data/acquisition/test_quota.py`, add assertions:
    - `CallType.FUNDAMENTALS` is a member of `CallType`
    - `QuotaBucket.cost_for(CallType.FUNDAMENTALS) == 10`
  - [x] Run `pytest test/unit/data/acquisition/test_quota.py` — all pass

- [x] **T04 — Migration: `041_create_universe_members`**
  - [x] In `src/manta_trading/market/schema/migrations/minute.py`, append a new entry to
    `MINUTE_MIGRATIONS` with id `"041_create_universe_members"`:
    ```sql
    CREATE TABLE IF NOT EXISTS universe_members (
        universe_name TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        added_date    DATE NOT NULL,
        removed_date  DATE,
        PRIMARY KEY (universe_name, symbol, added_date)
    );
    CREATE INDEX IF NOT EXISTS idx_universe_members_active
        ON universe_members (universe_name, symbol)
        WHERE removed_date IS NULL;
    ```
  - [x] Down path not required (migration runner is forward-only), but add a comment noting
    the table can be dropped manually if needed
  - [x] Success: `"041_create_universe_members"` appears in `MINUTE_MIGRATIONS` with valid SQL

- [x] **T05 — Test: migration applies cleanly**
  - [x] Run `mt data init` (or `mt db migrate`) against `trading_test` and confirm:
    - Migration `041` appears in `schema_migrations` with a timestamp
    - `\d universe_members` shows the expected columns and PK
    - `\d idx_universe_members_active` shows the partial index
  - [x] Re-run migration — confirm idempotent (no error, migration skipped)
  - [x] Success: migration applies and is idempotent

- [x] **T06 — `universe/constants.py` — tracked-universe registry**
  - [x] Create `src/manta_trading/data/universe/constants.py`:
    ```python
    TRACKED_UNIVERSES: dict[str, str] = {
        "sp500":     "GSPC.INDX",
        "r2000":     "RUT.INDX",
        "nasdaq100": "NDX.INDX",
    }
    ```
  - [x] No other code in this module — constants only
  - [x] Success: `from manta_trading.data.universe.constants import TRACKED_UNIVERSES`
    returns a dict with exactly three entries

- [x] **T07 — `universe/tracking.py` — core tracking logic**
  - [x] Create `src/manta_trading/data/universe/tracking.py` with the following functions.
    See slice design §Core Tracking Logic for full signatures and behavior.
    - `fetch_constituents(fetch_fn: Callable[[], dict], eodhd_code: str) -> set[str]`
      — calls `fetch_fn()`, extracts `Components` dict (reuse `_extract_components`
      pattern from `lists.py`); raises `UniverseTrackingError` on malformed payload;
      never silently returns empty for a well-formed response
    - `get_active_members(conn, universe_name: str) -> set[str]`
      — `SELECT symbol FROM universe_members WHERE universe_name = %s AND removed_date IS NULL`
    - `apply_universe_diff(conn, universe_name, fetched: set[str], as_of_date: date) -> tuple[int, int]`
      — inserts additions, sets `removed_date` on departures; returns `(added, removed)`;
      idempotent for repeated calls with same `as_of_date`
    - `is_refreshed_today(conn, universe_name: str, today: date) -> bool`
      — `SELECT 1 ... WHERE universe_name = %s AND (added_date = %s OR removed_date = %s)`
    - `refresh_universe(conn, fetch_fn, universe_name: str, eodhd_code: str, today: date) -> tuple[int, int]`
      — composes above; skips if already refreshed today (returns `(0, 0)`);
      seeds with full INSERT on first run (empty table for this universe)
    - `refresh_all_universes(conn, fetch_fn_factory, today: date) -> None`
      — iterates `TRACKED_UNIVERSES`; calls `refresh_universe` per entry;
      logs `"universe {name}: +{added} -{removed} as of {today}"` per entry
    - `UniverseTrackingError(RuntimeError)` — single error class for this module
  - [x] `fetch_fn_factory` is a `Callable[[str], Callable[[], dict]]` — given an EODHD
    code, returns the zero-arg callable; allows daemon to inject `eodhd_get`-backed
    factory and CLI to inject plain-httpx factory without logic changes
  - [x] Success: module imports cleanly; all public names importable

- [x] **T08 — Tests: `tracking.py`**
  - [x] Create `test/unit/data/universe/__init__.py` (if needed)
  - [x] Create `test/unit/data/universe/test_tracking.py` with unit tests for each function.
    Use `psycopg` with `trading_test` DB or in-memory fixtures.
    - `fetch_constituents` with well-formed payload → correct symbol set
    - `fetch_constituents` with missing `Components` key → raises `UniverseTrackingError`
    - `fetch_constituents` with empty `Components` → raises `UniverseTrackingError`
    - `get_active_members` returns only rows with `removed_date IS NULL`
    - `apply_universe_diff` — additions inserted with correct dates
    - `apply_universe_diff` — departures updated with `removed_date`
    - `apply_universe_diff` — idempotent on same `as_of_date`
    - `is_refreshed_today` returns False when table empty; True after seeding
    - `refresh_universe` skips if `is_refreshed_today` is True
    - `refresh_universe` seeds on first run (empty universe)
    - `refresh_all_universes` calls `refresh_universe` for each entry in `TRACKED_UNIVERSES`
  - [x] Run `pytest test/unit/data/universe/test_tracking.py` — all pass

- [x] **T09 — Daemon integration: universe refresh hook in runner**
  - [x] In `src/manta_trading/data/acquisition/daemon/runner.py`:
    - [x] Add `run_universe_refresh: Callable[[QuotaBucket], None] | None = None` parameter
      to `Runner.__init__` (alongside `run_ca_update`)
    - [x] Add `_universe_refresh_noop` and `make_universe_refresh_fn(settings)` helpers,
      following the `_ca_update_noop` / `make_ca_update_fn` pattern
    - [x] `make_universe_refresh_fn` closes over `settings`, builds the `fetch_fn_factory`
      using `eodhd_get` with `CallType.FUNDAMENTALS`, calls `refresh_all_universes`
    - [x] Wire the call in the main loop: after the daily cycle block, call
      `self._run_universe_refresh(self._bucket)` once per calendar day (no separate
      `_due` predicate needed — `is_refreshed_today` guards internally)
    - [x] Late-bind default like `run_ca_update` so tests can inject mocks
  - [x] Update `make_runner(settings)` factory (or wherever `Runner` is constructed for
    production) to wire `make_universe_refresh_fn(settings)` in
  - [x] Success: `Runner` accepts `run_universe_refresh`; default wires
    `make_universe_refresh_fn`; mock injection works

- [x] **T10 — Tests: daemon runner — universe refresh hook**
  - [x] In `test/unit/data/acquisition/daemon/test_runner.py`, add test cases:
    - [x] Runner constructed with mock `run_universe_refresh`; after a daily cycle, the mock
      is called exactly once
    - [x] Re-triggering daily cycle on same calendar day does NOT call the mock a second time
      (because `is_refreshed_today` returns True — simulate via mock return value)
    - [x] Runner constructed without `run_universe_refresh` → noop, no error
  - [x] Run `pytest test/unit/data/acquisition/daemon/test_runner.py` — all pass

- [x] **T11 — CLI: `mt data universes` sub-app**
  - [x] Create `src/manta_trading/cli/commands/universes.py` with `universes_app`:
    - `universes_app = typer.Typer(name="universes", help="...")`
    - `ls` command: queries `universe_members` for active count and
      `MAX(added_date)` per `universe_name`; prints table (universe, members,
      last_refresh); supports `--json`
    - `as-of` command: `--date YYYY-MM-DD` (required), `--name NAME` (required);
      executes the as-of query from slice design §CLI; prints one symbol per line
      or JSON array with `--json`; exits nonzero if universe unknown
    - `refresh` command: `[--name NAME]` optional; calls `refresh_all_universes` or
      single `refresh_universe` if `--name` given; bypasses today-guard by passing
      `today = date.min`; uses plain `httpx` (not daemon quota); prints per-universe
      result line; supports `--json`
  - [x] In `src/manta_trading/cli/commands/data.py`:
    - Import `universes_app` from `universes.py`
    - Add `data_app.add_typer(universes_app, name="universes")`
  - [x] Success: `mt data universes --help` shows `ls`, `as-of`, `refresh` sub-commands

- [x] **T12 — Tests: CLI universes commands**
  - [x] Create `test/unit/cli/commands/test_data_universes.py` using `typer.testing.CliRunner`
    - `mt data universes ls` with empty DB → exits 0; prints headers with zero rows
    - `mt data universes ls` with seeded rows → correct counts displayed
    - `mt data universes as-of --date <today> --name sp500` with seeded data → correct
      symbol list
    - `mt data universes as-of --name unknown_universe` → exits nonzero with error message
    - `mt data universes refresh --name sp500` with mocked fetch → correct output;
      `universe_members` seeded with expected rows
    - `mt data universes refresh --name sp500` called twice same day → idempotent
  - [x] Run `pytest test/unit/cli/commands/test_data_universes.py` — all pass

- [x] **T13 — Full test suite + commit**
  - [x] Run `pytest test/unit/` — all existing and new tests pass; no regressions
  - [x] Build (`python -m build` or equivalent) — clean
  - [x] Commit:
    ```
    feat: add index constituent tracking for SP500, R2000, NASDAQ-100 (slice 161)
    ```
    Stage: all modified/new files under `src/` and `test/` and the slice design +
    tasks docs

- [x] **T14 — Verification walkthrough**
  - [x] Follow the verification steps in `161-slice` §Verification Walkthrough against
    the `trading` DB (not `trading_test`)
  - [x] Confirm each step's expected output matches actual output:
    1. `mt data init` applies migration `041`; `\d universe_members` shows correct schema
    2. `mt data universes refresh` seeds all three universes; output shows seeded counts
       (SP500 ≈503, R2000 ≈2000, NASDAQ-100 =100)
    3. `mt data universes ls` shows three rows with correct member counts and today's date
    4. `mt data universes as-of --date <today> --name sp500` returns ≈503 symbols
    5. Simulate a removal (manual SQL UPDATE), verify `as-of` semantics
    6. Re-run `mt data universes refresh` — idempotent (counts unchanged)
    7. Confirm daemon logs show three universe lines after one daily cycle
  - [x] Update `161-slice.md` `status:` to `complete` and `dateUpdated` to today
  - [x] Update `161-tasks.md` `status:` to `complete`
