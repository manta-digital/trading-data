---
docType: slice-design
slice: candlestick-collection
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [263]
interfaces: [265, 266]
effort: 3
dateCreated: 20260825
dateUpdated: 20260825
status: not_started
---

# Slice Design: Candlestick Collection (264)

## Overview

This slice adds the second phase to the Kalshi collection pass: **1-minute candlesticks for every non-MVE market the catalog knows**, appended to `PASS_PHASES` behind the catalog phase and run by the existing `mt-kalshi-pass.timer` with no unit, timer, installer, wrapper, or `mt-run` change. Each pass brings every market's candle record up to the last fully elapsed minute (or through its close), driven by a per-market watermark in `kalshi.market_candle_state`, writing into a new `kalshi.candlesticks` table keyed on `(market_ticker, period, end_period_ts)` with conflict-ignore inserts. `mt data kalshi status` gains a candle block that answers the completeness definition's candle clause: which closed markets' candles fall short of close, which open markets are lagging, and which finalized markets can no longer be served by the live endpoint.

Two measurements taken during this design (live public API, 2026-08-26 ~02:30 UTC) shape everything below and are recorded in **Discovery Findings**: the candle endpoints cap each request on *requested* `tickers × periods` (5,000 single, 10,000 batch) and reject anything larger; and candles are **sparse** — Kalshi serves a candle only for a period in which the book or the tape moved, so 200 open markets over six hours returned 1,535 one-minute candles, not 72,000. The first fact makes a batch planner mandatory; the second is what makes 1-minute candles for the whole catalog affordable at all.

## Discovery Findings (live API, public mode, 2026-08-26)

The 261 design recorded the candlestick endpoints' *shape*; this slice needed their *limits* and the workload's *scale*. Every number below was measured through `KalshiClient` at the 300 req/min public budget; the probe scripts are not part of the deliverable.

### Endpoint limits (verified by provoking them)

| Endpoint | Cap | Evidence |
|---|---|---|
| `GET /series/{series}/markets/{ticker}/candlesticks` | **5,000 candles per request**, computed from the requested range, not from what would be served | HTTP 400 `"requested time range with candlesticks: 261343.966667, max candlesticks: 5000"` for a 181-day range at `period_interval=1`; a 5,000-minute window succeeds |
| `GET /markets/candlesticks` (batch, `market_tickers=` ≤ 100) | **10,000 candles per request** = `len(tickers) × periods_in_window`, again on the *request*, not the response | HTTP 400 `"requested candlesticks across all markets: 36000, max candlesticks: 10000"` for 100 tickers × 360 minutes; 100 tickers × 100 minutes (exactly 10,000) succeeds and served 269 candles |
| batch, unknown ticker | **silently omitted** from the response (3 of 4 returned), same as `GET /markets?tickers=` | probe |
| batch, market with no activity in the window | **present with an empty `candlesticks` list** — 100 requested → 100 entries, most empty | probe; 6 of 200 sampled open markets served no candle in six hours |
| batch response shape | `{"markets": [{"market_ticker": ..., "candlesticks": [...]}]}`; no `series_ticker` needed (the live single-market path needs it; the batch path does not) | probe + docs |
| `include_latest_before_start` | prepends a *synthetic* candle (OHLC null, `previous_price` = prior close); not used — only served candles are stored | docs |

### Candles are sparse

- A candle exists for a period only if something moved in it. Sample: 200 open markets (every 15th of the first three `status=open` pages), six hours, `period_interval=1` → **1,535 candles ≈ 1.28 per market-hour**; per-market maximum 115 in six hours; 11 of the 200 had ever traded.
- Short-lived markets are dense: three `KXCRYPTOLEAD15M` markets (15-minute life) served 15–16 one-minute candles each, first `end_period_ts = open_time + 1 min`, **last `end_period_ts = close_time + 1 min`** — the candle ending one period *after* close exists and is the last one. Fetch windows therefore run through `close_time + period`.
- Candle `end_period_ts` values are period-aligned (multiples of 60 s).

### Scale of the universe (post-sync catalog, `mve_filter=exclude`)

- **106,577 open markets** at probe time (94,817 at 262's survey two days earlier — the venue is growing). Lifetime (`close_time − open_time`): ≤ 15 min 17 · ≤ 1 h 2,218 · ≤ 1 d 4,427 · ≤ 7 d 16,348 · ≤ 30 d 18,961 · ≤ 180 d 21,178 · **> 180 d 43,428**; median ≈ 104 days. 45% (48,020) have ever traded. 2,495 close within the next hour; 1,671 were created within the last hour.
- If candles were dense, 1-minute history from each open market's open would be **8.2 billion** candles, growing 6.4 M per hour — the whole `minute_ohlcv` table again. Sparseness makes the real figure ~1.3 per market-hour (below), which is why this design collects minute candles for the *whole* catalog rather than a selected subset.
- The finalized backlog still on the live endpoints — everything settled since the historical cutoff (`market_settled_ts = 2026-06-25`, unchanged since 261) — is the 3.5 M markets 263's rehearsal captured, dominated by 15-minute crypto ladders (~15 candles each).

### What the numbers imply (derived, labelled as estimates)

| Quantity | Estimate | Basis |
|---|---|---|
| Steady-state requests per hourly pass (open markets) | ~1,070 (≈ 3.6 min at 300/min) | 106,577 / 100 tickers; a 60-minute window is 100 × 61 = 6,100 ≤ 10,000 |
| Requests to finish the finalized backlog once | ~35,000 (≈ 2 h at 300/min, spread over passes by the per-pass cap — Decision 6) | 3.5 M markets / 100 per batch; ladders closing in the same hour pack at 100 × ~75 periods |
| First-sight history for the open set (Decision 5, 24 h lookback) | ~17,800 requests ≈ 59 min, once | 1,440 periods → ⌊10,000 / 1,440⌋ = 6 tickers per request |
| Candle rows per day | ~4.4 M (open ~3.3 M + short-lived ladders ~1.1 M) ≈ **1.6 B/year** | 1.28/market-hour × 106.6 k × 24; 74 k settlements/day × 15 |
| Plain-table size | ~280 B/row with the primary key ≈ **1.2 GB/day, ~450 GB/year** | 16 NUMERIC columns + ticker + tuple header + PK entry |
| Compressed hypertable size | ~25 B/row ≈ **40 GB/year** | `minute_ohlcv`'s measured ~10 B/row floor for six numerics (journal 20260719), scaled |

The storage line is the reason Decision 4 recommends creating `kalshi.candlesticks` as a hypertable from day one, and it is flagged for PM ratification because the 260 slice plan says no hypertable is created in this plan.

## Value

- **The initiative's second time-series surface, at the granularity the PM required.** Kalshi probability moves play out on the order of minutes (260 slice plan, PM direction 20260824); after this slice every non-MVE market accumulates 1-minute candles from the moment the collector first sees it through its close, unattended, on the timer that already runs.
- **The completeness definition becomes answerable for candles.** `mt data kalshi status` reports, from persisted state alone, which closed markets are candle-complete through close, which are still pending, which open markets are lagging, and which finalized markets have fallen behind Kalshi's live/historical cutoff without candles — the honest "known-lost until 266" number.
- **Architectural enablement.** 266 (historical backfill) gets the exact set of markets whose candles it must fetch from `/historical/markets/{ticker}/candlesticks`, and a table it is idempotent against. 265 (trades) is independent and follows the same phase shape.

## Technical Scope

**In scope:**

- `KalshiClient.get_markets_candlesticks(...)` — the batch endpoint (`GET /markets/candlesticks`), with recorded fixtures for its success shape and its over-cap error body.
- `kalshi_005_candlesticks` migration: `kalshi.candlesticks` (hypertable per Decision 4, plain table if the PM declines), `coverage_from_ts` on `kalshi.market_candle_state`, corrected column comments, grants.
- The candle phase: `CandlesPhase` in `collection_pass.py`, appended to `PASS_PHASES`; `CandleSync` core (pending-market selection, batch planning, fetch, write, watermarks, events, result); `CandleRepository` (every SQL statement for candles and candle state).
- Pass preflight verifies the kalshi track's *ledger* is complete, not just that `sync_state` exists (Decision 8) — so a firing between install and migration exits 1 instead of crashing in the phase.
- `mt data kalshi status` candle block (Rich and `--json`); `print_pass_summary` dispatches per-phase renderers.
- Constants for every new comparison value; unit, fixture, and integration tests; CHANGELOG; runbook 100 Kalshi subsection paragraph.

**Explicitly out of scope:**

- Hourly/daily candles (Decision 1: derived locally from minute candles when wanted; the schema's `period` key keeps the door open).
- Market selectivity beyond MVE exclusion (Decision 2: none built; levers documented).
- Candles for markets behind the historical cutoff — reported here, fetched by 266.
- A deeper-history operator lever (`--lookback`-style replay) — the first-sight lookback is a constant; a replay lever, if ever wanted, follows 263 Decision 1 and lives on a `sync`-style command, not on `pass`.
- Compression policy, retention, and any change to unit files, timer cadence, `mt-run`, or the installer.
- Bounded concurrent fetching inside the phase (Decision 9: sequential, like 262; revisit with rehearsal evidence).

## Dependencies

### Prerequisites

- 263 complete and cut over on manta9000 (it is: `v0.9.0`, timer firing hourly at `:20` UTC). The candle phase inherits `KalshiRun`, the phase contract, the abort rule, the exit taxonomy, and the deployment path.
- 262's catalog populated: the phase's market set *is* `kalshi.markets` post-sync (`open_time`, `close_time`, `status`, `settlement_ts`), which is why the catalog phase runs first and an abort there skips this phase.
- 261's `Candlestick`/`PriceOhlc` models and `CandlePeriod`; `market_candle_state` (261 created it for this slice; its `watermark_ts` semantics are corrected here — Decision 3).
- TimescaleDB on the database host and on the test cluster (the `kalshi` schema already lives there; `create_hypertable` is available).

### Interfaces Required

- `KalshiRun` (263): `client`, locked `conn`, `sink`, `run_id`, `clock`.
- `PassPhase` / `PhaseReport` / `PASS_PHASES` (263); `SyncOutcome` and `EXIT_BY_OUTCOME` (262) — the candle phase's report classifies into the same four outcomes, so a pass whose candle phase storage-aborts exits 4 exactly as a catalog abort does.
- `SyncEvent` / `SyncEventSink` (262): the phase emits `phase_finished` and `item_error` with `phase="candles"`; no new event type.
- `CatalogRepository.transaction()` pattern: caller-owned transaction granularity, one per batch here.
- `KalshiClient.get_historical_cutoff()` (261): read once per phase run.
- `TRACKS["kalshi"]` (261): the ledger preflight enumerates its migration ids.

## Architecture

### Component Structure

```
cli/commands/kalshi.py        pass ─► run_context ─► CollectionPass(run, PASS_PHASES)
                              status ─► read_catalog_status + read_candle_status (new)
cli/commands/kalshi_render.py print_pass_summary → {CATALOG: print_phase_summary, CANDLES: print_candle_summary}
                              print_status gains the candle block

data/kalshi/collection_pass.py   PassPhaseName.CANDLES = "candles"
                                 CandlesPhase (PassPhase) — constructs CandleSync, classifies, reports
                                 PASS_PHASES = (CatalogPhase(), CandlesPhase())

data/kalshi/candle_sync.py       CandleSync — one run: cutoff → pending sets → plan → fetch/write per batch → state → events
data/kalshi/candle_plan.py       pure: CandleTarget, CandleBatch, target_window(), plan_batches()
data/kalshi/candle_repository.py CandleRepository — pending_live / pending_finishing / pending_backlog,
                                 insert_candles (conflict-ignore), advance_state, set_sync_state
data/kalshi/candle_types.py      CandleResult (+ to_dict), CandleSource Protocol, classification
data/kalshi/client.py            get_markets_candlesticks(tickers, start_ts, end_ts, period)  (batch)
data/kalshi/models.py            MarketCandlesticks, BatchCandlesticksResponse
data/kalshi/constants.py         MARKETS_CANDLESTICKS_PATH, COLLECTED_CANDLE_PERIOD, the caps, lookback, per-pass cap, …
data/kalshi/db.py                preflight: kalshi ledger complete (Decision 8)
data/kalshi/status.py            CandleStatus, read_candle_status
market/schema/migrations/kalshi.py   kalshi_005_candlesticks
scripts/record_kalshi_fixtures.py    --only candlesticks_batch, candlesticks_batch_over_cap
```

Module boundaries follow 262: the core (`candle_sync.py`) has no httpx, no typer, no SQL — it depends on a `CandleSource` Protocol (the two client calls it needs: `get_markets_candlesticks`, `get_historical_cutoff`) and a `CandleRepository`. The planner is pure so its packing rules are unit-tested without a database or a fake source. Each file stays under the ~300-line guideline by construction (the 262 catalog core needed the same split).

### Data Flow — the candle phase, one pass

1. **Cutoff.** `get_historical_cutoff().market_settled_ts` once; markets finalized before it are the live endpoint's *no longer served* set and are never requested (they are 266's). The observed cutoff is persisted in `sync_state['candlesticks'].watermark_ts` so `status` can report the behind-cutoff count without an API call.
2. **Pending sets** (three queries against the post-sync catalog, all over `kalshi.markets LEFT JOIN market_candle_state` at `period = COLLECTED_CANDLE_PERIOD`; a market is pending when `open_time < phase_start` and its watermark is NULL or below its target end — Decision 3):
   - **live** — `status ≠ finalized` (open, paused, closed-awaiting): unbounded; this is the steady state.
   - **finishing** — `finalized` *with* a state row whose watermark is short of `close_time + period`: unbounded; these are live markets that closed and settled since the last pass, and their tail must not queue behind history.
   - **backlog** — `finalized`, `settlement_ts ≥ cutoff`, *no* state row: ordered by `settlement_ts` ascending (nearest to falling behind the cutoff first), limited to `CANDLE_BACKLOG_REQUESTS_PER_PASS × CANDLE_BATCH_MAX_TICKERS` rows (Decision 6).
3. **Targets.** For each pending market, `target_window()` yields `[start, end)`: `start = watermark` if it has one, else `max(open_time, min(close_time, phase_start) − CANDLE_FIRST_SIGHT_LOOKBACK)` (Decision 5); `end = min(close_time + period, last_complete_period(phase_start))` where the last complete period is `phase_start` floored to the period minus one period (Decision 3). Targets with `start ≥ end` are already complete and are dropped (they can only arise from a `close_time` that moved earlier).
4. **Plan.** `plan_batches()` sorts targets by `start` and packs greedily: a batch's window is the union `[min start, max end)`; a target joins while `len(batch)+1 ≤ CANDLE_BATCH_MAX_TICKERS` and `(len(batch)+1) × periods(union) ≤ CANDLE_BATCH_MAX_CANDLES`. A single target longer than the cap is split into consecutive windows of at most `CANDLE_BATCH_MAX_CANDLES` periods before packing (a 24-hour lookback fits; a `close_time` far in the future never widens a window because `end` is clamped to the phase start). The packing is deterministic, pure, and the cap is asserted on every batch — a 400 from the API on this path is a planner bug and propagates as a provider abort (Decision 7).
5. **Fetch and write, one batch at a time** (Decision 9, sequential): `get_markets_candlesticks(tickers, start_ts, end_ts, period)`; then, **in one transaction**: insert every served candle with `ON CONFLICT DO NOTHING`; for each *requested* ticker present in the response (with or without candles) upsert `market_candle_state` with `watermark_ts = min(batch end, close_time + period)` and `coverage_from_ts = coalesce(existing, target start)`; a requested ticker *absent* from the response is an item error (unknown to Kalshi — the catalog is ahead of the venue, or the market vanished) and its state is untouched. Counts: `requests`, `markets_requested`, `markets_advanced`, `candles_fetched`, `candles_written`, `item_errors`. One INFO line per `CANDLE_PROGRESS_EVERY_REQUESTS` (the settled-window precedent, 263 Decision 8) so `mt-run follow kalshi` shows progress through a long first run.
6. **State and events.** After the last batch: `sync_state['candlesticks']` gets `last_full_sync_at = phase_start` (the phase completed over its whole pending set — the backlog cap does not count against this; the backlog's remaining size is a status figure) and `watermark_ts = cutoff`. `phase_finished` is emitted with the counts plus `backlog_remaining` and `behind_cutoff`. Classification: `ProviderError` → `PROVIDER_ABORT`, `psycopg.OperationalError` → `STORAGE_ABORT`, any item error → `PARTIAL`, else `OK` — the same `classify` shape as 262, on a `CandleResult`.

Under the abort rule (263 Decision 2), a catalog abort skips this phase; a candle abort cannot affect the catalog phase, which has already finished, so the catalog's watermark and awaiting set are never held hostage by candle trouble.

### State Management

- **`kalshi.market_candle_state (market_ticker, period)`** — one row per market once the phase has requested it. `watermark_ts` = *candles fetched through this instant* (the batch window's end, clamped to `close_time + period`), **not** the newest stored candle (Decision 3). `coverage_from_ts` (new) = the start of the first window ever requested for this market — equal to `open_time` when the market was first seen young, later when it was first seen with a lookback. `updated_at` as before.
- **`kalshi.sync_state['candlesticks']`** — `last_full_sync_at`: start of the last pass whose candle phase completed over its pending set; `watermark_ts`: the historical cutoff observed then (repurposed from "unused"; comment corrected in `kalshi_005`); `cursor`: unused.
- **No pass-level state**, as 263 established; a batch's candles and watermarks commit together, so an interrupted phase re-requests at most one batch on the next firing (conflict-ignore makes that free).
- The catalog owns `close_time`; if it moves later, the market becomes pending again because its target end moves; if it moves earlier, already-stored candles past the new close stay (they were served, they are real).

## Technical Decisions

1. **One period — 1-minute — collected; coarser periods derived locally, never fetched.** The PM's requirement is minute resolution; the architecture's stated trade-off (finest-and-derive multiplies storage vs. several periods duplicate data) was priced on the assumption of dense candles. Candles are sparse (Discovery Findings), so fetching 60-minute or daily candles would *not* be smaller per market by 60× — it would mostly re-serve the same moves at coarser boundaries. Hourly or daily bars, when analysis wants them, are a `time_bucket` over `kalshi.candlesticks` for the covered span. `COLLECTED_CANDLE_PERIOD = CandlePeriod.MINUTE` is the single definition; `period` stays in the primary key and in `market_candle_state` so a second period can be added without a schema change if that judgement changes. *Rejected:* hourly-for-all plus minute-for-selected — two watermark rows per market, two policies, and a selectivity rule (Decision 2) it turned out we do not need.

2. **No market selectivity: every non-MVE market gets minute candles.** The PM delegated selectivity to this slice, to be informed by the populated catalog. The catalog says: the API cost of covering every open market is ~1,070 requests per hourly pass (~3.6 minutes at the public budget), and sparseness puts storage at ~4.4 M rows/day for the whole catalog — a figure a hypertable absorbs (Decision 4). The information a selective rule would have to reason about (has it traded? will it?) is exactly what the candles record, and a market that starts trading after being skipped would have lost its early book history. So the rule is the catalog's rule — `mve_filter=exclude`, already applied upstream — and nothing else. *Levers, documented, not built:* a series deny-list (the 15-minute crypto ladders are ~1.1 M rows/day and 3.5 M of the backlog) and "traded-only" (`volume_fp > 0`, 45% of the open set). Either is a `WHERE` clause in `CandleRepository`'s pending queries plus a constant, and a separate PM decision.

3. **The watermark is the fetched window's end, and the last complete period is one minute behind the phase start.** 261's column comment defined `watermark_ts` as "`end_period_ts` of the newest stored candle". That definition fails on sparse data: an idle market would never advance and would be re-requested from the same start forever. `watermark_ts` therefore records *through when candles were requested and the response was stored* — advanced for every ticker present in the response, candles or not. The target end is `floor(phase_start, period) − period`: the period ending at the floor is complete, but a one-period guard keeps a still-settling candle out of a conflict-ignore table that never updates. A request re-includes the watermark instant itself (`start_ts = watermark`), because the endpoints' boundary inclusivity is not documented; the overlap costs nothing under `ON CONFLICT DO NOTHING`. `kalshi_005` rewrites the comment (comment-only precedent: `kalshi_004`).

4. **`kalshi.candlesticks` is created as a hypertable, chunked by 7 days — requires PM ratification.** The 260 slice plan records "no hypertable is created in this plan; promotion is a future decision after observed volume", on the PM's assessment that promotion is nearly certain but cheap at tens of millions of rows and a long maintenance window at hundreds of millions. The volume is now observed at design time: ~1.6 B rows/year, ~450 GB/year as a plain table, i.e. "hundreds of millions" inside the first quarter. Creating the hypertable on the empty table costs nothing and removes the guaranteed rewrite; `chunk_time_interval = 7 days` follows the 20260719 rule (wall-clock span ÷ 1,000–2,000 chunks: ~520 chunks per decade; ~30 M rows/chunk at current volume), defined once as `KALSHI_CANDLE_CHUNK_INTERVAL` and rendered into the migration like `MINUTE_OHLCV_CHUNK_INTERVAL`. The primary key `(market_ticker, period, end_period_ts)` already contains the partitioning column, and a hypertable may carry the foreign key to `kalshi.markets`. **No compression policy in this slice** — that is a measured, separately-ratified step (in place, like every other hypertable operation), and until it lands the PM should size disk against ~1.2 GB/day. **Fallback if declined:** the migration drops its one `create_hypertable` line; nothing else in the slice changes. Either way, per the extraction discipline, the table references nothing in `public`.

5. **First sight buys 24 hours of history; before that, nothing — except for markets seen young.** A market with no state row starts at `max(open_time, min(close_time, phase_start) − 24 h)`. A 15-minute ladder finalized between two passes is fetched from its open (it was seen "young"); a market that has been open for 100 days gets its last 24 hours, and `coverage_from_ts` records that honestly for `status`. The cost of the first pass under this rule is ~17,800 requests (six tickers per request at 1,440 periods) ≈ 59 minutes once — the "runs long once" the architecture accepted, and it overruns exactly one timer firing (absorbed by systemd's no-overlap). Full 1-minute history for the open set is 8.2 B candles if dense and unbounded in requests either way, so it is not on the table; a 6-hour lookback would cut the first pass to ~13 minutes at the price of 18 fewer hours of history per market. `CANDLE_FIRST_SIGHT_LOOKBACK = timedelta(hours=24)` is the single definition; changing it is a one-constant decision. *Rejected:* an operator `--lookback` on the pass — the timer's command takes no levers (263 Decision 1).

6. **The finalized backlog drains under a per-pass request cap; live markets are never capped.** 3.5 M finalized markets are still on the live endpoint and will fall behind the cutoff when it moves — 264 collects them because they are reachable now (capture before it disappears), oldest settlement first. Uncapped, that is ~35,000 requests ≈ 2 hours in one pass, during which the catalog goes stale. Capped at `CANDLE_BACKLOG_REQUESTS_PER_PASS = 1000` (~3.3 minutes), the backlog drains over ~35 hourly passes (about a day and a half) while every pass still refreshes the catalog and the live set, and the pass stays the bounded shape the architecture asked for. The cutoff has not moved in two months; if it moves during the drain, markets that cross it are simply excluded on the next pass and counted as behind-cutoff (266's list). Live and finishing markets are never subject to the cap: their pending set is bounded by the venue (~120 k markets ≈ 1,200 requests). `status` reports `backlog_remaining` so the drain is visible, not inferred.

7. **A provider error on a batch aborts the phase; only an omitted ticker is an item error.** The planner guarantees the cap, so a 400 on `/markets/candlesticks` is our bug or an API change — it must fail the pass visibly (exit 2), not be absorbed as 100 item errors that hide the cause. 429/5xx are the transport's transient path (bounded retry, then `ProviderTransientError` → abort). The one per-market failure the API actually signals is omission from the response, and that is the item error (`phase="candles"`, `ticker`, reason `"not served by the batch endpoint"`); the market's state is untouched, so it is retried next pass, and if the catalog later drops it the pending query no longer selects it.

8. **Pass preflight verifies the kalshi ledger, not one table.** `open_sync_connection` today checks `to_regclass('kalshi.sync_state')`. This slice's deploy is the first that adds a migration a *running timer* depends on: install the release, and the next firing runs the candle phase against a table that may not exist yet — an `UndefinedTable` (`psycopg.ProgrammingError`) is outside the storage taxonomy and would propagate as a bug. The preflight instead reads `schema_migrations` and requires every id in `TRACKS["kalshi"]`; a missing one is a `PreflightError` naming it (`kalshi track has pending migrations: kalshi_005_candlesticks — mt data migrate apply --track kalshi`) → exit 1, `Result=exit-code`, visible in `mt-run status`, harmless, retried next hour. The `sync_state` check becomes redundant and is removed. This also gives the walkthrough a clean deploy order: install → migrate → the next firing runs the phase.

9. **Sequential fetch and write, on the run's single connection.** 262 and 263 are sequential and reached 3.5 M rows in 45 minutes; the rate limiter (5 req/s) — not request latency — bounds this phase, and the pass holds one locked connection with caller-owned transactions, which a fetch pool would have to serialize its writes onto anyway. Rehearsal records requests-per-minute achieved for the phase; if it is materially below the budget, a bounded fetch pool ahead of the sequential writer is a follow-up with evidence, not a speculative addition here.

10. **Candles are fully structured; nested OHLC objects flatten to columns; conflict-ignore, never update.** `yes_bid`/`yes_ask`/`price` become `yes_bid_open_dollars … price_mean_dollars` (object name + field name, so the API's own names survive); `volume_fp NOT NULL`, `open_interest_fp` nullable, price columns nullable (a period with no trade serves `price: {}`). No `raw` column (261 Decision 6: candles are fully structured; unknown fields are dropped, the `extra="allow"` model notwithstanding). `INSERT … ON CONFLICT DO NOTHING` per the architecture's idempotent-write principle: a served candle for a completed period is treated as immutable, and the one-period guard in Decision 3 is what backs that assumption. A `CANDLE_COLUMNS` tuple defined once in `candle_repository.py` is the flattening map, and the migration parity test asserts it equals the table's column set.

11. **`status` reads the database only; the phase leaves it what it needs.** The behind-cutoff count needs the cutoff; rather than have `status` call the API (it never has), the phase persists the cutoff it used in `sync_state['candlesticks'].watermark_ts`. All candle status figures come from `markets ⋈ market_candle_state` and `sync_state`; nothing counts rows in `kalshi.candlesticks` (a count over a billion-row hypertable is neither fast nor, compressed, reliable — journal 20260720).

## Implementation Details

### Constants (`constants.py`, each defined once, each citing its decision)

```python
MARKETS_CANDLESTICKS_PATH = "/markets/candlesticks"          # batch endpoint (Discovery Findings)
COLLECTED_CANDLE_PERIOD = CandlePeriod.MINUTE                 # Decision 1
CANDLE_BATCH_MAX_TICKERS = 100                                # documented + verified
CANDLE_BATCH_MAX_CANDLES = 10_000                             # verified: tickers × periods, HTTP 400 above
CANDLE_SINGLE_MAX_CANDLES = 5_000                             # verified; recorded, the phase uses the batch path only
CANDLE_FIRST_SIGHT_LOOKBACK = timedelta(hours=24)             # Decision 5
CANDLE_BACKLOG_REQUESTS_PER_PASS = 1_000                      # Decision 6
CANDLE_PROGRESS_EVERY_REQUESTS = 100                          # one INFO line per this many requests
CANDLE_LAG_STALE_AFTER = timedelta(hours=2)                   # status: an open market two firings behind is "lagging"
KALSHI_CANDLE_CHUNK_INTERVAL = timedelta(days=7)              # Decision 4 (journal 20260719 rule)
```

`PassPhaseName.CANDLES = "candles"` is the phase's name in reports, events (`phase` field), the JSON summary, and the log lines.

### Client and models

- `KalshiClient.get_markets_candlesticks(tickers: Sequence[str], *, start_ts: int, end_ts: int, period_interval: CandlePeriod) -> list[MarketCandlesticks]` — `GET /markets/candlesticks?market_tickers=a,b,c&start_ts&end_ts&period_interval`. No cursor. The client passes the request through; the *planner* owns the cap (the client does not silently split — a caller that exceeds the cap gets the API's 400, per fail-loud).
- Models: `MarketCandlesticks(market_ticker: str, candlesticks: list[Candlestick])`, `BatchCandlesticksResponse(markets: list[MarketCandlesticks])`. `Candlestick`/`PriceOhlc` unchanged.
- `CandleSource` Protocol (in `candle_types.py`): `get_markets_candlesticks(...)`, `get_historical_cutoff()`. `KalshiClient` satisfies it structurally; tests use a fixture-backed fake that records every received query (the `FakeCatalogSource` pattern in `test/unit/data/kalshi/kalshi_support/`).

### Planner (`candle_plan.py`, pure)

```python
@dataclass(frozen=True)
class CandleTarget:  ticker: str; start: datetime; end: datetime; close_end: datetime  # close_time + period
@dataclass(frozen=True)
class CandleBatch:   tickers: tuple[str, ...]; start: datetime; end: datetime

def last_complete_period(now: datetime, period: CandlePeriod) -> datetime   # floor − one period (Decision 3)
def target_window(market, state, *, phase_start, period, lookback) -> CandleTarget | None   # None when complete
def plan_batches(targets: Sequence[CandleTarget], *, period, max_tickers, max_candles) -> list[CandleBatch]
```

`plan_batches` asserts `len(tickers) × periods(start, end) ≤ max_candles` for every batch it returns (a violated invariant raises — never a silent trim). A property-style unit test packs random target sets and checks the invariant plus "every target is covered by the union of its batches' windows".

### Repository (`candle_repository.py`)

- `pending_live(period, phase_start) -> list[PendingMarket]`, `pending_finishing(period)`, `pending_backlog(period, cutoff, limit)` — the three queries of Data Flow step 2, each returning `(ticker, open_time, close_time, watermark_ts)`; the backlog query orders by `settlement_ts`. Every status value is a bound parameter from `MarketStatus` (262's rule: no lifecycle literal in SQL text).
- `insert_candles(rows) -> int` — multi-row `INSERT … ON CONFLICT DO NOTHING` chunked under the bind-parameter ceiling like `CatalogRepository._upsert`; returns rows written.
- `advance_state(period, advances: Sequence[(ticker, watermark, coverage_from)])` — one multi-row upsert: `watermark_ts = EXCLUDED.watermark_ts`, `coverage_from_ts = COALESCE(state.coverage_from_ts, EXCLUDED.coverage_from_ts)`, `updated_at = now()`.
- `set_sync_state(period_start, cutoff)` — `last_full_sync_at` and `watermark_ts` on `sync_state['candlesticks']` (via `CatalogRepository`'s existing `_set_state_column` shape, or its own upsert — one statement, no duplication of the catalog's).
- `transaction()` as `CatalogRepository.transaction()` — the sync core wraps each batch's insert + advance in one.

Storage failure taxonomy as 262: `IntegrityError` on a batch (an FK the catalog just lost) rolls the batch back and rewrites per market, offenders become item errors; `OperationalError` propagates (storage abort); any other `psycopg.Error` propagates (bug).

### `collection_pass.py`

```python
class PassPhaseName(StrEnum):
    CATALOG = "catalog"
    CANDLES = "candles"            # 265: TRADES

class CandlesPhase:
    name = PassPhaseName.CANDLES
    async def run(self, run: KalshiRun) -> PhaseReport:
        sync = CandleSync(run.client, CandleRepository(run.conn), run.sink, run_id=run.run_id, clock=run.clock)
        ... same try/except/classify shape as CatalogPhase ...
        return PhaseReport(name=self.name, outcome=classify_candles(sync.result, failure),
                           summary=sync.result.to_dict(), duration_ms=..., error=...)

PASS_PHASES: tuple[PassPhase, ...] = (CatalogPhase(), CandlesPhase())
```

The existing test that asserts the catalog phase is first stays true; a new one asserts the candle phase is second and that `PASS_PHASES` names are unique.

### `CandleResult.to_dict()` (the phase's `summary`)

```json
{"run_id": "...", "started_at": "...", "period": 1, "cutoff": "2026-06-25T00:00:00+00:00",
 "pending": {"live": 106577, "finishing": 2412, "backlog": 100000, "backlog_remaining": 3412000},
 "requests": 2166, "markets_requested": 208989, "markets_advanced": 208985,
 "candles_fetched": 1493210, "candles_written": 1493201,
 "item_errors": [{"ticker": "...", "reason": "not served by the batch endpoint"}],
 "duration_ms": 412803, "error": null}
```

### Migration `kalshi_005_candlesticks`

```sql
CREATE TABLE IF NOT EXISTS kalshi.candlesticks (
    market_ticker            TEXT        NOT NULL REFERENCES kalshi.markets (ticker),
    period                   SMALLINT    NOT NULL,
    end_period_ts            TIMESTAMPTZ NOT NULL,
    yes_bid_open_dollars     NUMERIC,  yes_bid_high_dollars NUMERIC,  yes_bid_low_dollars NUMERIC,  yes_bid_close_dollars NUMERIC,
    yes_ask_open_dollars     NUMERIC,  yes_ask_high_dollars NUMERIC,  yes_ask_low_dollars NUMERIC,  yes_ask_close_dollars NUMERIC,
    price_open_dollars       NUMERIC,  price_high_dollars   NUMERIC,  price_low_dollars   NUMERIC,  price_close_dollars   NUMERIC,
    price_previous_dollars   NUMERIC,  price_mean_dollars   NUMERIC,
    volume_fp                NUMERIC   NOT NULL,
    open_interest_fp         NUMERIC,
    PRIMARY KEY (market_ticker, period, end_period_ts),
    CONSTRAINT candlesticks_period_check CHECK (period IN (1, 60, 1440))   -- rendered from CandlePeriod
);
SELECT create_hypertable('kalshi.candlesticks', 'end_period_ts',
                         chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);   -- Decision 4
ALTER TABLE kalshi.market_candle_state ADD COLUMN IF NOT EXISTS coverage_from_ts TIMESTAMPTZ;
COMMENT ON COLUMN kalshi.market_candle_state.watermark_ts IS 'candles requested and stored through this instant (window end, clamped to close_time + period) — NOT the newest stored candle: Kalshi serves no candle for an idle period (slice 264, Decision 3)';
COMMENT ON COLUMN kalshi.market_candle_state.coverage_from_ts IS 'start of the first window ever requested; equals open_time only when the market was first seen young (slice 264, Decision 5)';
COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS '... candlesticks: market_settled_ts of the historical cutoff observed by the last candle phase (slice 264, Decision 11)';
GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.candlesticks TO trading_app;
```

Additive and idempotent; no down-migration (the track's convention). Applied to production by the operator from the dev checkout with the maintenance credential (runbook 100 *Update procedure*), **before** the first firing after the install (Decision 8 makes the wrong order harmless).

### Preflight (`db.py`)

`open_sync_connection` replaces the `to_regclass` probe with: `SELECT migration_id FROM schema_migrations WHERE migration_id = ANY(%s)` over the ids of `TRACKS["kalshi"]`; any id missing → `PreflightError(f"kalshi track has pending migrations: {', '.join(missing)} — {APPLY_HINT}")`. A database without `schema_migrations` at all is caught by the same query's `UndefinedTable` and reported as the track not applied. `TRACK_NOT_APPLIED`'s wording is updated; the lock step is unchanged.

### CLI and rendering

- `mt data kalshi pass` — unchanged surface. The pass table gains a `candles` row; `print_pass_summary` looks up a renderer by `PassPhaseName` (`_SUMMARY_RENDERERS`), and `print_candle_summary(summary)` prints requests, markets requested/advanced, candles fetched/written, pending live/finishing/backlog (+ remaining), item errors.
- `mt data kalshi status` — `read_candle_status(conn) -> CandleStatus | None` (None until the phase has run once; Rich prints "Candlesticks: never collected", JSON `"candles": null`). Fields, every one a persisted fact:
  - `period_minutes`, `last_phase_at`, `cutoff_observed`
  - `markets_tracked` — rows in `market_candle_state`
  - `open_lagging` / `open_oldest_watermark` — open (`status ≠ finalized`, `close_time > now`) markets whose watermark is older than `now − CANDLE_LAG_STALE_AFTER`
  - `closed_short_of_close` — markets past close (any status), not behind the cutoff, with a state row whose watermark `< close_time + period` (should be ~0 between passes; an anomaly if it persists)
  - `backlog_remaining` — finalized since the cutoff, no state row (drains at the cap; expected to reach 0 within ~2 days of the first deploy)
  - `behind_cutoff_uncollected` — finalized before the cutoff, no state row — 266's input, reported as known-lost-live
  - `partial_history` — state rows whose `coverage_from_ts > open_time`
  - `complete_through_close` — state rows with watermark `≥ close_time + period` (the completeness clause, satisfied)
- Rich block:

```
Kalshi candlesticks        period 1 min   last phase 2026-08-27 14:24:11 UTC (36 min ago)   cutoff 2026-06-25
  tracked             3,612,404 markets   complete through close 3,489,910   partial history 104,222
  open lagging        0 (oldest watermark 2026-08-27 14:19 UTC)
  short of close      0        backlog remaining 0        behind cutoff, uncollected 41,203
```

### Fixtures and recorder

- `--only candlesticks_batch` — a real batch response: ≥ 3 open tickers over the last hour at `period_interval=1`, chosen so at least one entry is empty (`markets_with_no_activity` are common) and one has candles with `price: {}` — the sparse shapes the code must handle.
- `--only candlesticks_batch_over_cap` — the HTTP 400 body for 100 tickers × 360 minutes, saved as `error_400_candles_cap.json`, driving the transport-level test that the error is *permanent* and the planner test that it is never provoked.
- Existing `candlesticks.json` (single-market path) stays for the 261 client test.

### Runbook 100 and CHANGELOG

- Kalshi subsection gains one paragraph: the pass now has two phases; the first firing after this release runs ~1 hour (first-sight history), then the backlog drains at ~3 minutes per pass for a day or two; `kalshi_005` must be applied during the update (a firing before that exits 1 with the migration named — expected, not a defect); the new `status` figures and what "backlog remaining" vs "behind cutoff" mean. The *Update procedure* already says apply migrations after installing and before the next firing; this slice does not add the 263 "run the installer twice" step because no unit changes.
- CHANGELOG under `[Unreleased]`: candle phase, status block, migration, the preflight change (a firing during a half-finished update now exits 1 with a named migration instead of failing inside a phase).

### Tests

- **Unit — planner (`test_candle_plan.py`):** `last_complete_period` alignment and the one-period guard; `target_window` for each first-sight case (young market → open; old market → lookback; `close_time` in the past → clamped; complete → `None`; state present → watermark); packing: never exceeds either cap, splits an over-long single target into consecutive windows, unions windows correctly, deterministic order; randomized invariant test.
- **Unit — core (`test_candle_sync.py`):** with `FakeCandleSource` and an in-memory fake repository: live/finishing/backlog ordering and the cap applied to backlog only; a market present with zero candles advances; an omitted ticker is an item error and does not advance; `coverage_from_ts` set once; `sync_state` written after the last batch; classification for provider abort / storage abort / partial / ok; events (`phase_finished` counts, one `item_error` per omission, shared `run_id`); progress line cadence.
- **Unit — client/models/fixtures:** `get_markets_candlesticks` request parameters (tickers joined, ints), `BatchCandlesticksResponse` parses the recorded batch fixture including empty entries; the 400 fixture classifies as `ProviderPermanentError`; `test_constants.py` checks each new constant is unique and cited.
- **Unit — pass:** `PASS_PHASES == (catalog, candles)` in that order; `print_pass_summary` dispatches both renderers; JSON round-trips.
- **Integration (`kalshi_db`):** `kalshi_005` applies and re-applies; `kalshi.candlesticks` is a hypertable with the configured chunk interval (or a plain table, per the PM's Decision 4 call); `CANDLE_COLUMNS` parity with the table; conflict-ignore on a duplicate natural key; the three pending queries against fixture markets (young finalized ladder → backlog with start = open; a market whose `close_time` moved later becomes pending again; a market finalized before the cutoff is never selected and counts as behind-cutoff); an end-to-end `pass` on a throwaway database runs both phases, writes candles and state, and a second pass writes nothing; preflight exit 1 names a missing migration (drop `kalshi_005` from the ledger row set on a throwaway copy); `status` shape and counts.
- **Deploy drift guard:** unchanged (no unit or `KINDS` change); the CliRunner check that `pass` exists still passes.
- Gates as 263: unit tier, kalshi integration tier via `scripts/run_tests.py`, ruff scoped to touched files, mypy, strict pyright on the kalshi package, `git diff main -- pyproject.toml uv.lock` empty.

## Integration Points

### Provides to Other Slices

- **265 (trades):** the second worked example of a `PassPhase` with its own core/repository/types split, a per-market pending query over the catalog, and a per-pass cap on history; `PassPhaseName.TRADES` appends after `CANDLES`. The ledger preflight (Decision 8) covers 265's migration for free.
- **266 (historical backfill):** `behind_cutoff_uncollected` — the exact market set to fetch from `/historical/markets/{ticker}/candlesticks` (no series segment); the same `kalshi.candlesticks` table and conflict-ignore insert (idempotent against anything 264 stored); `coverage_from_ts` telling 266 where live coverage begins for markets 264 saw late, should it ever extend history backwards for open markets.
- **Future hypertable compression / retention:** a hypertable already in place (if Decision 4 is ratified), chunked so the policy is a one-statement addition.

### Consumes from Other Slices

- 263's phase contract, abort rule, and the `run_id`-shared event stream, unchanged.
- 262's catalog columns (`open_time`, `close_time`, `status`, `settlement_ts`) and the rule that the catalog phase runs first; 262's `Surface.CANDLESTICKS` row in `sync_state`.
- 261's `Candlestick` model, `CandlePeriod`, `market_candle_state`, `get_historical_cutoff`, transport error taxonomy.

## Success Criteria

1. **The pass has two phases.** `mt data kalshi pass --json` reports `phases[].name == ["catalog", "candles"]`; a catalog abort reports the candle phase `skipped`; a candle abort leaves the catalog phase's outcome and state intact.
2. **Candles land under the natural key.** On a throwaway database with a synced catalog, one pass writes rows into `kalshi.candlesticks` for markets that had activity, and `market_candle_state` rows for every market it requested — including markets that served no candle.
3. **A second pass writes only what is new.** Immediately re-run, `candles_written` covers only periods past the previous watermarks (zero when nothing moved), and no duplicate row exists (primary key + conflict-ignore, proven by an integration test that re-inserts a batch).
4. **The planner never exceeds the caps.** Every batch satisfies `tickers ≤ 100` and `tickers × periods ≤ 10,000` (unit invariant); the rehearsal journal shows zero HTTP 400 on `/markets/candlesticks`.
5. **First sight follows Decision 5.** A market first seen with `open_time` within the lookback has `coverage_from_ts == open_time`; one first seen older has `coverage_from_ts == phase_start − 24 h` and counts in `partial_history`.
6. **Closed markets complete through close.** A market that closes between passes reaches `watermark_ts ≥ close_time + period` on the next pass and counts in `complete_through_close`; it is never requested again.
7. **The backlog drains under the cap, oldest first, and is visible.** On the rehearsal database, the finalized backlog shrinks by at most `1000 × 100` markets per pass, in `settlement_ts` order, and `status` reports `backlog_remaining` decreasing pass over pass.
8. **Behind-cutoff markets are never requested and are counted.** Markets with `settlement_ts < cutoff` appear in `behind_cutoff_uncollected`, have no state row, and produce no request (fake-source assertion + integration query).
9. **An omitted ticker is a partial, not an abort.** A batch response missing a requested ticker yields one `item_error` event with `phase="candles"`, the pass exits 3, and the market's state is untouched.
10. **Preflight names the missing migration.** With `kalshi_005` absent from the ledger, `mt data kalshi pass` exits 1 with a message naming `kalshi_005_candlesticks`; after `mt data migrate apply --track kalshi`, the same command runs.
11. **`status` answers the candle clause from the database alone.** `mt data kalshi status --json` carries the `candles` object with every field in *CLI and rendering*, with no API call (a unit test asserts `status.py` imports neither the client nor the transport).
12. **Production: the timer runs the phase unattended.** After the release is installed and `kalshi_005` applied, the next firing's journal shows `kalshi pass started … phases=catalog,candles`, a `candles` phase outcome `ok`, `Result=success`; `mt-run data kalshi status` shows `markets_tracked > 0` and `open_lagging == 0` on the firing after that.

## Verification Walkthrough

Draft; refined with observed output after Phase 6, as 263's was. Steps 1–5 run on a **throwaway database on the test cluster** (runbook 400 pattern; `MT_TIMESCALE_DB_URL` points at it for these commands only and the production URL is never in the shell). Steps 6–8 are on manta9000 and are the PM's.

**1. Throwaway database, migrated, with a small catalog.**

```bash
# create the database and apply the kalshi track (kalshi_001 … kalshi_005) as 263's rehearsal did
uv run mt data migrate status --track kalshi      # → 0 pending, kalshi_005_candlesticks listed as applied
uv run psql "$MT_TIMESCALE_DB_URL" -c "select hypertable_name from timescaledb_information.hypertables where hypertable_schema='kalshi'"
#    → candlesticks                                 (Decision 4; absent if the PM declined)
# a catalog small enough to rehearse in minutes: full live walk plus a six-hour settled window
uv run mt data kalshi sync --settled-since "$(date -u -d '6 hours ago' +%FT%TZ)"
#    → exit 0; ~180k live markets, ~18k settled
```

**2. Preflight names a missing migration (Criterion 10).**

```bash
uv run psql "$MT_TIMESCALE_DB_URL" -c "delete from schema_migrations where migration_id='kalshi_005_candlesticks'"
uv run mt data kalshi pass
#    → Error: kalshi track has pending migrations: kalshi_005_candlesticks — mt data migrate apply --track kalshi   (exit 1)
uv run mt data migrate apply --track kalshi        # re-applies idempotently, ledger row restored
```

**3. First pass: both phases, first-sight history, the backlog cap (Criteria 1, 2, 5, 7, 8).**

```bash
uv run mt data kalshi pass --events-file candles-pass1.jsonl
#    journal: kalshi pass started run_id=… mode=public budget=300/min phases=catalog,candles
#             candles: pending live 106,xxx finishing 0 backlog 100,000 (remaining ~xx,xxx) cutoff 2026-06-25
#             candles: 100 requests … / 200 requests … (progress lines)
#             kalshi pass finished outcome=ok … phases: catalog=ok candles=ok
#    expect ~1 h at the public budget (Decision 5) — this is the once-only cost
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.market_candle_state"          # ≈ live + 100k backlog
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) filter (where coverage_from_ts > m.open_time) partial, count(*) filter (where s.watermark_ts >= m.close_time + interval '1 minute') complete from kalshi.market_candle_state s join kalshi.markets m on m.ticker = s.market_ticker"
uv run psql "$MT_TIMESCALE_DB_URL" -c "select market_ticker, end_period_ts, yes_bid_close_dollars, price_close_dollars, volume_fp from kalshi.candlesticks order by end_period_ts desc limit 5"
jq -r 'select(.event_type=="phase_finished" and .phase=="candles") | .counts' candles-pass1.jsonl
```

**4. Second pass: incremental only; a closed market completes (Criteria 3, 6).**

```bash
uv run mt data kalshi pass --json | jq '.phases[] | {name, outcome, w: .summary.candles_written, r: .summary.requests}'
#    → catalog ok; candles ok with requests ≈ 1,100 + ≤ 1,000 backlog, candles_written small (only new periods)
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.candlesticks c join kalshi.candlesticks d using (market_ticker, period, end_period_ts) where c.ctid <> d.ctid"   # → 0
```

**5. Status and the omission path (Criteria 9, 11).**

```bash
uv run mt data kalshi status                                   # candle block as in *CLI and rendering*
uv run mt data kalshi status --json | jq .candles
# "no API call" is a structural fact, not an observation: status.py imports neither the client nor the transport,
# and the unit test `test_status_module_makes_no_requests` asserts that import boundary.
# omission path: integration test `test_omitted_ticker_is_item_error` — the fake source drops one requested ticker;
# the pass exits 3 and the ticker's state row is absent.
```

**6. Production deploy (PM).** Runbook 100 *Update procedure*: tag `v0.10.0` → `install-production.sh --ref v0.10.0` (once — no new units) → from the dev checkout, `uv run mt data migrate status --track kalshi` (1 pending) → `uv run mt data migrate apply --track kalshi` (maintenance credential) → `status` shows 0 pending. If the timer fires between install and apply, `mt-run status` shows `last run: exit-code, exit=1` and the journal names `kalshi_005_candlesticks` — expected (Decision 8).

**7. First supervised firing (Criterion 12).**

```bash
sudo mt-run kalshi                     # or wait for :20; live output; Ctrl-C detaches
mt-run follow kalshi                   # progress lines every 100 requests; expect ~1 h for the first run
systemctl show mt-kalshi-pass.service -p Result -p ExecMainStatus     # Result=success, ExecMainStatus=0
journalctl -u mt-kalshi-pass.service --grep 'kalshi pass finished' -n 1  # phases: catalog=ok candles=ok
journalctl -u mt-kalshi-pass.service -o cat --since -2h | grep -c 'HTTP 4'   # 0 on /markets/candlesticks
```

**8. Steady state, one day later.**

```bash
mt-run data kalshi status              # open lagging 0; backlog remaining → 0 within ~35 firings; behind-cutoff count stable
journalctl -u mt-kalshi-pass.service --since -24h | grep -c retry     # the Decision-7-of-263 budget evidence, now with ~2k more requests per pass
```

### Success criteria — where each is proven

| # | Unit | Integration | Rehearsal / host |
|---|---|---|---|
| 1 | pass order, dispatch | pass end-to-end | step 3 journal |
| 2 | core: advance on empty | insert + state | step 3 queries |
| 3 | — | duplicate re-insert | step 4 |
| 4 | planner invariant | — | step 7 grep |
| 5 | `target_window` cases | pending queries | step 3 partial/complete |
| 6 | core: finishing set | close-then-pass | step 4 |
| 7 | core: cap on backlog only | backlog ordering | step 3/4 status |
| 8 | core: cutoff exclusion | behind-cutoff query | step 5 status |
| 9 | core: omission | `test_omitted_ticker…` | — |
| 10 | — | ledger preflight | step 2 |
| 11 | status dataclass | status queries | step 5 |
| 12 | — | — | steps 7–8 |

## Risk Assessment

- **Rate budget exposure grows by ~2k requests per pass.** The design-time probes drew a handful of 429s on `/markets` at 300/min (retried and recovered); 263's cutover drew none. The candle phase roughly triples per-pass request volume. Mitigations already in place: bounded retry with backoff, `MT_KALSHI_REQUESTS_PER_MINUTE`, and authenticated mode at 1,000/min (PEM placement rule in the runbook). Rehearsal records `grep -c retry` for the phase; if retries are routine, the PM's lever is a configuration change, not code.
- **Storage growth is large and PM-visible.** ~1.2 GB/day uncompressed until a compression policy is ratified. Decision 4 (hypertable now) is the structural mitigation; the PM decides.
- **Kalshi may revise a completed candle.** Conflict-ignore keeps the first version. The one-period guard (Decision 3) limits exposure to late-arriving trades; if revisions are observed in rehearsal (compare a re-fetched window against stored rows), the fix is `DO UPDATE` on the value columns — a one-statement change that the design deliberately does not pre-empt.

## Open decisions for the Project Manager

1. **Decision 4 — hypertable at creation.** Ratify (recommended) or decline (plain table; one line removed from the migration). Either way no compression policy ships in this slice.
2. **Decision 5 — 24-hour first-sight lookback** (≈ 1-hour first pass) versus 6 hours (≈ 13 minutes, less history). Recommended: 24 h.
3. **Decision 2 — no market selectivity.** Confirm, or name a series deny-list / traded-only rule to build instead.

## Implementation Notes

### Development Approach

Sections, each a checkpoint commit (memory: commit per section):

1. Client + models + fixtures (`get_markets_candlesticks`, batch fixtures, error fixture) — testable alone.
2. Migration `kalshi_005` + parity test + preflight ledger check — the pass keeps working on a migrated database; on an unmigrated one it now exits 1 with a name.
3. Planner (pure) with its tests.
4. Repository + integration tests for the pending queries and inserts.
5. `CandleSync` core with fakes; `CandlesPhase`; `PASS_PHASES` append; renderer dispatch.
6. `status` block (queries, dataclass, Rich, JSON).
7. Rehearsal on the test cluster (walkthrough 1–5), notes file `user/notes/2026-MM-DD-264-rehearsal.md`, then runbook + CHANGELOG, then the host steps.

Scope every `ruff`/format invocation to the files touched (263 process note).

### Special Considerations

- The pending queries scan `kalshi.markets` (3.5 M+ rows) once per pass each; rehearsal records their wall time. If they dominate the phase, a partial index on `markets (settlement_ts) WHERE status = 'finalized'` is the first lever — rendered from `MarketStatus`, never a literal.
- `coverage_from_ts` is set by `COALESCE` so a replayed or re-run window can never move it later.
- The phase logs the cutoff it used at INFO on every run; a cutoff that jumps forward is the signal that 266 has become urgent.
