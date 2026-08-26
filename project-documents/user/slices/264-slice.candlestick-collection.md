---
docType: slice-design
slice: candlestick-collection
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [263]
interfaces: [265, 266]
effort: 3
dateCreated: 20260825
dateUpdated: 20260826
status: not_started
---

# Slice Design: Candlestick Collection (264)

## Overview

This slice adds the second phase to the Kalshi collection pass: **1-minute candlesticks for the markets the collection rule selects** — by default, markets that traded in the last 24 hours, excluding the Sports and Mentions categories (Decision 2, PM-ratified 20260826); the rule is **configuration** (`MT_KALSHI_CANDLE_*` settings), because this project ships publicly as a collector and other users will want other categories — appended to `PASS_PHASES` behind the catalog phase and run by the existing `mt-kalshi-pass.timer` with no unit, timer, installer, wrapper, or `mt-run` change. Each pass brings every selected market's candle record up to the last fully elapsed minute (or through its close), driven by a per-market watermark in `kalshi.market_candle_state`, writing into a new `kalshi.candlesticks` hypertable keyed on `(market_ticker, period, end_period_ts)` with conflict-ignore inserts. `mt data kalshi status` gains a candle block that answers the completeness definition's candle clause for selected markets and makes the exclusions visible: which closed markets' candles fall short of close, which open markets are lagging, which finalized markets can no longer be served by the live endpoint, and how many were excluded by rule.

Three measurements taken during this design (live public API and a throwaway test-cluster database, 2026-08-26) shape everything below and are recorded in **Discovery Findings**: the candle endpoints cap each request on *requested* `tickers × periods` (5,000 single, 10,000 batch) and reject anything larger; candles are **sparse** (a candle exists only for a minute in which the book or the tape moved) and **97% of them carry no trade**; and a stored candle costs 262 B uncompressed / 61 B compressed. The first fact makes a batch planner mandatory; the second two are why collecting *everything* would be 600 GB/year of market-maker re-quotes, and why the rule exists.

## Discovery Findings (2026-08-26)

The 261 design recorded the candlestick endpoints' *shape*; this slice needed their *limits*, the workload's *scale*, and its *cost*. Numbers below were measured through `KalshiClient` at the 300 req/min public budget; the probe scripts are not part of the deliverable.

### Endpoint limits (verified by provoking them)

| Endpoint | Cap | Evidence |
|---|---|---|
| `GET /series/{series}/markets/{ticker}/candlesticks` | **5,000 candles per request**, computed from the requested range | HTTP 400 `"requested time range with candlesticks: 261343.966667, max candlesticks: 5000"` for a 181-day range at `period_interval=1`; a 5,000-minute window succeeds |
| `GET /markets/candlesticks` (batch, `market_tickers=` ≤ 100) | **10,000 = `len(tickers) × periods_in_window`**, on the *request*, not the response | HTTP 400 `"requested candlesticks across all markets: 36000, max candlesticks: 10000"` for 100 tickers × 360 minutes; 100 × 100 (exactly 10,000) succeeds and served 269 candles |
| batch, unknown ticker | **silently omitted** from the response, as `GET /markets?tickers=` does | probe |
| batch, market with no activity in the window | **present with an empty `candlesticks` list** | probe; 100 requested → 100 entries, most empty |
| batch response shape | `{"markets": [{"market_ticker": …, "candlesticks": […]}]}`; no `series_ticker` needed | probe + docs |
| `include_latest_before_start` | prepends a *synthetic* candle (OHLC null); not used — only served candles are stored | docs |

### Candles are sparse, and almost all are quotes

- A candle exists for a period only if something moved in it. 200 open markets × 6 h at `period_interval=1` → **1,535 candles**, not 72,000 (≈ 1.3 per market-hour across the open set; 7.3 per market-hour for markets that traded in the last day).
- **Only 3% of served candles carry a trade** (`volume_fp > 0`); the other 97% record bid/ask OHLC moves — a market maker re-quoting. 55% of open markets (57.5 k of 104.5 k) have never traded and still emit ~1.7 M such candles a day.
- Short-lived markets are dense: 15-minute crypto ladders serve 15–16 one-minute candles each, first `end_period_ts = open_time + 1 min`, **last `end_period_ts = close_time + 1 min`** — fetch windows run through `close_time + period`.
- `end_period_ts` values are period-aligned (multiples of 60 s).

### The universe by Kalshi's series category (open markets 104,533 in 3,772 series; settlements 75,724 in the last 24 h)

| category | open | traded ever | traded 24 h | candles / market-h (traded 24 h) | rows/day if collected (traded 24 h) | settled/day | median life (min) |
|---|---|---|---|---|---|---|---|
| **Sports** | **60,655** | 20,631 | 9,357 | 6.3 | **1,412 k** | 12,006 | 1,205 |
| Elections | 11,353 | 6,540 | 1,070 | 3.6 | 92 k | 23 | 9,497 |
| Financials | 10,060 | 4,623 | 975 | 5.2 | 121 k | 4,220 | 1,260 |
| Entertainment | 8,081 | 6,031 | 1,604 | 4.7 | 181 k | 647 | 9,765 |
| Crypto (mostly 15-min ladders) | 4,034 | 817 | 568 | 15.0 | 205 k | 51,338 | 60 |
| Economics | 3,624 | 3,003 | 669 | 5.1 | 83 k | 107 | 1,079 |
| Politics | 2,260 | 2,062 | 620 | 4.6 | 68 k | 43 | 15,660 |
| Commodities | 1,463 | 713 | 483 | 19.1 | 221 k | 5,459 | 119 |
| Climate and Weather | 945 | 746 | 489 | 11.2 | 132 k | 1,768 | 60 |
| Science and Technology | 966 | 822 | 287 | 3.5 | 24 k | 0 | — |
| **Mentions** (category) | 157 | 157 | 110 | 6.1 | 16 k | 44 | 571 |
| **mention-titled series in other categories** (188 series, e.g. `KXFEDMENTION`, `KXTRUMPSAY`, `KXEARNINGSMENTIONNVDA`) | 327 | 322 | 246 | 7.2 | 42 k | 97 | 1,411 |
| Companies / Social / World / unknown | 588 | 556 | 85 | — | 6 k | 2 | — |

Two facts the rule depends on: Kalshi's `series.category` is a clean, venue-maintained partition (Sports is 58% of the catalog by market count); and "mention" markets are only partly in the `Mentions` category — 188 series elsewhere carry *mention*/*say* in their titles, so the rule needs a title/ticker pattern as well as the category.

### Storage cost, measured

41,846 real candles (782 markets that traded in the last 24 h) inserted into a throwaway hypertable on the test cluster (same DDL as `kalshi_005`, then `compress_chunk` with `segmentby market_ticker, orderby end_period_ts DESC`; database dropped afterwards):

| | heap | PK index | toast | **total** |
|---|---|---|---|---|
| uncompressed | 142 B/row | 120 B/row | — | **262 B/row** |
| compressed | 15 B/row | 3 B/row | 43 B/row | **61 B/row** (4.3×) |

Sixteen nullable NUMERIC columns and a long text key compress worse than `minute_ohlcv`'s six columns (~10 B/row); 61 B/row is the planning figure.

### What the selection rule buys (rows/day from the table above; 262 / 61 B per row)

| rule | rows/day | uncompressed | compressed |
|---|---|---|---|
| everything (non-MVE) | 6.3 M | 1.65 GB/day · 600 GB/yr | 385 MB/day · 140 GB/yr |
| traded in last 24 h, all categories | 2.85 M | 750 MB/day · 270 GB/yr | 175 MB/day · 63 GB/yr |
| **traded in last 24 h, no Sports, no Mentions (Decision 2)** | **~1.4 M** | **360 MB/day · 130 GB/yr** | **85 MB/day · 31 GB/yr** |
| … additionally dropping markets living ≤ 15 min | 1.15 M | 300 MB/day · 110 GB/yr | 70 MB/day · 26 GB/yr |

The 1.4 M/day line is ~1.15 M from the open set plus ~0.25 M from traded short-lived markets that settle between passes (kept — Decision 2).

### Workload under the rule (derived)

| Quantity | Estimate | Basis |
|---|---|---|
| Selected open markets per pass | ~6,900 | 16,559 traded-24 h − 9,357 Sports − 356 mentions |
| Steady-state requests per hourly pass | ~70 (≈ 15 s of budget) | 6,900 / 100 tickers; a 60-minute window is 100 × 61 ≤ 10,000 |
| First-sight history for the open set (Decision 5, 24 h lookback) | ~1,150 requests ≈ 4 min, once | 1,440 periods → ⌊10,000 / 1,440⌋ = 6 tickers per request |
| Finalized backlog still on the live endpoint (settled since the 2026-06-25 cutoff, ever traded, not Sports/Mentions) | ~0.5 M markets → ~5,000 requests ≈ 17 min, spread over passes by the cap (Decision 6) | ~8.5 k traded non-Sports settlements/day × ~62 days; ladders closing in the same hour pack at 100 × ~75 periods |

## Value

- **The initiative's second time-series surface, at the granularity the PM required, for the markets the PM is interested in.** Kalshi probability moves play out on the order of minutes (260 slice plan, PM direction 20260824); after this slice every selected market accumulates 1-minute candles from the moment the collector first sees it trade through its close, unattended, on the timer that already runs — at ~31 GB/year compressed rather than 140.
- **The completeness definition becomes answerable for candles.** `mt data kalshi status` reports, from persisted state alone, which selected closed markets are candle-complete through close, which are pending, which open markets are lagging, which finalized markets have fallen behind Kalshi's live/historical cutoff without candles (the honest "known-lost until 266" number), and how many closed markets were excluded by rule.
- **Architectural enablement.** 266 gets the exact set of markets whose candles it must fetch from `/historical/markets/{ticker}/candlesticks`, and a table it is idempotent against. 265 (trades) is independent, follows the same phase shape, and can reuse the selection predicate if the PM wants the same universe for trades.

## Technical Scope

**In scope:**

- `KalshiClient.get_markets_candlesticks(...)` — the batch endpoint, with recorded fixtures for its success shape and its over-cap error body.
- `kalshi_005_candlesticks` migration: `kalshi.candlesticks` as a hypertable (Decision 4, PM-ratified), `coverage_from_ts` on `kalshi.market_candle_state`, corrected column comments, grants.
- **The collection rule** (Decision 2) as five `Settings` fields with the PM's rule as defaults, parsed once into a frozen `CandleRule`, rendered into one SQL predicate used by every pending query and by `status`.
- The candle phase: `CandlesPhase` in `collection_pass.py`, appended to `PASS_PHASES`; `CandleSync` core; `CandleRepository`; pure batch planner.
- Pass preflight verifies the kalshi track's *ledger* is complete (Decision 8).
- `mt data kalshi status` candle block (Rich and `--json`); `print_pass_summary` dispatches per-phase renderers.
- Constants for every new comparison value; unit, fixture, and integration tests; CHANGELOG; runbook 100 Kalshi subsection paragraph.

**Explicitly out of scope:**

- Hourly/daily candles (Decision 1: derived locally when wanted; `period` stays in the key).
- A minimum-lifetime exclusion (the PM chose to keep short-lived markets; adding one later is a constant plus one clause in the predicate).
- Candles for markets behind the historical cutoff — reported here, fetched by 266.
- A deeper-history or rule-override operator lever — the timer's command takes no levers (263 Decision 1).
- Compression *policy*, retention, and any change to unit files, timer cadence, `mt-run`, or the installer. (The table is compression-*enabled* so a policy is one statement later; nothing compresses automatically in this slice.)
- Bounded concurrent fetching inside the phase (Decision 9).

## Dependencies

### Prerequisites

- 263 complete and cut over on manta9000 (`v0.9.0`, timer firing hourly at `:20` UTC).
- 262's catalog populated: the phase's universe *is* `kalshi.markets ⋈ events ⋈ series` post-sync (`open_time`, `close_time`, `status`, `settlement_ts`, `volume_fp`, `volume_24h_fp`, `series.category`, `series.title`). The catalog phase walks every open market each pass, so `volume_24h_fp` is at most one pass old when the rule reads it.
- 261's `Candlestick`/`PriceOhlc` models and `CandlePeriod`; `market_candle_state`.
- TimescaleDB on the database host and on the test cluster (`create_hypertable` available; the ephemeral-database fixture already creates hypertables for the minute track).

### Interfaces Required

- `KalshiRun` (263): `client`, locked `conn`, `sink`, `run_id`, `clock`.
- `PassPhase` / `PhaseReport` / `PASS_PHASES` (263); `SyncOutcome` and `EXIT_BY_OUTCOME` (262).
- `SyncEvent` / `SyncEventSink` (262): `phase_finished` and `item_error` with `phase="candles"`; no new event type.
- `CatalogRepository.transaction()` pattern: caller-owned granularity, one transaction per batch.
- `KalshiClient.get_historical_cutoff()` (261): read once per phase run.
- `TRACKS["kalshi"]` (261): the ledger preflight enumerates its migration ids.

## Architecture

### Component Structure

```
cli/commands/kalshi.py        pass ─► run_context ─► CollectionPass(run, PASS_PHASES)
                              status ─► read_catalog_status + read_candle_status (new)
cli/commands/kalshi_render.py print_pass_summary → {CATALOG: print_phase_summary, CANDLES: print_candle_summary}
                              print_status gains the candle block

data/kalshi/collection_pass.py   PassPhaseName.CANDLES = "candles"; CandlesPhase; PASS_PHASES = (CatalogPhase(), CandlesPhase())
data/kalshi/candle_sync.py       CandleSync — cutoff → pending sets → plan → fetch/write per batch → state → events
data/kalshi/candle_plan.py       pure: CandleTarget, CandleBatch, last_complete_period(), target_window(), plan_batches()
data/kalshi/candle_repository.py CandleRepository — selection predicate, pending_live / pending_finishing / pending_backlog,
                                 insert_candles (conflict-ignore), advance_state, set_sync_state
data/kalshi/candle_types.py      CandleResult (+ to_dict), CandleSource Protocol, classify_candles
data/kalshi/client.py            get_markets_candlesticks(tickers, start_ts, end_ts, period)  (batch)
data/kalshi/models.py            MarketCandlesticks, BatchCandlesticksResponse
data/kalshi/constants.py         MARKETS_CANDLESTICKS_PATH, COLLECTED_CANDLE_PERIOD, the caps, lookback, per-pass cap, …
config/__init__.py               Settings.kalshi_candle_* (the rule; rule C as defaults) → Settings.candle_rule() → CandleRule
data/kalshi/db.py                preflight: kalshi ledger complete (Decision 8)
data/kalshi/status.py            CandleStatus, read_candle_status
market/schema/migrations/kalshi.py   kalshi_005_candlesticks
scripts/record_kalshi_fixtures.py    --only candlesticks_batch, candlesticks_batch_over_cap
```

Module boundaries follow 262: the core has no httpx, no typer, no SQL — it depends on a `CandleSource` Protocol (`get_markets_candlesticks`, `get_historical_cutoff`) and a `CandleRepository`; the planner is pure. Each file stays under the ~300-line guideline by construction.

### Data Flow — the candle phase, one pass

1. **Cutoff.** `get_historical_cutoff().market_settled_ts` once; markets finalized before it are never requested (266's). The observed cutoff is persisted in `sync_state['candlesticks'].watermark_ts` so `status` reports the behind-cutoff count without an API call.
2. **Pending sets** — three queries over `kalshi.markets m JOIN kalshi.events e JOIN kalshi.series s LEFT JOIN market_candle_state st` at `period = COLLECTED_CANDLE_PERIOD`, each carrying the **selection predicate** (Decision 2) and the pending condition (`open_time < phase_start` and watermark NULL or below the target end — Decision 3):
   - **live** — `status ≠ finalized`, predicate with the *recent-trade* form (`volume_24h_fp > 0`): unbounded; the steady state.
   - **finishing** — `finalized` *with* a state row short of `close_time + period`, predicate with the *ever-traded* form (`volume_fp > 0`; `volume_24h_fp` is meaningless once settled): unbounded, so a market that closed and settled since the last pass never queues behind history.
   - **backlog** — `finalized`, `settlement_ts ≥ cutoff`, *no* state row, ever-traded form: ordered by `settlement_ts` ascending, limited to `CANDLE_BACKLOG_REQUESTS_PER_PASS × CANDLE_BATCH_MAX_TICKERS` rows (Decision 6).
   A market that stops satisfying the live predicate simply stops being selected; its state row and candles remain, and if it trades again it re-enters with `start = watermark`, so its record has latency but no gap.
3. **Targets.** `target_window()` yields `[start, end)`: `start = watermark` if present, else `max(open_time, min(close_time, phase_start) − CANDLE_FIRST_SIGHT_LOOKBACK)` (Decision 5); `end = min(close_time + period, last_complete_period(phase_start))`. Targets with `start ≥ end` are dropped.
4. **Plan.** `plan_batches()` sorts targets by `start` and packs greedily under both caps (union window; `(n+1) × periods(union) ≤ 10,000`, `n+1 ≤ 100`), splitting an over-long single target into consecutive windows first. Pure, deterministic; the cap is asserted on every batch, so a 400 on this path is a planner bug and propagates (Decision 7).
5. **Fetch and write, one batch at a time** (Decision 9). In **one transaction** per batch: insert every served candle with `ON CONFLICT DO NOTHING`; for each requested ticker present in the response (with or without candles) upsert state with `watermark_ts = min(batch end, close_time + period)` and `coverage_from_ts = coalesce(existing, target start)`; a requested ticker *absent* from the response is an item error and its state is untouched. One INFO line per `CANDLE_PROGRESS_EVERY_REQUESTS`.
6. **State and events.** After the last batch: `sync_state['candlesticks'].last_full_sync_at = phase_start`, `.watermark_ts = cutoff`; `phase_finished` with counts plus `backlog_remaining` and `behind_cutoff`. Classification: `ProviderError` → `PROVIDER_ABORT`, `psycopg.OperationalError` → `STORAGE_ABORT`, any item error → `PARTIAL`, else `OK`.

Under the abort rule (263 Decision 2), a catalog abort skips this phase; a candle abort cannot affect the catalog phase, which has already finished.

### State Management

- **`kalshi.market_candle_state (market_ticker, period)`** — one row per market once the phase has requested it. `watermark_ts` = *candles fetched through this instant* (Decision 3), **not** the newest stored candle. `coverage_from_ts` (new) = start of the first window ever requested — `open_time` when first seen young, later when first seen with a lookback.
- **`kalshi.sync_state['candlesticks']`** — `last_full_sync_at`: start of the last pass whose candle phase completed over its pending set; `watermark_ts`: the historical cutoff observed then; `cursor`: unused.
- **No pass-level state**; a batch's candles and watermarks commit together, so an interrupted phase re-requests at most one batch.
- The catalog owns `close_time`, `volume_fp`, `volume_24h_fp`, `category`; the rule is re-evaluated from them every pass and stores nothing of its own.

## Technical Decisions

1. **One period — 1-minute — collected; coarser periods derived locally, never fetched.** The PM's requirement is minute resolution, and sparseness removes the architecture's storage argument for coarser periods: hourly candles would mostly re-serve the same moves at coarser boundaries, not be 60× fewer. Hourly or daily bars are a `time_bucket` over `kalshi.candlesticks`. `COLLECTED_CANDLE_PERIOD = CandlePeriod.MINUTE` is the single definition; `period` stays in the key so a second period is a data change, not a schema change.

2. **The collection rule — traded in the last 24 hours, and not Sports, and not Mentions. PM-ratified 20260826 (rule C).** Measured: 97% of candles are re-quotes, 55% of markets never trade, and Sports is 58% of the catalog and half the candle stream; collecting everything is ~600 GB/year uncompressed. The rule selects a market when **all** hold, every one from fields the catalog already stores:
   - **traded** — `volume_24h_fp > 0` for live markets (`volume_fp > 0` for finalized ones, whose 24 h figure is meaningless);
   - **in the allow-list, if one is set** — `series.category IN kalshi_candle_categories` (default empty = every category);
   - **not an excluded category** — `series.category NOT IN kalshi_candle_excluded_categories` (default `Sports, Mentions`);
   - **not a mention market by name** — `series.ticker !~ kalshi_candle_excluded_series_pattern` (default `MENTION|SAY`) and `series.title !~* kalshi_candle_excluded_title_pattern` (default `\m(say|says|mention|mentions)\M`) — because 188 mention series live outside the `Mentions` category.
   **The rule is configuration, not code (PM direction 20260826).** The project is released publicly as a collector — its data cannot be redistributed under Kalshi's API terms, but anyone can run it — and other users will want other categories. The five values above are `Settings` fields (`MT_KALSHI_CANDLE_*`, see *Settings*), with an **allow-list** as well as the exclude-list so "only Sports" is as expressible as "no Sports". The PM's rule C is the *default*, defined once in `Settings` (the config layer, per CLAUDE.md), and the rendered predicate (`CandleRepository.selection_sql(rule, form)`) is built from the parsed `CandleRule` the phase reads off `run.settings`. `status` prints the rule in force and takes it from the same `Settings`, so collection and reporting cannot disagree — but a user who changes the rule between passes should expect `closed_excluded_by_rule` and `selected_open` to move, which is exactly what the block is for. It is a *scheduling* rule, not a coverage rule: the watermark makes re-entry gap-free (Data Flow step 2). What it deliberately gives up: the quote history of markets that never trade, quotes before a market's first trade beyond the 24 h lookback, and Sports/Mentions entirely — an interest limitation the PM accepted knowingly (Sports includes the venue's most-traded markets). Short-lived markets (15-minute crypto ladders and the like) are **kept**: the traded ones are ~0.25 M rows/day, ~15 MB/day compressed, and are the purest minute-scale moves on the venue; a lifetime floor is a documented lever (one constant, one clause), not built. The rehearsal lists every excluded series so the pattern's reach is inspected, not assumed. *Rejected:* a hand-curated series deny-list — it goes stale as Kalshi adds series and would have excluded exactly the minute-scale ladders.

3. **The watermark is the fetched window's end, and the last complete period is one minute behind the phase start.** 261's comment defined `watermark_ts` as the newest stored candle; on sparse data an idle market would never advance. `watermark_ts` records *through when candles were requested and the response was stored*, advanced for every ticker present in the response, candles or not. Target end = `floor(phase_start, period) − period` (one-period guard for a still-settling candle in a conflict-ignore table). A request re-includes the watermark instant (boundary inclusivity is undocumented; the overlap is free). `kalshi_005` rewrites the comment.

4. **`kalshi.candlesticks` is a hypertable from creation, chunked by 7 days. PM-ratified 20260826.** Even under the rule the table reaches hundreds of millions of rows within its first year, where 260's "promote later" becomes a long maintenance window; creating the hypertable on the empty table costs nothing. `chunk_time_interval = 7 days` follows the 20260719 rule (~520 chunks per decade; ~10 M rows/chunk at the rule's volume), defined once as `KALSHI_CANDLE_CHUNK_INTERVAL`. The primary key contains the partitioning column; the foreign key to `kalshi.markets` is permitted on a hypertable. The table is created **compression-enabled** (`segmentby market_ticker`, `orderby end_period_ts DESC` — the settings the measurement used) but **no compression policy** ships here; until one is ratified the PM sizes disk at ~360 MB/day. Per the extraction discipline, nothing references `public`.

5. **First sight buys 24 hours of history; before that, nothing — except for markets seen young. PM-ratified 20260826.** A market with no state row starts at `max(open_time, min(close_time, phase_start) − 24 h)`: a ladder finalized between passes is fetched from its open; a market open for 100 days gets its last day, and `coverage_from_ts` records that for `status`. Under the rule the first pass costs ~1,150 requests ≈ 4 minutes (six tickers per request at 1,440 periods). `CANDLE_FIRST_SIGHT_LOOKBACK = timedelta(hours=24)` is the single definition.

6. **The finalized backlog drains under a per-pass request cap; live markets are never capped.** ~0.5 M selected finalized markets are still on the live endpoint and fall behind the cutoff when it moves — collected oldest-settlement-first because they are reachable now. `CANDLE_BACKLOG_REQUESTS_PER_PASS = 1000` (~3.3 minutes) keeps every pass bounded and the catalog hourly; the backlog drains in roughly six firings. Live and finishing sets are bounded by the venue (~7 k markets ≈ 70 requests). `status` reports `backlog_remaining`.

7. **A provider error on a batch aborts the phase; only an omitted ticker is an item error.** The planner guarantees the cap, so a 400 on `/markets/candlesticks` is our bug or an API change and must fail the pass visibly (exit 2). 429/5xx follow the transport's bounded-retry path. Omission from the response is the one per-market failure the API signals: `item_error` (`phase="candles"`, reason `"not served by the batch endpoint"`), state untouched, retried next pass.

8. **Pass preflight verifies the kalshi ledger, not one table.** This is the first deploy that adds a migration a *running timer* depends on. `open_sync_connection` reads `schema_migrations` and requires every id in `TRACKS["kalshi"]`; a missing one is a `PreflightError` naming it (`kalshi track has pending migrations: kalshi_005_candlesticks — mt data migrate apply --track kalshi`) → exit 1, `Result=exit-code`, visible, harmless, retried next hour. The `to_regclass('kalshi.sync_state')` check becomes redundant and is removed.

9. **Sequential fetch and write on the run's single connection.** Under the rule the phase is ~70 requests in steady state; concurrency would buy nothing. Rehearsal records the phase's wall time; a fetch pool stays a follow-up with evidence.

10. **Candles are fully structured; nested OHLC objects flatten to columns; conflict-ignore, never update.** `yes_bid`/`yes_ask`/`price` → `yes_bid_open_dollars … price_mean_dollars`; `volume_fp NOT NULL`, the rest nullable (`price: {}` on a no-trade period). No `raw` (261 Decision 6). `INSERT … ON CONFLICT DO NOTHING`; the one-period guard backs the immutability assumption. `CANDLE_COLUMNS` in `candle_repository.py` is the flattening map, checked against the table by the parity test.

11. **`status` reads the database only; the phase leaves it what it needs.** The behind-cutoff count needs the cutoff, persisted by the phase in `sync_state['candlesticks'].watermark_ts`. Every candle figure comes from `markets ⋈ events ⋈ series ⋈ market_candle_state` and `sync_state`; nothing counts rows in `kalshi.candlesticks` (journal 20260720).

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

`PassPhaseName.CANDLES = "candles"` names the phase in reports, events, the JSON summary, and log lines.

### Settings — the collection rule (`config/__init__.py`, Decision 2)

```python
# Kalshi candle collection rule (slice 264, Decision 2). Defaults are the
# PM's rule C; every value is overridable so another operator can collect a
# different universe. Category strings are Kalshi's own series.category
# values — the venue owns that vocabulary, so they are data, not an enum.
kalshi_candle_traded_only: bool = True                        # require volume_24h_fp > 0 (live) / volume_fp > 0 (finalized)
kalshi_candle_categories: frozenset[str] = frozenset()        # allow-list; empty = every category
kalshi_candle_excluded_categories: frozenset[str] = frozenset({"Sports", "Mentions"})
kalshi_candle_excluded_series_pattern: str | None = r"MENTION|SAY"                      # PostgreSQL regex on series.ticker; empty/None disables
kalshi_candle_excluded_title_pattern: str | None = r"\m(say|says|mention|mentions)\M"   # case-insensitive, on series.title; empty/None disables
```

- Environment form: `MT_KALSHI_CANDLE_TRADED_ONLY=true`, `MT_KALSHI_CANDLE_CATEGORIES=` (comma-separated; whitespace trimmed; empty = all), `MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES=Sports,Mentions`, `MT_KALSHI_CANDLE_EXCLUDED_SERIES_PATTERN=MENTION|SAY`, `MT_KALSHI_CANDLE_EXCLUDED_TITLE_PATTERN=\m(say|says|mention|mentions)\M`. A `field_validator(mode="before")` splits the comma-separated forms — pydantic-settings' default JSON-list parsing is not what a `.env` author expects.
- `Settings.candle_rule() -> CandleRule` (frozen dataclass in `candle_types.py`: `traded_only`, `categories`, `excluded_categories`, `excluded_series_pattern`, `excluded_title_pattern`) is the one parse; the phase reads it from `run.settings`, `status` from the CLI's settings. `CandleRule.describe()` renders the one-line human form used by the `status` block and the phase's start log line (`candles rule: traded 24h · categories all · excluding Sports, Mentions · patterns 2`).
- A regex that PostgreSQL rejects fails the phase at its first pending query with the database's own error (`invalid regular expression`) — a storage-taxonomy `ProgrammingError`, which is a *bug in configuration* and propagates as such rather than being swallowed. The rehearsal's "inspect the rule" step is the operator's check.
- Evaluation order: allow-list (if non-empty) → exclude-list → patterns → traded. Allow and exclude naming the same category is not an error; exclude wins, which the docstring says.
- `deploy/manta-trading.env.example` gains the five lines, commented, showing the defaults; runbook 100's Kalshi subsection explains them in one paragraph and points at the `status` block's `rule` line.

### Client and models

- `KalshiClient.get_markets_candlesticks(tickers: Sequence[str], *, start_ts: int, end_ts: int, period_interval: CandlePeriod) -> list[MarketCandlesticks]` — `GET /markets/candlesticks?market_tickers=…&start_ts&end_ts&period_interval`. The client passes the request through; the planner owns the cap.
- Models: `MarketCandlesticks(market_ticker: str, candlesticks: list[Candlestick])`, `BatchCandlesticksResponse(markets: list[MarketCandlesticks])`.
- `CandleSource` Protocol: `get_markets_candlesticks(...)`, `get_historical_cutoff()`. Tests use a fixture-backed fake recording every received query (the `FakeCatalogSource` pattern).

### Planner (`candle_plan.py`, pure)

```python
@dataclass(frozen=True)
class CandleTarget:  ticker: str; start: datetime; end: datetime; close_end: datetime  # close_time + period
@dataclass(frozen=True)
class CandleBatch:   tickers: tuple[str, ...]; start: datetime; end: datetime

def last_complete_period(now, period) -> datetime                     # floor − one period (Decision 3)
def target_window(market, state, *, phase_start, period, lookback) -> CandleTarget | None
def plan_batches(targets, *, period, max_tickers, max_candles) -> list[CandleBatch]
```

`plan_batches` asserts `len(tickers) × periods(start, end) ≤ max_candles` on every batch it returns; a randomized unit test checks the invariant and full coverage of every target.

### Repository (`candle_repository.py`)

- `selection_sql(rule: CandleRule, form: Literal["recent", "ever"]) -> sql.Composed` — the Decision 2 predicate over aliases `m`, `s`, with the allow-list, exclude-list, and both patterns as bound parameters (a clause is omitted when its setting is empty); `recent` tests `m.volume_24h_fp > 0`, `ever` tests `m.volume_fp > 0`, neither when `traded_only` is false. **The only place the rule is rendered.**
- `pending_live(period, phase_start)`, `pending_finishing(period)`, `pending_backlog(period, cutoff, limit)` — Data Flow step 2; each returns `(ticker, open_time, close_time, watermark_ts)`; every status value is a bound parameter from `MarketStatus`.
- `insert_candles(rows) -> int` — multi-row `INSERT … ON CONFLICT DO NOTHING` chunked under the bind-parameter ceiling like `CatalogRepository._upsert`.
- `advance_state(period, advances)` — one multi-row upsert: `watermark_ts = EXCLUDED.watermark_ts`, `coverage_from_ts = COALESCE(state.coverage_from_ts, EXCLUDED.coverage_from_ts)`, `updated_at = now()`.
- `set_sync_state(phase_start, cutoff)`; `transaction()` as `CatalogRepository.transaction()`.

Storage failure taxonomy as 262: `IntegrityError` on a batch → rewrite per market, offenders become item errors; `OperationalError` propagates (storage abort); any other `psycopg.Error` propagates (bug).

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

PASS_PHASES: tuple[PassPhase, ...] = (CatalogPhase(), CandlesPhase())
```

### `CandleResult.to_dict()` (the phase's `summary`)

```json
{"run_id": "...", "started_at": "...", "period": 1, "cutoff": "2026-06-25T00:00:00+00:00",
 "pending": {"live": 6912, "finishing": 214, "backlog": 100000, "backlog_remaining": 412000},
 "requests": 1092, "markets_requested": 107126, "markets_advanced": 107124,
 "candles_fetched": 1631204, "candles_written": 1631198,
 "item_errors": [{"ticker": "...", "reason": "not served by the batch endpoint"}],
 "duration_ms": 231803, "error": null}
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
                         chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);          -- Decision 4
ALTER TABLE kalshi.candlesticks SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'market_ticker', timescaledb.compress_orderby = 'end_period_ts DESC');  -- enabled, no policy
ALTER TABLE kalshi.market_candle_state ADD COLUMN IF NOT EXISTS coverage_from_ts TIMESTAMPTZ;
COMMENT ON COLUMN kalshi.market_candle_state.watermark_ts IS 'candles requested and stored through this instant (window end, clamped to close_time + period) — NOT the newest stored candle: Kalshi serves no candle for an idle period (slice 264, Decision 3)';
COMMENT ON COLUMN kalshi.market_candle_state.coverage_from_ts IS 'start of the first window ever requested; equals open_time only when the market was first seen young (slice 264, Decision 5)';
COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS '... candlesticks: market_settled_ts of the historical cutoff observed by the last candle phase (slice 264, Decision 11)';
GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.candlesticks TO trading_app;
```

Additive and idempotent; no down-migration. Applied to production by the operator from the dev checkout with the maintenance credential (runbook 100 *Update procedure*), before the first firing after the install (Decision 8 makes the wrong order harmless).

### Preflight (`db.py`)

`open_sync_connection` replaces the `to_regclass` probe with `SELECT migration_id FROM schema_migrations WHERE migration_id = ANY(%s)` over `TRACKS["kalshi"]`'s ids; any missing → `PreflightError` naming them; an absent `schema_migrations` table is the same error. `TRACK_NOT_APPLIED`'s wording is updated; the lock step is unchanged.

### CLI and rendering

- `mt data kalshi pass` — unchanged surface. `print_pass_summary` looks up a renderer by `PassPhaseName`; `print_candle_summary(summary)` prints requests, markets requested/advanced, candles fetched/written, pending live/finishing/backlog (+ remaining), item errors.
- `mt data kalshi status` — `read_candle_status(conn) -> CandleStatus | None` (None until the phase has run once; Rich prints "Candlesticks: never collected", JSON `"candles": null`). Fields, every one a persisted fact:
  - `period_minutes`, `last_phase_at`, `cutoff_observed`
  - `rule` — the `CandleRule` in force (from `Settings`), as an object in JSON and one line in Rich
  - `selected_open` — open markets the rule selects right now (what the next pass will request)
  - `markets_tracked` — rows in `market_candle_state`
  - `open_lagging` / `open_oldest_watermark` — tracked, still-open markets whose watermark is older than `now − CANDLE_LAG_STALE_AFTER` *and* that the rule still selects (a deselected market is not "lagging"; it is idle)
  - `complete_through_close` — state rows with watermark `≥ close_time + period`
  - `closed_short_of_close` — tracked markets past close, not behind the cutoff, watermark `< close_time + period` (should be ~0 between passes)
  - `backlog_remaining` — finalized since the cutoff, selected (ever-traded form), no state row
  - `behind_cutoff_uncollected` — finalized before the cutoff, selected (ever-traded form), no state row — 266's input
  - `closed_excluded_by_rule` — markets past close with no state row that the rule does not select (never-traded, Sports, Mentions) — the exclusion made visible
  - `partial_history` — state rows whose `coverage_from_ts > open_time`
- Rich block:

```
Kalshi candlesticks        period 1 min   last phase 2026-08-27 14:24:11 UTC (36 min ago)   cutoff 2026-06-25
  rule                traded 24h · categories all · excluding Sports, Mentions · patterns 2   (MT_KALSHI_CANDLE_*)
  selected open       6,912
  tracked             521,404 markets   complete through close 512,110   partial history 5,822
  open lagging        0 (oldest watermark 2026-08-27 14:19 UTC)
  short of close      0        backlog remaining 0        behind cutoff, uncollected 9,203
  excluded by rule    3,117,908 closed markets (never traded, Sports, Mentions)
```

### Fixtures and recorder

- `--only candlesticks_batch` — a real batch response over ≥ 3 selected tickers for the last hour at `period_interval=1`, chosen so at least one entry is empty and one has a candle with `price: {}`.
- `--only candlesticks_batch_over_cap` — the HTTP 400 body for 100 tickers × 360 minutes, saved as `error_400_candles_cap.json`.
- Existing `candlesticks.json` stays for the 261 client test.

### Runbook 100 and CHANGELOG

- Kalshi subsection gains one paragraph: the pass has two phases; what the collection rule is, that it is set by the `MT_KALSHI_CANDLE_*` lines in the environment file (defaults shown in the example), and that `status` shows the rule in force and the excluded count; `kalshi_005` must be applied during the update (a firing before that exits 1 with the migration named — expected); the first firing after the release takes a few minutes longer (first-sight history) and the backlog drains over ~6 firings; the table is compression-enabled but no policy runs until ratified.
- CHANGELOG under `[Unreleased]`: candle phase and rule, status block, migration (hypertable), the preflight change.

### Tests

- **Unit — planner:** `last_complete_period`; `target_window` cases (young → open; old → lookback; past close → clamped; complete → `None`; state → watermark); packing never exceeds either cap, splits over-long targets, unions correctly, deterministic; randomized invariant.
- **Unit — core:** with `FakeCandleSource` and an in-memory fake repository: live/finishing/backlog ordering and the cap on backlog only; present-with-zero-candles advances; omitted ticker → item error, no advance; `coverage_from_ts` set once; `sync_state` after the last batch; classification; events; progress cadence.
- **Unit — client/models/fixtures/settings:** request parameters; batch fixture parses including empty entries; 400 fixture is `ProviderPermanentError`; `Settings` parses each `MT_KALSHI_CANDLE_*` form (comma lists with whitespace, empty = all/disabled, booleans) into the expected `CandleRule`, the defaults equal rule C, and `describe()` is stable.
- **Unit — pass and rendering:** `PASS_PHASES == (catalog, candles)`; renderer dispatch; JSON round-trip; `status.py` imports neither the client nor the transport.
- **Integration (`kalshi_db`):** `kalshi_005` applies and re-applies; `kalshi.candlesticks` is a hypertable with the configured chunk interval and compression settings, no policy; `CANDLE_COLUMNS` parity; conflict-ignore on a duplicate key; **the selection predicate** against fixture markets with synthesized series — a Sports market, a `Mentions`-category market, a mention-titled market in another category, a never-traded market, and a traded-24 h Politics market: under the default rule only the last is `pending_live`; under an allow-list of `Sports` only the Sports market is; with `traded_only=false` the never-traded market joins; with every setting empty all five are; the same set under the `ever` form for finalized rows; an invalid regex surfaces the database's error; a market whose `close_time` moved later becomes pending again; a market finalized before the cutoff is never selected and counts as behind-cutoff; an end-to-end `pass` runs both phases and a second pass writes nothing; preflight exit 1 names a missing migration; `status` shape and every count.
- **Deploy drift guard:** unchanged.
- Gates as 263.

## Integration Points

### Provides to Other Slices

- **265 (trades):** the worked example of a `PassPhase` with core/repository/types split, a per-market pending query over the catalog, a per-pass cap on history, and `selection_sql()` — reusable verbatim if the PM wants the same universe for trades. `PassPhaseName.TRADES` appends after `CANDLES`. The ledger preflight covers 265's migration for free.
- **266 (historical backfill):** `behind_cutoff_uncollected` — the exact market set for `/historical/markets/{ticker}/candlesticks`; the same table and conflict-ignore insert; `coverage_from_ts`.
- **Future compression policy:** a compression-enabled hypertable already in place; the policy is one statement.

### Consumes from Other Slices

- 263's phase contract, abort rule, and shared `run_id` event stream, unchanged.
- 262's catalog columns and refresh cadence (`volume_24h_fp` is walked every pass), `Surface.CANDLESTICKS`.
- 261's `Candlestick` model, `CandlePeriod`, `market_candle_state`, `get_historical_cutoff`, transport error taxonomy.

## Success Criteria

1. **The pass has two phases.** `mt data kalshi pass --json` reports `phases[].name == ["catalog", "candles"]`; a catalog abort reports the candle phase `skipped`; a candle abort leaves the catalog phase's outcome and state intact.
2. **The rule selects exactly what Decision 2 says, and it is configuration.** Under the defaults, a Sports market, a `Mentions` market, a mention-titled market in another category, and a never-traded market are never requested and have no state row, while a traded-24 h market outside those categories is; with `MT_KALSHI_CANDLE_CATEGORIES=Sports` and the exclusions cleared, the Sports market is the one requested — proven by the fake source's recorded queries and by the integration test on the predicate, and demonstrable live in walkthrough step 3.
3. **Candles land under the natural key.** One pass writes rows into `kalshi.candlesticks` for selected markets that had activity, and `market_candle_state` rows for every market it requested — including markets that served no candle.
4. **A second pass writes only what is new** and no duplicate row exists (conflict-ignore, proven by re-inserting a batch).
5. **The planner never exceeds the caps** (unit invariant); the rehearsal journal shows zero HTTP 400 on `/markets/candlesticks`.
6. **First sight follows Decision 5.** A market first seen young has `coverage_from_ts == open_time`; one first seen old has `coverage_from_ts == phase_start − 24 h` and counts in `partial_history`.
7. **Closed markets complete through close** (`watermark_ts ≥ close_time + period`), are counted in `complete_through_close`, and are never requested again.
8. **The backlog drains under the cap, oldest first, and is visible** (`backlog_remaining` decreasing pass over pass; at most `1000 × 100` markets per pass).
9. **Behind-cutoff markets are never requested and are counted** in `behind_cutoff_uncollected`.
10. **An omitted ticker is a partial, not an abort** (one `item_error`, exit 3, state untouched).
11. **Preflight names the missing migration** (exit 1 with `kalshi_005_candlesticks` until `mt data migrate apply --track kalshi`).
12. **`status` answers the candle clause from the database alone** — every field in *CLI and rendering*, including `closed_excluded_by_rule`; `status.py` imports neither the client nor the transport.
13. **Production: the timer runs the phase unattended.** After the release is installed and `kalshi_005` applied, the next firing's journal shows `phases=catalog,candles`, `candles=ok`, `Result=success`; on the following firing `mt-run data kalshi status` shows `open_lagging == 0` and `backlog_remaining` falling.

## Verification Walkthrough

Draft; refined with observed output after Phase 6, as 263's was. Steps 1–5 run on a **throwaway database on the test cluster** (`MT_TIMESCALE_DB_URL` points at it for these commands only; the production URL is never in the shell). Steps 6–8 are on manta9000 and are the PM's.

**1. Throwaway database, migrated, with a small catalog.**

```bash
uv run mt data migrate status --track kalshi      # → 0 pending; kalshi_005_candlesticks applied
uv run psql "$MT_TIMESCALE_DB_URL" -c "select hypertable_name, compression_enabled from timescaledb_information.hypertables where hypertable_schema='kalshi'"
#    → candlesticks | t
uv run mt data kalshi sync --settled-since "$(date -u -d '6 hours ago' +%FT%TZ)"     # ~180k live + ~18k settled; minutes, not 45
```

**2. Preflight names a missing migration (Criterion 11).**

```bash
uv run psql "$MT_TIMESCALE_DB_URL" -c "delete from schema_migrations where migration_id='kalshi_005_candlesticks'"
uv run mt data kalshi pass      # → Error: kalshi track has pending migrations: kalshi_005_candlesticks — …   (exit 1)
uv run mt data migrate apply --track kalshi
```

**3. Inspect the rule before running it, and show it is configuration (Criterion 2).**

```bash
uv run mt data kalshi status --json | jq .candles.rule         # the defaults: traded_only true, excluded Sports,Mentions, two patterns
MT_KALSHI_CANDLE_CATEGORIES=Sports MT_KALSHI_CANDLE_EXCLUDED_CATEGORIES= uv run mt data kalshi status --json | jq '.candles | {rule, selected_open}'
#    → selected_open jumps to the ~9k traded Sports markets; nothing was collected, only the rule in force changed
# what the default rule selects right now, and every series the exclusion patterns catch — read this list
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.markets m join kalshi.events e on e.event_ticker=m.event_ticker join kalshi.series s on s.ticker=e.series_ticker where m.status<>'finalized' and m.volume_24h_fp>0 and s.category not in ('Sports','Mentions') and s.ticker !~ 'MENTION|SAY' and s.title !~* '\m(say|says|mention|mentions)\M'"
uv run psql "$MT_TIMESCALE_DB_URL" -c "select ticker, category, title from kalshi.series where ticker ~ 'MENTION|SAY' or title ~* '\m(say|says|mention|mentions)\M' order by category, ticker"
```

**4. First pass: both phases, first-sight history, the backlog cap (Criteria 1, 3, 6, 8, 9).**

```bash
uv run mt data kalshi pass --events-file candles-pass1.jsonl
#    journal: kalshi pass started … phases=catalog,candles
#             candles: cutoff 2026-06-25 · pending live ~6,900 finishing 0 backlog 100,000 (remaining ~xxx,xxx)
#             candles: 100 requests … (progress) … kalshi pass finished outcome=ok … phases: catalog=ok candles=ok
#    expect a few minutes (Decision 5: ~1,150 first-sight requests + ≤1,000 backlog)
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.market_candle_state"
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) filter (where s.coverage_from_ts > m.open_time) partial, count(*) filter (where s.watermark_ts >= m.close_time + interval '1 minute') complete from kalshi.market_candle_state s join kalshi.markets m on m.ticker=s.market_ticker"
uv run psql "$MT_TIMESCALE_DB_URL" -c "select market_ticker, end_period_ts, yes_bid_close_dollars, price_close_dollars, volume_fp from kalshi.candlesticks order by end_period_ts desc limit 5"
jq -r 'select(.event_type=="phase_finished" and .phase=="candles") | .counts' candles-pass1.jsonl
```

**5. Second pass, status, omission path (Criteria 4, 7, 10, 12).**

```bash
uv run mt data kalshi pass --json | jq '.phases[] | {name, outcome, w: .summary.candles_written, r: .summary.requests}'
#    → candles ok, requests ≈ 70 + ≤1,000 backlog, candles_written small
uv run psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.candlesticks c join kalshi.candlesticks d using (market_ticker, period, end_period_ts) where c.ctid <> d.ctid"   # → 0
uv run mt data kalshi status                       # candle block as in *CLI and rendering*, excluded-by-rule count non-zero
uv run mt data kalshi status --json | jq .candles
# omission path: integration test `test_omitted_ticker_is_item_error` — the fake source drops one requested ticker; exit 3, no state row.
```

**6. Production deploy (PM).** Runbook 100 *Update procedure*: tag `v0.10.0` → `install-production.sh --ref v0.10.0` (once — no new units) → from the dev checkout `uv run mt data migrate status --track kalshi` (1 pending) → `uv run mt data migrate apply --track kalshi` (maintenance credential) → `status` shows 0 pending. A firing between install and apply shows `last run: exit-code, exit=1` naming `kalshi_005_candlesticks` — expected (Decision 8).

**7. First supervised firing (Criterion 13).**

```bash
sudo mt-run kalshi                     # or wait for :20; Ctrl-C detaches
mt-run follow kalshi
systemctl show mt-kalshi-pass.service -p Result -p ExecMainStatus                    # Result=success, ExecMainStatus=0
journalctl -u mt-kalshi-pass.service --grep 'kalshi pass finished' -n 1               # phases: catalog=ok candles=ok
journalctl -u mt-kalshi-pass.service -o cat --since -2h | grep -c 'HTTP 4'            # 0
```

**8. Steady state, one day later.**

```bash
mt-run data kalshi status              # open lagging 0; backlog remaining → 0 within ~6 firings; behind-cutoff and excluded counts stable
journalctl -u mt-kalshi-pass.service --since -24h | grep -c retry                     # 263 Decision 7 evidence, now with ~70 more requests per pass
```

### Success criteria — where each is proven

| # | Unit | Integration | Rehearsal / host |
|---|---|---|---|
| 1 | pass order, dispatch | pass end-to-end | step 4 journal |
| 2 | core: recorded queries | predicate fixture set | step 3 listing |
| 3 | core: advance on empty | insert + state | step 4 queries |
| 4 | — | duplicate re-insert | step 5 |
| 5 | planner invariant | — | step 7 grep |
| 6 | `target_window` cases | pending queries | step 4 partial/complete |
| 7 | core: finishing set | close-then-pass | step 5 |
| 8 | core: cap on backlog only | backlog ordering | steps 4–5 status |
| 9 | core: cutoff exclusion | behind-cutoff query | step 5 status |
| 10 | core: omission | `test_omitted_ticker…` | — |
| 11 | — | ledger preflight | step 2 |
| 12 | status imports | status queries | step 5 |
| 13 | — | — | steps 7–8 |

## Risk Assessment

- **The exclusion patterns may over- or under-reach.** `SAY` as a ticker substring and `\m(say|says|mention|mentions)\M` on titles matched 188 series this morning; a series whose title happens to contain "say" in another sense would be dropped, and a mention series with an unusual title kept. Walkthrough step 3 prints the full matched list for the PM to read before the first production firing; a wrong match is an environment-file edit, and nothing already stored is deleted.
- **Kalshi may revise a completed candle.** Conflict-ignore keeps the first version; the one-period guard limits exposure. If rehearsal shows revisions (re-fetch a window and diff), `DO UPDATE` on the value columns is a one-statement change.
- **Storage is small under the rule but uncompressed until a policy is ratified:** ~360 MB/day. The table is compression-enabled; the policy is a separate PM-ratified statement.

## Decisions ratified by the Project Manager (20260826)

- **Decision 4** — hypertable at creation: yes.
- **Decision 5** — 24-hour first-sight lookback: yes.
- **Decision 2** — collection rule C: traded in the last 24 h, no Sports, no Mentions, short-lived markets kept. Sports is acknowledged as an interest limitation, not a liquidity judgement. **And the rule is configuration** (`MT_KALSHI_CANDLE_*`, rule C as defaults): the project is released publicly as a collector — the PM's collected data cannot be redistributed under the API terms — and other users will want other categories.

## Implementation Notes

### Development Approach

Sections, each a checkpoint commit:

1. Client + models + fixtures (`get_markets_candlesticks`, batch fixtures, error fixture).
2. Migration `kalshi_005` + parity and hypertable tests + preflight ledger check.
3. Planner (pure) with its tests.
4. Repository: `selection_sql`, pending queries, inserts — with the predicate fixture set in the integration tier.
5. `CandleSync` core with fakes; `CandlesPhase`; `PASS_PHASES` append; renderer dispatch.
6. `status` block.
7. Rehearsal on the test cluster (walkthrough 1–5) with the excluded-series listing pasted into `user/notes/2026-MM-DD-264-rehearsal.md`, then runbook + CHANGELOG, then the host steps.

Scope every `ruff`/format invocation to the files touched (263 process note).

### Special Considerations

- The pending queries join `markets` (3.5 M+ rows) to `events` and `series` once per pass each; rehearsal records their wall time. The `status` index (`markets(status)`) and the `finalized` predicate keep the live query small; if the backlog query dominates, a partial index on `markets (settlement_ts) WHERE status = 'finalized'` (rendered from `MarketStatus`) is the first lever.
- `coverage_from_ts` is set by `COALESCE` so a re-run can never move it later.
- The phase logs the cutoff it used at INFO on every run; a cutoff that jumps forward is the signal that 266 has become urgent.
