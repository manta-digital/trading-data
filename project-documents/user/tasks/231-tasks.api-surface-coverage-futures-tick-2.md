---
docType: tasks
slice: api-surface-coverage-futures-tick
project: trading-data
lld: user/slices/231-slice.api-surface-coverage-futures-tick.md
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [229, 230]
projectState: >
  Part 1 complete: the tick pool and its 503, the response models, and the
  catalog routes (products, contracts, schedule).
dateCreated: 20261012
dateUpdated: 20261012
status: not_started
---

# Tasks: API Surface Coverage — Futures Tick (part 2 of 2)

Part 2: the four time-series routes, the health/status/overview additions,
the documents, the load tier and the closing checks. The Context Summary in
part 1 (`231-tasks.api-surface-coverage-futures-tick-1.md`) applies here:
paths, test environment, lint and production safety.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 6 - Time-series routes (TD3, TD4, TD5, TD6)

- [ ] **6.1 Add the ceiling refusal to `admission.py`** (effort 1)
  - [ ] `over_ceiling(max_rows: int, *, remedy: str) -> HTTPException`
        returns the `422` "the request matches more than the {max_rows:,}
        row limit; {remedy}", in the existing refusal's style.
  - [ ] Add `NARROW_TICKS = "narrow start/end or from/until"` beside
        `NARROW_WINDOW`; bars use `NARROW_WINDOW`.
  - [ ] Success: `test_admission.py` gains a case for the new helper;
        passes.
- [ ] **6.2 Build the shared read helper** (effort 4)
  - [ ] In `routes/futures_series.py`, or in `api_server/futures_read.py`
        if the route file would pass ~300 lines, write one async function
        that takes a pooled connection, a plan builder, a row reader
        (ticks or bars) and the ceiling, and returns `(plan, rows)`. It:
    - runs on the connection in one `REPEATABLE READ READ ONLY`
      transaction (TD6), using the pattern `cli/commands/tick_get_cmds.py`
      uses;
    - builds the plan (`contract_plan` or `product_plan`, with
      `(app.state.tick_url, app.state.calendar_url)` for products);
    - for ticks, clips each `TickWindow` to `[from, until)`:
      `open_ns = max(open_ns, from_ns)`,
      `close_ns = min(close_ns, until_ns − 1)`, dropping windows whose
      clipped `open_ns > close_ns` (TD3);
    - reads each window in order, through an `on_batch` collector that
      appends rows and raises `RowCeilingExceeded` once the total passes
      the ceiling (TD4).
  - [ ] **Error mapping**, in one place (a small function the routes
        call):
    - `ContractNotFound` → `404`;
    - `ContractAmbiguous`, `RollRangeTooLong` and other
      `TickPreflightError` → `422`;
    - `RowCeilingExceeded` → 6.1's `422`, raised after the connection is
      released;
    - outages → `TickServiceUnavailable(UNREACHABLE)`;
    - `QueryCanceled` propagates to `504`.
  - [ ] `from` must be before `until`; otherwise `422`. A naive datetime is
        read as UTC, as in `windows.py`.
  - [ ] Success: no route repeats this logic; nothing in the helper formats
        a price or a time (the models do).
- [ ] **6.3 Test the read helper** (effort 3)
  - [ ] Unit tests for clipping on synthetic windows:
    - a `from` inside a window;
    - an `until` exactly at a window's open (the window is dropped);
    - a window fully outside the bounds;
    - adjacent `[a, b)` and `[b, c)` bounds that partition a window with no
      overlap.
  - [ ] Integration tests (seed with `seed_ticks`, about 30 ticks across one
        session, and patch the ceiling to 10 through
        `app.dependency_overrides[get_max_bars]`):
    - over the ceiling → `422`, and a spy on the collector shows at most
      ceiling + `TICK_GET_FETCH_ROWS` rows read;
    - consecutive `[from, until)` pages over the session return exactly
      the session's ledger `record_count`, with no duplicate
      `(ts_event, sequence)` (FR6).
  - [ ] Success: passes.
- [ ] **6.4 Add the contract ticks route** (effort 2)
  - [ ] `GET /api/v1/futures/contracts/{symbol}/ticks?start=&end=&from=&until=&format=`:
    - `response_class=Response`,
      `responses=timeseries_200(FuturesTicksResponse) |
      TICK_UNAVAILABLE_RESPONSE | GATEWAY_TIMEOUT_RESPONSE`;
    - it depends on `tick_connection` and `get_max_bars`;
    - it serializes through `timeseries_response`.
  - [ ] Register `futures_series`'s router in `create_app`.
  - [ ] Success: route in the schema with its `200` model, for both media
        types.
- [ ] **6.5 Test the contract ticks route** (effort 3)
  - [ ] Integration tests:
    - field-for-field equality with the CLI's JSON writer over the same
      seed and bounds (FR1). Call the `get` writer path in-process and
      compare parsed JSON rows;
    - `format=msgpack` decodes to the same body;
    - `ZZZ9` → `404`;
    - an ambiguous symbol (seed two instruments with one raw symbol a
      decade apart) → `422` naming both;
    - an empty range → `422`;
    - the closed-port fixture → `503`.
  - [ ] Success: passes.
- [ ] **6.6 Add the contract bars route** (effort 2)
  - [ ] `GET /api/v1/futures/contracts/{symbol}/bars?granularity=&start=&end=&format=`:
    - `granularity` is restricted to `TICK_BAR_GRANULARITIES` (1.1 found
      how) and required;
    - no `from`/`until` (TD3);
    - bars models and the read helper.
  - [ ] Success: the schema lists exactly the tick bar sizes.
- [ ] **6.7 Test the contract bars route** (effort 2)
  - [ ] Integration tests:
    - `1m` and `1d` equal the CLI's JSON bars over the same seed;
    - `granularity=2h`, or a size outside the tuple → `422`.
  - [ ] Success: passes.
- [ ] **6.8 Add the product ticks and product bars routes** (effort 3)
  - [ ] `GET /api/v1/futures/products/{product}/ticks` and
        `GET /api/v1/futures/products/{product}/bars`:
    - the same as 6.4 and 6.6, plus `rule`, parsed as in 5.7;
    - an unknown product → `404`;
    - the plan is `product_plan`.
  - [ ] `target` carries `kind: "product"`, `rule` and `is_default`;
        `segments`, `unresolved` and `incomplete` come straight from the
        plan.
  - [ ] Success: the four time-series routes share the 6.2 helper and the
        6.2 error mapping; none has its own `try`.
- [ ] **6.9 Test the product routes** (effort 3)
  - [ ] Integration tests over `seed_september_roll`:
    - `1d` bars across the roll: `segments` name ESU4 then ESZ4 under
      `v.0`, `is_default` is true, and the bars equal the CLI's (FR2);
    - a range starting at the first held session lists those sessions in
      `unresolved`, with HTTP 200 (FR3);
    - a session seeded `partial` appears in `incomplete` (FR3);
    - `rule=c.0` changes the segments;
    - `rule=bad` → `422`;
    - a range over 1,300 sessions → `422` (FR4).
  - [ ] Success: passes.
- [ ] **6.10 Commit** (effort 1)
  - [ ] Unit and integration tiers for the touched directories; mypy; ruff;
        `git diff main` checked.
  - [ ] Commit: `feat: add futures ticks and bars routes`.

## Section 7 - Health, status and overview (TD7)

- [ ] **7.1 Add `tick_db` to `/api/v1/health`** (effort 2)
  - [ ] `HealthResponse` gains `tick_db: TickLineState` and
        `tick_detail: str | None`. The detail is the `TickUnavailable`
        detail, dropped by `exclude_none` when the state is ok.
  - [ ] Make the handler `async`:
    - the existing `SELECT 1` and freshness probe run in
      `run_in_executor` on a pooled connection (keep `get_db_pool`
      semantics);
    - `probe_tick(app)` is awaited;
    - HTTP is 200 in every case.
  - [ ] Success: the existing `test_health.py` passes after only fixture
        changes; record any it needed in the commit message.
- [ ] **7.2 Test health** (effort 2)
  - [ ] Integration tests:
    - `tick_db: "ok"` with no `tick_detail`;
    - `not_configured`, `unreachable` and `migrations_pending` through the
      3.1 fixtures and 3.3's ledger-row trick, each with HTTP 200 and the
      matching detail;
    - a primary database failure still reports `db: "error"`, as before.
  - [ ] Success: passes.
- [ ] **7.3 Add the `tick` block to `/api/v1/status`** (effort 2)
  - [ ] `StatusResponse.tick: TickBlock`, always present.
  - [ ] The route probes the tick pool:
    - on success it runs `accounting_lines(conn, calendar_url,
      TICK_UNIVERSE)`, giving `state: "ok"`;
    - a probe failure, or an exception from the read classified through
      2.1's `classify`, gives that state and detail with `products: []`.

    The equities rows and filters are unchanged.
  - [ ] Success: no `5xx` can come from the tick block.
- [ ] **7.4 Test status** (effort 2)
  - [ ] Integration tests:
    - the `tick` block equals `mt data accounting --json`'s `tick` over
      the same seed (FR8, Integration Requirements), computed in-process;
    - with `symbol=AAPL&health=OK` the block is unchanged;
    - closed port → `tick.state == "unreachable"`, HTTP 200, equities rows
      present.
  - [ ] Success: passes; existing `test_status.py` passes.
- [ ] **7.5 Add the `futures ticks` source to `/api/v1/overview`**
      (effort 2)
  - [ ] `SourceRecord` gains `unavailable: str | None`. The existing four
        sources map `unavailable=None` explicitly.
  - [ ] The route probes the tick pool and appends
        `SourceRecord(TICK_SOURCE_NAME, newest, unavailable)`:
    - `newest` comes from `newest_event_ns` through `utc_from_ns`;
    - on failure, `newest` is null and `unavailable` is
      `unavailable_text(...)`.
  - [ ] Success: `gather_db_facts` is untouched; the order of the sources
        matches the CLI's.
- [ ] **7.6 Test overview** (effort 2)
  - [ ] Integration tests:
    - the record equals `mt data overview --json`'s `futures ticks`
      source (name, `newest`, `unavailable`) over the same seed;
    - not configured → `unavailable` carries the not-configured text and
      HTTP 200;
    - the four existing sources keep their values with
      `unavailable: null`.
  - [ ] Update `test_operations_models.py` and
        `test_openapi_operations_schema.py` for the new field.
  - [ ] Success: passes.
- [ ] **7.7 Commit** (effort 1)
  - [ ] Unit and integration tiers for the touched directories; mypy; ruff;
        `git diff main` checked.
  - [ ] Commit: `feat: add tick lines to health, status and overview`.

## Section 8 - Documents (the 190 gate)

- [ ] **8.1 Regenerate `openapi.json`** (effort 1)
  - [ ] Run `uv run python scripts/dump_openapi.py`, and the unit tests
        `test_openapi_artifact.py` and `test_openapi_operations_schema.py`.
  - [ ] Success: the artifact lists the seven new paths, each `200` with a
        `$ref` model; the tests pass.
- [ ] **8.2 Add the futures section to `docs/api/reference.md`** (effort 4)
  - [ ] A new section, "Endpoints: futures", with one subsection per route.
        Each carries an `<!-- endpoint: … params: … errors: … -->` marker
        in the gate's canonical types, an example request and an abridged
        response.
  - [ ] **Shared content:**
    - session dates with an exclusive `end`;
    - `from`/`until` as half-open instants, with a paging example;
    - the roll-rule grammar and the default;
    - what `segments`, `unresolved` and `incomplete` mean and when each
      appears (229 requires the unresolved part);
    - prices and times as strings.
  - [ ] **Value markers:** the 75,000 ceiling (`from:
        manta_trading.constants.API_MAX_BARS_PER_REQUEST`) and the 1,300
        session cap (`from:
        manta_trading.data.tick.constants.TICK_ROLL_MAX_SESSIONS`).
  - [ ] **Conventions sections:**
    - §2.2 gains the futures exception to inclusive windows, with the
      reason;
    - §2.5 explains the futures ceiling (enforced while reading; the
      refusal quotes no count);
    - §2.7 adds the `503` and its body.
  - [ ] **Existing route sections:** §3.1 health (`tick_db`,
        `tick_detail`, enum-marked against `TickLineState`), §3.5 status
        (the `tick` block), §5.3 overview (`futures ticks`,
        `unavailable`).
  - [ ] Success: `scripts/check_api_docs.py` reports no failure for
        `reference.md`.
- [ ] **8.3 Extend `docs/api/agents.md`** (effort 3)
  - [ ] §2 capability index: one row per new route.
  - [ ] §3 choosing a window: the futures window rule and paging.
  - [ ] §5 failure modes: the `503` verdict ("retry later; the tick
        database is down or unconfigured; equities routes are unaffected").
  - [ ] §7 vocabularies: the bar sizes, the rule grammar and the line
        states, each enum-marked.
  - [ ] The endpoint markers, as in 8.2.
  - [ ] Success: `uv run python scripts/check_api_docs.py --check` exits
        0.
- [ ] **8.4 Update the 180 serving architecture**
      (`user/architecture/180-arch.data-serving.md`) (effort 1)
  - [ ] Mark the "Tick data endpoints" exclusion closed by slice 231, dated.
  - [ ] Add the "Extended by slice 231" paragraph to the Range Policy,
        after 188's, covering the content of the design's TD4 bullet: the
        third enforcement style, the measured 687,287-tick session, and
        "no DB work for a rejected request" holding for bars only.
  - [ ] Success: both edits present; nothing else in 180 changed.
- [ ] **8.5 Add the 220 architecture Revision Log entry** (effort 1)
  - [ ] Under a 2026-10-12 slice 231 entry:
    - the health field uses the five `TickLineState` values, not
      "`ok` or `error`", with the reason;
    - futures windows are session dates with an exclusive end, plus
      half-open instants for ticks.
  - [ ] Success: entry present.
- [ ] **8.6 Commit** (effort 1)
  - [ ] Check gate and the docs tests (`test_api_docs.py`, and the
        integration `test_api_docs_examples.py`).
  - [ ] Commit: `docs: document futures API routes and tick lines`.

## Section 9 - Load tier and connection budget

- [ ] **9.1 A ceiling-sized seed helper** (effort 2)
  - [ ] Add a load-tier helper that seeds one instrument-session of about
        690,000 ticks (the busiest measured session) into a throwaway tick
        test database. Use the fastest existing insert path (`COPY` if a
        helper exists; 1.1 named it), plus the ledger row and the unit it
        needs.
  - [ ] Success: the seed finishes, and the ledger `record_count` equals
        the rows inserted.
- [ ] **9.2 Add `test/load/test_231_futures_api_nfr.py`** (effort 3)
  - [ ] Use `create_app(db_url=…, tick_db_url=…)` over the seeded
        databases, never the production URLs. Measure, in this order:
    - a 75,000-tick read through `from`/`until`;
    - a ceiling-refused whole-session read;
    - a 54-session `1m` product bars read across a roll (seed bars data
      with `seed_september_roll` extended, or document the session count
      actually reachable);
    - the cost the tick block adds to `/api/v1/status`, as the difference
      in median latency with and without a tick pool.
  - [ ] Each test asserts a bound set from its measured value (about 2× the
        median of 5 runs), and records the measurement and date in its
        docstring. The status addition is its own pass/fail test (design,
        Technical Requirements).
  - [ ] Also measure the 75,000-tick read with the rows built through
        validation instead of `model_construct`. Record both figures. Keep
        `model_construct` only if it is measurably faster; otherwise
        switch to validated construction and update the design's TD5
        bullet.
  - [ ] Success: the four tests pass; the existing load tests show no new
        failure beyond the 4 known ones.
- [ ] **9.3 Check the connection budget** (effort 1)
  - [ ] Compare the design's figures (per worker, 8 tick connections and 4
        primary connections; with 4 workers, 32 and 16) with the
        `max_connections` values recorded in 1.1, and add the primary
        cluster's existing API pools.
  - [ ] If either cluster cannot hold them, stop and ask the Project
        Manager. Do not change a cluster setting.
  - [ ] Record the figures in the design's Special Considerations.
  - [ ] Success: the figures are recorded.
- [ ] **9.4 Commit:** `test: add futures API load-tier bounds` (effort 1)

## Section 10 - Closing

- [ ] **10.1 Full validation** (effort 2)
  - [ ] Unit tier, then the integration tier (6 known failures only), then
        the load tier (4 known failures only), each run separately.
  - [ ] mypy across every touched source and test path in one
        invocation; ruff check and scoped format; `scripts/check_api_docs.py
        --check`.
  - [ ] Success: all clean apart from the known lists.
- [ ] **10.2 Run the Verification Walkthrough** (effort 3)
  - [ ] Run the design's steps 1–9 against the dev tick database and the
        primary calendar. Record each command, output excerpt and outcome
        in `user/notes/231-walkthrough.md` (frontmatter per
        `file-naming-conventions.md`). Step 4 picks the largest session
        from `mt data tick coverage`.
  - [ ] Step 10 (production after 234) is recorded as not runnable in
        this slice, with the reason.
  - [ ] Where the design's expected output was wrong, correct the
        walkthrough in the design and note the correction in the notes
        file.
  - [ ] Success: every step 1–9 is recorded as passing, or as corrected
        and passing.
- [ ] **10.3 Align the design with what shipped** (effort 1)
  - [ ] Update the design for anything decided during implementation: 1.1
        findings, file splits, the `model_construct` choice, and the
        measured bounds. Set `status: complete` and `dateUpdated`.
  - [ ] Success: the design matches the code.
- [ ] **10.4 Final commit:** `docs: mark slice 231 complete` (effort 1)
