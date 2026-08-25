---
docType: slice-design
slice: catalog-sync-with-settlement-capture
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: [261]
interfaces: [263, 264, 265, 266]
effort: 4
dateCreated: 20260824
dateUpdated: 20260825
status: complete
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

**Applied 2026-08-24:** 261's `kalshi_001_schema`, `kalshi_002_catalog`, `kalshi_003_collection_state` are on production (`status --track kalshi` → 0 pending), so the empty `kalshi` schema exists there now. What remains is this slice's own `kalshi_004`, applied the same routine way before the first production `sync` — runbook 100 *Update procedure* (pre-flight `status` → `apply` with the maintenance credential → `status` shows 0 pending). The walkthrough below starts with exactly this. The track is additive and idempotent; there is no down-migration.

## Dependencies

### Prerequisites
- **261 (complete):** `KalshiClient` (series list, `iter_markets`, `get_markets(tickers=…)`, `get_events(tickers=…, min_updated_ts=…)`, `get_series`, `get_historical_cutoff`), the `kalshi` schema, `MarketStatus`/`MarketStatusFilter`/`Surface` enums, fixtures and recorder.
- 900/100/913/916 (complete): CLI, config, logging, migration runner, role split, `mt-run` pass-through.
- Test cluster fixtures (`ephemeral_db`) for integration tests.

### Interfaces Required
- `KalshiClient.from_settings(settings)` — mode selection; this slice adds the budget override on top.
- `market/schema/runner` — unchanged; `kalshi_004` is appended to `KALSHI_MIGRATIONS`.
- `market/db_session.make_configure_connection` and `constants.DB_BULK_SESSION` — the session-settings contract (timezone, `work_mem`, `statement_timeout`), applied to the run's async connection (Technical Decision 8).

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

2. **MVE (parlay) markets are excluded — `mve_filter=exclude` on every markets request.** ~2,000 user-composed multi-leg markets per hour, zero volume, closing minutes after creation; `/events` does not even list their events. Including them would multiply the catalog by ~20× per year for no analytical signal (the initiative's motivation is event-contract *history*, not parlay tickets). Defined once as `KALSHI_MVE_FILTER = "exclude"`; MVE capture, if ever wanted, is a future decision with its own storage plan, not a flag here. **PM-sanctioned 2026-08-24** (Design review disposition, F002): the architecture's "no market may reach settlement unobserved" applies to the non-MVE catalog — recorded in 260-arch (*Catalog scale and incremental sync*) and the 260 slice plan Notes.

3. **Settlement capture is two mechanisms with distinct roles.** (a) The *settled stream* (`min_settled_ts`/`max_settled_ts` windows, watermarked) is the primary capture: it is the only way to observe the ~74k daily finalizations cheaply. (b) The *awaiting set* is the guarantee: a market enters it when **our own** stored `close_time` passes (`close_time ≤ now()` and `status ≠ finalized` — whatever the served status, including a market the API still calls `active`), and leaves only when a `finalized` row with its `result` is stored. Markets in the set that appeared in neither the full walk nor the settled stream this run ("vanished") are looked up directly by `tickers` batch. The set therefore never depends on which filter returned what.

4. **The settled stream is walked in bounded windows, oldest-first, and the watermark advances per completed window.** `SETTLED_WINDOW = 6 h` (≈19 pages at today's rate). Interruption loses at most one window (re-walked on resume — idempotent), no cursor persistence or "pending watermark" column is needed, and the first-run drain shows progress as a climbing watermark. Window boundaries overlap by one second (`min_settled_ts = start − 1`) because the parameters are strict "after/before" and timestamps are second-granular; the upsert makes overlap free.

5. **First-run floor is the historical cutoff, discovered not hardcoded.** When `watermark_ts` is NULL the drain starts at `get_historical_cutoff().market_settled_ts` — everything still on the live endpoints, per the capture mandate. `--settled-since <ISO-8601>` overrides the floor for that run only (an operator lever; recorded in the run's events). *Volume:* ~60 days × 74k ≈ 4.5M markets ≈ 4,500 requests ≈ 15 min at the public budget, plus the write time — the "first run runs long once" the architecture anticipated. See Risk Assessment for the storage implication.

6. **Write-on-change upserts; `first_seen_at` is immutable.** `INSERT … ON CONFLICT (ticker) DO UPDATE SET … WHERE kalshi.markets.raw IS DISTINCT FROM EXCLUDED.raw` (same shape for series/events). Unchanged rows cost nothing, so the hourly full walk does not rewrite 180k rows. Consequence: `last_synced_at` means "last time this row's content changed as served" — the catalog-level "last observed" time is `sync_state.last_full_sync_at`. Column mapping is one-to-one from the Pydantic models (261's parity test guarantees it); `raw` is `model_dump(mode="json")` of the served object so equality is by content.

7. **Lifecycle transitions are recorded from the upsert, not re-derived.** Per page the repository selects the prior `(ticker, status)` for the page's tickers, upserts, and returns the transitions `(from_status → to_status)` as counts in `SyncResult` and in the phase's structured event. `initialized→active`, `active→closed`, `closed→determined`, `determined→finalized` are the expected edges; anything else is still recorded (never rejected — the CHECK on `status` is the only vocabulary gate).

8. **Async psycopg — one `AsyncConnection` for the run, no pool, with an enumerated failure taxonomy.** The client is async; running sync psycopg calls on the event loop violates the project's <1 ms rule for synchronous work in `async def` (code review 261 F001 precedent), and wrapping every statement in `to_thread` is noise. psycopg 3 ships async connections — no new dependency. The sync is a single sequential writer, so it opens **one** connection at preflight and holds it for the run: no pool, hence no pool-exhaustion or hung-checkout mode to reason about, and the run's advisory lock (Decision 11) lives on that connection. Session settings are applied at connect from `DB_BULK_SESSION` (the same `SET`s `make_configure_connection` issues; a few async lines, since the sync hook's `execute` calls cannot run on the loop). The storage path is a new I/O path and is held to the bar 261's client was held to (review 261 F004): every failure mode is enumerated in **Storage failure taxonomy** below, classification is complete over `psycopg.Error`, and an operational storage fault has its own exit code. `status.py` stays synchronous (one short read, same pattern as `mt data status`).

9. **Parents are resolved per page, and a missing parent is an item error, not a fallback.** Unknown `event_ticker`s on a page are fetched in `tickers` batches of `TICKERS_BATCH_SIZE = 100` (verified to 117 for events and 300 for markets; one conservative constant for both); an event whose `series_ticker` is not in this run's series list is fetched with `GET /series/{t}`. If a parent cannot be obtained, the dependent rows are **skipped, counted, and logged at ERROR with tickers**; the run continues and exits with `EXIT_SYNC_PARTIAL`. Never a placeholder parent row.

10. **Stuck threshold: `KALSHI_SETTLEMENT_STUCK_AFTER = 7 days` of age (`now − close_time`).** From the survey, markets that will settle normally do so within days; beyond a week the population is the long tail (30–365 d: 3,874; >365 d: 6,402). Status reports the age histogram and the count past the threshold rather than a single boolean, because on day one ~10k *inherited* closed markets are already past it — that is a fact about Kalshi, reported honestly, not an alarm about the collector. The threshold is one constant, used only for reporting; no automatic retirement (a market leaves the set only by finalizing).

11. **Exit codes are explicit and shared with 263.** `0` success · `1` preflight (config missing, DB unreachable within `DB_CONNECT_TIMEOUT_SECONDS`, kalshi track not applied, or **another sync holds the run lock**) · `2` provider abort (a phase raised `ProviderTransientError`/`ProviderPermanentError` after the client's retries; state advanced only as far as completed) · `3` completed with item-level errors (Decision 9 and the integrity row of the storage taxonomy) · `4` storage abort (`psycopg.OperationalError` — connection lost, statement timeout, connect failure mid-run; committed pages and watermarks stand). Preflight takes a session-level advisory lock (`pg_try_advisory_lock(SYNC_ADVISORY_LOCK_KEY)`) on the run's connection so two syncs never write concurrently — systemd serializes 263's unit against itself, but an operator's hand-run alongside the timer would not otherwise be prevented. Constants in `cli/commands/kalshi.py`; 263 maps them to the unit's failure semantics.

12. **CLI: `mt data kalshi sync` / `mt data kalshi status`, new module.** `data.py` is already 136 KB; the kalshi group lives in `cli/commands/kalshi.py` and is attached with `data_app.add_typer(kalshi_app, name="kalshi")`. Names finalize the architecture's "e.g. `mt data kalshi status`"; 263 adds `pass` alongside. Production reach is `mt-run data kalshi status` via 916's pass-through — nothing to add.

13. **Rate budget override at wiring time.** `Settings.kalshi_requests_per_minute: int | None = None`; when set it replaces the mode's constant (`RateLimit(requests_per_minute=…)`) at `KalshiClient` construction in the CLI. Unset → 261's mode defaults, unchanged.

14. **Structured events mirror the 120 shape but are Kalshi-typed.** `AcquisitionEvent` carries `symbol`/`granularity` fields that have no meaning here; forcing them would be the "labels as structure" anti-pattern. `events.py` defines `SyncEventType` (`run_started`, `phase_finished`, `item_error`, `run_finished`), a `SyncEvent` dataclass (run_id, timestamp, phase, counts dict, transitions dict, error, duration_ms), the `SyncEventSink` Protocol, `NullSyncEventSink`, and `JsonlSyncEventSink` (`--events-file PATH`). Per-item events exist only for errors; everything else is aggregated per phase so the sink is not flooded by 74k settlements a day.

## Implementation Details

### Constants added (`data/kalshi/constants.py`)

`CATALOG_WALK_FILTERS = (MarketStatusFilter.UNOPENED, OPEN, PAUSED, CLOSED)` · `KALSHI_MVE_FILTER = "exclude"` · `MARKETS_PAGE_LIMIT = 1000` · `EVENTS_PAGE_LIMIT = 200` · `TICKERS_BATCH_SIZE = 100` · `SETTLED_WINDOW = timedelta(hours=6)` · `WINDOW_OVERLAP = timedelta(seconds=1)` · `KALSHI_SETTLEMENT_STUCK_AFTER = timedelta(days=7)` · `AWAITING_AGE_BUCKETS = (1 d, 7 d, 30 d)` (histogram edges; the 7 d edge *is* the stuck threshold, referenced not repeated) · `DB_CONNECT_TIMEOUT_SECONDS` · `SYNC_ADVISORY_LOCK_KEY` (a fixed bigint; the lock namespace is this key alone).

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

### Storage failure taxonomy (review 262 F001)

Transaction granularity first, because it bounds every failure below: **one transaction per written page** (a page's parent events, its markets, and the transition select), one per settled-window page, one per awaiting-set statement group, and one per `sync_state` update — which is written only after the window or walk it describes has committed. A failure therefore loses at most one page of writes, and persisted state never claims more than what is on disk. Classification is complete over `psycopg.Error` (psycopg 3 hierarchy; `psycopg_pool` is not used):

| Failure | Raised as | Handling |
|---|---|---|
| Connection lost mid-write, server restart, connect failure on a reconnect | `psycopg.OperationalError` | **Abort the run, exit 4.** `logger.exception` with phase and page context, `run_finished` event with `error`. The in-flight transaction rolled back with the connection; committed pages and watermarks stand. No in-process retry — the next pass is the retry (fail loud, back off hard). |
| `statement_timeout` expiry (`QueryCanceled`) | `OperationalError` subclass | Same as above. The session runs under `DB_BULK_SESSION` (`statement_timeout=300s`), so no statement can wedge a pass (journal 20260806). |
| Deadlock / serialization failure (`DeadlockDetected`, `SerializationFailure`) | `OperationalError` subclasses | Same as above, but **unreachable by construction**: the sync is the only writer of `kalshi.*`, and the advisory lock (Decision 11) turns a concurrent sync into a preflight refusal (exit 1) rather than contention. |
| Page rejected: `CheckViolation` (unknown served status — 261 Decision 7) or `ForeignKeyViolation` (a parent vanished between resolution and write) | `psycopg.IntegrityError` subclasses | The page's transaction rolls back and the page is **re-written row by row** in its own transactions, so only the offending rows become item errors (ticker and SQLSTATE at ERROR; `item_error` events). The run continues; **exit 3**. Bounded: at most one page (1,000 single-row statements) per rejected page. |
| `UniqueViolation` | `IntegrityError` | Unreachable with `ON CONFLICT (ticker)`; handled as the row above if it ever occurs. |
| Any other `psycopg.Error` (`ProgrammingError`, `DataError`, `InternalError`, `InterfaceError`) | — | A bug in our SQL or mapping. **Not caught** — propagates with a traceback, consistent with 261's rule that nothing outside the enumerated set is swallowed. |
| `SyncEventSink.emit` raises | any | Best-effort: logged at ERROR, never aborts the run (120 precedent). |
| SIGTERM / SIGINT (systemd stop, Ctrl-C) | `CancelledError` / `KeyboardInterrupt` | `asyncio.run` cancels the phase; the open transaction rolls back when the connection closes; state is consistent by construction. Default signal exit status; no handler of our own. |

Preflight (before `run_started`): connect with `connect_timeout=DB_CONNECT_TIMEOUT_SECONDS`, apply session settings, verify `kalshi.sync_state` exists (else "apply the kalshi track", exit 1), take the advisory lock (else "another sync holds the lock", exit 1). Everything after that is one of the rows above.

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
11. Storage failure handling is proven on a throwaway database, one test per reachable row of the taxonomy: a fixture page carrying an out-of-vocabulary status → row-by-row rewrite, the one ticker as an item error, every other row written, exit 3; the run's backend terminated mid-walk from a second connection (`pg_terminate_backend`) → exit 4 with committed pages present and `sync_state` unchanged; a second sync started while the first holds the lock → exit 1 with the lock message.

## Verification Walkthrough

Refined at the end of Phase 6 (2026-08-25) from a rehearsal on throwaway database `mt_262_rehearsal_9e3bc0ba` on the test cluster (public mode, budget 300/min), dropped afterwards. Steps 0 and 6 are PM / operator actions after merge — not performed by this slice's automation. Every other step was run as written; the outputs below are what was observed.

```bash
# 0. PM — apply this slice's kalshi_004 to production (runbook 100, Update procedure).
#    261's three migrations were applied 2026-08-24; only kalshi_004 should show pending.
cd ~/source/repos/manta/trading-data
uv run mt data migrate status --track kalshi         # pre-flight: kalshi_004 pending (app credential)
uv run mt data migrate apply  --track kalshi         # maintenance credential, interactive shell
uv run mt data migrate status --track kalshi         # → "… applied, 0 pending" (comments only; re-apply is a no-op)

# 1. Status before any sync — reports, never refuses
uv run mt data kalshi status
#    → "Kalshi catalog has never synced."   (exit 0; --json → {"synced": false})

# 2. Rehearsal on a throwaway database (test cluster, runbook 400). The track was applied with
#    apply_migrations(TRACKS["kalshi"]) against the throwaway URL; MT_TIMESCALE_DB_URL pointed at it
#    for the process only (never edit .env for this).
uv run mt data kalshi sync --settled-since 2026-08-25T00:00:00Z --events-file /tmp/kalshi-run1.jsonl
#    observed 2026-08-25 00:24–00:26 UTC, exit 3, 118 s:
#      series      fetched 13,445  written 13,445
#      markets     fetched 175,232 written 170,775  skipped 4,457   transitions initialized→active 12
#      events      fetched 0 (first run: no floor)
#      settled     windows 1  captured 2,230        watermark → 2026-08-25T00:26:27Z (run start)
#      awaiting    entered 9,838  retired 0  checked 0  unreachable 0
#    The 4,457 skips (366 distinct parent events) were the tickers-batch quirk described in the
#    Implementation disposition below; with the single-event fallback now in place the walk stores
#    them (one-time cost ≈366 extra requests) and a clean catalog reaches exit 0.
uv run mt data kalshi sync --settled-since 2026-08-25T00:00:00Z --events-file /tmp/kalshi-run2.jsonl --json
#    observed 95 s later, exit 3 (same skips), write-on-change proven:
#      series 13,445 fetched / 0 written · markets 175,235 fetched / 8,254 written (live price/volume
#      changes) · events 5 fetched / 5 written (min_updated_ts refresh) · settled 2,235 fetched / 5 written
#      transitions initialized→active 65, active→inactive 6, active→closed 5, inactive→determined 184,
#      determined→finalized 2, active→finalized 12 · awaiting entered 201  retired 14
uv run mt data kalshi status
#    Kalshi catalog
#      last full sync      2026-08-25 00:26:27 UTC  (3 min ago)
#      settled watermark   2026-08-25 00:26:27 UTC  (3 min ago)
#      series / events     13,445 / 14,293
#    Markets by status     initialized 57,689 · active 102,886 · inactive 350 · closed 9,288 · determined 550 · finalized 2,230
#    Awaiting settlement   9,838 markets
#      age                 <1d 962 · 1d-7d 425 · 7d-30d 860 · >30d 7,591
#      past 7d threshold   8,451   oldest HOMEUS-23JUN-T1.0 (1,159 d)
#      checked directly    0  (looked up by ticker; still unsettled)
wc -l /tmp/kalshi-run1.jsonl                         # 7 + one item_error line per skipped market (4,464 in run 1)
#    run_started, phase_finished ×5 (series, markets, events, settled, awaiting), run_finished

# 3. Settlement capture, observed end-to-end on the throwaway DB (no waiting needed at this
#    catalog's pace: 14 awaiting markets retired between the two runs above, 2 min apart)
uv run psql "$MT_TIMESCALE_DB_URL" -c "select ticker, close_time, settlement_ts, result from kalshi.markets \
   where status='finalized' and close_time < '<run 1 start>' and settlement_ts > '<run 1 start>' \
   and ticker not in (select market_ticker from kalshi.awaiting_settlement) order by settlement_ts limit 3"
#    KXMLBTOTAL-26AUG241840TBDET-5 | 00:24:31Z | 00:26:37Z | yes     ← entered by run 1, captured by
#    KXWTAMATCH-26AUG24DOLPOD-POD  | 00:25:45Z | 00:27:47Z | yes       run 2's settled stream, retired
#    KXWTAMATCH-26AUG24DOLPOD-DOL  | 00:25:45Z | 00:27:47Z | no

# 4. Interrupted drain resumes without loss (throwaway DB)
#    (on the throwaway DB the earlier runs had already set a watermark; it was NULLed first so this
#     drain behaves like the first production run — see the replay caveat below)
uv run mt data kalshi sync --settled-since 2026-08-15T00:00:00Z --events-file /tmp/kalshi-run5.jsonl
#    SIGINT 175 s in (walk 86 s, then ~90 s of drain). Default signal exit (KeyboardInterrupt
#    traceback, shell exit 130 / `timeout` 124); no handler of our own. No run_finished event.
uv run mt data kalshi status
#      last full sync      2026-08-25 00:29:29 UTC   ← unchanged: the interrupted run never reached phase 6
#      settled watermark   2026-08-17 06:00:00 UTC   ← floor + 9 fully walked windows, exactly on the boundary
uv run mt data kalshi sync --events-file /tmp/kalshi-run6.jsonl --json
#    resumed at 2026-08-17 06:00 − 1 s and drained to the run start: exit 0, outcome ok, 451 s;
#      settled fetched 551,582 / written 540,402 (the difference = rows the interrupted run had already
#      stored, free on re-walk) over 32 windows; watermark → 2026-08-25T00:47:20Z (the run start);
#      markets 174,719 fetched / 10,250 written (live churn), 0 item errors — the first clean exit 0 once
#      the single-event parent fallback was in place.
#    No gap / no duplicate: for windows 8, 9 (the one re-walked) and 10, the API's count with the
#    same strict bounds equals count(*) of kalshi.markets by settlement_ts — observed
#      window 8 16,163 = 16,163 · window 9 14,836 = 14,836 · window 10 18,072 = 18,072
#    (ticker is the primary key, so a duplicate row is impossible by construction).

# 5. Tests (unit tier, then the kalshi integration set, then the type gate)
uv run pytest test/unit -q                                              # 2026-08-25: green
uv run python scripts/run_tests.py integration -- -k kalshi -q          # 45 passed
uv run ruff check src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py test/kalshi_support test/unit/data/kalshi
uv run --extra dev mypy src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py
npx --yes pyright src/manta_trading/data/kalshi src/manta_trading/cli/commands/kalshi.py test/kalshi_support test/unit/data/kalshi test/integration/test_kalshi_sync.py

# 6. First production run (dev checkout, application credential) — the long one
uv run mt data kalshi sync --events-file ~/kalshi-first-sync.jsonl
#    → first-run drain from the historical cutoff (2026-06-25): ~4.5M settled markets,
#      ~15 min of requests at the public budget plus write time; watermark climbs per 6 h window.
#      Expect exit 3 on the first pass only if the API omits a parent both in batch and singly.
uv run mt data kalshi status
mt-run data kalshi status                            # same, through the production front door
```

Caveats discovered in the rehearsal:
- A `--settled-since` *behind* the stored watermark replays those windows (duplicates are free) but cannot record progress in `watermark_ts` — the watermark never moves backwards (Decision 4, Task 5.6) — so an interrupted replay must be re-issued with the same `--settled-since`, not resumed bare. Resume-at-the-watermark applies to a first-run drain (watermark NULL) or a drain that started at or after the watermark, which is the production case (step 6).
- `GET /events?tickers=` silently omits some events (see the disposition); the single-event fallback covers it.
- Two syncs on one cluster share the advisory key: the lock is per *database* (`pg_locks.database`), so a sync on another database is not refused, and anything that kills "the backend holding the key" must scope by database — the first proof test did not and killed the rehearsal run (exit 4, committed pages kept, `sync_state` untouched — an unplanned live proof of the storage-abort row).
- Rapid back-to-back runs in public mode draw 429s; the client's backoff absorbs them but a run can start slowly.
- The Rich summary wraps long lines at 80 columns when not attached to a terminal; `--json` is the machine-readable form.

### Success criteria — where each is proven

| # | Criterion | Proof |
|---|---|---|
| 1 | fresh sync exit 0, tables populated, FKs, both `sync_state` columns | `test_kalshi_sync.py::TestEndToEnd::test_first_run_populates_and_sets_state`; rehearsal step 2 (exit 3 only for the batch quirk, now covered) |
| 2 | second run writes zero rows, `first_seen_at` untouched | `test_second_identical_run_writes_nothing`; rehearsal run 2 (series 0 written) |
| 3 | closed → awaiting; finalized+result → retired; vanished → looked up, `last_checked_at` | `test_awaiting_lifecycle`; unit `test_sync_awaiting.py`; rehearsal step 3 |
| 4 | windowed drain resumes from the last completed window, no gap, no duplicates | `test_interrupted_drain_resumes_without_gap_or_duplicates`; unit `test_sync_settled.py`; rehearsal step 4 |
| 5 | transitions per run in `phase_finished`; per-ticker `item_error`; JSONL valid | `test_events_file_is_valid_jsonl`; unit `test_sync_core.py`; `wc -l` in step 2 |
| 6 | `status` sections, histogram and threshold from the constant, works before any sync | `test_kalshi_status.py`; `test_data_kalshi.py::TestStatus`; steps 1 and 2 |
| 7 | exit codes 0/1/2/3 (+4); provider abort with an unreachable base URL | `test_data_kalshi.py::TestExitCodes`; `test_provider_abort_through_real_client` (real `KalshiClient`, `http://127.0.0.1:1`) |
| 8 | `kalshi_004` in the track, applies and re-applies; 261 tests pass | `test_kalshi_migrations.py::TestSyncStateComments` and the rest of that file |
| 9 | MVE never stored: every markets request carries `mve_filter=exclude` | `test_sync_awaiting.py::test_every_markets_query_of_a_full_run_excludes_mve` (walk, windows, lookups) |
| 10 | ruff, mypy, strict pyright clean; no new dependency | step 5; `git diff main -- pyproject.toml uv.lock` empty |
| 11 | storage taxonomy: out-of-vocabulary status → exit 3 with one item error; backend terminated → exit 4, pages kept, state unchanged; held lock → exit 1 | `TestStorageFailureProofs` (two tests) and `TestPreflight::test_held_lock_is_refused`; the rehearsal's accidental kill |

## Risk Assessment

- **Storage growth is real and PM-visible.** ~74k non-MVE settlements/day at ~2.2 KB served JSON (plus columns) ≈ 200 MB/day, ≈ 70 GB/year in `kalshi.markets`, ~11 GB for the first-run drain — driven by 15-minute crypto markets, three quarters of them zero-volume. This design captures them (the mandate is completeness; the PM's "watch growth" posture already anticipates hypertable promotion for the large tables). Levers if the PM wants them, each a separate decision: `--settled-since` for the first run, dropping `raw` for finalized zero-volume markets, or a series allow/deny list. None is built here.
- **Unauthenticated budget is undocumented.** A ~190-request pass at 5 req/s is modest; the first-run drain is ~4,500 requests over ~15 min. No 429 was seen in ~900 survey requests. Mitigation as in 261: 429 is transient with backoff; the budget override and authenticated mode (≈3× faster) are available.
- **Kalshi's `status` vocabulary drift.** A new served status fails the page's upsert on the CHECK — recorded as item errors, exit 3, run otherwise complete. Intended.

## Design review disposition (20260824)

Review: `user/reviews/262-review.slice.catalog-sync-with-settlement-capture.md`, claude-sonnet-5, verdict CONCERNS (one concern, one note, three passes), against `78fbd90`.

- **F001 (concern) — the async DB write path had no enumerated failure taxonomy.** Valid; it is the bar 261's client was held to (261 F004) and this slice's storage path is likewise a new I/O path. Answered by the new **Storage failure taxonomy** subsection and the revised Decisions 8 and 11: one connection per run (no pool, so exhaustion and hung checkouts are not modes), per-page transactions bounding any loss to one page, classification complete over `psycopg.Error` (operational faults → exit 4; page-level integrity rejections → row-by-row rewrite and exit 3; anything else → uncaught, a bug), `DB_BULK_SESSION`'s statement timeout, a session-level advisory lock that makes deadlock unreachable and refuses a concurrent sync at preflight, and Success Criterion 11 proving the three reachable modes on a throwaway database.
- **F002 (note) — MVE exclusion narrows the binding constraint without a recorded sanction.** Valid as of the review. The PM reviewed the MVE explanation and sanctioned the exclusion on 2026-08-24; the sanction is now recorded at architecture level (260-arch, *Catalog scale and incremental sync*: the constraint's universe is the non-MVE catalog) and in the 260 slice plan Notes, and Decision 2 points to both. The PM also confirmed the storage figure (~70 GB/year) is acceptable and that market selectivity belongs to 264/265, not the catalog.
- **F003, F004, F005 (pass).** No action.

## Code review disposition (20260825)

Review: `user/reviews/262-review.code.catalog-sync-with-settlement-capture.md`, claude-sonnet-5, verdict CONCERNS (one concern, one note), against `01176c3`.

- **F001 (concern) — `JsonlSyncEventSink.emit` did synchronous file I/O on the event loop.** Valid under the project's async rule (<1 ms worst case for synchronous work inside `async def`; a flush on slow storage has no such bound, and a page of item errors emits once per row). Fixed: `CatalogSync.emit` is now `async` and runs the sink call in a worker thread (`asyncio.to_thread`); `phase_finished` / `item_error` follow. The `SyncEventSink` Protocol stays synchronous, so sinks remain trivial and 263 routes pass events unchanged. The core is a single sequential writer, so at most one sink call is in flight.
- **F002 (note) — parent series/events written during a markets page are not counted in the series/events phases.** Intentional: a phase count is that phase's own work (series list; `min_updated_ts` refresh), and parent rows created while resolving a page are a side effect of that page. Folding them in would make the series/events lines depend on which markets happened to be walked first. Documented at `_own_kind` in `sync_writer.py`.

## Implementation disposition (20260825)

What Phase 6 found that the design did not anticipate, and what was done about it.

- **`GET /events?tickers=` silently omits some events.** In the first live walk, 4,457 markets (366 distinct parent events — older events such as `KXNOCONFFRA-25`, `KXCANREGISTER-25APR`, `KXNFLPREPACKSGP-…` whose markets are still on the live endpoints) were skipped because the batch lookup returned nothing for them, while `GET /events/{ticker}` returned each one. Decision 9's mechanism assumed the batch was complete for known tickers; its intent — resolve every parent, never write a placeholder — is unchanged. **Amendment to Decision 9:** tickers the batch omits are fetched singly (`CatalogSource.get_event`), the same per-item shape the design already uses for series; only a parent that is unobtainable both ways becomes an item error. One-time cost ≈366 requests; afterwards the events are stored and cost nothing. *PM attention:* this is a small design addition made during implementation on the strength of the live evidence; veto or ratify.
- **The advisory lock is per database.** `pg_try_advisory_lock` keys are scoped to the connected database, so the lock refuses a concurrent sync on the *same* database only — correct for production (one `trading` database), but a test that terminates "the backend holding the key" must scope by `pg_locks.database`. The first version of the storage proof did not and killed the rehearsal run on a sibling database (which, usefully, proved the storage-abort path live: exit 4, committed pages intact, `sync_state` unchanged). Fixed in the test.
- **`session_statements`** (`market/db_session.py`) now exists so the sync's async preflight and the pool `configure` hook issue the same `SET`s from one list, composed with `psycopg.sql.Literal` rather than f-strings.
- **Status timestamps** are rendered in UTC explicitly: psycopg returns `timestamptz` in the session's zone.
- **Interrupted drain (walkthrough step 4):** observed as recorded in the walkthrough — SIGINT after 9 windows left `watermark_ts` exactly on the window boundary (2026-08-17 06:00 UTC) with `last_full_sync_at` untouched; the bare re-run resumed there, drained 32 windows to the run start, and windows 8–10 matched the API count for count.

## Implementation Notes

### Development Approach

Suggested order: constants + `events.py` → `repository.py` with integration tests on `ephemeral_db` (upserts, guard, awaiting statements) → `CatalogSource` fake + `sync.py` phases with unit tests (series → markets walk → events refresh → settled windows → reconciliation) → `kalshi_004` → `status.py` → CLI module + exit codes → recorder targets and fixtures → rehearsal on a throwaway database (walkthrough steps 2–5) → walkthrough refresh.

Branch: `262-slice.catalog-sync-with-settlement-capture` from `main` (no integration branch configured).

### Special Considerations
- The first production run is long and writes millions of rows; run it from an interactive shell with `--events-file`, not from a timer (263 will inherit a warm catalog).
- All destructive statements in tests target `ephemeral_db` databases only; `status` and `sync` use the application credential (DML) and never the maintenance URL.
- Nothing in this slice references `public` (extraction discipline).
- `SyncResult` should be JSON-serializable from day one — 263's pass summary and any future `mt-run status` row will consume it.
