---
docType: tasks
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
lld: user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md
dependencies: [167, 185, 186]
projectState: >
  Serving API complete through slice 186 (merged). `GET /api/v1/symbols/{symbol}`
  computes `available` with two lazy MIN/MAX queries dispatched via
  `asyncio.gather` on one pooled connection — measured on prod at 2.7-4.0s,
  96% planning time over daily_ohlcv's 3,371 chunks. `minute_coverage` and
  `daily_coverage` (slice 167) exist but are structurally stale (52 days on the
  daily side) because their 365-day refresh bucket is never re-materialized
  after creation; `assert_cagg_fresh` cannot detect this (one-bucket detection
  floor). No `test/load/` coverage exists for `api_server`.
dateCreated: 20260804
dateUpdated: 20260804
status: complete
---

## Context Summary

- Working on the **symbols-ranges-via-coverage-caggs-api-load-test-tier** slice
  (187), the seventh slice of the 180 data-serving-api initiative. Two pieces
  that share one prerequisite: (a) replace `GET /api/v1/symbols/{symbol}`'s
  `available` computation with a coverage-floor + bounded-head-probe read, (b)
  add the `test/load/` tier for `api_server`.
- **Design reference:** `user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md`.
  Decisions **D1–D12** are authoritative; tasks cite them rather than restate
  them. Read D1–D4 and D6–D10 in full before writing code — D1 and D5
  especially, since they invert what the slice-plan entry originally assumed.
- **This slice does NOT repair the coverage caggs.** That is slice 169 (D5),
  filed separately. This slice makes the endpoint correct *despite* the
  staleness (D2's head probe) and makes the staleness *visible* (D6's
  content-edge check) — those are two independent changes, and D6 will flip
  prod's `coverage` field to `"stale"` on every operator surface the moment it
  ships. That is intended (D5, D6); do not treat it as a regression to fix.
- **No unbounded aggregate on any path.** Every statement this slice adds or
  touches must carry a bound the planner can prune on (D1, D2, D3). If a task
  seems to need a plain `MIN/MAX ... WHERE symbol = %s` with no other
  predicate, re-read D1 — that is the exact query this slice replaces.
- **`GET /api/v1/symbols/{symbol}`'s response shape does not change.**
  `SymbolDetail`, `AvailableRange`, the 404 contract, and the omit-empty-family
  rule are unchanged — only how `available` is computed. The committed
  `docs/api/openapi.json` is expected to show no diff for this route.
- **`CycleGranularity` (slice 912), not a string literal, is the family tag**
  for the coverage/head-probe UNION (D7). No bare `"minute"`/`"daily"` in new
  code.
- **Branch:** `187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier`,
  created from `main` (`git.integration_branch` is unset).
- **This is file 1 of 2.** Tasks 1–9 (the read-path implementation: freshness
  guard, `create_app` seam, `fetch_available_ranges`, and the `get_symbol`
  rewire) live here. Tasks 10–16 (integration test, load tier, D11, docs,
  prod verification, close-out) continue in
  `187-tasks.symbols-ranges-via-coverage-caggs-api-load-test-tier-2.md`. Work
  both files sequentially as one slice — the split is a file-length limit
  only, not a scope boundary.

---

## Task 1 — Branch setup and grounding read

- [x] Create the slice branch and confirm the starting state
  - [x] Confirm `cf config get git.integration_branch` is empty; target is `main`
  - [x] From a clean tree on `main`, run
        `git checkout -b 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier main`
  - [x] Read design D1–D4 and D6–D10 in full
  - [x] Read `api_server/routes/symbols.py`, `api_server/queries.py`,
        `api_server/app.py`, `data/maintenance/status_coverage.py`, and
        `market/maintenance/cagg_freshness.py` (`FreshnessVerdict`, `_raw_max`,
        `_evaluate`, `assert_cagg_fresh`) — these five files are where nearly
        all of this slice lands
  - [x] Success: `uv run pytest test/unit -q` passes; record the baseline test
        count for later comparison
  - [x] Effort: 1

---

## Task 2 — Coverage content-edge staleness constant

- [x] Add `COVERAGE_CONTENT_STALENESS` to `constants.py` (D6)
  - [x] `COVERAGE_CONTENT_STALENESS: timedelta` — value is
        `MAX_COVERAGE_SOURCE_STALENESS + <the coverage refresh policies'
        end_offset>`, read from prod (1–4 h per D5) rather than guessed;
        docstring records the derivation and states explicitly this is a
        *detection* fix, not a policy tightening (D6)
  - [x] Docstring distinguishes it from `MAX_COVERAGE_SOURCE_STALENESS`: this
        measures content lag (`max(last_bucket)` vs raw `max(time)`, no bucket
        alignment), not bucket lag
  - [x] Success: importable; no existing constant renamed; value is not equal
        to `MAX_COVERAGE_SOURCE_STALENESS` by coincidence-only reasoning — the
        docstring shows the arithmetic
  - [x] Effort: 1

---

## Task 3 — `bucket_width` on `FreshnessVerdict`; pin the detection floor

- [x] Add `bucket_width` to `FreshnessVerdict` and thread it through `_evaluate` (D6)
  - [x] New frozen-dataclass field `bucket_width: str | None`, populated from
        the `_bucket_width` call already made inside `_evaluate` (no new
        catalog read)
  - [x] Every existing `FreshnessVerdict(...)` construction site in
        `cagg_freshness.py` (including the `PROBE_FAILED` and `NO_JOB_ROW`
        early returns) supplies it — `None` where `_bucket_width` was not
        reached
  - [x] Extend the `_raw_max` docstring with an explicit statement of the
        one-bucket detection floor: bucketing the raw edge onto the cagg's own
        grid means no lag smaller than one bucket width can ever be observed
  - [x] Success: `mypy`/`pyright` clean on the module; no existing caller of
        `assert_cagg_fresh` breaks (it's an added field, not a signature change
        to the function)
  - [x] Effort: 2

- [x] Test the detection floor is real and machine-checked (D6, success criterion 5)
  - [x] New test in `test/unit/market/maintenance/test_cagg_freshness.py` (or
        the existing module for this file): construct a synthetic wide-bucket
        cagg scenario (e.g. `bucket_width` a year, raw edge lagging the cagg's
        materialized bucket by less than one bucket width) and assert the
        generic verdict reports `is_fresh=True` — proving the floor exists
  - [x] A second assertion shows the same scenario reporting stale when the
        lag exceeds one bucket width — the floor is a boundary, not a blanket
        `False`
  - [x] Test docstring states plainly that a passing test here means "the
        limitation is present and expected," not "coverage caggs are fresh"
  - [x] Success: tests pass; changing `_raw_max`'s bucket-alignment step (e.g.
        removing it) makes the first assertion fail — verify this by hand
        during development, not as a permanent test mutation
  - [x] Effort: 2

---

## Task 4 — Coverage content-edge freshness check

- [x] Add `CONTENT_EDGE_TOO_OLD` to `StalenessSignal` (D6)
  - [x] New `StrEnum` member; docstring states it fires only from
        `check_coverage_freshness`, never from the generic `assert_cagg_fresh`
        evaluation
  - [x] Success: importable; existing signal members unchanged
  - [x] Effort: 1

- [x] Add the content-edge assertion to `check_coverage_freshness` (D6)
  - [x] For each coverage view, one bounded probe: `max(last_bucket)` on the
        cagg vs `max(time)` on `COVERAGE_SOURCE_TABLE[view_name]`, no bucket
        alignment — reuse the connection already open, inside the existing
        verdict cache (do not add a second cache layer)
  - [x] Lag exceeding `COVERAGE_CONTENT_STALENESS` appends
        `StalenessSignal.CONTENT_EDGE_TOO_OLD` to that cagg's verdict signals;
        `is_fresh` becomes `False` if it fires even when the generic bucket
        check reports fresh
  - [x] The probe uses the module's existing statement-timeout discipline
        (`_set_probe_timeout` / `_restore_probe_timeout` pattern) — no
        unbounded query added
  - [x] `CoverageFreshness.describe()` needs no change if `FreshnessVerdict`
        already carries the new signal in its `signals` tuple — confirm this
        rather than editing `describe()` speculatively
  - [x] Success: `check_coverage_freshness` return type and cache contract
        unchanged; `GET /api/v1/symbols/{symbol}` (Task 9) does not call this
        function at all — confirm no accidental new dependency
  - [x] Effort: 3

- [x] Test the content-edge check (success criterion 4)
  - [x] Against an ephemeral DB fixture whose coverage cagg tracks its source
        (fresh) — `check_coverage_freshness` reports fresh, no
        `CONTENT_EDGE_TOO_OLD`
  - [x] Against a fixture where the coverage cagg's `last_bucket` is pinned
        stale relative to a freshly-inserted raw row beyond
        `COVERAGE_CONTENT_STALENESS` — reports stale, `CONTENT_EDGE_TOO_OLD`
        present, and the generic bucket-lag signal is *not* what caught it
        (assert the scenario is constructed so bucket-lag alone would report
        fresh — otherwise the test doesn't isolate the new check)
  - [x] Success: tests pass; both scenarios use the project's existing
        ephemeral-DB fixture convention, no new fixture invented
  - [x] Effort: 2

---

## Task 5 — `create_app(db_url=...)` seam

- [x] Add the optional parameter (D9)
  - [x] `create_app(db_url: str | None = None) -> FastAPI` in `app.py`;
        `lifespan` reads `db_url` via closure (or an equivalent seam) — when
        `None`, behavior is unchanged: `Settings().timescale_db_url` is read
        exactly as today
  - [x] No route module changes; this is app-factory-only
  - [x] Success: `create_app()` with no argument is behavior-identical to
        today; `create_app(db_url="postgresql://...")` uses that URL instead of
        `Settings()`
  - [x] Effort: 2

- [x] Test the seam
  - [x] `create_app(db_url=...)` with a fake/unreachable URL fails at lifespan
        startup the same way a bad `MT_TIMESCALE_DB_URL` would (no swallowed
        error, no silent fallback to `Settings()`)
  - [x] `create_app()` with `MT_TIMESCALE_DB_URL` unset still raises the
        existing `RuntimeError` — proves the seam didn't change the no-URL path
  - [x] Success: tests pass; no live DB required for either assertion
  - [x] Effort: 1

---

## Task 6 — `fetch_available_ranges`: the three bounded statements

- [x] Add the universe-edge read to `queries.py` (D3)
  - [x] SQL: universe-wide `MAX(last_bucket)` per coverage cagg, one round trip
        for both families via `UNION ALL`, family tagged with `CycleGranularity`
  - [x] `fetch_universe_edges(conn) -> dict[CycleGranularity, date | None]`
  - [x] Success: single statement, no per-symbol predicate; return type covers
        both `CycleGranularity` members even when a cagg has no rows
  - [x] Effort: 2

- [x] Add the per-symbol coverage read to `queries.py` (D2, statement B)
  - [x] SQL matches the design's statement B verbatim (D2): `UNION ALL` over
        `minute_coverage` and `daily_coverage`, tagged with `CycleGranularity`,
        `first_bucket`/`last_bucket` cast `::date`
  - [x] `fetch_symbol_coverage(conn, symbol) -> dict[CycleGranularity, tuple[date | None, date | None]]`
  - [x] Success: one round trip for both families; a symbol absent from both
        caggs returns an empty mapping (not an exception)
  - [x] Effort: 2

- [x] Add the bounded head-probe read to `queries.py` (D2 statement C, D3, D8)
  - [x] SQL matches the design's statement C: `UNION ALL` over
        `minute_5min_ohlcv` (D8 — not `minute_4hour_ohlcv`) and `daily_ohlcv`,
        each bounded by `time_bucket > %s` / `time > %s` using the universe
        edge for that family (Task 6's first read), tagged with
        `CycleGranularity`
  - [x] All four range expressions cast `AT TIME ZONE 'UTC'` before `::date`,
        explicitly — not relying on the pool's `configure` hook (D8)
  - [x] `fetch_symbol_head(conn, symbol, edges: dict[CycleGranularity, date | None]) -> dict[CycleGranularity, tuple[date | None, date | None]]`;
        a family with no universe edge is skipped (no unbounded fallback)
  - [x] Success: `EXPLAIN` shows chunk exclusion for both branches given a
        realistic bound; no branch of the UNION lacks a `time`/`time_bucket`
        predicate
  - [x] Effort: 3

- [x] Add the merge function (D2 — the `COALESCE` logic)
  - [x] `merge_available_ranges(coverage, head) -> dict[CycleGranularity, AvailableRange]`
        (or equivalent): `start = COALESCE(coverage_start, head_start)`,
        `end = COALESCE(head_end, coverage_end)`; family omitted when both
        `start` and `end` resolve to `None`
  - [x] Pure function — no I/O, no connection argument — so it is unit-testable
        without a database
  - [x] Success: importable independently of the three fetch functions
  - [x] Effort: 2

---

## Task 7 — Unit tests: merge logic and every coverage/head combination

- [x] Test `merge_available_ranges` against all four D2 cases
  - [x] Data spanning the edge: coverage start + head end
  - [x] Data entirely before the edge: head returns `(None, None)`, coverage
        supplies both
  - [x] Data entirely after the edge: no coverage row, head supplies both
  - [x] No data in a family: both `None`, family omitted from the result dict
  - [x] Success: tests pass with no database; each case is its own parametrized
        entry so a future regression names the specific case that broke
  - [x] Effort: 2

- [ ] Test the three fetch functions against a fixture DB
  - [ ] `test/unit/api_server/test_symbols.py` (or a new `test_queries.py`):
        seed a small ephemeral DB with coverage rows and raw bars covering the
        four D2 cases plus a symbol present in neither cagg
  - [ ] Assert each fetch function's SQL shape produces the expected merged
        result via `merge_available_ranges` — this is the seam Task 9 will call
  - [ ] Success: tests pass against the project's ephemeral-DB fixture
  - [ ] Effort: 2

---

## Task 8 — Universe-edge cache on `app.state`

- [x] Cache `fetch_universe_edges` under `CAGG_FRESHNESS_CACHE_TTL` (D3)
  - [x] Lifespan or a small cache helper on `app.state`: TTL-based, same
        constant slice 168 already defined (`CAGG_FRESHNESS_CACHE_TTL`) — do
        not introduce a second TTL constant for the same 60 s policy
  - [x] Docstring explains the reuse: coverage refresh policies fire hourly, so
        60 s cannot mask an edge movement, and it's already the project's
        answer to "how long may a cagg-derived fact be cached"
  - [x] Add a `deps.py` accessor (matching the `get_max_bars` precedent — a
        thin function reading a cached value off `app.state`) rather than
        reading `app.state` directly from `symbols.py`; cache miss/expiry
        triggers exactly one `fetch_universe_edges` call, not one per family
  - [x] Success: repeated calls within the TTL window issue no new query
        (assert with a connection/cursor spy); a call after expiry re-queries
  - [x] Effort: 2

- [x] Test the cache
  - [x] Cold call populates the cache and returns the queried value
  - [x] Warm call within TTL returns the cached value with zero additional
        queries
  - [x] Call after TTL expiry re-queries (use a fake clock or monkeypatched TTL,
        consistent with how `cagg_freshness`'s own cache is tested)
  - [x] Success: tests pass; no live DB required beyond the fixture already
        used for `fetch_universe_edges`
  - [x] Effort: 2

---

## Task 9 — Rewire `get_symbol`: drop the gather, use the new read path

- [x] Replace the two lazy range queries in `symbols.py` (D2, D7)
  - [x] Remove `_MINUTE_RANGE_SQL`, `_DAILY_RANGE_SQL`, and the `asyncio.gather`
        dispatch; remove `_MINUTE_GRANULARITIES`/`_DAILY_GRANULARITIES` module
        constants if `CycleGranularity` supersedes their role in the new path
  - [x] `get_symbol` issues the instrument lookup, then the three new
        statements (universe edges from cache — Task 8; coverage — Task 6;
        head probe — Task 6) **sequentially inside a single
        `run_in_executor` call**, one thread, one connection checkout — not
        three separate `run_in_executor` dispatches (D7)
  - [x] `available` is built from `merge_available_ranges`'s result, mapped
        from `CycleGranularity` to the existing per-`Granularity` response keys
        (each family still fans out to its group of `Granularity` members, as
        today)
  - [x] Success: `symbols.py` issues no query without a `time`/`time_bucket`
        (or cagg-native) bound; `asyncio` import is removed if nothing else in
        the file uses it
  - [x] Effort: 3

- [x] Update/extend `test/unit/api_server/test_symbols.py`
  - [x] Existing tests for `get_symbol`'s response shape still pass unchanged
        (D2's equivalence claim — same shape, different computation)
  - [x] New assertion: the route issues exactly one `run_in_executor` dispatch
        for the ranges portion (or equivalent proof there is no
        `asyncio.gather` reintroduced) — the regression this task exists to
        prevent
  - [x] A symbol absent from both caggs and both raw tables still returns
        `available: {}`, not a 500 or a 404-via-empty-ranges
  - [x] Success: full test file passes; `ruff`/mypy clean on `symbols.py`
  - [x] Effort: 2

---

