---
docType: initiative-plan
layer: project
project: trading
source: user/project-guides/000-concept.trading.md
dateCreated: 20260327
dateUpdated: 20260816
status: in_progress
---

# Initiative Plan: Trading

## Source
000-concept.trading.md

## Index Convention
This project uses a mixed index strategy:
- **900 band** for foundation/maintenance work (CLI, config, project structure)
- **100-200 range with gap of 20** for data infrastructure initiatives

Rationale: A gap of 20 gives each initiative room for up to 19 slices, which is realistic for initiatives of this complexity. The 900 band separates cross-cutting foundation work from domain-specific initiatives. Data acquisition is split by provider — 120 (AlphaVantage/EODHD equities), 220 (Databento futures tick) — so each initiative owns a coherent vertical (protocol impl + daemon/tool + provider-specific concerns) rather than cramming all providers into 120. This leaves the 260-800 range open for future initiatives (analysis engine, strategy framework, etc.) as described in the concept's system boundary model.

## Initiatives

1. [ ] **(900) Foundation & Cleanup** — CLI framework (Typer + Rich, command name `mt`), TOML config with precedence (CLI > project > user > defaults), structured logging, `src/` layout migration, deprecated code cleanup, provider registry with enums. Modeled on Squadron patterns. This is the maintenance/refactor band covering cross-cutting project infrastructure. Dependencies: None (do first). Status: in_progress

2. [x] **(100) Data Storage** — TimescaleDB layer cleanup and integration. Tick-level schema design (event model: trade, quote, BBO). Continuous aggregates across all granularities (tick → 1s → 1min → 5min → 15min → 1hr → daily). Compression and retention policies. Existing minute/daily storage proven (13k+ rows/sec writes, 95% compression), needs integration into new architecture. Instrument registry and trading calendars. Dependencies: [900]. Status: not_started

3. [x] **(120) Data Acquisition — AlphaVantage (Equities)** — Multi-provider framework scaffolding plus the AlphaVantage vertical. Protocol interfaces per granularity (`ITickDataProvider`, `IMinuteDataProvider`, `IDailyDataProvider`). Provider registry (enum-keyed, no string dispatch). Daily and minute acquisition daemons against AlphaVantage. Rate limiting per provider. Idempotent ingestion (staging → validate → upsert). Resumable orchestrator patterns that later providers reuse. DataBento and flat file import are carried in separate initiatives (220, 240). Dependencies: [100]. Status: in_progress

4. [ ] **(140) Data Quality & Operations** — Calendar-aware gap detection across all granularities (distinguish holidays from real gaps). Coverage reporting. Data freshness monitoring. Cross-validation (tick-derived minutes vs provider minutes, minute aggregation vs daily OHLC). Recovery orchestration for gaps. All surfaced via CLI (`mt status`, `mt data quality`). Dependencies: [120]. Status: not_started

5. [x] **(180) Data Serving API** — HTTP API exposing the existing data access layer to external consumers (primarily trading-ui, a TypeScript/React application that cannot import Python). The database stores unadjusted prices; adjustment logic lives in Python; the API applies adjustment before returning data, making it the only viable path for non-Python consumers. Design principle: thin wrapper — no business logic in the API layer. Endpoints: `GET /api/v1/bars/{symbol}` (OHLCV, minute or daily granularity, optional msgpack format), `GET /api/v1/symbols` (symbol list with metadata and prefix search), `GET /api/v1/symbols/{symbol}` (detail with available data ranges per granularity), `GET /api/v1/health`. Framework: FastAPI + Uvicorn, orjson serialization, permissive CORS for local network. CLI surface: `mt serve --host --port --reload`. Estimated 3–4 slices: skeleton + health → bars endpoint (unblocks UI) → symbols endpoints → polish (msgpack, gaps endpoint, error handling). Architecture: [180-arch.data-serving.md](../architecture/180-arch.data-serving.md). Dependencies: [100]. Status: not_started Note: concurrent daemon + API operation (running both processes simultaneously) should be assessed during slicing — separate terminals may suffice, or a supervised launcher slice may be warranted.

7. [ ] **(200) Event Infrastructure** — Event-sourced operational audit trail. Ingestion tracking (`ingest_runs` table: timestamp, symbol range, row counts, data hash). Data lineage (provider version, transform version, adjustment policy). Local storage initially, structured for eventual cloud migration. Dependencies: [900]. Status: not_started

8. [ ] **(220) Data Acquisition — Futures Tick (Primary Focus)** — Tick-level acquisition for futures instruments. **This is the project's primary research target**; equities (initiative 120) is the cheaper-to-source proving ground for the pipeline patterns this initiative consumes.

    Strategy: prove the pipeline against **purchased historical tick data** (cheap one-off — Databento single-contract historical ~$10-30, CME Datamine ~$50-200, or free-tier Dukascopy forex ticks for code-path exercise) before committing to the **$179/mo Databento US-futures live feed**. The historical buy validates schema, provider seam, continuation/roll logic, storage volume, and query performance against real data; only then do we sign up for the live feed.

    Scope:
    - **Schema and storage** — tick hypertable design (`(symbol, time_ns, price, size, side, exchange, conditions)`), sub-second time precision, TimescaleDB compression tuning (~20× expected on tick data; one E-mini contract is ~1B ticks/year so storage discipline matters from day one). Lands the schema work that initiative 100 left abstract.
    - **`ITickDataProvider` seam** — protocol modeled on `IMinuteDataProvider`. First impl is whatever historical source we buy (likely `DatabentoHistoricalTickProvider`); live impl (`DatabentoLiveTickProvider` or equivalent) follows once historical proves out.
    - **Continuous-contract logic** — ESM26 → ESU26 rolls. Both timestamp-based (last-trade-date) and volume-based (open-interest crossover) rolls supported as configurable strategies; user picks per backtest.
    - **`contract_specs` table** — futures equivalent of equity instrument metadata: tick size, point value, expiry, last-trade-date, first-notice-date per contract. Required for correct PnL math.
    - **Tick acquisition daemon** — extracted shared daemon framework from initiatives 120's daily and minute daemons (the deferred extraction lands here since a third daemon now exists).
    - **Real-time gap** — equities tolerate 15-min delayed feeds (EODHD); tick-driven futures strategies do not. The live-feed cutover is gated on (a) historical pipeline proven against purchased data, (b) backups and ops story sufficient for the volume, (c) actual strategy ready to consume the feed (otherwise we're paying $179/mo for nothing).

    Out of initial scope: equities tick data (separate problem, far larger universe), options ticks, multi-exchange consolidation, market-microstructure analytics. Dependencies: [100, 120]. Status: not_started

9. [ ] **(240) Data Acquisition — Flat File Import** — One-shot import tool for bulk historical data (CSV, Parquet, provider-specific export formats). CLI-invoked, not a daemon. Reuses 120's orchestrator core, state tracking, and idempotent-write patterns so imports participate in the same `acquisition_state` universe as live acquisition. Supports rehydrating archived minute data (e.g. the irreplaceable .95/.144 historical rows) and seeding from third-party dumps. Dependencies: [100, 120]. Status: not_started

10. [ ] **(260) Kalshi Event-Contract Data** — Continuous collection of Kalshi prediction-market data via their public REST API (`trade-api/v2`; market data requires no authentication). Relational catalog following Kalshi's own hierarchy: series → events → markets, with settlement outcomes captured on close. Collected surfaces: catalog sync (market lifecycle and settlement), candlesticks, and public trades; orderbook snapshots optional/later. Value is time-sensitive: Kalshi is migrating settled markets, old trades, and candlesticks behind `/historical/*` endpoints with a cutoff timestamp whose retention policy is theirs to change, and orderbook depth is live-only — data not collected now may be unobtainable later. Runs as a low-priority async collector (polling daemon on the 120 daemon/orchestrator patterns; websocket exists if ever needed) so it accumulates data while higher-priority initiatives (e.g. 220) proceed. Simpler than minute OHLCV: modest volumes, plain relational tables (hypertable only if trade/candle volume warrants), tiered rate limits are generous at the public tier. Dependencies: [900] (patterns from 120 reused, not blocking). Status: not_started

## Cross-Initiative Dependencies

- **100 depends on 900**: Needs CLI framework, TOML config, structured logging, and `src/` layout in place before building the storage layer.
- **120 depends on 100**: Provider interfaces write to storage; needs TimescaleDB schemas, instrument registry, and storage patterns established.
- **140 depends on 120**: Gap detection and quality validation require ingested data to operate on. Calendar-aware checks need the acquisition pipeline functional.
- **180 depends on 100**: HTTP API wraps the existing data access layer; needs TimescaleDB schemas and storage patterns in place. No dependency on 120/140 — the API serves whatever data exists.
- **200 depends on 900**: Event Infrastructure needs config and logging foundation. Can proceed in parallel with 100-160 once foundation is in place.
- **220 depends on 100, 120**: Tick hypertable (100) + daemon patterns, provider protocol scaffolding, and orchestrator core established in 120. The daemon framework extraction deferred from 120 lands here as its first slice, since a third daemon now exists.
- **240 depends on 100, 120**: Reuses 120's orchestrator core, `acquisition_state` tracking, and idempotent-write patterns so imports share the live-acquisition state universe. Writes to storage established in 100.

## Dependency Graph

```
900 Foundation & Cleanup
 ├── 100 Data Storage
 │    ├── 120 Data Acquisition — AlphaVantage
 │    │    ├── 140 Data Quality & Operations
 │    │    ├── 220 Data Acquisition — Futures Tick (primary focus)
 │    │    └── 240 Data Acquisition — Flat File Import
 │    └── 180 Data Serving API
 ├── 200 Event Infrastructure
 └── 260 Kalshi Event-Contract Data
```

## Recommended Sequencing

1. **900** — Foundation (prerequisite for all others)
2. **100** — Data Storage (prerequisite for 120 and 160)
3. **120 + 200** — AlphaVantage acquisition and Event Infrastructure (parallel, different dependency chains)
4. **140 + 180** — Data Quality and Data Serving API (parallel; 180 depends only on 100 and can proceed as soon as storage schemas are stable)
5. **220** — Futures tick acquisition (the project's primary research target). Start with purchased historical-tick data to prove schema, provider seam, roll logic, and storage volume before signing up for a $179/mo live feed. Can begin once the equities pipeline (120 → 140 → 160) is solid enough to validate the patterns 220 reuses.
6. **240** — Flat file import, sequenced after 220 or in parallel depending on operational priority (it is a smaller, self-contained tool and can be slotted opportunistically)
7. **260** — Kalshi collector, started as early as practical and run async at low priority alongside 220 — its value is the data accumulated while other work proceeds

## Notes

- Indices are tentative and may be reassigned as initiatives are added or reorganized.
- New initiatives discovered during development are added here with the next available base index.
- Check off initiatives as their architecture documents and slice plans are complete.
- The 240-899 range is reserved for future initiatives (strategy engine integration, analysis framework, etc.) as the project grows beyond the data layer.
- Architecture documents will be created per-initiative during Phase 2. The archived architecture document (`050-arch.data-storage-and-acquisition.md`) contains excellent prior design work that will inform initiatives 100-180.
- Tier 2 (Analysis & Strategy) and Tier 3 (Application & UX) from the existing architecture document are out of scope for this project — they will live in separate repositories per the concept's system boundary model.
