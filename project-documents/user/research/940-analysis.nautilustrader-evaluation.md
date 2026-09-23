---
docType: analysis
project: trading
topic: nautilustrader-evaluation
audience: [human, ai]
description: Whether NautilusTrader overlaps with or should replace trading-data, and where it fits
dateCreated: 20260923
dateUpdated: 20260923
status: complete
---

# NautilusTrader Evaluation

**Question:** How much does NautilusTrader overlap with trading-data, how does it work, and should we migrate to it instead of continuing our own system?

**Verdict:** Do not migrate trading-data. The two systems sit at different layers and overlap only on Databento ingestion. NautilusTrader is, however, a strong candidate for the strategy/simulation engine (manta-engine), which the concept document places in a separate repo.

Facts about NautilusTrader were gathered on 2026-09-23 from its docs, GitHub repo and releases. Sources are listed at the end.

## 1. What NautilusTrader Is

- It is an open-source, event-driven trading engine for research, backtesting and live trading, built by Nautech Systems. The lead maintainer is Chris Sellers.
- **License:** LGPL-3.0. The project calls itself "open-core."
- **Paid tiers:** Pro, Cloud Platform and Institutional. These are waitlist-only with no published pricing. Pro adds execution algorithms (VWAP, POV, Iceberg), a risk engine, a message bus ("Nautilus Nexus") and a dashboard.
- **Community:** about 29.3k GitHub stars, 3.9k forks and 199 contributors. It has existed since 2018 and is actively maintained.
- **Stability:**
  - 1.x was labelled "Beta" for its whole life.
  - v1.231.0 (2026-08-02) is intended to be the last 1.x release. It gets security backports for about 3 months.
  - **v2.0.0 is still in release candidate** (rc5, 2026-09-15). There is no GA date.
  - v1 and v2 APIs are incompatible.
  - Every release carries a substantial "Breaking Changes" section. A formal deprecation policy is promised only after v2 ships.

## 2. How It Works

- **Language split:** the core is Rust. Python is the control plane through PyO3 bindings; v1 used Cython.
- **Kernel:** a single `NautilusKernel` wires together:
  - the MessageBus (pub/sub and request/response)
  - the Cache
  - the DataEngine, ExecutionEngine and RiskEngine
  - the Portfolio
  - a Trader that hosts Strategies, Actors and execution algorithms
- **Threading:** the core is single-threaded. Networking, logging and persistence run on background threads.
- **Parity:** backtest, sandbox and live trading run the same kernel and the same strategy code. This is the project's main selling point.
- **Domain model:**
  - Market data types: `Bar`, `TradeTick`, `QuoteTick`, `OrderBookDelta(s)` and `OrderBookDepth10`, plus mark price, funding rate and instrument status.
  - Instrument classes include Equity, FuturesContract, OptionContract and BinaryOption.
  - Every object carries `ts_event` and `ts_init` as UNIX nanoseconds.
  - Prices are fixed-point, at 64-bit or 128-bit precision.
  - There are 17 bar aggregation methods (time, tick, volume, imbalance, Renko and others).
- **Historical data:** stored in the `ParquetDataCatalog`, which is **Parquet files, not a database**.
  - Layout: `data/{type}/{instrument_id}/{start}_{end}.parquet`, on local disk or object storage.
  - It is queried through DataFusion, with SQL-style filters.
  - Data is loaded through "wranglers" and provider loaders such as `DatabentoDataLoader`.
- **Persistent state:** the Cache can be backed by Redis or PostgreSQL. This only covers recovery of instruments, accounts, orders and positions. The docs state it is "not a complete event archive," and market-data history is not restored.
- **Adapters listed as stable:** AX Exchange, Betfair, Binance, BitMEX, Blockchain (DeFi), Bybit, Coinbase, Databento, Deribit, Derive, dYdX, Hyperliquid, Interactive Brokers, Kraken, Lighter, OKX, Polymarket, Tardis.

## 3. Overlap With trading-data

trading-data is a data foundation. It acquires, stores, checks and serves data. Strategy, backtesting and execution are out of scope by design and belong to manta-engine (`000-concept.trading.md`). NautilusTrader is the opposite: an engine that consumes data.

| Capability | trading-data | NautilusTrader |
|---|---|---|
| EODHD equities (daily and 1-minute data for about 33k symbols) | Yes | No adapter |
| Kalshi catalog, candles, trades, backfill | Yes (initiative 260) | Not shipped: an empty placeholder crate, and a community PR closed 2026-09-17 because the build-out will be maintainer-owned |
| Market-data storage | TimescaleDB with 4.4B minute rows, compression, continuous aggregates | Parquet files only |
| Splits and dividends, adjusted when data is read | Yes | Not supported (issue #3307, open since Dec 2025 with no maintainer response) |
| Instrument registry, point-in-time index membership, trading calendars | Yes | No |
| Gap detection, repair, quota-aware scheduled passes, health | Yes (initiative 140, systemd timers) | Only basic helpers that find missing time ranges; no scheduler |
| Read API for the UI and other clients | FastAPI (initiative 180) | None |
| Databento CME futures | Planned (initiative 220) | **Yes**: historical and live, CME `GLBX.MDP3`, parent (`ES.FUT`) and continuous (`ES.c.0`) symbols |
| Strategy, backtest, order management, risk, broker execution | None, by design | **Core purpose** |

The real overlap is the Databento adapter, which covers what initiative 220 plans to ingest. Its limits:

- The live client does not support bar or MBP-10 subscriptions.
- Statistics and imbalance data cannot be written to the catalog.

## 4. Why Not Migrate trading-data

1. **We would rebuild almost everything.** EODHD, Kalshi, corporate actions, universes, calendars, gap tracking, scheduling and the API all have no NautilusTrader equivalent. Migrating means discarding about 46k LOC of working, tested code and rewriting most of it as custom adapters and tooling on a less suitable storage layer.
2. **We would lose capabilities.** Files replace SQL and continuous aggregates, so there would be no multi-client read API and no operational health surface.
3. **The platform is not stable yet.**
   - 2.0 is in release candidate, and every release has breaking changes.
   - There are open 2.0 bugs in backtest fills and accounting (#5047, #5054).
   - Strategy callbacks silently discard exceptions (#5039). That conflicts directly with our no-silent-failure rule.
4. **It does not cover our equity research needs.** Without split and dividend adjustment, long-horizon equity backtests would get adjusted data from us anyway.

## 5. Where It Does Fit: manta-engine

NautilusTrader provides the parts manta-engine would otherwise build from scratch:

- the event loop and message bus
- order and position state
- fill simulation with order-book awareness
- risk checks
- the same strategy code running in backtest and live
- broker connectivity (Interactive Brokers covers equities, futures and options)

Writing these correctly is expensive, and it is where home-grown engines usually go wrong.

The layering would be:

```
EODHD / Kalshi / Databento
          │
    trading-data  (acquire, store, check, adjust, serve)  ← keep
          │  export: TimescaleDB → ParquetDataCatalog
          ▼
    NautilusTrader  (strategies, backtest, live execution)  ← candidate for manta-engine
```

## 6. Recommendations

1. **Keep trading-data as the source of truth for data.** No change to the current roadmap.
2. **Evaluate NautilusTrader when manta-engine design starts.** Do this before writing our own event loop or order handling.
   - Gate adoption on the v2.0 GA release.
   - Prove it on one ES strategy end to end before committing.
   - Check the silent-exception behaviour (#5039) and the state of the backtest fill bugs at that time.
3. **Design initiative 220 so NautilusTrader can consume its output.** This costs nothing now and keeps a later export step cheap:
   - Store the event timestamp and the receive timestamp at nanosecond precision.
   - Choose instrument identifiers that map cleanly to NautilusTrader IDs (for example `ESZ5.GLBX`).
   - Keep price precision explicit rather than relying on float.

   It does not create a dependency on NautilusTrader.
4. **If adopted, add one export slice** from TimescaleDB to a NautilusTrader `ParquetDataCatalog`, with adjusted equity bars and futures trades/TBBO. trading-data remains the store, and the catalog is a derived artifact.
5. **Disregard NautilusTrader for Kalshi.** Our Kalshi support is ahead of theirs.

## Sources

- Repository: https://github.com/nautechsystems/nautilus_trader
- Roadmap: https://github.com/nautechsystems/nautilus_trader/blob/develop/ROADMAP.md
- v1.231.0 release notes: https://github.com/nautechsystems/nautilus_trader/releases/tag/v1.231.0
- Product site and paid tiers: https://nautilustrader.io/ , https://nautilustrader.io/pro/
- Architecture: https://nautilustrader.io/docs/latest/concepts/architecture
- Data concepts: https://nautilustrader.io/docs/latest/concepts/data
- Cache and persistence: https://nautilustrader.io/docs/latest/concepts/cache/
- Catalog API: https://docs.rs/nautilus-persistence/latest/nautilus_persistence/python/catalog/struct.ParquetDataCatalogV2.html
- Integrations: https://nautilustrader.io/docs/latest/integrations/
- Databento adapter: https://nautilustrader.io/docs/latest/integrations/databento
- Installation (v1/v2 split): https://nautilustrader.io/docs/latest/getting_started/installation/
- Kalshi status: https://nautilustrader.io/prediction/ , https://docs.rs/nautilus-kalshi/latest/nautilus_kalshi/ , https://github.com/nautechsystems/nautilus_trader/pull/5010
- Corporate actions: https://github.com/nautechsystems/nautilus_trader/issues/3307

**Not verified:** Cloud Platform features and pricing; which features require Postgres; the v2 GA date.
