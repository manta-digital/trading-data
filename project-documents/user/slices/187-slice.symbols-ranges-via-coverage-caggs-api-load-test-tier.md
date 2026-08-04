---
docType: slice-design
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
parent: user/architecture/180-slices.data-serving-api.md
dependencies: [167, 185, 186]
interfaces: [907]
dateCreated: 20260804
dateUpdated: 20260804
status: not_started
effort: 3
---

# Slice Design: Symbols Ranges via Coverage Caggs + API Load-Test Tier

## Overview

Two pieces of work that share one prerequisite — knowing what the serving API
actually costs.

The first replaces the `available` ranges computation in
`GET /api/v1/symbols/{symbol}`. Slice 183 computed those ranges with live
per-symbol `MIN/MAX` at request time and deferred a precomputed structure until
ingest stabilized; slice 167 has since built one. The second adds the `test/load/`
assertions for `api_server` that the project's Python rules require of any
network-path code and that the API has never had.

**The Phase 4 investigation changed this slice materially.** Three things
measured on prod `trading` (2026-08-04) do not match what the slice-plan entry
assumed:

1. The expensive query is not the one the plan names. Per-symbol `MIN/MAX` on
   `minute_5min_ohlcv` costs ~32 ms. Per-symbol `MIN/MAX` on `daily_ohlcv`
   costs **2.5–4.0 s**, and 96% of that is *planning* across 3,371 chunks — the
   same pathology slice 186 D12b found in the adjustment path.
2. The coverage caggs are **structurally stale**: `daily_coverage` has not
   advanced past 2026-06-12 while raw `daily_ohlcv` reaches 2026-08-03.
3. `assert_cagg_fresh` reports both caggs **fresh with `lag=0`**, and cannot do
   otherwise — its bucket-alignment step cancels exactly the lag in question.

So reading `available` from the coverage caggs *under the freshness guard*, as
the plan specifies, would replace a slow-but-correct answer with a fast one that
is 52 days wrong, with a guard that certifies it. This slice therefore reads
coverage for the historical floor and a **bounded head probe** for the leading
edge — correct regardless of coverage staleness — and makes the coverage
freshness verdict mean something. Repairing the caggs themselves is a separate
slice (PM, 2026-08-04).

## Value

- `GET /api/v1/symbols/{symbol}` drops from ~2.7–4.0 s to ~25 ms — the endpoint
  the UI calls to decide what it may request stops being the slowest thing in
  the API.
- The coverage freshness verdict stops being vacuous. Today `data_status`,
  `mt data status`, `/api/v1/health`, and `/api/v1/status` all report healthy
  coverage while the daily branch understates the leading edge by 52 days.
- The API acquires the load tier the Python rules require, including a
  **request-latency** bound — the gap slice 186 D12b proved is real and that
  `statement_timeout` structurally cannot close.

## Technical Scope

| File | Change |
| --- | --- |
| `api_server/routes/symbols.py` | Replace the two lazy range queries and the `asyncio.gather` with the coverage + head-probe read (D2, D3, D7) |
| `api_server/queries.py` | New `fetch_available_ranges` and the cached universe-edge read (D2, D3) |
| `api_server/app.py` | `create_app(db_url=None)` seam for the load tier; universe-edge cache on `app.state` (D3, D10) |
| `data/maintenance/status_coverage.py` | Content-edge freshness check for the coverage caggs (D6) |
| `market/maintenance/cagg_freshness.py` | Expose `bucket_width` on the verdict; document and test the detection floor (D6) |
| `constants.py` | `COVERAGE_CONTENT_STALENESS` threshold (D6) |
| `test/load/test_187_api_nfr.py` | New — the API load tier (D9, D10) |
| `test/load/conftest.py` | Extract 167's `prod_shaped_db` fixture for reuse; add the dense-minute fixture (D10) |
| `test/unit/api_server/test_symbols.py` | Cover the merge logic and every coverage/head combination (D2) |
| `test/integration/test_symbol_ranges_sql.py` | New — execute the real statements against real caggs (D2) |
| `docs/api/openapi.json`, `README.md`, `CHANGELOG.md` | Regenerate/document |

Out of scope, by decision: repairing the coverage caggs' refresh (D5), CI
wiring for the load tier (D9), and pagination or any change to the bars contract.

## Technical Decisions

### D1 — The premise is inverted: `daily_ohlcv` is the cost, not the 5min cagg

Measured on prod `trading`, 2026-08-04, single connection, `statement_timeout`
set, times in ms:

| symbol | lazy minute `MIN/MAX` | lazy daily `MIN/MAX` | coverage (both families) |
| --- | --- | --- | --- |
| SPY | 102.4 | 3,998.6 | 9.7 |
| AAPL | 32.2 | 2,672.1 | 10.0 |
| MSFT | 31.1 | 2,679.0 | 10.3 |
| IBM | 32.7 | 2,690.8 | 10.0 |
| GE | 32.0 | 2,689.6 | 10.4 |
| F | 35.1 | 2,723.4 | 14.8 |

`EXPLAIN (ANALYZE, BUFFERS)` on `MIN(time), MAX(time) FROM daily_ohlcv WHERE
symbol = 'SPY'`:

```
Planning Time:  2848.850 ms
Execution Time:  104.167 ms
```

96% planning, over `daily_ohlcv`'s **3,371 chunks**. The same query with a
plan-time-prunable bound (`AND time > '2026-06-12'`):

```
Planning Time:  1.667 ms
Execution Time:  0.052 ms
```

A 1,700× planning collapse. This is slice 186 D12b's finding in a second
place, and it sets the whole shape of D2: **every statement on this path must
carry a bound the planner can use for chunk exclusion.** The unbounded query is
not slow because of data volume — it is slow before it reads anything.

The architecture document (`180-arch.data-serving.md`, "Symbol Detail") and the
slice-plan entry both attribute the cost to `minute_5min_ohlcv`. Both are
corrected in this slice (D12).

Note also that `symbols.py` issues its two range queries through
`asyncio.gather` on a **single pooled connection**. psycopg serializes execution
on a connection's lock, so the gather buys no parallelism — the endpoint pays
the sum, ~2.7–4.1 s. `status.py::get_status` documents this exact reasoning and
deliberately runs sequentially; `symbols.py` predates that note. D7 removes it.

### D2 — `available` = coverage floor + bounded head probe; no unbounded query on any path

Three statements, all bounded:

**A. Universe edges** (cached, D3) — the leading edge each coverage cagg has
materialized across all symbols.

**B. Per-symbol coverage**, one round trip for both families. The two caggs share
column names, so the union is symmetric and the only added element is the family
tag, which is `CycleGranularity` (D7):

```sql
SELECT 'minute' AS family,
       MIN(first_bucket)::date, MAX(last_bucket)::date
FROM minute_coverage WHERE symbol = %s
UNION ALL
SELECT 'daily', MIN(first_bucket)::date, MAX(last_bucket)::date
FROM daily_coverage  WHERE symbol = %s
```

**C. Per-symbol head probe**, bounded by the universe edge for its family:

```sql
SELECT 'minute' AS family,
       MIN(time_bucket)::date, MAX(time_bucket)::date
FROM minute_5min_ohlcv WHERE symbol = %s AND time_bucket > %s
UNION ALL
SELECT 'daily', MIN(time)::date, MAX(time)::date
FROM daily_ohlcv       WHERE symbol = %s AND time > %s
```

Merged per family:

```
start = COALESCE(coverage_start, head_start)
end   = COALESCE(head_end,       coverage_end)
family omitted when both are NULL
```

The `COALESCE` order is the whole design, and each of the four cases is real:

- **Data spanning the edge** — coverage supplies the true start, the head probe
  the true end. The common case.
- **Data entirely before the edge** (delisted, or a symbol whose ingest stopped)
  — the head probe returns `(NULL, NULL)` and coverage supplies both. Measured
  at 7–12 ms; it does not degrade to a scan.
- **Data entirely after the edge** — no coverage row exists; the head probe
  supplies both. This is why `start` coalesces from coverage *first* rather than
  taking the coverage value unconditionally.
- **No data** — everything is `NULL` and the family is omitted, matching today's
  contract. Verified on three instruments absent from `daily_coverage` (BOED,
  BRKD, EXPI): 7–12 ms, `(NULL, NULL)`, no slow path.

Measured end to end (per-symbol B + C, warm connection): AAPL 22.0 ms, IBM
21.2 ms, F 139.6 ms, SPY 405.9 ms — the SPY figure being the first statement
issued on a fresh connection, with the same query costing 11–12 ms on subsequent
calls. Against 2,672–3,999 ms today.

**Equivalence.** For all six symbols measured, the merged `(start, end)` is
byte-identical to the lazy result at date grain, on both families. The minute
family's coverage timestamps are truncated to the parent 4-hour cagg's bucket
start (slice 167 D3/D7), but a 4-hour bucket start always shares the UTC date of
every bar inside it, so the truncation is invisible at the `::date` grain this
endpoint reports — asserted directly rather than argued (success criterion 2).

### D3 — The head probe's bound is the universe-wide coverage edge, cached

The bound must be a value the planner can prune on, and it must exist for
symbols that have no coverage row at all. The **universe-wide**
`MAX(last_bucket)` per cagg satisfies both. Measured at ~32 ms steady state
(60.9 ms cold), so it is cached on `app.state` under the existing
`CAGG_FRESHNESS_CACHE_TTL` (60 s) rather than paid per request. Reusing that
constant is deliberate: the coverage refresh policies fire hourly, so a 60 s TTL
cannot mask an edge movement, and it is already the project's answer to "how
long may a cagg-derived fact be cached".

The alternative — bounding by each symbol's *own* coverage end — is exact but
its cost scales with how old that end is. For a symbol whose coverage ends in
2015, `time > '2015-01-01'` still leaves roughly 580 of 3,371 chunks and
reintroduces ~490 ms of planning. Rejected on that measurement.

**The residual gap, stated precisely.** A bar whose timestamp falls between a
symbol's own coverage end and the universe edge, *and* which was written after
the coverage cagg froze, is seen by neither statement. Today that window is at
most 2026-06-12 minus that symbol's coverage end. It matters only if such a bar
is the symbol's true maximum — a symbol backfilled into that window after the
freeze and not updated since. Today's unbounded query has no such gap; this is a
real exactness-for-latency trade, taken knowingly. It closes on its own when the
coverage repair slice lands and coverage tracks the leading edge again. The
verification walkthrough quantifies it across a symbol sample rather than
assuming it is small (step 4).

### D4 — No tail probe; `available.start` comes from coverage alone

Symmetry suggests also probing `time < coverage_start` to catch deep-history
backfill written after the cagg materialized an old bucket — a real exposure,
since a refresh policy's `start_offset` (750 days) means buckets older than that
are never reconsidered.

Measured and rejected: the tail probe does not prune. `MIN(time) FROM
daily_ohlcv WHERE symbol = %s AND time < <coverage_start>` costs 1,424.8 ms
(SPY), 597.7 ms (F), 407.1 ms (AAPL), 8.5 ms (IBM) — the bound excludes chunks
*after* the start, which is nearly none of them for a symbol with deep history.
There is no cheap version of this query.

So `available.start` is exactly what coverage says. The exposure is documented
in the README rather than engineered around: a symbol whose deep history is
backfilled after this slice ships will report a `start` that is too late until
its bucket is re-materialized. The daily cold-start backfill for the current
universe is complete, so this is a forward-looking caveat, not a live defect.

### D5 — The coverage caggs are structurally stale; this slice does not repair them (PM, 2026-08-04)

Evidence, prod `trading`, 2026-08-04:

- `daily_coverage`: universe-wide `MAX(last_bucket)` = **2026-06-12**, with
  11,675 of 12,040 symbols pinned at exactly that date. Raw `daily_ohlcv`
  reaches **2026-08-03**, with **390,884 rows** between the two dates.
- `minute_coverage`: `MAX(last_bucket)` = 2026-07-24 12:00 against a raw
  `minute_ohlcv` edge of 2026-07-28 13:30.
- Both refresh policies (jobs 1107, 1108) are `scheduled = true`, hourly, with
  `last_run_status = 'Success'` and 205 successes each. They are firing and
  succeeding.
- Both caggs' watermarks sit at 2026-12-27 — the **end** of the current
  365-day bucket.

Root cause: a refresh policy's window is `[now - start_offset, now - end_offset]`
truncated to whole buckets. With `COVERAGE_BUCKET_INTERVAL = 365 days` and
`end_offset` of 1–4 hours, `now - end_offset` falls inside the current bucket,
so truncation drops it. **The current bucket is materialized once, when the cagg
is created, and then never again until the year rolls over.** The hourly job has
been a successful no-op since creation. The daily universe's catch-up landed
after that point, which is why the daily side is 52 days out and the minute side
only 4.

Observable consequence today, independent of this slice: `data_status` reports
`SPY / daily / OK / last_bar_ts = 2026-06-12`.

Per the PM's direction, the repair — which needs either a smaller
`COVERAGE_BUCKET_INTERVAL` (and the row-count trade slice 167 D1 made
deliberately) or an explicit scheduled refresh covering the head bucket, plus a
rematerialization against prod — is filed as **slice 169** in the initiative-140
plan rather than absorbed here. This slice is designed so that the endpoint is
**correct while that defect stands** (D2) and so that the defect stops being
invisible (D6).

### D6 — The freshness guard has a one-bucket detection floor; coverage gets a content-edge check

`assert_cagg_fresh` computes

```
lag = time_bucket(bucket_width, max(time) on raw) - max(time_bucket) on cagg
```

Bucketing the raw edge onto the cagg's own grid is correct and was added for
good reason (slice 168: a healthy `daily_quarterly_ohlcv` otherwise reads 72
days behind purely because its newest bucket has a quarter still to run). But
the cancellation is total: **no lag smaller than one bucket width can ever be
detected.** For the coverage caggs that width is 365 days, against a threshold
of `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset` = 1 day 1 h.
The check is vacuous, and on prod today it returns `is_fresh=True, lag=0` over a
52-day staleness.

The fix belongs in the coverage-specific layer, not the generic one. The generic
guard is right for narrow-bucket caggs and has no general way to see inside a
bucket — but `minute_coverage` and `daily_coverage` both carry a `last_bucket`
column that *is* a content timestamp rather than a bucket start. So:

- **`cagg_freshness.py`** — `FreshnessVerdict` gains `bucket_width`, and the
  floor is documented at the `_raw_max` docstring and pinned by a test that
  constructs a wide-bucket cagg lagging inside one bucket and asserts the
  generic verdict cannot see it. No behavior change: this makes the limit
  explicit and machine-checked instead of implicit.
- **`status_coverage.check_coverage_freshness`** — adds a content-edge
  assertion per cagg: `max(last_bucket)` against `max(time)` on the source
  table, with no bucket alignment, judged against a new
  `COVERAGE_CONTENT_STALENESS` threshold. A new `StalenessSignal`
  (`CONTENT_EDGE_TOO_OLD`) carries it, so the reason is a signal and not a
  string. Cost is two bounded `max()` probes on the same connection, inside the
  existing verdict cache.

**Stated consequence, which the PM should expect.** With this check in place,
prod reports coverage **STALE** the moment the slice ships, and stays stale until
the repair lands. That flips `mt data status`'s banner, `/api/v1/health`'s
`coverage` field to `"stale"`, `/api/v1/status`'s `coverage.is_stale` to `true`,
and raises the existing ERROR log on every uncached read. That is the correct
report of the actual state — slice 167 D3a's rule is *report, don't refuse*, and
nothing starts failing — but it is a visible operational change, not a silent
one. `GET /api/v1/symbols/{symbol}` is deliberately unaffected: D2 makes its
answer correct without consulting the verdict at all.

`COVERAGE_CONTENT_STALENESS` is a new constant rather than a reuse of
`MAX_COVERAGE_SOURCE_STALENESS`: the two measure different things (content lag
vs. bucket lag) and will want different values once the repair lands. Its
initial value is `MAX_COVERAGE_SOURCE_STALENESS + end_offset`, which is the same
budget the bucket check nominally applies — chosen so the new check is strictly
a *detection* fix and not a quiet tightening of policy.

### D7 — One statement per concern; `CycleGranularity` as the family tag

The `asyncio.gather` in `get_symbol` is removed. It dispatches two queries onto
two executor threads sharing one pooled connection, which psycopg serializes;
it buys nothing and obscures that fact. `status.py` already documents the
correct pattern in `get_status::_fetch`. After D2 there are three statements,
issued sequentially inside a single `run_in_executor` call — one thread, one
connection, one checkout.

The union's family tag is `CycleGranularity.MINUTE` / `CycleGranularity.DAILY`,
the enum slice 912 introduced for exactly these two values (they are already
what `data_gaps.granularity` and `acquisition_state.granularity` store). No bare
`'minute'`/`'daily'` literal appears in the new code, and the merge dispatches on
enum members.

### D8 — The minute family keeps `minute_5min_ohlcv`; dates are cast in UTC explicitly

The head probe reads `minute_5min_ohlcv` even though coverage derives from
`minute_4hour_ohlcv`. This preserves today's contract exactly: `available`
advertises one range for the whole minute family, and the 5-minute cagg is what
183 chose as its proxy. It is also the more conservative of the two — the 5min
policy's `end_offset` is 5 minutes against the 4-hour cagg's 4 hours — so the
advertised edge never runs ahead of what a `5m` request can return.

All four range expressions cast with an explicit `AT TIME ZONE 'UTC'` before
`::date` rather than relying on the session's `TimeZone`. The API pool sets
`timezone = 'UTC'` in its `configure` hook, so this changes no value today; it
removes the dependency on that hook staying set. Slice 186 D12b spent real time
on a spurious difference caused by a `date → timestamptz` cast under differing
session timezones, and the cheapest place to not repeat that is here.

### D9 — Load-test tier: manual gate matching slice 167; CI wiring stays in 907

Slice 907 (CI pipeline and load-test gating) is `not_started`, and
`.github/workflows/ci.yml` is publish-on-tag with no test job. The slice-plan
entry's "wired into CI gating" is therefore not achievable here; it is 907's
deliverable and 907 already names the load tier in its scope.

This slice delivers the tests and the same documented manual gate
`test_167_data_status_nfr.py` established:

```
MT_RUN_LOAD_TESTS=1 uv run pytest test/load/
```

with `MT_TIMESCALE_TEST_URL` exported. The tier convention is inherited whole:
`pytest.mark.skipif` on `MT_RUN_LOAD_TESTS`, an ephemeral database per test, and
`test_load_tier_never_references_prod_db_url`, which fails any load-test line
that reads the production URL variable.

That last rule constrains the design: the load tier cannot point the API at its
database by setting `MT_TIMESCALE_DB_URL`. So `create_app` gains an optional
`db_url: str | None = None` parameter, `None` meaning "read `Settings()`" — the
current behavior, unchanged for every production path, and an explicit seam
rather than an environment side channel. The unit tier benefits too.

### D10 — What the load tier asserts

Four assertions, each chosen because a unit or integration test cannot make it.
Requests go through `httpx.ASGITransport` against the real app, so the executor
bridge, the pool, and the route are all in the measured path. (The ASGI
transport does not exercise uvicorn's HTTP layer; that limitation is recorded in
the module docstring, and it is not where the risk lies — 186 D12b's 95-second
request was 94 sequential statements, all of which this path reproduces.)

1. **Symbol detail latency.** Median over repeated calls, cold verdict cache
   reset before each. Provisional bound **< 250 ms** — ten times the ~25 ms
   measured on prod, and still an order of magnitude under today's behavior.
2. **Bars request latency at the admission ceiling.** The headline assertion.
   `statement_timeout` bounds a statement, not a request, and nothing in the
   stack bounds request latency (`180-arch.data-serving.md`, Error Handling).
   A `1m` request at exactly the `MT_API_MAX_BARS_PER_REQUEST` window must
   complete inside a stated wall-clock bound. Provisional **< 15 s**; Phase 6
   fixes it at three times the measured median on the fixture and records the
   measurement.
3. **Concurrency / pool contention.** 16 concurrent symbol-detail requests
   against a pool of `max_size=8` must all complete, none exceeding the
   single-request bound by more than the queueing factor. This is the assertion
   that would have caught the "held connection for the whole request" problem
   slice 185 D8a fixed by inspection, and it is the input to D11.
4. **Status endpoint latency.** Full-universe `/api/v1/status`, reusing slice
   167's sub-second NFR as the DB-side budget plus serialization headroom.
   Provisional **< 1.5 s**.

**Fixtures, and an honest statement of what they do and don't reproduce.**
167's `prod_shaped_db` (12,000 symbols × 10 years, one bar per symbol-year)
reproduces the *row-count* shape that drives coverage and `data_status` reads,
and is reused for assertions 1, 3, and 4 — extracted into `test/load/conftest.py`
rather than imported across test modules. It does **not** reproduce prod's
3,371-chunk `daily_ohlcv` planning cost, and no affordable fixture does; the
D1/D2 measurements against prod are the evidence for that dimension and are
recorded here rather than asserted in CI. Assertion 2 needs density instead of
breadth and gets its own fixture: one symbol with a dense `1m` window large
enough to exceed the 75,000-bar ceiling (~120 days × 960 extended-hours bars ≈
115k rows), which is a cheap `COPY`.

Every bound is provisional in this document and **must be re-derived from a
measurement recorded in the Phase 6 walkthrough**. A load test whose threshold
was invented is a test that passes for the wrong reason.

### D11 — Pool sizing and the single-pool question are decided from assertion 3, not assumed

`180-arch.data-serving.md` defers both to this slice: the API process owns three
independent pools (`app.state.db_pool` plus the two class-owned pools), and
consolidating them "is the better end state and is deferred to slice 187, which
builds the load-test tier that can size it."

This slice **decides** the question and records the decision; it does not
presume the answer. Assertion 3 supplies the measurement. Consolidation lands in
this slice only if the concurrency numbers show the three-pool arrangement
costing something real — otherwise the decision is "not now", written down with
the numbers behind it, so it stops being an open question carried forward
silently. Slice 186 D2 declined to change pool sizing for exactly the reason
that it had no measurement; this slice removes that excuse either way.

### D12 — Documents corrected in this slice

- `180-arch.data-serving.md`, "Symbol Detail": the paragraph attributing the
  ranges cost to `minute_5min_ohlcv` and calling both queries "sub-millisecond
  index seeks" is wrong on both counts. Replaced with the D1 measurement and the
  D2 read path.
- `180-slices.data-serving-api.md`, entry 7: materialize the `(187)` index and
  correct the scope text (the guard-based fallback the entry specifies is not
  what this slice builds, and CI gating moves to 907).
- `README.md`: `available` semantics — the leading edge is exact, the start is
  as of the last coverage materialization (D4), and the residual window from D3.

## API Specification — changed surfaces

`GET /api/v1/symbols/{symbol}` keeps its response shape exactly. `SymbolDetail`,
`AvailableRange`, the 404-on-unknown-symbol contract, and the omit-empty-family
rule are all unchanged; only how `available` is computed changes. The committed
`docs/api/openapi.json` is therefore expected to be unchanged for this route,
and the existing drift test will confirm that rather than the change being
asserted by hand.

`GET /api/v1/health` and `GET /api/v1/status` change in *value*, not in shape:
their coverage fields begin reporting `stale` / `is_stale: true` on production
data as a consequence of D6.

## Cross-Slice Dependencies and Interfaces

- **167** — supplies `minute_coverage`/`daily_coverage`, `status_coverage`, and
  the load-tier conventions this slice extends. D6 modifies its freshness
  reporting; D5 documents a defect in its delivery that this slice does not fix.
- **168** — `assert_cagg_fresh` is extended (verdict field, tests), not replaced.
- **185** — owns the `coverage` field on `/health` and `/status` whose value D6
  changes.
- **186** — supplies `queries.py`, the `504` handler, the committed OpenAPI
  artifact, and the D12b planning-cost finding that D1 re-applies.
- **907** — inherits CI gating for this tier (D9). Its scope already names the
  load tier; no change needed there.
- **169** — coverage-cagg refresh repair, filed by this design (D5) as entry 29
  in the initiative-140 plan. Blocks nothing here; until it lands, prod reports
  coverage stale (D6) and the D3 residual window stays open. Conversely, 169's
  verification depends on this slice: D6 is what makes its "before" state
  visible and its "after" state confirmable.

## Success Criteria

1. `GET /api/v1/symbols/{symbol}` issues no unbounded aggregate. Confirmed by
   plan inspection: every statement on the path shows chunk exclusion and
   planning time in single-digit ms.
2. For a sample of at least 20 symbols spanning dense, sparse, delisted, and
   no-data cases, the merged `available` equals the lazy `MIN/MAX` result at
   date grain on both families — or, where it differs, the difference is
   attributable to the D3 residual window and is recorded with the symbol and
   the size of the discrepancy.
3. A symbol with no coverage row in a family returns that family's range from
   the head probe alone, and a symbol with no data in a family omits it.
4. `check_coverage_freshness` reports the coverage caggs **stale** against
   current prod data, naming `CONTENT_EDGE_TOO_OLD`, and reports **fresh**
   against a fixture whose coverage tracks its source.
5. A test pins the generic guard's one-bucket detection floor, failing if
   `_raw_max`'s bucket alignment is changed without acknowledging it.
6. `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` passes with all four D10
   assertions present, every bound traceable to a measurement in the
   walkthrough, and `test_load_tier_never_references_prod_db_url` still green.
7. `create_app(db_url=...)` lets the load tier target an ephemeral database with
   no reference to `MT_TIMESCALE_DB_URL`.
8. The D11 pool decision is recorded with its measurement, either way.
9. Full suite green, mypy clean on touched packages, ruff clean, `cf check`
   clean; `docs/api/openapi.json` regenerated and the drift test passing.

## Verification Walkthrough (draft)

To be executed against prod `trading` at Phase 6 close and refined then. Every
ad-hoc query sets `statement_timeout` explicitly.

**1 — Before/after latency on the changed endpoint.** With the server running,
time `GET /api/v1/symbols/SPY`, `/AAPL`, `/F`, and one delisted symbol, five
calls each, against the pre-slice figures in D1. Expect ~25 ms warm against
2.7–4.0 s. Report medians, not best-of.

**2 — The read is actually bounded.** `EXPLAIN (ANALYZE, BUFFERS)` each of the
three statements from D2 with production parameters; confirm planning time in
single-digit ms and chunk exclusion in the plan. This is the criterion-1
evidence — a fast read of the wrong thing would satisfy step 1 alone.

**3 — Value equivalence at scale.** Run the merged read and the lazy `MIN/MAX`
for a 20+ symbol sample covering dense (SPY, AAPL), sparse, delisted, minute-only,
daily-only, and no-data instruments; diff both families at date grain. This is
criterion 2; any difference must be explained, not tolerated.

**4 — Quantify the D3 residual window.** Across the same sample, compute each
symbol's own coverage end against the universe edge and check whether any raw
bar falls in between. Report the count and the largest discrepancy. If it is not
negligible, the D3 trade needs revisiting before close, not after.

**5 — The freshness fix reports the truth.** `check_coverage_freshness` against
prod must return stale with `CONTENT_EDGE_TOO_OLD` and a lag matching the
observed 52 days. Then `mt data status`, `GET /api/v1/health`, and
`GET /api/v1/status` must all reflect it — and `GET /api/v1/symbols/SPY` must
still return the correct 2026-08-03 daily edge, demonstrating D2's independence
from the verdict.

**6 — The detection floor is pinned.** Run the new generic-guard test and show
it failing when `_raw_max`'s alignment is removed, then passing when restored.
A guard test that passes both ways proves nothing.

**7 — Load tier.** `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/` end to end.
Record each assertion's measured median and the bound derived from it. Confirm
assertion 2 (bars at the ceiling) measures *request* latency by showing the
measured wall clock exceeding the configured `statement_timeout` without a
`504` — the 186 D12b shape, now visible.

**8 — Concurrency and the pool decision.** Report assertion 3's per-request
latencies at concurrency 16 against a pool of 8, and record the D11 decision
with those numbers.

**9 — Contract unchanged.** `GET /api/v1/symbols/SPY` response body diffed
against a pre-slice capture: identical apart from the values D2 corrects.
`docs/api/openapi.json` drift test green.

## Risks

- **D6 flips production to a stale report.** Intended and correct, but it is a
  visible change to three operator surfaces at once, and it will stay that way
  until the repair slice lands. If the ERROR-per-uncached-read volume proves
  noisy in practice, the log level for the content-edge signal is the knob —
  not the check.
- **The D3 residual window is bounded by evidence, not by construction.** Step 4
  is what makes it safe to ship; if it turns out non-negligible, the fallback is
  per-symbol bounding for symbols whose coverage end is recent and the universe
  edge otherwise, at the cost of a branch on the read path.
- **Load-tier bounds calibrated on a fixture that is not production.** Explicitly
  acknowledged in D10. The bounds guard against regression in the *shape* of the
  code, not against prod's absolute numbers; the walkthrough carries the prod
  measurements that the fixture cannot.
