---
docType: tasks
slice: staleness-surface-for-api-clients
project: trading-data
lld: user/slices/185-slice.staleness-surface-for-api-clients.md
dependencies: [167, 168, 184]
projectState: >
  Serving API (slices 181–184) complete and running; api_server package has
  app.py, deps.py, routes/{health,bars,symbols,gaps}.py, models/responses.py.
  Freshness machinery from 167/168 complete and in production use by
  `mt data status`. No API surface currently exposes staleness.
dateCreated: 20260803
dateUpdated: 20260803
status: in_progress
---

## Context Summary

- Working on the **staleness-surface-for-api-clients** slice (185), the fifth
  slice of the 180 data-serving-api initiative.
- **What this slice delivers:** three additive staleness surfaces on the serving
  API — a new `GET /api/v1/status` endpoint, a `coverage` field on
  `GET /api/v1/health`, and an `is_stale` field on bars responses for
  cagg-served granularities.
- **Key assumption:** this slice writes **no new freshness logic**. Every task
  below is a new *caller* of machinery that already exists and is proven
  (`cagg_freshness.assert_cagg_fresh` from 168; `status_coverage`/
  `status_queries` from 167). Any task that appears to require editing
  `status_coverage.py`, `status_queries.py`, or `cagg_freshness.py` is a
  misreading — stop and re-read the slice design.
- **Prerequisites:** slices 167, 168 complete (freshness machinery); slice 184
  complete (current `api_server/` package state, global 500 handler).
- **Design reference:** `user/slices/185-slice.staleness-surface-for-api-clients.md`.
  Decisions D1–D10 are authoritative; tasks reference them rather than
  restating them.
- **Ownership boundary:** D10 fixes the `bars.py`/`responses.py` split with
  slice 186. Do not implement range caps, pagination, pool tuning, or the
  `openapi.json` version fix here — those are 186.
- **Next planned slice:** 186 (API hardening), then 187 (coverage-cagg-backed
  symbol ranges), which depends on this slice.
- **Branch:** `185-slice.staleness-surface-for-api-clients`, created from
  `main` (`git.integration_branch` is unset).

---

## Task 1 — Branch setup and grounding read

- [x] Create the slice branch and confirm the starting state
  - [x] Confirm `cf config get git.integration_branch` is empty; target is `main`
  - [x] From a clean tree on `main`, run
        `git checkout -b 185-slice.staleness-surface-for-api-clients main`
  - [x] Read the slice design's D1–D10 sections in full before writing code
  - [x] Read `src/manta_trading/data/maintenance/status_coverage.py` and
        `src/manta_trading/market/maintenance/cagg_freshness.py` (the
        `FreshnessVerdict` dataclass and `assert_cagg_fresh` signature only) —
        these are the contracts every task below consumes
  - [x] Success: on the new branch, `uv run pytest test/unit/api_server/ -q`
        passes with the pre-existing test count recorded for later comparison
  - [x] Effort: 1

---

## Task 2 — Status response models in `models/responses.py`

- [x] Add the four new status models per design D2
  - [x] `CoverageVerdict` — fields `view_name`, `is_fresh`, `signals`,
        `lag_seconds`, `threshold_seconds`, `detail`
  - [x] `CoverageStatus` — fields `is_stale`, `verdicts: list[CoverageVerdict]`
  - [x] `StatusRowRecord` — one field per `StatusRow` attribute in
        `cli/rendering/status_table.py` (symbol, granularity, health,
        bars_stored, first_bar_ts, last_bar_ts, gap_count, last_attempt_ts,
        last_attempt_outcome, target_end_ts, effective_start), with the same
        optionality as the dataclass
  - [x] `StatusResponse` — fields `scope`, `symbol`, `count`, `rows`,
        `summary`, `coverage` per D2
  - [x] Success: models import cleanly; `pyright` strict reports zero errors on
        `responses.py`
  - [x] Effort: 1

- [x] Add mapping constructors from the domain dataclasses to the models
  - [x] `CoverageVerdict.from_verdict(FreshnessVerdict)` — converts
        `signals` to their `.value` strings, and `lag`/`threshold` (both
        `timedelta | None`) to `lag_seconds`/`threshold_seconds` via
        `.total_seconds()`, preserving `None`
  - [x] `CoverageStatus.from_freshness(CoverageFreshness)` — maps
        `.is_stale` and each verdict in `.verdicts` order
  - [x] `StatusRowRecord.from_status_row(StatusRow)` — field-name-for-field-name;
        no positional indexing
  - [x] Import `FreshnessVerdict`/`CoverageFreshness`/`StatusRow` under
        `TYPE_CHECKING` where possible, matching the file's existing
        `Granularity` import pattern
  - [x] Success: no `dict`-literal or string-keyed mapping is used; every field
        is named. `ruff` clean.
  - [x] Effort: 2

---

## Task 3 — Tests for the status response models

- [x] Create `test/unit/api_server/test_status.py` with a model-mapping section
  - [x] Fixture builders for `FreshnessVerdict` (fresh and stale variants) and
        `CoverageFreshness`, following the pattern already established in
        `test/unit/cli/commands/test_data_status_coverage.py` — reuse that
        file's approach; do not invent a second fixture idiom
  - [x] Test: a fresh `CoverageFreshness` maps to `is_stale=False` with two
        verdicts in `COVERAGE_VIEWS` order
  - [x] Test: a stale verdict maps `signals` to their string values and
        `lag`/`threshold` timedeltas to float seconds
  - [x] Test: a verdict with `lag=None`/`threshold=None` maps to
        `lag_seconds=None`/`threshold_seconds=None` (not `0.0` — a silent
        fallback would be a bug)
  - [x] Test: `StatusRowRecord.from_status_row` round-trips every field
        including the nullable ones
  - [x] Success: `uv run pytest test/unit/api_server/test_status.py -q` passes
  - [x] Effort: 2

---

## Task 4 — Implement `routes/status.py`

- [x] Create the route module with the query-parameter contract from D1
  - [x] `symbol: str | None = None`
  - [x] `health: str | None = None` — comma-separated; split, strip, upper-case,
        and validate each token against the existing `HealthStatus` StrEnum in
        `cli/rendering/status_table.py`. Do not define a second set of health
        strings; `status_queries.py` already imports from that module, so this
        is an established import path.
  - [x] `granularity: Literal["daily", "minute"] | None = None` (DB family, as
        `data_status.granularity` stores it — same values `gaps.py` maps to)
  - [x] `all_rows: bool = Query(False, alias="all")` — aliased so the public
        param is `all` without shadowing the builtin
  - [x] Filter resolution mirrors the CLI exactly: `all_rows=True` →
        `health_filter=None`; otherwise the parsed `health` list, defaulting to
        the CLI's default of non-`OK` values when `health` is omitted
  - [x] Invalid health token → `HTTPException(status_code=422)` naming the
        valid values, matching the 422 FastAPI already returns for the bars
        route's invalid granularity enum
  - [x] Effort: 3

- [x] Implement the handler body per D1 (async) and D9 (no new exception handling)
  - [x] `async def get_status(...)` with `db: Annotated[psycopg.Connection[Any],
        Depends(get_db)]`
  - [x] ~~Dispatch `fetch_status_rows_with_freshness(...)` and
        `fetch_all_health_counts_with_freshness(conn)` concurrently via
        `asyncio.gather(loop.run_in_executor(...), loop.run_in_executor(...))`,
        the same shape `symbols.py::get_symbol` already uses~~ — **amended
        during implementation; see slice design D1a.** Both calls run
        sequentially inside one `run_in_executor`. They share a single pooled
        connection, and `psycopg.Cursor.execute` holds the connection lock for
        every statement, so gathering them buys no parallelism while letting
        the freshness guard's `statement_timeout` save/restore interleave
        across two threads — which can leave a pooled connection clamped to the
        probe's 10s. Sequential order also lets the second call hit the verdict
        cache the first warmed. The handler is still `async def` and still runs
        off the event loop.
  - [x] Build `StatusResponse` using the Task 2 mapping constructors; `scope` is
        `"symbol"` whenever `symbol` was supplied (even with zero rows, per D5),
        else `"all"`; `count` is `len(rows)`; `summary` is the unfiltered health
        counts; `coverage` comes from the freshness returned by the row fetch
  - [x] Add **no** try/except — DB failures propagate to the global 500 handler
        registered in `app.py` (D9). Do not call `maybe_extend_trading_sessions`
        (D4). Do not embed gaps (D3).
  - [x] Success: module is under ~120 lines, contains no SQL, and `ruff`/`pyright`
        strict are clean
  - [x] Effort: 3

- [x] Register the router in `app.py`
  - [x] Import `status_router` and add `app.include_router(status_router)`
        alongside the four existing routers
  - [x] Success: `create_app().routes` contains `/api/v1/status`
  - [x] Effort: 1

---

## Task 5 — Tests for the `/api/v1/status` route

- [x] Extend `test/unit/api_server/test_status.py` with route tests
  - [x] Follow `test_health.py`'s app fixture pattern: `create_app()`, install a
        sentinel `app.state.db_pool`, override `get_db`, never enter
        `TestClient` as a context manager (so lifespan does not run)
  - [x] Patch `status_queries.fetch_status_rows_with_freshness` and
        `fetch_all_health_counts_with_freshness` at the route module's import
        site; no live DB
  - [x] Test: default request returns 200 with `scope="all"`, `coverage.verdicts`
        of length 2, and `summary` passed through unchanged
  - [x] Test: `?symbol=SPY` sets `scope="symbol"` and forwards `symbol` to the
        fetch call
  - [x] Test: unknown symbol (fetch returns `[]`) returns **200** with
        `rows: []` and `scope="symbol"` — not 404 (D5)
  - [x] Test: `?all=true` forwards `health_filter=None`; omitting `health`
        forwards the CLI default non-`OK` list; `?health=OK,GAPS` forwards
        exactly those; `?health=BOGUS` returns 422
  - [x] Test: `?granularity=daily` forwards `granularity="daily"`;
        `?granularity=hourly` returns 422 from Literal validation
  - [x] Test: a stale `CoverageFreshness` surfaces `coverage.is_stale=true`
        while `rows` are still returned (report-don't-refuse, D9/167 D3a)
  - [x] Test: the route does not call `maybe_extend_trading_sessions` — assert
        via a patch that would record a call (D4)
  - [x] Success: all cases pass; no test requires a DB connection
  - [x] Effort: 3

---

## Task 6 — Commit checkpoint: status endpoint

- [x] Validate and commit the status surface
  - [x] `uv run ruff check src test` and `uv run pyright` — zero new errors
  - [x] `uv run pytest test/unit/api_server/ -q` — all pass
  - [x] Commit from the project root: `feat(api): add GET /api/v1/status
        staleness endpoint`
  - [x] Success: clean tree, buildable state, status endpoint complete and
        independently revertable
  - [x] Effort: 1

---

## Task 7 — Health endpoint `coverage` field

- [x] Extend `HealthResponse` in `models/responses.py`
  - [x] Add `coverage: Literal["ok", "stale"] | None = None` per D6
  - [x] Success: existing `exclude_none=True` serialization keeps the field
        absent when unset, so no existing client contract changes
  - [x] Effort: 1

- [x] Wire the coverage probe into `routes/health.py`
  - [x] After the existing `SELECT 1` succeeds, call
        `status_coverage.check_coverage_freshness(db)` and set `coverage` to
        `"stale"` when `freshness.is_stale` else `"ok"`
  - [x] Populate `coverage` **only** when `db == "ok"`; on the DB-error path
        leave it unset (D6)
  - [x] Keep `health()` a **sync** handler — FastAPI runs sync routes in a
        worker thread, so no `run_in_executor` wrapping is needed here (D6).
        Do not convert it to `async def`.
  - [x] Do not probe the seven bar-serving caggs — exactly the two coverage
        caggs via `check_coverage_freshness` (D6)
  - [x] Success: route stays under ~55 lines; `ruff`/`pyright` clean
  - [x] Effort: 2

---

## Task 8 — Tests for the health `coverage` field

- [x] Extend `test/unit/api_server/test_health.py`
  - [x] Patch `check_coverage_freshness` at the health module's import site
  - [x] Test: healthy DB + fresh coverage → `{"status":"ok","db":"ok",
        "coverage":"ok"}`
  - [x] Test: healthy DB + stale coverage → `coverage == "stale"`, still HTTP 200
  - [x] Test: DB error → response has `db == "error"` and **no** `coverage` key,
        and `check_coverage_freshness` was never called
  - [x] Test: `check_coverage_freshness` is called exactly once per request
        (guards against an accidental double-probe)
  - [x] Success: pre-existing health tests still pass unmodified except where
        the new field is asserted
  - [x] Effort: 2

---

## Task 9 — Commit checkpoint: health coverage

- [x] Validate and commit the health surface
  - [x] `uv run ruff check src test`, `uv run pyright`, and
        `uv run pytest test/unit/api_server/ -q` all clean
  - [x] Commit: `feat(api): add coverage freshness field to health endpoint`
  - [x] Effort: 1

---

## Task 10 — Bars `is_stale` field and `db` dependency

- [x] Extend `BarsResponse` in `models/responses.py`
  - [x] Add `is_stale: bool` (required, no default — an unset staleness value
        must not silently serialize as `False`)
  - [x] Add an `is_stale` parameter to `BarsResponse.from_dataframe` and pass it
        through to the constructor
  - [x] Success: `pyright` flags every existing `from_dataframe` call site that
        needs updating — fix each rather than defaulting the parameter
  - [x] Effort: 2

- [x] Add the `db` dependency to `routes/bars.py` per D8
  - [x] Add `db: Annotated[psycopg.Connection[Any], Depends(get_db)]` to
        `get_bars`, reusing the existing pooled dependency — no new
        connection-management code
  - [x] Success: existing bars behavior unchanged; route still returns 200 for
        the pre-existing test cases
  - [x] Effort: 1

- [x] Implement the freshness probe branch in `get_bars` per D7
  - [x] Determine cagg-served vs raw with
        `CAGG_BASE_GRANULARITY[granularity] != granularity` — do **not** add a
        second granularity set alongside `_MINUTE_GRAINS`
  - [x] For cagg-served granularities, run
        `assert_cagg_fresh(db, GRANULARITY_SOURCE[granularity])` in
        `run_in_executor`, concurrently with the existing data fetch via
        `asyncio.gather`; do not pass `source_table` (the helper resolves it)
  - [x] Plain `asyncio.gather` — no `return_exceptions=True` (D9)
  - [x] For `M1`/`D1`, issue no probe at all and set `is_stale=False`
  - [x] `is_stale = verdict is not None and not verdict.is_fresh`
  - [x] Field goes in the response **body**; add no header (D7)
  - [x] Verify both the JSON and msgpack serialization paths carry the new field
  - [x] Success: `get_bars` stays under ~50 lines of logic; `ruff` (`ASYNC`
        rules) and `pyright` strict clean
  - [x] Effort: 3

---

## Task 11 — Tests for bars `is_stale`

- [x] Update existing `test/unit/api_server/test_bars.py` cases
  - [x] Override `get_db` in the app fixture so no test depends on the sentinel
        pool MagicMock resolving by accident
  - [x] Update the two `from_dataframe` model tests for the new parameter
  - [x] Success: all pre-existing bars tests pass with no behavioral change
        beyond the added field
  - [x] Effort: 2

- [x] Add new staleness cases
  - [x] Patch `assert_cagg_fresh` at the bars module's import site
  - [x] Test: `?granularity=5m` with a fresh verdict → `is_stale: false`, and
        the probe was called with `minute_5min_ohlcv`
  - [x] Test: `?granularity=5m` with a stale verdict → `is_stale: true`, HTTP
        still 200, bars still returned
  - [x] Test: `?granularity=1mo` probes `daily_monthly_ohlcv` (confirms the
        daily-family branch resolves through `GRANULARITY_SOURCE`)
  - [x] Test: `?granularity=1m` and `?granularity=1d` → `is_stale: false` and
        `assert_cagg_fresh` was **never** called
  - [x] Test: msgpack response carries `is_stale`
  - [x] Success: `uv run pytest test/unit/api_server/ -q` fully green
  - [x] Effort: 3

---

## Task 12 — Commit checkpoint: bars staleness

- [x] Validate and commit the bars surface
  - [x] `uv run ruff check src test`, `uv run pyright`, and the full
        `uv run pytest test/unit -q` (not just `api_server/`, since
        `BarsResponse.from_dataframe` changed signature)
  - [x] Commit: `feat(api): add is_stale to bars responses for cagg granularities`
  - [x] Effort: 1

---

## Task 13 — Regression check on the CLI path

- [x] Confirm `mt data status` is unchanged (Success Criteria 7)
  - [x] `git diff main --stat` shows **no** changes to `status_coverage.py`,
        `status_queries.py`, `cagg_freshness.py`, or
        `cli/rendering/status_table.py`
  - [x] `uv run pytest test/unit/cli -q` passes
  - [x] Success: this slice added callers only; if any of those four files
        appears in the diff, stop and reconcile against the design's Excluded
        list before proceeding
  - [x] Effort: 1

---

## Task 14 — Live verification walkthrough

- [x] Run the design's Verification Walkthrough steps 1–6 against a live DB
  - [x] Start the server (`uv run mt serve`) with `MT_TIMESCALE_DB_URL` set
  - [x] Step 2: `/api/v1/status` — 2 coverage verdicts, `summary` present
  - [x] Step 3: `/api/v1/status?symbol=SPY&all=true` — `scope: "symbol"`, daily
        and minute rows returned
  - [x] Step 4: `/api/v1/health` — includes `coverage`
  - [x] Step 5: `/api/v1/bars/SPY?granularity=5m&...` — `is_stale` present
  - [x] Step 6: `?granularity=1d` — `is_stale: false`
  - [x] Record the observed cold-cache added latency on the step-5 request
        against the D7 budget (near-zero on cache hit; ≤ +2.5s on cache miss).
        If the observed miss cost exceeds the budget on the minute caggs, report
        it to the Project Manager as new information rather than silently
        accepting it.
  - [x] Success: all six steps produce the documented response shapes
  - [x] Effort: 2

- [ ] Induced-staleness verification (Walkthrough step 7) — **disposable test DB
      only, never prod** — **NOT RUN (2026-08-03, PM decision).** No suitable DB
      exists: prod is off-limits for pausing a refresh policy, and `trading_test`
      is ruled out (plain views, not caggs — nothing to pause). Filed as future
      work item 3 on `user/architecture/180-slices.data-serving-api.md`.
      Re-open this task once that DB exists.
  - [x] Confirm with the Project Manager which DB to use before starting; if no
        disposable DB with representative caggs is available, record the step as
        not run rather than substituting `trading_test`, whose views are not
        caggs
  - [ ] Follow `user/runbooks/cagg-maintenance-pausing.md` to pause the relevant
        refresh policy, then re-run walkthrough steps 2, 4, and 5
  - [ ] Confirm all three surfaces report stale **and still return rows**
  - [ ] Resume the policy; confirm all three report fresh again after the 60s
        TTL window elapses
  - [ ] Success: staleness is observably surfaced end-to-end, not just in mocks
  - [x] Effort: 3

---

## Task 15 — Slice wrap-up

- [x] Close out the slice
  - [x] Update the slice design frontmatter `status` to `complete` and
        `dateUpdated`
  - [x] Record any deviation from D1–D10 in the slice doc (there should be none;
        if there is, it needs a stated reason)
  - [x] Commit the doc update, then merge
        `185-slice.staleness-surface-for-api-clients` into `main`
  - [x] Note for the 186 breakdown (D10): 186 must diff against 185's landed
        `bars.py`/`responses.py` — `is_stale` and the `db` dependency are
        already present and are not 186's to reintroduce
  - [x] Success: branch merged, tree clean, `main` green
  - [x] Effort: 1

---

## Notes for the Project Manager

- **Invalid `?health=` handling (Task 4)** is the one contract detail D1 leaves
  open. The tasks specify `422` with the valid values named, chosen to match the
  422 the bars route already returns for an invalid granularity enum rather than
  inventing a `400`. Flagging it because it is a client-visible contract, not
  because it is ambiguous in implementation.
- **`granularity` family values** (`"daily"`/`"minute"`) have no shared enum in
  the codebase today — `gaps.py` spells them in a local dict. This slice uses a
  `Literal` per D1 rather than introducing a shared enum, which would touch
  files outside this slice's scope. Consolidating them is a reasonable future
  cleanup, filed nowhere yet.
</content>
</invoke>
