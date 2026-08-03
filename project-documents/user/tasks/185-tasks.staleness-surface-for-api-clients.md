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
status: not_started
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

- [ ] Create the slice branch and confirm the starting state
  - [ ] Confirm `cf config get git.integration_branch` is empty; target is `main`
  - [ ] From a clean tree on `main`, run
        `git checkout -b 185-slice.staleness-surface-for-api-clients main`
  - [ ] Read the slice design's D1–D10 sections in full before writing code
  - [ ] Read `src/manta_trading/data/maintenance/status_coverage.py` and
        `src/manta_trading/market/maintenance/cagg_freshness.py` (the
        `FreshnessVerdict` dataclass and `assert_cagg_fresh` signature only) —
        these are the contracts every task below consumes
  - [ ] Success: on the new branch, `uv run pytest test/unit/api_server/ -q`
        passes with the pre-existing test count recorded for later comparison
  - [ ] Effort: 1

---

## Task 2 — Status response models in `models/responses.py`

- [ ] Add the four new status models per design D2
  - [ ] `CoverageVerdict` — fields `view_name`, `is_fresh`, `signals`,
        `lag_seconds`, `threshold_seconds`, `detail`
  - [ ] `CoverageStatus` — fields `is_stale`, `verdicts: list[CoverageVerdict]`
  - [ ] `StatusRowRecord` — one field per `StatusRow` attribute in
        `cli/rendering/status_table.py` (symbol, granularity, health,
        bars_stored, first_bar_ts, last_bar_ts, gap_count, last_attempt_ts,
        last_attempt_outcome, target_end_ts, effective_start), with the same
        optionality as the dataclass
  - [ ] `StatusResponse` — fields `scope`, `symbol`, `count`, `rows`,
        `summary`, `coverage` per D2
  - [ ] Success: models import cleanly; `pyright` strict reports zero errors on
        `responses.py`
  - [ ] Effort: 1

- [ ] Add mapping constructors from the domain dataclasses to the models
  - [ ] `CoverageVerdict.from_verdict(FreshnessVerdict)` — converts
        `signals` to their `.value` strings, and `lag`/`threshold` (both
        `timedelta | None`) to `lag_seconds`/`threshold_seconds` via
        `.total_seconds()`, preserving `None`
  - [ ] `CoverageStatus.from_freshness(CoverageFreshness)` — maps
        `.is_stale` and each verdict in `.verdicts` order
  - [ ] `StatusRowRecord.from_status_row(StatusRow)` — field-name-for-field-name;
        no positional indexing
  - [ ] Import `FreshnessVerdict`/`CoverageFreshness`/`StatusRow` under
        `TYPE_CHECKING` where possible, matching the file's existing
        `Granularity` import pattern
  - [ ] Success: no `dict`-literal or string-keyed mapping is used; every field
        is named. `ruff` clean.
  - [ ] Effort: 2

---

## Task 3 — Tests for the status response models

- [ ] Create `test/unit/api_server/test_status.py` with a model-mapping section
  - [ ] Fixture builders for `FreshnessVerdict` (fresh and stale variants) and
        `CoverageFreshness`, following the pattern already established in
        `test/unit/cli/commands/test_data_status_coverage.py` — reuse that
        file's approach; do not invent a second fixture idiom
  - [ ] Test: a fresh `CoverageFreshness` maps to `is_stale=False` with two
        verdicts in `COVERAGE_VIEWS` order
  - [ ] Test: a stale verdict maps `signals` to their string values and
        `lag`/`threshold` timedeltas to float seconds
  - [ ] Test: a verdict with `lag=None`/`threshold=None` maps to
        `lag_seconds=None`/`threshold_seconds=None` (not `0.0` — a silent
        fallback would be a bug)
  - [ ] Test: `StatusRowRecord.from_status_row` round-trips every field
        including the nullable ones
  - [ ] Success: `uv run pytest test/unit/api_server/test_status.py -q` passes
  - [ ] Effort: 2

---

## Task 4 — Implement `routes/status.py`

- [ ] Create the route module with the query-parameter contract from D1
  - [ ] `symbol: str | None = None`
  - [ ] `health: str | None = None` — comma-separated; split, strip, upper-case,
        and validate each token against the existing `HealthStatus` StrEnum in
        `cli/rendering/status_table.py`. Do not define a second set of health
        strings; `status_queries.py` already imports from that module, so this
        is an established import path.
  - [ ] `granularity: Literal["daily", "minute"] | None = None` (DB family, as
        `data_status.granularity` stores it — same values `gaps.py` maps to)
  - [ ] `all_rows: bool = Query(False, alias="all")` — aliased so the public
        param is `all` without shadowing the builtin
  - [ ] Filter resolution mirrors the CLI exactly: `all_rows=True` →
        `health_filter=None`; otherwise the parsed `health` list, defaulting to
        the CLI's default of non-`OK` values when `health` is omitted
  - [ ] Invalid health token → `HTTPException(status_code=422)` naming the
        valid values, matching the 422 FastAPI already returns for the bars
        route's invalid granularity enum
  - [ ] Effort: 3

- [ ] Implement the handler body per D1 (async) and D9 (no new exception handling)
  - [ ] `async def get_status(...)` with `db: Annotated[psycopg.Connection[Any],
        Depends(get_db)]`
  - [ ] Dispatch `fetch_status_rows_with_freshness(...)` and
        `fetch_all_health_counts_with_freshness(conn)` concurrently via
        `asyncio.gather(loop.run_in_executor(...), loop.run_in_executor(...))`,
        the same shape `symbols.py::get_symbol` already uses
  - [ ] Build `StatusResponse` using the Task 2 mapping constructors; `scope` is
        `"symbol"` whenever `symbol` was supplied (even with zero rows, per D5),
        else `"all"`; `count` is `len(rows)`; `summary` is the unfiltered health
        counts; `coverage` comes from the freshness returned by the row fetch
  - [ ] Add **no** try/except — DB failures propagate to the global 500 handler
        registered in `app.py` (D9). Do not call `maybe_extend_trading_sessions`
        (D4). Do not embed gaps (D3).
  - [ ] Success: module is under ~120 lines, contains no SQL, and `ruff`/`pyright`
        strict are clean
  - [ ] Effort: 3

- [ ] Register the router in `app.py`
  - [ ] Import `status_router` and add `app.include_router(status_router)`
        alongside the four existing routers
  - [ ] Success: `create_app().routes` contains `/api/v1/status`
  - [ ] Effort: 1

---

## Task 5 — Tests for the `/api/v1/status` route

- [ ] Extend `test/unit/api_server/test_status.py` with route tests
  - [ ] Follow `test_health.py`'s app fixture pattern: `create_app()`, install a
        sentinel `app.state.db_pool`, override `get_db`, never enter
        `TestClient` as a context manager (so lifespan does not run)
  - [ ] Patch `status_queries.fetch_status_rows_with_freshness` and
        `fetch_all_health_counts_with_freshness` at the route module's import
        site; no live DB
  - [ ] Test: default request returns 200 with `scope="all"`, `coverage.verdicts`
        of length 2, and `summary` passed through unchanged
  - [ ] Test: `?symbol=SPY` sets `scope="symbol"` and forwards `symbol` to the
        fetch call
  - [ ] Test: unknown symbol (fetch returns `[]`) returns **200** with
        `rows: []` and `scope="symbol"` — not 404 (D5)
  - [ ] Test: `?all=true` forwards `health_filter=None`; omitting `health`
        forwards the CLI default non-`OK` list; `?health=OK,GAPS` forwards
        exactly those; `?health=BOGUS` returns 422
  - [ ] Test: `?granularity=daily` forwards `granularity="daily"`;
        `?granularity=hourly` returns 422 from Literal validation
  - [ ] Test: a stale `CoverageFreshness` surfaces `coverage.is_stale=true`
        while `rows` are still returned (report-don't-refuse, D9/167 D3a)
  - [ ] Test: the route does not call `maybe_extend_trading_sessions` — assert
        via a patch that would record a call (D4)
  - [ ] Success: all cases pass; no test requires a DB connection
  - [ ] Effort: 3

---

## Task 6 — Commit checkpoint: status endpoint

- [ ] Validate and commit the status surface
  - [ ] `uv run ruff check src test` and `uv run pyright` — zero new errors
  - [ ] `uv run pytest test/unit/api_server/ -q` — all pass
  - [ ] Commit from the project root: `feat(api): add GET /api/v1/status
        staleness endpoint`
  - [ ] Success: clean tree, buildable state, status endpoint complete and
        independently revertable
  - [ ] Effort: 1

---

## Task 7 — Health endpoint `coverage` field

- [ ] Extend `HealthResponse` in `models/responses.py`
  - [ ] Add `coverage: Literal["ok", "stale"] | None = None` per D6
  - [ ] Success: existing `exclude_none=True` serialization keeps the field
        absent when unset, so no existing client contract changes
  - [ ] Effort: 1

- [ ] Wire the coverage probe into `routes/health.py`
  - [ ] After the existing `SELECT 1` succeeds, call
        `status_coverage.check_coverage_freshness(db)` and set `coverage` to
        `"stale"` when `freshness.is_stale` else `"ok"`
  - [ ] Populate `coverage` **only** when `db == "ok"`; on the DB-error path
        leave it unset (D6)
  - [ ] Keep `health()` a **sync** handler — FastAPI runs sync routes in a
        worker thread, so no `run_in_executor` wrapping is needed here (D6).
        Do not convert it to `async def`.
  - [ ] Do not probe the seven bar-serving caggs — exactly the two coverage
        caggs via `check_coverage_freshness` (D6)
  - [ ] Success: route stays under ~55 lines; `ruff`/`pyright` clean
  - [ ] Effort: 2

---

## Task 8 — Tests for the health `coverage` field

- [ ] Extend `test/unit/api_server/test_health.py`
  - [ ] Patch `check_coverage_freshness` at the health module's import site
  - [ ] Test: healthy DB + fresh coverage → `{"status":"ok","db":"ok",
        "coverage":"ok"}`
  - [ ] Test: healthy DB + stale coverage → `coverage == "stale"`, still HTTP 200
  - [ ] Test: DB error → response has `db == "error"` and **no** `coverage` key,
        and `check_coverage_freshness` was never called
  - [ ] Test: `check_coverage_freshness` is called exactly once per request
        (guards against an accidental double-probe)
  - [ ] Success: pre-existing health tests still pass unmodified except where
        the new field is asserted
  - [ ] Effort: 2

---

## Task 9 — Commit checkpoint: health coverage

- [ ] Validate and commit the health surface
  - [ ] `uv run ruff check src test`, `uv run pyright`, and
        `uv run pytest test/unit/api_server/ -q` all clean
  - [ ] Commit: `feat(api): add coverage freshness field to health endpoint`
  - [ ] Effort: 1

---

## Task 10 — Bars `is_stale` field and `db` dependency

- [ ] Extend `BarsResponse` in `models/responses.py`
  - [ ] Add `is_stale: bool` (required, no default — an unset staleness value
        must not silently serialize as `False`)
  - [ ] Add an `is_stale` parameter to `BarsResponse.from_dataframe` and pass it
        through to the constructor
  - [ ] Success: `pyright` flags every existing `from_dataframe` call site that
        needs updating — fix each rather than defaulting the parameter
  - [ ] Effort: 2

- [ ] Add the `db` dependency to `routes/bars.py` per D8
  - [ ] Add `db: Annotated[psycopg.Connection[Any], Depends(get_db)]` to
        `get_bars`, reusing the existing pooled dependency — no new
        connection-management code
  - [ ] Success: existing bars behavior unchanged; route still returns 200 for
        the pre-existing test cases
  - [ ] Effort: 1

- [ ] Implement the freshness probe branch in `get_bars` per D7
  - [ ] Determine cagg-served vs raw with
        `CAGG_BASE_GRANULARITY[granularity] != granularity` — do **not** add a
        second granularity set alongside `_MINUTE_GRAINS`
  - [ ] For cagg-served granularities, run
        `assert_cagg_fresh(db, GRANULARITY_SOURCE[granularity])` in
        `run_in_executor`, concurrently with the existing data fetch via
        `asyncio.gather`; do not pass `source_table` (the helper resolves it)
  - [ ] Plain `asyncio.gather` — no `return_exceptions=True` (D9)
  - [ ] For `M1`/`D1`, issue no probe at all and set `is_stale=False`
  - [ ] `is_stale = verdict is not None and not verdict.is_fresh`
  - [ ] Field goes in the response **body**; add no header (D7)
  - [ ] Verify both the JSON and msgpack serialization paths carry the new field
  - [ ] Success: `get_bars` stays under ~50 lines of logic; `ruff` (`ASYNC`
        rules) and `pyright` strict clean
  - [ ] Effort: 3

---

## Task 11 — Tests for bars `is_stale`

- [ ] Update existing `test/unit/api_server/test_bars.py` cases
  - [ ] Override `get_db` in the app fixture so no test depends on the sentinel
        pool MagicMock resolving by accident
  - [ ] Update the two `from_dataframe` model tests for the new parameter
  - [ ] Success: all pre-existing bars tests pass with no behavioral change
        beyond the added field
  - [ ] Effort: 2

- [ ] Add new staleness cases
  - [ ] Patch `assert_cagg_fresh` at the bars module's import site
  - [ ] Test: `?granularity=5m` with a fresh verdict → `is_stale: false`, and
        the probe was called with `minute_5min_ohlcv`
  - [ ] Test: `?granularity=5m` with a stale verdict → `is_stale: true`, HTTP
        still 200, bars still returned
  - [ ] Test: `?granularity=1mo` probes `daily_monthly_ohlcv` (confirms the
        daily-family branch resolves through `GRANULARITY_SOURCE`)
  - [ ] Test: `?granularity=1m` and `?granularity=1d` → `is_stale: false` and
        `assert_cagg_fresh` was **never** called
  - [ ] Test: msgpack response carries `is_stale`
  - [ ] Success: `uv run pytest test/unit/api_server/ -q` fully green
  - [ ] Effort: 3

---

## Task 12 — Commit checkpoint: bars staleness

- [ ] Validate and commit the bars surface
  - [ ] `uv run ruff check src test`, `uv run pyright`, and the full
        `uv run pytest test/unit -q` (not just `api_server/`, since
        `BarsResponse.from_dataframe` changed signature)
  - [ ] Commit: `feat(api): add is_stale to bars responses for cagg granularities`
  - [ ] Effort: 1

---

## Task 13 — Regression check on the CLI path

- [ ] Confirm `mt data status` is unchanged (Success Criteria 7)
  - [ ] `git diff main --stat` shows **no** changes to `status_coverage.py`,
        `status_queries.py`, `cagg_freshness.py`, or
        `cli/rendering/status_table.py`
  - [ ] `uv run pytest test/unit/cli -q` passes
  - [ ] Success: this slice added callers only; if any of those four files
        appears in the diff, stop and reconcile against the design's Excluded
        list before proceeding
  - [ ] Effort: 1

---

## Task 14 — Live verification walkthrough

- [ ] Run the design's Verification Walkthrough steps 1–6 against a live DB
  - [ ] Start the server (`uv run mt serve`) with `MT_TIMESCALE_DB_URL` set
  - [ ] Step 2: `/api/v1/status` — 2 coverage verdicts, `summary` present
  - [ ] Step 3: `/api/v1/status?symbol=SPY&all=true` — `scope: "symbol"`, daily
        and minute rows returned
  - [ ] Step 4: `/api/v1/health` — includes `coverage`
  - [ ] Step 5: `/api/v1/bars/SPY?granularity=5m&...` — `is_stale` present
  - [ ] Step 6: `?granularity=1d` — `is_stale: false`
  - [ ] Record the observed cold-cache added latency on the step-5 request
        against the D7 budget (near-zero on cache hit; ≤ +2.5s on cache miss).
        If the observed miss cost exceeds the budget on the minute caggs, report
        it to the Project Manager as new information rather than silently
        accepting it.
  - [ ] Success: all six steps produce the documented response shapes
  - [ ] Effort: 2

- [ ] Induced-staleness verification (Walkthrough step 7) — **disposable test DB
      only, never prod**
  - [ ] Confirm with the Project Manager which DB to use before starting; if no
        disposable DB with representative caggs is available, record the step as
        not run rather than substituting `trading_test`, whose views are not
        caggs
  - [ ] Follow `user/runbooks/cagg-maintenance-pausing.md` to pause the relevant
        refresh policy, then re-run walkthrough steps 2, 4, and 5
  - [ ] Confirm all three surfaces report stale **and still return rows**
  - [ ] Resume the policy; confirm all three report fresh again after the 60s
        TTL window elapses
  - [ ] Success: staleness is observably surfaced end-to-end, not just in mocks
  - [ ] Effort: 3

---

## Task 15 — Slice wrap-up

- [ ] Close out the slice
  - [ ] Update the slice design frontmatter `status` to `complete` and
        `dateUpdated`
  - [ ] Record any deviation from D1–D10 in the slice doc (there should be none;
        if there is, it needs a stated reason)
  - [ ] Commit the doc update, then merge
        `185-slice.staleness-surface-for-api-clients` into `main`
  - [ ] Note for the 186 breakdown (D10): 186 must diff against 185's landed
        `bars.py`/`responses.py` — `is_stale` and the `db` dependency are
        already present and are not 186's to reintroduce
  - [ ] Success: branch merged, tree clean, `main` green
  - [ ] Effort: 1

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
