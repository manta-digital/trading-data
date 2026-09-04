---
docType: architecture
component: kalshi-event-contract-data
project: trading
parent: 001-initiative-plan.trading.md
dependencies:
  - 900-arch.foundation-cleanup.md
  - 100-arch.data-storage.md
relatedSlices: []
riskLevel: low
archIndex: 260
dateCreated: 20260823
dateUpdated: 20260903
status: complete
---

# Kalshi Event-Contract Data Architecture

## Overview

Initiative 260 adds continuous collection of Kalshi prediction-market data to trading-data. Kalshi is a regulated event-contract exchange; its public REST API (`trade-api/v2`) serves market data without authentication. This initiative builds a relational catalog that mirrors Kalshi's own hierarchy — series → events → markets — with settlement outcomes captured as markets close, plus the time-series surfaces that hang off that catalog: candlesticks and public trades.

**Scope:** A Kalshi API client, relational schema for the catalog and time-series surfaces, a bounded collection pass run on a timer under the supervised-production model established by slice 916, and CLI surfaces for status and manual operation. Orderbook snapshots are optional/later scope. Strategy evaluation, pricing analysis, and any trading against Kalshi are out of scope — this is collection and storage only, consistent with the project's data-layer boundary.

**Motivation:** The value is time-sensitive. Kalshi is migrating settled markets, old trades, and candlesticks behind `/historical/*` endpoints with a cutoff timestamp whose retention policy is theirs to change, and orderbook depth is live-only. Data not collected now may be unobtainable later. The collector is deliberately structured as a low-priority background accumulator: it gathers data continuously while higher-priority initiatives (220 futures ticks, 240 flat-file import) proceed, so that when event-contract data becomes analytically interesting, years of it already exist.

## Design Goals

- **Capture before it disappears** — The primary goal is completeness of the record while it is still reachable: full catalog lifecycle (open → close → settlement outcome), candlestick history, and public trades, collected ahead of Kalshi's historical-endpoint migration and retention decisions. **Scope of "complete" (PM decisions 20260824 and 20260826, recorded at slice 262 and 264 design):** the *catalog* is complete for every non-MVE market; the *time-series surfaces* are complete for the markets a configurable **collection rule** selects, not for the whole catalog — except the trade tape, which is additionally scoped by the **trades filter** (`MT_KALSHI_TRADES_EXCLUDED_CATEGORIES`, slice 268): a tape-filtered market's trade tape is deliberately not collected and is excluded from tape-completeness evaluation. The rule exists because measurement showed the unfiltered candle stream to be ~600 GB/year of which 97% is market-maker re-quoting on markets that never trade; the project's default rule keeps markets that traded in the last 24 hours and excludes the Sports and Mentions categories (~31 GB/year compressed), and any operator can set a different rule (`MT_KALSHI_CANDLE_*`) because the collector ships publicly while the collected data cannot be redistributed under Kalshi's API terms. Excluded markets are counted and reported by `mt data kalshi status`, never silently dropped.

- **Faithful catalog, Kalshi's shape** — Store the domain in Kalshi's own hierarchy (series → events → markets) with Kalshi's own identifiers. The catalog is queryable on its own terms; no translation into the equities instrument model is attempted.

- **Unattended low-priority operation** — Collection runs on a timer under systemd supervision and needs no routine attention: each pass syncs until caught up, backs off on errors, and resumes from persisted state. Low priority is currently an intent, not an enforced property: slice 916 explicitly deferred cross-source arbitration (its acquisition slice ships with no resource weights, and two pass units run concurrently with no priority mechanism), naming Kalshi as the trigger for solving it. Until that arbitration slice lands, the mitigations are structural — a bounded pass on a sparse timer, a conservative rate budget, and workload that is small relative to EODHD acquisition. This gap is inherited, not solved here.

- **Pattern reuse, not framework invention** — The persisted acquisition state, structured event emission, idempotent writes, and CLI-parity patterns proven in Initiative 120, and the oneshot-pass/timer/install patterns proven in slice 916, are reused. This initiative should feel like another instance of established shapes, not a new design.

- **Isolation from the core pipeline** — Kalshi collection lives in its own tables and its own pass unit. A Kalshi outage, API change, or collector bug cannot affect equities acquisition, quality operations, or serving.

## Architectural Principles

- **The catalog is the spine; time series hang off it** — Series, events, and markets form the referential backbone. Candlesticks and trades reference market tickers. Within each collection pass, catalog sync completes before the time-series surfaces run, and candle/trade collection operates on the post-sync market set. Surfaces are sequential phases of a pass, not concurrent tasks; concurrency (bounded per-market fetch pools, per the 120 async-fetch pattern) lives *within* a phase. A candle or trade for an unknown market therefore indicates a sync defect, not an acceptable race.

- **Kalshi identifiers are primary keys, not labels** — Series tickers, event tickers, and market tickers are Kalshi's stable identifiers and are used as the join keys. They do not enter the equities `instruments` registry; the Kalshi domain is self-contained. Kalshi does register in the provider registry: a `ProviderType.KALSHI` member and a `ProviderProfile` using the existing `AuthType.NONE`, per 900's no-magic-strings discipline.

- **Progress is persisted, not inferred** — Per-surface watermarks (catalog sync cursor state, per-market candle watermarks, trade cursor) live in database state, exactly as Initiative 120's `acquisition_state` pattern established. A pass resumes from state, never from scratch, and never by re-deriving position from the data tables.

- **Idempotent writes everywhere** — Catalog rows upsert on ticker. Candles insert on the natural key `(market_ticker, period, timestamp)` with conflict-ignore; trades insert on Kalshi's trade id alone. Re-fetching an overlapping window is always safe, which keeps retry and resume logic simple.

- **Settlement is a first-class collection event** — A market is not "done" at close; it is done when its settlement outcome is recorded. Catalog sync tracks markets through determination/settlement in an explicit awaiting-settlement set and only then retires them from the active polling set. The age of every market in that set is a required status surface (what threshold marks one "stuck" is a slice-design decision, but the visibility is not optional).

- **Polling REST first; websocket only if earned** — Modest data volumes and generous public-tier rate limits make polling sufficient. Under 916's taxonomy this also settles the process form: REST polling is a bounded workload and gets the oneshot-pass shape; the long-running `Type=simple` form is reserved for genuinely streaming subscriptions, which Kalshi does not need unless orderbook capture is later scoped.

- **CLI is the baseline, the timer is the target** — Every collection operation works as a one-shot CLI command sharing the pass's orchestrator and state. The production timer runs the same pass automated; a manual run is the same code path invoked by an operator. One code path, no state divergence.

- **Fail loud, back off hard** — Provider errors are logged with full context and surfaced in status. As a low-priority collector, the correct response to sustained API trouble is aggressive backoff, a nonzero pass exit surfaced by systemd, and a visible degraded status — never tight retry loops.

## Current State

No Kalshi code exists in the repository. What exists to build on:

- **Initiative 120 acquisition patterns (complete)** — orchestrator core, persisted `acquisition_state`, structured event emission (`data/acquisition/events.py`, `state.py`, `orchestrator.py`), retry/backoff, and the CLI/automation shared-code-path pattern.
- **Slice 916 supervised production services (complete)** — the production form new sources follow: a bounded `mt`-CLI pass wrapped in a `mt-{source}-pass.service` oneshot unit fired by a `.timer`, grouped under `manta-acquisition.slice`, installed by `deploy/install-production.sh` into the pinned `/opt/manta-trading` checkout, and fronted by the `mt-run` operator wrapper. 916 explicitly anticipated Kalshi as the next such source.
- **Initiative 900 foundation (complete)** — Typer CLI framework, TOML config with precedence, structured logging, provider registry with enums (`ProviderType`, `AuthType`, `ProviderProfile`), `src/` layout.
- **Initiative 100 storage layer (complete)** — the two-host storage split (plain PostgreSQL on the prototype host for daily; TimescaleDB on the database host for minute) and the migration framework with per-track migration chains.

What is missing is everything Kalshi-specific: API client, schema and migration track, catalog sync, time-series collectors, pass command, and CLI status surface.

## Envisioned State

A Kalshi collection pass — a bounded run that brings all surfaces up to date and exits — fired on a timer as a supervised oneshot unit on the database host, per the 916 production model. Each pass executes its surfaces in order:

- **Catalog sync** — Discovers and refreshes series, events, and markets; tracks market lifecycle status; captures settlement outcomes for the awaiting-settlement set. Active/near-close markets refresh every pass; settled markets are retired from polling once their outcome is recorded.

- **Candlestick collection** — Appends 1-minute candlestick history for each market the collection rule selects (Design Goals), from the moment the collector first sees it trade (with a bounded lookback) through its close, driven by per-market watermarks against the freshly synced market set. Kalshi serves a candle only for a period in which the book or tape moved, so the record is sparse by nature; the watermark records *through when* candles were requested, not the newest candle stored (slice 264).

- **Public trades collection** — Appends the public trade tape, cursor-driven, idempotent on trade id.

**Storage** — Kalshi tables live on the TimescaleDB host (the database host where the pass runs), as their own migration track alongside the existing daily and minute tracks. Placing them there is deliberate: the default posture is plain relational tables, and keeping them on the TimescaleDB host means later promotion of trades or candles to a hypertable is an in-place operation, not a cross-host migration.

**Operational model** — the same one 916 built, extended by one source:

- The timer runs the pass automated. `sudo mt-run kalshi` runs one manually with live streamed output (a wrapper-verb addition to `mt-run`), and `mt-run status` reports the pass unit alongside daily/minute: running/idle, last result, next timer firing.
- Data-level status is a CLI command (surface finalized at slice design, e.g. `mt data kalshi status`) reading persisted collection state, reachable in production through `mt-run`'s pass-through (`mt-run data kalshi status`). It answers "complete until when?" from per-surface watermarks and the completeness definition below, and reports the awaiting-settlement set with ages, markets with candle coverage short of close, and ranges known-lost behind the historical cutoff.
- Production runs entirely from the pinned install; the dev checkout retains only its existing runbook roles (operator-run migrations with the maintenance credential, and deploys).

**Completeness definitions** (the analogue of 120's caught-up definitions): a *closed market is complete* when its settlement outcome is recorded, its candles cover open through close, and its trade tape reaches close. The *collector is caught up* when every market past close is complete, explicitly marked unrecoverable (behind the historical cutoff), or tape-filtered; and open-market surfaces are within one pass interval of now. (Candle and settlement completeness for tape-filtered markets are unchanged — the filter touches only the tape clause; slice 268.) These definitions are what the status command evaluates.

At completion, event-contract data accumulates with no operational attention, the settlement record is complete for every market the collector has seen close, and downstream consumers (future analysis work, Initiative 180 serving if ever extended) find a coherent relational catalog.

**A deliberate departure from 120:** 120's service-per-concern principle separated daily and minute into independent processes because their workload characteristics differ materially. Kalshi's three surfaces are folded into a single pass instead: they share one rate budget, one catalog spine, and one modest volume profile, and the catalog-leads ordering is trivially enforced by phase sequencing within one process. The surfaces remain separable — each has its own watermarks, one-shot CLI command, and phase boundary — so splitting into independent pass units later is a unit-file change, not a redesign.

## Technical Considerations

- **Historical-endpoint migration and the cutoff timestamp** — Kalshi's split of old data behind `/historical/*` endpoints, governed by a moving cutoff, is the central external constraint. The collector must treat the cutoff as data (discovered, not hardcoded), prefer live endpoints for everything still on them, and the design must leave room for a one-time historical backfill slice that drains the historical endpoints while they remain available. Exact endpoint behavior and the current cutoff must be verified against Kalshi's published documentation during slice design — not assumed.

- **Catalog scale and incremental sync** — Kalshi's market catalog is large (tens of thousands of markets across their lifecycle) and cursor-paginated. A full crawl every pass is wasteful; a status- and close-time-filtered incremental sync risks missing transitions. The sync strategy — what gets a full pass, what gets an incremental pass, and on what cadence — is the main algorithmic decision for slice design. The architectural constraint on that decision: whatever the strategy, no market may reach settlement unobserved, so the awaiting-settlement set must be maintained from close events, not from whichever markets the incremental filter happened to return. **Universe (PM decision 20260824, recorded at slice 262 design review):** the catalog, this constraint, and the completeness definitions cover Kalshi's regular markets; multivariate-event (MVE/parlay) markets — user-composed multi-leg tickets, ~2,000 created per hour, zero volume, not listed by `/events` — are excluded from collection entirely (`mve_filter=exclude` on every markets request). Re-including them would be a separate scoping decision with its own storage plan.

- **Settlement capture timing** — Markets pass through close → determination → settlement, and the interval varies by market. The awaiting-settlement set and its age-visibility requirement (Architectural Principles) cover the mechanism; the stuck-threshold and any operator alerting are slice-design decisions.

- **Rate-limit budget** — Public-tier limits are generous relative to this workload, but they are tiered and Kalshi's to change; the client needs the same configurable rate-limiter discipline as existing providers, budgeted so catalog sync, candles, and trades share one budget across the pass. The authenticated-tier question is resolved (20260824): the PM holds a funded, verified account and authenticated operation is planned near-term, so the client supports an optional signed mode from slice 261 with its documented (higher) budget — while the hard constraint stands unchanged: the collector must keep working without credentials, on the public tier.

- **Pass duration and the timer interval** — Unlike daily/minute passes, first-run Kalshi catch-up (full catalog crawl plus candle history) may take far longer than a steady-state pass. The oneshot unit's semantics already prevent overlap (systemd will not start a unit that is still running), but the timer interval and any first-run expectations should be chosen so steady-state passes are short and frequent while the initial catch-up simply runs long once.

- **Candlestick period selection** — Kalshi serves candlesticks at multiple period intervals. Collecting the finest interval and deriving coarser ones locally minimizes API surface but multiplies storage; collecting several intervals duplicates data. This trade-off is deferred to slice design, but the schema records the period explicitly (it is part of the candle natural key) so the decision can evolve.

- **Volume and storage posture** — Plain relational tables with proper indexes are the default posture for the catalog and state tables. For the time-series surfaces the "measure, then promote" stance was overtaken by measurement at design time: slice 264 measured the candle stream (1.4 M rows/day under the default rule, 262 B/row plain vs 61 B/row compressed) and, with PM ratification (20260826), creates `kalshi.candlesticks` as a hypertable from day one (7-day chunks per journal 20260719) with a compression policy at 14 days — because a table that reaches hundreds of millions of rows within its first year is cheapest to promote while empty. Slice 265 makes the same call for trades on its own measurement; its composite `(created_time, trade_id)` key was chosen for exactly this. Hosting the schema on the TimescaleDB host is what makes either choice a local one.

- **Orderbook snapshots (optional/later)** — Orderbook depth is live-only and unrecoverable, which argues for capturing it; but snapshot cadence, storage cost, and the likely need for the websocket API make it a separately-scoped decision. If scoped, it is the one Kalshi workload that would take the long-running streaming form (`Type=simple`, like `mt-serve`) rather than joining the pass. The initial architecture must not preclude it: the catalog gives any future snapshot collector its market universe.

- **Testing strategy** — Per existing project patterns: unit tests against an httpx mock transport with recorded Kalshi response fixtures (real response shapes, per the parsing rules), integration tests against a real throwaway database, and a pass testable as a function of (state, provider results) → (new state, writes).

## Anticipated Slices

Exploratory, not a commitment:

- **Kalshi client and catalog schema** — API client (public tier, rate-limited, error taxonomy), provider-registry entries, Kalshi migration track and schema on the TimescaleDB host, one-shot catalog sync with settlement capture, CLI entry point.
- **Collection pass and supervised install** — The bounded pass command (catalog phase first), persisted state, structured events, `mt-kalshi-pass.service` + `.timer` under `manta-acquisition.slice`, `mt-run` wrapper verb, status CLI, install-script integration per 916 patterns.
- **Candlestick collection** — Per-market watermarked candle acquisition as a pass phase.
- **Public trades collection** — Cursor-driven trade tape acquisition as a pass phase.
- **Historical backfill (conditional)** — One-time drain of `/historical/*` endpoints for data predating collector start, if verification shows recoverable data there.
- **Orderbook snapshots (optional/later)** — Only if scoped and prioritized; websocket-based streaming unit, separate from the pass.

## Related Work

- **001-initiative-plan.trading.md** — Initiative 260 definition; dependency on [900] (patterns from 120 are reused but 120 is not a blocking dependency).
- **916-slice.supervised-production-services** (complete) — The production form this initiative follows: oneshot passes + timers, `mt-run` front door, pinned `/opt` install. Also the source of the deferred cross-source arbitration gap this initiative inherits (916 named Kalshi as the trigger for that follow-on slice).
- **120-arch.data-acquisition.md** (complete) — Source of the persisted-state, structured-event, idempotent-write, and CLI-parity patterns this initiative instantiates. Its service-per-concern principle is deliberately departed from (see Envisioned State).
- **100-arch.data-storage.md** (complete) — The two-host storage split and migration framework; the Kalshi schema joins the TimescaleDB host as its own migration track.
- **900-arch.foundation-cleanup.md** (complete) — CLI framework, config, logging, provider registry.
- **000-process-journal.md, entry 20260823** — ADR recording the production form for new collectors (pass + timer + `mt-run`), applicable to the upcoming Databento tick decisions.
