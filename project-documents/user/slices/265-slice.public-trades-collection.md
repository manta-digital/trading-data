---
docType: slice-design
slice: public-trades-collection
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [263]
interfaces: [266]
effort: 3
dateCreated: 20260828
dateUpdated: 20260828
status: not_started
---

# Slice Design: Public Trades Collection (265)

## Overview

This slice adds the third and last phase to the Kalshi collection pass: **the public trade tape**, appended to `PASS_PHASES` behind the candle phase and run by the existing hourly `mt-kalshi-pass.timer` with no unit, timer, installer, wrapper, or `mt-run` change. Kalshi serves one exchange-wide tape (`GET /markets/trades`, newest first, 1,000 per page, filterable by `min_ts`/`max_ts`), so the phase walks that tape **in one-hour windows, oldest first, under a single watermark** in `kalshi.sync_state['trades']` — the same windowed-drain shape 262 built for the settled stream — and stores each trade whose market the catalog knows and the collection rule selects, into a new `kalshi.trades` hypertable keyed on `(market_ticker, created_time, trade_id)` with conflict-ignore inserts. `mt data kalshi status` gains a trades block: how far the tape is complete, how far behind it is, and — for the selected closed markets — how many are tape-complete through close, partial, still short of close, or before the collector's coverage (266's input).

Three measurements taken during this design (live public API and a throwaway test-cluster database, 2026-08-28) shape everything below and are recorded in **Discovery Findings**: the tape runs at **~300–550 k trades per hour (≈ 10 M/day, ≈ 10 k requests/day)** across ~26 k markets an hour, which rules out any per-market fetch strategy; **8% of trades are on MVE/parlay markets** the catalog deliberately excludes and **59% fall under the candle rule**; and a stored trade costs **257 B uncompressed / 71.5 B compressed**. At the rule's share that is ~155 GB/year compressed — five times the candle surface — and the storage decision is the PM's (Decision 3).

## Discovery Findings (2026-08-28)

Numbers were measured through the public endpoint at ≤ 4 req/s; the probe scripts are not part of the deliverable. Catalog joins ran read-only against the production catalog with a statement timeout; the storage measurement used a throwaway database on the test cluster, created and dropped by its exact generated name.

### Endpoint behavior (verified)

| Fact | Evidence |
|---|---|
| `GET /markets/trades` returns **newest first**, strictly: 352,000 consecutive trades, **0 out-of-order pairs** | one-hour walk, 352 pages |
| `limit` ceiling is **1,000** (1,001 → HTTP 400 `Field validation for 'Limit' failed on the 'lte' tag`) | probe |
| `min_ts` / `max_ts` bound a walk and terminate it: a `[05:00, 06:00)` window returned oldest `05:00:00.016`, newest `05:59:59.986` — strict at second granularity, as 262 found for `min_settled_ts` | three fixed-hour walks |
| Last page and empty window both serve `{"cursor": "", "trades": []}`-style termination: **`cursor` is present and empty** on the final page | probe |
| `ticker=` filter works with the cursor (one 15-minute BTC ladder: 1,000 trades and a cursor) | probe |
| Cursor pagination is clean: **0 duplicate `trade_id`** across 352 consecutive pages | one-hour walk |
| Every `trade_id` is UUID-shaped (352,000 of 352,000); `yes_price + no_price = 1` on every trade; `count_fp` is fractional on 49% (sub-contract fills); `taker_side` (deprecated) is still served alongside `taker_outcome_side` / `taker_book_side`; 0 block trades in the sample | sample |
| `GET /historical/trades` is public and serves the same shape with a cursor (first row `2026-06-28T23:59:59Z`); `GET /historical/cutoff` today: `trades_created_ts = 2026-06-29T00:00:00Z` (it read 2026-06-25 on 08-24 and 06-28 on 08-27 — **the cutoff advances about one day per day; the live tape is a rolling ~60 days**) | probe |
| Kalshi's batch and per-market candle endpoints have no trades analogue; the tape is the only bulk surface | docs, 261 |

### The tape's volume (four sampled hours)

| Hour (UTC) | trades | pages (= requests) | distinct markets |
|---|---|---|---|
| 2026-08-28 14:27–15:27 (weekday morning, US) | 352,000 | 352 | 25,993 |
| 2026-08-28 05:00–06:00 | 303,346 | 304 | — |
| 2026-08-28 01:00–02:00 (US evening) | 549,016 | 550 | — |
| 2026-08-27 19:00–20:00 | 463,811 | 464 | — |

Planning figures: **~420 k trades/hour, ~10 M/day, ~10 k requests/day**; a weekday hour ranges 300–550 k. The heaviest markets are the 15-minute crypto and metals ladders (`KXBTC15M` alone was 23% of the morning hour, four ladders at 13–22 k trades each) and live tennis matches.

### What the catalog and the rule say about it (the 352,000-trade hour joined to the production catalog)

| Set | trades | share | markets |
|---|---|---|---|
| **Unknown to the catalog — all `KXMVECROSSCATEGORY*` (MVE/parlay, excluded by 262's `mve_filter=exclude`)** | 28,797 | **8.2%** | 19,207 |
| Sports | 112,989 | 32.1% | 3,632 |
| Crypto | 156,312 | 44.4% | 752 |
| Commodities | 33,060 | 9.4% | 245 |
| Climate and Weather / Financials / Elections / Economics / Entertainment / Politics / Science | 18,477 | 5.2% | 2,002 |
| Mentions | 2,365 | 0.7% | 155 |
| **Selected by the default collection rule (no Sports, no Mentions, no mention-titled series)** | **207,849** | **59.0%** | 2,999 |
| … of which on markets living ≤ 15 minutes | 152,637 | 73% of selected | — |

Two consequences: the phase must **drop or resolve** unknown-market trades — MVE markets have no catalog row, and 15 MVE *series* do exist in `kalshi.series` (the series list is unfiltered) while 0 MVE markets do; and the rule's storage saving for trades (36% of the non-MVE tape) is much smaller than for candles (78%), because trades concentrate where candles are dense anyway.

### Storage cost, measured

352,000 real trades inserted into a throwaway hypertable on the test cluster (7-day chunks, `compress_segmentby market_ticker`, `compress_orderby created_time DESC`), sizes from `hypertable_detailed_size`, chunks then compressed by hand:

| layout | uncompressed (heap + index) | compressed (heap + index + toast) | ratio |
|---|---|---|---|
| **B — `PRIMARY KEY (market_ticker, created_time, trade_id UUID)`** | 113 + 144 = **257 B/row** | 42 + 7 + 23 = **71.5 B/row** | 3.6× |
| A — `PRIMARY KEY (created_time, trade_id UUID)` + index `(market_ticker, created_time)` | 113 + 157 = 270 B/row | 71.6 B/row | 3.8× |
| C — as B with `trade_id TEXT` | 134 + 189 = 324 B/row | 88.3 B/row | 3.7× |

The UUID column is worth 67 B/row uncompressed and 17 B/row compressed over TEXT; the ticker-first key is 13 B/row cheaper than time-first plus the per-market index it would need.

### What each storage choice costs (rows/day × 257 / 71.5 B)

| stored | rows/day | uncompressed | compressed | 14-day uncompressed buffer |
|---|---|---|---|---|
| everything the catalog knows (non-MVE) | ~9.2 M | 2.4 GB/day · 860 GB/yr | 0.66 GB/day · **240 GB/yr** | 33 GB |
| **the collection rule (Decision 3)** | ~5.9 M | 1.5 GB/day · 550 GB/yr | 0.42 GB/day · **155 GB/yr** | 21 GB |
| the rule, minus markets living ≤ 15 min | ~1.6 M | 0.4 GB/day · 150 GB/yr | 0.11 GB/day · **41 GB/yr** | 6 GB |

For scale: the production `trading` database is 159 GB today; the `kalshi` schema is 12 GB, of which `candlesticks` is 4.8 GB after two days.

### Workload (derived)

| Quantity | Estimate | Basis |
|---|---|---|
| Steady-state requests per hourly pass | **~300–550** (≈ 1.5 min at 300/min); the pass becomes ~800–900 requests ≈ 3 min | one hour of tape at 1,000/page |
| Tape on the live endpoint at first run | ~60 days ≈ 600 M trades ≈ **600 k requests ≈ 33 h of budget** | cutoff 2026-06-29, 10 M/day |
| Initial drain at `TRADE_REQUESTS_PER_PASS = 3,000` (≈ 10 min/pass) | ~7 h of tape per pass, ~7 days per day, net 6 days/day against a moving "now" → **caught up in ~10 days**; the watermark starts *at* the cutoff and outruns it, so no live tape is lost during the drain | Decision 2 |
| Rows written during the drain under the rule | ~354 M → ~25 GB compressed | 59% of 600 M |

## Value

- **The initiative's third surface, and the one Kalshi is actively migrating behind the cutoff.** After this slice every trade on a selected market — the actual fills, with price, size, and taker side — accumulates unattended on the timer that already runs, from the historical cutoff forward, with the ~60 days still on the live endpoint drained automatically over the first ~10 days.
- **The completeness definition becomes answerable for trades.** `status` reports, from persisted state alone, how far the tape is complete and how far behind it is, and which selected closed markets are tape-complete through close, partial, pending, or before coverage.
- **Architectural enablement.** 266 gets an exact, persisted coverage floor (`coverage_from_ts`) and a table it is idempotent against; `/historical/trades` has the same shape and cursor, so 266's drain is this phase's window loop pointed at another path.

## Technical Scope

**In scope:**

- `kalshi_006_trades` migration: `kalshi.trades` as a hypertable with compression (Decision 4); `kalshi.sync_state.coverage_from_ts`; corrected column comments; grants.
- The trades phase: `TradesPhase` in `collection_pass.py`, appended to `PASS_PHASES`; `TradeSync` core (windowed walk, per-page classify-and-write, per-window watermark advance, request cap); `TradeRepository`; `TradeSource` Protocol.
- The collection rule renamed from candle-specific to surface-neutral settings, with a loud guard for the old names (Decision 3); `selection_sql` gains the trade form.
- `mt data kalshi status` trades block (Rich and `--json`); `print_trade_summary` registered in `PHASE_RENDERERS`.
- Constants for every new comparison value; fixtures for a windowed page, a final page, and an empty window; unit, fixture, and integration tests; CHANGELOG; runbook 100 Kalshi subsection paragraph; `manta-trading.env.example` rename.

**Explicitly out of scope:**

- Trades behind the historical cutoff, and any tape range the collector could not reach live — reported here, drained by 266 from `/historical/trades`.
- Orderbook, streaming, or any per-market fetching (impossible at the measured volume).
- Cursor-resume of an interrupted window (Decision 7); a fetch pool (Decision 9); a rule-override or deeper-history lever (263 Decision 1).
- Retention, and any change to unit files, timer cadence, `mt-run`, or the installer.

## Dependencies

### Prerequisites

- 264 complete and cut over on manta9000 (`v0.10.0`; candle backlog drained — the trades drain should not start while the candle backlog is still using the hour).
- 262's catalog populated and walked every pass: `kalshi.markets ⋈ events ⋈ series` supplies `open_time`, `close_time`, `status`, `volume_fp`, `series.category`, `series.title`; `sync_state['catalog'].last_full_sync_at` is the walk start the window end trails (Decision 5).
- 261's `Trade` / `TradesPage` / `HistoricalCutoff` models, `KalshiClient.get_trades(cursor, min_ts, max_ts, limit)` — **no client change is needed** — and `Surface.TRADES`.
- TimescaleDB on the database host and the test cluster.

### Interfaces Required

- `KalshiRun` (263): `settings`, `client`, locked `conn`, `sink`, `run_id`, `clock`.
- `PassPhase` / `PhaseReport` / `PASS_PHASES` (263); `SyncOutcome`, `classify_outcome` (262/264).
- `SyncEvent` / `SyncEventSink` / `emit_in_thread` (262/264): `phase_finished` with `phase="trades"`; no new event type.
- `CatalogRepository.transaction()` and its `sync_state` statements; `selection_sql` and the catalog join (264, moved per Decision 3).
- `TRACKS["kalshi"]` (261): the ledger preflight covers `kalshi_006` for free (264 Decision 8).

## Architecture

### Component Structure

```
cli/commands/kalshi.py        pass ─► CollectionPass(run, PASS_PHASES)   (three phases)
                              status ─► read_catalog_status + read_candle_status + read_trade_status (new)
cli/commands/kalshi_render.py PHASE_RENDERERS[TRADES] = print_trade_summary; print_status gains the trades block

data/kalshi/collection_pass.py   PassPhaseName.TRADES = "trades"; TradesPhase; PASS_PHASES = (Catalog, Candles, Trades)
data/kalshi/trade_sync.py        TradeSync — cutoff → floor/watermark → window end → per window: pages → classify+write → advance
data/kalshi/trade_repository.py  TradeRepository — read_state, init_state, write_page (classify + insert + counts), advance_watermark
data/kalshi/trade_types.py       TradeResult (+ to_dict, counts), TradeSource Protocol, TradesBehindCutoffError, classify_trades
data/kalshi/selection.py         CollectionRule (was CandleRule), SelectionForm ("recent" | "ever" | "any"), selection_sql, CATALOG_JOIN   ← renamed/moved from candle_selection.py
data/kalshi/candle_selection.py  keeps the candle-only pieces: MARKET_JOIN (= CATALOG_JOIN + candle state), BACKLOG_CONDITION, BEHIND_CUTOFF_CONDITION
data/kalshi/constants.py         TRADE_* constants, KALSHI_TRADE_CHUNK_INTERVAL, KALSHI_TRADE_COMPRESS_AFTER
config/__init__.py               Settings.kalshi_collection_* (rename), Settings.collection_rule(), loud guard for MT_KALSHI_CANDLE_*
data/kalshi/status.py            TradeStatus, read_trade_status
market/schema/migrations/kalshi.py   kalshi_006_trades
scripts/record_kalshi_fixtures.py    --only trades_window, trades_window_last, trades_empty
```

Module boundaries follow 262/264: the core has no httpx, no typer, no SQL; it depends on a `TradeSource` Protocol (`get_trades`, `get_historical_cutoff`) and a `TradeRepository`. Each file stays under the ~300-line guideline by construction. `candle_*` modules change only where the rename touches them.

### Data Flow — the trades phase, one pass

1. **Cutoff and state.** `get_historical_cutoff().trades_created_ts` once (logged at INFO every run — the line that says how much live tape remains). Read `sync_state['trades']`: on the **first run** there is no row; the phase writes one with `coverage_from_ts = watermark_ts = cutoff` (Decision 2). If `watermark_ts < cutoff` on a later run, the tape between them is no longer served live: raise `TradesBehindCutoffError` naming the range (Decision 6) — never skip silently.
2. **Window end.** `end = sync_state['catalog'].last_full_sync_at − TRADE_LATE_ARRIVAL_GUARD` (Decision 5): every market that traded before the catalog walk began is in the catalog after it. No catalog row → the phase does nothing and says so.
3. **Windows, oldest first.** From `watermark_ts` in steps of `TRADE_WINDOW` (1 h), the last one clamped to `end`. Before each window: if `requests ≥ TRADE_REQUESTS_PER_PASS`, stop and mark the result `capped` (Decision 8) — the next pass continues from the watermark.
4. **One window.** Page through `get_trades(min_ts = start − WINDOW_OVERLAP, max_ts = end, limit = TRADE_PAGE_LIMIT, cursor)` until the cursor is empty. **Each page is one transaction** (`write_page`): the page's tickers are classified in SQL against the catalog and the rule (Decision 5) — *unknown* (no market row), *excluded* (known, rule does not select), *selected* — and the selected rows are inserted with `ON CONFLICT DO NOTHING`; the statement returns the three counts and the rows written, so `duplicates = selected − written`. Pages of a window commit as they go; the watermark does not move yet.
5. **Window done.** In one transaction: `sync_state['trades'].watermark_ts = end`, `updated_at = now()`. One INFO line per window (263 Decision 8): `trades window {start}→{end} pages N fetched F written W unknown U excluded X` — the operator's progress line during the drain.
6. **Finish.** `sync_state['trades'].last_full_sync_at = phase_start`; `phase_finished` with counts. Classification: `ProviderError` → `PROVIDER_ABORT`, `psycopg.OperationalError` → `STORAGE_ABORT`, else `OK` — this phase has no per-item failure and therefore never reports `PARTIAL` (Decision 9).

Under the abort rule (263 Decision 2) a catalog or candle abort skips this phase; a trades abort cannot affect the phases that already finished.

### State Management

- **`kalshi.sync_state['trades']`** — `watermark_ts`: *the tape is complete through this instant* (the end of the last fully walked window; **not** the newest stored trade — 261's comment is rewritten by `kalshi_006`); `coverage_from_ts` (new column): the instant the tape starts, set once on the first run and never moved; `last_full_sync_at`: start of the last pass whose trades phase ran to its end (capped or not); `cursor`: unused (Decision 7).
- **No per-market state.** A market's tape completeness is derived: complete through close when `close_time ≤ watermark_ts`.
- **A window is the unit of loss.** Pages commit independently (conflict-ignore makes a re-walk free); an interrupted phase re-walks at most one window (≤ ~550 requests).
- The catalog owns `close_time`, `volume_fp`, `category`; the rule is re-evaluated from them per page and stores nothing of its own.

## Technical Decisions

1. **One exchange-wide tape walk in one-hour windows, oldest first, under one watermark; never per market.** Measured: ~26 k distinct markets trade in an hour, so per-market requests would cost 26 k+ per hour against a 300/min budget — an 87-minute phase before any page is fetched twice. The global tape costs one request per 1,000 trades (~420/hour) regardless of the rule. Windows (`min_ts`/`max_ts`, verified to terminate a walk) turn the newest-first tape into an oldest-first drain whose watermark advances once per fully walked window — 262 Decision 4's shape, with `WINDOW_OVERLAP` (1 s) on the lower bound for the strict-at-second-granularity boundary. `TRADE_WINDOW = 1 hour` ≈ 300–550 pages; a window is what an abort loses. *Rejected:* per-market walks (cost above); a single unbounded newest-first walk to the watermark (nothing can be committed as complete until the last page, so a long catch-up that aborts near its end repeats entirely).

2. **The first run starts at the trades cutoff and drains the live tape forward under a per-pass request cap. PM-ratified 20260828.** Today ~60 days ≈ 600 k requests are still served live and fall behind the cutoff at about a day per day; started at the cutoff, the drain outruns it (7 days of tape per day at `TRADE_REQUESTS_PER_PASS = 3,000`) and reaches "now" in ~10 days, unattended, with `status` showing the lag shrinking. Oldest-first is also what keeps compression safe: chunks behind the watermark are never written again, so the policy compressing them is correct; only the chunk under the watermark can be met mid-write (Decision 4). *Alternative for the PM:* start at `now − 24 h` (264 Decision 5's shape), leaving the June–August tape to 266's operator-run historical drain — simpler for this slice, the same 600 k requests later, and dependent on Kalshi's unknown retention on `/historical/trades`. *Rejected:* a second, backward-moving watermark for history (two cursors, a compression race on every chunk, no gain in total work).

3. **The collection rule governs trades too, under a surface-neutral name. PM ratification requested — this is the storage decision.** The rule is one universe: a selected market has candles *and* trades, and one `status` figure explains exclusions. For trades the rule is a *storage* filter (the tape is fetched whole either way), applied per page in SQL (Decision 5) with the traded clause dropped — a trade is proof of trading (`SelectionForm = "any"`); `status` uses the `"ever"` form as candles do. The cost table in Discovery Findings is the PM's choice: **~155 GB/year compressed under the default rule**, ~240 GB/year for everything the catalog knows, ~41 GB/year if markets living ≤ 15 minutes were also excluded (73% of selected trades are on the 15-minute ladders the PM chose to *keep* for candles as the purest minute-scale moves). The design recommends the rule as it stands. Room on the host (measured 20260828): the database volume (`/`, holding `/var/lib/postgresql/17/main`) has 990 GB free of 1.8 TB; the production `trading` database is 159 GB. **Rename:** `MT_KALSHI_CANDLE_*` → `MT_KALSHI_COLLECTION_*` (`Settings.kalshi_collection_*`, `Settings.collection_rule()`, `CollectionRule` in `selection.py`), because a setting named *candle* that silently governs trades is a trap. A set `MT_KALSHI_CANDLE_*` variable would otherwise be ignored by pydantic-settings — a silent fallback to the defaults — so `Settings` fails loudly on any environment variable with the old prefix, naming the new one (CHANGELOG "Breaking"). *Cheaper alternative if the PM prefers no rename:* keep the names and say in the runbook and the `status` line that they govern both surfaces.

4. **`kalshi.trades` is a hypertable from creation, keyed `(market_ticker, created_time, trade_id UUID)`, 7-day chunks, compression policy at 14 days. PM-ratified 20260828** (the PM noted a time-only index may be wanted later for exchange-wide range reads — a one-line addition when a query needs it). The 260 slice plan asked for `(created_time, trade_id)`; measurement shows the ticker-first key serves per-market reads from the primary key alone and is 13 B/row cheaper than time-first plus the per-market index those reads would need. It still contains the partitioning column, so it is the in-place-promotable shape the plan wanted — and the table is a hypertable from day one anyway (arch *Volume and storage posture*, extended to 265). Idempotency is unchanged: Kalshi's ids are globally unique and a re-fetched trade is byte-identical, so conflict-ignore on the composite key rejects every duplicate the walk produces. `trade_id UUID`: 352,000 of 352,000 ids parse; a non-UUID id would fail the page's insert loudly (a `DataError` propagates as a bug), which is the fail-explicit posture — TEXT would cost 17 B/row compressed for a format Kalshi has never served. Chunking follows 264 Decision 4 and the 20260719 rule (`KALSHI_TRADE_CHUNK_INTERVAL = 7 days`, ~40 M rows/chunk under the rule); compression settings are the measured ones (`segmentby market_ticker`, `orderby created_time DESC`); `KALSHI_TRADE_COMPRESS_AFTER = 14 days` keeps the uncompressed buffer at ~21 GB. **The drain and the policy:** oldest-first means the policy only ever meets the chunk currently under the watermark; the rehearsal measures per-window wall time before and after compressing that chunk by hand (walkthrough step 6). If inserts into a compressed chunk prove slow, the lever is the standing 266 rule — pause the policy by hypertable name for the drain, resume after — as a runbook step, never automated (the application role cannot `alter_job`, by design of the 913 role split). Foreign key to `kalshi.markets` as candlesticks has; nothing references `public`.

5. **The window end trails the catalog walk; unknown-market trades are dropped and counted, never stored, never an error.** A trade for a market the catalog lacks is either MVE (8% of the tape, excluded on purpose) or a catalog defect. The design removes the honest race: window end = `sync_state['catalog'].last_full_sync_at − TRADE_LATE_ARRIVAL_GUARD` (1 min), so every non-MVE market that traded inside a window existed before the walk that just completed began, and the four walked statuses plus the settled stream list it. What remains is distinguishing MVE from a defect without parsing tickers (CLAUDE.md forbids labels as logic): the phase counts `unknown_market` and logs the distinct unknown ticker *prefixes* with counts at INFO once per phase (`trades unknown markets: KXMVECROSSCATEGORY 27,688 · …`) — display, not logic — so a non-MVE prefix is visible to an operator and the rehearsal lists the set. Classification runs in SQL per page: `unnest` of the page's rows `LEFT JOIN markets ⋈ events ⋈ series`, `selected = COALESCE(<rule "any">, FALSE)`, a data-modifying CTE inserting the selected rows and returning `unknown`, `excluded`, `written` in one round trip. *Rejected:* resolving unknown tickers through `GET /markets?tickers=` (+45% requests for a set that is measurably all MVE, and coupling to 262's parent-resolution internals); storing unknown-market trades without the FK (MVE is out of the initiative's scope by PM decision).

6. **A watermark behind the cutoff aborts the phase loudly; nothing jumps forward.** The condition needs a collector outage longer than the live window (~60 days). Skipping to the cutoff would lose a range no status figure could show; instead `TradesBehindCutoffError` names the range and 266 as the remedy, the pass exits nonzero, the unit shows failed, and the earlier phases are unaffected (trades runs last). 266 drains the range from `/historical/trades` and moves the watermark.

7. **No cursor resume; a window is the unit of loss.** The `cursor` column stays unused. Resuming an interrupted window would need the window's end persisted alongside an opaque cursor of unknown lifetime, to save at most ~550 requests. *Rejected* for the same reason 264 Decision 9 rejected concurrency: complexity without a measured need.

8. **The cap is in requests and checked before each window.** The 264 lesson (journal 20260827): state a cap in the unit the loop counts. `TRADE_REQUESTS_PER_PASS = 3,000` (≈ 10 minutes at the public budget); a pass may exceed it by one window (≤ ~550). Steady state never reaches it (~420 requests). The result records `capped`, and `status` shows the lag, so the drain's progress is visible pass over pass. Ordering within the pass is fixed by `PASS_PHASES`: the catalog (~5 min) and candles (~70 requests) always come first; the trades cap bounds the pass at roughly 15 minutes during the drain and ~3 minutes after.

9. **Sequential fetch and write; no item errors.** ~420 requests and ~420 inserts of 1,000 rows in steady state; the drain runs 3,000. The rehearsal records per-window wall time (264 measured ~0.6 s per request with a 10,000-row insert on the serial loop; here inserts are 1,000 rows). A prefetch of the next page is the lever if it matters, with evidence. There is no per-item failure mode: a page parses or the request fails (provider abort); unknown and excluded rows are counts, so the phase reports `OK` or an abort, never `PARTIAL`.

10. **`status` reads the database only.** Every trades figure comes from `sync_state['trades']` and `markets ⋈ events ⋈ series`; nothing counts rows in `kalshi.trades` (journal 20260720). Per-market completeness is derived from the single watermark and the coverage floor (see *CLI and rendering*).

11. **Fully structured rows; `no_price_dollars` kept, `taker_side` dropped.** Columns are the served fields: `count_fp`, `yes_price_dollars`, `no_price_dollars` (measured always `1 − yes`, stored anyway — capture, not derivation), `taker_outcome_side`, `taker_book_side` (Kalshi's vocabulary as TEXT; venue-owned, so data, not an enum), `is_block_trade`. The deprecated `taker_side` duplicates `taker_outcome_side` and is not stored. No `raw` (261 Decision 6). `TRADE_COLUMNS` in `trade_repository.py` is the model→column map, parity-tested against the table.

## Implementation Details

### Constants (`constants.py`, each defined once, each citing its decision)

```python
TRADE_PAGE_LIMIT = 1_000                              # verified ceiling (1,001 → HTTP 400)
TRADE_WINDOW = timedelta(hours=1)                     # Decision 1: one window ≈ 300–550 pages at measured volume
TRADE_LATE_ARRIVAL_GUARD = timedelta(minutes=1)       # Decision 5: window end trails the catalog walk start by this
TRADE_REQUESTS_PER_PASS = 3_000                       # Decision 8: checked before each window
TRADE_LAG_STALE_AFTER = timedelta(hours=2)            # status: tape more than two firings behind is "behind"
KALSHI_TRADE_CHUNK_INTERVAL = timedelta(days=7)       # Decision 4
KALSHI_TRADE_COMPRESS_AFTER = timedelta(days=14)      # Decision 4
```

`WINDOW_OVERLAP` (262) is reused for the lower bound. `PassPhaseName.TRADES = "trades"` names the phase in reports, events, the JSON summary, and log lines; `Surface.TRADES` already exists.

### Settings — the rename (`config/__init__.py`, Decision 3)

- Fields `kalshi_collection_traded_only`, `kalshi_collection_categories`, `kalshi_collection_excluded_categories`, `kalshi_collection_excluded_series_pattern`, `kalshi_collection_excluded_title_pattern` — same defaults, same validators; `Settings.collection_rule() -> CollectionRule`. `traded_only` keeps its meaning for candles (the phase that schedules on it) and is documented as not applying to trades.
- Environment form: `MT_KALSHI_COLLECTION_TRADED_ONLY`, `MT_KALSHI_COLLECTION_CATEGORIES`, `MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES`, `MT_KALSHI_COLLECTION_EXCLUDED_SERIES_PATTERN`, `MT_KALSHI_COLLECTION_EXCLUDED_TITLE_PATTERN`.
- A `model_validator(mode="after")` raises with the rename message if any environment variable starts with the old `MT_KALSHI_CANDLE_` prefix (the prefix string defined once beside the new one). `deploy/manta-trading.env.example` and runbook 100 are updated; the CHANGELOG entry is marked breaking.
- `CollectionRule.describe()` is unchanged; the candle `status` line reads `(MT_KALSHI_COLLECTION_*)`.

### `selection.py` (moved from `candle_selection.py`)

- `CollectionRule` (frozen dataclass, was `CandleRule`), `SelectionForm = Literal["recent", "ever", "any"]` — `any` omits the traded clause (Decision 3) — `selection_sql(rule, form) -> Selection`, and `CATALOG_JOIN = "FROM kalshi.markets m JOIN kalshi.events e … JOIN kalshi.series s …"`. `candle_selection.py` keeps `MARKET_JOIN` (composed as `CATALOG_JOIN` + the candle-state `LEFT JOIN`), `BACKLOG_CONDITION`, `BEHIND_CUTOFF_CONDITION`. `candle_types.py` re-exports nothing; imports move.

### Core (`trade_sync.py`) and types (`trade_types.py`)

```python
class TradeSource(Protocol):
    async def get_trades(self, *, cursor: str | None = None, min_ts: int, max_ts: int, limit: int) -> TradesPage: ...
    async def get_historical_cutoff(self) -> HistoricalCutoff: ...

@dataclass
class TradeResult:  run_id, started_at, cutoff, coverage_from, watermark_before, watermark_after,
                    windows_completed, requests, trades_fetched, trades_written, unknown_market,
                    excluded_by_rule, duplicates, capped: bool, unknown_prefixes: dict[str, int],
                    duration_ms, error
class TradesBehindCutoffError(Exception): ...          # Decision 6; propagates out of the pass
def classify_trades(result, exc) -> SyncOutcome:       # classify_outcome(False, exc)
```

`TradeSync.run()` is Data Flow steps 1–6; `_window(start, end)` is step 4–5. The unknown-prefix tally is kept in memory for the log line only (ticker text before the first `-`, display only).

### Repository (`trade_repository.py`)

- `read_state() -> TradeState | None` (`watermark_ts`, `coverage_from_ts`); `init_state(cutoff)` (insert the row with both set to the cutoff — first run only).
- `write_page(rows) -> PageCounts` — one statement, one transaction (Decision 5):

```sql
WITH page AS (SELECT * FROM unnest(%(tickers)s, %(created)s, %(ids)s::uuid[], …) AS p(market_ticker, created_time, trade_id, …)),
     classified AS (
       SELECT p.*, m.ticker IS NOT NULL AS known, COALESCE({rule_any}, FALSE) AS selected
       FROM page p LEFT JOIN kalshi.markets m ON m.ticker = p.market_ticker
       LEFT JOIN kalshi.events e ON e.event_ticker = m.event_ticker
       LEFT JOIN kalshi.series s ON s.ticker = e.series_ticker),
     ins AS (INSERT INTO kalshi.trades (…) SELECT … FROM classified WHERE selected
             ON CONFLICT DO NOTHING RETURNING 1)
SELECT count(*) FILTER (WHERE NOT known), count(*) FILTER (WHERE known AND NOT selected),
       count(*) FILTER (WHERE selected), (SELECT count(*) FROM ins) FROM classified;
```

  `{rule_any}` is `selection_sql(rule, "any").predicate`, the only rendering of the rule; the arrays are bound parameters (one statement per page of 1,000 — under the bind-parameter ceiling by construction: nine arrays, not 9,000 placeholders).
- `advance_watermark(end)`; `set_last_full_sync(phase_start)` via `CatalogRepository`'s `sync_state` statements; `transaction()`.

Storage taxonomy: `OperationalError` propagates (storage abort); any other `psycopg.Error` propagates as a bug — there is no per-market rewrite because the FK is guaranteed by the join and duplicates are conflict-ignored.

### `collection_pass.py`

```python
class PassPhaseName(StrEnum):
    CATALOG = "catalog"; CANDLES = "candles"; TRADES = "trades"

class TradesPhase:   # same try/except/classify shape as CandlesPhase; rule from run.settings.collection_rule()
PASS_PHASES = (CatalogPhase(), CandlesPhase(), TradesPhase())
```

### `TradeResult.to_dict()` (the phase's `summary`)

```json
{"run_id": "...", "started_at": "...", "cutoff": "2026-06-29T00:00:00+00:00",
 "coverage_from": "2026-06-29T00:00:00+00:00",
 "watermark": {"before": "2026-07-03T00:00:00+00:00", "after": "2026-07-03T07:00:00+00:00"},
 "windows_completed": 7, "requests": 3004, "capped": true,
 "trades_fetched": 2998113, "trades_written": 1770214, "unknown_market": 244900,
 "excluded_by_rule": 982999, "duplicates": 0,
 "unknown_prefixes": {"KXMVECROSSCATEGORY": 244900},
 "duration_ms": 612040, "error": null}
```

### Migration `kalshi_006_trades`

```sql
CREATE TABLE IF NOT EXISTS kalshi.trades (
    market_ticker       TEXT        NOT NULL REFERENCES kalshi.markets (ticker),
    created_time        TIMESTAMPTZ NOT NULL,
    trade_id            UUID        NOT NULL,
    count_fp            NUMERIC     NOT NULL,
    yes_price_dollars   NUMERIC     NOT NULL,
    no_price_dollars    NUMERIC     NOT NULL,
    taker_outcome_side  TEXT,
    taker_book_side     TEXT,
    is_block_trade      BOOLEAN     NOT NULL,
    PRIMARY KEY (market_ticker, created_time, trade_id)                              -- Decision 4
);
SELECT create_hypertable('kalshi.trades', 'created_time', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
ALTER TABLE kalshi.trades SET (timescaledb.compress, timescaledb.compress_segmentby = 'market_ticker',
                               timescaledb.compress_orderby = 'created_time DESC');
SELECT add_compression_policy('kalshi.trades', compress_after => INTERVAL '14 days', if_not_exists => TRUE);
ALTER TABLE kalshi.sync_state ADD COLUMN IF NOT EXISTS coverage_from_ts TIMESTAMPTZ;
COMMENT ON COLUMN kalshi.sync_state.coverage_from_ts IS 'trades: the instant the stored tape starts (the trades cutoff observed on the first run); set once, never moved (slice 265, Decision 2). NULL for other surfaces.';
COMMENT ON COLUMN kalshi.sync_state.watermark_ts IS '… trades: the tape is complete through this instant (end of the last fully walked window) — NOT the newest stored trade (slice 265, Decision 1). candlesticks: …';
COMMENT ON COLUMN kalshi.sync_state.cursor IS '… trades: unused — windows replace cursor resume (slice 265, Decision 7).';
GRANT SELECT, INSERT, UPDATE, DELETE ON kalshi.trades TO trading_app;
```

Both intervals render from the constants (`_interval_sql`); the `COMMENT ON` statements carry the other surfaces' clauses forward unchanged. Additive and idempotent; no down-migration. Applied by the operator from the dev checkout with the maintenance credential before the first firing after the install; a firing before that exits 1 naming `kalshi_006_trades` (264 Decision 8).

### CLI and rendering

- `mt data kalshi pass` — unchanged surface; `print_trade_summary(summary)` prints windows, requests (and `capped`), watermark before → after, fetched / written / unknown / excluded / duplicates.
- `mt data kalshi status` — `read_trade_status(conn, rule) -> TradeStatus | None` (None until the phase has run once; Rich prints "Trades: never collected", JSON `"trades": null`). Fields, every one a persisted fact:
  - `last_phase_at`, `tape_through` (`watermark_ts`), `lag` (`now − watermark_ts`), `behind` (`lag > TRADE_LAG_STALE_AFTER`), `coverage_from`
  - over selected closed markets (`"ever"` form; `close_time < now()`):
    `complete_through_close` — `open_time ≥ coverage_from AND close_time ≤ watermark`;
    `partial_history` — `open_time < coverage_from ≤ close_time` (the tape starts mid-life);
    `short_of_close` — `close_time > watermark` (the tape has not reached them yet; large during the drain, ~0 after);
    `before_coverage` — `close_time < coverage_from` — 266's input for trades.
  - No `excluded_by_rule` here: one rule, one figure, already in the candle block.
- Rich block:

```
Kalshi trades              last phase 2026-08-28 15:24:11 UTC  (3 min ago)   cutoff 2026-06-29
  tape through        2026-08-28 15:19:10 UTC  (8 min behind)        coverage from 2026-06-29 00:00 UTC
  closed markets      complete through close 412,010   partial history 6,120   short of close 310
  before coverage     1,203,442 closed markets (tape predates the collector; slice 266)
```

### Fixtures and recorder

- `--only trades_window` — a windowed page (`min_ts`/`max_ts` one minute apart, `limit=100`) with a cursor; `trades_window_last` — the same window's final page (`cursor: ""`); `trades_empty` — a future window (`{"cursor": "", "trades": []}`). Existing `trades_page1/2` stay for the 261 client test.

### Runbook 100 and CHANGELOG

- Kalshi subsection gains one paragraph: the pass has three phases; the collection rule now governs candles and trades and is set by the `MT_KALSHI_COLLECTION_*` lines (renamed — an old `MT_KALSHI_CANDLE_*` line fails the pass at start, naming the new name); `kalshi_006` must be applied during the update; the first ~10 days after the release drain the live tape from the cutoff (each pass ~15 minutes, `status` shows the lag falling); how to see the trades compression policy by hypertable name, and that a drain that proves slow against compressed chunks is paused/resumed the 266 way.
- CHANGELOG under `[Unreleased]`: trades phase, status block, migration (hypertable), **breaking** settings rename.

### Tests

- **Unit — core** (`FakeTradeSource` serving scripted pages, in-memory fake repository): first run initialises state at the cutoff; window sequence from the watermark, last one clamped to the catalog walk start minus the guard; no catalog row → nothing fetched; per-page write counts aggregate; watermark advances only after a window's last page; an abort mid-window leaves the watermark; the cap stops before a window and sets `capped`; `watermark < cutoff` raises `TradesBehindCutoffError`; classification; events; the unknown-prefix tally.
- **Unit — settings, selection, fixtures, rendering:** every `MT_KALSHI_COLLECTION_*` form parses as before; an `MT_KALSHI_CANDLE_*` variable fails loudly with the rename message; `selection_sql(rule, "any")` has no traded clause and `"recent"`/`"ever"` are unchanged; the three trade fixtures parse (empty cursor terminates); `PASS_PHASES == (catalog, candles, trades)`; renderer dispatch; JSON round-trip; `status.py` imports neither the client nor the transport.
- **Integration (`kalshi_db`):** `kalshi_006` applies and re-applies; `kalshi.trades` is a hypertable with the configured chunk interval, compression settings, and a policy whose `compress_after` equals the constant (read by hypertable name); running that job on a chunk older than the horizon compresses it and the rows read back identical; `TRADE_COLUMNS` parity; `write_page` against fixture markets with synthesized series — a Sports trade is excluded and counted, a `KXMVE…` ticker with no market row is unknown and counted, a Politics trade is written, a second write of the same page writes 0 and counts duplicates, under `MT_KALSHI_COLLECTION_CATEGORIES=Sports` the Sports trade is the one written; a page carrying a non-UUID id fails the write loudly; `advance_watermark`; an end-to-end three-phase `pass` on a small catalog, and a second pass that writes nothing new; preflight exit 1 names `kalshi_006_trades`; `status` shape and every count, including `before_coverage` and `partial_history` against markets straddling the floor.
- Gates as 264.

## Integration Points

### Provides to Other Slices

- **266 (historical backfill):** `sync_state['trades'].coverage_from_ts` — the exact upper bound of the trade range it must drain from `/historical/trades` (same shape, same cursor, same `write_page`); `before_coverage` — the selected markets that range covers; the table it is idempotent against; and the same standing rule: the policy is paused by hypertable name for its drain and resumed after. If a `TradesBehindCutoffError` ever fires, 266 is the remedy and moves the watermark.
- **Future streaming work:** the tape's measured rate (~120 trades/s peak) is the load a websocket collector must absorb.

### Consumes from Other Slices

- 263's phase contract, abort rule, shared `run_id`, and the per-window INFO line convention.
- 262's catalog columns and cadence, `sync_state['catalog'].last_full_sync_at`, `WINDOW_OVERLAP`, `Surface.TRADES`.
- 264's `selection_sql`, catalog join, ledger preflight, and the hypertable/compression test pattern.
- 261's `Trade`/`TradesPage`/`HistoricalCutoff`, `get_trades`, transport error taxonomy.

## Success Criteria

1. **The pass has three phases in order.** `mt data kalshi pass --json` reports `phases[].name == ["catalog", "candles", "trades"]`; a catalog or candle abort reports the trades phase `skipped`; a trades abort leaves the earlier phases' outcomes and state intact.
2. **Trades land under the key, and only selected ones.** After one pass over a window, `kalshi.trades` holds every trade whose market the catalog knows and the rule selects; the phase summary's `unknown_market` and `excluded_by_rule` account for the rest exactly (`fetched = written + unknown + excluded + duplicates`).
3. **A second pass writes only what is new** — re-walking the overlap writes 0 rows and the duplicate-key query returns 0.
4. **The watermark moves per window and never mid-window.** After a pass, `watermark_ts` equals the end of the last completed window; a phase aborted inside a window leaves `watermark_ts` where it was and the next pass re-walks that window.
5. **The rule is one rule, configuration, and renamed.** Under the defaults a Sports trade is excluded and a Politics trade stored; with `MT_KALSHI_COLLECTION_CATEGORIES=Sports` and the exclusions cleared the Sports trade is the one stored; an `MT_KALSHI_CANDLE_*` variable in the environment fails the command at start, naming the new prefix; the candle phase behaves exactly as before the rename.
6. **The first run starts at the cutoff and says so.** On an empty `sync_state['trades']`, `coverage_from_ts == watermark_ts == trades_created_ts` as observed, and the phase logs the cutoff; a watermark behind the cutoff aborts with the range named and the earlier phases' results intact.
7. **The window end trails the catalog walk.** No window ends after `sync_state['catalog'].last_full_sync_at − 1 min`; with no catalog row the phase fetches nothing and reports it.
8. **The cap is in requests and visible.** A pass with more tape pending than `TRADE_REQUESTS_PER_PASS` stops after the window that crosses it, reports `capped: true`, and the next pass continues from the watermark; `status` shows the lag falling pass over pass.
9. **Unknown markets are dropped, counted, and their prefixes logged** — never stored, never an item error, never an abort.
10. **Preflight names the missing migration** (exit 1 with `kalshi_006_trades` until `mt data migrate apply --track kalshi`).
11. **`status` answers the trade clause from the database alone** — every field in *CLI and rendering*; `status.py` imports neither the client nor the transport.
12. **Compression is live and proven on a real chunk** — a policy on `kalshi.trades` with `compress_after = 14 days`, and the integration test compresses an old chunk with rows identical afterwards; the rehearsal records per-window wall time before and after compressing the chunk under the watermark.
13. **Production: the timer runs three phases unattended.** After the release is installed and `kalshi_006` applied, the next firing's journal shows `phases: catalog=ok candles=ok trades=ok`, `Result=success`, the trades phase capped at ~3,000 requests; over the following days `mt-run data kalshi status` shows `tape through` advancing ~7 hours per firing until `behind` clears, then staying within two hours of now.

## Verification Walkthrough

Steps 1–7 run on a UUID-named throwaway database on the test cluster (`MT_TIMESCALE_DB_URL` pointed at it for those commands only; the production URL never enters the shell). Steps 8–10 are on manta9000 and are the PM's. Expected outputs are drafts to be replaced by observed ones at Phase 6.

**1. Throwaway database, migrated, with a small catalog.**

```bash
uv run mt data migrate apply --track kalshi          # → … kalshi_006_trades applied
uv run mt data migrate status --track kalshi         # → 0 pending
psql "$MT_TIMESCALE_DB_URL" -c "select hypertable_name, compression_enabled from timescaledb_information.hypertables where hypertable_schema='kalshi'"
#    → candlesticks | t ;  trades | t
uv run mt data kalshi sync --settled-since "$(date -u -d '6 hours ago' +%FT%TZ)"     # a catalog to join against
```

**2. Preflight names a missing migration (Criterion 10).**

```bash
psql "$MT_TIMESCALE_DB_URL" -c "delete from schema_migrations where migration_id='kalshi_006_trades'"
uv run mt data kalshi pass      # → Error: kalshi track has pending migrations: kalshi_006_trades — …  exit 1
uv run mt data migrate apply --track kalshi
```

**3. The rename is loud, and the rule still moves (Criterion 5).**

```bash
MT_KALSHI_CANDLE_CATEGORIES=Sports uv run mt data kalshi status
#    → error naming MT_KALSHI_COLLECTION_*; nonzero exit
uv run mt data kalshi status --json | jq .candles.rule.description
#    → "traded 24h · categories all · excluding Mentions, Sports · patterns 2"   (unchanged by the rename)
```

**4. First pass: three phases, the first-run floor, the cap (Criteria 1, 2, 6, 8, 9).** The throwaway catalog is hours old, so set the drain start close enough to finish in a few windows — the design's first-run rule is the cutoff (~60 days); for the rehearsal, seed the row by hand at `now − 3 h` and record that this substitutes for step 4's cutoff start, which the host step proves.

```bash
psql "$MT_TIMESCALE_DB_URL" -c "insert into kalshi.sync_state (surface, watermark_ts, coverage_from_ts) values ('trades', now() - interval '3 hours', now() - interval '3 hours')"
uv run mt data kalshi pass --events-file trades-pass1.jsonl
#    journal: kalshi trades phase started … cutoff=2026-06-29T00:00:00+00:00 coverage_from=… watermark=…
#             trades window …→… pages 3xx fetched 3xx,xxx written 2xx,xxx unknown 2x,xxx excluded 1xx,xxx   (× 3)
#             trades unknown markets: KXMVECROSSCATEGORY 8x,xxx
#             kalshi pass finished outcome=ok … phases: catalog=ok candles=ok trades=ok
#    summary: fetched = written + unknown + excluded + duplicates   (Criterion 2, checked by eye and by jq)
psql "$MT_TIMESCALE_DB_URL" -c "select watermark_ts, coverage_from_ts from kalshi.sync_state where surface='trades'"
#    → watermark = catalog last_full_sync_at − 1 min (Criterion 7); coverage_from unchanged
psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.trades t join kalshi.markets m on m.ticker=t.market_ticker join kalshi.events e using (event_ticker) join kalshi.series s on s.ticker=e.series_ticker where s.category in ('Sports','Mentions')"   # → 0
```

**5. Second pass, duplicates, status (Criteria 3, 4, 11).**

```bash
uv run mt data kalshi pass --json | jq '.phases[2].summary | {windows_completed, requests, trades_written, duplicates}'
#    → one short window; duplicates equal the 1-second overlap's rows; nothing else written twice
psql "$MT_TIMESCALE_DB_URL" -c "select count(*) from kalshi.trades a join kalshi.trades b using (market_ticker, created_time, trade_id) where a.ctid <> b.ctid"   # → 0
uv run mt data kalshi status
#    Kalshi trades   last phase … (0 min ago)   cutoff 2026-06-29
#      tape through   … (1 min behind)   coverage from …
#      closed markets complete through close N   partial history P   short of close 0
#      before coverage B closed markets (…)
```

**6. Compression, and the drain against a compressed chunk (Criterion 12).**

```bash
psql "$MT_TIMESCALE_DB_URL" -c "select job_id, scheduled, config from timescaledb_information.jobs where proc_name='policy_compression' and hypertable_schema='kalshi' and hypertable_name='trades'"
# resolve the id above, then (two statements — a subquery is not a valid CALL argument, journal 20260827):
psql "$MT_TIMESCALE_DB_URL" -c "call run_job(<job_id from the line above>)"
psql "$MT_TIMESCALE_DB_URL" -c "select compress_chunk(c) from show_chunks('kalshi.trades') c"     # force the chunk under the watermark compressed
# seed the watermark back one hour and re-run the pass: the per-window INFO line's wall time is the measurement
psql "$MT_TIMESCALE_DB_URL" -c "update kalshi.sync_state set watermark_ts = watermark_ts - interval '1 hour' where surface='trades'"
uv run mt data kalshi pass                       # → compare the window's duration with step 4's; record both in the rehearsal notes
```

**7. Abort inside a window leaves the watermark (Criterion 4)** — run the pass with the transport's retry budget exhausted mid-window (inject a `ProviderError` after page 2 via the unit-test fake) and confirm `watermark_ts` unchanged; this is the integration test's job and is recorded here as the manual analogue.

**8. Production deploy (PM).** Runbook 100 *Update procedure*: tag → `install-production.sh --ref` → `uv run mt data migrate status --track kalshi` (1 pending) → `apply` (maintenance credential) → `status` 0 pending → replace `MT_KALSHI_CANDLE_*` lines in `/etc/manta-trading.env` with `MT_KALSHI_COLLECTION_*` if any are set (the example file shows the new names; unset lines need nothing).

**9. First supervised firing (Criterion 13).**

```bash
sudo mt-run kalshi ; mt-run follow kalshi
journalctl -u mt-kalshi-pass.service --grep 'kalshi pass finished' -n 1
#    → … phases: catalog=ok candles=ok trades=ok   (trades capped at ~3,000 requests, ~10–15 min)
journalctl -u mt-kalshi-pass.service --grep 'trades window' | tail -3          # ~7 windows, June 29 onward
mt-run data kalshi status                                                     # tape through 2026-06-29 07:00 …  (60 d behind)
```

**10. The drain, over the following days.** `mt-run data kalshi status` each day: `tape through` advances ~7 days per day; `short of close` falls; `before coverage` is constant (266's number). `journalctl … | grep -c 'HTTP 429'` per firing stays at attempt 1. When `behind` clears (~10 days), a steady-state pass is ~800–900 requests and ~3 minutes. If any firing's `trades window` lines show a window taking minutes rather than seconds, the chunk under the watermark was compressed by the policy — pause it by hypertable name for the remainder of the drain (runbook), resume after.

## Risk Assessment

- **Storage.** ~155 GB/year compressed under the rule is a materially larger commitment than any other Kalshi surface; the PM ratifies Decision 3 with the host's free space in hand. Every stricter option in the cost table is one clause in the rule (a lifetime floor would be a constant plus one clause, as 264 noted).
- **Inserts into compressed chunks during the drain.** Only the chunk under the watermark is exposed (Decision 4); step 6 measures it before the host sees it, and the pause/resume lever needs no code.
- **Late-arriving trades.** The tape is append-ordered by `created_time`; a fill that becomes visible after the window covering its `created_time` has been walked would be missed. The one-minute guard and the catalog-walk lag (windows end ≥ 5 minutes behind "now") are the mitigation; the rehearsal re-walks a settled hour a day later and diffs the count as the check.

## Decisions requiring Project Manager ratification

- **Decision 2** — ✅ ratified 20260828: first run starts at the trades cutoff and drains ~60 days of live tape forward under the per-pass cap.
- **Decision 3** — trades are filtered by the same collection rule (~155 GB/year compressed; alternatives 240 / 41 GB/year), and the settings are renamed `MT_KALSHI_COLLECTION_*` with a loud guard (vs. keeping the candle names). Free disk confirmed: 990 GB on the database volume.
- **Decision 4** — ✅ ratified 20260828: primary key `(market_ticker, created_time, trade_id)`, `trade_id` as `UUID`; the 260 slice plan's key note is updated to match. A time-only index is a later addition if exchange-wide range reads need it.

## Implementation Notes

### Development Approach

Sections, each a checkpoint commit:

1. The rename (`selection.py`, settings, guard, env example) with the candle suite green throughout.
2. Migration `kalshi_006` + parity, hypertable, and policy tests.
3. Repository: `write_page` with the predicate fixture set in the integration tier; state statements.
4. `TradeSync` core with fakes; `TradesPhase`; `PASS_PHASES` append; renderer dispatch; fixtures and recorder.
5. `status` block.
6. Rehearsal on the test cluster (walkthrough 1–7) recorded in `user/notes/2026-MM-DD-265-rehearsal.md` (with the unknown-prefix listing and the compressed-chunk timing), then runbook + CHANGELOG, then the host steps.

Scope every `ruff`/format invocation to the files touched.

### Special Considerations

- `write_page` joins 1,000 rows to the catalog per page (primary-key lookups; ~10 k statements a day); the rehearsal records its wall time from the per-window line. If the join dominates, a per-phase cache of ticker → selected for tickers seen this phase is the first lever (the 15-minute ladders repeat across pages).
- The phase logs the cutoff and the coverage floor every run; a cutoff that reaches the watermark is the signal that 266 has become urgent.
- Nothing in this slice writes data older than the watermark; the only writer of pre-coverage data is 266, which pauses the policy.
