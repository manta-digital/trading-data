---
docType: architecture
component: kalshi-event-contract-data
project: trading
parent: 001-initiative-plan.trading.md
dependencies:
  - 900-arch.foundation-cleanup.md
relatedSlices: []
riskLevel: low
archIndex: 260
dateCreated: 20260823
dateUpdated: 20260823
status: in_progress
---

# Kalshi Event-Contract Data Architecture

## Overview

Initiative 260 adds continuous collection of Kalshi prediction-market data to trading-data. Kalshi is a regulated event-contract exchange; its public REST API (`trade-api/v2`) serves market data without authentication. This initiative builds a relational catalog that mirrors Kalshi's own hierarchy — series → events → markets — with settlement outcomes captured as markets close, plus the time-series surfaces that hang off that catalog: candlesticks and public trades.

**Scope:** A Kalshi API client, relational schema for the catalog and time-series surfaces, a low-priority polling collector daemon built on the acquisition patterns from Initiative 120, and CLI surfaces for status and one-shot operation. Orderbook snapshots are optional/later scope. Strategy evaluation, pricing analysis, and any trading against Kalshi are out of scope — this is collection and storage only, consistent with the project's data-layer boundary.

**Motivation:** The value is time-sensitive. Kalshi is migrating settled markets, old trades, and candlesticks behind `/historical/*` endpoints with a cutoff timestamp whose retention policy is theirs to change, and orderbook depth is live-only. Data not collected now may be unobtainable later. The collector is deliberately structured as a low-priority background accumulator: it gathers data continuously while higher-priority initiatives (220 futures ticks, 240 flat-file import) proceed, so that when event-contract data becomes analytically interesting, years of it already exist.

## Design Goals

- **Capture before it disappears** — The primary goal is completeness of the record while it is still reachable: full catalog lifecycle (open → close → settlement outcome), candlestick history, and public trades, collected ahead of Kalshi's historical-endpoint migration and retention decisions.

- **Faithful catalog, Kalshi's shape** — Store the domain in Kalshi's own hierarchy (series → events → markets) with Kalshi's own identifiers. The catalog is queryable on its own terms; no translation into the equities instrument model is attempted.

- **Unattended low-priority operation** — The collector runs as a supervised daemon that needs no routine attention: it polls, backs off on errors, resumes from persisted state after restarts, and never competes with the production OHLCV pipeline for operational priority.

- **Pattern reuse, not framework invention** — The daemon loop, persisted acquisition state, structured event emission, idempotent writes, and CLI-parity patterns proven in Initiative 120 are reused. This initiative should feel like a third instance of an established shape, not a new design.

- **Isolation from the core pipeline** — Kalshi collection lives in its own schema/tables and its own daemon process. A Kalshi outage, API change, or collector bug cannot affect equities acquisition, quality operations, or serving.

## Architectural Principles

- **The catalog is the spine; time series hang off it** — Series, events, and markets form the referential backbone. Candlesticks and trades reference market tickers. Catalog sync must therefore lead the time-series collectors: a candle or trade for an unknown market indicates a sync gap, not an acceptable orphan.

- **Kalshi identifiers are primary keys, not labels** — Series tickers, event tickers, and market tickers are Kalshi's stable identifiers and are used as the join keys. They do not enter the equities `instruments` registry; the Kalshi domain is self-contained.

- **Progress is persisted, not inferred** — Per-surface watermarks (catalog sync cursor state, per-market candle/trade watermarks) live in database state, exactly as Initiative 120's `acquisition_state` pattern established. Restart resumes from state, never from scratch, and never by re-deriving position from the data tables.

- **Idempotent writes everywhere** — Catalog rows upsert on ticker; candles and trades insert with natural keys (`market_ticker + period + timestamp`, trade id) and conflict-ignore. Re-fetching an overlapping window is always safe, which keeps retry and resume logic simple.

- **Settlement is a first-class collection event** — A market is not "done" at close; it is done when its settlement outcome is recorded. The catalog sync loop tracks markets through determination/settlement and only then retires them from the active polling set.

- **Polling REST first; websocket only if earned** — Modest data volumes and generous public-tier rate limits make a polling daemon sufficient. Kalshi's websocket API exists as an upgrade path if a future need (e.g. orderbook capture) demands it, but nothing in the initial design depends on it.

- **CLI is the baseline, daemon is the target** — Every collection operation works as a one-shot CLI command sharing the daemon's orchestrator and state, per the 120 principle. The daemon is the loop; the CLI is one iteration.

- **Fail loud, back off hard** — Provider errors are logged with full context and surfaced in status. As a low-priority collector, the correct response to sustained API trouble is aggressive backoff and a visible degraded status — never tight retry loops.

## Current State

No Kalshi code exists in the repository. What exists to build on:

- **Initiative 120 acquisition patterns (complete)** — orchestrator core, persisted `acquisition_state`, structured event emission (`data/acquisition/events.py`, `state.py`, `orchestrator.py`), retry/backoff, and the CLI/daemon shared-code-path pattern.
- **Initiative 900 foundation (complete)** — Typer CLI framework, TOML config with precedence, structured logging, provider registry with enums, `src/` layout.
- **Initiative 100 storage layer (complete)** — TimescaleDB database with an established migration chain; new Kalshi tables join the existing migration discipline.
- **Slice 916 supervised production services (complete)** — systemd unit patterns and a real install path for daemons; the Kalshi collector deploys as another supervised service.

What is missing is everything Kalshi-specific: API client, schema, catalog sync, time-series collectors, daemon, and CLI surface.

## Envisioned State

A single low-priority Kalshi collector daemon running as a supervised service on the database host, cycling three collection surfaces against the public API:

- **Catalog sync** — Discovers and refreshes series, events, and markets; tracks market lifecycle status; captures settlement outcomes when markets close. Active/near-close markets refresh frequently; settled markets are retired from polling once their outcome is recorded.

- **Candlestick collection** — Appends candlestick history per market from each market's open through its close, driven by per-market watermarks.

- **Public trades collection** — Appends the public trade tape, cursor-driven, idempotent on trade id.

Shared with the rest of the system: persisted collection state and structured events in the database; status queryable via CLI (`mt data kalshi status` or equivalent surface, finalized at slice design); one-shot CLI commands for each surface; deployment and supervision via the 916 service patterns. Storage is plain relational tables — a hypertable is adopted for trades or candles only if observed volume warrants it.

At completion, event-contract data accumulates continuously with no operational attention, the settlement record is complete for every market the collector has seen close, and downstream consumers (future analysis work, Initiative 180 serving if ever extended) find a coherent relational catalog.

## Technical Considerations

- **Historical-endpoint migration and the cutoff timestamp** — Kalshi's split of old data behind `/historical/*` endpoints, governed by a moving cutoff, is the central external constraint. The collector must treat the cutoff as data (discovered, not hardcoded), prefer live endpoints for everything still on them, and the design must leave room for a one-time historical backfill slice that drains the historical endpoints while they remain available. Exact endpoint behavior and the current cutoff must be verified against Kalshi's published documentation during slice design — not assumed.

- **Catalog scale and incremental sync** — Kalshi's market catalog is large (tens of thousands of markets across their lifecycle) and cursor-paginated. A full crawl every cycle is wasteful; a status- and close-time-filtered incremental sync risks missing transitions. The sync strategy — what gets a full pass, what gets an incremental pass, and on what cadence — is the main algorithmic decision for slice design.

- **Settlement capture timing** — Markets pass through close → determination → settlement, and the interval varies by market. The catalog loop needs an explicit "awaiting settlement" set that keeps polling closed markets until the outcome lands, with visibility into markets stuck in that state.

- **Rate-limit budget** — Public-tier limits are generous relative to this workload, but they are tiered and Kalshi's to change; the client needs the same configurable rate-limiter discipline as existing providers, budgeted so catalog sync, candles, and trades share one budget. Whether an authenticated tier is worth adopting for higher limits is an open decision — the architecture assumes the unauthenticated public tier and must not require credentials.

- **Candlestick period selection** — Kalshi serves candlesticks at multiple period intervals. Collecting the finest interval and deriving coarser ones locally minimizes API surface but multiplies storage; collecting several intervals duplicates data. This trade-off is deferred to slice design, but the schema should record the period explicitly so the decision can evolve.

- **Volume and storage posture** — Expected volumes are modest compared to minute OHLCV (thousands of markets, most thinly traded). Plain relational tables with proper indexes are the default posture; promotion of trades/candles to hypertables is a measured decision after real volume is observed, not an up-front commitment.

- **Orderbook snapshots (optional/later)** — Orderbook depth is live-only and unrecoverable, which argues for capturing it; but snapshot cadence, storage cost, and the likely need for the websocket API make it a separately-scoped decision. The initial architecture must not preclude it: the catalog gives any future snapshot collector its market universe.

- **Testing strategy** — Per existing project patterns: unit tests against an httpx mock transport with recorded Kalshi response fixtures (real response shapes, per the parsing rules), integration tests against a real throwaway database, and a collector cycle testable as a function of (state, provider results) → (new state, writes).

## Anticipated Slices

Exploratory, not a commitment:

- **Kalshi client and catalog schema** — API client (public tier, rate-limited, error taxonomy), relational schema via the migration chain, one-shot catalog sync with settlement capture, CLI entry point.
- **Collector daemon** — Wrap catalog sync in the supervised daemon loop: persisted state, structured events, graceful shutdown, status CLI, systemd install per 916 patterns.
- **Candlestick collection** — Per-market watermarked candle acquisition integrated into the daemon cycle.
- **Public trades collection** — Cursor-driven trade tape acquisition integrated into the daemon cycle.
- **Historical backfill (conditional)** — One-time drain of `/historical/*` endpoints for data predating collector start, if verification shows recoverable data there.
- **Orderbook snapshots (optional/later)** — Only if scoped and prioritized; likely websocket-based.

## Related Work

- **001-initiative-plan.trading.md** — Initiative 260 definition; dependency on [900] (patterns from 120 are reused but 120 is not a blocking dependency).
- **120-arch.data-acquisition.md** (complete) — Source of the daemon/orchestrator, persisted-state, structured-event, and CLI-parity patterns this initiative instantiates.
- **900-arch.foundation-cleanup.md** (complete) — CLI framework, config, logging, provider registry.
- **100-arch.data-storage.md** (complete) — Database, migration chain, storage conventions the Kalshi schema joins.
- **916-slice.supervised-production-services** (complete) — systemd supervision and install path for the collector daemon.
