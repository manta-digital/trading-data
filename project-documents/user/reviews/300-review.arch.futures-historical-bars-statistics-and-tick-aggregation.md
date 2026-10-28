---
docType: review
layer: project
reviewType: arch
slice: futures-historical-bars-statistics-and-tick-aggregation
targetKind: arch
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md
aiModel: claude-opus-5-5
aiProfile: sdk
status: complete
dateCreated: 20261028
dateUpdated: 20261028
reviewedSha: 565f841b3dc1a49a423241f9ea8b384a1ae8ae1d
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 9
turns: 10
promptTokens: 495178
cachedTokens: 405047
cacheWriteTokens: 90113
completionTokens: 9734
durationSeconds: 103.6
costUsd: 0.997417
costBasis: provider
squadronVersion: 0.24.6
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "The spend guard and cost accounting don't work for subscription-included history"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F002
    severity: concern
    category: abstraction
    summary: "\"Add to the schema enum\" understates how tick-specific the manifest and ingest are"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#anticipated-slices"
  - id: F003
    severity: concern
    category: antipattern
    summary: "Tick-derived continuous aggregates are the heaviest option and largely duplicate provider bars"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F004
    severity: concern
    category: technology
    summary: "Continuous aggregates can't reproduce the current bars' semantics"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F005
    severity: concern
    category: consistency
    summary: "Completeness ships after the schemas, contradicting the doc's own principle"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#anticipated-slices"
  - id: F006
    severity: concern
    category: completeness
    summary: "The sparse-bar completeness definition is the hardest part and is left unspecified"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F007
    severity: concern
    category: feasibility
    summary: "Statistics don't fit the per-day unit, session ledger and unit-level supersession"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F008
    severity: concern
    category: completeness
    summary: "Contract identity for 2010+ bars needs `definition` history that no slice acquires"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#anticipated-slices"
  - id: F009
    severity: concern
    category: consistency
    summary: "Which bar source wins is deferred, though a principle says the design names it"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#technical-considerations"
  - id: F010
    severity: note
    category: feasibility
    summary: "Archive unit count at day grain over 16 years"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md#envisioned-state"
  - id: F011
    severity: note
    category: dependencies
    summary: "Frontmatter dependencies don't match the surfaces the doc extends"
    location: "project-documents/user/architecture/300-arch.futures-historical-bars-statistics-and-tick-aggregation.md"
---

# Review: arch — initiative 300

**Verdict:** CONCERNS
**Model:** claude-opus-5-5

## Findings

### [CONCERN] The spend guard and cost accounting don't work for subscription-included history

The doc says "every request that could cost money … passes the spend guard" and "the manifest records what the estimate says" for plan-included ranges ("Cost accounting"). In the code, the 30-day ceiling (`MT_TICK_SPEND_30D_CEILING_USD`, `TICK_SPEND_WINDOW`) adds up every manifest request plus every job Databento lists that the manifest doesn't hold. If plan-included pulls are recorded at list price, the "History acquisition" slice hits the guard at once. ES+GC `ohlcv-1s` over all history alone is about $1,111 at list, against a current 30-day ceiling of $0.50. That leaves two options, and both break something:
- Raise the ceilings to thousands of dollars. The guard then stops protecting the usage-billed `trades`/`tbbo` purchases outside the plan, which the doc still expects ("Selected older history can be bought outside the plan").
- Record the plan-included pulls at $0. The "invoice agreed with the manifest" goal can then only ever hold for the purchase test.

`tick_request.estimated_cost_usd`/`actual_cost_usd` also has no way to mark a request as plan-included. This decision carries weight for both the guard and the acquisition slice, and needs settling here: probably a plan-inclusion attribute on the request, with the guard counting only marginal spend.

### [CONCERN] "Add to the schema enum" understates how tick-specific the manifest and ingest are

The "Bar and statistics schemas" slice is described as "`ohlcv-1m`, `ohlcv-1s` and `statistics` in the schema enum, archive, manifest and their own tables." In `data/tick/constants.py`, though, the enum feeds tick-tier logic:
- `TICK_TIERS` order drives `TICK_TIER_RANK`/`tier_rank_sql`, the unit supersession rank (a higher tier supersedes a lower one for the same day).
- `STORED_TIERS` decides what ingest selects (`ingest_select.py`, `ingest_worker.py`).
- The preflight judges a "tier + `definition`" bundle.
- `ingest_rows.COPY_SQL` is hard-wired to `tick_trade`.

`ohlcv-1m` is not a tier that supersedes `tbbo`, and `statistics` doesn't fit the "tier + companion" bundle. The doc doesn't say:
- whether supersession is limited to a schema family;
- how ingest picks a projector per schema (it needs a schema-to-writer registry);
- whether the `tick_*` manifest, `MT_TICK_*` settings and `mt data tick` commands become generic futures-archive concepts or keep the `tick_` name while holding bars.

Without that, the slice will either fill `constants.py` with special cases or need a rework halfway through.

### [CONCERN] Tick-derived continuous aggregates are the heaviest option and largely duplicate provider bars

The doc admits the aggregate "cannot be the primary bars tier" and that provider `ohlcv-1s`/`ohlcv-1m` cover the same tick window: 12-month `ohlcv-1s` costs about $89 at list and is included in the plan. Even so, it plans continuous aggregates over `tick_trade`, a switch of `read_bars`, and a third bar source the read path has to arbitrate. The only reason given, "granularities provider bars do not give," is never named. Coarser bars can come from `ohlcv-1m`, as the doc itself suggests.

The motivating claim is also overstated. `read_bars` (`tick_reads.py:145`) is bounded per session (`TickWindow`, `ts_event BETWEEN open_ns AND close_ns`), so a range read is many per-session scans, not one full rescan.

Recommendation: cut the aggregate slice, or defer it until a measured read latency or a specific sub-minute granularity needs it. Keep tick-derived bars as the on-demand parity check and the fallback for held ticks with no provider bars.

### [CONCERN] Continuous aggregates can't reproduce the current bars' semantics

If the aggregate stays, three problems remain:
1. **Open and close.** `read_bars` picks open and close with `array_agg(price ORDER BY ts_event, sequence, sequence_ordinal)` because "several trades can share a nanosecond". Continuous aggregates don't allow ordered-set `array_agg`, and `first()`/`last()` order by one column, so ties at the same nanosecond resolve differently. The "parity against the raw ticks" in the slice will fail by construction, or will need a composite ordering key added to `tick_trade`.
2. **Bucket alignment.** Intraday buckets are clipped to the session open (`GREATEST(time_bucket(...), open_ns)`) and `1d` is the CME session. Neither can be expressed as a fixed `time_bucket` in a continuous aggregate. The 17:00 CT open moves against UTC with DST, so 1d bars and any bucket that doesn't divide the hour can't be pre-aggregated.
3. **Supersession.** Ingest runs `DELETE FROM tick_trade WHERE unit_id = …` on compressed chunks. That invalidates the aggregate, and the doc gives no refresh horizon or cost for it.

The doc's only constraint is "integer-now function"; `tick_now_ns` already exists in `tick_007`.

### [CONCERN] Completeness ships after the schemas, contradicting the doc's own principle

The Architectural Principles say "A tier ships with its completeness check — No new schema is done until `status`, `coverage`, `health` and `accounting` can say whether its range is complete." The Anticipated Slices then list "Bar and statistics schemas" and "Completeness for every futures tier" as separate slices, in that order. The doc should either fold each tier's completeness into the slice that adds it, or drop the principle.

### [CONCERN] The sparse-bar completeness definition is the hardest part and is left unspecified

"Completeness of sparse bars" proposes "manifest state plus the ledger's counts and first and last event time" and calls it "the hardest part", without resolving it. For ticks, the ledger is checked against the provider's record count and against `tick_trade`, so it proves the load, not whether the market data is complete. For `ohlcv-1s`, a session with 40% of its seconds missing looks the same as a quiet session.

The data needed for an actual check is already in this initiative:
- the session sum of `ohlcv-1m` volume against `statistics` cleared volume;
- `ohlcv-1s` volume summed to minutes against `ohlcv-1m`;
- `tick_day_condition` (`degraded`/`missing`).

The architecture should commit to a cross-tier volume reconciliation like this, or state that bar completeness means "load-complete" only. The doc treats completeness as a deliverable for every tier, so this can't be left to slice design.

### [CONCERN] Statistics don't fit the per-day unit, session ledger and unit-level supersession

Session D's statistics are published during D+1. A statistics record's `ts_event` therefore falls in a later UTC-day unit than the session it describes, which is identified by `ts_ref`/the trading date. `tick_ingest_ledger` assigns `session_date` per (unit, instrument), the way ticks are mapped. Applied to `ts_event`, it puts settlement and open interest on the wrong session.

"Revisions may supersede earlier values; provenance per figure is kept" is a second, record-level supersession model, alongside the unit-level `superseded_by_unit_id` the manifest has. A correction can arrive in a later day's unit, and the doc doesn't say how it is resolved (latest `ts_recv`? `update_action`?). The open-interest roll rule reads these values, so the session assignment and revision semantics belong in the architecture.

### [CONCERN] Contract identity for 2010+ bars needs `definition` history that no slice acquires

The bar and statistics tables are "projected … with contract identity". OHLCV and statistics records carry only `instrument_id`, and `instrument_id` is mapped to a contract through `tick_definition` validity windows. The "History acquisition" slice lists `ohlcv-1m`, `statistics` and `ohlcv-1s` but not `definition`. The cost table does price `definition` (GC: 3.1 M records, 1.62 GB), but the doc never says it is required back to 2010.

Two consequences:
- If 2010–2024 definitions are missing, bar ingest can't attribute rows to contracts, and the open-interest roll rule can't resolve contracts.
- The 1.62 GB of GC definitions ingested into `tick_definition` is a sizing cost the doc doesn't account for.

### [CONCERN] Which bar source wins is deferred, though a principle says the design names it

The principle "Never splice tiers silently" says "the design names which wins". "Which tier serves bars" then says "Slice design settles which granularities come from which source." That is the core read-path decision behind "One bars read path", and the serving and aggregate slices both depend on it.

Provider `ohlcv-1h`/`ohlcv-1d` add a trap the doc doesn't mention: they are bucketed by UTC hour/day, not by the CME session that `read_bars` uses for `1d`. Serving provider `ohlcv-1d` as "1d" would silently change what a daily bar means. The architecture should state the rule. For example: provider `ohlcv-1m` is the default from 1m up; coarser bars are rolled up from it on the 221 session model; provider `ohlcv-1h`/`1d` are never served; tick-derived bars are used only where no provider bar unit exists.

### [NOTE] Archive unit count at day grain over 16 years

`tick_archive_unit` is "one UTC day of a request". Full-history `ohlcv-1m` + `statistics` + `definition` for two products comes to roughly 30k+ units and files, plus `ohlcv-1s` if pulled. That load on the manifest, preflight and status surfaces is probably fine, but it's about 100× today's 105 sessions. Slice 226's proof measured per-unit timings, not manifest-scale queries; worth one measurement before the acquisition slice.

### [NOTE] Frontmatter dependencies don't match the surfaces the doc extends

The doc extends `data_status`, the coverage aggregates and `health` from `140-arch.data-quality-operations.md`, but 140 appears only under Related Work. `900-arch.foundation-cleanup.md` is listed as a dependency, yet nothing in the body uses it.

### Run Digest

- Response length: 11710 chars
- Response is newline-free: no
- Tool calls made: 9
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 10
- Tokens — prompt / cached / cache write / completion / reasoning: 495178 / 405047 / 90113 / 9734 / not computed
- Duration: 103.6 s
- Cost: $1.00 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
