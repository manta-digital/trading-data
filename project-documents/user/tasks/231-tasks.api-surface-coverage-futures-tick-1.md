---
docType: tasks
slice: api-surface-coverage-futures-tick
project: trading-data
lld: user/slices/231-slice.api-surface-coverage-futures-tick.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [229, 230]
projectState: >
  Slices 220-230 are merged. trading_tick holds both adopted ES jobs (trades
  2024-08-30 → 09-29, tbbo 2024-11-02 → 2025-01-01), and the CLI answers
  every tick question: status, coverage, active, get, debug, plus tick
  lines in accounting and overview. The API knows nothing about ticks.
  Slice design committed and reviewed (CONCERNS, all findings fixed).
dateCreated: 20261012
dateUpdated: 20261012
status: not_started
---

# Tasks: API Surface Coverage — Futures Tick (part 1 of 2)

Part 1: preflight, the shared moves, the tick pool and its `503`, the
response models, and the three catalog routes. Part 2
(`231-tasks.api-surface-coverage-futures-tick-2.md`): the four time-series
routes, the health/status/overview additions, the documents, the load tier
and the closing checks.

## Context Summary

- Working on slice 231. It adds seven read-only `GET` routes under
  `/api/v1/futures` and adds tick information to `/api/v1/health`,
  `/api/v1/status` and `/api/v1/overview`:
  - catalog: `products`, `contracts`, `schedule`;
  - time series: ticks and bars, each by contract or by product plus rule.

  `mt-serve` keeps serving equities and Kalshi when the tick database is
  missing or down; tick routes answer `503`. No migration, no schema
  change, nothing bought, no write anywhere.
- The design (`user/slices/231-slice.api-surface-coverage-futures-tick.md`)
  holds every decision. Read TD1–TD9 (its Technical Decisions), its
  Functional Requirements and its Verification Walkthrough before starting.
  Tasks cite them as TDn and FRn (Functional Requirement n) rather than
  repeat them.
- **Paths.** Source is under `src/manta_trading/`:
  - API code in `api_server/` (`routes/`, `models/`, `app.py`, `deps.py`,
    `admission.py`, `serialization.py`);
  - tick data code in `data/tick/`.

  Tests:
  - unit tests in `test/unit/api_server/` and `test/unit/data/tick/`;
  - API integration tests that need a database go in
    `test/integration/api_server/` (create it, with `__init__.py`);
  - tick helpers are in `test/tick_support/` (`cli.py`: `clear_tick_env`,
    `closed_port`; `ticks.py`: `Tick`, `seed_ticks`; `roll_seed.py`:
    `seed_september_roll`; `database.py`).
- **What 229 and 230 hand over (reuse; never copy):**
  - `get_plan` (`contract_plan`, `product_plan`, `GetPlan`,
    `ContractNotFound`, `ContractAmbiguous`);
  - `tick_reads` (`read_ticks`, `read_bars`, `TickWindow`, the column
    tuples);
  - `tick_format` (`price_text`, `ts_text`);
  - `tick_accounting.accounting_lines` and `status_reads.newest_event_ns`;
  - `line_state` (`TickLineState`, `TickUnavailable`, `unavailable_text`,
    `first_line`);
  - `active_contract` (`build_schedule`, `latest_lines`, `roll_conninfo`,
    `RollRangeTooLong`);
  - `roll_payload` and `roll_rules` (`RollRule.parse`, `DEFAULT_ROLL_RULE`,
    `STATUS_ROLL_RULES`).
- **Test environment.**
  - Export `MT_TIMESCALE_TEST_URL` from `.env`, quotes stripped, in the same
    shell call.
  - Run with `uv run python scripts/run_tests.py
    unit|integration|load -- <paths> -q`, one tier at a time.
  - The integration tier has 6 known failures: priority1 list ×2,
    migration 051/052 ×2, policy-advances-head unaided ×2. Re-run any other
    failure in isolation before investigating.
  - The load tier has 4 known failures on `main`, including
    `/api/v1/status` at 2.79 s against 1.5 s.
- **Type check and lint.**
  - mypy in one invocation: `MYPYPATH=scripts uv run --extra dev mypy <src
    paths> <test paths>`. Do not list `test/tick_support/*.py` directly.
  - Scope `ruff format` to touched files, and check `git diff main` for
    swept pre-existing lines before each commit. Never format
    `test_overview.py`.
- **Production safety.** Tests never read `MT_TIMESCALE_DB_URL` or
  `MT_TICK_DB_URL`. The load tier passes URLs explicitly through
  `create_app(db_url=…, tick_db_url=…)`. Walkthrough reads against the dev
  tick database are `SELECT`-only (read-only sessions).
- Delivers to 232 (GC needs no API change) and 234 (production
  `MT_TICK_DB_URL`). Next slice: 232.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 1 - Preflight facts

- [ ] **1.1 Confirm the code facts the design relies on** (effort 2)
  - [ ] **psycopg_pool:** the installed `psycopg_pool` version, and whether
        `AsyncConnectionPool` supports each of these in it:
        `open=False`, `await pool.open(wait=False)`, `timeout=` (the
        checkout timeout), an async `configure` callback, and
        `PoolTimeout` on checkout. Use context7 for the version's docs if
        the installed source is unclear.
  - [ ] **Roll functions:** `latest_lines` and `build_schedule` signatures
        and what they raise; `roll_conninfo`'s constants
        (`TICK_DB_CONNECT_TIMEOUT_SECONDS`, its value).
        `missing_migrations(conn)`: whether it needs a transaction or
        autocommit.
  - [ ] **API test fixtures:** how existing API tests build an app and a
        client (`test/unit/api_server/test_app.py`, `test_health.py`,
        `test_operations.py`). Whether they use `TestClient` with the real
        `lifespan` or a stub, and how a test database URL reaches
        `create_app`. List every test module that calls `create_app`
        (`grep -rln "create_app(" test`) for 3.2's guard.
  - [ ] **Tick test database:** how the integration tier creates a migrated
        tick test database (`test/tick_support/database.py` and the
        fixtures 230's `get` integration tests use), so an API test can
        hold both a primary test URL (for the calendar) and a tick test
        URL.
  - [ ] **Granularity parameter:** how `/api/v1/bars` declares
        `granularity: Granularity`, and how to restrict a FastAPI query
        parameter to `TICK_BAR_GRANULARITIES` so the OpenAPI schema lists
        exactly those values. Expect a `Literal` or a subset enum built
        from the tuple; never a hand-typed list.
  - [ ] **CLI paths that move:** where `classify(exc, settings)` is called
        (`cli/tick_composed.py` and its tests), and every importer of
        `TICK_SOURCE_NAME`.
  - [ ] **Connection limits:** `SHOW max_connections` on the dev tick
        cluster and the dev primary cluster (read-only), for task 9.3
        (part 2). Record them as dev values. Production's limits are 234's
        check (9.3 hands it over).
  - [ ] Write a `Findings (1.1)` block under this task. If
        `AsyncConnectionPool` cannot open without blocking or cannot time
        out a checkout, stop and ask the Project Manager: TD2 depends on
        both.
  - [ ] Success: findings block present; no code changes.

## Section 2 - Shared moves (no behaviour change)

- [ ] **2.1 Move `classify` into `data/tick/line_state.py`** (effort 2)
  - [ ] Add `classify(exc, password: str | None, statement_timeout: str)
        -> TickUnavailable` to `line_state.py`, moved from
        `cli/tick_composed.py`. It keeps the outage tuple and the scrubbing
        as they are. The `QueryCanceled` wording names the timeout passed
        in: a timeout constant would be wrong for the API, whose
        connections run under the API `statement_timeout`.
  - [ ] Keep the module free of I/O and of any CLI import. The outage
        tuple's `TickCalendarError` import comes from
        `data/tick/tick_calendar.py`.
  - [ ] `cli/tick_composed.classify(exc, settings)` becomes a wrapper that
        resolves the password (its existing `_password`) and calls the
        moved function with `f"{TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS} s"`.
        The CLI's text is then byte-for-byte unchanged.
  - [ ] Success: `tick_composed.py` has no classification logic of its
        own; the CLI tests for accounting, overview and `tick_composed`
        pass unchanged.
- [ ] **2.2 Test the moved `classify`** (effort 1)
  - [ ] Unit tests in `test/unit/data/tick/test_line_state.py`:
    - `OperationalError` → `unreachable`;
    - `QueryCanceled` → `unreachable`, naming the timeout argument it was
      given (two different values, two different texts);
    - `TickPreflightError` and a generic `psycopg.Error` → `failed`;
    - a password in the message is replaced by `***`;
    - `password=None` leaves the text alone.
  - [ ] Success: tests pass; the existing `tick_composed` tests pass.
- [ ] **2.3 Move `TICK_SOURCE_NAME`; add the pool constants** (effort 1)
  - [ ] Move `TICK_SOURCE_NAME = "futures ticks"` to
        `data/tick/constants.py`, and import it in
        `cli/commands/overview.py` and every importer found in 1.1.
  - [ ] Add `TICK_API_POOL_MIN` (1), `TICK_API_POOL_MAX` (4) and
        `TICK_API_CHECKOUT_TIMEOUT_SECONDS` to `data/tick/constants.py`,
        each with a one-line comment. The checkout timeout must be no
        larger than `TICK_DB_CONNECT_TIMEOUT_SECONDS` (from 1.1), so a
        tick `503` never waits longer than a roll connect.
  - [ ] Success: `grep -rn '"futures ticks"' src` finds one definition;
        overview CLI tests pass.
- [ ] **2.4 Commit** (effort 1)
  - [ ] Unit tier for `test/unit/data/tick` and `test/unit/cli`; mypy and
        ruff on touched files.
  - [ ] Commit: `refactor: move tick classify and source name out of the
        CLI`.

## Section 3 - The tick pool and the `503` (TD2)

- [ ] **3.1 Build `api_server/tick_access.py` and the app seam** (effort 4)
  - [ ] **`create_app` and `lifespan`:**
    - `create_app(db_url=None, tick_db_url=None)`, with `tick_db_url`
      passed to `lifespan` the way `db_url` is.
    - `lifespan` resolves the tick URL: the explicit value, otherwise
      `resolve_database_url(settings, Database.TICK,
      Credential.APPLICATION)`.
    - `DatabaseNotConfiguredError` → `app.state.tick_pool = None`, and the
      not-configured detail (`f"{exc.env_var} unset"`) is held on
      `app.state`.
  - [ ] **The pool**, when the URL is set:
    - `AsyncConnectionPool(..., open=False)` with the 2.3 constants, then
      `await pool.open(wait=False)`.
    - The pool's `configure` sets read-only transactions, the API
      `statement_timeout` and `API_SERVING_SESSION.work_mem`, and leaves
      the connection idle. psycopg_pool discards a connection that
      `configure` leaves inside a transaction, so commit, or use
      autocommit, inside it.
    - A failure at startup is logged at WARNING and not raised.
    - `app.state.tick_url` and `app.state.calendar_url` (the resolved
      primary URL) are held for the roll functions.
    - The pool is closed in `lifespan`'s `finally`.
  - [ ] **`TickServiceUnavailable(Exception)`**, which carries a
        `TickUnavailable`.
  - [ ] **The `tick_connection` dependency:**
    - pool `None` → `NOT_CONFIGURED`;
    - a `PoolTimeout` or `OperationalError` on checkout → `UNREACHABLE`,
      through 2.1's `classify` with the tick URL's password and the API
      `statement_timeout`;
    - `missing_migrations` non-empty → `MIGRATIONS_PENDING`;
    - otherwise it yields the connection.
  - [ ] **The migration check leaves the connection idle.** Using 1.1's
        answer, run it in autocommit, or commit straight after it. Task 6.2
        then sets `REPEATABLE READ` on the same connection, and that fails
        if a transaction is still open.
  - [ ] **`probe_tick(app) -> TickUnavailable | None`** for health, status
        and overview: the same checks, returned rather than raised.
  - [ ] **`tick_errors()`, the one error mapping for every futures route.**
        It is a context manager. Order matters:
    1. `psycopg.errors.QueryCanceled` is re-raised unchanged, so the
       existing `504` handler gets it. It must come first, because it is a
       subclass of `OperationalError`.
    2. Any other `psycopg.OperationalError`, or a `TickCalendarError`, →
       `TickServiceUnavailable(UNREACHABLE)` through `classify`.
    3. `ContractNotFound` → `404`.
    4. `TickPreflightError` (which includes `ContractAmbiguous` and
       `RollRangeTooLong`) → `422` with the message.

    Tasks 5.3, 5.5, 5.7 and 6.2 all use it; none writes its own catch
    tuple.
  - [ ] **An app-level handler** maps `TickServiceUnavailable` to
        `503 {"error": unavailable_text(...)}`. It is registered beside the
        `QueryCanceled` handler in `app.py`.
  - [ ] **`TICK_UNAVAILABLE_RESPONSE`** in `models/responses.py`, shaped
        like `GATEWAY_TIMEOUT_RESPONSE`, declares the `503`.
  - [ ] Success: `tick_access.py` is under ~200 lines; no equities or
        Kalshi route imports it.
- [ ] **3.2 Test infrastructure, and keeping the dev tick database out of
      tests** (effort 3)
  - [ ] **The leak:** `lifespan` builds `Settings()`, which reads `.env`.
        With `tick_db_url=None`, every existing test that calls
        `create_app(db_url=…)` would open a pool against the dev tick
        database (1.1 lists them: unit `test/unit/api_server/*`,
        `test/integration/test_operations_serving.py`,
        `test/integration/test_api_docs_examples.py`, and
        `test/load/test_187/188/189_*`).
  - [ ] **The guard:** add an autouse fixture, `no_dev_tick_settings`, in a
        shared helper (`test/tick_support/api.py`). It patches the
        `Settings` that `api_server/app.py` uses to return the loaded
        settings with `tick_db_url` and `tick_maintenance_url` set to
        `None` (`model_copy(update=…)`). Enable it from the conftest of
        `test/unit/api_server/`, `test/integration/api_server/`, the two
        integration modules above, and `test/load/`. Production code is
        unchanged.
  - [ ] **Fixtures** in `test/integration/api_server/conftest.py`:
    - a `TestClient` over `create_app(db_url=<primary test URL>,
      tick_db_url=<migrated tick test URL>)`, using the real `lifespan`;
    - a client whose tick URL is a `closed_port()` URL with a password in
      it;
    - a client with `tick_db_url=None`, so not configured through the
      guard;
    - a **function-scoped throwaway tick database**, created by the fixture
      through `test/tick_support/database.py`'s pattern, for the tests that
      delete a migration ledger row (3.3, 7.2). Never the shared session
      database.
  - [ ] Seeding goes through the existing helpers (`seed_september_roll`,
        `seed_ticks`); add none unless 1.1 found a gap.
  - [ ] Success: a smoke test, `GET /api/v1/health` returning 200 through
        each fixture, passes. A test asserts that under the guard, an app
        built with `tick_db_url=None` has `tick_pool is None` even when
        `.env` sets `MT_TICK_DB_URL`.
- [ ] **3.3 Test the pool posture** (effort 3)
  - [ ] These tests use a throwaway probe route registered only in the test
        (depending on `tick_connection`), because the real futures routes
        come later. Integration tests:
    - not configured → `503` with `not configured (MT_TICK_DB_URL unset)`;
    - closed port → `503` `unreachable: …` within
      `TICK_API_CHECKOUT_TIMEOUT_SECONDS` plus 1 s (FR9);
    - on the function-scoped throwaway tick database, with the newest
      tick-track migration's ledger row deleted → `503` `migrations
      pending (…)` (FR10);
    - after the row is restored, the same client gets 200, with no
      restart (FR9 recovery).
  - [ ] Unit tests for `tick_errors()`, one case per mapping. A raised
        `QueryCanceled` must reach the `504` handler, not become a `503`.
  - [ ] The app starts under all three fixtures: startup never raises for
        the tick pool.
  - [ ] **Equities and Kalshi are unaffected:** with the closed-port
        fixture, `/api/v1/bars/{symbol}`, `/api/v1/symbols`,
        `/api/v1/kalshi/categories` and `/api/v1/overview` keep their
        existing status codes. Seed the minimum each needs; reuse the
        existing tests' seeding.
  - [ ] **No secret:** the closed-port `503` body does not contain the
        URL's password (FR11; 7.2 and 7.4 cover `/health` and `/status`).
  - [ ] Success: all pass; existing `test_app.py` tests pass unchanged
        apart from the guard.
- [ ] **3.4 Commit** (effort 1)
  - [ ] Unit tier for `test/unit/api_server`; the integration tier for
        `test/integration/api_server` and the two integration modules; mypy;
        ruff.
  - [ ] Commit: `feat: add tick pool with 503 posture to the API`.

## Section 4 - Response models (TD5)

- [ ] **4.1 Build `api_server/models/futures.py`** (effort 3)
  - [ ] **Time-series envelope** (TD5's example is the reference):
    - `FuturesTarget`: `kind`, `product`, `contract`, `instrument_id`,
      `rule`, `is_default`;
    - `SegmentRecord`, `UnresolvedRecord`, `IncompleteRecord`;
    - `TickRecord`, whose fields are `TICK_COLUMNS` in order, with string
      prices and times;
    - `BarRecord` (named `FuturesBarRecord`, to avoid clashing with the
      equities `BarRecord`), whose fields are `BAR_COLUMNS` in order;
    - `FuturesTicksResponse`, with `from`/`until` aliased, since `from`
      is a Python keyword;
    - `FuturesBarsResponse`.
  - [ ] **Builders:** `from_plan(plan, rows, ...)` classmethods that format
        through `price_text` and `ts_text` and build rows with
        `model_construct`.
  - [ ] **Catalog models**, each with a `from_…` classmethod over 229's
        dataclasses, never over the `roll_payload` dicts:
    - `FuturesProductsResponse`, `ActiveLineRecord`;
    - `FuturesContractsResponse`, `ContractRecord`, with a `held` sub-object;
    - `FuturesScheduleResponse`.
  - [ ] **Status block:** `TickBlock` (`state`, `detail`, `products`) and
        `TickAccountingRecord`, the same keys as `mt data accounting
        --json`'s `tick`.
  - [ ] If the file passes ~300 lines, split the catalog models into
        `models/futures_catalog.py`.
  - [ ] Success: every model has a docstring naming its route; no integer
        field can carry nanoseconds.
- [ ] **4.2 Test the models** (effort 2)
  - [ ] Unit tests:
    - `TickRecord` and `FuturesBarRecord` field names equal `TICK_COLUMNS`
      and `BAR_COLUMNS`, in order;
    - each catalog model's fields equal its `roll_payload` dictionary's
      keys, at every level, computed from sample dataclasses (not typed
      lists):
      - `ActiveLineRecord` against `active_line_dict`;
      - the schedule model against `schedule_dict`;
      - the contract record (used in `answers` and `next_roll`) against
        `contract_dict`;
      - the unresolved record against `unresolved_dict`;
      - the schedule segment record against the segment dictionary
        `schedule_dict` produces. It carries `expiration`, unlike the
        time-series `SegmentRecord`; two models, both pinned;
    - `UNDEF_INT64` serializes to `null`, and a price serializes exactly
      (`"5950.25"`);
    - a JSON dump of a tick response with a 2024 `ts_event` has no integer
      above 2^53;
    - `from`/`until` appear under those names in the dump.
  - [ ] Success: all pass.
- [ ] **4.3 Commit:** `feat: add futures API response models` (effort 1)

## Section 5 - Catalog routes (TD1, TD5)

- [ ] **5.1 Add `data/tick/catalog_reads.py`** (effort 2)
  - [ ] `contracts_for(conn, product) -> list[CatalogContract]`:
    - every `tick_definition` instrument whose `asset` is the product, one
      row per instrument (aggregate as `get_plan._candidates` does);
    - LEFT JOINed to the first and last `tick_ingest_ledger` session with
      `record_count > 0` under `COVERAGE_PREDICATE`;
    - ordered by expiration, then instrument id.
  - [ ] No API import. A frozen dataclass result.
  - [ ] Success: one statement; no hypertable scan (`EXPLAIN` shows no
        `tick_trade`).
- [ ] **5.2 Test `catalog_reads`** (effort 2)
  - [ ] Integration test over `seed_september_roll`:
    - both contracts listed in expiration order;
    - `held` matches each one's seeded sessions;
    - a defined contract with no ledger rows has `held` null;
    - a different product's definitions are excluded.
  - [ ] Success: passes.
- [ ] **5.3 Add `routes/futures_catalog.py` with the `contracts` route**
      (effort 2)
  - [ ] `GET /api/v1/futures/products/{product}/contracts`:
    - an unknown product (not in `TICK_UNIVERSE`) → `404 "Product 'X' not
      found"`, through `admission.not_found`;
    - it depends on `tick_connection`, calls `contracts_for` inside
      `tick_errors()`, and returns `FuturesContractsResponse`;
    - `responses=TICK_UNAVAILABLE_RESPONSE | GATEWAY_TIMEOUT_RESPONSE`.
  - [ ] Register the router in `create_app`.
  - [ ] Success: the route appears in `/openapi.json` with its model.
- [ ] **5.4 Test the `contracts` route** (effort 1)
  - [ ] Integration tests:
    - 200 with the seeded contracts and `min_price_increment` as a
      decimal string;
    - `404` for `ZZ`;
    - `503` under the closed-port fixture.
  - [ ] Success: passes.
- [ ] **5.5 Add the `products` route** (effort 3)
  - [ ] `GET /api/v1/futures/products`:
    - it depends on `tick_connection`, held for the request (TD2's
      connection bound);
    - for each `TICK_UNIVERSE` entry it calls `latest_lines(app tick_url,
      calendar_url, entry, STATUS_ROLL_RULES)`.
  - [ ] **Error mapping (TD2):**
    - first, `TickPreflightError` and `RollInputError` from one product's
      `latest_lines` → that product's lines each carry the error text, as
      `latest_active` builds them. Reuse that construction; if sharing it
      needs a small public helper in `active_contract.py`, add it there;
    - everything else goes through 3.1's `tick_errors()`: outages → `503`,
      `QueryCanceled` → `504`.
  - [ ] Returns `FuturesProductsResponse`, with each product's `calendar`
        from `calendar_for_product`.
  - [ ] Success: no `latest_active` call in the route.
- [ ] **5.6 Test the `products` route** (effort 3)
  - [ ] Integration tests:
    - 200 with ES's two lines, matching `mt data tick active --json` over
      the same seed (FR7). Run the CLI function in-process; do not
      shell out.
    - a product with nothing held → a line `reason`, HTTP 200;
    - the closed-port fixture → `503`;
    - a calendar URL pointing at a closed port with a live tick database →
      `503` (outage in the roll path).
  - [ ] Unit test with `latest_lines` patched to raise `RollInputError`:
        the line carries `error` and the response is 200.
  - [ ] **Two-product universe** (Integration Requirements; GC needs no API
        change). Patch `TICK_UNIVERSE` to ES plus a second entry with a
        known calendar, and seed one definition for the second product.
        Then:
    - `/futures/products` lists both;
    - the second product's `contracts` and `schedule` routes answer 200,
      not `404`.

    The same fixture is reused in 6.9.
  - [ ] Success: passes.
- [ ] **5.7 Add the `schedule` route** (effort 2)
  - [ ] `GET /api/v1/futures/products/{product}/schedule?rule=&start=&end=`:
    - `rule` goes through `RollRule.parse`; a `ValueError` → `422` with its
      message; the default is `DEFAULT_ROLL_RULE`;
    - `start`/`end` are session dates with `end` exclusive (TD3). When
      either is omitted, read `held_session_bounds(conn,
      shape_of(entry))` on the pooled connection and fill the missing side
      as `product_plan` does (`end` = last held + 1 day);
    - if nothing is held and a side is missing → 200 with empty
      `segments`, `answers` and `unresolved` (the same as `get_plan`'s
      empty plan); `build_schedule` is not called;
    - otherwise it calls `build_schedule`.
  - [ ] **Error mapping:** through 3.1's `tick_errors()`. An empty range
        (`end` ≤ `start`) is a `TickPreflightError` → `422`.
  - [ ] Success: route in the schema with `FuturesScheduleResponse`.
- [ ] **5.8 Test the `schedule` route** (effort 2)
  - [ ] Integration tests:
    - equals `mt data tick active --product ES --start … --end … --json`'s
      schedule over the September seed (FR7), including
      `definition_unit_id` and `basis_unit_ids`;
    - a bad rule (`x.0`) → `422`;
    - a range over `TICK_ROLL_MAX_SESSIONS` → `422` naming the cap;
    - `end` ≤ `start` → `422`.
  - [ ] Success: passes.
- [ ] **5.9 Commit** (effort 1)
  - [ ] Unit and integration tiers for the touched test directories; mypy;
        ruff; `git diff main` checked for swept lines.
  - [ ] Commit: `feat: add futures catalog routes`.
