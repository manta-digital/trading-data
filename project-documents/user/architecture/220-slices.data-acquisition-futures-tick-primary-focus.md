---
docType: slice-plan
parent: user/architecture/220-arch.data-acquisition-futures-tick-primary-focus.md
project: trading
dateCreated: 20260923
dateUpdated: 20260929
status: in_progress
---

# Slice Plan: Futures Tick Data Acquisition

## Parent Document

220-arch.data-acquisition-futures-tick-primary-focus.md — Historical tick data for CME futures from Databento (ES first, then GC), trades and trades-with-BBO tiers. Purchased as files into an archive tracked by a manifest, then projected into a tick hypertable on its own database. The initiative includes a futures instrument model with read-time roll methods, and parity with the minute tier on every operator, API, and backup surface. It is proved on the ES files already bought with free credits before the universe expands.

## Foundation Work

1. [x] **(220) Databento Adapter and Cost Preflight** — Adds the `databento` dependency, the tick-provider protocol (shaped like `IMinuteDataProvider`/`IDailyDataProvider`), and the one adapter that owns the wire format. The adapter covers DBN decode, symbology (raw, instrument id, parent, continuous), and both delivery modes: batch job and direct range request. Its execution model is a synchronous *unit → bounded record batches* iterator that runs off the event loop. The batch bound is one named constant, set conservatively until the proof measures it. The threads-versus-processes question is answered here by measuring whether the provider's decoder releases the interpreter lock. The first tick CLI verb is a cost and size preflight. It calls only the free metadata endpoints (`get_cost`, `get_billable_size`, `get_record_count`, dataset range and condition) and reports per tier (`trades`, `tbbo`, `mbp-1`) for the same instrument and range. That comparison is the input to the tier decision. The spend-ceiling setting, `MT_TICK_SPEND_CEILING_USD`, is added to `Settings` with no default. The slice-design verification items are resolved here: the batch retention window, size limits per delivery mode, the 24-hour embargo, the record-count query, and how symbol-mapping records appear in delivered files. Unit tests run over real DBN files. The provider's published sample files are the candidate (verify at design), because this slice may not buy anything. Mapping rows go in the data-correctness contract. Dependencies: [900 provider registry, complete]. Risk: Medium. Effort: 3/5

2. [x] **(221) CME Session Model and Data-Correctness Amendment** — Adds the futures session model as a second calendar behind the I4 session surface. The pieces are:
   - A CME `calendar_id` row in `trading_calendars`, verified per product (ES and GC are expected to share one).
   - The open-after-close rule in `populate_trading_sessions`, applied to every calendar. `NYSE` rows are unchanged, and a test proves it.
   - CME holidays, early closes, and irregular sessions seeded as `trading_holidays` rows by a minute-track migration, from CME's public schedule.
   - Sessions populated backward over the wanted range, and forward through the existing per-calendar extension (`TRADING_SESSIONS_EXTENSION_YEARS`, `mt data extend --calendar`, and the automatic extension that `mt data status` runs).
   - A `TradingCalendar` method that returns the session containing a timestamp. A timestamp outside the populated range fails explicitly.

   This is the first slice to touch a governed surface, so it also writes the frame of the contract amendment: the tick invariants, the I7 exception with its reason, the vocabulary rules (manifest-based completeness, and where `granularity = 'tick'` may appear), and sequence-gap detection moved to the realtime initiative. The slice needs no purchase and no tick database. Dependencies: none within this plan. Risk: Medium. Effort: 3/5

3. [x] **(222) Tick Storage Track** — Appends to the `tick` migration track that 923 registers (ledger bootstrap only, routed to the tick database). Its tables are:
   - The trades-tier hypertable, with trade fields always present and nullable BBO fields, plus each row's archive-unit integer id (not a string, not in the natural key). Its chunk interval is set explicitly and conservatively from wall-clock span (journal 20260719). Physical grouping by instrument waits for the proof.
   - Definitions (the futures instrument model, with identifier validity windows).
   - The manifest of archive units: state machine (rows written at *requested*), delivery-mode discriminator, tier, download deadline, estimated and actual cost, the minute tier's fetch-state vocabulary (`FetchStatus`: attempt count, `FAILED_RETRYABLE`, `RETRY_EXHAUSTED`, `PROVIDER_HOLE`), and links to the unit it repurchases or supersedes.
   - The ingest ledger: one row per instrument, session, and unit, holding records loaded, first and last event time, and traded volume. Session totals are summed at read time over current (non-superseded) units.

   The slice renders its tier CHECK constraints from `STORED_TIERS`, a subset of the `TickSchema` enum that 220 defines (the preflight needs the vocabulary first), and adds its tables to the write surface of 923's tick grant artifact (`scripts/provision_tick_roles.sql`). It removes slice 105's remaining leftovers: the `TickEventType` enum and its unit test (923 deletes `test_tick_schema_integration.py`, which read `MT_TICK_DB_URL`). Storage follows the NautilusTrader-compatibility recommendation (940 analysis): event and receive timestamps at nanosecond precision, fixed-point prices kept exact, and contract identifiers that map cleanly to venue-suffixed IDs. No data is written; this slice only creates tables. Dependencies: [923 Multi-Database Migration and Credential Plumbing — hard gate, no fallback], [220]. Risk: Medium. Effort: 3/5

## Feature Slices (in implementation order)

4. [x] **(223) Tick Archive Adoption** — The foundation the acquisition pass stands on, and everything that spends no money. It adds **adoption of existing batch files**: given a job id and its local directory or zip, verify every file against the job's `manifest.json` (size and SHA-256), read the job's record (free), and write archive units with the recorded cost, verified per UTC day. The two free-credit ES jobs are adopted this way. It also adds the run context and its preflight (refuses while tick migrations are pending, and on an unknown `MT_TICK_*` key), the advisory lock, the compare-and-set manifest repository, the `tick_006` migration (availability tables and `reopened_at`), the 30-day spend and archive-directory settings, **fetch-state tracking at minute parity** with a manual reset for exhausted and holed units, and **enrols the archive directory in the backup set** (architecture, "Backup is part of done"). The design of record is 224's slice design; 223 implements the sections that design assigns to it. Dependencies: [222]. Risk: Medium. Effort: 3/5

5. [x] **(224) Historical Acquisition Pass** — The acquisition pass follows the Kalshi phase/report/result contract, copied into the tick package and diffed against the Kalshi original. Its phases:
   - Reconcile in-flight units first. Deliverable units download earliest deadline first. A unit whose deadline passes moves to *failed* with the reason *retention expired*, and its repurchase is a new unit that names the expired one.
   - Capture the availability edge from the provider's free dataset metadata.
   - Compute the wanted set (universe × range) minus the manifest, in calendar-day UTC units.
   - Estimate, then apply both spend guards: the per-pass ceiling (`MT_TICK_SPEND_CEILING_USD`) and the rolling 30-day cap (`MT_TICK_SPEND_30D_CEILING_USD`), summed from the manifest using actual cost where known and the estimate otherwise. The pass refuses to purchase when either is absent.
   - Submit batch jobs only (no direct-range purchases), poll within the pass's wait budget, download, and verify checksums and sizes.

   Definitions for the adopted jobs are not in their files, so this pass buys them (under $0.01).

   Contract definitions are acquired through this same path as `definition`-schema units. They are billable, and the architecture allows no exempt requests. This moves definitions capture here from the architecture's storage-track sketch. The slice also adds a configured tick universe (products or contracts, tier per instrument, wanted range).

   The pass runs manually from the CLI. No schedule code is built: `PassKind.TICK`, its `schedule_for` branch, and any timer wait for the cadence decision (233). The spend-ceiling guard is tested with mocked cost responses and never against the live account. Dependencies: [223]. Risk: High. Effort: 4/5

6. [ ] **(225) Ingest Pass and Proof Parity** — The ingest pass reads archive units that have not been ingested yet. It projects definition units first, then trade units. It decodes through the adapter's bounded iterator, resolves identifiers against stored definitions, and assigns each record a session through the 221 lookup. Each worker writes its own unit with `COPY` over a dedicated connection it owns. Only progress returns to the loop.

   The ingest ledger gets a row per instrument, session, and unit for every configured instrument whose definition is valid in a session the unit's range touches, including zero-record rows. A unit that supersedes others deletes their rows for its range and loads its own in one transaction. The pass reads the CME calendar from the production database once per unit, so a production outage halts ingest (nothing is lost). Three checks gate the commit of a unit's ledger rows and its *ingested* transition:
   - *Counts:* the provider's record count equals the records decoded, which equals the rows in the raw table.
   - *Resolution:* every record resolves to a contract.
   - *Session boundaries:* no trade falls in the daily break, on a closed day, or outside the populated calendar range.

   A unit that fails a check moves to *failed* with the check named.

   The slice also delivers proof parity on the tick CLI:
   - status per product and contract: sessions complete, pending, in flight, missing, failed, retry-exhausted, provider hole, and edge unknown. A session takes the worst provider condition of the days it touches.
   - coverage per session.
   - completeness under the architecture's definitions.

   These read only the manifest, the ledger, and raw counts, never an aggregate. Dependencies: [221, 224]. Risk: High. Effort: 4/5

7. [ ] **(226) Proof on Existing Data** — The proof runs first on the two adopted free-credit ES jobs: `trades` 2024-08-30 → 09-29 (10.0 M records) and `tbbo` 2024-11-01 → 12-31 (17.6 M records). Both ranges contain a quarterly roll, the September and December 2024 ES rolls. No purchase is needed. The first data under the Standard plan follows the go/no-go, through the manual pass. It measures:
   - bytes per record by tier, and compressed bytes per row;
   - ingest rate, against the throughput pass/fail restated for on-demand acquisition: a day of sessions far faster than a day of market time, and a month within an operator's working session;
   - typical batch-job duration and poll interval;
   - query latency at the chosen chunk interval;
   - whether intra-unit checkpoints are needed;
   - the decoded-batch memory budget, which sets the batch-size constant;
   - contention: the tick ingest running concurrently with the minute pass (the minute pass's duration, host I/O, memory, and write throughput against the solo baseline).

   Mapping-record completeness is a pass/fail criterion. Either every tick is attributable to its contract, or the raw-symbol fallback is adopted. The ingest checks pass on every unit. The chunk interval is validated. The physical-grouping decision (space partitioning and compression layout) is made from the numbers and applied by a tick-track migration. If the schema is invalidated, the remedy is a rebuild from the archive. The output is a written go/no-go on tier and on GC, and on subscribing to the Standard plan. Passes run by hand. Dependencies: [225] (not 233, which is deferred). Risk: High. Effort: 4/5

8. [ ] **(227) Backup Coverage for Tick Archive and Database** — The archive is already in the backup set, from 223. This slice chooses the database policy (full base backup, WAL-only, or rebuild from the archive on restore) is chosen from the proof's measured rebuild cost. The 913 role set and the backup tooling are extended to the tick instance, not copied by hand. The restore drill covers both the archive and the database. Retention, exclusions, and placement are recorded in the backup runbook. Dependencies: [226]. Risk: Medium. Effort: 3/5

9. [ ] **(228) Active Contract and Roll Methods** — Adds the roll-method vocabulary: calendar with an optional days-before-expiry offset, and volume. **Open interest must be evaluated here** (PM direction 2026-09-27). The Standard plan includes the `statistics` schema, open interest among it, so the rule is no longer blocked on a purchase. The design decides whether it ships in this slice, and if so, `statistics` units come in through the 224 path. The project default is defined once.

   Active-contract resolution reads stored definitions and ledger volume. It reports the latest session its inputs cover, and it can be re-derived later from stored state. Roll-day handling has a stated default. The continuous series is a read-time view and is never stored. Status lines name the active contract, the rule that chose it, its expiration, and the next roll.

   The vocabulary matches Databento's continuous-symbol conventions. A test resolves the same contract both ways, over a roll in the purchased range. Dependencies: [226]. Risk: Medium. Effort: 3/5

10. [ ] **(229) Operator Surface Full Parity** — Applies 220's `mt data tick` subgroup decision. Covers the verbs the proof did not need:
    - `get` for ticks and for tick-derived bars, by contract or by product plus roll method;
    - accounting lines;
    - debug reads of the manifest and definitions;
    - a tick line in `mt data overview`, reachable as `mt-run data overview`.

    Every composed surface reports "unreachable" rather than omitting the line. The slice also writes the runbook's operator procedures and adds contract mapping rows for these surfaces. The same questions get answers with the same defaults as the minute tier. Dependencies: [228]. Risk: Low. Effort: 3/5

11. [ ] **(230) API Surface Coverage — Futures Tick** — Applies 220's `/api/v1/futures/*` namespace decision. The endpoints serve:
    - ticks and tick-derived bars by contract or by product plus roll method;
    - the contract catalog, with definitions, expirations, and the active contract per rule;
    - tick coverage and freshness in `/api/v1/status` and `/api/v1/overview`.

    `create_app` gains a `tick_db_url` seam. `mt-serve` starts without the tick database, and tick endpoints return `503` while the pool is down. `/api/v1/health` gains a tick-database field beside `db` and always returns 200.

    Each endpoint answers the standing question "does this belong in the API?" Every route publishes a response model, avoiding the `response_class=Response` trap. Both reference documents are extended under the 190 gate, and `openapi.json` is regenerated. Closes 180's tick exclusion. Dependencies: [229]. Risk: Medium. Effort: 3/5

## Integration Work

12. [ ] **(231) Universe Expansion — GC** — Adds GC as a configuration edit, plus the `CME_METALS` calendar: 221 found that GC's holiday schedule differs from ES's, so this slice seeds a second calendar (a dated exception table and a minute-track migration, following 221's pattern) and adds `GC` to `FUTURES_PRODUCT_CALENDAR`, with a cost estimate that includes roll inputs for the range. It is acquired (plan-included where possible) and ingested at the tier set by the 226 go/no-go, and it runs at whatever cadence the PM has decided. The session model is verified for GC, and the ingest checks pass on every GC unit. Status, overview, and the API show GC with no code change. Verification uses a roll inside the purchased range and a manually fired pass. It does not wait for a future roll or a future timer firing. Dependencies: [230], the 226 go/no-go. Risk: Low. Effort: 2/5

13. [ ] **(232) Cross-Source Acquisition Arbitration** — Sets `IOWeight`, `CPUWeight`, and `MemoryMax` on `manta-acquisition.slice` from the proof's contention numbers. It then chooses among the 900 plan's three mechanisms: a resource-class lock the passes take, a scheduler inside `mt`, or keeping calendar stagger as it is. The contention measurement is repeated to show the chosen setting works. This closes 900 Future Work item 4. Dependencies: [226]. Risk: Medium. Effort: 2/5

14. [ ] **(233) Tick Pass Production Wiring** — **Deferred until the cadence decision** (PM direction 2026-09-27: no schedule code yet), which depends on the realtime initiative. When it is built, it deploys the passes in the 916 production form and adds:
   - The minute-track migration adding `PassKind.TICK` and re-rendering the `pass_runs` CHECK constraint, and the `PassKind.TICK` branch in `schedule_for`.
   - The pass unit under `manta-acquisition.slice`, and a timer only if the cadence decision calls for one.
   - `mt-run tick`, `mt-run follow tick`, and the `mt-run status` row.
   - The install-script entry.
   - `MT_TICK_DB_URL`, `MT_DATABENTO_API_KEY`, and `MT_TICK_SPEND_CEILING_USD` in the service environment files of every process with a tick duty.
   - The tick archive directory on the host.
   - The runbook's add-a-source entries.

   It also adds the second connection pool from `MT_TICK_DB_URL` to the health check, as the first composing surface. The check gains a retention-deadline finding for any in-flight unit whose deadline falls before the next scheduled firing, or, with a manual-only pass, within a fixed number of days. When the setting is absent, health prints a loud "not configured" line and continues, so minute, daily, and Kalshi monitoring are never silenced. When the tick database is unreachable, health reports "unreachable" rather than omitting the line. The minute, daily, and Kalshi passes never read the tick setting. Cutover is a script per the PM-host-step convention. Dependencies: [225]. Risk: Medium. Effort: 3/5

## Notes

- **Resequenced 2026-09-28 (PM direction):** slice numbers follow build order. Production wiring, deferred until the cadence decision, moved from 225 to 232 (last). The former 226–232 became 225–231. Reviews written before this date use the old numbers.
- **Split 2026-09-28 (PM direction):** the historical acquisition pass (223) was split at its task breakdown so each slice fits one implementation session: 223 Tick Archive Adoption and 224 Historical Acquisition Pass. The former 224–232 became 225–233. The combined design is `224-slice.historical-acquisition-pass.md`; reviews of it and of its task files were written under the number 223.
- **PM direction, 2026-09-27** (the architecture's Revision Log has the detail):
  - **Data comes in three stages:** the verified free-credit files on disk (`/data/market-data/databento`), then the Standard plan's included history, then rare purchases outside the plan.
  - **Subscription timing:** ideally not until realtime work begins. Realtime remains its own initiative.
  - **Purchases are batch jobs only.**
  - **Spend guards:** a rolling 30-day cap joins the per-pass ceiling, and the provider-side limit is set.
  - **Cadence is deferred, with no schedule code until realtime work makes it decidable;** every tick pass runs by hand. The availability lag is measured at 8 hours, not 24.
  - **Fetch states at minute parity** (`FetchStatus`), run by hand.
  - **Precedence:** historical data replaces live data for the same period by default. Each tick row carries its unit's integer id, and the ledger is per unit.

- **923 is the one external gate.** 220 and 221 need no tick database and proceed in parallel with 923. 222 onward waits for it. Scheduling 923 is the PM's decision. While it waits, 220's and 221's outputs do not decay. The exceptions are the pinned SDK and the recorded fixtures: re-record them and re-run the preflight when 222 starts if the SDK has moved.
- **Differences from the architecture's Anticipated Slices** (which the architecture labels exploratory):
  - The first sketched slice (adapter + session model + contract frame) is split into 220 and 221. The two are independent and share no code.
  - Contract-definitions capture moves from the storage track (222) to the acquisition pass (224). Definitions are billable, and the architecture's cost principle allows no request outside the estimate → guard → manifest path. That path is built in 224. The sequencing rule still holds: 222 creates the table and 224 writes it.
  - The sketched passes slice is split into adoption (223), acquisition (224), ingest with proof parity (225), and production wiring (233). Each leaves a working system. After 223 the free-credit jobs are archived, verified and backed up. After 224 a manually run pass fills the archive. After 225 the database fills and status answers. After 233 it runs under systemd.
  - The steady-state slice is split into GC expansion (231) and arbitration (232). They depend on different inputs and change different components.
  - Backup (227) comes right after the proof, not after the API. The architecture says "backup is part of done". The database policy needs only the proof's rebuild-cost number, so there is no reason to leave real purchased data unprotected across three more slices.
- **Architecture statements superseded by 220's slice design** (the slice design is authoritative for 222 onward):
  - The decode batch bound is a byte budget (`TICK_DECODE_BATCH_BYTES`) with the record count derived per schema, not "a record count, one named constant" — a count cannot bound memory across 48-byte trades and 520-byte definitions.
  - Delivered historical files carry no symbol-mapping records. The mappings live in the DBN file's metadata header (`mappings`, `partial`, `not_found`); in-stream mapping records are a live-API feature only. 225 and 226 read the header.
- **Architecture statement superseded by 221's slice design:** ES and GC do not share a calendar row. Regular hours match (17:00–16:00 CT), but holiday halts and early closes differ (equity 12:00 CT vs metals 13:30 CT on US holidays; Black Friday 12:15 vs 12:45; equity-only abbreviated Good Friday sessions). Calendars are keyed per schedule: `CME_EQUITY` (221) and `CME_METALS` (231).
- **Architecture statements superseded by 222's slice design:**
  - The natural key is `(instrument, event time, sequence, sequence_ordinal)`, not the provider's triple, which repeats for 1.95% of real ES trades (TD1).
  - The *failed* state is `fetch_status` (`FetchStatus`) beside a furthest-reached `state`, so a failed unit keeps its resume point (TD4).
  - The manifest is two tables: job-grain facts on `tick_request`, the unit lifecycle on `tick_archive_unit` (TD4).
  - The archive unit is one UTC day of a request, normally one provider file, not "one provider file": a provider hole has no file (TD4).
  - The tier is reached through the unit's request, not stored on the ledger (TD7).
- **Architecture and plan statements superseded by 224's slice design** (recorded in the architecture's Revision Log, entry 2026-09-28):
  - "Tick acquisition, which needs no calendar" — planning and adoption read the CME calendar for session days; reconcile, download, verify and definitions do not, so a production outage stops new purchases, not deliveries (TD6).
  - 225 "projects definition units first" — 224 projects them; 225 requires definition units to be *ingested* before tier units (TD5).
  - Definition scope — definitions are bought per tier request shape (same symbols, `stype_in` and days), not per configured product (TD5).
  - `PROVIDER_HOLE` from missing days — a `missing` day is recorded in `tick_day_condition` and not bought; `PROVIDER_HOLE` marks a unit only when a completed job delivered no file for a session day (TD8).
  - 222 "no schema change of their own" — 223 adds `tick_006`: the two availability tables and `reopened_at` (TD8).
  - "A file that fails verification is not adopted" — adoption is all-or-nothing per job (TD10).
- **No billable request before 224.** 220's preflight uses only free metadata endpoints, and its test fixtures must be free real DBN files. The first real purchase happens only after the PM sets `MT_TICK_SPEND_CEILING_USD`.
- **The CLI and API surface decision was made at 220 design:** an `mt data tick` subgroup and a `/api/v1/futures/*` namespace. 229 and 230 apply it.
- **Standing obligations on every 220 slice:** diff the tick pass contract against the Kalshi original, as a named task in the slice's task file (from 224 on). 224 also adds a unit test comparing the tick copy's fields with the Kalshi original's, so divergence fails a test rather than waiting for a checklist (review F015). Include a "realtime paths" check naming any decision that rules out path A (assemble from realtime) or path B (historical with delay). Add the slice's rows to the data-correctness contract's slice-mapping table. Answer "does this belong in the API?" for any new surface.
- **Proof range suggestion (PM decision):** choose the 226 ES range to cover a quarterly roll. 228 and 231 can then verify roll resolution on data already bought, with no wait for a future expiry.
- **Calendar start bounds the ES range (found by 224's load test):** `CME_EQUITY` is populated from 2020-01-01 23:00 UTC, so session days asked for from 2020-01-01 raise `TickCalendarError` and the pass ends `storage_abort`. When 226 sets the ES tier range, start it on 2020-01-02 or later, or extend the calendar back first.
- **Architecture verification items resolved at slice design:** batch retention window and size limits, embargo, record-count query, mapping-record behaviour, and decoder lock release (220). CME schedule reach-back and per-product hours (221). Everything else the architecture marks as measured is measured in 226.
- **Unchanged decisions carried from the architecture:** `pass_runs` and the CME calendar live on the production database's minute track, and they are the only exceptions to tick isolation. Completeness is never read from an aggregate. No stored continuous or back-adjusted series. Pass form only; streaming belongs to the realtime initiative.

## Future Work

1. [ ] **NautilusTrader Catalog Export** — One export slice from the tick and equities stores to a NautilusTrader `ParquetDataCatalog`, if manta-engine adopts NautilusTrader (940 analysis, recommendation 4). The catalog is a derived artifact, and trading-data stays the store. Dependencies: [230].
2. [ ] **Open-Interest Roll Method** — Adds the `.n`-equivalent rule, if 228's evaluation defers it. The `statistics` schema is included in the Standard plan, so this needs no purchase decision. Dependencies: [228].
3. [ ] **Intra-Unit Ingest Checkpoints** — Only if 226 measures that unit-boundary re-decode cost justifies them. Dependencies: [226].
4. [ ] **Finer Schema Tier (mbp-1)** — A per-instrument tier configuration change plus a projection. It needs a cost case from the preflight. Dependencies: [226].
5. [ ] **Shared Pass-Framework Extraction (9xx)** — If three copies of the phase/report/result contract (Kalshi, tick, and one more) make hoisting worthwhile. Foundation work, not 220.
6. [ ] **Realtime Tick Capture** — Its own initiative, in the streaming form (`Type=simple`). It inherits the delivery-agnostic key, the manifest's delivery-mode discriminator, and the reserved sequence-gap tracking.
7. [ ] **Futures OHLCV History (1-Minute and 1-Second)** — The Standard plan includes 16 years of `ohlcv-*` for CME futures (`ohlcv-1s`, `-1m`, `-1h`, `-1d`, `-eod`). This history is free only while the plan is subscribed. Uses: long-range and older-period studies (for example the COVID era) without buying ticks, and a check on bars derived from ticks. **Decision (PM, 2026-09-28):** take it during the first subscription month: the full 16 years of `ohlcv-1m` for the chosen instruments, and at least some `ohlcv-1s`. `ohlcv-1s` is about two orders of magnitude larger than `ohlcv-1m`, so its range is bounded by storage, not by value. The files are archived first, through the same estimate → guard → manifest path; the preflight confirms $0 cost before anything is pulled. Storage design follows later: its own tables with contract identity, never `minute_ohlcv`, and never the tick hypertable. Dependencies: [226].
