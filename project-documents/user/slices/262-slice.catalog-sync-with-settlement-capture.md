---
docType: slice-design
slice: catalog-sync-with-settlement-capture
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261]
interfaces: [263, 264, 265, 266]
effort: 4
dateCreated: 20260824
dateUpdated: 20260824
status: not_started
---

# Slice Design: Catalog Sync with Settlement Capture (262)

## Overview

The first collection logic of Initiative 260. This slice delivers a one-shot CLI command, `mt data kalshi sync`, that brings the Kalshi catalog (series → events → markets) up to date in the `kalshi` schema created by 261, tracks market lifecycle transitions, captures settlement outcomes, and maintains the awaiting-settlement set so that **no market reaches settlement unobserved** — the architecture's binding constraint. It also delivers the first cut of `mt data kalshi status`, reading only persisted state.

The main algorithmic decision the architecture deferred here — incremental versus full sync — is settled by measurement, not assumption (see **Sync-Sizing Survey**): the entire non-settled, non-parlay catalog is ~180k markets and walks in ~180 requests (~45 s at the public budget), so **every pass does a full walk of the live catalog**; the only incremental surface is the *settled stream*, driven by a `settlement_ts` watermark. Settlement capture is belt-and-braces: the settled stream is the primary capture, and an awaiting-settlement set derived from *our own* observation of `close_time` passing — never from what a filter happened to return — is the guarantee, reconciled by direct ticker lookups when a market vanishes from the walk.

Slice 263 wraps this command in the supervised pass unit; this slice ends with a command an operator can run by hand from the dev checkout (and, via `mt-run`'s pass-through, from the production install), against a production database whose kalshi track the PM has applied (see **Deployment prerequisite**).

## Sync-Sizing Survey (live API, 2026-08-24 ~19:30 UTC, public mode)

The 261 design recorded endpoint *behavior*; this slice needed *scale*. Every number below was measured through `KalshiClient` at the 300 req/min public budget with `limit=1000`.

| Population (`GET /markets`, `mve_filter=exclude` unless noted) | Markets | Pages | Walk time |
|---|---|---|---|
| `status=open` | 94,817 (11,689 distinct events) | 95 | 23 s |
| `status=unopened` | 69,062 (944 distinct events) | 70 | 18 s |
| `status=paused` (served `inactive` 1,122 + `closed` 105) | 1,227 | 2 | <1 s |
| `status=closed` (served `closed` 13,177 + `determined` 622) | 13,799 | 14 | 3 s |
| **Full live catalog, non-MVE** | **~179k** | **~181** | **~45 s** |
| `status=open`, MVE *included* | >60,000 in the first 60 pages, **all created within the preceding 30 minutes** | — | — |
| `min_settled_ts = now−24h` (the settled stream) | 74,309 (3,901 events; 75% zero-volume; avg 2.2 KB JSON) | 75 | 18 s |
| `GET /series` (unpaginated) | 13,435 series; 1,768 updated in the last 24 h | 1 | — |
| `GET /events?status=open` | 11,682 events; **zero** `KXMVE*` events | 59 | — |

Behavioral facts the design relies on (all verified in the same session):

- **`status` filter + `mve_filter=exclude` is accepted** for every status (the 261 note that `min_updated_ts` tolerates only `mve_filter=exclude` says nothing about `status`; both combine fine).
- **Settlements are reachable incrementally:** `min_settled_ts` works alone, with `status=settled`, and with `mve_filter=exclude`; results come **newest-first** by `settlement_ts` and every returned market is `finalized`. `max_settled_ts` bounds a window from above.
- **Batch lookups by `tickers` work with no documented maximum:** 300 market tickers in one `GET /markets` request returned 300; 117 event tickers in one `GET /events` request returned 117. An unknown ticker in a batch is silently omitted (5 real + 1 bogus → 5 rows), not an error.
- **Multivariate-event (MVE) parlays dominate raw volume and are excluded by design** (Technical Decision 2): user-composed multi-leg combinations, ~2,000 created per hour, zero volume, and `/events` does not list their events at all.
- **The settled stream is dominated by 15-minute crypto markets** (`KXSOLE/KXSOLD`, `KXETH/KXETHD`, `KXBTC/KXBTCD`, `KXXRP…`, `KXNASDAQ100U`): ~74k non-MVE settlements/day, three quarters with zero volume.
- **Settlement latency has a long tail.** Age (`now − close_time`) of the 13,799 closed-but-unsettled markets: <1 d 1,370 · 1–7 d 594 · 7–30 d 1,559 · 30–365 d 3,874 · **>365 d 6,402**; 11,571 are past their own `latest_expiration_time`. Every `determined` market carried a `result`.
- `GET /historical/cutoff` → `market_settled_ts = 2026-06-25T00:00:00Z` (unchanged since 261): everything settled since then is still on the live endpoints.

## Value

- **Time-sensitive capture starts.** After this slice, the catalog and settlement record can be accumulated on demand (and, from 263, unattended). Markets settled since the historical cutoff (~4.5M, two months' worth) are drained from the live endpoints before the cutoff advances past them.
- **Architectural enablement.** 263 composes this command's core as the first pass phase; 264/265 operate on the post-sync market set (and get the event→series mapping the candlestick path needs); 266 gets a populated table to be idempotent against.
- **Operator visibility.** `mt data kalshi status` answers "when did the catalog last sync, how many markets are in each lifecycle state, and which closed markets are still waiting on settlement — and for how long."

## Technical Scope

**In scope**
- `src/manta_trading/data/kalshi/sync.py` (orchestration), `repository.py` (all SQL), `events.py` (structured events), plus a `CatalogSource` protocol the sync core consumes so it is testable as *(state, provider results) → (writes, new state)*.
- Full-walk catalog sync of series, events, markets (non-MVE); lifecycle transition tracking; write-on-change upserts.
- Settled stream capture with a persisted `settlement_ts` watermark and a first-run drain from the historical cutoff, windowed so interruption never loses more than one window.
- Awaiting-settlement set maintenance (entry from observed close, retirement on finalization, reconciliation of vanished markets by batch lookup).
- `mt data kalshi sync` and `mt data kalshi status` (`--json` on both) in a new CLI module.
- One additive migration (`kalshi_004`) correcting the `sync_state` column comments to this slice's semantics.
- `Settings.kalshi_requests_per_minute` (rate-budget override the 261 design promised for "CLI wiring time").
- Fixtures for the two request shapes 261 did not record (`tickers` batch responses; a `min_settled_ts` window page); unit + integration tests.

**Out of scope**
- The pass command, timer, `mt-run kalshi` verb, install-script wiring (263).
- Candles/trades and their status columns (264/265); `/historical/*` fetches (266).
- MVE/parlay markets (excluded; see Technical Decision 2). Orderbook anything.
- Applying the kalshi migration track to production — a **PM action** this design depends on (below), not a task of this slice.

## Deployment prerequisite (PM action)

The production ledger shows `kalshi_001_schema`, `kalshi_002_catalog`, `kalshi_003_collection_state` **pending**. Before the first `sync` against production, and again for this slice's `kalshi_004`, the PM applies the track per runbook 100 *Update procedure* (tag → update → pre-flight `status` → `apply` with the maintenance credential → `status` shows 0 pending). The walkthrough below starts with exactly this. The track is additive and idempotent; there is no down-migration.

## Dependencies

### Prerequisites
- **261 (complete):** `KalshiClient` (series list, `iter_markets`, `get_markets(tickers=…)`, `get_events(tickers=…, min_updated_ts=…)`, `get_series`, `get_historical_cutoff`), the `kalshi` schema, `MarketStatus`/`MarketStatusFilter`/`Surface` enums, fixtures and recorder.
- 900/100/913/916 (complete): CLI, config, logging, migration runner, role split, `mt-run` pass-through.
- Test cluster fixtures (`ephemeral_db`) for integration tests.

### Interfaces Required
- `KalshiClient.from_settings(settings)` — mode selection; this slice adds the budget override on top.
- `market/schema/runner` — unchanged; `kalshi_004` is appended to `KALSHI_MIGRATIONS`.
- `market/db_session.make_configure_connection` — the session-settings contract (timezone, `work_mem`, `statement_timeout`), mirrored for the async pool (Technical Decision 8).

## Architecture

### Component Structure

```
src/manta_trading/data/kalshi/
  constants.py     # + sync constants (walk filters, page/batch sizes, settled window,
                   #   stuck threshold, MVE filter value) — still the single source
  sync.py          # CatalogSync: the pass-phase core; CatalogSource protocol;
                   #   SyncResult (counts, transitions, errors) — no SQL, no typer
  repository.py    # CatalogRepository: every SQL statement for kalshi.* (async psycopg)
  events.py        # SyncEventType / SyncEvent / SyncEventSink (+ Null, Jsonl sinks)
  status.py        # read-only queries + dataclasses behind `mt data kalshi status`
src/manta_trading/cli/commands/kalshi.py   # kalshi_app: `sync`, `status`
src/manta_trading/market/schema/migrations/kalshi.py   # + kalshi_004
src/manta_trading/config/__init__.py       # + kalshi_requests_per_minute
test/fixtures/kalshi/                      # + markets_by_tickers.json, events_by_tickers.json,
                                           #   markets_settled_window.json
```

`sync.py` depends on a `CatalogSource` Protocol (the five client calls it uses) and a `CatalogRepository`; it never imports httpx or typer. The CLI module constructs the real client and repository and calls `CatalogSync.run()`; 263 will call the same function from its pass. One code path, per the architecture's CLI-is-the-baseline principle.

### Data Flow — one sync run

```
run_started  ─►  1. series      GET /series (1 request) ──► upsert series (write-on-change)
             ─►  2. markets     for status in (unopened, open, paused, closed):
                                  GET /markets?status=…&mve_filter=exclude&limit=1000 … follow cursor
                                  per page: resolve unknown event tickers
                                              └─ GET /events?tickers=… (≤100/request)
                                                   └─ unknown series → GET /series/{t}
                                            upsert events, then markets (page batch);
                                            record status transitions; add tickers to `seen`
             ─►  3. events      GET /events?min_updated_ts=(last_full_sync_at − 1 s) … cursor
                                  → upsert (metadata changes on already-known events)
             ─►  4. settled     windows of SETTLED_WINDOW from watermark_ts (first run: from the
                                historical cutoff) up to now:
                                  GET /markets?min_settled_ts=a−1s&max_settled_ts=b&mve_filter=exclude
                                  → upsert; after each complete window: watermark_ts := b
             ─►  5. awaiting    SQL: enter (close_time ≤ now, status ≠ finalized);
                                     retire (status = finalized); refresh close_time
                                vanished = awaiting − seen − captured-this-run
                                  → GET /markets?tickers=… (≤100/request) → upsert; last_checked_at := now
             ─►  6. state       sync_state[catalog].last_full_sync_at := run start time
run_finished (SyncResult → exit code)
```

Phases 1–3 are the *full walk*; 4 is the *settled stream*; 5 is the *guarantee*. Order matters for foreign keys (series before events before markets) and for reconciliation (5 needs the `seen` set from 2 and the captures from 4). If any phase aborts on a provider error, later phases do not run and the run's state writes are limited to what completed (watermarks advance only per completed window/walk).

### State Management

Persisted in `kalshi.sync_state` row `surface = 'catalog'` (semantics fixed by this slice, comments corrected by `kalshi_004`):

| Column | Meaning after 262 |
|---|---|
| `last_full_sync_at` | Start time of the last run whose full walk (phases 1–3) completed. Also the `min_updated_ts` floor for the events refresh. NULL until the first successful walk. |
| `watermark_ts` | Upper bound of the last **completed settled window**: every non-MVE market with `settlement_ts` < this has been captured. NULL until the first window completes. |
| `cursor` | Unused by 262 (NULL). Windows make cursor persistence unnecessary; the column stays for surfaces whose walks are not windowable (trades, 265). |

Per-market state lives in `kalshi.markets` (`status`, settlement columns, `first_seen_at`, `last_synced_at`) and `kalshi.awaiting_settlement` (`close_time`, `entered_at`, `last_checked_at`). Nothing is inferred from data tables at run start except the awaiting set itself, which *is* state.

## Technical Decisions

1. **Full walk of the live catalog every pass; incremental only for the settled stream.** The survey shows the whole non-MVE live catalog is ~181 requests (~45 s public, ~11 s authenticated). At that cost a full walk is cheaper to reason about than any incremental scheme and cannot miss a transition among live markets. *Rejected:* `min_updated_ts` incremental sync — it "tracks non-trading changes only" (documented), is incompatible with `status`, its cursor walks a set that mutates underneath it, and it would make the awaiting set depend on the filter — exactly what the architecture forbids. *Rejected:* walking `/events?with_nested_markets=true` as the primary source (1,524 markets/request — denser) — event-level `status` semantics differ from market-level ("at least one market …"), there is no `paused` event status, and completeness becomes an argument about two vocabularies instead of one. Markets are the unit that settles, so markets are the unit we walk.

2. **MVE (parlay) markets are excluded — `mve_filter=exclude` on every markets request.** ~2,000 user-composed multi-leg markets per hour, zero volume, closing minutes after creation; `/events` does not even list their events. Including them would multiply the catalog by ~20× per year for no analytical signal (the initiative's motivation is event-contract *history*, not parlay tickets). Defined once as `KALSHI_MVE_FILTER = "exclude"`; MVE capture, if ever wanted, is a future decision with its own storage plan, not a flag here. **PM-visible decision.**

3. **Settlement capture is two mechanisms with distinct roles.** (a) The *settled stream* (`min_settled_ts`/`max_settled_ts` windows, watermarked) is the primary capture: it is the only way to observe the ~74k daily finalizations cheaply. (b) The *awaiting set* is the guarantee: a market enters it when **our own** stored `close_time` passes (`close_time ≤ now()` and `status ≠ finalized` — whatever the served status, including a market the API still calls `active`), and leaves only when a `finalized` row with its `result` is stored. Markets in the set that appeared in neither the full walk nor the settled stream this run ("vanished") are looked up directly by `tickers` batch. The set therefore never depends on which filter returned what.

4. **The settled stream is walked in bounded windows, oldest-first, and the watermark advances per completed window.** `SETTLED_WINDOW = 6 h` (≈19 pages at today's rate). Interruption loses at most one window (re-walked on resume — idempotent), no cursor persistence or "pending watermark" column is needed, and the first-run drain shows progress as a climbing watermark. Window boundaries overlap by one second (`min_settled_ts = start − 1`) because the parameters are strict "after/before" and timestamps are second-granular; the upsert makes overlap free.

5. **First-run floor is the historical cutoff, discovered not hardcoded.** When `watermark_ts` is NULL the drain starts at `get_historical_cutoff().market_settled_ts` — everything still on the live endpoints, per the capture mandate. `--settled-since <ISO-8601>` overrides the floor for that run only (an operator lever; recorded in the run's events). *Volume:* ~60 days × 74k ≈ 4.5M markets ≈ 4,500 requests ≈ 15 min at the public budget, plus the write time — the "first run runs long once" the architecture anticipated. See Risk Assessment for the storage implication.

6. **Write-on-change upserts; `first_seen_at` is immutable.** `INSERT … ON CONFLICT (ticker) DO UPDATE SET … WHERE kalshi.markets.raw IS DISTINCT FROM EXCLUDED.raw` (same shape for series/events). Unchanged rows cost nothing, so the hourly full walk does not rewrite 180k rows. Consequence: `last_synced_at` means "last time this row's content changed as served" — the catalog-level "last observed" time is `sync_state.last_full_sync_at`. Column mapping is one-to-one from the Pydantic models (261's parity test guarantees it); `raw` is `model_dump(mode="json")` of the served object so equality is by content.

7. **Lifecycle transitions are recorded from the upsert, not re-derived.** Per page the repository selects the prior `(ticker, status)` for the page's tickers, upserts, and returns the transitions `(from_status → to_status)` as counts in `SyncResult` and in the phase's structured event. `initialized→active`, `active→closed`, `closed→determined`, `determined→finalized` are the expected edges; anything else is still recorded (never rejected — the CHECK on `status` is the only vocabulary gate).

8. **Async psycopg (`AsyncConnectionPool`) for the sync path.** The client is async; running sync psycopg calls on the event loop violates the project's <1 ms rule for synchronous work in `async def` (code review 261 F001 precedent), and wrapping every statement in `to_thread` is noise. psycopg 3 ships the async pool — no new dependency. An async twin of `make_configure_connection` applies the same session settings (a few lines; the sync hook cannot be reused because its `execute` calls are synchronous). `status.py` stays synchronous (one short read, same pattern as `mt data status`).

9. **Parents are resolved per page, and a missing parent is an item error, not a fallback.** Unknown `event_ticker`s on a page are fetched in `tickers` batches of `TICKERS_BATCH_SIZE = 100` (verified to 117 for events and 300 for markets; one conservative constant for both); an event whose `series_ticker` is not in this run's series list is fetched with `GET /series/{t}`. If a parent cannot be obtained, the dependent rows are **skipped, counted, and logged at ERROR with tickers**; the run continues and exits with `EXIT_SYNC_PARTIAL`. Never a placeholder parent row.

10. **Stuck threshold: `KALSHI_SETTLEMENT_STUCK_AFTER = 7 days` of age (`now − close_time`).** From the survey, markets that will settle normally do so within days; beyond a week the population is the long tail (30–365 d: 3,874; >365 d: 6,402). Status reports the age histogram and the count past the threshold rather than a single boolean, because on day one ~10k *inherited* closed markets are already past it — that is a fact about Kalshi, reported honestly, not an alarm about the collector. The threshold is one constant, used only for reporting; no automatic retirement (a market leaves the set only by finalizing).

11. **Exit codes are explicit and shared with 263.** `0` success · `1` preflight (config/DB unreachable; existing convention) · `2` provider abort (a phase raised `ProviderTransientError`/`ProviderPermanentError` after the client's retries; state advanced only as far as completed) · `3` completed with item-level errors (Decision 9). Constants in `cli/commands/kalshi.py`; 263 maps them to the unit's failure semantics.

12. **CLI: `mt data kalshi sync` / `mt data kalshi status`, new module.** `data.py` is already 136 KB; the kalshi group lives in `cli/commands/kalshi.py` and is attached with `data_app.add_typer(kalshi_app, name="kalshi")`. Names finalize the architecture's "e.g. `mt data kalshi status`"; 263 adds `pass` alongside. Production reach is `mt-run data kalshi status` via 916's pass-through — nothing to add.

13. **Rate budget override at wiring time.** `Settings.kalshi_requests_per_minute: int | None = None`; when set it replaces the mode's constant (`RateLimit(requests_per_minute=…)`) at `KalshiClient` construction in the CLI. Unset → 261's mode defaults, unchanged.

14. **Structured events mirror the 120 shape but are Kalshi-typed.** `AcquisitionEvent` carries `symbol`/`granularity` fields that have no meaning here; forcing them would be the "labels as structure" anti-pattern. `events.py` defines `SyncEventType` (`run_started`, `phase_finished`, `item_error`, `run_finished`), a `SyncEvent` dataclass (run_id, timestamp, phase, counts dict, transitions dict, error, duration_ms), the `SyncEventSink` Protocol, `NullSyncEventSink`, and `JsonlSyncEventSink` (`--events-file PATH`). Per-item events exist only for errors; everything else is aggregated per phase so the sink is not flooded by 74k settlements a day.

## Implementation Details

### Constants added (`data/kalshi/constants.py`)

`CATALOG_WALK_FILTERS = (MarketStatusFilter.UNOPENED, OPEN, PAUSED, CLOSED)` · `KALSHI_MVE_FILTER = "exclude"` · `MARKETS_PAGE_LIMIT = 1000` · `EVENTS_PAGE_LIMIT = 200` · `TICKERS_BATCH_SIZE = 100` · `SETTLED_WINDOW = timedelta(hours=6)` · `WINDOW_OVERLAP = timedelta(seconds=1)` · `KALSHI_SETTLEMENT_STUCK_AFTER = timedelta(days=7)` · `AWAITING_AGE_BUCKETS = (1 d, 7 d, 30 d)` (histogram edges; the 7 d edge *is* the stuck threshold, referenced not repeated).

### `CatalogSource` protocol (what `sync.py` needs from the client)

`get_series_list()`, `get_series(ticker)`, `iter_markets(**MarketsQuery)`, `get_markets(tickers=…, limit=…)`, `get_events(tickers=… | min_updated_ts=…, cursor=…, limit=…)`, `get_historical_cutoff()`. `KalshiClient` already satisfies it structurally; tests substitute a fixture-backed fake.

### Repository contract (`repository.py`, all SQL lives here)

- `upsert_series(rows) -> int` / `upsert_events(rows) -> int` / `upsert_markets(rows) -> MarketUpsertOutcome` (written count + transition counts), each a single parameterized multi-row statement per page with the write-on-change guard:

  ```sql
  INSERT INTO kalshi.markets (…columns…, raw) VALUES …
  ON CONFLICT (ticker) DO UPDATE SET …, last_synced_at = now()
  WHERE kalshi.markets.raw IS DISTINCT FROM EXCLUDED.raw
  ```

- `known_event_tickers(tickers) -> set[str]`, `known_series_tickers(tickers) -> set[str]` — parent resolution.
- `enter_awaiting(now) -> int`, `retire_awaiting() -> int`, `refresh_awaiting_close_times() -> int` — three statements:

  ```sql
  INSERT INTO kalshi.awaiting_settlement (market_ticker, close_time)
  SELECT ticker, close_time FROM kalshi.markets
  WHERE close_time <= %(now)s AND status <> %(finalized)s
  ON CONFLICT DO NOTHING;
  DELETE FROM kalshi.awaiting_settlement a USING kalshi.markets m
  WHERE m.ticker = a.market_ticker AND m.status = %(finalized)s;
  ```

- `awaiting_tickers() -> list[str]`, `mark_checked(tickers, now)`.
- `get_sync_state(surface) / set_last_full_sync(surface, ts) / set_watermark(surface, ts)`.
- Enum values are passed as parameters from `MarketStatus` — no status literal appears in SQL.

### Sync core (`sync.py`)

`CatalogSync(source, repository, sink, clock)` with `async run(settled_since: datetime | None = None) -> SyncResult`. `SyncResult` carries per-phase counts (fetched / written / skipped-unchanged), transition counts, settled captured, awaiting entered/retired/checked/unreachable, item errors (ticker + reason), duration, and the exit-code classification. Phase functions are small (`_sync_series`, `_walk_markets`, `_refresh_events`, `_drain_settled`, `_reconcile_awaiting`), each emitting one `phase_finished` event. Provider exceptions propagate out of `run()` after a `run_finished` event with `error` set — the CLI maps them to exit code 2.

Memory: the `seen` set holds ~180k ticker strings (tens of MB); pages are written as they arrive and never accumulated.

### Migration `kalshi_004_catalog_sync_semantics`

`COMMENT ON COLUMN` updates for `kalshi.sync_state.watermark_ts` ("catalog: settlement_ts upper bound of the last completed settled window …") and `.cursor` ("catalog: unused — windows replace cursor resume; trades: …"), plus the `last_full_sync_at` wording above. Comments only; no shape change. Idempotent.

### CLI specification (`cli/commands/kalshi.py`)

```
mt data kalshi sync [--settled-since ISO] [--events-file PATH] [--json]
```
Preflight: `MT_TIMESCALE_DB_URL` present (application credential — DML only, per 913). Builds `KalshiClient.from_settings` (+ budget override), the async pool, the sink; runs `asyncio.run(CatalogSync(...).run(...))`; prints the `SyncResult` summary (Rich table or JSON); exits per Decision 11. Logs the selected client mode and budget at INFO at start, as 261 does.

```
mt data kalshi status [--json]
```
Reads only the database (no API call). Output sections:

```
Kalshi catalog
  last full sync      2026-08-25 03:12:07 UTC  (48 min ago)
  settled watermark   2026-08-25 03:00:00 UTC
  series / events     13,435 / 24,310
Markets by status     initialized 69,062 · active 94,817 · inactive 1,122 · closed 13,177
                      determined 622 · finalized 4,512,884
Awaiting settlement   13,799 markets
  age                 <1d 1,370 · 1–7d 594 · 7–30d 1,559 · >30d 10,276
  past 7d threshold   12,235   oldest KXABC-23JUN22 (1,159 d)
  checked directly    41  (looked up by ticker this or a previous run; still unsettled)
```
Empty-state (no `sync_state` row) prints "catalog has never synced" and exits 0 — reporting, never refusing (the `mt data status` precedent). `--json` emits the same fields as a flat object.

### Fixtures and tests

- Recorder gains three targets (`--only markets_by_tickers`, `events_by_tickers`, `markets_settled_window`) producing real responses for the shapes this slice introduces; the existing `markets_page*.json`, `series_list.json`, `events_page*.json` drive the walk.
- **Unit (`test/unit/data/kalshi/test_sync.py`, `test_events.py`):** the sync core against a fake `CatalogSource` serving fixtures and an in-memory fake repository: phase ordering, parent resolution (known/unknown/unfetchable), transition counting, settled windowing (window boundaries, overlap, floor from cutoff vs `--settled-since`, watermark advances only per complete window, abort mid-window leaves the watermark), awaiting reconciliation (vanished → batch → unreachable), exit-code classification, event emission per phase.
- **Integration (`test/integration/test_kalshi_sync.py`, `ephemeral_db`):** real repository on a throwaway database with the fake source: first run populates all three tables with FKs satisfied; second identical run writes zero rows (write-on-change) and preserves `first_seen_at`; a fixture edit that closes a market → it enters `awaiting_settlement`; a finalized fixture → it is retired with `result`/`settlement_ts` stored; a market removed from the walk fixtures → looked up by ticker; `status.py` queries return the documented shape; `kalshi_004` applies and re-applies cleanly.
- Type gate as in 261: mypy (project config) and strict pyright on the kalshi package and tests.

## Integration Points

### Provides to Other Slices
- **263:** `CatalogSync.run()` as the first pass phase, the exit-code constants, the `SyncEventSink` to route pass events, and the `status` command it fronts with `mt-run`.
- **264/265:** a post-sync market set with `event_ticker` → `series_ticker` (the candlestick path needs the series), `status`/`close_time` for the completeness definition, and the awaiting set as "closed but not done".
- **266:** the populated catalog to be idempotent against; the watermark as the boundary between "drained live" and "behind the cutoff".

### Consumes from Other Slices
- 261 client and schema, unchanged. If the API begins serving a status outside `MarketStatus`, the upsert fails loudly on that page (261 Decision 7); the run records the page's tickers as item errors and continues — the enum change is a one-line follow-up.

## Success Criteria

1. `mt data kalshi sync` against a freshly migrated throwaway database completes with exit 0; `kalshi.series`, `events`, `markets` are populated with FKs satisfied; `sync_state['catalog']` has `last_full_sync_at` and `watermark_ts` set; the run summary reports the per-phase counts.
2. A second run immediately after writes zero catalog rows (write-on-change) and leaves `first_seen_at` untouched — proven in the integration test and demonstrable live.
3. Every closed market observed by the walk is in `awaiting_settlement` with age computable as `now() − close_time`; a market whose finalized row (with `result`) is stored is retired; a market missing from both the walk and the stream is looked up by ticker and `last_checked_at` is set.
4. The settled stream is captured in windows; killing the process mid-drain and re-running resumes from the last completed window with no duplicate rows and no gap (integration test with an aborting fake source; live: SIGINT during the first-run drain, re-run, compare counts by window).
5. Lifecycle transitions are counted per run and emitted in `phase_finished` events; item errors are per-ticker events; `--events-file` yields valid JSONL.
6. `mt data kalshi status` shows the sections specified above, computes the age histogram and the past-threshold count from the constant, and works before any sync has run.
7. Exit codes 0/1/2/3 behave as specified (unit-tested via the classification function; provider abort demonstrated with an unreachable base URL).
8. `kalshi_004` is in `TRACKS["kalshi"]`, applies and re-applies on a throwaway database; the 261 migration tests still pass.
9. MVE markets never appear in `kalshi.markets` (every markets request carries `mve_filter=exclude`; asserted on the fake source's received queries).
10. `ruff`, mypy, strict pyright clean; no new direct dependency.

## Verification Walkthrough

Draft; refined at the end of Phase 6. Steps 0–1 are PM actions on production; every later step runs first against a throwaway database on the test cluster, then against production through the dev checkout / `mt-run`.

```bash
# 0. PM — apply the kalshi track to production (runbook 100, Update procedure).
#    This slice's kalshi_004 rides along with 261's three pending migrations.
cd ~/source/repos/manta/trading-data
uv run mt data migrate status --track kalshi         # pre-flight: kalshi_001..004 pending (app credential)
uv run mt data migrate apply  --track kalshi         # maintenance credential, interactive shell
uv run mt data migrate status --track kalshi         # → "… applied, 0 pending"

# 1. Status before any sync — reports, never refuses
uv run mt data kalshi status
#    → "Kalshi catalog has never synced." (exit 0)

# 2. Rehearsal on a throwaway database (test cluster, runbook 400)
export MT_TIMESCALE_DB_URL=postgresql://trading_test_admin:...@host:5432/mt_walk_xxx
export MT_TIMESCALE_MAINTENANCE_URL=$MT_TIMESCALE_DB_URL
uv run mt data migrate apply --track kalshi
uv run mt data kalshi sync --settled-since 2026-08-24T00:00:00Z --events-file /tmp/kalshi-sync.jsonl
#    → mode=public budget=300/min
#      series      fetched 13,435  written 13,435
#      markets     fetched ~179k   written ~179k   transitions: (first run: none)
#      events      fetched ~12.6k  written ~12.6k
#      settled     windows 4  captured ~74k        watermark → <run start>
#      awaiting    entered ~13.8k  retired 0  checked 0  unreachable 0
#      exit 0, ~2–3 min
uv run mt data kalshi sync                           # idempotence: written 0 / 0 / 0 (except live changes), exit 0
uv run mt data kalshi status                         # sections as specified; awaiting histogram populated
wc -l /tmp/kalshi-sync.jsonl                         # run_started + 5 phase_finished + run_finished per run

# 3. Settlement capture, observed end-to-end on the throwaway DB
#    Pick an awaiting market with close_time in the last hour; wait for Kalshi to settle it
#    (crypto 15-minute markets settle within minutes); re-run sync:
uv run mt data kalshi sync
uv run psql "$MT_TIMESCALE_DB_URL" -c "select ticker,status,result,settlement_ts from kalshi.markets where ticker='<T>'"
#    → finalized | yes|no | <ts>;  and the ticker is gone from kalshi.awaiting_settlement

# 4. Interrupted drain resumes without loss (throwaway DB)
uv run mt data kalshi sync --settled-since 2026-07-01T00:00:00Z   # Ctrl-C after a few windows
uv run mt data kalshi status                          # watermark = last completed window end
uv run mt data kalshi sync --settled-since 2026-07-01T00:00:00Z   # resumes at the watermark; final counts match a clean run

# 5. Tests
uv run pytest test/unit/data/kalshi -q
uv run python scripts/run_tests.py integration -- -k kalshi -q

# 6. First production run (dev checkout, application credential) — the long one
uv run mt data kalshi sync --events-file ~/kalshi-first-sync.jsonl
#    → first-run drain from the historical cutoff (2026-06-25): ~4.5M settled markets,
#      ~15 min of requests at the public budget plus write time; watermark climbs per 6 h window
uv run mt data kalshi status
mt-run data kalshi status                            # same, through the production front door
```

There is no `mt data kalshi pass` and no timer yet — that is 263. What the user can prove after this slice: the catalog is populated and idempotently refreshed, settlements are captured and the awaiting set is maintained with ages visible, and an interrupted first-run drain resumes without loss.

## Risk Assessment

- **Storage growth is real and PM-visible.** ~74k non-MVE settlements/day at ~2.2 KB served JSON (plus columns) ≈ 200 MB/day, ≈ 70 GB/year in `kalshi.markets`, ~11 GB for the first-run drain — driven by 15-minute crypto markets, three quarters of them zero-volume. This design captures them (the mandate is completeness; the PM's "watch growth" posture already anticipates hypertable promotion for the large tables). Levers if the PM wants them, each a separate decision: `--settled-since` for the first run, dropping `raw` for finalized zero-volume markets, or a series allow/deny list. None is built here.
- **Unauthenticated budget is undocumented.** A ~190-request pass at 5 req/s is modest; the first-run drain is ~4,500 requests over ~15 min. No 429 was seen in ~900 survey requests. Mitigation as in 261: 429 is transient with backoff; the budget override and authenticated mode (≈3× faster) are available.
- **Kalshi's `status` vocabulary drift.** A new served status fails the page's upsert on the CHECK — recorded as item errors, exit 3, run otherwise complete. Intended.

## Implementation Notes

### Development Approach

Suggested order: constants + `events.py` → `repository.py` with integration tests on `ephemeral_db` (upserts, guard, awaiting statements) → `CatalogSource` fake + `sync.py` phases with unit tests (series → markets walk → events refresh → settled windows → reconciliation) → `kalshi_004` → `status.py` → CLI module + exit codes → recorder targets and fixtures → rehearsal on a throwaway database (walkthrough steps 2–5) → walkthrough refresh.

Branch: `262-slice.catalog-sync-with-settlement-capture` from `main` (no integration branch configured).

### Special Considerations
- The first production run is long and writes millions of rows; run it from an interactive shell with `--events-file`, not from a timer (263 will inherit a warm catalog).
- All destructive statements in tests target `ephemeral_db` databases only; `status` and `sync` use the application credential (DML) and never the maintenance URL.
- Nothing in this slice references `public` (extraction discipline).
- `SyncResult` should be JSON-serializable from day one — 263's pass summary and any future `mt-run status` row will consume it.
