---
docType: slice-plan
parent: user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
project: trading
dateCreated: 20260923
dateUpdated: 20260925
status: in_progress
---

# Slice Plan: Futures Tick Data Acquisition

## Parent Document

220-arch.data-acquisition-futures-tick-primary-focus.md — Historical tick data for CME futures from Databento (ES first, then GC), trades and trades-with-BBO tiers. Purchased as files into an archive tracked by a manifest, then projected into a tick hypertable on its own database. The initiative includes a futures instrument model with read-time roll methods, and parity with the minute tier on every operator, API, and backup surface. It is proved on a bounded first purchase before the universe expands.

## Foundation Work

1. [ ] **(220) Databento Adapter and Cost Preflight** — Adds the `databento` dependency, the tick-provider protocol (shaped like `IMinuteDataProvider`/`IDailyDataProvider`), and the one adapter that owns the wire format. The adapter covers DBN decode, symbology (raw, instrument id, parent, continuous), and both delivery modes: batch job and direct range request. Its execution model is a synchronous *unit → bounded record batches* iterator that runs off the event loop. The batch bound is one named constant, set conservatively until the proof measures it. The threads-versus-processes question is answered here by measuring whether the provider's decoder releases the interpreter lock. The first tick CLI verb is a cost and size preflight. It calls only the free metadata endpoints (`get_cost`, `get_billable_size`, `get_record_count`, dataset range and condition) and reports per tier (`trades`, `tbbo`, `mbp-1`) for the same instrument and range. That comparison is the input to the tier decision. The spend-ceiling setting, `MT_TICK_SPEND_CEILING_USD`, is added to `Settings` with no default. The slice-design verification items are resolved here: the batch retention window, size limits per delivery mode, the 24-hour embargo, the record-count query, and how symbol-mapping records appear in delivered files. Unit tests run over real DBN files. The provider's published sample files are the candidate (verify at design), because this slice may not buy anything. Mapping rows go in the data-correctness contract. Dependencies: [900 provider registry, complete]. Risk: Medium. Effort: 3/5

2. [ ] **(221) CME Session Model and Data-Correctness Amendment** — Adds the futures session model as a second calendar behind the I4 session surface. The pieces are:
   - A CME `calendar_id` row in `trading_calendars`, verified per product (ES and GC are expected to share one).
   - The open-after-close rule in `populate_trading_sessions`, applied to every calendar. `NYSE` rows are unchanged, and a test proves it.
   - CME holidays, early closes, and irregular sessions seeded as `trading_holidays` rows by a minute-track migration, from CME's public schedule.
   - Sessions populated backward over the wanted range, and forward through the existing per-calendar extension (`TRADING_SESSIONS_EXTENSION_YEARS`, `mt data extend --calendar`, and the automatic extension that `mt data status` runs).
   - A `TradingCalendar` method that returns the session containing a timestamp. A timestamp outside the populated range fails explicitly.

   This is the first slice to touch a governed surface, so it also writes the frame of the contract amendment: the tick invariants, the I7 exception with its reason, the vocabulary rules (manifest-based completeness, and where `granularity = 'tick'` may appear), and sequence-gap detection moved to the realtime initiative. The slice needs no purchase and no tick database. Dependencies: none within this plan. Risk: Medium. Effort: 3/5

3. [ ] **(222) Tick Storage Track** — Creates the `tick` migration track on the tick database. Its tables are:
   - The trades-tier hypertable, with trade fields always present and nullable BBO fields. Its chunk interval is set explicitly and conservatively from wall-clock span (journal 20260719). Physical grouping by instrument waits for the proof.
   - Definitions (the futures instrument model, with identifier validity windows).
   - The manifest of archive units: state machine, delivery-mode discriminator, tier, download deadline, and a link to the unit it repurchases.
   - The ingest ledger: one row per instrument and session, holding records loaded, first and last event time, traded volume, and source unit.

   The slice renders its tier CHECK constraints from `STORED_TIERS`, a subset of the `TickSchema` enum that 220 defines (the preflight needs the vocabulary first), and adds tick roles per 913. It removes slice 105's leftovers: the `TickEventType` enum and its unit test, and `test_tick_schema_integration.py`, which turns red on the day `MT_TICK_DB_URL` is set. Storage follows the NautilusTrader-compatibility recommendation (940 analysis): event and receive timestamps at nanosecond precision, fixed-point prices kept exact, and contract identifiers that map cleanly to venue-suffixed IDs. No data is written; this slice only creates tables. Dependencies: [923 Multi-Database Migration and Credential Plumbing — hard gate, no fallback], [220]. Risk: Medium. Effort: 3/5

## Feature Slices (in implementation order)

4. [ ] **(223) Historical Acquisition Pass** — The acquisition pass follows the Kalshi phase/report/result contract, copied into the tick package and diffed against the Kalshi original. Its phases:
   - Reconcile in-flight units first. Deliverable units download earliest deadline first. A unit whose deadline passes moves to *failed* with the reason *retention expired*, and its repurchase is a new unit that names the expired one.
   - Capture the availability edge from the provider's free dataset metadata.
   - Compute the wanted set (universe × range) minus the manifest, in calendar-day UTC units.
   - Estimate, then apply the spend-ceiling guard. The pass refuses to purchase when the ceiling is absent.
   - Submit, poll within the pass's wait budget, download, and verify checksums and sizes.

   Contract definitions are acquired through this same path as `definition`-schema units. They are billable, and the architecture allows no exempt requests. This moves definitions capture here from the architecture's storage-track sketch. The slice also adds:
   - A run-context preflight that refuses to run while tick migrations are pending.
   - A minute-track migration adding `PassKind.TICK` and re-rendering the `pass_runs` CHECK constraint.
   - The `PassKind.TICK` branch in `schedule_for`, staggered against the minute pass's firing times.
   - A configured tick universe (products or contracts, tier per instrument, wanted range).

   The pass runs manually from the CLI. The spend-ceiling guard is tested with mocked cost responses and never against the live account. Dependencies: [222]. Risk: High. Effort: 4/5

5. [ ] **(224) Ingest Pass and Proof Parity** — The ingest pass reads archive units that have not been ingested yet. It projects definition units first, then trade units. It decodes through the adapter's bounded iterator, resolves identifiers against stored definitions, and assigns each record a session through the 221 lookup. Each worker writes its own unit with `COPY` over a dedicated connection it owns. Only progress returns to the loop.

   The ingest ledger gets a row for every configured instrument whose definition is valid in a session the unit's range covers, including zero-record rows. Three checks gate the commit of a unit's ledger rows and its *ingested* transition:
   - *Counts:* the provider's record count equals the records decoded, which equals the rows in the raw table.
   - *Resolution:* every record resolves to a contract.
   - *Session boundaries:* no trade falls in the daily break, on a closed day, or outside the populated calendar range.

   A unit that fails a check moves to *failed* with the check named.

   The slice also delivers proof parity on the tick CLI:
   - status per product and contract: sessions complete, pending, in flight, missing, failed, and edge unknown.
   - coverage per session.
   - completeness under the architecture's definitions.

   These read only the manifest, the ledger, and raw counts, never an aggregate. Dependencies: [221, 223]. Risk: High. Effort: 4/5

6. [ ] **(225) Tick Pass Production Wiring** — Deploys the passes in the 916 production form. It adds:
   - The pass unit and timer under `manta-acquisition.slice`.
   - `mt-run tick`, `mt-run follow tick`, and the `mt-run status` row.
   - The install-script entry.
   - `MT_TICK_DB_URL`, `MT_DATABENTO_API_KEY`, and `MT_TICK_SPEND_CEILING_USD` in the service environment files of every process with a tick duty.
   - The tick archive directory on the host.
   - The runbook's add-a-source entries.

   It also adds the second connection pool from `MT_TICK_DB_URL` to the health check, as the first composing surface. The check gains a retention-deadline finding for any in-flight unit whose deadline falls before the next scheduled firing. When the setting is absent, health fails at startup. When the tick database is unreachable, health reports "unreachable" rather than omitting the line. The minute, daily, and Kalshi passes never read the tick setting. Cutover is a script per the PM-host-step convention. Dependencies: [224]. Risk: Medium. Effort: 3/5

7. [ ] **(226) First Purchase and Proof** — A bounded ES range at the tier chosen from 220's per-tier estimates, bought through the production pass. It measures:
   - bytes per record by tier, and compressed bytes per row;
   - ingest rate, and the cadence check: a day's sessions must ingest well inside the daily pass interval;
   - typical batch-job duration and poll interval;
   - query latency at the chosen chunk interval;
   - whether intra-unit checkpoints are needed;
   - the decoded-batch memory budget, which sets the batch-size constant;
   - contention: the tick ingest running concurrently with the minute pass (the minute pass's duration, host I/O, memory, and write throughput against the solo baseline).

   Mapping-record completeness is a pass/fail criterion. Either every tick is attributable to its contract, or the raw-symbol fallback is adopted. The ingest checks pass on every unit. The chunk interval is validated. The physical-grouping decision (space partitioning and compression layout) is made from the numbers and applied by a tick-track migration. If the schema is invalidated, the remedy is a rebuild from the archive. The output is a written go/no-go on tier and on GC. Dependencies: [225]. Risk: High. Effort: 4/5

8. [ ] **(227) Backup Coverage for Tick Archive and Database** — Adds the archive to the 915/920 backup set as data. The database policy (full base backup, WAL-only, or rebuild from the archive on restore) is chosen from the proof's measured rebuild cost. The 913 role set and the backup tooling are extended to the tick instance, not copied by hand. The restore drill covers both the archive and the database. Retention, exclusions, and placement are recorded in the backup runbook. Dependencies: [226]. Risk: Medium. Effort: 3/5

9. [ ] **(228) Active Contract and Roll Methods** — Adds the roll-method vocabulary: calendar with an optional days-before-expiry offset, and volume. Open interest waits until statistics are bought. The project default is defined once.

   Active-contract resolution reads stored definitions and ledger volume. It reports the latest session its inputs cover, and it can be re-derived later from stored state. Roll-day handling has a stated default. The continuous series is a read-time view and is never stored. Status lines name the active contract, the rule that chose it, its expiration, and the next roll.

   The vocabulary matches Databento's continuous-symbol conventions. A test resolves the same contract both ways, over a roll in the purchased range. Dependencies: [226]. Risk: Medium. Effort: 3/5

10. [ ] **(229) Operator Surface Full Parity** — Covers the verbs the proof did not need:
    - `get` for ticks and for tick-derived bars, by contract or by product plus roll method;
    - accounting lines;
    - debug reads of the manifest and definitions;
    - a tick line in `mt data overview`, reachable as `mt-run data overview`.

    Every composed surface reports "unreachable" rather than omitting the line. The slice also writes the runbook's operator procedures and adds contract mapping rows for these surfaces. The same questions get answers with the same defaults as the minute tier. Dependencies: [228]. Risk: Low. Effort: 3/5

11. [ ] **(230) API Surface Coverage — Futures Tick** — Applies the namespace decision (see Notes). The endpoints serve:
    - ticks and tick-derived bars by contract or by product plus roll method;
    - the contract catalog, with definitions, expirations, and the active contract per rule;
    - tick coverage and freshness in `/api/v1/status` and `/api/v1/overview`.

    `create_app` gains a `tick_db_url` seam. `mt-serve` starts without the tick database, and tick endpoints return `503` while the pool is down. `/api/v1/health` gains a tick-database field beside `db` and always returns 200.

    Each endpoint answers the standing question "does this belong in the API?" Every route publishes a response model, avoiding the `response_class=Response` trap. Both reference documents are extended under the 190 gate, and `openapi.json` is regenerated. Closes 180's tick exclusion. Dependencies: [229]. Risk: Medium. Effort: 3/5

## Integration Work

12. [ ] **(231) Universe Expansion — GC** — Adds GC as a configuration edit, with a cost estimate that includes roll inputs for the range. It is bought and ingested at the tier set by the 226 go/no-go, and it moves the universe to the daily catch-up pass in production. The session model is verified for GC, and the ingest checks pass on every GC unit. Status, overview, and the API show GC with no code change. Verification uses a roll inside the purchased range and a manually fired pass. It does not wait for a future roll or a future timer firing. Dependencies: [230], the 226 go/no-go. Risk: Low. Effort: 2/5

13. [ ] **(232) Cross-Source Acquisition Arbitration** — Sets `IOWeight`, `CPUWeight`, and `MemoryMax` on `manta-acquisition.slice` from the proof's contention numbers. It then chooses among the 900 plan's three mechanisms: a resource-class lock the passes take, a scheduler inside `mt`, or keeping calendar stagger as it is. The contention measurement is repeated to show the chosen setting works. This closes 900 Future Work item 4. Dependencies: [226]. Risk: Medium. Effort: 2/5

## Notes

- **923 is the one external gate.** 220 and 221 need no tick database and proceed in parallel with 923. 222 onward waits for it. Scheduling 923 is the PM's decision.
- **Differences from the architecture's Anticipated Slices** (which the architecture labels exploratory):
  - The first sketched slice (adapter + session model + contract frame) is split into 220 and 221. The two are independent and share no code.
  - Contract-definitions capture moves from the storage track (222) to the acquisition pass (223). Definitions are billable, and the architecture's cost principle allows no request outside the estimate → guard → manifest path. That path is built in 223. The sequencing rule still holds: 222 creates the table and 223 writes it.
  - The sketched passes slice is split into acquisition (223), ingest with proof parity (224), and production wiring (225). Each leaves a working system. After 223 a manually run pass fills the archive. After 224 the database fills and status answers. After 225 it runs under systemd.
  - The steady-state slice is split into GC expansion (231) and arbitration (232). They depend on different inputs and change different components.
  - Backup (227) comes right after the proof, not after the API. The architecture says "backup is part of done". The database policy needs only the proof's rebuild-cost number, so there is no reason to leave real purchased data unprotected across three more slices.
- **Architecture statements superseded by 220's slice design** (the slice design is authoritative for 222 onward):
  - The decode batch bound is a byte budget (`TICK_DECODE_BATCH_BYTES`) with the record count derived per schema, not "a record count, one named constant" — a count cannot bound memory across 48-byte trades and 520-byte definitions.
  - Delivered historical files carry no symbol-mapping records. The mappings live in the DBN file's metadata header (`mappings`, `partial`, `not_found`); in-stream mapping records are a live-API feature only. 224 and 226 read the header.
- **No billable request before 223.** 220's preflight uses only free metadata endpoints, and its test fixtures must be free real DBN files. The first real purchase happens only after the PM sets `MT_TICK_SPEND_CEILING_USD`.
- **The CLI and API surface decision is made once, at 220 design.** 220 ships the first tick CLI verb, and the architecture requires the CLI (subgroup or granularity switches) and the API (`/api/v1/futures/*` or tick as a granularity on bars) to make one decision together.
- **Standing obligations on every 220 slice:** diff the tick pass contract against the Kalshi original, as a named task in the slice's task file (from 223 on). Add the slice's rows to the data-correctness contract's slice-mapping table. Answer "does this belong in the API?" for any new surface.
- **Proof range suggestion (PM decision):** choose the 226 ES range to cover a quarterly roll. 228 and 231 can then verify roll resolution on data already bought, with no wait for a future expiry.
- **Architecture verification items resolved at slice design:** batch retention window and size limits, embargo, record-count query, mapping-record behaviour, and decoder lock release (220). CME schedule reach-back and per-product hours (221). Everything else the architecture marks as measured is measured in 226.
- **Unchanged decisions carried from the architecture:** `pass_runs` and the CME calendar live on the production database's minute track, and they are the only exceptions to tick isolation. Completeness is never read from an aggregate. No stored continuous or back-adjusted series. Pass form only; streaming belongs to the realtime initiative.

## Future Work

1. [ ] **NautilusTrader Catalog Export** — One export slice from the tick and equities stores to a NautilusTrader `ParquetDataCatalog`, if manta-engine adopts NautilusTrader (940 analysis, recommendation 4). The catalog is a derived artifact, and trading-data stays the store. Dependencies: [230].
2. [ ] **Open-Interest Roll Method** — Adds the `.n`-equivalent rule. It needs a PM decision to buy the statistics schema, archived as manifest units with provenance like any other purchase. Dependencies: [228].
3. [ ] **Intra-Unit Ingest Checkpoints** — Only if 226 measures that unit-boundary re-decode cost justifies them. Dependencies: [226].
4. [ ] **Finer Schema Tier (mbp-1)** — A per-instrument tier configuration change plus a projection. It needs a cost case from the preflight. Dependencies: [226].
5. [ ] **Shared Pass-Framework Extraction (9xx)** — If three copies of the phase/report/result contract (Kalshi, tick, and one more) make hoisting worthwhile. Foundation work, not 220.
6. [ ] **Realtime Tick Capture** — Its own initiative, in the streaming form (`Type=simple`). It inherits the delivery-agnostic key, the manifest's delivery-mode discriminator, and the reserved sequence-gap tracking.
