---
docType: tasks
slice: api-surface-coverage-kalshi
project: trading-data
lld: user/slices/188-slice.api-surface-coverage-kalshi.md
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [186, 187, 262, 264, 265, 268]
interfaces: [189, 190]
projectState: >
  Continuation of `188-tasks.api-surface-coverage-kalshi-1.md`. Sections 1–4
  are complete before this file starts: the serialization helper is extracted
  and bars uses it, the catalog readers and routes are in place, and
  `serve_timeseries.py` returns market context, tape facts and the candle and
  trade counts and rows.
dateCreated: 20260912
dateUpdated: 20260912
status: not_started
---

## Context Summary

- Part 2 of the 188 breakdown. Read
  `188-tasks.api-surface-coverage-kalshi-1.md` first: its Context Summary
  carries the code map, the branch rule, the test-tier commands and the
  no-migration constraint, and none of that is repeated here.
- Remaining sections: (5) time-series models and routes; (6) the load tier and
  its fixture; (7) documentation and the OpenAPI artifact.
- Entering Section 5, these exist and are green:
  `api_server/serialization.py::timeseries_response`;
  `data/kalshi/serve_catalog.py`; `routes/kalshi_catalog.py` with its six
  routes registered; `api_server/models/kalshi.py` with the catalog models;
  `data/kalshi/trade_status.py::effective_tape_floor` (public);
  `data/kalshi/serve_timeseries.py` with `market_context`, `tape_facts`,
  `tape_filtered`, and the candle and trade count/fetch pairs.
- Section 7 is last on purpose: `openapi.json` is regenerated once, against
  the final route set, so the drift test is not fought section by section.
- Commit at least once per section. Scope `ruff format` to touched files and
  check `git diff` for unrelated rewrites before each commit.

## Section 5: Time-series models and routes

Design *D4*, *D5*, *D6*, *D7*, *D10*, *API Specification* (Time series),
*SC2*, *SC3*, *SC6*.

- [ ] **Task 5.1: Time-series response models** (effort: 3)
  - [ ] In `api_server/models/kalshi.py`: `CandleRecord` with the nested
        `yes_bid` / `yes_ask` / `price` OHLC objects and flat `end_period_ts`,
        `volume_fp`, `open_interest_fp`; `TradeRecord` with the eight served
        trade fields; `CandlesResponse` (`market_ticker`, `period_minutes`,
        `collected`, `coverage_from`, `complete_through`, `count`,
        `candlesticks`) and `TradesResponse` (`market_ticker`,
        `coverage_from`, `tape_complete_through`, `tape_filtered`, `count`,
        `trades`).
  - [ ] The flat-to-nested candle mapping is driven by
        `candle_repository.CANDLE_COLUMNS`, whose second element is already
        the `(object, field)` path — walk it, do not restate it. Nulls are
        preserved: a period with only `price.previous_dollars` keeps every
        other field null rather than dropping the object.
  - [ ] All price and size fields are `Decimal | None`. `period_minutes` is
        `COLLECTED_CANDLE_PERIOD`, reported so a later slice can widen it.
  - [ ] Field descriptions state the D5 meanings — in particular that
        `complete_through` is "requested and stored through", not "newest
        stored candle", and that null `tape_complete_through` means the trades
        phase has never run.
  - [ ] Success: `models/kalshi.py` stays under ~300 lines; split into
        `models/kalshi_catalog.py` and `models/kalshi_timeseries.py` if it
        does not.
- [ ] **Task 5.2: Tests for the time-series models** (effort: 2)
  - [ ] In `test_kalshi_models.py`: a `CandleRow` with all fourteen values
        round-trips into the three nested objects with every value in its
        documented slot; a sparse row keeps its nulls; Decimals dump as
        strings under `mode="json"`; a `TradesResponse` with `count=0` and
        null facts serializes without error.
  - [ ] A test asserting the nested candle field set is exactly what
        `CANDLE_COLUMNS` describes, so a column added there cannot go
        unserved.
  - [ ] Success: the file passes in the unit tier.
- [ ] **Task 5.3: Time-series routes** (effort: 3)
  - [ ] New `routes/kalshi_timeseries.py` with
        `GET /api/v1/kalshi/markets/{ticker}/candlesticks` and
        `.../trades`, both taking optional `start`/`end` and
        `format=json|msgpack`, both declaring `GATEWAY_TIMEOUT_RESPONSE`.
  - [ ] Order per the design's *Data Flow*: resolve the window (422 on a
        reversed range, before any DB work) → one executor call that, inside a
        single `pool.connection()` from `get_db_pool`, runs
        `market_context` (None → 404), the count, and the fetch only if the
        count is within `get_max_bars` → build the model → return
        `timeseries_response(model, fmt)`.
  - [ ] The checkout is scoped to seek + count + fetch and released before
        serialization (185 D8a); the over-ceiling `HTTPException(422)` is
        raised after the executor call returns, so the connection is not held
        while unwinding. The 422 message quotes the actual count and the live
        ceiling.
  - [ ] The trades route resolves `tape_filtered` from the context's series
        category and the `get_kalshi_trades_excluded` dependency; absent
        `tape_facts` yields nulls and a 200 (D10).
  - [ ] Register the router in `create_app`.
  - [ ] Success: both routes appear in the schema; each function is under ~50
        lines with the reasoning in comments and a public-facing docstring.
- [ ] **Task 5.4: Unit tests for the time-series routes** (effort: 3)
  - [ ] New `test/unit/api_server/test_kalshi_timeseries.py` with the reader
        functions monkeypatched.
  - [ ] One test per contract line: unknown ticker → 404 with no count call;
        count over the ceiling → 422 quoting both numbers with no fetch call
        (SC2); known market, empty window → 200 `count: 0` with the D5 facts
        populated (SC3); all four meanings of zero distinguishable from the
        response body alone (past the watermark, `tape_filtered: true`,
        `collected: false`, and genuinely no activity); `start > end` → 422;
        a bare `start=2026-06-01` becomes midnight UTC and a bare `end`
        becomes that day's last instant; a naive datetime is read as UTC;
        omitted bounds reach the reader as `None`; `format=msgpack` returns
        `application/x-msgpack` and decodes with Decimal fields as strings
        (SC6); absent trades state → 200 with null facts.
  - [ ] Success: the file passes in the unit tier. Commit (section
        checkpoint).

## Section 6: Load tier

Design *D11*, *SC2*, *SC7*.

- [ ] **Task 6.1: Move `apply_kalshi_track` to a shared location** (effort: 2)
  - [ ] Create `test/kalshi_support/` (with `__init__.py`) and move
        `ensure_timescaledb` and `apply_kalshi_track` there from
        `test/integration/kalshi_helpers.py`. Leave the fixture row builders
        (`market_rows`, `write_catalog`, `column`, …) where they are — only
        the schema helpers move, because only they are needed by both tiers.
  - [ ] Update `test/integration/conftest.py` (~lines 81, 89) and any other
        importer. Confirm the new package is importable from both tiers'
        `sys.path` configuration before relying on it.
  - [ ] Success: `python scripts/run_tests.py integration` passes with no
        import errors; `grep -rn "kalshi_helpers import.*apply_kalshi_track\|ensure_timescaledb"`
        shows only the new path.
- [ ] **Task 6.2: `kalshi_dense_db` fixture** (effort: 3)
  - [ ] In `test/load/conftest.py`, a `kalshi_dense_db(ephemeral_db)` fixture
        that applies the kalshi track and seeds with `COPY`: one series with
        25,000 events (above the measured 24,382 maximum), one of those events
        with 450 markets, and one market with `ceiling + 1,000` trades and
        `ceiling + 1,000` candles, where `ceiling` is
        `API_MAX_BARS_PER_REQUEST`.
  - [ ] Also seed the `kalshi.sync_state` trades row and the
        `kalshi.market_candle_state` row the D5 facts read, so the responses
        under measurement are the real shape and not a stripped one.
  - [ ] Never reads `MT_TIMESCALE_DB_URL`; derives everything from
        `ephemeral_db`.
  - [ ] Success: the fixture builds in a tolerable time and
        `test_load_tier_never_references_prod_db_url` still passes.
- [ ] **Task 6.3: Load assertions** (effort: 3)
  - [ ] New `test/load/test_188_kalshi_api_nfr.py`, gated on
        `MT_RUN_LOAD_TESTS=1` with a `pytest.mark.timeout` sized like
        `test_187_api_nfr.py`, driving `create_app(db_url=…)` through
        `httpx.ASGITransport`.
  - [ ] The four D11 assertions: (1) a windowed trades request admitting
        exactly the ceiling completes end to end; (2) the unbounded request
        over `ceiling + 1,000` rows returns 422 and issues **no row fetch** —
        prove it without faking the reader, via a statement counter on the
        connection or a `log_statement` capture, and record which mechanism
        was chosen in the module docstring; (3) the 25,000-row events list
        completes; (4) 16 concurrent market-detail requests against the
        pool of 8 all complete within the queueing factor of the
        single-request bound.
  - [ ] Every bound is **measured first and then written down**, with the
        measured figure in a comment next to it. The design's provisional
        numbers (15 s, 500 ms, 2 s) are starting points to check against, not
        values to commit unverified.
  - [ ] Record the byte size of a ceiling-sized candle response next to 186's
        11.58 MB bars figure (D8's open question).
  - [ ] Success: `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_188_kalshi_api_nfr.py`
        passes (SC7). Commit (section checkpoint).

## Section 7: Documentation and the OpenAPI artifact

Design *D8*, *D12*, *SC1*, *SC9*.

- [ ] **Task 7.1: Re-describe the rows ceiling** (effort: 1)
  - [ ] Update the `API_MAX_BARS_PER_REQUEST` docstring in `constants.py`
        (~line 1287): it bounds rows per response — equity bars, Kalshi
        candles and trades, and scoped Kalshi catalog lists. Note that the
        Kalshi paths enforce it with an exact count rather than a window
        estimate, and why (D4). The env-var name keeps its historical `BARS`;
        say so explicitly so no one renames it later.
  - [ ] Update both README rows for `MT_API_MAX_BARS_PER_REQUEST` (~lines 154
        and 687) to match.
  - [ ] Success: no second setting and no rename; `grep -rn "MAX_BARS" src/`
        shows one constant.
- [ ] **Task 7.2: README endpoint list** (effort: 2)
  - [ ] Remove the "The API serves equity data only…" sentence (~line 567).
        Add the nine Kalshi routes to the endpoint list with a one-line
        description each, leading with `GET /api/v1/kalshi/categories` as the
        discovery entry point, and a short paragraph giving the D5 field
        meanings:
        what `collected`, `coverage_from`, `complete_through`,
        `tape_complete_through` and `tape_filtered` each tell a client, and
        the four readings of `count: 0`.
  - [ ] Note the range policy in the same place: no pagination, an exact
        count guard, 422 above the ceiling with the actual count.
  - [ ] Success: the README no longer claims equities-only anywhere;
        `grep -n "equity data only" README.md` is empty.
- [ ] **Task 7.3: App description and architecture documents** (effort: 2)
  - [ ] `create_app`'s `description` names Kalshi alongside bars, symbols and
        gaps (190 replaces it with the real reference later).
  - [ ] `180-arch.data-serving.md`: an Endpoints subsection for the Kalshi
        namespace pointing at this slice, and a Range Policy note that the
        Kalshi paths use a count-first guard where bars use a window estimate,
        with the measured reason.
  - [ ] `180-slices.data-serving-api.md`: confirm entry 8 links this slice and
        that *Future work* carries the cross-cutting markets list and derived
        candle periods (both were added at design time — verify, do not
        duplicate).
  - [ ] Success: the two 180 documents and the app description agree with the
        shipped routes.
- [ ] **Task 7.4: Regenerate the OpenAPI artifact** (effort: 1)
  - [ ] Run `uv run python scripts/dump_openapi.py` and commit the result.
  - [ ] Diff the artifact: the nine new paths appear, and the entries for
        `/api/v1/bars/{symbol}`, `/api/v1/symbols`, `/api/v1/symbols/{symbol}`,
        `/api/v1/gaps/{symbol}`, `/api/v1/status` and `/api/v1/health` are
        **byte-identical** to the previous version (SC1). A diff in an
        equities path means the Section 1 extraction changed a response shape;
        stop and fix that rather than accepting the artifact.
  - [ ] Success: `uv run python scripts/dump_openapi.py --check` exits 0 and
        `test_openapi_artifact.py` passes.
- [ ] **Task 7.5: Full verification pass** (effort: 2)
  - [ ] Run the design's *Verification Walkthrough* steps 1–8 against
        production read-only with `mt serve` on the host, substituting current
        tickers for any that have aged out. Record the observed counts,
        because the design's figures (132,552 trades; 68,663 candles) will
        have moved.
  - [ ] Run `python scripts/run_tests.py unit`, then
        `python scripts/run_tests.py integration`, then the load tier with its
        gate. Run `mypy` over the touched source and test paths in one
        invocation. Scope `ruff format` to touched files and check
        `git diff main` for deletions before committing.
  - [ ] Walk Success Criteria 1–9 and record the evidence for each.
  - [ ] Success: all three tiers pass, the walkthrough's expectations hold,
        and every success criterion has a named piece of evidence. Commit
        (section checkpoint).
