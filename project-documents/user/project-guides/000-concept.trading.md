---
docType: concept
layer: project
phase: 0
phaseName: concept
project: trading
audience: [human, ai]
description: Concept for manta-trading - market data infrastructure platform
dependsOn: []
dateCreated: 20260327
dateUpdated: 20260510
status: in_progress
---

# Manta Trading

## Overview

A CLI-first market data infrastructure platform for acquiring, storing, validating, and serving financial market data across all granularities — daily, minute, and tick-level — from multiple providers. Built for local-first operation with cloud-ready event-sourced architecture. Designed to serve as the data foundation for real-time strategy evaluation, regime detection, and historical replay.

## User-Provided Concept

The project aims to build a data-focused trading platform. The core focus is **data** — acquisition, storage, quality, and accessibility across all granularities including tick-level. Rationale, strategy, and execution are separate concerns for separate systems.

The eventual purpose of the system is to enable real-time strategy evaluation and regime detection, where "real-time" means tick-level. To properly test strategies we must be able to perform replay using historical data. This project is the data aspect of that — the strategy/simulation engine is a separate system that consumes this one (analogous to migratory-behavior vs migratory-world-server).

### Goals
- Acquire and manage market data (tick, minute, and daily OHLCV) from multiple providers
- Support tick-level data storage and serving for real-time strategy evaluation
- Enable historical data replay at tick granularity for backtesting and strategy development
- Support multiple providers: EODHD (primary OHLCV), Finnhub (instrument enrichment), Databento (planned tick data), flat file imports
- Provide a discoverable CLI interface (`mt data`, `mt data daemon`, etc.) with Rich output and `--json` mode
- Status visibility at every level: connection health, data freshness, gap detection, recovery progress
- Event-sourced architecture — starts local, structured for eventual cloud migration
- Structured, configurable logging
- MCP integration in use (postgres MCP tools active; broader MCP server planned)
- Multi-repo agent-to-agent coordination is a future direction

### Constraints
- Solo developer + AI assistants
- EODHD: 100k credits/day, 1000 credits/min burst limit
- Finnhub: free tier, 60 calls/min; used for instrument enrichment only (IPO dates, exchange)
- Data gaps exist from months of inactivity — gap detection and recovery are first-class concerns
- No magic strings — all dispatch via enums, registries, or typed constants
- No UI work until data pipeline is solid. UI will be a separate repository.

### Motivation
Prior attempts at this project were shelved because scale and complexity exceeded what one person could accomplish with AI tools available at the time. The tooling landscape has changed significantly (Context Forge, Squadron, improved AI agents), making this achievable now. The underlying trading ideas are simple but interesting — they've worked but never consistently, and experimenting with large amounts of data is a core goal.

### Domain: manta.trading
### Package name: manta-trading (PyPI, npm reserved)
### Import name: manta_trading

## Refined Concept

### Problem & Motivation

Building a reliable trading system requires a solid data foundation. Without trustworthy, complete, and accessible market data, everything downstream — analysis, backtesting, strategy development, live trading — is unreliable. This is especially true when the goal is tick-level real-time strategy evaluation and regime detection — the data must be complete, fast, and replayable.

The platform is not a trading execution system — it's the data foundation that trading systems consume. This follows the same architectural boundary pattern as the migratory project: manta-trading is to the strategy/simulation engine what migratory-behavior is to migratory-world-server. Each is a separate, focused system with clean service interfaces between them.

### Current Production State (as of 2026-05-10)

- **Daily OHLCV**: S&P 500 (503 symbols) fully loaded, daemon in STEADY_STATE
- **Minute OHLCV**: S&P 500 backfill in progress (~281 of 503 symbols have data), daemon running
- **Instruments**: 33,595 symbols in registry; Finnhub enrichment incomplete (only AAPL has `first_listing_date`)
- **Provider**: EODHD sole production OHLCV provider. AlphaVantage removed entirely (2026-05-05).
- **Continuous aggregates**: 7 caggs (5m, 15m, 1h, 4h, 1w, 1mo, 1q) materialized with refresh policies
- **Schema**: Single TimescaleDB database (`trading` on <db-host>). Migration chain complete through migration 039.

### Target Users

- **Primary**: The developer (solo operator) via CLI for data management, monitoring, and operations
- **Secondary**: AI agents via MCP for automated data access and analysis
- **Tertiary**: Strategy/simulation engine (separate repo, ~300 band) via replay and streaming data interfaces
- **Tertiary**: UI application (separate repo) via API/MCP
- **Future**: Potential PyPI package consumers who need a clean market data abstraction

### Solution Approach

#### System Boundary Model

manta-trading is one component in a larger system. The boundary pattern follows migratory's architecture:

```
┌─────────────────────────────────────────────────────────┐
│                    manta-trading                         │
│              (this project — data layer)                 │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │  Daily   │  │  Minute  │  │   Tick   │  │ Quality│  │
│  │  OHLCV   │  │  OHLCV   │  │   Data   │  │   &    │  │
│  │          │  │          │  │          │  │  Ops   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬───┘  │
│       └──────────────┴──────────────┴──────────────┘     │
│                         │                                │
│              Service Interfaces (IDataService)           │
│              Replay Interface (IReplayProvider)           │
│              Streaming Interface (ITickStream)            │
└─────────────────┬───────────────────┬───────────────────┘
                  │                   │
    ┌─────────────▼──────┐   ┌───────▼──────────────────┐
    │  Strategy/Simulation│   │         UI               │
    │  Engine             │   │    (separate repo)       │
    │  (separate repo,    │   │                          │
    │   ~300 band)        │   │  Consumes data via       │
    │                     │   │  API/MCP                 │
    │  Consumes:          │   └──────────────────────────┘
    │  - Replay data      │
    │  - Live tick stream  │
    │  - Historical ranges │
    └─────────────────────┘
```

#### Capability Areas Within This Project

1. **CLI & Project Foundation** (Initiative 900) — Typer-based CLI with Rich output, TOML config with precedence (CLI > project > user > defaults), structured logging, provider registry with enums. Command surface: `mt data`, `mt data daemon`, `mt data instruments`, `mt data caggs`, `mt data lists`, `mt data ca`, `mt data migrate`, `mt data status`.

2. **Data Storage** (Initiative 100) — TimescaleDB for time-series across all granularities (tick, minute, daily OHLCV), with continuous aggregations (5min, 15min, hourly, 4h, weekly, monthly, quarterly). Compression policies for cost efficiency. Proven at minute/daily level: 13k+ rows/sec writes, <50ms queries, 95% compression. Tick-level storage is new scope (Initiative 200).

3. **Data Acquisition** (Initiative 120/140) — EODHD as sole production OHLCV provider. Finnhub for instrument enrichment (IPO dates) only. Daemon-driven gap-based backfill with `data_gaps` table. STEADY_STATE (bulk EOD) vs BACKFILL (per-symbol) mode selection. Named symbol lists (`mt data lists`). Adjusted-on-read via `adjusted()` function.

4. **Data Quality & Operations** (Initiative 140) — Calendar-aware gap detection. Coverage reporting via `data_gaps`. Data freshness monitoring via `data_status` view. Recovery via `mt data pull --reset`. All surfaced via CLI.

5. **Data Serving** (Initiative 160) — Query APIs for historical data. The interface contracts downstream systems consume. Not yet built beyond CLI `mt data get`.

6. **Event Infrastructure** (Initiative 180) — Event-sourced record of all data operations. Not yet started.

7. **Tick Data** (Initiative 200) — Planned. Databento as provider (DBN file format for historical, streaming for realtime). Initial instruments: CME futures (/EC, /GC). Separate ingestor service (new repo). IPC via shared memory ring buffer (mmap) for realtime feed. Raw DBN files as primary historical store. Separate `tick_gaps` table (sequence-number keyed, not time-window keyed). Futures instrument model (contracts, roll schedules, continuous series) requires its own design — does not fit current `instruments` table.

#### Live Data Flow (Planned — Not Yet Built)

For realtime tick data, shared memory is the stated IPC approach between the ingestor service and consumers:

```
Provider (Databento streaming)
    → Ingestor service (separate repo, low-latency)
        → Shared memory ring buffer (mmap, lock-free)
            → Consumer (engine, manta-trading archiver)
        → TimescaleDB (permanent storage via archiver)
            → Continuous aggregates
```

The ingestor service is a separate repository. manta-trading owns the consumer side of the IPC and the storage/query layer.

#### External Systems (Separate Repos, Not Scoped Here)

- **Strategy/Simulation Engine** (manta-engine, ~300 band) — Consumes manta-trading's data serving and streaming interfaces. Orchestrates replay. Performs real-time strategy evaluation and regime detection.
- **Tick Ingestor** — New repo. Databento WebSocket connection, writes to shared memory ring buffer and TimescaleDB.
- **Broker Integration** — API integration for order execution. Separate repo. Decision deferred.
- **UI** — Separate repository. Consumes data + strategy results via API/MCP.

#### Client Access Patterns

```
CLI user ──→ manta-trading directly     (mt data status, mt data get, mt data pull)
AI agent ──→ manta-trading via MCP      (data queries, status checks)
Engine   ──→ manta-trading via service  (historical pulls, shared memory subscription)
UI       ──→ manta-trading for raw data (quality dashboards, data explorer)
UI       ──→ engine for strategy views  (simulation results, regime state)
```

### Scaling Path

The system is designed for current needs but with known scaling boundaries and upgrade paths.

**Current design point**: ~50-200 ticks/sec total (all symbols), 4-32 tick symbols, ~500 minute symbols, 1-2 consumers.

**Market hours**: US equities have defined trading hours. Futures are nearly 24/7 (23/6). Crypto is 24/7. The system must support always-on ingest for non-equity markets.

#### Data Arrival Rate (ticks/sec total, all symbols combined)

| Scale | Rate | Architecture | Bottleneck |
|-------|------|-------------|------------|
| Current | ~50-200/sec | Python single-process, shared memory, TimescaleDB | None — all components at <1% capacity |
| 10x | ~2k/sec | Same architecture, larger batch writes | None — TimescaleDB proven at 13k+ rows/sec |
| 100x | ~20k/sec | Multi-process Python workers, partitioned by symbol | Python GIL on per-tick processing. Move all aggregation to TimescaleDB. |
| 1000x | ~200k/sec | Rust ingest service, Python for orchestration | Python can't process per-tick at this rate. Rewrite ingest hot path in Rust. |
| Beyond | 1M+/sec | Different system | Kafka/Redpanda, distributed TimescaleDB, kernel bypass. Not this project. |

#### Symbol Count

| Scale | What Changes |
|-------|-------------|
| 4-32 tick, ~500 minute | Single process handles all |
| ~500 tick symbols | Partition processing across workers by symbol hash. No architecture change. |
| 5,000+ tick symbols | Service-per-market. Separate ingest workers per exchange/provider. TimescaleDB space partitioning essential. |

#### Consumer Count

| Scale | What Changes |
|-------|-------------|
| 1-2 | Direct shared memory reads |
| 10 | Multiple consumers off same ring buffer — zero architecture change. |
| 100+ | Consider dedicated message broker (NATS, Kafka) if consumers need topic-based routing. |

#### Processing Complexity

| Level | Scope | Where It Lives |
|-------|-------|---------------|
| Storage aggregation | Tick → 1min → 5min → ... | TimescaleDB continuous aggregates. Manta-trading. |
| Derived streams | VWAP, volume bars, dollar bars | Engine or dedicated workers. NOT manta-trading. |
| Real-time indicators | Moving averages, regime detection | Engine / strategy layer. |
| ML inference | Feature computation, model scoring | Dedicated inference workers. |

### Initial Technical Direction

- **Language**: Python 3.12+
- **Package manager**: uv
- **Build system**: Hatchling (PEP 517/518)
- **Project layout**: `src/manta_trading/` (standard src layout)
- **CLI**: Typer + Rich
- **Database**: PostgreSQL + TimescaleDB (single DB `trading`; tick-level may require separate hot/cold storage)
- **HTTP client**: httpx
- **Config**: pydantic-settings, environment variables
- **Logging**: stdlib logging with structured formatters
- **Testing**: pytest, 1246+ tests passing
- **MCP**: postgres MCP tools active; broader MCP server planned

### Development Approach

- **Methodology**: Iterative slices via Context Forge workflow. Each slice delivers working, tested functionality.
- **Quality**: Tests required for all new code. Integration tests against real DB where appropriate.
- **No magic strings**: Enums and typed constants for all dispatch logic.
- **No silent failures**: Explicit errors, never swallow exceptions or return None where an error occurred.
- **CLI-first verification**: Every feature must be exercisable and visible through the CLI before it's considered done.
