---
docType: architecture
layer: project
project: trading
archIndex: 280
component: futures-realtime-data-databento-live
parent: 001-initiative-plan.trading.md
dependencies:
  - 220-arch.data-acquisition-futures-tick-primary-focus.md
  - 180-arch.data-serving.md
  - 900-arch.foundation-cleanup.md
relatedSlices: []
riskLevel: medium
dateCreated: 20261015
dateUpdated: 20261015
status: not_started
---

# Futures Realtime Data Architecture — Databento Live

## Overview

Initiative 280 adds live capture of CME futures ticks through the Databento Live API, plus the history the Databento Standard plan subscription ($199/month) unlocks. Initiative 220 built and proved the historical pipeline: the adapter, the archive and manifest, tick storage, the CME session model, roll rules, operator commands and the API. It did that on two free-credit ES jobs and never paid for anything. This initiative is what makes the monthly subscription worth paying.

Realtime data has one purpose in this project: executing strategies that historical research produced (journal 20260824). The end state of data gathering is a few selected futures streaming live ticks, not everything at once.

**Scope:**
- **Live capture** of ES and GC on `GLBX.MDP3` at the tier 220 chose for history (`tbbo`: every trade, with the best bid and offer at that moment), plus the live `definition` and `statistics` records that keep the instrument model and roll inputs current.
- **Durable recording** of the live stream into the archive and the tick tables, in the same shape historical data takes.
- **Recovery** from disconnects and outages of any length, with no silent holes.
- **Reconciliation** of live data against historical data once history becomes available, and the daily cadence that drives it.
- **Subscription-month history:** 16 years of `ohlcv-1m`, `ohlcv-1s` on request, and 16 years of `statistics` (open interest and settlement), each with its own storage. The plan's included 12 months of `tbbo` needs no new code; 220's pass takes it as built.
- **Near-realtime reads** for trading-data's own consumers (operator surfaces, the API), plus the decision on whether trading-data also feeds execution.

**Out of scope:** MBP-10 and MBO (not in the plan), live `mbp-1` (every top-of-book change; a future tier change, sized below), options, equities realtime, Kalshi near-real-time (its own future work; the journal expects the two to mature together), order routing and execution.

**Motivation:** Two forces set this initiative's shape. First, the subscription is the expensive recurring cost, so the work is designed to be **built and tested before subscribing**: the subscription starts when the work is ready to run, not while code is being written. Second, a live feed fails in ways a historical batch job never does. Connections drop, the host restarts, the consumer falls behind, the gateway restarts during the CME daily pause. Each of those must leave a recorded, explainable gap that gets filled, never a hole someone finds later in a backtest.

## Design Goals

- **Build everything before paying** — Every slice except the cutover is designed, built and tested against recorded data: the local ES batch files, the DBN project's published sample files for the schemas we don't hold, and a local stand-in for the live gateway that replays those files over the real protocol. Questions only a subscription can answer are listed (Technical Considerations, "Assumptions taken until the subscription") and taken on assumption; the work proceeds past them.
- **No silent holes** — At any moment the system can say, per instrument and session, which intervals it holds live, which it lost and why, and which have been reconciled against history. A gap is a recorded fact with a cause, never an absence someone has to detect.
- **One shape for live and historical data** — Live ticks land in the same archive, manifest and tick tables as historical ticks, under 220's delivery-agnostic natural key. Every read surface 220 built (status, coverage, `get`, `active`, `/api/v1/futures/*`) serves live data with no second code path.
- **History is the record; live is the bridge** — Live data bridges from now back to where historical data becomes available, about 8 hours behind the market. Once a day's history is available it supersedes that day's live data, and the comparison between them measures how well capture did.
- **Measured capacity, not guessed** — Bandwidth, record rates, storage and ingest headroom come from the files already on disk and from Databento's free metadata calls (Technical Considerations, "Measured bandwidth and size"). At `tbbo` they are not constraints; the design says so and spends its effort on continuity.

## Architectural Principles

- **One live session per dataset, owned by one service** — Capture is one long-running `Type=simple` service (the streaming form reserved by the 20260823 production-form ADR and the 20260824 journal entry), holding one Live API session to `GLBX.MDP3`. A session covers exactly one dataset (SDK `live/session.py`). The Standard plan allows 10 simultaneous sessions per dataset per user, so capture uses one and leaves the rest for consumers such as manta-engine. Nothing else in trading-data opens a live session.
- **The receive path never waits on the database** — Records go from the socket to a durable local file first. Projection into the tick tables is a separate consumer that may lag and catches up without loss. A slow or unavailable database can delay what readers see; it can never make the gateway drop records. The client is configured to warn when it reads too slowly, never to skip records (`slow_reader_behavior`); a skipped-records error from the gateway is a recorded gap, not a log line.
- **File-first, as in 220** — Raw DBN is archived before anything is projected. A live **segment** (one connection's records within one UTC day) is an archive unit with a new delivery mode alongside 220's `batch_job` and `direct_range`. A segment ends at the UTC day boundary, on disconnect, or on service stop. The manifest records each segment's covered interval, its record count, its cause of ending, and its checksum once closed. Ingest projects segments through 220's adapter and worker, so live and historical rows are produced by one code path.
- **Gaps are intervals, recorded where they happen** — Neither `tbbo` nor `trades` can reveal a gap through sequence numbers: CME's sequence counts every venue message, not just trades. On the local ES `trades` file for 2024-09-18, only 9% of consecutive trades on the front contract differ by one, with a median step of 50. Gaps are therefore detected from the connection, not the data. The signals are disconnect intervals (the SDK reports the last record time before a drop and the start of the new session), the heartbeat watchdog, slow-reader warnings, skipped-records errors, and service stops. Each one becomes a gap interval in the manifest with its cause. The authoritative completeness check is reconciliation against history.
- **Recover with the cheapest complete source first** — The order is: (1) reconnect and replay from the last record received, inside the gateway's 24-hour replay window; (2) for anything older or aged out, history through 220's acquisition pass once the dataset's available end passes the gap, read at fill time from `get_dataset_range` (220 stores it as `tick_dataset_edge`), never a fixed lag. The SDK's own reconnect does not replay (it resubscribes with no `start`), so replay is this initiative's responsibility, not the library's.
- **Supersession stays 220's rule** — Historical over live by default, per whole UTC day, in one transaction, with the manifest recording which units superseded which (220 Architectural Principles, "Every row knows its unit"). Partial-range supersession, which 220 left to this initiative, is needed only if a day's history can't be taken; the reconciliation design decides whether that case exists.
- **Test against the real client, not a mock of it** — The stand-in gateway speaks the Live protocol as the SDK implements it (text handshake with challenge-response auth, subscription requests, a DBN metadata header and record stream, system and error records). The real `databento` client connects to it through the SDK's `gateway=` override. Fault injection (drop, stall, slow reader, error records, aged-out replay, gateway restart in the daily pause) runs against production code paths. A test that only passes against an invented protocol is a false pass (project parsing rules).
- **Backup and health are part of done** — The live archive is in the backup set from its first slice. Every new state (segment open, gap unreconciled, ingest lag, live-versus-history mismatch, capture down during an open session) is a health finding with a fixed threshold. "Down" is judged against the CME session model (slice 221), so silence during the daily pause or the weekend close is expected and never a finding.

## Current State

- **Historical pipeline (220, complete, v0.23.1 in production)** — The Databento adapter (DBN decode, symbology, batch and direct-range delivery), the tick database on its own cluster, the archive and manifest (`DeliveryMode.BATCH_JOB`, `DIRECT_RANGE`), the ingest pass (measured at 228,000 records/s: an 889,373-record `tbbo` day in 3.9 s), the CME and CME metals calendars, roll rules (calendar and volume), operator commands, `/api/v1/futures/*`, backup and a proven restore drill. ES and GC are configured at `tbbo`.
- **What 220 reserved for this initiative** — A delivery-agnostic natural key `(instrument, event time, sequence)` with provider fields preserved; a manifest delivery-mode discriminator that admits a captured segment; completeness defined per unit, so a segment with a disconnect is an incomplete unit, not an undefined state; supersession with historical over live by default; reserved sequence-gap tracking. The last of these does not fit, because the tiers we capture can't reveal gaps by sequence (principle above).
- **Tick cadence is manual** — No timer exists for tick passes (slice 234: `FiringSchedule.manual()`), on PM direction 2026-09-27 that the cadence waits for realtime. This initiative supplies the answer.
- **Roll inputs** — The open-interest rule is not built. 229 and 232 deferred it because it needs the `statistics` schema; its trigger is the first subscription month. The schema enum (`TickSchema`) knows `trades`, `tbbo`, `mbp-1` and `definition` only, so `ohlcv-*` and `statistics` cannot even be archived today.
- **Serving** — The API is request/response. 180's architecture defers WebSocket streaming "until live tick data flows" (180 Technical Considerations). manta-engine has not chosen its engine; the 940 analysis recommends NautilusTrader, which ships its own Databento live adapter.
- **Host** — manta9000 has a 1 Gbit/s wired link and about 1,339 GB free on `/data` (226 capacity report). Acquisition services run under `manta-acquisition.slice` with a fixed `MemoryMax=256G` (v0.23.1).

## Envisioned State

At completion, trading-data holds a continuous tick record for the live universe. Live data covers the last hours up to now; history covers everything older and has replaced each day's live data once available. Every interval in between is accounted for.

- **Capture service** — One supervised streaming service subscribes to `tbbo`, `definition` and `statistics` for the configured products (ES, GC) on `GLBX.MDP3`, with gateway send timestamps (`ts_out`) on so delivery latency is measurable. It writes segments to the archive and records them in the manifest. On disconnect it reconnects with replay from the last record received, trims replayed records it already holds so segments never overlap, and records the interval between as a gap with its cause. It runs through the CME daily pause and the weekend close. A gateway restart in the pause is an expected, recorded segment end.
- **Live projection** — A consumer projects open and closed segments into the tick tables continuously, through 220's adapter and worker. Ticks are readable in the database within seconds of receipt. Projection lag is a reported quantity. Definitions arriving live update the instrument model, so a newly listed contract is known before its first trade is stored.
- **Daily reconciliation** — A timed tick pass, the first timer tick has had, runs after the dataset's available end has passed a session. It takes that day's history through 220's acquisition path, compares it per instrument and session with the live record (record counts, first and last event time, traded volume from the ingest ledger), records the result, and supersedes the live day. A mismatch is a health finding that names the intervals. The same pass fills recorded gaps older than the replay window. If plan-included history prices at $0 the pass is free; if it doesn't, ES and GC `tbbo` cost about $5 per five-session week at historical prices (free estimate, 2025-09-08 to 09-12), and the spend guard bounds it either way.
- **Subscription-month history** — `ohlcv-1m`, `ohlcv-1s` and `statistics` are new schemas in the archive, each projected into its own table with contract identity (never `minute_ohlcv`, never the tick hypertable), each acquired through the estimate → guard → manifest path. `ohlcv-1m` and `statistics` are pulled whole for ES and GC (16 years, 1.4 GB and 1.6 GB billable). `ohlcv-1s` is pulled **on request**: an operator or API request for a range fetches the units it lacks, archives them, and keeps them; later requests for the same range read what is held. The open-interest roll rule reads the stored `statistics`.
- **Reads** — Status, coverage, overview, health and `/api/v1/futures/*` report live and historical data together, with each interval's provenance (live, reconciled, gap, filled) and a "data through" time per instrument. Bars built from ticks cover the live window too.
- **Execution feed** — Decided with manta-engine (Technical Considerations, "Who consumes realtime"). If trading-data serves it, the capture service is the single source and fan-out happens after the durable write, so a slow consumer can never cost the record.
- **Production** — The capture service and the reconciliation timer are installed by the existing install path, with backup, health and the runbook updated. The cutover is one script. The subscription starts only at that cutover.

## Technical Considerations

- **Measured bandwidth and size** — Measured on 2026-10-15 from the local ES batch files (`ES.FUT` parent, every outright and spread) and from Databento's free record-count and billable-size calls. These figures cover full CME sessions only.

  | Quantity | ES `tbbo` | ES `trades` | Source |
  |---|---|---|---|
  | Records per session day | mean 428,045, max 889,373 | mean 498,556, max 756,899 | 2024-11/12 and 2024-09 files |
  | Bytes per record (DBN) | 80 | 48 | files |
  | Raw stream per day | mean 34 MB, max 71 MB | mean 24 MB, max 36 MB | files |
  | Busiest second | 2,798 records = 224 KB/s, about 1.8 Mbit/s | 2,017 records | 2024-12-18 (`tbbo`), 2024-09-06 (`trades`) |
  | Busiest 100 ms | 813 records, about 5.2 Mbit/s instantaneous | 749 records | files |
  | 99th-percentile second | about 70 records | about 83 records | files |

  - **GC** runs at 27% to 36% of ES's records (226: 35.0 M per year against ES; the 2025-09-08 week: 633,558 against 1,736,085).
  - **ES plus GC at `tbbo`:** under 3 Mbit/s at the busiest second, about 47 MB of raw stream per day, and 4.70 GB per year stored (226 projection). That is under 1% of the link and far below ingest capacity (228,000 records/s against a peak second of 2,798).
  - **For scale, `mbp-1`** (every top-of-book change) would be 6.3 M records per day for ES (505 MB) and 1.8 M for GC (143 MB): 14 to 18 times `tbbo`, still a few percent of the link. It is the first tier where bandwidth and storage would need design.
  - **Consequence:** at `tbbo`, wire compression, buffer sizing and resource weights are not design problems. The constraints are continuity and latency.
- **Subscription-month history sizes** (free metadata calls, 2026-10-15, `GLBX.MDP3` parent, 2010-06-06 to 2026-10-01):

  | Schema | ES | GC |
  |---|---|---|
  | `ohlcv-1m` | 8.7 M records, 0.49 GB | 15.8 M records, 0.89 GB |
  | `ohlcv-1s`, all 16 years | 170 M records, 9.53 GB | 133 M records, 7.47 GB |
  | `ohlcv-1s`, last 12 months | 14.2 M records, 0.79 GB | 11.4 M records, 0.64 GB |
  | `statistics` | 6.0 M records, 0.48 GB | 13.3 M records, 1.07 GB |

  `ohlcv-1s` is about 20 times `ohlcv-1m` for ES and 8 times for GC, not two orders of magnitude: a bar exists only for a second with a trade. The whole 16 years for both products is 17 GB billable. Storage is therefore not the argument for on-request; the argument is that no consumer has a purpose for it yet.
- **What 1-second bars are for** — Ticks are held only for what was bought or captured, so 1-second bars serve research in periods we hold no ticks for (older history, the COVID era) at a resolution 1-minute bars can't give. For realtime they add nothing: live bars at any resolution come from the captured ticks. The design is **on request, keep what you get** (PM lean 2026-10-15): the request path fetches the units a range lacks, archives them, and keeps them. A full 16-year pull is the same path over a wide range, so it is a configuration choice, not a design change. Note that the 16 years are available only while subscribed; on-request is safe exactly as long as the subscription runs for realtime anyway.
- **Session lifecycle and maintenance** — CME Globex pauses daily (17:00–18:00 ET) and closes for the weekend. A Databento status incident shows GLBX gateways restarted "during the next session pause at 5–6 PM ET", so a disconnect in the pause is normal, not a policy we can rely on in either direction. The capture service must survive one, record it, and resume. Liveness is judged against the 221 session model: no records in an open session past a fixed threshold is a finding; no records in the pause is not. The SDK's heartbeat watchdog (interval plus 10 s) distinguishes a silent market from a dead socket.
- **Replay window and boundary trimming** — Replay (`start`) reaches back at most 24 hours, possibly less on weekends, and a replay past the window fails with `REPLAY_DATA_AGED_OUT`. Replay filters on event time, so the first replayed records can duplicate the end of the previous segment. Segments must not overlap, because 220's conflict-ignore never decides between two different units. The trim rule, comparing the last held `(event time, sequence)` per instrument, is the capture slice's to specify and test on the stand-in gateway.
- **Live definitions and symbol mapping** — Historical files map symbols in their metadata header. Live sessions instead send `SymbolMappingMsg` records in-stream (SDK `client.py`). With a parent subscription, a newly listed contract arrives as a mapping plus a definition mid-session. The live path must handle identifiers that appear mid-segment, and a definition must be stored before ticks that depend on it are projected. Whether the live gateway accepts `parent` symbology for `GLBX.MDP3` is unverified. 220's fallback (explicit raw contract symbols resolved locally from definitions) applies.
- **Supersession and what "match" means** — Historical and live records for the same interval should be identical, record for record. That is an assumption the reconciliation pass measures rather than relies on. The comparison grain is 220's ingest ledger: per instrument and session, record count, first and last event time, traded volume. A live day with recorded gaps is expected to mismatch inside the gaps and nowhere else. The design must define the outcome when history can't be taken for a day, which is the one case partial-range supersession would exist for.
- **Who consumes realtime, and how fast** — Realtime exists to execute strategies. manta-engine is the executor; its engine is undecided, and the leading candidate (NautilusTrader) has its own Databento live client. With 10 sessions per dataset, the engine can hold its own session, and trading-data's role is the durable record plus near-realtime reads for the UI and operators. That is the lighter design, and it keeps trading-data out of the execution latency path. The alternative is trading-data as the execution feed: a fan-out stream (180's deferred WebSocket) tapped after the durable write, carrying its own latency requirement. This is a PM decision, taken with manta-engine's engine choice. The capture design must not foreclose either option; the read side commits only to "readable in the database within seconds".
- **Service placement and memory** — Capture is a long-running service under the production model. Running it inside `manta-acquisition.slice` puts it under the same memory bound as the heavy minute and tick passes, where an out-of-memory kill of the group would stop capture. Its own memory need is small at `tbbo`. Placement (same group or its own) is a slice-design decision, recorded with evidence. A capture outage is recovered by replay and history either way, so this is about avoiding needless gaps, not about losing data.
- **Assumptions taken until the subscription** — Each is listed in the cutover slice's first-day checklist and the work proceeds past it:
  - plan-included history prices at $0 in `get_cost` (220 Technical Considerations); if not, the spend guard and the about-$5-a-week figure above apply;
  - the live gateway accepts `parent` symbology for `GLBX.MDP3`;
  - replay reaches 24 hours back on weekdays;
  - the gateway's default slow-reader behaviour, and whether it ever disconnects a slow reader (moot under `warn` at `tbbo`'s rates, but recorded);
  - live and historical records for the same interval are identical;
  - the stand-in gateway's protocol matches the real gateway's for every message the capture path reads.

  The last is the real risk of building first. It is bounded because the stand-in is written from the SDK's own protocol code and exercised by the SDK's own client.
- **Testing strategy** — Unit tests run over real DBN files: the local ES files, and the DBN project's sample files for `ohlcv-1s`, `ohlcv-1m`, `statistics` and `definition` (verify each at slice design; a fixture in an invented format is a false pass). Integration tests use a throwaway tick database on the test cluster. Capture tests run the real SDK client against the stand-in gateway with injected faults, at accelerated replay speed so a session's worth of records runs in seconds. The reconciliation pass is a function of (manifest, provider results) → (new manifest, writes), as 220's passes are.

## Anticipated Slices

Provisional, in build order; only the last needs the subscription.

- **Stand-in live gateway and live adapter** — The live half of the provider seam in 220's adapter: session, subscription, records out, control and error records surfaced as typed events. Built with the stand-in gateway that replays local DBN files over the Live protocol with fault injection. The new delivery mode in the manifest.
- **Capture service and segments** — The streaming service: segments to the archive, segment rotation, reconnect with replay, boundary trimming, gap intervals with causes, `warn` slow-reader handling, survival of the daily pause. Tested end to end on the stand-in.
- **Live projection** — Continuous ingest of open and closed segments through 220's worker, live definitions and mid-session mappings, projection lag as a reported quantity, the latency target.
- **Daily reconciliation and gap fill** — The first tick timer: history for each available session, live-versus-history comparison per instrument and session, supersession, gap fill past the replay window, results in the manifest and health.
- **Operator, health and API parity for live data** — Provenance per interval (live, reconciled, gap, filled), "data through" per instrument, capture liveness judged against the session model, overview and API exposure. Answers the standing API question for live, including whether a stream endpoint is needed (per the consumer decision).
- **Bar and statistics history** — `ohlcv-1m`, `ohlcv-1s` (on request) and `statistics` as archive schemas with their own tables, the request-driven fill for `ohlcv-1s`, and the open-interest roll rule. Independent of the live slices; can be built in any position before the cutover.
- **Production wiring and subscription cutover** — Install the capture service and the reconciliation timer, enrol the live archive in backup, update the runbook. Then one cutover script: subscribe, run the first-day assumptions checklist, take the subscription-month history (the included 12 months of `tbbo`, `ohlcv-1m`, `statistics`), start capture, and report.

## Related Work

- **220-arch.data-acquisition-futures-tick-primary-focus.md** (complete) — the historical pipeline this initiative extends. Specifically: "Pass form for historical, streaming form reserved", "Realtime paths" (Path A, recent history assembled from capture; Path B, from the historical API; this design uses capture for the live window and the historical API for the record), "Every row knows its unit" (supersession), "Delivery mode and retention", "Cadence", and "Sourcing sequence and plan".
- **220-slices.data-acquisition-futures-tick-primary-focus.md** — 221 (CME session model), 222 (tick storage and manifest), 224/225 (acquisition and ingest passes), 226 (the proof's measured rates and capacity), 229 (roll rules), 231 (futures API), 234 (production wiring, manual cadence).
- **226-analysis.tick-proof-go-no-go.md** — bytes per record, ingest rate, capacity projection, and the `tbbo` tier decision.
- **180-arch.data-serving.md** — request/response serving and the deferred WebSocket item.
- **940-analysis.nautilustrader-evaluation.md** — manta-engine's likely engine and its own Databento live adapter, which drive the execution-feed decision.
- **000-process-journal.md** — 20260823 (production form; live capture as the first `Type=simple` source) and 20260824 (realtime is the end state; streaming, never tight polling; Kalshi near-real-time matures alongside).
- **900-slices.foundation-cleanup.md** — 233 (cross-source arbitration, `manta-acquisition.slice` memory bound) and the shared pass-framework extraction future item, which the reconciliation pass may make the third copy of.
- **Databento sources** — `databento-python` 0.87.0 (`live/client.py`, `live/session.py`, `live/gateway.py`, `common/cram.py`), the `dbn` record and enum definitions, the 2025-02-16 live connection-limits blog post, and the GLBX status incident on gateway restarts in the session pause. The docs site is client-rendered and unreadable by fetch; verify against SDK source.
