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
dateUpdated: 20261013
status: complete
---

# Tasks: API Surface Coverage — Futures Tick (part 2 of 2)

Part 2: the four time-series routes, the health/status/overview additions,
the documents, the load tier and the closing checks. The Context Summary in
part 1 (`231-tasks.api-surface-coverage-futures-tick-1.md`) applies here:
paths, test environment, lint and production safety.

**Effort scale:** 1 (trivial) to 5 (hard).

---

## Section 6 - Time-series routes (TD3, TD4, TD5, TD6)

- [x] **6.1 Add the ceiling refusal to `admission.py`** (effort 1)
  - [x] `over_ceiling(max_rows: int, *, remedy: str) -> HTTPException`
        returns the `422` "the request matches more than the {max_rows:,}
        row limit; {remedy}", in the existing refusal's style.
  - [x] Add `NARROW_TICKS = "narrow start/end or from/until"` beside
        `NARROW_WINDOW`; bars use `NARROW_WINDOW`.
  - [x] Success: `test_admission.py` gains a case for the new helper;
        passes.
- [x] **6.2 Build the shared read helper** (effort 4)
  - [x] In `routes/futures_series.py`, or in `api_server/futures_read.py`
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
  - [x] **Error mapping:** the plan and the reads run inside part 1's
        `tick_errors()` (task 3.1), which already orders `QueryCanceled` →
        `504` before `OperationalError`/`TickCalendarError` → `503`, then
        `ContractNotFound` → `404` and `TickPreflightError` → `422`. Do not
        add a second mapping. The only addition is `RowCeilingExceeded` →
        6.1's `422`, raised after the connection is released.
  - [x] `from` must be before `until`; otherwise `422`. A naive datetime is
        read as UTC, as in `windows.py`.
  - [x] Success: no route repeats this logic; nothing in the helper formats
        a price or a time (the models do).
- [x] **6.3 Test the read helper** (effort 3)
  - [x] Unit tests for clipping on synthetic windows:
    - a `from` inside a window;
    - an `until` exactly at a window's open (the window is dropped);
    - a window fully outside the bounds;
    - adjacent `[a, b)` and `[b, c)` bounds that partition a window with no
      overlap.
  - [x] Integration tests that call the helper directly, with an explicit
        ceiling argument; no route exists yet. Seed with `seed_ticks`,
        about 30 ticks across one session:
    - ceiling 10 → `RowCeilingExceeded`, and a spy on the collector shows
      at most ceiling + `TICK_GET_FETCH_ROWS` rows read;
    - exactly 10 ticks in the bounds with ceiling 10 → all 10 returned
      (FR5: at the ceiling succeeds);
    - consecutive `[from, until)` bounds over the session return exactly
      the session's ledger `record_count`, with no duplicate
      `(ts_event, sequence)` (FR6).
  - [x] Success: passes.
- [x] **6.4 Add the contract ticks route** (effort 2)
  - [x] `GET /api/v1/futures/contracts/{symbol}/ticks?start=&end=&from=&until=&format=`:
    - `response_class=Response`,
      `responses=timeseries_200(FuturesTicksResponse) |
      TICK_UNAVAILABLE_RESPONSE | GATEWAY_TIMEOUT_RESPONSE`;
    - it depends on `tick_connection` and `get_max_bars`;
    - it serializes through `timeseries_response`.
  - [x] Register `futures_series`'s router in `create_app`.
  - [x] Success: route in the schema with its `200` model, for both media
        types.
- [x] **6.5 Test the contract ticks route** (effort 3)
  - [x] Integration tests:
    - field-for-field equality with the CLI's JSON writer over the same
      seed and bounds (FR1). Call the `get` writer path in-process and
      compare parsed JSON rows;
    - `format=msgpack` decodes to the same body;
    - `ZZZ9` → `404`;
    - an ambiguous symbol (seed two instruments with one raw symbol a
      decade apart) → `422` naming both;
    - an empty range → `422`;
    - `from == until` and `from > until` → `422` naming the problem
      (FR4);
    - a naive `from` is read as UTC: the same result as the `Z` form;
    - with the ceiling patched to 10 through
      `app.dependency_overrides[get_max_bars]`, a wider window → `422`
      whose body names 10 and `from/until` (FR5);
    - the closed-port fixture → `503`.
  - [x] Success: passes.
- [x] **6.6 Add the contract bars route** (effort 2)
  - [x] `GET /api/v1/futures/contracts/{symbol}/bars?granularity=&start=&end=&format=`:
    - `granularity` is restricted to `TICK_BAR_GRANULARITIES` (1.1 found
      how) and required;
    - no `from`/`until` (TD3);
    - bars models and the read helper.
  - [x] Success: the schema lists exactly the tick bar sizes.
- [x] **6.7 Test the contract bars route** (effort 2)
  - [x] Integration tests:
    - `1m` and `1d` equal the CLI's JSON bars over the same seed;
    - `granularity=2h`, or a size outside the tuple → `422`.
  - [x] Success: passes.
- [x] **6.8 Add the product ticks and product bars routes** (effort 3)
  - [x] `GET /api/v1/futures/products/{product}/ticks` and
        `GET /api/v1/futures/products/{product}/bars`:
    - the same as 6.4 and 6.6, plus `rule`, parsed as in 5.7;
    - an unknown product → `404`;
    - the plan is `product_plan`.
  - [x] `target` carries `kind: "product"`, `rule` and `is_default`;
        `segments`, `unresolved` and `incomplete` come straight from the
        plan.
  - [x] Success: the four time-series routes share the 6.2 helper and the
        6.2 error mapping; none has its own `try`.
- [x] **6.9 Test the product routes** (effort 3)
  - [x] Integration tests over `seed_september_roll`:
    - `1d` bars across the roll: `segments` name ESU4 then ESZ4 under
      `v.0`, `is_default` is true, and the bars equal the CLI's (FR2);
    - a range starting at the first held session lists those sessions in
      `unresolved`, with HTTP 200 (FR3);
    - a session seeded `partial` appears in `incomplete` (FR3);
    - `rule=c.0` changes the segments;
    - `rule=bad` → `422`;
    - a range over 1,300 sessions → `422` (FR4);
    - a calendar URL pointing at a closed port, with a live tick database
      → `503` (an outage in `product_plan`'s roll path);
    - with part 1's two-product fixture (5.6), the second product's
      `bars?granularity=1d` answers 200, not `404`.
  - [x] Success: passes.
- [x] **6.10 Commit** (effort 1)
  - [x] Unit and integration tiers for the touched directories; mypy; ruff;
        `git diff main` checked.
  - [x] Commit: `feat: add futures ticks and bars routes`.

## Section 7 - Health, status and overview (TD7)

- [x] **7.1 Add `tick_db` to `/api/v1/health`** (effort 2)
  - [x] `HealthResponse` gains `tick_db: TickLineState` and
        `tick_detail: str | None`. The detail is the `TickUnavailable`
        detail, dropped by `exclude_none` when the state is ok.
  - [x] Make the handler `async`:
    - the existing `SELECT 1` and freshness probe run in
      `run_in_executor` on a pooled connection (keep `get_db_pool`
      semantics);
    - `probe_tick(app)` is awaited;
    - HTTP is 200 in every case.
  - [x] Success: the existing `test_health.py` passes after only fixture
        changes; record any it needed in the commit message.
- [x] **7.2 Test health** (effort 2)
  - [x] Integration tests:
    - `tick_db: "ok"` with no `tick_detail`;
    - `not_configured`, `unreachable` and `migrations_pending` through
      part 1's fixtures (task 3.2) and the ledger-row deletion on the
      function-scoped throwaway tick database, each with HTTP 200 and the
      matching detail;
    - with the closed-port URL carrying a password, `tick_detail` does not
      contain it (FR11);
    - a primary database failure still reports `db: "error"`, as before.
  - [x] Success: passes.
- [x] **7.3 Add the `tick` block to `/api/v1/status`** (effort 2)
  - [x] `StatusResponse.tick: TickBlock`, always present.
  - [x] The route probes the tick pool:
    - on success it runs `accounting_lines(conn, calendar_url,
      TICK_UNIVERSE)`, giving `state: "ok"`;
    - a probe failure, or an exception from the read classified through
      2.1's `classify` (with the API `statement_timeout`), gives that state
      and detail with `products: []`.

    The equities rows and filters are unchanged.
  - [x] Success: no `5xx` can come from the tick block.
- [x] **7.4 Test status** (effort 2)
  - [x] Integration tests:
    - the `tick` block equals `mt data accounting --json`'s `tick` over
      the same seed (FR8, Integration Requirements), computed in-process;
    - with `symbol=AAPL&health=OK` the block is unchanged;
    - closed port → `tick.state == "unreachable"`, HTTP 200, equities rows
      present, and `tick.detail` does not contain the URL's password
      (FR11);
    - not configured → `tick.state == "not_configured"`, HTTP 200 (FR8).
  - [x] Success: passes; existing `test_status.py` passes.
- [x] **7.5 Add the `futures ticks` source to `/api/v1/overview`**
      (effort 2)
  - [x] `SourceRecord` gains `unavailable: str | None`. The existing four
        sources map `unavailable=None` explicitly.
  - [x] The route probes the tick pool and appends
        `SourceRecord(TICK_SOURCE_NAME, newest, unavailable)`:
    - `newest` comes from `newest_event_ns` through `utc_from_ns`;
    - on failure, `newest` is null and `unavailable` is
      `unavailable_text(...)`.
  - [x] Success: `gather_db_facts` is untouched; the order of the sources
        matches the CLI's.
- [x] **7.6 Test overview** (effort 2)
  - [x] Integration tests:
    - the record equals `mt data overview --json`'s `futures ticks`
      source (name, `newest`, `unavailable`) over the same seed;
    - not configured → `unavailable` carries the not-configured text and
      HTTP 200 (FR8);
    - closed port → `unavailable` starts `unreachable:` and HTTP 200
      (FR9);
    - the four existing sources keep their values with
      `unavailable: null`.
  - [x] Update `test_operations_models.py` and
        `test_openapi_operations_schema.py` for the new field.
  - [x] Success: passes.
- [x] **7.7 Commit** (effort 1)
  - [x] Unit and integration tiers for the touched directories; mypy; ruff;
        `git diff main` checked.
  - [x] Commit: `feat: add tick lines to health, status and overview`.

## Section 8 - Documents (the 190 gate)

- [x] **8.1 Regenerate `openapi.json`** (effort 1)
  - [x] Run `uv run python scripts/dump_openapi.py`, and the unit tests
        `test_openapi_artifact.py` and `test_openapi_operations_schema.py`.
  - [x] Success: the artifact lists the seven new paths, each `200` with a
        `$ref` model; the tests pass.
- [x] **8.2 Add the futures section to `docs/api/reference.md`** (effort 4)
  - [x] A new section, "Endpoints: futures", with one subsection per route.
        Each carries an `<!-- endpoint: … params: … errors: … -->` marker
        in the gate's canonical types, an example request and an abridged
        response.
  - [x] **Shared content:**
    - session dates with an exclusive `end`;
    - `from`/`until` as half-open instants, with a paging example;
    - the roll-rule grammar and the default;
    - what `segments`, `unresolved` and `incomplete` mean and when each
      appears (229 requires the unresolved part);
    - prices and times as strings.
  - [x] **Value markers:** the 75,000 ceiling (`from:
        manta_trading.constants.API_MAX_BARS_PER_REQUEST`) and the 1,300
        session cap (`from:
        manta_trading.data.tick.constants.TICK_ROLL_MAX_SESSIONS`).
  - [x] **Conventions sections:**
    - §2.2 gains the futures exception to inclusive windows, with the
      reason;
    - §2.5 explains the futures ceiling (enforced while reading; the
      refusal quotes no count);
    - §2.7 adds the `503` and its body.
  - [x] **Existing route sections:** §3.1 health (`tick_db`,
        `tick_detail`, enum-marked against `TickLineState`), §3.5 status
        (the `tick` block), §5.3 overview (`futures ticks`,
        `unavailable`).
  - [x] Success: `scripts/check_api_docs.py` reports no failure for
        `reference.md`.
- [x] **8.3 Extend `docs/api/agents.md`** (effort 3)
  - [x] §2 capability index: one row per new route.
  - [x] §3 choosing a window: the futures window rule and paging.
  - [x] §5 failure modes: the `503` verdict ("retry later; the tick
        database is down or unconfigured; equities routes are unaffected").
  - [x] §7 vocabularies: the bar sizes, the rule grammar and the line
        states, each enum-marked.
  - [x] The endpoint markers, as in 8.2.
  - [x] Success: `uv run python scripts/check_api_docs.py --check` exits
        0.
- [x] **8.4 Update the 180 serving architecture**
      (`user/architecture/180-arch.data-serving.md`) (effort 1)
  - [x] Mark the "Tick data endpoints" exclusion closed by slice 231, dated.
  - [x] Add the "Extended by slice 231" paragraph to the Range Policy,
        after 188's, covering the content of the design's TD4 bullet: the
        third enforcement style, the measured 687,287-tick session, and
        "no DB work for a rejected request" holding for bars only.
  - [x] Success: both edits present; nothing else in 180 changed.
- [x] **8.5 Add the 220 architecture Revision Log entry** (effort 1)
  - [x] Under a 2026-10-12 slice 231 entry:
    - the health field uses the five `TickLineState` values, not
      "`ok` or `error`", with the reason;
    - futures windows are session dates with an exclusive end, plus
      half-open instants for ticks.
  - [x] Success: entry present.
- [x] **8.6 Commit** (effort 1)
  - [x] Check gate and the docs tests (`test_api_docs.py`, and the
        integration `test_api_docs_examples.py`).
  - [x] Commit: `docs: document futures API routes and tick lines`.

## Section 9 - Load tier and connection budget

- [x] **9.1 A ceiling-sized seed helper** (effort 2)
  - [x] Add a load-tier helper that seeds one instrument-session of about
        690,000 ticks (the busiest measured session) into a throwaway tick
        test database. Use the fastest existing insert path (`COPY` if a
        helper exists; 1.1 named it), plus the ledger row and the unit it
        needs.
  - [x] Success: the seed finishes, and the ledger `record_count` equals
        the rows inserted.
- [x] **9.2 Add `test/load/test_231_futures_api_nfr.py`** (effort 3)
  - [x] Use `create_app(db_url=…, tick_db_url=…)` over the seeded
        databases, never the production URLs. Measure, in this order:
    - a 75,000-tick read through `from`/`until`;
    - a ceiling-refused whole-session read;
    - a 54-session `1m` product bars read across a roll (seed bars data
      with `seed_september_roll` extended, or document the session count
      actually reachable);
    - the cost the tick block adds to `/api/v1/status`, as the difference
      in median latency with and without a tick pool.
  - [x] Each test asserts a bound set from its measured value (about 2× the
        median of 5 runs), and records the measurement and date in its
        docstring. The status addition is its own pass/fail test (design,
        Technical Requirements).
  - [x] Also measure the 75,000-tick read with the rows built through
        validation instead of `model_construct`. Record both figures. Keep
        `model_construct` only if it is measurably faster; otherwise
        switch to validated construction and update the design's TD5
        bullet.
  - [x] **Existing load tests with a tick pool configured** (Technical
        Requirements): pass the seeded test tick URL explicitly to the
        `create_app` calls in `test_187_api_nfr.py`,
        `test_188_kalshi_api_nfr.py` and `test_189_operations_nfr.py`. Part
        1's guard (task 3.2) keeps `.env`'s URL out; this puts a real pool
        in, never the dev or production one.
  - [x] Success: the four tests pass; the existing load tests, now with a
        tick pool, show no new failure beyond the 4 known ones.
- [x] **9.3 Check the connection budget** (effort 1)
  - [x] Compare the design's figures (per worker, 8 tick connections and 4
        primary connections; with 4 workers, 32 and 16) with the
        `max_connections` values recorded in 1.1, and add the primary
        cluster's existing API pools.
  - [x] If either cluster cannot hold them, stop and ask the Project
        Manager. Do not change a cluster setting.
  - [x] Record the figures in the design's Special Considerations as dev
        values.
  - [x] Production is not checked here. Add "check production
        `max_connections` on both clusters against 231's per-worker
        connection budget for the configured `--workers`" to the 234 entry
        in the 220 slice plan.
  - [x] Success: the figures are recorded.
- [x] **9.4 Commit:** `test: add futures API load-tier bounds` (effort 1)

## Section 10 - Closing

- [x] **10.1 Full validation** (effort 2)
  - [x] Unit tier, then the integration tier (6 known failures only), then
        the load tier (4 known failures only), each run separately.
  - [x] mypy across every touched source and test path in one
        invocation; ruff check and scoped format; `scripts/check_api_docs.py
        --check`.
  - [x] **The load tier is gated by this manual run, not by CI.** Record
        this in the closing notes: `.github/workflows/ci.yml` publishes on
        tags and runs no test tier, and the load tier needs the private test
        cluster, so CI cannot run it. Wiring CI is not part of this slice.
        If the Project Manager wants it, it is a separate slice.
  - [x] Create `user/notes/231-walkthrough.md` (frontmatter per
        `file-naming-conventions.md`) with a "Load-tier gate" section
        holding that decision; 10.2 adds the walkthrough to it.
  - [x] Success: all clean apart from the known lists; the gate decision is
        recorded.
- [x] **10.2 Run the Verification Walkthrough** (effort 3)
  - [x] Run the design's steps 1–9 against the dev tick database and the
        primary calendar. Record each command, output excerpt and outcome
        in `user/notes/231-walkthrough.md`. Step 4 picks the largest session
        from `mt data tick coverage`.
  - [x] Step 10 (production after 234) is recorded as not runnable in
        this slice, with the reason.
  - [x] Where the design's expected output was wrong, correct the
        walkthrough in the design and note the correction in the notes
        file.
  - [x] Success: every step 1–9 is recorded as passing, or as corrected
        and passing.
- [x] **10.3 Align the design with what shipped** (effort 1)
  - [x] Update the design for anything decided during implementation: 1.1
        findings, file splits, the `model_construct` choice, and the
        measured bounds. Set `status: complete` and `dateUpdated`.
  - [x] Success: the design matches the code.
- [x] **10.4 Final commit:** `docs: mark slice 231 complete` (effort 1)
