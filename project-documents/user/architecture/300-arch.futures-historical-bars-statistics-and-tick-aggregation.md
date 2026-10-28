---
docType: architecture
layer: project
project: trading
archIndex: 300
component: futures-historical-bars-statistics-and-tick-aggregation
parent: 001-initiative-plan.trading.md
dependencies:
  - 220-arch.data-acquisition-futures-tick-primary-focus.md
  - 180-arch.data-serving.md
  - 140-arch.data-quality-operations.md
relatedSlices: []
riskLevel: medium
dateCreated: 20261028
dateUpdated: 20261028
status: not_started
---

# Futures Historical Bars, Statistics and Tick Aggregation Architecture

## Overview

Initiative 220 built and proved the futures tick pipeline, but on two free-credit files, and it has never bought anything. Production holds ES from 2024-08-30 to 2024-12-31 (64 sessions) and GC from 2024-11-04 to 2024-12-31 (41 sessions), both at `tbbo`. That is enough to prove a pipeline and not enough to research with. This initiative makes the futures history usable: deep bar history, open interest, one bars read path with a named source, and checks that say whether what is stored is complete.

The shape of what Databento sells decides the design. Verified on the pricing page on 2026-10-28, the Standard plan ($199/month) includes `ohlcv-1s/1m/1h/1d`, `definition`, `statistics` (open interest, settlement) and `status` for 16+ years, and `trades`, `tbbo`, `bbo` and `mbp-1` for the last 12 months only. Ticks therefore cover a rolling year plus the files we already hold. Bars and statistics cover the whole history. Research that reaches back past a year can only be served from the bar and statistics schemas.

**Scope:** the bar and statistics schemas as archive units with their own storage; the open-interest roll rule; tick-derived aggregates only if a measured need appears; completeness checks for every futures tier; serving of the new data; and a small real purchase that proves 220's purchase path. It is the part of 280's original scope that does not depend on a live feed, moved here on 2026-10-28.

**Motivation:** the primary research target is futures, and research needs history before it needs a live feed. Today bars exist only where ticks do, and a range read scans every tick in it, session by session, nothing reaches back past what was bought, `mt data health` and `accounting` cannot say whether a futures range is complete, and the one thing 220 reserved for later (open interest) is not built.

## Design Goals

- **Deep history for ES and GC** — Bars from the start of the dataset (2010-06-06) and open interest and settlement over the same range, stored in the project's own database and kept.
- **One bars read path that says where its bars came from** — A consumer asks for bars and gets them. The response names the source (provider bars or tick-derived), so a spliced series is never silent.
- **Every tier answers "what do I have, and is it complete"** — Ticks, bars and statistics each get a completeness verdict against the CME calendar, in the same surfaces operators already read.
- **A purchase path proven with real money** — A small real purchase, checked against the invoice, so the first large purchase is not the first test.
- **Open interest available as a roll rule** — The rule 220 evaluated and deferred, built on stored `statistics`.

## Architectural Principles

- **Provider bars are a tier of their own** — `ohlcv-1m`, `ohlcv-1s` and `statistics` are new archive schemas, each projected into its own table with contract identity. Never into `minute_ohlcv` (equities, symbol-keyed) and never into the tick hypertable. This repeats 220's rule that a futures contract is the instrument and a continuous series is a rule.
- **File-first, as in 220** — Raw DBN is archived and recorded in the manifest before anything is projected. Acquisition and ingest stay separate and idempotent; a database rebuilt from the archive holds the same projection.
- **Cost is a first-class input, never a surprise** — Every request that could cost money is preceded by an estimate and passes the spend guard, and the guard counts marginal spend only: a pull the plan includes is marked so, and costs the guard nothing. The PM sets the ceilings.
- **A tier ships with its completeness check** — The slice that adds a schema also extends `status`, `coverage`, `health` and `accounting` for it, and is not done until they can say whether its range is complete. A tier that cannot be verified is not delivered.
- **Completeness comes from the manifest and the ledger, never from an aggregate** — Same rule as 220. A continuous aggregate answers a read; it never answers "what do I have", and nothing it derives informs acquisition (journal 20260725).
- **Never splice tiers silently** — Where provider bars and tick-derived bars could both answer, the design names which wins (Technical Considerations, "Which source serves bars") and the response says so. Tick-derived bars are checked against provider bars where both exist, by measurement.
- **Everything is built and tested before it costs money** — Code runs against real DBN files (the local files and the DBN project's samples), never invented fixtures. Real spending starts with a bounded test purchase.
- **Every feature answers the API question** — Whether it belongs in `/api/v1/futures/*`, with human and AI documentation, is decided per feature, not at the end.

## Current State

- **Ticks:** the pipeline works end to end (adapter, archive, manifest, ingest, calendars, roll rules for calendar and volume, `/api/v1/futures/*`). ES and GC are configured at `tbbo`, measured at 28.6 bytes per row compressed and about 4.7 GB per year for both. The pass has only run on the two free-credit files; the spend ceiling is $0.50.
- **Bars:** the futures bars routes compute bars at read time. `read_bars` runs `time_bucket` over one session's `tick_trade` rows, so bars exist only where ticks do, and a range read is one bounded scan per session. There are no continuous aggregates over `tick_trade`, which is an integer-nanosecond hypertable.
- **Schemas:** the schema enum knows `trades`, `tbbo`, `mbp-1` and `definition`. `ohlcv-*` and `statistics` cannot be archived today.
- **Open interest:** evaluated in 220 and deferred. For ES, Databento's `.n.0` matched `.v.0` on all 11 rolls from 2023 to 2025; GC differs.
- **Checks:** `mt data health` reports the tick database's state and retention. `accounting` and the status coverage aggregates map only `minute_ohlcv` and `daily_ohlcv`. Tick completeness is a manual raw-count proof (`mt data tick coverage`).
- **Consumers:** trading-ui plots equities from the API. Neither it nor manta-engine calls the futures routes yet.
- **Cost of history, usage-based list prices** (free metadata calls, 2026-10-28, `GLBX.MDP3` parent symbols, 2010-06-06 to 2026-10-15). All of it is included in the Standard plan.

  | Schema | ES | GC |
  |---|---|---|
  | `ohlcv-1m` | 8.7 M records, 0.49 GB, $31.86 | 15.9 M records, 0.89 GB, $57.88 |
  | `ohlcv-1s`, all history | 170.6 M records, 9.56 GB, $622.97 | 133.8 M records, 7.49 GB, $488.41 |
  | `ohlcv-1s`, last 12 months | 13.8 M records, 0.77 GB, $50.26 | 10.6 M records, 0.59 GB, $38.77 |
  | `statistics` | 6.0 M records, 0.48 GB, $0.45 | 13.4 M records, 1.07 GB, $1.00 |
  | `definition` | 0.19 M records, $0.16 | 3.1 M records, 1.62 GB, $2.56 |

## Envisioned State

The archive holds `ohlcv-1m`, `ohlcv-1s` (in the range chosen by purpose) and `statistics` units for ES and GC, each recorded in the manifest with its cost, ingested into its own table, and counted by the same completeness vocabulary 220 uses. A single bars read names its source tier. `mt data tick coverage`, `health`, `accounting` and `overview` report every futures tier against the CME session calendar, and a gap is a named finding, not a surprise in a backtest. The open-interest roll rule reads stored `statistics`. 220's purchase path has spent real money once, and the invoice agreed with the manifest.

## Technical Considerations

- **What a "day" is** — Two different days run through this work, and they must not be mixed. The archive unit is one UTC calendar day of a request, because that is the grain Databento splits files at. The trading day is the CME session: it opens in the evening of the previous calendar date, runs through the daily maintenance break (17:00–18:00 ET) and closes the next afternoon, with the Sunday-evening open and the holiday exceptions of the 221 session model. A session therefore spans two UTC dates and is covered by two units, and a unit can hold the end of one session and the start of the next. Coverage, the ingest ledger, the `1d` bar and every completeness verdict are per session; only acquisition and the manifest are per UTC day.
- **Which source serves bars** — Decided here, so the read path and the serving slice start from one rule. Provider `ohlcv-1m` is the default source for 1m and every coarser bar, and coarser bars are rolled up from it on the 221 session model. Provider `ohlcv-1s` serves 1s. Provider `ohlcv-1h` and `ohlcv-1d` are never served as our 1h and 1d: they are bucketed by their own clock, not the CME session `read_bars` uses for `1d`, so serving them would silently change what a daily bar means (their bucketing is verified at slice design). We may use their 1h and 1d at some point, and this is revisited then (PM, 2026-10-28). Tick-derived bars are used only where no provider bar unit exists for the range, and the response names the source. Provider bars reach back to 2010; tick-derived bars exist only for the rolling year and the held files.
- **What 1-second bars are for** — Ticks are held only for what was bought, so 1s bars serve research in periods with no ticks. Storage is not the argument: ES and GC `ohlcv-1s` over all history is about 17 GB billable, against $1,111 at list price outside a subscription. The open question is purpose, and the `ohlcv-1s` slice is gated on it. The PM leaned (2026-10-15) toward pulling only for requested intervals and keeping what was fetched; pulling everything and fetching on request are the same path over different ranges.
- **Tick-derived continuous aggregates, conditional** — Provider `ohlcv-1m` and `ohlcv-1s` already cover the tick window, and the plan includes them, so this is the last bars work to build, not the first. It is built only if provider bars arrive late or a measured read latency or a granularity provider bars lack needs it (PM, 2026-10-28: small, but its importance depends on how fast the other bars arrive). If built, three constraints from the 300 review apply and are tested at slice design, not assumed: `read_bars` picks open and close with an ordered `array_agg` over `(ts_event, sequence, sequence_ordinal)` because trades share nanoseconds, and a continuous aggregate may not reproduce that ordering; intraday buckets are clipped to the session open and `1d` is the CME session, which a fixed `time_bucket` cannot express across DST; and ingest supersession deletes by unit on compressed chunks, which invalidates an aggregate. Intervals are set explicitly and refresh horizons stated (journal 20260725). Tick-derived bars stay the on-demand parity check and the fallback for held ticks with no provider bars.
- **Parity between tiers** — Provider bars are trade-only bars per second or minute. Tick-derived bars from `tbbo` trades should match them for the same instrument and interval. Parity is measured on a window where we hold both, not assumed. The 2024 files give such a window.
- **Completeness of sparse bars** — An `ohlcv-1s` bar exists only for a second with a trade, so a row count per session proves nothing, and a quiet overnight session is not a hole. The ledger proves a load, not that the market data is whole. So bar completeness has two parts, both committed here. **Load-complete:** the unit is verified and its counts and first and last event time match the manifest. **Content-complete:** where both tiers exist, a session's `ohlcv-1m` volume agrees with `statistics` cleared volume, `ohlcv-1s` volume summed to minutes agrees with `ohlcv-1m`, and a provider day condition of `degraded` or `missing` is shown as such. A session that fails either part is a named finding. Where only load-completeness is possible the surface says so.
- **Statistics** — Contents (settlement, cleared volume, open interest, session statistics) and publication timing (CME publishes session D's statistics during D + 1, pinned in 220's constants) set when a session's statistics are complete. Because they arrive after the session, a statistics record's event time falls in a later UTC-day unit than the session it describes. The session is assigned from the record's reference time and trading date, never its event time, so the per-unit session ledger cannot be applied to statistics as it is to ticks. Revisions are a second, record-level supersession alongside the manifest's unit-level one: a correction can arrive in a later unit, and the winner is chosen by the record's update action and receive time. Both rules are confirmed against the DBN statistics record definition at slice design, and the roll rule reads only resolved values.
- **Open-interest roll** — Needs per-contract daily open interest from `statistics`, aligned to the 221 session model. ES `.n.0` matched `.v.0` on all 11 rolls; GC does not, so GC is where the rule earns its place. The rule vocabulary must match Databento's continuous-symbol conventions closely enough that a request in either resolves to the same contract on the same day.
- **Purchase test** — A bounded real purchase (about $25 per the PM), usage-billed because it is bought outside any subscription, so this is where the invoice reconciliation holds. The PM raises the ceilings for that run, the actual charge is compared with the estimate and the manifest, and what is bought is kept. Candidates and sizes are chosen at slice design from the table above.
- **The rolling tick window** — In-plan `trades`/`tbbo` cover only the last 12 months, so each day without a subscription moves the oldest in-plan day out of the plan. This is a known cost of delay (PM, 2026-10-28), not a blocker. Selected older history can be bought outside the plan as budget allows.
- **Serving** — `/api/v1/futures/products/{product}/bars` and the contract routes serve tick-derived bars today. New tiers change what "bars" means there. The response must name its source tier, and the change is documented in `docs/api/reference.md`, `agents.md` and the OpenAPI file. Open interest and settlement need routes or an explicit decision not to serve them.
- **Surface coverage** — `COVERAGE_SOURCE_TABLE` and the status coverage logic map only minute and daily tables; tick and the new tiers need their own definition of "source edge" and "caught up". The slice that adds a tier extends these surfaces in place; no parallel set.
- **Cost accounting** — The 30-day spend guard sums every manifest request plus every job the provider lists that the manifest lacks, and the request row has no way to say a pull is plan-included. Recording plan-included pulls at list price would stop the history acquisition at the first large pull (ES and GC `ohlcv-1s` alone is $1,111 against a $0.50 ceiling), and raising the ceilings would stop the guard protecting the usage-billed purchases outside the plan. So each request carries a plan-inclusion marker, set from one table (schema to included history) kept in configuration, and the guard counts marginal spend only. List-price estimates stay on the row for information. Invoice reconciliation applies to usage-billed requests.
- **Generalising the archive beyond tick tiers** — Adding `ohlcv-*` and `statistics` to the schema enum is not enough: the enum feeds tick-tier logic. Tier order drives the supersession rank, the stored-tier set decides what ingest selects, the preflight judges a tier-plus-definition bundle, and the ingest copy is written for `tick_trade`. The architecture's position: the archive and manifest become schema-family aware, with families for tick tiers, bars, statistics and definitions; supersession applies within a family only (a bar unit never supersedes a `tbbo` unit); ingest selects a projector per schema from one registry, replacing the single hard-wired writer; and the bundle rule stays a tick-tier rule. The `tick` migration track, `MT_TICK_*` settings and `mt data tick` namespace keep their names, so existing surfaces do not move (220, Technical Decision 3); a rename is out of scope.
- **Contract identity needs definitions back to 2010** — Bar and statistics records carry only an instrument identifier, and an identifier is mapped to a contract through `tick_definition` validity windows. So `definition` history for the same range is a prerequisite of bar and statistics ingest and of any roll rule that resolves contracts, and the history slices acquire it. It is priced in the table above (GC: 3.1 M records, 1.62 GB billable) and is a storage cost in `tick_definition` that slice design sizes.
- **Manifest scale** — The manifest unit is one UTC day of a request. Full history of `ohlcv-1m`, `statistics` and `definition` for two products is on the order of tens of thousands of units and files, about a hundred times today's, and more with `ohlcv-1s`. Per-unit timings were measured in 226; manifest-scale queries, preflight and status over that many rows have not been, and one measurement precedes the history acquisition slice.

## Anticipated Slices

Provisional, in rough build order. Only the purchase test spends money before a subscription.

- **Archive generalisation** — Schema families, per-schema projector registry, family-scoped supersession, and the plan-inclusion marker with its configuration table and guard change. Everything below builds on it.
- **`ohlcv-1m` with definition history** — The schema, its table, `definition` back to 2010, contract identity, and completeness for the tier (load-complete and the volume checks that need only `ohlcv-1m`), built on DBN sample files.
- **`statistics` and the open-interest roll rule** — The schema, session assignment and revision rules, completeness for the tier, the cross-tier volume check against `ohlcv-1m`, and the rule evaluated against calendar and volume for ES and GC.
- **Purchase test** — A small real purchase through the pass, invoice reconciled, ceilings raised once by the PM.
- **`ohlcv-1s`** — The schema and its completeness, once its purpose is stated.
- **Futures completeness surfaces** — Tick, bar and statistics verdicts together in `health`, `accounting`, `overview` and the status coverage aggregates, and the manifest-scale measurement.
- **Serving the new tiers** — Bars with a named source under the rule above, open interest and settlement through `/api/v1/futures/*`, with documentation.
- **History acquisition** — Taking the chosen ranges through the same pass, within the budget set at the time.
- **Tick-derived aggregates (conditional)** — Only on the conditions in Technical Considerations.

## Related Work

- **220-arch.data-acquisition-futures-tick-primary-focus.md** (complete) — the pipeline this extends: adapter, archive, manifest, ingest, session model, roll rules, "Completeness is answered from the manifest and the raw table", "A futures contract is the instrument", "Active contract and roll methods".
- **220-slices.data-acquisition-futures-tick-primary-focus.md** — 221 (CME session model), 222 (tick storage and manifest), 224/225 (acquisition and ingest), 226 (measured rates and the `tbbo` decision), 229/232 (roll rules), 231 (futures API).
- **226-analysis.tick-proof-go-no-go.md** — bytes per record, capacity, the `tbbo` decision.
- **280-arch.futures-realtime-data-databento-live.md** — now live-only. Its former "Bar and statistics history" slice and subscription-month history moved here (280's architecture amended 2026-10-28).
- **180-arch.data-serving.md** — the serving layer these tiers extend.
- **140-arch.data-quality-operations.md** — the quality surfaces (`data_status`, coverage aggregates, health) the checks extend.
- **000-process-journal.md** — 20260719 (chunk intervals from wall-clock span), 20260725 (aggregate intervals set explicitly; derived data must not inform acquisition), 20260823 (production form).
- **Databento pricing page** (`databento.com/pricing#cme`, read 2026-10-28) — the plan's included schemas and history. The docs site is client-rendered; the pricing page needs a browser to render.
