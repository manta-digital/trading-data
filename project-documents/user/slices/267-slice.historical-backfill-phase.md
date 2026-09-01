---
docType: slice-design
slice: historical-backfill-phase
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [264, 265]
interfaces: []
effort: 3
dateCreated: 20260831
dateUpdated: 20260831
status: not_started
---

# Slice Design: Historical Backfill Phase (267)

## Overview

Kalshi serves everything older than its live cutoff from `/historical/*`
(verified in 261: same shapes, same cursor pagination, unauthenticated). Today
that data is only *counted* — `mt data kalshi status` reports 20,937 selected
closed markets whose trades predate the collector (`before coverage`) and 8,394
selected finalized markets whose candles fell behind the cutoff
(`behind cutoff, uncollected`) — and nothing fetches it.

The retired slice 266 made the backfill an operator-run drain that had to wait
for the live trades drain to finish. **This slice replaces it with a fourth
phase of the existing hourly pass.** The phase starts on the first firing after
the release is installed, runs every hour under its own request cap, and
trickles the historical range in behind the live phases. No new unit, timer,
command, or operator step; no waiting on anything; progress is visible in
`status` every hour and the phase stops by itself when the floor is reached.

## Value

- Both "known-lost" counts shrink while the phase runs. `behind cutoff,
  uncollected` falls as markets are stamped; `before coverage` falls as the
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

**In:** a `HistoricalPhase` appended to `PASS_PHASES` after `trades`; two
client methods (`get_historical_trades`, `get_historical_market_candlesticks`)
with recorded fixtures; a `HistoricalTradeSource` adapter so 265's window loop
and `write_page` are reused unchanged; a bounded per-market candle fetch for
the behind-cutoff set; one `sync_state` row (`historical`) carrying the
downward trades watermark; migration `kalshi_007` widening the `surface`
CHECK; a `status` line; the renderer, fixtures, tests, runbook paragraph.

**Out:** any change to the live phases' behaviour or caps; pausing compression
policies (Decision 4); a CLI to drive the backfill by hand (the phase is the
driver; `mt data kalshi pass` from a shell already runs it).

## Architecture

### The phase, one firing

```
catalog → candles → trades → historical
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

Requests are counted across both sub-drains against one cap. Candles run first
because the set is small and finite (8,394 markets, gone in a few firings at
1,000/pass); the trades drain takes whatever budget remains each firing.

### State

`kalshi.sync_state['historical']`: `watermark_ts` = the oldest hour fully
walked (moves **down**), `coverage_from_ts` = `HISTORICAL_TRADES_FLOOR` (the
target, recorded so `status` can show distance), `last_full_sync_at` = last
phase completion. The live `trades` row is untouched. `status` derives an
**effective floor** = `min(trades.coverage_from_ts, historical.watermark_ts)`
(the live floor until the historical row exists) and the four closed-market
buckets partition against it, so `before coverage` means what it says —
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
6. **Failure semantics identical to `TradesPhase`:** `ProviderError` and
   `OperationalError` fail the phase (pass exit code unchanged, earlier phases'
   results intact); the next firing resumes from the watermark. A page that
   fails mid-window re-walks that window (idempotent).
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

## Implementation Details

- `constants.py`: `HISTORICAL_PHASE_MINUTES = 30` (the request cap is
  `rate_limit.requests_per_minute × HISTORICAL_PHASE_MINUTES`, computed once
  at phase start from the client's selected budget and logged),
  `HISTORICAL_CANDLE_MARKETS_PER_PASS = 1_000`, `HISTORICAL_TRADES_FLOOR`,
  `HISTORICAL_SLOW_MARKET_SECONDS = 30`; `Surface.HISTORICAL = "historical"`.
- `client.py`: `get_historical_trades` (mirror of `get_trades`),
  `get_historical_market_candlesticks` (mirror of `get_market_candlesticks`);
  recorder gains both; fixtures `historical_trades_window`,
  `historical_candles_market`.
- `trade_sync.py`: `_windows` takes a `direction` (forward: `start < end`,
  windows `[w, w+1h)`; backward: `end > floor`, windows `[w−1h, w)`), watermark
  update passes the window's far edge. Everything else shared.
- `historical_sync.py` (new, ~250 l): `HistoricalSync.run()` — candles
  sub-drain (query the behind-cutoff set with `LIMIT` per pass, fetch, write,
  stamp), then trades sub-drain via `TradeSync` in backward mode with a shared
  request counter and the phase cap; `HistoricalResult.to_dict()` with both
  sub-drains' counts and `floor_reached: bool`.
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
   trades=ok historical=ok`, and the status line shows the tape range growing
   downward on the following firings — observed, not waited for.

## Verification

Rehearsal on a throwaway database (265's procedure): seed the live row at
`now − 3 h`, run two passes, assert criteria 1–7 from journal + `status
--json`. Host: criterion 8 from the first firing after install, then the
status line over any later hour; the firing's client-construction line reads
`authenticated` (the key is installed before this slice ships — Decision 2).

## Risks

- **`/historical/*` may cost more than the default 10 tokens per request.**
  `GET /account/endpoint_costs` (authenticated) is the authoritative list; the
  first implementation task reads it once and records the answer in the
  design. A higher cost only lengthens the drain — the 429 backoff carries it.
- **Candle writes into compressed chunks** may be slow; per-market timing is
  logged and the pause lever exists (Decision 4).
- **Kalshi may rate-limit `/historical/*` separately or lower.** The client's
  existing 429 backoff applies; a sustained escalation shows in the health
  check's Kalshi phase-recency rule and in the journal.
