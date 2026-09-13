---
docType: tasks
slice: api-surface-coverage-kalshi
project: trading-data
lld: user/slices/188-slice.api-surface-coverage-kalshi.md
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [186, 187, 262, 264, 265, 268]
interfaces: [189, 190, 907]
projectState: >
  Design 188 committed at 505a667; slice review PASS with two informational
  notes and no design changes. `src/manta_trading/api_server/` has zero Kalshi
  references and README line 567 says the API serves equity data only. The
  kalshi track (series, events, markets, trades, candlesticks, sync_state,
  market_candle_state) is populated in production: 345M trades, 240M candles,
  11.1M markets. Slices 186/187 established the error shape, the 404/200
  split, the 504 handler, the rows ceiling, the load tier and the
  `create_app(db_url=)` seam.
dateCreated: 20260912
dateUpdated: 20260913
status: not_started
---

## Context Summary

- Working on **188 API surface coverage: Kalshi** in seven sections:
  (1) the serialization extraction; (2) catalog readers; (3) catalog models
  and routes; (4) the tape-floor promotion and time-series readers;
  (5) time-series models and routes; (6) the load tier and its fixture;
  (7) documentation and the OpenAPI artifact.
- Source of truth: the slice design. Tasks cite its Technical Decisions
  (D1–D12), *API Specification*, *Testing Strategy* and Success Criteria
  (SC1–SC9). Read the cited section before starting each task.
- Section order matters: routes need readers and models; the load fixture
  needs both routes; `openapi.json` is regenerated last so it is written once
  against the final route set.
- Phase 6 branch: `188-slice.api-surface-coverage-kalshi`, forked from the
  integration target (`cf config get git.integration_branch`, `main` if
  empty). Verify before Task 1.1.
- **No migration and no new table.** Every column this slice reads already
  exists. If a task appears to need DDL, stop and raise it.
- Code the tasks touch, all read during breakdown:
  - `api_server/routes/bars.py` — the inline `format` branch at the end of
    `get_bars` (~line 225: `msgpack.packb(response.model_dump(), default=str)`
    / `orjson.dumps(response.model_dump())`) is what Section 1 extracts;
    `_admit_range` (~line 100) is the 422 message precedent.
  - `api_server/routes/symbols.py` — the `get_db` route shape, the executor
    bridge, the `_LIST_FILTERED_SQL` `ILIKE %s` prefix search, and the
    sequential-statements comment in `_fetch_ranges`.
  - `api_server/routes/status.py` — `_VALID_HEALTH` / `_resolve_health_filter`
    (~line 34–90): the enum-derived comma-separated filter with a 422 naming
    the valid set. The `status=` validator copies this shape.
  - `api_server/deps.py` — `get_db`, `get_db_pool`, `get_max_bars` (~line 45);
    `get_kalshi_trades_excluded` is written in the same three-line shape.
  - `api_server/app.py` — `lifespan` (~line 36) sets `max_bars_per_request`
    and `statement_timeout` on `app.state`; `create_app` (~line 105) has the
    `description` string and the five `include_router` calls; the three
    exception handlers (`HTTPException` → `{"error": …}`, `QueryCanceled` →
    504, `Exception` → 500) already cover the new routes.
  - `api_server/models/responses.py` (291 lines) — `GATEWAY_TIMEOUT_RESPONSE`
    and the model style; Kalshi models go in a new `models/kalshi.py` (D12).
  - `data/kalshi/status.py` and `historical_status.py` — the sync-reader
    discipline the new modules copy: frozen dataclasses, `psycopg.Connection`,
    no client/transport/config import.
  - `data/kalshi/trade_status.py` — `_effective_floor` (~line 140) is promoted
    to `effective_tape_floor`; `STATE_QUERY` (~line 95) reads
    `last_full_sync_at, watermark_ts, coverage_from_ts` from
    `kalshi.sync_state`; `read_trade_status` (~line 152) is its only caller.
  - `data/kalshi/selection.py` — `trades_filter_sql` (~line 151) returns a
    `Selection(predicate, params)` over the `s.category` test and renders
    literal `FALSE` for an empty set; `CATALOG_TABLES` (~line 188) is the
    markets→events→series join with aliases `m`/`e`/`s`. Never re-spelled.
  - `data/kalshi/constants.py` — `MarketStatus` (~line 104, the served
    vocabulary; **not** `MarketStatusFilter` at ~135), `CandlePeriod` (~158),
    `Surface` (~170), `COLLECTED_CANDLE_PERIOD` (~246).
  - `data/kalshi/candle_repository.py` — `CANDLE_COLUMNS` (~line 50): the
    fourteen flat column names paired with their nested
    `(object, field)` path. This mapping is what Section 5 re-nests on the way
    out; it is not restated.
  - `data/kalshi/trade_repository.py` — `TRADE_COLUMNS` (~line 45): the nine
    stored trade columns.
  - `market/schema/migrations/kalshi.py` — the DDL the readers project from:
    `kalshi.series` (~line 107), `kalshi.events` (~125, index
    `events_series_ticker_idx`), `kalshi.markets` (~146, indexes
    `markets_event_ticker_idx` / `markets_status_idx`), `kalshi.sync_state`
    (~216, `coverage_from_ts` added at ~435), `kalshi.market_candle_state`
    (~249, `coverage_from_ts` added at ~350).
  - `constants.py` — `API_MAX_BARS_PER_REQUEST` (~line 1287) and its
    docstring, re-described in Section 7; `API_SERVING_SESSION` (~1268).
  - `config/__init__.py` — `kalshi_trades_excluded_categories` (~line 172),
    `api_max_bars_per_request` (~263).
  - `test/unit/api_server/test_symbols.py` — the unit pattern: `create_app()`,
    `TestClient`, `app.dependency_overrides[get_db]`, module-level
    monkeypatching of the reader functions.
  - `test/unit/api_server/test_openapi_artifact.py` and
    `scripts/dump_openapi.py` — the drift gate; `--check` needs no database.
  - `test/integration/kalshi_helpers.py` — `apply_kalshi_track`,
    `ensure_timescaledb`, `market_rows`, `write_catalog`; imported bare
    (`from kalshi_helpers import …`) by six integration modules and by
    `test/integration/conftest.py` (~lines 81, 89).
  - `test/load/test_187_api_nfr.py` and `test/load/conftest.py` — the gate
    (`MT_RUN_LOAD_TESTS=1`), `pytest.mark.timeout`, the `ephemeral_db`-derived
    fixtures, `httpx.ASGITransport` against `create_app(db_url=…)`.
- Tests: `python scripts/run_tests.py unit` and
  `python scripts/run_tests.py integration` run separately; integration needs
  `MT_TIMESCALE_TEST_URL` exported. The load tier never reads the production
  URL — `create_app(db_url=…)` is the only seam.
- Commit at least once per section; every checkpoint leaves the working tree
  importable and the unit tier green. Scope `ruff format` to touched files and
  check `git diff` for unrelated rewrites before each commit.

## Section 1: Serialization extraction

Design *D7*, *Technical Scope* (`api_server/serialization.py`), *SC6*, *SC8*.

- [ ] **Task 1.1: Extract `timeseries_response` from `bars.py`** (effort: 2)
  - [ ] New module `api_server/serialization.py` with
        `timeseries_response(model: BaseModel, fmt: Literal["json", "msgpack"]) -> Response`.
        It dumps with `model.model_dump(mode="json")` and returns
        `orjson.dumps(...)` at `application/json` or `msgpack.packb(...)` at
        `application/x-msgpack`.
  - [ ] `mode="json"` is what renders `Decimal` as a string and `datetime` as
        ISO-8601 before either encoder sees the object; the msgpack call
        therefore carries **no** `default=str`. Say so in the docstring and
        cite D7.
  - [ ] The `fmt` literal type is defined once here and imported by the route
        modules, so the two accepted values exist in one place.
  - [ ] Success: the module imports nothing from `routes/`; `ruff` and `mypy`
        clean.
- [ ] **Task 1.2: Point `bars.py` at the helper** (effort: 1)
  - [ ] Replace the two-branch `Response` construction at the end of
        `get_bars` with a single `return timeseries_response(response, fmt)`.
        Remove the now-unused `msgpack`, `orjson` and `Response`-construction
        imports if nothing else in the module uses them.
  - [ ] Bars keep `float` for equity prices — this task changes *where* the
        response is built, never *what* it contains. `BarsResponse` is
        untouched.
  - [ ] Success: `test/unit/api_server/test_bars.py` passes **unmodified**
        (SC8). If a bars test needs editing, the extraction changed behavior;
        stop and fix the helper instead.
- [ ] **Task 1.3: Tests for the helper** (effort: 1)
  - [ ] New `test/unit/api_server/test_serialization.py`: a small model with a
        `Decimal`, an aware `datetime` and a `None` field. Assert the json
        branch has media type `application/json` and the Decimal decodes to
        the string `"0.4900"`; the msgpack branch has media type
        `application/x-msgpack` and `msgpack.unpackb` yields the same string;
        the aware datetime is ISO-8601 with a UTC offset on both branches;
        `None` survives as null on both.
  - [ ] Success: the new file and `test_bars.py` both pass in the unit tier.
        Commit (section checkpoint).

## Section 2: Catalog readers

Design *D2*, *D8*, *D9*, *Technical Scope* (`data/kalshi/serve_catalog.py`).

- [ ] **Task 2.1: Catalog record dataclasses** (effort: 2)
  - [ ] New module `data/kalshi/serve_catalog.py`. Frozen dataclasses
        `SeriesRow`, `EventRow`, `MarketRow` whose fields are exactly the
        typed columns listed for each record in the design's *API
        Specification*, in DDL order. `raw` appears in none of them (D2).
  - [ ] `MarketRow` is flat — one field per column. The lifecycle /
        settlement / economics grouping is a response-model concern
        (Section 3), not the reader's.
  - [ ] Each dataclass has a module-level `_SELECT` tuple of its column names
        used to build both the projection and the row mapper, so a column is
        named once per record type.
  - [ ] Success: the module imports only `dataclasses`, `datetime`, `decimal`,
        `psycopg` and `manta_trading.data.kalshi.constants`. No client, no
        transport, no config (D9 / the `status.py` discipline).
- [ ] **Task 2.2: Seek functions** (effort: 2)
  - [ ] `fetch_series(conn, ticker) -> SeriesRow | None`,
        `fetch_event(conn, event_ticker) -> EventRow | None`,
        `fetch_market(conn, ticker) -> MarketRow | None`. Each is one
        primary-key `SELECT` with a `%s` parameter, returning `None` when the
        row is absent.
  - [ ] `None` means "no such row" and nothing else. No exception is caught
        inside these functions: a `psycopg.Error` propagates so a failed seek
        can never be reported as a 404 (D10).
  - [ ] Success: three functions, one statement each, all parameterised.
- [ ] **Task 2.2a: Category listing reader** (effort: 1)
  - [ ] In `serve_catalog.py`, a frozen `CategoryCount` (`category`,
        `series_count`) and `fetch_categories(conn) -> list[CategoryCount]`:
        one `SELECT category, count(*) FROM kalshi.series GROUP BY category
        ORDER BY category`.
  - [ ] No count guard and no filter parameters: the result is bounded by the
        number of distinct categories (20 on production 2026-09-13) and the
        aggregate measured 15 ms over the whole table.
  - [ ] `category` is free text Kalshi assigns, not an enum in this codebase —
        this reader is the only way a client can learn what `category=`
        accepts. Say so in the docstring and cite D2.
  - [ ] Success: one statement, ordered by `category` so the response is
        stable between calls.
- [ ] **Task 2.3: Scoped list functions with the count guard** (effort: 3)
  - [ ] `count_series(conn, *, category, search) -> int` and
        `fetch_series_list(conn, *, category, search) -> list[SeriesRow]`;
        `count_events(conn, series_ticker, *, strike_from, strike_to)` and
        `fetch_events(...)`; `count_markets(conn, event_ticker, *, statuses)`
        and `fetch_markets(...)`. Count and fetch share one predicate builder
        per resource so the guard and the read can never diverge.
  - [ ] `search` is a ticker prefix rendered `ILIKE %s` with `search + "%"`,
        the `symbols.py::_LIST_FILTERED_SQL` spelling. `category` is an exact
        match. `strike_from`/`strike_to` are inclusive bounds on
        `strike_date`, bound as `timestamptz` (D4's binding rule applies to
        every hypertable-adjacent predicate; `events.strike_date` is
        `TIMESTAMPTZ`). `statuses` is a sequence matched with `= ANY(%s)`.
  - [ ] Lists are ordered deterministically: series by `ticker`, events by
        `event_ticker`, markets by `ticker`.
  - [ ] Success: every list function has a matching count function over the
        identical `WHERE` clause; no `LIMIT` anywhere in the module.
- [ ] **Task 2.4: Integration tests for the catalog readers** (effort: 3)
  - [ ] New `test/integration/test_kalshi_serving.py` on the `kalshi_db`
        fixture, seeded through `kalshi_helpers.write_catalog` so rows are the
        recorded served shapes.
  - [ ] Assert: `fetch_categories` returns one row per distinct category with
        the right series count, ordered by category, and every value it
        returns is accepted by the series list's `category=` filter — the two
        must agree, because the first exists to feed the second.
  - [ ] Assert: each seek returns the row and an unknown ticker returns
        `None`; the series list honors `category` and the `search` prefix
        (including a prefix matching nothing → empty list, not an error); the
        events list is scoped to its series and respects both strike bounds
        inclusively; the markets list is scoped to its event and filters on a
        multi-value `statuses`; each count equals `len()` of its fetch for
        every filter combination exercised.
  - [ ] Success: the file passes in the integration tier. Commit (section
        checkpoint).

## Section 3: Catalog models and routes

Design *D1*, *D2*, *D3*, *D6*, *D8*, *D10*, *API Specification* (Catalog),
*SC1*, *SC3*.

- [ ] **Task 3.1: `get_kalshi_trades_excluded` dependency and lifespan wiring** (effort: 2)
  - [ ] In `app.py`'s `lifespan`, next to `max_bars_per_request`, resolve
        `app.state.kalshi_trades_excluded = settings.kalshi_trades_excluded_categories`
        once, with a comment citing D5 and the 186 D9 pattern.
  - [ ] In `deps.py`, add `get_kalshi_trades_excluded(request) -> frozenset[str]`
        in the same three-line shape as `get_max_bars`.
  - [ ] Success: no route reads `Settings()` per request and no route touches
        `app.state` directly.
- [ ] **Task 3.2: Catalog response models** (effort: 3)
  - [ ] New `api_server/models/kalshi.py` with `SeriesRecord`, `EventRecord`,
        `MarketRecord`, plus the nested `MarketLifecycle`, `MarketSettlement`
        and `MarketEconomics` models and the list wrappers
        `CategoryListResponse`, `SeriesListResponse`, `EventListResponse`,
        `MarketListResponse` (each carrying its scope key and `count`, per the
        *API Specification*).
  - [ ] `CategoryListResponse` wraps `CategoryRecord` (`category`,
        `series_count`) and carries `count` — the number of categories, not
        the number of series.
  - [ ] `MarketRecord` exposes `settlement` on **every** market with its five
        fields null until settled (D3). Field names inside each nested object
        are the DB/Kalshi names verbatim.
  - [ ] Decimal-typed columns are typed `Decimal | None` on the models, not
        `float`, so D7's string rendering applies to the catalog too.
  - [ ] A classmethod per model converts the Section 2 dataclass to the model,
        so the flat-to-nested mapping lives in one place.
  - [ ] Success: `models/kalshi.py` is under ~300 lines and
        `models/responses.py` is unmodified.
- [ ] **Task 3.3: Tests for the catalog models** (effort: 2)
  - [ ] In a new `test/unit/api_server/test_kalshi_models.py`: a fully
        populated `MarketRow` maps to a `MarketRecord` with every column
        present exactly once across the flat fields and the three nested
        objects; an unsettled market has `settlement` present with five nulls;
        a `Decimal("0.4900")` field dumps to the string `"0.4900"` under
        `model_dump(mode="json")`.
  - [ ] A test that asserts the set of `MarketRow` field names equals the
        union of the names reachable in `MarketRecord`, so a column added to
        the reader cannot silently go unserved.
  - [ ] Success: the file passes in the unit tier.
- [ ] **Task 3.4: The `status=` filter validator** (effort: 2)
  - [ ] In `routes/kalshi_catalog.py`, a `_resolve_status_filter(status: str | None) -> list[str] | None`
        modelled on `status.py::_resolve_health_filter`: comma-separated,
        stripped, each token checked against `MarketStatus`, `None` when the
        parameter is omitted (no filter).
  - [ ] A token outside the enum, or a present-but-empty value, raises
        `HTTPException(422)` with the message naming the invalid tokens and
        the valid set. The valid set is derived from `MarketStatus` at module
        scope and never restated. Use `MarketStatus`, not
        `MarketStatusFilter` — the served vocabulary is what the column holds
        (D2).
  - [ ] Success: adding a member to `MarketStatus` extends the accepted set
        with no other edit.
- [ ] **Task 3.5: Catalog routes** (effort: 3)
  - [ ] New `routes/kalshi_catalog.py` with the seven routes of the *API
        Specification* under the prefix `/api/v1/kalshi`, all `GET`, all
        declaring `responses=GATEWAY_TIMEOUT_RESPONSE`, all depending on
        `get_db` (D9: the whole request is a few milliseconds).
  - [ ] Each route runs its statements sequentially inside one
        `loop.run_in_executor` call, the `symbols.py::_fetch_ranges` pattern,
        with the 187 D7 reason in a comment rather than the docstring.
  - [ ] A list route seeks its parent first: absent → `HTTPException(404)`
        with the `"<Resource> '<ticker>' not found"` message shape. Then the
        count: over `get_max_bars` → `HTTPException(422)` quoting the actual
        count and the live ceiling, never a literal (D8). Then the fetch.
  - [ ] Docstrings are written as public API descriptions — FastAPI publishes
        them (the 187 rule). Decision references go in comments.
  - [ ] Register the router in `create_app`.
  - [ ] `GET /categories` is the exception to the list shape: no parent to
        seek and no count guard (its row count is the number of distinct
        categories), so it is one aggregate → model → JSON.
  - [ ] Success: the seven routes appear in `create_app().openapi()`; no route
        function exceeds ~50 lines.
- [ ] **Task 3.6: Unit tests for the catalog routes** (effort: 3)
  - [ ] New `test/unit/api_server/test_kalshi_catalog.py` following
        `test_symbols.py`: `create_app()`, `TestClient`, `get_db` overridden
        with a sentinel, the `serve_catalog` functions monkeypatched on the
        route module.
  - [ ] One test per contract line: `GET /categories` returns 200 with the
        rows in category order and `count` equal to the number of categories
        (not the series total), and issues no count-guard call; each seek
        route returns 200 with the record and 404 with `{"error": "…"}` for an
        unknown ticker; a list
        route with an unknown parent is 404 **before** any count runs (assert
        the count fake was not called); a count over the ceiling is 422 with
        both numbers in the message and no fetch call; `status=active,finalized`
        reaches the reader as two values; `status=open` and `status=` are 422
        naming the valid set; a valid but empty result is 200 with `count: 0`
        (SC3).
  - [ ] Success: the file passes in the unit tier. Commit (section
        checkpoint).

## Section 4: Tape floor and time-series readers

Design *D4*, *D5*, *D9*, *D10*, *Interfaces required*, *SC4*, *SC5*.

- [ ] **Task 4.1: Promote `effective_tape_floor`** (effort: 1)
  - [ ] In `data/kalshi/trade_status.py`, rename `_effective_floor` to
        `effective_tape_floor`, keep the signature and body, and expand the
        docstring to say it is the one spelling of the floor for the CLI and
        the API (267 Decision 8). Update its call in `read_trade_status`.
  - [ ] Success: `grep -rn "_effective_floor" src/ test/` returns nothing.
- [ ] **Task 4.2: Tests for the promotion** (effort: 1)
  - [ ] Extend the existing trade-status integration coverage: with no
        historical `sync_state` row the function returns the live floor; with
        a historical row whose watermark is older it returns the watermark;
        with a newer one it still returns the live floor.
  - [ ] Success: `test/integration/test_kalshi_status.py` and the new
        assertions pass; `mt data kalshi status --json` output is unchanged.
- [ ] **Task 4.3: Market context reader** (effort: 3)
  - [ ] New module `data/kalshi/serve_timeseries.py` with a frozen
        `MarketContext` (`ticker`, `series_category`, `candle_collected`,
        `candle_coverage_from`, `candle_complete_through`) and
        `market_context(conn, ticker, *, period) -> MarketContext | None`.
  - [ ] One statement: `kalshi.markets` joined to `events` and `series` for
        the category, `LEFT JOIN kalshi.market_candle_state` on
        `(market_ticker, period)` for the three candle facts. `None` means
        unknown ticker (D6). A missing state row means
        `candle_collected=False` and both candle timestamps `None` — not an
        error (D5).
  - [ ] `period` is passed by the caller as `COLLECTED_CANDLE_PERIOD`; the
        constant is not read inside the reader.
  - [ ] Success: one statement, one round trip; unknown ticker and known
        ticker with no state row are distinguishable in the return value.
- [ ] **Task 4.4: Trade tape facts reader** (effort: 2)
  - [ ] In the same module, a frozen `TapeFacts`
        (`coverage_from`, `tape_complete_through`) and
        `tape_facts(conn) -> TapeFacts | None`: reads
        `kalshi.sync_state` for `Surface.TRADES` and resolves the floor
        through `effective_tape_floor`. `None` when the trades row is absent
        (D10: a fresh install, not an error).
  - [ ] A `tape_filtered(category, excluded) -> bool` helper that evaluates
        the same membership test `selection.trades_filter_sql` renders — call
        that function and evaluate its predicate, or share one comparison
        helper with it. The excluded-category test must not be re-spelled in
        Python (268 Decision 3).
  - [ ] Success: with `MT_KALSHI_TRADES_EXCLUDED_CATEGORIES` set to a
        category, the helper and the SQL predicate agree for a market in it
        and one outside it (asserted in Task 4.7).
- [ ] **Task 4.5: Window parsing and validation** (effort: 2)
  - [ ] A `resolve_window(start, end) -> tuple[datetime | None, datetime | None]`
        used by both time-series routes: a bare date means midnight UTC for
        `start` and the last instant of that day for `end` (the
        `bars._window_start_utc` / `_window_end_utc` convention, reused not
        re-derived); a naive datetime is read as UTC; both are optional and
        `None` means unbounded on that side.
  - [ ] `start > end` after resolution raises `HTTPException(422)` with the
        bars-style reversed-range message.
  - [ ] Where this lives: with the routes, not the readers — it raises
        `HTTPException`. Put it in `routes/kalshi_timeseries.py` or a small
        shared route helper; the readers take resolved `datetime | None`
        bounds only.
  - [ ] Success: bounds reach the readers as aware UTC datetimes or `None`,
        never as `date` objects (D4, 187 D8).
- [ ] **Task 4.6: Candle and trade count/fetch functions** (effort: 3)
  - [ ] `count_candles(conn, ticker, *, period, start, end) -> int` and
        `fetch_candles(...) -> list[CandleRow]`; `count_trades(conn, ticker, *, start, end) -> int`
        and `fetch_trades(...) -> list[TradeRow]`. Count and fetch share one
        predicate builder per resource.
  - [ ] Candles filter on `end_period_ts` and are ordered by it; trades filter
        on `created_time` and are ordered by `(created_time, trade_id)` so the
        order is total. Both bind bounds as `timestamptz`; omitted bounds emit
        no predicate rather than a sentinel date.
  - [ ] `CandleRow` carries `end_period_ts` plus the fourteen value columns
        **named from `candle_repository.CANDLE_COLUMNS`**, not retyped.
        `TradeRow` carries the nine columns of
        `trade_repository.TRADE_COLUMNS` minus `market_ticker`.
  - [ ] No `LIMIT` on either fetch (D4): the guard is the count, and a row
        inserted between the two makes the response one row over, never one
        short. State that in the module docstring.
  - [ ] Success: four functions; the column lists are imported, not
        duplicated; `mypy` clean.
- [ ] **Task 4.7: Integration tests for the time-series readers** (effort: 3)
  - [ ] Extend `test/integration/test_kalshi_serving.py` on `kalshi_db` with
        candle and trade rows written through the real repositories, using the
        recorded `candlesticks.json` fixture so the stored shape is the served
        shape (the project's parser-fixture rule).
  - [ ] Assert: `market_context` returns `None` for an unknown ticker,
        `collected=False` with null timestamps for a market with no state row,
        and the state row's `watermark_ts` / `coverage_from_ts` when one
        exists (SC5); `tape_facts` returns `None` with no trades state row and
        the effective floor when one exists (SC4); `tape_filtered` agrees with
        a direct SQL evaluation of `trades_filter_sql` for an excluded and a
        non-excluded category; counts equal `len()` of the fetches at the
        window boundary (a row exactly at `start`, one exactly at `end`, one
        just outside each); an unbounded request returns every stored row.
  - [ ] Success: the file passes in the integration tier. Commit (section
        checkpoint).


---

Sections 5–7 (time-series models and routes, the load tier, documentation and
the OpenAPI artifact) continue in
`188-tasks.api-surface-coverage-kalshi-2.md`.
