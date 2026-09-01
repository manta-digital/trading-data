---
docType: slice-design
slice: historical-backfill-phase
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [264, 265]
interfaces: []
effort: 3
dateCreated: 20260831
dateUpdated: 20260901
status: in_progress
---

# Slice Design: Historical Backfill Phase (267)

## Overview

Kalshi serves everything older than its live cutoff from `/historical/*`
(verified in 261: same shapes, same cursor pagination, unauthenticated). Today
that data is only *counted* — `mt data kalshi status` reports 20,937 selected
closed markets whose trades predate the collector (`before coverage`) and 8,394
selected finalized markets whose candles fell behind the cutoff
(`behind cutoff, uncollected`) — and nothing fetches it.

**The catalog does not know most of the markets the backfill will meet.**
Read 2026-08-31 on production: the oldest finalized settlement in
`kalshi.markets` is 2026-06-25 (the cutoff at the first cold start) and only
9,835 markets close before 2026-06-01. 265's `write_page` keeps a trade only
when the catalog has its market (that is how the collection rule is
applied), so a trades walk into spring 2026 would classify nearly every
trade `unknown` and store nothing. The phase therefore walks Kalshi's market
archive (`GET /historical/markets`) into the catalog **before** it walks the
tape — the same catalog-before-tape rule the live pass already follows
(265 Decision 5). Decision 9.

The retired slice 266 made the backfill an operator-run drain that had to wait
for the live trades drain to finish. **This slice replaces it with a fourth
phase of the existing hourly pass.** The phase starts on the first firing after
the release is installed, runs every hour under its own request cap, and
trickles the historical range in behind the live phases. No new unit, timer,
command, or operator step; no waiting on anything; progress is visible in
`status` every hour and the phase stops by itself when the floor is reached.

## Value

- Both "known-lost" counts shrink while the phase runs. `behind cutoff,
  uncollected` first **grows** — the archive walk adds every pre-cutoff
  market the catalog never knew (Decision 9) — then falls as markets are
  stamped; `before coverage` falls as the
  historical watermark descends, because `status` partitions the closed
  markets against the *effective* floor — the oldest hour the tape covers —
  not the live row's floor (Decision 8). A visible floor replaces a permanent
  caveat.
- Zero human involvement: the same install that ships the release starts the
  drain; the pass already exists, is supervised, and is resumable.
- One request budget, sequenced: the phases run one after another inside one
  process, so the historical drain never competes with the live drain for the
  public-tier rate limit.

## Technical Scope

**In:** a `HistoricalPhase` appended to `PASS_PHASES` after `trades`; three
client methods (`get_historical_markets`, `get_historical_trades`,
`get_historical_market_candlesticks`) with recorded fixtures; a one-time,
resumable walk of the market archive into the catalog through 262's
`CatalogSync.ingest_markets` (parent resolution included), its cursor kept in
`sync_state['historical'].cursor`; a `HistoricalTradeSource` adapter so
265's window loop and `write_page` are reused unchanged; a bounded
per-market candle fetch for the behind-cutoff set; one `sync_state` row
(`historical`) carrying the downward trades watermark; migration
`kalshi_007` widening the `surface`
CHECK; a `status` line; the renderer, fixtures, tests, runbook paragraph.

**Out:** any change to the live phases' behaviour or caps; pausing compression
policies (Decision 4); a CLI to drive the backfill by hand (the phase is the
driver; `mt data kalshi pass` from a shell already runs it).

## Architecture

### The phase, one firing

```
catalog → candles → trades → historical
                                ├─ 0. archive walk (until done, once): pages of
                                │     /historical/markets newest-first, each page
                                │     through CatalogSync.ingest_markets (parents
                                │     resolved, upsert); cursor saved per page;
                                │     stops one day past HISTORICAL_TRADES_FLOOR
                                ├─ 1. behind-cutoff candles: up to
                                │     HISTORICAL_CANDLE_MARKETS_PER_PASS markets,
                                │     /historical/markets/{ticker}/candlesticks,
                                │     write via CandleRepository (conflict-ignore),
                                │     stamp market_candle_state → market leaves the set
                                └─ 2. historical trades: 1-hour windows walking
                                      BACKWARD from the row's watermark (initially
                                      the live coverage_from_ts) toward
                                      HISTORICAL_TRADES_FLOOR, /historical/trades,
                                      TradeRepository.write_page per page (one
                                      transaction per page, watermark moves per
                                      window — 265's loop, reversed direction),
                                      until the phase's request cap is spent
```

Requests are counted across the three sub-drains against one cap. The
archive walk runs first and only until it is done (a few thousand requests
once — Decision 9); trades never start while it is incomplete, so no trade is
classified before its market is known. Candles run next under a per-pass
market ceiling while the trades drain is still descending, and without it
once `floor_reached` (Decision 9); the trades drain takes whatever budget
remains each firing.

### State

`kalshi.sync_state['historical']`: `watermark_ts` = the oldest hour fully
walked (moves **down**), `coverage_from_ts` = `HISTORICAL_TRADES_FLOOR` (the
target, recorded so `status` can show distance), `last_full_sync_at` = last
phase completion, `cursor` = the archive walk's resume cursor while the walk
is in progress, `NULL` before it starts and after it completes (the column
exists since kalshi_003 and no other surface uses it). The live `trades`
row is untouched. `status` derives an **effective floor** =
`min(trades.coverage_from_ts, historical.watermark_ts)` (the live floor until
the historical row exists) and the four closed-market buckets partition against
it, so `before coverage` means what it says —
closed before any hour the tape covers — and shrinks as the watermark
descends. The historical line reports the tape range and the distance to
`HISTORICAL_TRADES_FLOOR`.

### Data flow guarantees carried from 265

`fetched = written + unknown + excluded + duplicates` per page and per window;
unknown-market trades dropped and prefix-logged; the collection rule applied
through `selection_sql`; idempotent on `(market_ticker, created_time,
trade_id)`; watermark advances only after a window's last page committed.

## Technical Decisions

1. **A pass phase, not an operator drain.** The only reason 266 waited was
   rate-budget contention with the live drain and a clean observation window.
   Sequencing the historical work *after* the live phases inside one process
   removes the contention by construction; observation is per-firing in
   `status`. Nothing waits.
2. **The pass runs authenticated; the cap is thirty minutes of the budget.
   PM-ratified 20260831.** The PM's Kalshi API key is installed on the
   host (`/etc/manta-trading/kalshi.pem`, `root:manta-trading 0640`;
   `MT_KALSHI_API_KEY_ID` and `MT_KALSHI_PRIVATE_KEY_PATH` in
   `/etc/manta-trading.env`), so the client runs in the signed mode 261 built,
   on the documented Basic budget (`KALSHI_AUTHENTICATED_RATE_LIMIT`,
   1,000/min). The cap is still counted in requests (the 264 rule) but sized
   from the mode's budget: `HISTORICAL_PHASE_MINUTES = 30`, cap = the client's
   `requests_per_minute × 30` → **30,000 authenticated, 9,000 public** — the
   phase never runs longer than half an hour in either mode, and with catalog
   (~2 min), candles (~1 min) and the live trades cap (3,000 ≈ 3 min
   authenticated) the pass stays under ~40 min during the live drain. The
   collector still works with no key configured (arch constraint), just
   slower. *Rejected:* the earlier draft's fixed 1,500/pass — ~5 min of work
   then 55 idle, stretching the backfill to weeks for no reason; a fixed
   10,000 — right for one mode, over the hour in the other; and the
   Advanced-tier upgrade (a free API call, 30 r/s) — not needed at this volume.
3. **`HISTORICAL_TRADES_FLOOR = 2026-01-01T00:00Z`. PM-ratified 20260831.**
   Measured 20260831 through `/historical/trades` (14:00 UTC hour, 1,000
   trades/page): 2026-01-15 39 pages, 03-15 102, 05-15 148, 06-15 213. Jan–Jun
   2026 ≈ **450k requests** (±30%) ≈ 400 M trades ≈ 17 GB compressed under
   the rule (265's 71.5 B/trade × 59% selected). At the authenticated cap that
   is ~15 firings — overnight — after the candle set clears. Extending the
   floor later is one constant edit; the phase continues downward from where
   it stopped (everything before 2026 is thinner still, and an empty hour costs
   one request).
4. **Compression policies stay on.** 265's rehearsal measured no penalty
   inserting into a compressed trades chunk (0.19 vs 0.21 s/page). Candles
   into compressed chunks are unmeasured: the phase logs per-market wall time
   and warns above `HISTORICAL_SLOW_MARKET_SECONDS`; the manual pause lever in
   runbook 100 remains the remedy — never automated (unchanged 264 decision).
5. **Historical endpoints are adapters over 265's abstractions.**
   `HistoricalTradeSource.get_trades(cursor, min_ts, max_ts, limit)` calls
   `/historical/trades` with the same parameters; `TradeSync`'s window walker is
   parameterised by direction rather than duplicated. Candles reuse
   `CandleRepository.write_batch` and the existing state stamping.
6. **Failure semantics as `TradesPhase`, plus one item error.**
   `ProviderTransientError` and `OperationalError` fail the phase (pass exit
   code unchanged, earlier phases' results intact); the next firing resumes
   from the watermark and the cursor. A page that fails mid-window re-walks
   that window (idempotent). A **`ProviderPermanentError` on one market's
   candles** (a 404 for an archived ticker, say) is an item error, not an
   abort — the market is skipped and counted, the phase reports `PARTIAL`
   (exit 3, the unit shows failed, as 264's candle phase does for an
   unserved market), and the next firing retries it. *Why:* under a plain
   abort one unserved market would re-abort every firing forever and the
   drain would never reach the floor (PM, 20260831). Trades windows and
   archive pages have no per-item failure: a page parses or its request
   fails.
7. **Migration `kalshi_007`** widens `sync_state_surface_check` to include
   `historical` (rendered from `Surface`, as kalshi_001 does) — no other schema
   change.
8. **`status` measures trade coverage from the effective floor.**
   `read_trade_status` reads the `historical` row alongside `trades` and passes
   `min(trades.coverage_from_ts, historical.watermark_ts)` as `coverage_from`
   to `TRADE_COUNTS`; the four-bucket partition and its sum check are
   unchanged (one parameter moves), and `TradeStatus.coverage_from` reports
   the effective floor. *Rejected:* pinning `before coverage` to the live
   floor and showing historical progress only on its own line — the existing
   trades block would then show no movement while the range fills, and a
   market whose trades are fully present would stay bucketed as lost.
9. **Catalog before tape: walk the market archive first, once. PM-ratified
   20260831.** `GET /historical/markets` takes only `tickers`,
   `event_ticker`, `series_ticker`, `mve_filter`, `limit`, `cursor` — no
   settlement window (docs.kalshi.com and a live probe 20260831: a March
   `min/max_settled_ts` window returned July markets). So the walk is the
   archive's own order: pages of `MARKETS_PAGE_LIMIT` with
   `mve_filter=exclude`, coarsely newest-first (three pages probed
   20260831: 2026-07-01 23:06 → 20:30 → 17:30, with minute-level overlap
   inside pages), each page through `CatalogSync.ingest_markets` behind a
   `HistoricalCatalogSource` adapter (`get_markets` → the archive; events and
   series pass through unchanged), the cursor saved after every page so an
   abort or the cap resumes it. **Stop rule:** the walk is done when every
   market on a page settled before `HISTORICAL_TRADES_FLOOR −
   HISTORICAL_ARCHIVE_STOP_MARGIN` (one day, covering the observed overlap)
   — a market settled before the floor cannot have traded after it, so no
   trade in range belongs to a market beyond that point. The cursor is
   cleared and `archive_walked` recorded. Cost, estimated from the live
   catalog's monthly counts (1.59 M finalized in July, 2.17 M in August):
   ~8–10 k requests including the already-known July–August rows (the
   upsert leaves them `unchanged`), i.e. one authenticated firing or two
   public ones, and several million upserts — the first firing after
   install runs hours, not 40 minutes, and a timer firing that overlaps it
   exits 1 on the run lock (expected, runbook 100).
   **Measured 20260901 (rehearsal, test cluster, public cap 9,000):** one
   firing's historical phase walked **3,895 pages = 3,895,000 markets in
   41 minutes** and reached settlement `2026-03-31T09:02Z` from
   `2026-07-02T16:47Z` before the cap stopped it — the archive runs at
   roughly **1,000 pages per month of settlements**, and the parent
   lookups (`GET /events?tickers=` for events the catalog does not know)
   took the other ~5,100 requests of the 9,000, so a page costs ~2.3
   requests, not 1. Extrapolated to the stop point (settled before
   2025-12-31): **~7,000 pages ≈ 7 M markets ≈ 16,000 requests** — one
   authenticated firing at the 30,000 cap, two to three public ones. The
   catalog grew from 217,595 to 4,112,620 markets and the database by
   10.1 GB (≈ 2.6 KB per market, raw JSONB included), so the whole walk
   adds **~19 GB to `kalshi.markets` on production** — plan the disk.
   The consequence for candles is larger than the paragraph above says:
   `status` showed `behind cutoff, uncollected` go from 1 to **388,788**
   after that one partial walk (selected, finalized, no state row), so the
   per-pass ceiling of 1,000 holds the candle sub-drain for ~15 firings
   and, once the floor is reached, the uncapped sub-drain needs on the
   order of 800 k–1.5 M requests (one to two per market) — several dozen
   authenticated firings, not nine. *Consequence for
   candles:* the archived markets join the behind-cutoff set (finalized,
   before the cutoff, no state row, selected), taking it from 8,394 to
   millions. The per-pass ceiling `HISTORICAL_CANDLE_MARKETS_PER_PASS`
   applies while the trades drain is descending; once `floor_reached`, the
   candle sub-drain is bounded by the request cap alone, so the whole
   budget goes to candles until `status` shows 0 remaining. *Rejected:*
   resolving unknown tickers page by page from the tape (`tickers=` lookups
   in batches of 100 — precise but ~60 k requests and a lookup inside the
   write path); walking by settlement window (not offered by the endpoint).


## Implementation Details

- `constants.py`: `HISTORICAL_PHASE_MINUTES = 30` (the request cap is
  `rate_limit.requests_per_minute × HISTORICAL_PHASE_MINUTES`, computed once
  at phase start from the client's selected budget and logged),
  `HISTORICAL_CANDLE_MARKETS_PER_PASS = 1_000`, `HISTORICAL_TRADES_FLOOR`,
  `HISTORICAL_ARCHIVE_STOP_MARGIN = timedelta(days=1)` (Decision 9),
  `HISTORICAL_SLOW_MARKET_SECONDS = 30`; `Surface.HISTORICAL = "historical"`;
  the three `/historical/*` paths.
- `client.py`: `get_historical_markets` (mirror of `get_markets`, same
  `MarketsQuery`), `get_historical_trades` (mirror of `get_trades`),
  `get_historical_market_candlesticks` (mirror of `get_market_candlesticks`);
  recorder gains all three; fixtures `historical_markets_page`,
  `historical_trades_window`, `historical_candles_market`.
  **Found during implementation (20260901, Task 3.2):** the historical
  candles endpoint does *not* serve the live candle shape. It serves the
  legacy key names — `volume`, `open_interest`, and `open/high/low/close/
  mean/previous` inside `price`/`yes_bid`/`yes_ask` — with the same dollar
  and fp string values (fixture `historical_candles_market`, 1,423 candles).
  261's "same shape" finding was made for trades (proven by the fixture
  parity test) and never checked for candles. Resolution: `models.py` gains
  `LegacyPriceOhlc`, `HistoricalCandlestick` and
  `HistoricalCandlesticksResponse`; the client parses through them and maps
  each candle to a `Candlestick` (`to_candlestick()`), so the repository,
  the stamping, and the tests downstream see one shape. Kept as separate
  models rather than aliases on `PriceOhlc`/`Candlestick`, so a drift on
  either endpoint fails loudly instead of parsing through the other's
  names. *Pending PM ratification* — the alternative (aliases) is a
  smaller diff with a silent-parse risk.
- `historical_types.py`: `HistoricalCatalogSource` (Decision 9) and
  `HistoricalTradeSource` (Decision 5) — the two adapters that let
  `CatalogSync.ingest_markets` and `TradeSync.drain` run unchanged.
  **Found during implementation (20260901, Task 6.2):** because the
  archive walk saves its cursor before the tape is seeded, the historical
  `sync_state` row exists with NULL instants when the trades step reaches
  `init_state`; a plain `ON CONFLICT DO NOTHING` would leave the watermark
  NULL forever (and the walk would rerun every firing). `init_state` is
  therefore set-once-by-column — `DO UPDATE SET watermark_ts =
  COALESCE(existing, new)`, likewise `coverage_from_ts` — which fills NULLs
  and never overwrites a set instant; the live path's behaviour is
  unchanged (integration-tested on both surfaces).
- `trade_sync.py`: `_windows` takes a `direction` (forward: `start < end`,
  windows `[w, w+1h)`; backward: `end > floor`, windows `[w−1h, w)`), watermark
  update passes the window's far edge. Everything else shared.
- `historical_sync.py` (new): `HistoricalSync.run()` — archive walk until
  done (cursor per page, stop rule, Decision 9), then the candles sub-drain
  (the behind-cutoff set with `LIMIT` per pass until `floor_reached`; fetch,
  write, stamp; permanent error → item error, Decision 6), then the trades
  sub-drain via `TradeSync` in backward mode with a shared request counter
  and the phase cap; `HistoricalResult.to_dict()` with the three sub-drains'
  counts, `archive_walked: bool`, `floor_reached: bool`, and the item errors.
- `collection_pass.py`: `PassPhaseName.HISTORICAL`, `HistoricalPhase`,
  `PASS_PHASES` four entries. `kalshi_render.py`: summary block + status line
  (`historical tape 2026-07-01 → 2026-05-14 (floor 2026-01-01) · behind-cutoff
  candles remaining 7,994 · last phase 12 min ago`).
- `trade_status.py`/`status.py`: read the `historical` row, compute the
  effective floor (Decision 8) and pass it as `coverage_from` to
  `TRADE_COUNTS`; reuse the behind-cutoff count already computed for candles.
- Runbook 100: one paragraph — four phases; the historical phase self-limits;
  how to read the status line; the floor constant.

## Success Criteria

1. `mt data kalshi pass --json` reports `phases[].name == ["catalog",
   "candles", "trades", "historical"]`; a trades abort marks historical
   `skipped`; a historical abort leaves the three earlier phases' results and
   state intact.
2. On the first run the `historical` row is created with `watermark_ts ==`
   the live `coverage_from_ts` and `coverage_from_ts == HISTORICAL_TRADES_FLOOR`;
   the phase logs both.
3. Each firing the historical watermark moves **down** by whole hours only,
   never past the floor; when it reaches the floor the phase reports
   `floor_reached: true` and issues no further trade requests.
4. `fetched = written + unknown + excluded + duplicates` holds per firing;
   a re-run over an already-walked hour writes 0 rows.
5. Behind-cutoff candles: after one firing the `behind cutoff, uncollected`
   count has fallen by exactly the number of markets the phase reports
   completed (≤ the per-pass constant), and those markets have candle rows and
   a state row.
6. The phase logs its computed cap at start (30,000 authenticated / 9,000
   public) and never exceeds it by more than one window/one market; the total
   pass duration during the live drain stays under 45 minutes on production.
7. The live `trades` row's `coverage_from_ts` is unchanged by any number of
   historical firings; `status --json` reports `coverage_from` equal to the
   effective floor, the `before coverage` count falls by exactly the number
   of selected closed markets whose `close_time` lies in the hours walked
   that firing, and the four buckets still sum to the selected closed total.
8. Production: the first firing after install shows `catalog=ok candles=ok
   trades=ok historical=ok` (or `historical=partial` with named item
   errors), and the status line shows the tape range growing downward on the
   following firings — observed, not waited for.
9. The archive walk runs before any trades request, saves its cursor after
   every page, resumes from it after an abort or the cap, and once done
   clears the cursor and reports `archive_walked: true` on every later
   firing without a request; a trade for a market that only the archive
   knows is **written**, not counted `unknown`.
10. A permanent provider error on one market's candles leaves the phase
    `partial` with that ticker in `item_errors`, every other market of the
    firing stamped, and the trades sub-drain run; a transient error still
    aborts.


## Verification

**Rehearsal (done 2026-09-01, `user/notes/2026-09-01-267-rehearsal.md`).**
A throwaway database on the test cluster, driven from a scratch directory
with no `.env`, the throwaway URL passed per command as
`MT_TIMESCALE_DB_URL` / `MT_TIMESCALE_MAINTENANCE_URL`:

```
mt data migrate apply --track kalshi --json        → kalshi_007_historical_surface applied, 0 pending
mt data kalshi sync --settled-since <6 h ago>      → a 217 k-market catalog (213 s)
<seed: one archived market with parents; live row init_state(cutoff, cutoff)>   # note, Step 2
mt data kalshi pass --json --events-file passN.jsonl     × 5
mt data kalshi status --json ; mt data kalshi status     after each pass
```

Expected, and observed: pass 1's historical phase is the archive walk only
(`archive page N: … oldest …` lines, then `cap reached during the archive
walk pages=3895; cursor saved`); the next pass logs `archive walk resuming
cursor=…` and its first page is just below the previous last; once the row
has a watermark, each pass logs `historical candles: … pending=1000 (limit
1000)`, then `historical window a→b …` lines walking **down** by whole hours
until `historical cap reached: requests≥cap`, and `status` shows
`tape <live floor> → <watermark> (floor 2026-01-01) · behind-cutoff candles
remaining N` with `trades.coverage_from` equal to the watermark and
`before coverage` falling by the selected markets closed in the walked
hours. The identity `fetched = written + unknown + excluded + duplicates`
holds on every pass; a re-walked hour writes 0.

Caveats learned there: (1) the walk was **not** run to its stop point on the
test cluster (disk) — resume was proven on 200 pages and the walk
hand-finished; the full walk is first observed on production (cutover
report); (2) the candles-fixture ticker is a **Sports** market and rule C
excludes it, so it never enters the behind-cutoff set — Criterion 5 is
shown on the archive's own markets; (3) on a catalog that has not walked the
settled stream since the cutoff, the unknown share is ~30 % (post-cutoff
settlers), not the MVE-only ~10 % production sees; (4) 0.20 s per tape page,
insert path, uncompressed — the cutover baseline.

**Host:** criterion 8 from the first firing after install (the cutover
script's report), then the status line over any later hour; the firing's
client line reads `mode=authenticated budget=1000/min` and the cap line
`cap=30000` (Decision 2). Known before the cutover: production's catalog
phase is `partial` since 2026-09-01 15:22 UTC on two markets served with
status `amended` (outside `MarketStatus`) — a separate fix; the cutover
report accepts `catalog=partial` only if the PM says so.

## Risks

- **`/historical/*` may cost more than the default 10 tokens per request.**
  `GET /account/endpoint_costs` (authenticated) is the authoritative list; the
  first implementation task reads it once and records the answer in the
  design. A higher cost only lengthens the drain — the 429 backoff carries it.
  **Read 2026-09-01 on manta9000 with the production key**
  (`scripts/kalshi_endpoint_costs.py`): `default_cost: 10`, and the list of
  exceptions names none of the three endpoints this slice uses — so
  `GET /historical/trades` = **10**,
  `GET /historical/markets/{ticker}/candlesticks` = **10**, and, for the
  comparison, `GET /markets/trades` = **10**. (The exceptions are portfolio,
  RFQ, margin and `cfbenchmarks` endpoints at 2–50.) Decision 3's firing
  count stands as written.
- **Candle writes into compressed chunks** may be slow; per-market timing is
  logged and the pause lever exists (Decision 4).
- **The historical candle shape is the legacy one** (found 20260901, see
  *Implementation Details*, `client.py`). Mapped in the client; if Kalshi
  migrates the endpoint to the `_dollars`/`_fp` names the
  `HistoricalCandlesticksResponse` parse fails loudly (`volume` required)
  and the fixture is re-recorded — a one-model change.
- **Kalshi may rate-limit `/historical/*` separately or lower.** The client's
  existing 429 backoff applies; a sustained escalation shows in the health
  check's Kalshi phase-recency rule and in the journal.
- **The archive's size and order were estimated from three pages**
  (Decision 9). The rehearsal measured one capped firing — 3,895 pages,
  ~1,000 per month, ~2.6 KB per market — and the estimate is now ~7,000
  pages and **~19 GB of catalog growth on production**; the walk is
  resumable and idempotent, so the remaining uncertainty costs firings,
  not correctness, but the disk is a host fact to check before the
  cutover. If a saved cursor is ever rejected between
  firings (a 4xx on the resume request), the walk restarts from the first
  page — upserts make that safe; the restart is logged.
- **The archive walk's first firing runs hours** (millions of catalog
  upserts under the run lock); overlapping timer firings exit 1 on the lock
  until it finishes — the runbook's cold-start paragraph already describes
  this shape.
