---
docType: architecture
layer: project
component: data-acquisition-futures-tick
project: trading
parent: 001-initiative-plan.trading.md
dependencies:
  - 100-arch.data-storage.md
  - 120-arch.data-acquisition.md
  - 900-arch.foundation-cleanup.md
relatedSlices: []
riskLevel: medium
archIndex: 220
dateCreated: 20260921
dateUpdated: 20260921
status: not_started
---

# Futures Tick Data Acquisition Architecture

## Overview

Initiative 220 brings tick-level market data into trading-data. It is the project's primary research target: daily bars, minute bars, the TimescaleDB storage tier, the quality operations, and the serving API were all built as the proving ground for the patterns this initiative consumes (initiative plan, entry 220). The data mode is **historical** — purchased tick history, acquired as files and projected into a queryable store — because historical data is what strategies are researched and tested against. Realtime streaming is the end-state of data gathering (process journal, 20260824), but it is a separate initiative with a different process form, and nothing here is built for it beyond not precluding it.

**Scope:** One provider, one venue, one asset class, a deliberately small universe, and the trades tier of market data:

- **Provider:** Databento, and only Databento to start. The provider seam is designed so a second tick provider is an addition, not a redesign, but no second provider is planned in this initiative.
- **Venue and instruments:** CME Globex futures (Databento dataset `GLBX.MDP3`). E-mini S&P 500 (`ES`) first; Gold (`GC`) next; a few more later. The data is priced by volume, so the initial purchase is a bounded range of ES, chosen to prove the pipeline rather than to fill an archive.
- **Schema tier:** trades, and trades with the best bid/offer at the moment of each trade (Databento's `tbbo`). Every top-of-book change (`mbp-1`) and the full order book (`mbo`) are out of scope: they multiply cost, storage, and bandwidth by every quote update rather than every trade, and the data-correctness contract already places order-book depth outside the data layer's guarantees. The tier is a per-instrument configuration so a later slice can add a level without touching the acquisition path.
- **Futures identity:** a futures instrument model — contracts, expirations, and continuous series — which the concept explicitly says does not fit the equities `instruments` registry.

Out of scope: realtime and intraday capture (its own initiative, streaming form), order-book depth, equity ticks (data-correctness contract: universe too large), cross-venue consolidation, strategy or replay logic, and tick serving endpoints (initiative 180 defers those until tick storage exists; they follow this initiative, not join it).

**Motivation:** Everything downstream — real-time strategy evaluation, regime detection, replay — needs tick data, and the project has none. The initiative plan's strategy is explicit: prove schema, provider seam, roll logic, storage volume, and query performance against purchased historical data *before* paying for a live subscription. Two things make "prove first" more than caution. The storage tier's own history: minute storage failed on chunk geometry at 7 B rows (journal 20260719, 20260725), and tick carries the same failure modes at one to three orders of magnitude more volume. And the cost model: Databento prices historical data per uncompressed gigabyte by schema, so the schema-tier decision *is* the budget decision, and it must be made from measured sizes, not projections.

## Design Goals

- **Prove the tick tier on purchased data before any subscription** — The first deliverable is a bounded, real ES dataset flowing end to end: purchased, archived, ingested, queried, and cross-checked. Every quantity this initiative depends on — bytes per record per schema, records per session, compressed bytes per row, query latency at the chosen chunk geometry — is measured on that data, and expansion (more range, GC, more instruments) is a configuration change gated on those measurements.

- **Right-sized capture, not maximal capture** — Trades and trade-with-BBO are the tiers. The choice between them is a measured cost comparison for the same instrument and range, made before purchase. Nothing in the design assumes the finer tiers will ever be wanted; nothing in it prevents adding one.

- **Files are the record; the database is the query tier** — Raw Databento files are retained as the primary historical store (concept, Tick Data). The database holds a projection that can be rebuilt from the files at any time. This is what lets the storage schema evolve — a different chunk interval, a different compression layout, a new derived column — without buying the data again.

- **Futures identity is modelled, not improvised** — Contracts, expirations, provider identifiers, and continuous-series rules are first-class, captured alongside the data from the provider's own definitions. No adjusted or stitched series is ever stored as if it were market data.

- **Same operational shapes as every other source** — Bounded pass fired by a timer, `mt-run` front door, `pass_runs` accounting, the `mt data <granularity>` operator surface the data-correctness contract requires (I10), provider registry membership, and complete isolation from the equities and Kalshi pipelines. A tick pass failing, a tick database filling, or a provider outage cannot affect any other source.

## Architectural Principles

- **Acquire and ingest are separate, each idempotent** — Acquisition brings provider files into a local archive and records each in a manifest: what was requested (dataset, schema, symbols, range), what the provider returned (job identity, files, sizes, checksums), what it cost, and when. Ingestion reads the archive and projects it into the database, marking the manifest as it goes. Either step can be re-run; re-acquiring an archived range is a no-op, re-ingesting an ingested file is a no-op, and a database rebuilt from the archive is byte-for-byte the same projection.

- **Cost is a first-class input, never a surprise** — No request that costs money is issued without a preceding estimate. Databento's cost and billable-size queries are free; every acquisition computes the estimate, records it in the manifest, and refuses to proceed past a configured spend ceiling. The provider-side account budget is a second guard, not the first. Bounded initial scope is the PM's decision, made on cost; the pipeline makes the cost visible before each purchase so that decision stays informed.

- **Completeness is answered from the manifest and the raw table** — "What do I have?" for ticks is a join of the manifest (what was purchased and ingested, per instrument and session) with counts from the raw hypertable. It is never answered from a continuous aggregate (journal 20260725, rule 2: a derived object that informs acquisition is a production input, and pausing its refresh silently changes acquisition behaviour). Aggregates over ticks may exist for reads; they never feed the acquisition decision.

- **Chunk geometry is derived from wall-clock span, never from volume** — The archived tick outline's one-hour chunk interval is invalid as written (journal 20260719): over a decade it implies ~87,000 chunks before space partitioning multiplies it, 3.5× the count that made `minute_ohlcv` unusable. The tick hypertable's interval is set explicitly at creation from the table's lifetime ÷ a target chunk count, every aggregate over it sets its own materialization interval explicitly in the same migration (journal 20260725, rule 1), and capacity planning uses measured compressed bytes per row from real data, not compression-ratio projections.

- **Provider records are preserved, not reinterpreted** — Databento's event timestamp, receive timestamp, sequence number, publisher, and instrument identifier are stored as delivered. The natural key is the provider's `(instrument, event time, sequence)`; the project adds nothing to it. Prices arrive as fixed-point integers and stay exact. Tick gaps, if they are ever tracked, are sequence-keyed, not time-window-keyed (concept) — but within a purchased historical range the provider's delivery is complete by contract, so the completeness question is "which sessions were purchased and ingested", not "which sequence numbers are missing".

- **One provider adapter owns the wire format** — Decoding Databento's binary encoding, its symbology, and its delivery modes (batch files and direct range requests) lives in one adapter behind a tick-provider protocol shaped like the existing minute and daily provider protocols. Nothing outside the adapter reads a DBN record. The adapter is the whole surface a second provider would replace.

- **A futures contract is the instrument; a continuous series is a rule** — Storage is keyed by the actual traded contract. Databento's numeric instrument identifier is dataset-scoped and may be reused across time, so contract definitions are captured with the data and the mapping from identifier to contract is resolved through them, not assumed stable. Continuous series (front-by-calendar, front-by-open-interest, front-by-volume) are roll rules applied at read time to produce a view; they are never a stored series. Calendar spreads and other non-outright instruments under the same product are excluded from the universe unless explicitly configured.

- **Sessions, not calendar days** — CME trades nearly around the clock with a daily maintenance break and a Sunday open; a "trading day" spans two calendar dates. Coverage, completeness, and the operator surface are expressed per session. The equities calendar tables are not reused for this; the futures session model is its own.

- **Pass form for historical, streaming form reserved** — Historical acquisition and ingestion are bounded passes under the production model settled 2026-08-23 (`mt` command → `mt-{source}-pass.service` oneshot + `.timer` under `manta-acquisition.slice`, `mt-run` verb, install-script entry). Historical data becomes available a day behind (Databento embargoes historical access at 24 hours), so a daily pass is the natural cadence for keeping a configured universe current. A live subscription is the `Type=simple` case and belongs to the realtime initiative; this initiative's only obligation to it is that the archive, manifest, and storage schema accept records from either delivery path identically.

- **The third source must not create a third pass shape** — Two pass frameworks exist today: the EODHD daemon runner and the Kalshi phase/report contract. Initiative 120 deferred "daemon framework extraction" to the third source, which is this one. The tick passes instantiate one of the existing shapes (the Kalshi phase contract is the newer and less provider-specific of the two); whether extraction into a shared framework is warranted is decided from what the third instance actually needs, and if so it is foundation (9xx) work, not a prerequisite here.

## Current State

Nothing tick-shaped runs today. What exists, in order of usefulness:

- **Provider registry (900, complete)** — `ProviderType.DATABENTO` and a Databento `ProviderProfile` (historical base URL, `MT_DATABENTO_API_KEY` as the key variable, aliases) already exist in `providers/types.py` and `providers/profiles.py`. No client, no dependency on the `databento` package, and no key in any environment yet.
- **Separate tick database setting (100)** — `Settings.tick_db_url` (`MT_TICK_DB_URL`) exists with no consumer outside tests. 100-arch places the tick hypertable on a separate database instance from minute data for volume, chunking, compression, and operational-independence reasons; where that instance lives is undecided (see Technical Considerations).
- **Tick schema outline (slice 105, marked complete)** — 105 designed `tick_events` with a `(instrument_id, timestamp, sequence_number, source)` key, an event-type discriminator for trade and quote, one-hour chunks, and space partitioning by instrument. The SQL it describes is not in the tree: `data/base/tick_schema.py` carries only the `TickEventType` enum, and `test/integration/test_tick_schema_integration.py` reads migration files (`760_create_tick_events_hypertable.sql`) from a `database/migrations` directory that no longer exists. The outline is design input; its chunk interval is already ruled invalid (journal 20260719), and it predates the migration framework's per-track chains (`minute`, `daily`, `kalshi`).
- **Acquisition patterns (120, complete)** — persisted `acquisition_state`, structured events, idempotent writes, the orchestrator's chunk protocols, `pass_runs` with its `PassKind` enum (the explicit add-a-source seam, whose CHECK constraint is rendered from the enum into the minute migration track), and the EODHD daemon runner. The provider protocols are `IMinuteDataProvider` and `IDailyDataProvider`; there is no tick protocol, and concrete provider construction is a hand-written match rather than registry-driven.
- **Kalshi collector (260, complete)** — the most recent new source, with its own pass contract (`PassPhase` → `PhaseReport`/`PassResult` over one run context), its own migration track on the TimescaleDB host, and a hypertable created from day one on measured volume. It shares `pass_runs`, the provider error taxonomy, and the profile registry with 120, and nothing else.
- **Production model (916 and the 2026-08-23 ADR, complete)** — the pass + timer + `mt-run` form, the install script's unit list, the runbook's add-a-source checklist, and the explicit note that Databento historical backfill fits the pass form while live capture would be the first `Type=simple` source. Cross-source arbitration between pass units does not exist; overlap is avoided by choosing timer calendars by hand.
- **Quality and status surfaces (140, 167, 922)** — `data_status`, the coverage aggregates (`COVERAGE_SOURCE_TABLE` maps only `minute_ohlcv` and `daily_ohlcv`), `mt data overview` / `build_overview`, and the freshness guards. All are shaped around minute and daily bars; nothing knows about a tick granularity, and the data-correctness contract lists `mt data tick {status, ingest, daemon, coverage, backfill}` as net-new.
- **Flat-file import (240, not started)** — `ProviderType.FLAT_FILE` and its profile exist; no file-to-database path of any kind exists.
- **Backups (915, 920)** — the backup regime covers the production cluster; a file archive and a second database instance are new footprint it does not yet account for.

Constraints inherited from the storage tier's history are recorded as journal entries this initiative must cite in slice design: 20260719 (chunk intervals from wall-clock span), 20260725 (aggregate materialization intervals set explicitly; derived data must not inform acquisition; refresh horizons), 20260823 (production form), and 20260824 (realtime is streaming-form, never tight polling).

## Envisioned State

A configured **tick universe** — instruments (by product and contract selection rule), schema tier per instrument, and the range each is wanted for — drives two bounded passes and one operator surface.

- **Contract definitions** — For each configured product, the provider's instrument definitions for the wanted range are acquired and stored: contract symbol, provider instrument identifier, expiration, tick size, multiplier, and the identifier's validity window. This is the futures instrument model, and it is populated before any tick data, because tick records are keyed by identifiers only the definitions can resolve.

- **Acquisition pass** — Computes the wanted set (universe × range) minus the manifest, estimates cost and size for the remainder, refuses above the spend ceiling, and otherwise submits batch requests, waits for delivery, downloads the files into the archive, verifies them, and records manifest rows. On a timer, the pass keeps a configured universe current one day behind the market; run manually with a range, it is the backfill. Same command, same state.

- **Ingest pass** — Reads unprocessed archive files, decodes them through the provider adapter, resolves instrument identifiers against definitions, and bulk-loads the tick hypertable on the tick database. Idempotent on the manifest; a partial ingest resumes at the file boundary.

- **Storage** — A `tick` migration track on the tick database instance. One hypertable for the trades tier — trade fields always present, best-bid/offer fields present when the instrument's tier includes them — with chunk interval, compression layout (segment by instrument, order by event time and sequence), and any read-side aggregates all created per the journal rules. The definitions and manifest tables live alongside it. Whether space partitioning by instrument survives the chunk-count arithmetic is a measured decision, not an inherited one.

- **Operator surface** — `mt data tick {status, coverage, backfill, ingest}` per the data-correctness contract, reading manifest and raw counts per session; `mt-run tick` and `mt-run status` rows for the pass units; `PassKind.TICK` in `pass_runs`; the tick source visible in `mt data overview` and, by extension, `/api/v1/overview`. Process status from systemd, data status from persisted state — the two layers the 2026-08-23 ADR requires.

- **Validation loop** — Minute bars derived from ingested ticks are compared with the provider's own inexpensive OHLCV schema for the same instrument and sessions. Agreement proves the decode, the identifier resolution, the session boundaries, and the storage projection together; disagreement localizes the defect. This is the initiative's correctness check, cheap enough to run on every ingest.

**Completeness definitions** (the analogue of 120's caught-up and 260's complete-market definitions): an *instrument-session is complete* when the manifest shows its file acquired and ingested and the raw table's count for that session matches the manifest's record count. The *universe is caught up* when every configured instrument is complete for every session in its wanted range up to the provider's availability edge. Sessions the provider has not yet released are *pending*, never *missing*.

At completion, a small ES (then GC) tick history sits in the archive and the database, every number the realtime initiative and the tick-serving work need has been measured on it, and expanding the universe is a configuration edit followed by a cost estimate.

## Technical Considerations

- **Schema tier is the cost decision** — The three candidate tiers differ by what triggers a record: a trade (`trades`), a trade plus the book state just before it (`tbbo` — same record count as trades, wider records), or every top-of-book change (`mbp-1` — many times the record count for a liquid contract). Slice design must obtain billable size for each tier over the *same* ES range from the provider's free estimation endpoints before the first purchase, and record the numbers; the tier choice, and whether GC follows at the same tier, are made from those figures. Record sizes and per-session volumes are to be verified from Databento's schema documentation and measured, not assumed.

- **Delivery mode** — Databento serves history two ways that yield identical data: a direct range request streamed into the client, and an asynchronous batch job that produces files (split by day by default, zstd-compressed) retained for a limited window and downloadable within it. The file-first principle points at batch for anything that will be archived; direct requests suit small probes and estimates. The retention window and any size limits per mode must be verified during slice design, since a download that misses the window is a repurchase.

- **Availability lag and licensing** — Historical access is embargoed at 24 hours and requires no license; intraday and realtime require a live plan (CME live moved from usage-based to subscription plans in April 2025; the entry plan was announced at $179/month and the pricing page currently lists Standard at $199/month — verify at the time of any decision) plus CME's pass-through license fee, which depends on the subscriber's status. None of that is needed for this initiative; it fixes the historical pass cadence at daily, one session behind, and is the reason realtime is separate. Historical purchases are usage-based per uncompressed gigabyte with no subscription; new accounts carry a small expiring credit that can fund the first measurement.

- **Futures identity and symbology** — Databento resolves symbols four ways: raw contract symbols, dataset-scoped numeric instrument identifiers, parent symbols covering a whole product (including spreads), and continuous symbols (`.c`, `.n`, `.v` — calendar, open-interest, and volume roll rules — with a depth index). Continuous symbols are a *request* convenience that maps to real contracts over time; the data carries the real contract's identifier. The instrument model must therefore store definitions and their validity windows, treat identifier reuse as expected, and derive continuous views from stored rules. Exact roll semantics and the mapping-message behaviour in delivered files are slice-design verification items.

- **Session model** — CME's trading day, maintenance break, Sunday open, and holiday schedule define the session boundaries that coverage, completeness, and the derived-bar cross-check all depend on. The equities calendar in the storage layer does not describe them. A CME session model is required early; whether it is a table, a rule, or provider-sourced status records is a slice decision.

- **Database placement and roles** — `MT_TICK_DB_URL` exists but points nowhere. A separate instance can mean a second database on the production cluster, a second cluster on the production host, or a second host; each has a different answer for memory contention with the minute tier, for backup (915) and least-privilege roles (913), and for the test cluster (917). The decision is the PM's, informed by the first purchase's measured size; the architecture requires only that the tick track never shares a hypertable, chunk geometry, or refresh policy with the minute tier.

- **Ingest throughput and the write path** — Bulk load through `COPY` into a hypertable is the proven path (13k+ rows/s measured on minute data). Tick sessions are far larger than minute days; decode cost (the provider's Rust-backed decoder versus per-record Python), batch sizing, and whether ingest must be resumable *within* a file rather than at file boundaries are to be measured on the first purchase, not designed in advance.

- **Archive footprint and backup** — The file archive is the recoverable source of truth, which argues for backing it up under 915's regime and for treating the database projection as rebuildable rather than backed up at full weight. Archive location, retention, and its place in the backup set are decisions the backup slice's owner must ratify, not defaults this initiative sets alone.

- **Relationship to 240 (flat-file import)** — 240 is a generic import tool for CSV/Parquet/provider exports into the existing acquisition-state universe. 220's archive → manifest → ingest shape is the more general pattern; 240 should instantiate it for other formats rather than 220 borrowing from a tool that does not yet exist. DBN decoding stays inside 220's provider adapter.

- **Relationship to the tick serving surface** — Initiative 180's exclusions defer tick endpoints until tick storage exists. Once it does, the serving question ("does this belong in the API?") applies to ticks and derived bars alike and is answered there, with the futures session model and continuous-series rules as its inputs.

- **Testing strategy** — Per project rules: unit tests over real provider files (small purchased or sample DBN files as fixtures — a fixture in an invented format is a false pass), integration tests against a throwaway tick database per the dedicated test cluster, and passes testable as functions of (manifest, provider results) → (new manifest, writes). The estimate-before-purchase guard is tested with a mocked cost response, never against the live account.

- **Secrets and spend guards** — The API key enters through `MT_DATABENTO_API_KEY` only; it is not yet present in any environment. A provider-side account budget is configured before the first request as the second guard behind the local spend ceiling.

## Anticipated Slices

Exploratory, not a commitment:

- **Databento adapter, cost preflight, and contract definitions** — `databento` dependency, tick-provider protocol, the adapter (decode, symbology, batch and direct delivery), free cost/size estimation surfaced as a CLI command, definitions capture into the futures instrument model, the CME session model.
- **Tick storage track** — the `tick` migration track on the tick database: trades-tier hypertable with explicit chunk geometry and compression, definitions and manifest tables, `PassKind.TICK`, roles per 913.
- **Historical acquisition and ingest passes** — archive and manifest, the acquisition pass (estimate → guard → batch → download → verify), the ingest pass, `mt data tick {status, coverage, backfill, ingest}`, pass units and timer, `mt-run tick`, install-script and runbook entries.
- **First purchase and proof** — a bounded ES range at the chosen tier: measured sizes per tier, bytes per row compressed, chunk geometry validated, query latency, derived-bar cross-check against the provider's OHLCV, and the written go/no-go for tier and for GC. This is the slice the initiative plan's strategy describes.
- **Universe expansion and steady state** — GC and further products as configuration, the daily catch-up pass in production, coverage in `mt data overview`.

Follows this initiative, not part of it: realtime capture (streaming form, own initiative), tick serving endpoints (180 follow-on), and any shared pass-framework extraction (9xx).

## Related Work

- **001-initiative-plan.trading.md** — entry 220 and its dependency note (tick hypertable from 100; daemon patterns, provider seam, and orchestrator core from 120; the deferred daemon-framework extraction lands with the third source).
- **000-concept.trading.md, "Tick Data"** — Databento as provider, DBN files as the primary historical store, sequence-keyed tick gaps, the statement that futures identity needs its own model. (The concept numbers this initiative 200; the initiative plan renumbered it 220 when 200 became event infrastructure.)
- **100-arch.data-storage.md** and **105-slice.tick-event-hypertable-schema.md** — the tick schema outline and the separate-instance decision; superseded on chunk interval by the journal entries below.
- **120-arch.data-acquisition.md** (complete) — provider protocols, acquisition state, orchestrator, and the explicit deferral of framework extraction to this initiative.
- **260-arch.kalshi-event-contract-data.md** (complete) — the most recent new source; its pass contract, migration-track placement, and create-the-hypertable-on-measurement stance are the nearest precedents.
- **916-slice.supervised-production-services** (complete) and **100-production-operations.md**, "Adding a source" — the production form, the add-a-source checklist, and the note that Databento historical fits the pass form while live capture is the streaming case.
- **000-process-journal.md** — 20260719 (chunk intervals from wall-clock span; the tick outline's 1-hour interval is invalid), 20260725 (tick-tier design rules: explicit aggregate intervals, derived data never informs acquisition, refresh horizons), 20260823 (production form ADR, naming Databento), 20260824 (realtime is the end-state; streaming form, never tight polling).
- **reference/data-correctness-architecture.md** — I10 tooling consistency (`mt data tick` surface), the out-of-scope list (equity ticks, order-book depth, cross-venue consolidation).
- **240 (flat-file import, not started)** — shares the archive → ingest shape; sequenced after this initiative.
- **Databento documentation** — schemas and data formats (`trades`, `tbbo`, `mbp-1`, `mbo`, `ohlcv-*`, `definition`), symbology (raw, instrument id, parent, continuous), historical API (`timeseries.get_range`, `batch.submit_job`, `metadata.get_cost`, `metadata.get_billable_size`), metered pricing, live API and CME plan announcement (April 2025). Verified at architecture time only to the level cited here; every figure this document marks "verify" is a slice-design obligation.
