---
docType: tasks
slice: api-client-contract-hardening
project: trading-data
lld: user/slices/186-slice.api-client-contract-hardening.md
dependencies: [184, 185]
projectState: >
  Serving API complete through slice 185 (merged, cf64b8e). `api_server/` has
  app.py, deps.py, routes/{health,bars,symbols,gaps,status}.py,
  models/responses.py. Bars responses carry `is_stale`; `get_bars` depends on
  `deps.get_db_pool` and scopes its checkout to the freshness probe. No range
  limits, no version wiring, three error-body shapes in circulation, and the
  API process opens three independent connection pools at 300s/512MB.
dateCreated: 20260803
dateUpdated: 20260804
status: complete
---

## Context Summary

- Working on the **api-client-contract-hardening** slice (186), the sixth slice
  of the 180 data-serving-api initiative. It is contract, limits, and metadata
  on surfaces that already exist — **no new endpoints and no new capability.**
- **Design reference:** `user/slices/186-slice.api-client-contract-hardening.md`.
  Decisions **D1–D11** are authoritative; tasks cite them rather than restate
  them. Read D1, D4, D5, D9, and D10 in full before writing code.
- **Diff against landed code, not against slice 185's design.** `is_stale`,
  `deps.get_db_pool`, and the `CAGG_BASE_GRANULARITY` probe branch in `bars.py`
  already exist. Do not reintroduce them.
- **Two breaking contract changes** ship here (D5 empty-window `200`, D6 error
  bodies). They land together with the regenerated schema, README, and CHANGELOG
  — do not merge one without the others.
- **The architecture document is already updated** (Phase 4, commit `c15726f`)
  per D11. No arch edits are needed here; Task 13 only verifies it still
  matches what was built.
- **No freshness logic, no pool resizing, no pagination, no auth.** D2 defers
  pool sizing to 187; D8 keeps the auth/CORS posture unchanged. A task that
  seems to need any of these is a misreading — stop and re-read the design.
- **Branch:** `186-slice.api-client-contract-hardening`, created from `main`
  (`git.integration_branch` is unset).

---

## Task 1 — Branch setup and grounding read

- [x] Create the slice branch and confirm the starting state
  - [x] Confirm `cf config get git.integration_branch` is empty; target is `main`
  - [x] From a clean tree on `main`, run
        `git checkout -b 186-slice.api-client-contract-hardening main`
  - [x] Read design D1, D4, D5, D9, D10 in full
  - [x] Read `api_server/app.py`, `deps.py`, `routes/bars.py`, and the
        `_configure_connection` + `_init_pool` methods of
        `market/timescale_minute_db.py` and `market/timescale_daily_db.py` —
        these five files are where nearly all of this slice lands
  - [x] Success: `uv run pytest test/unit/api_server/ -q` passes; record the
        baseline test count for later comparison
  - [x] Effort: 1

---

## Task 2 — Session-settings and range-cap constants

- [x] Add the session-settings type and its two instances to `constants.py` (D1)
  - [x] `DbSessionSettings` — frozen dataclass, fields `work_mem: str` and
        `statement_timeout: str`
  - [x] `DB_BULK_SESSION = DbSessionSettings("512MB", "300s")` — today's values,
        now named; docstring says these are for bulk/analytics paths (CLI,
        daemon) and are the defaults every existing consumer keeps
  - [x] `API_SERVING_SESSION = DbSessionSettings("64MB", "20s")` — docstring
        records the derivation from D1 (measured serving latencies; `work_mem`
        is per sort/hash node, not per connection)
  - [x] Success: both importable; no existing constant renamed or removed
  - [x] Effort: 1

- [x] Add the range-cap constants and derivation inputs (D4)
  - [x] `API_MAX_BARS_PER_REQUEST: int = 75_000` — docstring records the 75k
        compromise and the payload estimate (~8–10 MB JSON / 3.5–4 MB msgpack)
  - [x] `INTRADAY_MINUTES_PER_TRADING_DAY: int = 960` — docstring records the
        measurement it comes from: prod, 2026-08-03, coverage 08:00–23:59 UTC,
        AAPL 960 `1m` bars on 2024-06-10. **Not** the 390-minute regular session
  - [x] `GRANULARITY_BAR_MINUTES: dict[Granularity, int]` — the five intraday
        granularities only
  - [x] `TRADING_DAYS_PER_CALENDAR_DAY: float = 252 / 365`
  - [x] `BARS_PER_TRADING_DAY: dict[Granularity, float]` — **derived** from
        `INTRADAY_MINUTES_PER_TRADING_DAY / GRANULARITY_BAR_MINUTES` for
        intraday, plus literal `D1: 1.0, W1: 1/5, MO1: 1/21, Q1: 1/63`
  - [x] Success: every `Granularity` member has a `BARS_PER_TRADING_DAY` entry;
        no per-granularity max span is written as a literal anywhere
  - [x] Effort: 2

- [x] Test the constants (`test/unit/test_constants.py` or the existing module)
  - [x] Parametrized test: `BARS_PER_TRADING_DAY` is complete over
        `Granularity` and every value is positive
  - [x] Assert the derived intraday values equal 960/192/64/16/4 for
        `1m/5m/15m/1h/4h` — this pins the derivation, not the literals
  - [x] Success: tests pass; `ruff` and `mypy` clean on `constants.py`
  - [x] Effort: 1

---

## Task 3 — Operator-settable policy knobs

- [x] Add the two settings to `config/__init__.py` (D9)
  - [x] `api_max_bars_per_request: int = API_MAX_BARS_PER_REQUEST`
  - [x] `api_statement_timeout: str = API_SERVING_SESSION.statement_timeout`
  - [x] Comment records that the defaults live in `constants.py` (one
        definition) and that env names are `MT_API_MAX_BARS_PER_REQUEST` and
        `MT_API_STATEMENT_TIMEOUT` via the existing `MT_` prefix
  - [x] Success: `Settings().api_max_bars_per_request == 75_000` with no env set
  - [x] Effort: 1

- [x] Test the overrides (`test/unit/test_config.py` or equivalent)
  - [x] `monkeypatch.setenv("MT_API_MAX_BARS_PER_REQUEST", "1000")` →
        `Settings().api_max_bars_per_request == 1000`
  - [x] A non-integer override raises `pydantic.ValidationError` at
        `Settings()` construction — assert this explicitly; the failure must be
        at load, not at first request
  - [x] `MT_API_STATEMENT_TIMEOUT` override is picked up as a string
  - [x] Success: tests pass
  - [x] Effort: 1

---

## Task 4 — Single source for the package version

- [x] Add `src/manta_trading/version.py` with `package_version() -> str` (D3)
  - [x] Body is the existing logic from `cli/app.py:41-49`: try
        `importlib.metadata.version(DISTRIBUTION_NAME)`, on
        `PackageNotFoundError` log a warning and return `"dev"`
  - [x] Success: module has no imports from `cli` or `api_server` (leaf module)
  - [x] Effort: 1

- [x] Wire both consumers
  - [x] `cli/app.py::_version_callback` calls `package_version()`; its inline
        try/except is deleted, not duplicated
  - [x] `api_server/app.py::create_app` passes `version=package_version()` in
        place of the hardcoded `"0.1.0"`
  - [x] Success: `uv run mt --version` and `GET /openapi.json` report the same
        non-`0.1.0` string
  - [x] Effort: 1

- [x] Test (`test/unit/test_version.py`, plus one api_server assertion)
  - [x] Patch `importlib.metadata.version` to raise `PackageNotFoundError` →
        `package_version() == "dev"` and a warning is logged
  - [x] `create_app().openapi()["info"]["version"] == package_version()`
  - [x] Success: tests pass
  - [x] Effort: 1

---

## Task 5 — Plumb session settings into the two DB classes

- [x] `TimescaleMinuteDataDB` accepts an optional session argument (D1)
  - [x] `__init__(self, conninfo: str, *, session: DbSessionSettings = DB_BULK_SESSION)`
  - [x] `_configure_connection` is currently a `@staticmethod` and cannot see
        the instance — convert it to an instance method (or bind a closure at
        pool creation) so `configure=` uses this instance's session values
  - [x] `work_mem` and `statement_timeout` come from `session`; the class's
        other SETs (`timezone`, `max_parallel_workers_per_gather`,
        `enable_partitionwise_aggregate`) are unchanged and **not**
        parameterized
  - [x] Success: `TimescaleMinuteDataDB(conninfo)` issues exactly the same SQL
        it does today
  - [x] Effort: 2

- [x] Same change for `TimescaleDailyDataDB`
  - [x] Identical signature and mechanism; this class has no extra SETs
  - [x] Success: default construction is behavior-identical to today
  - [x] Effort: 1

- [x] Test both classes without a live DB
  - [x] Build a fake connection object recording `execute()` calls and an
        `autocommit` attribute; invoke the bound configure callable directly
  - [x] Default construction emits `SET work_mem = '512MB'` and
        `SET statement_timeout = '300s'` — this is the CLI/daemon regression
        guard and is the most important assertion in this task
  - [x] Construction with `API_SERVING_SESSION` emits `'64MB'` / `'20s'`
  - [x] Minute class still emits its two extra SETs in both cases
  - [x] Success: tests pass; no test requires a database
  - [x] Effort: 2

---

## Task 6 — API lifespan uses the serving session and resolves settings once

- [x] Update `api_server/app.py` lifespan and pool configuration (D1, D9)
  - [x] `_configure_connection` uses `API_SERVING_SESSION.work_mem` and the
        configured `statement_timeout` — no literal `'512MB'` or `'300s'`
        remains in `app.py`
  - [x] Construct the two DB instances with the same session values so all
        three pools match
  - [x] Resolve `Settings()` **once** in the lifespan and store the two policy
        values on `app.state` (D9: read at startup, not per request)
  - [x] Success: a request that touches each pool shows `20s`/`64MB` on every
        backend the API owns
  - [x] Effort: 2

- [x] Add a `deps` accessor for the bar ceiling
  - [x] `get_max_bars(request) -> int` returning the value stored on
        `app.state`; docstring notes it is resolved at startup
  - [x] Success: importable from `deps.py` alongside `get_db_pool`
  - [x] Effort: 1

- [x] Test the wiring
  - [x] With a fake pool, assert the configure callable emits the API values
  - [x] `MT_API_STATEMENT_TIMEOUT=5s` is reflected in what the configure
        callable emits — proves the setting reaches the pool, not just `Settings`
  - [x] Success: tests pass
  - [x] Effort: 2

---

## Task 7 — Range admission cap in `bars.py`

- [x] Add the estimator and the two rejections (D4)
  - [x] `_estimate_bars(granularity, start, end) -> float` and
        `_max_span_days(granularity, ceiling) -> int`, both module-level in
        `bars.py`, both derived from the Task 2 constants
  - [x] In `get_bars`, **before** any executor dispatch: `start > end` → `422`;
        estimate over ceiling → `422`
  - [x] The over-range message names the estimate, the ceiling, and the maximum
        span for that granularity, computed from the live ceiling (never a
        literal `75,000` or `113`)
  - [x] Ceiling comes from the `get_max_bars` dependency (Task 6)
  - [x] Success: `bars.py` stays under ~200 lines; no DB call precedes either
        check
  - [x] Effort: 3

- [x] Test the cap (`test/unit/api_server/test_bars.py`)
  - [x] A 20-year `1m` request returns `422` with `{"error": ...}` naming the
        ceiling and span
  - [x] **The rejected request checks out no connection** — assert with a pool
        whose `.connection()` raises if called. This is the point of the
        decision, not a side effect
  - [x] A request one day inside the boundary is admitted; one day outside is
        rejected (parametrized over `1m`, `5m`, `15m`)
  - [x] `1d` over 20 years is admitted — the cap never binds at daily grain
  - [x] `start > end` returns `422` with the reversed-range message
  - [x] With `MT_API_MAX_BARS_PER_REQUEST=1000`, a previously-admitted request
        is rejected and the message quotes 1,000
  - [x] Success: tests pass; no live DB
  - [x] Effort: 3

---

## Task 8 — Empty-window contract and the shared symbol lookup

- [x] Add `api_server/queries.py` with `symbol_exists` (D5 addendum, F009)
  - [x] `_SYMBOL_EXISTS_SQL` — primary-key seek on `instruments`
  - [x] `symbol_exists(conn, symbol) -> bool`; no try/except — failures
        propagate to the global handlers by design
  - [x] Success: one definition of the existence check in the codebase;
        `symbols.py`'s fuller `SELECT` is left alone
  - [x] Effort: 1

- [x] Change the empty-frame branch in `bars.py` (D5)
  - [x] On an empty frame, check out a connection from the pool **scoped to the
        lookup** (185 D8a pattern) and call `symbol_exists`
  - [x] Unknown → `404`; known → `200` with `count: 0`, `bars: []`, and the
        already-computed `is_stale`
  - [x] Confirm `BarsResponse.from_dataframe` handles an empty frame — it
        iterates rows and touches no columns, so it should need no change;
        prove that with a test rather than assuming it
  - [x] Success: the non-empty path still checks out no connection for raw
        granularities
  - [x] Effort: 2

- [x] Test the contract split
  - [x] Known symbol, empty frame → `200`, `count: 0`, `is_stale` present
  - [x] Unknown symbol, empty frame → `404` with `{"error": ...}`
  - [x] The lookup runs **only** when the frame is empty — assert it is not
        called on a non-empty response
  - [x] Parametrized over **all nine** granularities, assert a non-empty
        response leaves the API pool's checkout count unchanged by this slice
        (review F012): `1m`/`1d` check out **zero** connections, the seven
        cagg-served granularities check out **exactly one** (the 185 freshness
        probe — that checkout is correct and must not be asserted away). This
        pins the claim that D5 adds no connection to the hot path; 185's
        `test_raw_granularity_checks_out_no_connection` and
        `test_cagg_granularity_checks_out_exactly_one_connection` are the
        starting point — extend them rather than writing parallel tests
  - [x] Lookup raising `psycopg.errors.QueryCanceled` → `504` (needs Task 10;
        write the test now and mark it `xfail` until then, or sequence this
        assertion into Task 10's test)
  - [x] Lookup raising another `psycopg.Error` → `500`, never `200` or `404`
  - [x] Success: tests pass; no test asserts a default-on-failure behavior
  - [x] Effort: 3

---

## Task 9 — Unified error bodies

- [x] Widen the `HTTPException` handler in `create_app` (D6)
  - [x] Every `HTTPException` returns `{"error": str(exc.detail)}`, not just
        `404`; the existing `Exception` handler is unchanged
  - [x] `status.py` needs **no** change — its two `422`s raise `HTTPException`
        and inherit the new body. Verify this rather than editing the route
  - [x] Update the status route docstring if it still describes a `detail` body
  - [x] Success: no route module constructs an error body of its own
  - [x] Effort: 1

- [x] Test all three shapes
  - [x] `404` from bars → `{"error": ...}`
  - [x] `422` from `?health=` on the status route → `{"error": ...}`
  - [x] `422` from an invalid `granularity` (FastAPI validation) → still
        `{"detail": [...]}` — the documented exception, asserted deliberately
        so a future change cannot silently unify it
  - [x] Success: tests pass
  - [x] Effort: 2

---

## Task 10 — Cancelled queries return `504`

- [x] Register the `QueryCanceled` handler in `create_app` (D10)
  - [x] Handler for `psycopg.errors.QueryCanceled` returning `504` with
        `{"error": "query exceeded the server's <configured> budget; narrow the
        requested range or use a coarser granularity"}`
  - [x] The budget string comes from the resolved setting on `app.state`, never
        a literal `20s`
  - [x] Log at WARNING with method, path, and query string — handled and
        operator-actionable, not a crash
  - [x] Declare `504` in the `responses=` of the bars, status, symbols, and gaps
        routes so it lands in the committed schema
  - [x] Success: the handler is narrower than, and takes precedence over, the
        global `Exception` handler
  - [x] Effort: 2

- [x] Test the mapping
  - [x] A route whose DB call raises `QueryCanceled` → `504`, and the message
        quotes the configured budget (test with a non-default value so a
        hardcoded `20s` would fail)
  - [x] A route raising a different `psycopg.Error` → still `500` with the
        sanitized body
  - [x] **A cancelled freshness probe must NOT produce a `504`** (review F011).
        Make `assert_cagg_fresh`'s probe raise `QueryCanceled` internally and
        assert `/health` returns `200` with `coverage: "stale"`, and a bars
        request returns `200` with `is_stale: true`. This pins D10's
        load-bearing claim that a `504` always means a *data* query was
        cancelled — without it, `504` could mean "coverage probe timed out",
        for which "narrow the requested range" is useless advice
  - [x] The `504` appears in `create_app().openapi()` for all four routes
  - [x] Success: tests pass
  - [x] Effort: 2

---

## Task 11 — Committed OpenAPI artifact

- [x] Add `scripts/dump_openapi.py` (D7)
  - [x] Writes `create_app().openapi()` to `docs/api/openapi.json`, stable key
        order, trailing newline
  - [x] `--check` mode compares instead of writing and exits non-zero on drift
  - [x] Note in the module docstring that schema generation does not enter the
        lifespan, so no database is required
  - [x] Success: `uv run python scripts/dump_openapi.py` writes the file with
        no DB configured
  - [x] Effort: 2

- [x] Commit the artifact and its drift test
  - [x] Generate and commit `docs/api/openapi.json`
  - [x] `test/unit/api_server/test_openapi_artifact.py`: committed document
        equals generated **ignoring `info.version`**; generated `info.version`
        equals `package_version()`
  - [x] Success: both assertions pass; a deliberate route-signature edit makes
        the first fail
  - [x] Effort: 2

---

## Task 12 — README and CHANGELOG

- [x] Update the README API section
  - [x] Endpoint list gains the `/api/v1/status` entry (landed in 185 but never
        documented) and notes `is_stale` on bars responses
  - [x] New subsection: error shapes (`{"error": ...}` plus the documented
        FastAPI validation exception), the range cap and its per-granularity
        effect, the `404` vs empty-`200` split, and `504`
  - [x] Document `MT_API_MAX_BARS_PER_REQUEST` and `MT_API_STATEMENT_TIMEOUT`
        with their defaults, and link `docs/api/openapi.json`
  - [x] Success: a client dev can learn the contract from the README alone
  - [x] Effort: 2

- [x] Add the CHANGELOG entry under `[Unreleased]`
  - [x] Both breaking changes called out as breaking (D5, D6)
  - [x] Success: entry names the version-metadata fix, the cap, `504`, and the
        two settings
  - [x] Effort: 1

---

## Task 13 — Prod verification and the D1 measurement

- [x] Run the design's Verification Walkthrough against prod `trading`
  - [x] Steps 1–2: server starts; all three pools show `20s`/`64MB`
  - [x] Step 3: measure the four slowest legitimate calls. **If any exceeds 8 s,
        raise `API_SERVING_SESSION.statement_timeout` and record the numbers in
        design D1** — the constant is derived from this measurement, not asserted
  - [x] Step 6: a 112-day dense `1m` request — record `count`, elapsed time, and
        payload size; compare against D4's ~8–10 MB estimate and correct the
        design if it is off
  - [x] Step 7: both overrides, including `MT_API_STATEMENT_TIMEOUT=100ms` to
        induce a real `QueryCanceled` → `504`
  - [x] Steps 8–12: reversed range, empty vs unknown symbol, error bodies,
        schema artifact, CLI unaffected
  - [x] Rewrite the design's **Verification Walkthrough section only** with the
        actual commands run, the observed output, and any caveats found. This is
        mandated, not optional: Phase 4 creates the walkthrough as "the draft
        walkthrough that will be refined when Phase 6 (Implementation) is
        complete," and slice 185 set the precedent (review F010). Bounded to
        that one section — do **not** revise decisions D1–D11 here. The single
        exception is the two measurements D1 and D4 explicitly ask for
        (statement timeout, payload size), which are recorded in place
  - [x] Success: every step passes or its deviation is recorded in the design;
        no section other than the walkthrough (plus those two measurements) is
        edited
  - [x] Effort: 3

- [x] Confirm the architecture document still matches what was built
  - [x] Re-read the five sections D11 corrected in `180-arch.data-serving.md`
        (already committed in Phase 4) against the landed code
  - [x] Success: no correction needed, or the correction is committed
  - [x] Effort: 1

---

## Task 14 — Close-out

- [x] Full verification
  - [x] `uv run pytest test/unit -q` — compare pass count and error list against
        the Task 1 baseline; the 35 pre-existing DB-host errors are expected
  - [x] `uv run --extra dev mypy src/manta_trading/api_server/` clean; `ruff`
        clean on every touched file (pre-existing errors in untouched files
        stay untouched)
  - [x] Regenerate `docs/api/openapi.json` as the final step so it reflects the
        merged state
  - [x] Success: suite green, static analysis clean on touched files
  - [x] Effort: 1

- [x] Commit and merge
  - [x] Semantic commits throughout; mark tasks complete via `task-checker`
  - [x] `cf check` clean before merge
  - [x] Merge to `main` with `--no-ff` and the message
        `Merge slice 186: API client-contract hardening`
  - [x] Success: `main` green, `cf check` clean post-merge, branch left in place
  - [x] Effort: 1
