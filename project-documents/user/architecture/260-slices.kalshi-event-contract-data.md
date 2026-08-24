---
docType: slice-plan
parent: user/architecture/260-arch.kalshi-event-contract-data.md
project: trading
dateCreated: 20260824
dateUpdated: 20260824
status: not_started
---

# Slice Plan: Kalshi Event-Contract Data

## Parent Document

260-arch.kalshi-event-contract-data.md — Continuous collection of Kalshi prediction-market data: relational catalog (series → events → markets) with settlement capture, plus candlesticks and public trades, run as a bounded pass under the 916 supervised-production model.

## Foundation Work

1. [ ] **(261) Kalshi Provider Foundation** — Register the provider: `ProviderType.KALSHI`, `ProviderProfile` with `AuthType.NONE`. Build the `trade-api/v2` API client: httpx-based, configurable rate limiter (shared-budget discipline per existing providers), cursor pagination, error taxonomy distinguishing transient from permanent failures, recorded real-response fixtures for every consumed endpoint. Verify Kalshi's current endpoint surface and the `/historical/*` cutoff behavior against published documentation (arch requires discovery, not assumption) and record findings in the slice design. Create the `kalshi` migration track on the TimescaleDB host with catalog tables (series, events, markets with lifecycle status and settlement fields) and collection-state tables (per-surface watermarks, awaiting-settlement set). Verifiable via client unit tests against fixtures and an applied/rolled-back migration on a throwaway database. Effort: 3/5

## Feature Slices

2. [ ] **(262) Catalog Sync with Settlement Capture** — One-shot CLI command that syncs the catalog: discovers/refreshes series, events, and markets; tracks lifecycle transitions; maintains the awaiting-settlement set from close events and captures outcomes until settlement is recorded. Implements the incremental-vs-full sync strategy (the main algorithmic decision deferred by the arch; the binding constraint: no market may reach settlement unobserved). Idempotent upserts on ticker, persisted watermarks, structured events per the 120 pattern. First cut of `mt data kalshi status`: catalog watermarks, awaiting-settlement set with ages, lifecycle counts. Dependencies: [261]. Effort: 4/5

3. [ ] **(263) Collection Pass and Supervised Install** — The bounded pass command (`mt data kalshi pass` or as finalized in design) composing collection phases in order — catalog only at this point — exiting nonzero on failure. Production wiring per 916: `mt-kalshi-pass.service` (oneshot, `Slice=manta-acquisition.slice`) + `.timer`, `install-production.sh` integration, `mt-run` wrapper verb (`sudo mt-run kalshi`, `mt-run status` row, `mt-run follow kalshi`), runbook section. Timer interval chosen for short steady-state passes; first-run catch-up simply runs long once. From this slice on, catalog and settlement data accumulate in production unattended — the initiative's time-sensitive value starts here. Dependencies: [262]. Effort: 2/5

4. [ ] **(264) Candlestick Collection** — Candle phase added to the pass: per-market watermarked acquisition from market open through close, against the post-sync market set. Resolves the period-selection trade-off (finest-and-derive vs multiple periods); schema keys candles on `(market_ticker, period, timestamp)` with conflict-ignore. Extends `mt data kalshi status` with candle coverage: markets whose candles fall short of close, per the completeness definition. Dependencies: [263]. Effort: 3/5

5. [ ] **(265) Public Trades Collection** — Trades phase added to the pass: cursor-driven trade tape, idempotent on Kalshi's trade id. Extends status with trade-cursor watermark and per-market tape completeness through close. Independent of 264 — the two phases can land in either order. Dependencies: [263]. Effort: 2/5

## Integration Work

6. [ ] **(266) Historical Backfill** — Conditional on findings from 261's endpoint verification: if recoverable data exists behind `/historical/*` (settled markets, old candles, old trades predating collector start), a one-time operator-run drain into the same tables, idempotent against live-collected rows, recording ranges confirmed unrecoverable so status can report known-lost data honestly. Runs from the CLI, not the timer. Dependencies: [264, 265]. Effort: 3/5

## Notes

- Sequencing is driven by the initiative's time-sensitivity: 261 → 262 → 263 is the shortest path to unattended production accumulation of the catalog and settlement record. Candles (264) and trades (265) then extend the running pass; each addition redeploys via the normal pinned-ref update.
- 264 and 265 are mutually independent. If prioritizing between them, trades are the surface Kalshi is actively migrating behind the historical cutoff.
- Decisions the architecture explicitly defers to slice design: incremental-sync strategy and cadence (262), candle period selection (264), awaiting-settlement stuck threshold (262 — the age visibility itself is required, only the threshold is open), timer interval (263), exact CLI command names (262/263).
- No hypertable is created in this plan. Tables are plain relational on the TimescaleDB host; promotion is a future decision after observed volume, kept in-place-cheap by host placement (arch: Volume and storage posture).
- The cross-source arbitration gap (916 deferred, names Kalshi as trigger) is inherited, not addressed here — it is a 916 follow-on slice outside this initiative. Mitigations in this plan are structural: bounded pass, sparse timer, conservative rate budget.
- Orderbook snapshots are deliberately absent: live-only data arguing for capture, but websocket-based streaming form and unresolved cadence/storage cost make it separately-scoped future work, not a slice here.

## Future Work

1. [ ] **Orderbook Snapshots** — Websocket-based streaming capture of orderbook depth for selected markets; `Type=simple` unit per the production-form ADR (journal 20260823), separate from the pass. Requires its own scoping decision on market selection, cadence, and storage. Dependencies: [262].

2. [ ] **Authenticated Tier Adoption** — If public-tier rate limits become binding (e.g. during historical backfill or catalog growth), evaluate authenticated access for higher limits. The architecture requires the collector to keep working without credentials. Dependencies: [263].

3. [ ] **Hypertable Promotion** — If observed trade/candle volume warrants, promote those tables to hypertables in place on the TimescaleDB host, with chunk intervals sized against wall-clock span per journal entry 20260719. Dependencies: [264, 265].
