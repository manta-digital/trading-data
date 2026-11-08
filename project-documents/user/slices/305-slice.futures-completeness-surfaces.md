---
docType: slice-design
slice: futures-completeness-surfaces
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 302
- 303
interfaces:
- 307
dateCreated: 20261108
dateUpdated: 20261108
status: not_started
turns: 34
promptTokens: 3283443
cachedTokens: 3119788
cacheWriteTokens: 163593
completionTokens: 33523
durationSeconds: 368.4
costUsd: 2.603747
usageRounds: 1
pricedRounds: 1
usageByModel:
  claude-opus-5-5:
    rounds: 1
    costUsd: 2.6037467000000003
    completionTokens: 33523
---

# Slice Design: Futures Completeness Surfaces

## Overview

Slices 302 (`ohlcv-1m` bars) and 303 (`statistics`) each shipped their tier's completeness check, but only in `mt data tick status` and `mt data tick coverage`. The surfaces operators actually read every day (`mt data health`, `mt data accounting`, `mt data overview` and the `tick` block of `/api/v1/status`) still know only the tick tier. A product that wants bars and statistics back to 2010 can be half bought and nothing outside `tick status` says so.

This slice brings every futures family into those surfaces, in place, through one function that `status` already uses. It defines, per family, the **source edge** (the last instant the provider can have published that family's data) and **caught up** (every wanted session up to that edge is complete, or a provider hole). It wires the cross-tier layer: 303's traded-volume-against-cleared-volume check runs in health and accounting, and the `ohlcv-1s`-summed-to-minutes check is registered but inactive until that schema exists. Finally, it measures status, accounting, health, the preflight and a steady-state pass against a manifest at the full size 307 will reach (about 30,000 units), with fixed bounds, before 307 makes that size real.

It buys nothing and changes no ingest or acquisition behaviour.

## Value

- **One answer in every surface.** `mt data health` fails on a futures defect (a failed unit, more traded than cleared), `accounting` counts sessions per family, and `/api/v1/status` gives trading-ui and trading-engine the same figures. Nobody has to know that `tick status` exists to learn that ES statistics stop in 2016.
- **307 can be verified.** History acquisition takes the full range through the pass. Its findings are only as good as the surfaces that report them, and the plan puts 305 before 307 for that reason.
- **No surprise at 100 times today's manifest.** Status, accounting and health currently judge about 200 units. The measurement proves they answer at about 30,000, or fixes the query that does not, before the purchase that creates them.
- **A defect is told apart from work not yet done.** A product configured but not yet bought shows as not caught up (informational). A failed unit or an over-count fails health. Health stays a signal someone can act on.

## Technical Scope

**Included**

- `FAMILY_PUBLICATION_LAG` (each family's publication lag in sessions) and `source_edge()`, which computes the edge from it: the futures counterpart of `COVERAGE_SOURCE_TABLE` (D1, D2).
- Caught up per family, judged up to that family's edge (D2).
- One per-family report, extracted from `tick_status_build._families`, used by `status`, `health`, `accounting` and `/api/v1/status` (D3).
- The cross-tier layer: traded volume against cleared volume, active; `ohlcv-1s` summed to minutes, named as inactive (D4).
- `mt data health` futures rows: one per product and wanted family, counted only for defects (D5).
- `mt data accounting` futures section: one line per product and family (D6).
- `mt data overview`: one freshness line per family (D7).
- `/api/v1/status` `tick` block: per-family records, additive, documented for humans and agents (D8).
- The manifest-scale measurement: a load test at 307's full shape, fixed bounds, and a recorded note (D9, D10).

**Excluded**

- Continuous aggregates over any tick table, and any new entry in `COVERAGE_SOURCE_TABLE` (D1).
- Any purchase or universe change (304, 307).
- Serving bars, open interest or settlement (306).
- The `ohlcv-1s` schema, table, reader and its "no ticks wanted" marker (future work in the slice plan).
- Tick-derived against provider bar parity (306 measures it when it retires the read-time tick bars).
- `/api/v1/health` and `/api/v1/overview`. They keep their 231 shape: health there reports the tick database's state, and the overview's tick line stays CLI-only (230 TD7). The status block is the API's completeness surface.
- Changing whether the `tick database` row counts. 234 TD6 made it informational and said counting it is a PM decision; this slice leaves it alone.

## Dependencies

### Prerequisites

- **302 (ohlcv-1m and definition history), merged:** `SchemaFamily.BAR_1M`, `tick_bar_ledger`, `FAMILY_LEDGER`, `HistoryWant`, per-family `family_scope` and `FamilyStatus`.
- **303 (statistics and the open-interest roll rule), merged:** `SchemaFamily.STATISTICS`, `statistics_verdicts` (sessions judged over their publication window), `content_findings`, `volume_check`, `classify_volume`, `STATISTICS_PUBLICATION_SESSIONS`, and the real statistics fixtures.
- **The test cluster** (`MT_TIMESCALE_TEST_URL`) for the throwaway measurement database. It has no TimescaleDB job scheduler, so the fixture compresses chunks itself.

Context for the production walkthrough only, not a prerequisite: 304 (first real purchase) configured production's `TICK_UNIVERSE` to want `definition` and `statistics` for ES and GC from 2010-06-08 to 2026-11-01, and ES `ohlcv-1m` for 2024. If its purchase has not run, production shows those families as not caught up, which is the correct report.

### Interfaces Required

- `tick_status_scope.family_wants`, `family_scope`, `_wanted_end`, `overlaps`.
- `tick_status.caught_up`, `SessionVerdict`, `TickSessionStatus`.
- `status_reads.dataset_edge` (`tick_dataset_edge.available_end`), `ledger_totals`, `shape_of`, `calendar_sessions`.
- `statistics_reads.volume_check` (303 D8), called unchanged. `checkable_sessions` is not called: the report's verdicts already say which sessions are load-complete (D4).
- `cli/tick_composed.read_line`: the bounded composed connection (`TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS` = 10 s) that health, accounting and overview already use.
- `api_server/tick_access.read_tick` and `TickBlock` (231).

## Architecture

### Component Structure

```
data/tick/family_edges.py         FAMILY_PUBLICATION_LAG; source_edge(family, ...) (new)
data/tick/family_report.py        family_reports(conn, url, entry, edge):
                                  extracted from tick_status_build._families;
                                  FamilyStatus moves here and gains
                                  source_edge, records, cross_tier;
                                  futures_completeness(conn, url, universe,
                                  progress, *, cross_tier): every product in
                                  one read; ReadProgress, the step recorder
                                  (new)
data/tick/cross_tier_checks.py    traded_vs_cleared(...);
                                  INACTIVE_CROSS_TIER_CHECKS (new)
data/tick/tick_status_build.py    _families -> family_reports
data/tick/tick_status_scope.py    _judged moves here, bounded by the family edge
data/tick/tick_accounting.py      one line per product and family
cli/commands/health_tick.py       futures completeness rows (D5)
cli/commands/accounting.py        futures section text and JSON
cli/commands/overview.py          per-family freshness lines
data/tick/status_reads.py         newest_bar_ns, newest_statistics_session
api_server/models/futures.py      TickAccountingRecord.families
constants.py                      COVERAGE_SOURCE_TABLE docstring points to
                                  FAMILY_PUBLICATION_LAG (no new entry)
test/load/test_305_manifest_scale_nfr.py   the measurement (new)
test/load/manifest_scale_seed.py  the full-shape seed (new)
docs/api/reference.md, agents.md, openapi.json   the status block
project-documents/user/notes/305-manifest-scale-measurement.md   figures (new)
```

### Data Flow

**Per product, once per surface call.** Every product is read on the one composed connection the surface already opens, which is read-only (D5, "Failure and concurrency"):

1. `dataset_edge(conn, CME_DATASET)`: one read.
2. For each wanted family (`family_wants`): `family_scope` reads the family's current units, the day conditions and the calendar's sessions, and judges each session (`session_verdicts`, or `statistics_verdicts` for statistics).
3. `source_edge(family, family_want_end, dataset_edge, calendar)` (D2) bounds the judged sessions, and `caught_up` runs over them. Each family uses its own want end.
4. Statistics only: `content_findings` over the load-complete sessions (303 D7).
5. Only when `cross_tier=True` (health, accounting and status; the API block passes False, see D8): `traded_vs_cleared` runs over the verdicts from step 2 (D4). Today that is one `volume_check` per wanted volume family (tick tier, `BAR_1M`), over the sessions that are load-complete in that family and in statistics.
6. `ledger_totals` per family for the record count.

The result is one `FamilyStatus` per family. `status` renders it in full, health turns it into one row, accounting into one line, and the API into one record. No surface reads anything the report does not.

**Overview** does not build reports. It reads one newest value per family from the ledgers (D7), as the existing `futures ticks` line reads `newest_event_ns`.

### State Management

No new tables and no migration, unless the measurement needs an index (D10). Every figure is computed on read from the manifest, the ledgers, `tick_definition`, `tick_day_condition`, `tick_dataset_edge` and, for the volume check, resolved `tick_statistics` values, as 220 requires: completeness never comes from an aggregate.

## Technical Decisions

### D1 — `COVERAGE_SOURCE_TABLE` gets no futures entries; families get `FAMILY_PUBLICATION_LAG`
Status: decided

`COVERAGE_SOURCE_TABLE` maps a coverage continuous aggregate (`minute_coverage`, `daily_coverage`) to the raw table its freshness is probed against, in the market database. The futures tiers live in the tick database, have no coverage aggregate, and must not get one (architecture principle: completeness from the manifest and the ledger, never from an aggregate; journal 20260725). An entry there would have no view to key on.

What the architecture asks for is a definition of "source edge" and "caught up" for each futures tier. That lives beside the families: `data/tick/family_edges.py` holds `FAMILY_PUBLICATION_LAG: Mapping[SchemaFamily, int]`, one entry per family with a ledger. The map holds lags only (0, 0 and 1 sessions). The rule that turns a lag into an edge is `source_edge()` (D2), and `COVERAGE_SOURCE_TABLE`'s docstring names the pair as the futures counterpart. A family added later (the `ohlcv-1s` slice) joins by one entry. A pin test asserts `FAMILY_PUBLICATION_LAG.keys() == FAMILY_LEDGER.keys()`, so a family cannot ship without a lag.

The slice plan says "extends `COVERAGE_SOURCE_TABLE`". This decision reads that as the architecture's "tick and the new tiers need their own definition of source edge and caught up", and records why the table itself is not the place.

- *Rejected: coverage caggs over `tick_bar_ledger` and `tick_statistics_ledger`.* Violates the aggregate rule, and the ledgers are small enough to read directly (D9 proves it).
- *Rejected: keying futures entries by table name in `COVERAGE_SOURCE_TABLE`.* Mixes two databases and two meanings of "source" in one map.

### D2 — Source edge and caught up per family
Status: decided

A family's value in `FAMILY_PUBLICATION_LAG` is its lag: how many sessions after session D the provider is still publishing D's data. Every family's edge is computed from **its own** want end and the dataset's available end (`tick_dataset_edge.available_end`, per dataset, as today). It never refers to another family's edge or want.

| Family | Lag (sessions) | Source edge |
|---|---|---|
| `TICK_TIER` | 0 | `min(tick want end, dataset available end)` |
| `BAR_1M` | 0 | `min(ohlcv-1m want end, dataset available end)` |
| `STATISTICS` | `STATISTICS_PUBLICATION_SESSIONS` (1) | `min(statistics want end, E)`, where E is the close of the last session whose publication window (D through the close of the session after D) ends at or before the dataset available end |

The statistics lag is 303's constant, not a restatement of it.

**Caught up** keeps 225's rule (every judged session `complete`, or `provider_hole` on a provider-`missing` day) and changes only which sessions are judged: those overlapping the family's wanted range **and** closing at or before the family's source edge. `_judged` moves from `tick_status_build` into `tick_status_scope` and takes the edge, so status and every surface judge the same set. Before this, a statistics session whose window had not closed was judged and kept `caught up` false until the next day's statistics arrived. Now it is out of range, shown as `pending` in the buckets, and does not count against caught up.

**This changes statistics behaviour; it is not a pure refactor.** No existing 303 expectation changes, because no 303 test judges a session whose window is open: `test_tick_statistics_status.py` seeds the dataset edge in 2030, and the status golden files (`es_status`, `es_tiered_status`) have no statistics want. A new test pins the change: a statistics session whose window closes after the edge is `pending` and leaves `caught_up` true.

The tick tier's and bars' judged sets are unchanged, and a regression test pins that on the existing status fixtures. A second test pins the case production has: the tick want ends 2024-12-31 and the statistics want ends 2026-11-01. The statistics edge must reach 2026, and a statistics session missing in 2025 must keep `caught_up` false.

`source_edge` is None when no dataset edge is known, and then `caught_up` is None, as status already does when there is no edge.

### D3 — One per-family report, used by every surface
Status: decided

`tick_status_build._families` becomes `family_report.family_reports(conn, calendar_url, entry, edge, *, cross_tier: bool) -> tuple[FamilyStatus, ...]`, in its own module (`tick_status_build.py` is already about 290 lines). There is one type. `FamilyStatus` (and its `StatisticsFamilyStatus` subclass, which carries the content findings) moves into that module and is extended in place with three fields: `source_edge`, `records` (the family's ledger total) and `cross_tier: tuple[CrossTierResult, ...]` (D4; empty when `cross_tier=False`). `status` keeps its tick-tier product fields, built as today, and renders these as its `families` list.

`tick_accounting.accounting_lines`, the health rows and the API block call `family_reports`, never `family_scope` themselves. That is the 230 rule (accounting's buckets are status's buckets, from the same function) extended to every family: the four surfaces cannot disagree.

Contract lines and roll lines are not part of the report. They stay in `status` only: the roll lines open their own connections (229 TD9) and are not a completeness figure.

- *Rejected: health and accounting call `build_status`.* It reads every contract's lines and runs the roll resolver per product, which is slow at scale and answers a different question.

### D4 — The cross-tier checks
Status: decided

There is no registry class: one check is active, and the other is a placeholder. `cross_tier_checks.py` holds the function `traded_vs_cleared(conn, entry, reports) -> tuple[CrossTierResult, ...]`, which `family_reports` calls directly, and the constant `INACTIVE_CROSS_TIER_CHECKS: Mapping[str, str]` (check name to reason), which status and accounting list as not active. A registry is introduced when a second check becomes active.

1. **`traded_vs_cleared`**, active. It applies when the entry wants statistics and at least one of `VOLUME_FAMILIES` (tick tier, `BAR_1M`). For each wanted volume family, it takes the sessions that are `complete` in that family's report and in the statistics report, and calls 303's `volume_check` once with them, unchanged. It then counts verdicts per family by `VolumeClass`: `agree`, `under`, `over`, `no_trades`. Sessions are not judged again: 303's `checkable_sessions` stays coverage's function. The range is every such session in the wanted range, not a recent window: the data is historical with fixed ends, and a recent window would never look at 2012. The measurement bounds its cost (D9).
2. **`ohlcv_1s_minute_sum`**, inactive, reason `needs the ohlcv-1s family`, the one entry in `INACTIVE_CROSS_TIER_CHECKS`. The comparison itself moves to the `ohlcv-1s` slice (issue #71). That slice has the real data and the row format, so a comparison built here would guess its inputs and could only be tested on fixtures that agree by construction.

   **Deviation from the slice plan.** The plan says this check is "wired but inactive". This slice delivers less than that: a name and a reason, listed as not active in status and accounting, with no check function. For the replacement to drop in, the `ohlcv-1s` slice must provide:
   - **Signature:** the same as `traded_vs_cleared`, `(conn, entry, reports) -> tuple[CrossTierResult, ...]`. `family_reports` calls it the same way. It returns nothing unless the entry wants both `BAR_1M` and the 1s family.
   - **Sessions:** those `complete` in both families' reports.
   - **Inputs per instrument and minute:** the `ohlcv-1m` bar (`open`, `high`, `low`, `close`, `volume`, `ts_event` = the minute's open, as in 302 D2), and that minute's `ohlcv-1s` bars, with the same columns.
   - **Result:** one `CrossTierResult` per wanted family pair, with counts of `agree` and of each named mismatch. A minute with seconds but no minute bar, or the reverse, is a mismatch.

   Activating the check is then three changes: delete the entry from `INACTIVE_CROSS_TIER_CHECKS`, add the function, and call it from `family_reports`.

A product that wants a volume family but not statistics gets no content check for it, and its report says so: the health and accounting text for that family carries `load-complete only`, as 302's status section already does. This is the architecture's "where only load-completeness is possible, the surface says so".

- *Rejected: adding `TickSchema.OHLCV_1S` and `SchemaFamily.BAR_1S` now so the check can be "active with no data".* Adding a schema is the `ohlcv-1s` slice's job, with its table and its marker. An enum member with no table would invite a want that cannot be served.

### D5 — Health: one row per product and family, counted only for defects
Status: decided

`gather_tick` adds, after `tick retention` and only when the tick database answered, one row per product and wanted family, named `futures {product} {schema}` (for example `futures ES tbbo`, `futures ES ohlcv-1m`, `futures GC statistics`). The schema text is display only; logic keys on `SchemaFamily`.

A row is one of three states, taken from the first rule that matches:

| State | When | Severity |
|---|---|---|
| `FAIL` | any judged session `failed` or `retry_exhausted`; or any `over` verdict in `traded_vs_cleared` | counted |
| `INFO` | `caught_up` is None (no dataset edge known: detail `no dataset edge; completeness not judged`); or not caught up; or statistics content findings; or `no_trades` verdicts | informational |
| `OK` | caught up, no finding | counted (passes) |

`caught_up` is None only when there is no dataset edge. A family without a wanted range gets no row, since `family_wants` lists only wanted families.

The detail is one line: complete over judged sessions, the source edge, caught up or not, and each non-zero bucket or finding count by name, for example `4,118/4,131 complete to 2026-11-06; not caught up: 13 missing` or `5,012/5,012 complete to 2026-11-05; caught up; traded_vs_cleared tbbo: 0 over, 2 no_trades; 3 content findings`.

Why the split: a counted check must be something a run can fix. A failed unit is fixed by a retry; `over` (more traded than CME cleared) is a double load or a wrong session, our defect. Not caught up is work not yet done: a product configured and not yet bought, which right after this release is production's own state until 304's purchase runs. Content findings and `no_trades` report what the provider delivered; 303 measured that deferred months legitimately lack cleared volume, and a provider gap in 2011 can never be fixed. Counting those would make health fail permanently on facts, and a check that always fails is ignored. They are named in health, listed per session in `status` and `coverage`, and 307 records them. Not caught up stays `INFO` after acquisition too, so health alone does not separate a real gap from work not yet done. The slice plan's 307 entry carries the criterion that does: once 307's purchases are ingested, every family's not-caught-up count is zero, or every remaining session is recorded by name with its reason.

**Failure and concurrency.**

- *Tick database does not answer:* only the existing `tick database` row prints, as for `tick retention` today.
- *A statement fails or times out partway:* health, accounting and the API block each make **one** call to `futures_completeness`, inside one `read_line` call (or one `read_tick` call in the API). That call reads every product's `family_reports`. Any error therefore fails the whole read, and no row is built from partial results. Before each step, `futures_completeness` writes the product, family and step to the `ReadProgress` the caller passed in. It catches nothing, and the error propagates to `read_line` or `read_tick` unchanged. The caller then joins `ReadProgress` with the unavailable text, for example `GC statistics: traded_vs_cleared: canceling statement due to statement timeout`. In health this is one row, `futures completeness`, and it is `FAIL`, **counted**. The database answered but completeness could not be verified, and indeterminate counts as failed, by the same rule as 168 D3 for cagg freshness. This is unlike the `tick database` row, whose informational status is 234's PM decision and is unchanged. Because a slowdown can trip this FAIL with no data defect, the timer path carries a stated margin: D9 bounds the health read at ≤ 5 s against the 10 s statement timeout, so 2× headroom is a requirement, not an accident. An operator who sees a timeout FAIL after release treats it as a D10 miss. They run `mt data tick status` to confirm the data verdict, and fix the named step's query or index, or file an issue, as D10 says. The bound is not raised and the check is not made informational. Accounting prints the unavailable line in place of the futures section, as it does today for an unreachable tick database, and its exit code is unchanged. The API block returns its existing non-`ok` `state` with that `detail` and no products (231).
- *A pass or ingest runs during the read:* the read takes no snapshot across products and families. Each statement sees the data committed when it runs, so a unit ingested partway through can make one figure differ from the next run's. Every figure here is a fresh read with no stored state, so the next run corrects it. No requirement asks for consistency across products, and an isolation change would touch both callers' connections for no stated need.

### D6 — Accounting: one line per product and family
Status: decided

`TickAccountingLine` gains `family` and `schema`, and `accounting_lines` returns one line per product and wanted family, built from `family_reports`: sessions in scope, sessions complete, buckets, records (the family's ledger total), `caught_up`, `source_edge`, and the cross-tier counts. The text section is retitled `futures universe:` and prints the lines grouped by product. JSON keeps `tick.products` with one entry per product; each entry's existing top-level fields stay the tick tier's (so 231's consumers do not break) and a `families` list carries every family, tick tier included. As today, the futures section never changes accounting's exit code or its recorded minute detail (230 TD7).

### D7 — Overview: one freshness line per family
Status: decided

The overview answers "what is fresh", not "what is complete" (completeness reaches it through the recorded health verdict it already prints). The `futures ticks` line stays. Two lines join it, each one ledger read on the same composed read:

- `futures ohlcv-1m`: newest `last_event_ns` in `tick_bar_ledger` over current units (`newest_bar_ns`);
- `futures statistics`: newest `session_date` in `tick_statistics_ledger` over current units (`newest_statistics_session`), shown as the session date, because a statistics record's own time is not the session it describes.

The names are constants beside `TICK_SOURCE_NAME`. A family no product wants prints no line. A tick outage is still a line, never a failed overview (230 TD8).

### D8 — `/api/v1/status`: per-family records, documented
Status: decided

API question: yes, this belongs in the API. trading-engine and trading-ui read `/api/v1/status` to decide whether a futures range is usable, and 231 already serves the tick tier there. `TickAccountingRecord` gains `families: list[FamilyAccountingRecord]`, one per wanted family, from the same `accounting_lines` (D6) called with `cross_tier=False`: `family`, `schema`, `sessions_in_scope`, `sessions_complete`, `buckets`, `records`, `caught_up`, `source_edge` and `load_complete_only`.

The block carries no cross-tier counts. `traded_vs_cleared` resolves cleared volume from the raw statistics table over the whole history, and the API would pay for that on every request. Its counts are in health, accounting and `status`, which run when an operator or the health timer asks. The block's latency bound is unchanged from 231 (D9).

The existing fields stay and keep their meaning, so the change is additive. `docs/api/reference.md`, `agents.md` and `openapi.json` describe the block, including that `caught_up: null` means no edge or no wanted range, never "unknown, assume yes".

### D9 — The manifest-scale measurement
Status: decided

**Shape.** One throwaway database on the test cluster, migrated to head, seeded to 307's full shape by `test/load/manifest_scale_seed.py`:

- ES and GC, 2010-06-08 to 2026-11-01, every session day from the real calendars: `definition`, `statistics` and `ohlcv-1m` units for both, plus the held-window `tbbo` units. That is about 30,000 units in month-run requests, all `ingested`.
- Ledger rows per unit at real proportions: instruments per day and stat types per instrument taken from the real 2024 fixtures (definitions, statistics, the derived `ohlcv-1m` day), so the ledgers are the size 307 will make them.
- `tick_definition` rows per instrument over the range.
- `tick_statistics` at its full size (19.4 M records for ES and GC, the architecture's cost table), with the stat-type mix of the real fixtures, because `volume_check` reads resolved cleared volume from the raw table. `tick_ohlcv_1m` raw rows are not seeded: no surface measured here reads them.
- Day conditions for every day, and one `tick_dataset_edge` row.
- Chunks compressed explicitly with `compress_chunk`, because the test cluster has no job scheduler and production's history is compressed.

**Measured**, each the median of three runs after one warm-up:

| Operation | Bound | Why this bound |
|---|---|---|
| `futures_completeness(cross_tier=True)` for both products, every family (what health and accounting run) | ≤ 5 s total; no single statement ≥ 5 s | a required 2× margin against the composed 10 s statement timeout on the health timer path, since a statement over 10 s is a counted FAIL (D5) |
| `/api/v1/status` added latency of the `tick` block (`cross_tier=False`) | < 0.25 s | 231's bound, which API consumers already rely on, held at full scale rather than relaxed |
| `mt data tick status` (`build_status`, both products) | ≤ 30 s | operator command; roll lines are bounded per statement at 30 s already (`TICK_ROLL_STATEMENT_TIMEOUT_SECONDS`) |
| tick preflight plus a steady-state `pass --estimate-only` that plans nothing | ≤ 10 s | runs before every pass; 224's steady pass was 2 s at about 1,500 days |
| `select_units` with nothing selectable | ≤ 2 s | runs at the start of every ingest |

**Recorded** in `user/notes/305-manifest-scale-measurement.md`: unit and request counts, each median and its samples, the slowest statement with its `EXPLAIN (ANALYZE, BUFFERS)`, and the host.

The test runs under `MT_RUN_LOAD_TESTS=1` like the rest of the load tier, and never reads the production URL (the tier's existing guard test covers the new file). The session-scoped fixture that creates the database drops it in its finalizer, so a failed or aborted run does not leave about 19.4 M rows on the shared cluster. The drop names only the database that fixture created, as the destructive-statement rule requires.

### D10 — A missed bound is fixed in this slice
Status: decided

A bound is not raised to pass. A miss is fixed here by a query rewrite (set-based instead of per-session) or an index, delivered as tick migration `tick_011_*` if it is an index (next after `tick_010_statistics`; read the last id at implementation), and re-measured. If the fix needs stored or cached derived state, a new table or a change in what a surface reports, it is beyond this slice: an issue is filed with the measurement, and 307 depends on it, since 307 creates the scale. The bounds and the outcome are recorded in the note either way.

## Implementation Details

### API Contracts

`GET /api/v1/status`, `tick.products[*]`, additive:

```json
{
  "product": "ES",
  "sessions_in_scope": 64, "sessions_complete": 64, "buckets": {"complete": 64}, "records": 0,
  "families": [
    {
      "family": "statistics", "schema": "statistics",
      "sessions_in_scope": 4131, "sessions_complete": 4118,
      "buckets": {"complete": 4118, "missing": 13},
      "records": 5980000, "caught_up": false,
      "source_edge": "2026-10-30T21:00:00Z", "load_complete_only": false
    }
  ]
}
```

The figures above show the shape only. The existing top-level fields are the tick tier's, as 231 defined them.

## Integration Points

### Provides to Other Slices

- **307 (futures history acquisition):** every family's verdict and caught up in health, accounting and the API, and the cross-tier counts in health and accounting, which 307 runs over the whole range and records; and the measurement (and, if a bound missed, the issue 307 then depends on). Because not caught up is `INFO` (D5), the slice plan's 307 entry now carries the exit criterion: each family's not-caught-up count is zero after its purchases are ingested, or every remaining session is recorded by name with its reason.
- **The `ohlcv-1s` slice (future work):** one `FAMILY_PUBLICATION_LAG` entry to add, and the `ohlcv_1s_minute_sum` entry in `INACTIVE_CROSS_TIER_CHECKS` to replace with an active check (introducing a registry then, if two checks are active), whose comparison that slice builds (issue #71).
- **306 (serving):** nothing it must consume. Its own decision about serving open interest is independent of the status block.

### Consumes from Other Slices

- **302, 303:** consumed unchanged except for two things. The `_families` extraction (D3) is a refactor pinned by the existing status tests. The `_judged` move (D2) also changes statistics behaviour: a session with an open publication window is now `pending` and no longer counts against caught up. No existing 303 expectation changes, and a new test pins the new behaviour.
- **304:** production's configured wants. If the purchase has not run, the new rows are `INFO` (not caught up), never `FAIL`.

## Success Criteria

### Functional Requirements

1. `FAMILY_PUBLICATION_LAG` has an entry for every family in `FAMILY_LEDGER`, enforced by a test; `COVERAGE_SOURCE_TABLE` is unchanged.
2. A statistics session whose publication window closes after the dataset available end is shown `pending` and does not make `caught_up` false. When the tick want ends 2024-12-31 and the statistics want ends 2026-11-01, the statistics edge reaches 2026, and a missing 2025 statistics session keeps `caught_up` false. Tick-tier and bar judged sets are unchanged on the existing status fixtures.
3. `mt data tick status`, `mt data health`, `mt data accounting` and `/api/v1/status` report identical buckets, caught up and source edge for each product and family. One test proves it by building all four from one seeded database.
4. `mt data health` prints one row per product and wanted family:
   - a seeded `failed` unit or an `over` verdict makes the row `FAIL` and the command exit 1;
   - a not-caught-up family with no defect, a content finding, a `no_trades` verdict or a missing dataset edge makes it `INFO` and leaves the exit code at 0;
   - a caught-up family with nothing found is `OK`;
   - a statement timeout injected partway through the read gives one counted `FAIL` row, `futures completeness`, naming the product, family and step, and no per-family rows.
5. A family whose product wants no statistics is labelled `load-complete only` in health, accounting and the API.
6. `traded_vs_cleared` runs over every session that is complete in both statistics and a wanted volume family in the wanted range, once per such family, without judging sessions again. Its counts match `mt data tick coverage --schema statistics` for the same range.
7. `ohlcv_1s_minute_sum` is listed as not active, with its reason, in status and accounting.
8. `mt data accounting` prints a `futures universe:` section with one line per product and family; its JSON keeps the tick tier's top-level fields and adds `families`; the exit code and the recorded minute detail are unchanged.
9. `mt data overview` prints `futures ohlcv-1m` and `futures statistics` lines when a product wants them, and a tick outage is still one line.
10. `/api/v1/status` carries `families` per product, with no cross-tier counts. The existing fields are unchanged in name, type and meaning.

### Technical Requirements

- The measurement (D9) meets every bound on the seeded full-shape database, or each miss is fixed per D10 and re-measured; the note records the figures.
- No surface reads a raw tick table except through 303's `volume_check`; status, health and accounting read the manifest and the ledgers.
- `ruff format` (touched files only), `ruff check`, mypy (one invocation over the source and tests paths) and the unit, integration and touched load tests pass.
- New modules stay near 300 lines; `tick_status_build.py` does not grow.

### Integration Requirements

- 307 can run `mt data health`, `accounting` and `tick status` over the full range and get each family's verdict without a new command.
- `docs/api/reference.md`, `agents.md` and `openapi.json` describe the new `families` field and `caught_up: null`.

### Verification Walkthrough

Draft; refined after implementation.

1. **Unit and integration tests** (test file names are set at task breakdown):

   ```bash
   cd ~/source/repos/manta/trading-data
   export MT_TIMESCALE_TEST_URL=...   # from .env, quotes stripped
   uv run pytest test/unit/data/tick -q
   uv run pytest test/integration -q -k "tick and (health or accounting or status or overview)"
   ```

   Expect all pass, including the four-surface agreement test (criterion 3) and the health severity table (criterion 4).

2. **The measurement:**

   ```bash
   MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_305_manifest_scale_nfr.py -s
   ```

   Expect the seeded unit count printed (about 30,000), each median under its bound, and the throwaway database dropped. Copy the figures into `user/notes/305-manifest-scale-measurement.md`.

3. **Health on production**, after the release is installed:

   ```bash
   sudo mt-run data health
   ```

   Expect, below `tick database` and `tick retention`, rows such as `futures ES tbbo`, `futures ES ohlcv-1m`, `futures ES statistics`, `futures GC tbbo`, `futures GC statistics`. If 304's purchase has not run, the history rows are `INFO ... not caught up: N missing` and the verdict line is unchanged. After the purchase, they are `OK` or `INFO` with named findings, and a `FAIL` names the failed units or `over` verdicts.

4. **Accounting and overview:**

   ```bash
   sudo mt-run data accounting | sed -n '/futures universe:/,$p'
   sudo mt-run data overview
   ```

   Expect one accounting line per product and family with sessions, records and caught up, and overview lines `futures ticks`, `futures ohlcv-1m`, `futures statistics`.

5. **The API:**

   ```bash
   curl -s localhost:8137/api/v1/status | jq '.tick.products[] | {product, families: [.families[] | {schema, caught_up, source_edge, sessions_complete, sessions_in_scope}]}'
   ```

   Expect the same figures as step 4 for each product and family.

6. **Agreement by hand:** `sudo mt-run data tick status --product GC --json | jq '.products[0].families'` shows the same buckets and `caught_up` as the GC lines in steps 3 to 5.

## Risk Assessment

### Technical Risks

- **A surface misses its bound at full scale.** Status judges sessions in Python over per-day unit and condition reads, and `volume_check` resolves cleared volume over 19.4 M rows; neither has run at that size.

### Mitigation Strategies

- The measurement runs on a seeded database before 307 creates the scale, and D10 says what a miss leads to. The slowest statement's plan is recorded, so a fix starts from evidence.

## Implementation Notes

### Development Approach

1. `FAMILY_PUBLICATION_LAG`, `source_edge` and the edge-bounded `_judged`, with the regression test on existing fixtures.
2. Extract `family_reports`; switch `status` to it with no output change except `source_edge`.
3. `traded_vs_cleared` over the report's verdicts, and `INACTIVE_CROSS_TIER_CHECKS`.
4. Accounting lines per family, then health rows, then overview lines, then the API block and its documentation.
5. The seed and the load test; run, fix per D10 if needed, write the note.

Commit at least once per numbered step.

### Special Considerations

- **Composed connection.** Every new read runs on the connection `read_line` opens, under its 10 s statement timeout; nothing opens a second tick connection for health, accounting or overview.
- **Seeding cost.** Generating 19.4 M statistics rows and compressing them takes minutes on the test cluster. The seed builds once per test session, not per test.
- **Dev versus production.** A dev-checkout `mt data health` judges with the dev `.env`; production verification uses `sudo mt-run`.
