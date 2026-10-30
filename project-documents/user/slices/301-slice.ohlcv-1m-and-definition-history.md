---
docType: slice-design
slice: ohlcv-1m-and-definition-history
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies: [300]
interfaces: [302, 303, 304, 305, 306]
dateCreated: 20261030
dateUpdated: 20261030
status: not_started
---

# Slice Design: ohlcv-1m and Definition History

## Overview

This slice adds Databento's `ohlcv-1m` (provider one-minute bars) as an archive schema, and makes `definition` wantable over its own range back to the start of the dataset (2010-06-06). Bars are projected into their own table, `tick_ohlcv_1m`, keyed by instrument and minute. They never go into `minute_ohlcv` or `tick_trade`. A bar record carries only an `instrument_id`, so every bar is resolved to a contract through `tick_definition` validity windows. That makes definitions a prerequisite for the whole bar range.

The tier ships with its completeness check. `mt data tick status` and `coverage` report the bar tier per CME session, next to the tick tier. "Load-complete" is proven per unit: the provider count, decoded count and stored count agree, and every bar lies inside the unit's UTC day. A provider day condition of `degraded` or `missing` is shown on the session. A quiet session with few or no bars is not a hole.

Design found a prerequisite the slice plan does not name: the CME calendars (`CME_EQUITY`, `CME_METALS`) are populated only from 2020-01-01. A bar from 2010 to 2019 cannot be placed in a session, so ingest would fail it on `session_boundary`. This slice extends both calendars back to 2010-06-06. Each pre-2020 holiday is cited from a dated CME document, as 221 and 232 did. Regular hours become dated, because the CME Globex close was not constant over that period (D6).

Nothing is bought here. The production universe wants no bar or history days after this slice, so the pass plans exactly what it plans today. 303 makes the first purchase.

## Value

- **Architectural enablement.** 302 (statistics) needs the definitions, the pre-2020 sessions and the per-session volume this slice stores. 303 buys `ohlcv-1m` and definitions through the path built here. 305 serves bars from `tick_ohlcv_1m`. 306 fills the full range.
- **History becomes possible.** After 303 and 306, research can reach back to 2010 on bars. Ticks cover only what is bought.
- **A verdict, not a hope.** An operator can ask `mt data tick status --product ES` whether the bar range is complete, and `coverage --schema ohlcv-1m` proves the stored rows match the ledger, session by session.
- **No spend and no behaviour change today.** The tick pipeline, the production pass and its spend lines are unchanged until 303 configures a bar range.

## Technical Scope

**Included**

- `TickSchema.OHLCV_1M`, its schema family, its plan-inclusion row and its archived-schema membership (D1).
- Migration `tick_009_ohlcv_1m`: widen `tick_request_schema_check`, create the `tick_ohlcv_1m` hypertable with its columnstore policy, and create `tick_bar_ledger` (D2).
- `data/tick/ohlcv_projector.py`: the `ohlcv-1m` projector, registered in `INGEST_PROJECTORS` (D3).
- A unit-day check at ingest, which is the load-complete rule (D4).
- Universe history wants (`HistoryWant`): an entry can want `ohlcv-1m` and `definition` over ranges of their own. The planner turns them into requests (D5). Production configures none.
- Calendar history: `CME_EQUITY` and `CME_METALS` sessions from 2010-06-06, with dated regular-hours eras and sourced pre-2020 exceptions. These live in a primary-track migration (D6).
- `mt data tick status` and `coverage` per schema family: the bar tier's session verdicts, conditions and the ledger-to-raw check (D7).
- Fixtures: the DBN project's `ohlcv-1m` samples, plus one real-shape bar day derived from a held `tbbo` day (D8).
- `mt data tick estimate` shows an `ohlcv-1m` row (D9).
- Backup: the new tables join the restore drill's table set, and the archive layout is pinned under `MT_TICK_ARCHIVE_DIR` (D10).

**Excluded**

- Any purchase, or any change to the production universe's ranges (303, 306).
- `statistics`, the cross-tier volume check against it, and the open-interest roll (302).
- `ohlcv-1s` (future work). Provider `ohlcv-1h` and `ohlcv-1d` are never archived.
- Serving bars through `/api/v1/futures/*` or `mt data tick get` (305). Neither changes here (D11).
- `health`, `accounting`, `overview` and the status coverage aggregates (304).
- Tick-derived-versus-provider bar parity. It needs provider bars over a window where ticks are held, and that first exists after 303. It is measured in 305, where the source rule is applied.
- Renaming the `tick` track, `MT_TICK_*` or `mt data tick` (architecture).

## Dependencies

### Prerequisites

- **300 (archive generalisation):** schema families, `supersedes_sql`, `INGEST_PROJECTORS` and `IUnitProjector`, the plan-inclusion table and marker. 300's code review is open, and this slice's branch is forked from the target after 300 merges.
- **221 and 232 session model:** `trading_calendars`, `trading_holidays`, the materialised sessions, `extend_calendar_sessions` and `SessionIndex`.
- **Sources for D6:** CME holiday schedules and contract-specification pages for 2010 to 2019, read through the Wayback Machine as 221 did.
- **DBN sample files:** `test_data.ohlcv-1m.v3.dbn.zst` and `.v1.dbn.zst` from `databento/dbn` at tag `v0.70.0`, the tag the existing fixtures pin. Checked at design: both hold 2 `GLBX.MDP3` ESH1 bars (instrument 5482) on 2020-12-28, `raw_symbol` symbology, fields `instrument_id, ts_event, open, high, low, close, volume`.

### Interfaces Required

- `IUnitProjector`, `Loaded`, `projector_for` (300 D2), unchanged.
- `ingest_select`'s `has_definitions` prerequisite (300 D4), unchanged. A bar unit waits for a same-shape, same-day definition unit that has been ingested.
- `ingest_records.resolve`, `locate_sessions` and `LedgerAccumulator`, reused as they are (D3).
- `tick_day_condition` and `tick_dataset_edge` (223), unchanged. Conditions are per dataset and day, not per schema.

## Architecture

### Component Structure

```
data/tick/constants.py         TickSchema.OHLCV_1M; ARCHIVED_SCHEMAS gains it;
                               TICK_OHLCV_1M_CHUNK_INTERVAL, _COMPRESS_AFTER,
                               _SEGMENT_BY, _ORDER_BY
data/tick/schema_families.py   SchemaFamily.BAR_1M: (OHLCV_1M,)
config/databento_plans.py      STANDARD[OHLCV_1M] = None (all history)
data/tick/storage_columns.py   TICK_OHLCV_1M_COLUMNS, TICK_OHLCV_1M_KEY
data/tick/ohlcv_projector.py   OhlcvProjector: load (COPY + ledger + unit-day check),
                               stored_count, finish (tick_bar_ledger), delete_unit
data/tick/projectors.py        INGEST_PROJECTORS[OHLCV_1M] = OHLCV_1M
data/tick/universe.py          HistoryWant; TickUniverseEntry.history
data/tick/planner.py           history wants beside tier and companion wants
data/tick/status_reads.py      Shape takes a family; ledger and raw reads per family
data/tick/tick_status.py       day_unit ranks within the unit's family
data/tick/tick_coverage.py     build_coverage(..., family)
market/schema/seed_cme_*.py    pre-2020 exceptions; dated regular-hours eras
data/base/session_extension.py sessions use the era in force on the session date
market/schema/migrations/      tick_009_ohlcv_1m (tick track);
                               one primary-track migration for D6
scripts/drill_228_*.py         new tables in the drill's table set
```

### Data Flow

**Acquisition (once 303 configures a range).** The planner reads each universe entry's `history` wants beside its tier. For every wanted UTC day, it builds the same day set the tier uses, from the product's sessions. Days already covered are dropped, and days are kept or tallied by condition exactly as tier days are. Runs within a UTC month become requests. Within a day, definitions are ordered before every other schema (224 TD4). The spend guard, the plan-inclusion marker and the manifest writes are 300's and do not change. Definition units are projected by the acquisition pass, as today (300 D3).

**Ingest.** `select_units` now also selects `ohlcv-1m` units, because the schema is a key of `INGEST_PROJECTORS`. A unit is selectable only once its same-shape, same-day definition unit is ingested. On the worker, `OhlcvProjector.load` does the following for each decoded batch:

1. Check every `ts_event` is inside the unit's UTC day (D4).
2. Resolve every `instrument_id` through the product's definitions (`resolve`).
3. Place every bar in a session (`locate_sessions`).
4. Fold the batch into a `LedgerAccumulator`, with `volume` as the size.
5. `COPY` the batch into `tick_ohlcv_1m`.

The worker then checks provider = decoded = stored. `finish` writes `tick_bar_ledger` rows. A key collision is the existing `overlap` failure.

**Status and coverage.** For each universe entry, status builds one verdict set per family the entry wants: the tick tier (as today) and `BAR_1M` when the entry has an `ohlcv-1m` want. A session's days come from its sessions. Each day's unit is the highest-ranked current unit of that family and shape. Conditions come from `tick_day_condition`. `coverage --schema ohlcv-1m` compares, per (instrument, session), the ledger's bar count and volume with `count(*)` and `sum(volume)` of `tick_ohlcv_1m`.

### State Management

- **New tables (tick database):** `tick_ohlcv_1m` (provider bars) and `tick_bar_ledger` (per unit, instrument and session: bar count, volume, first and last bar time). Both are written once per unit, inside the unit's ingest transaction.
- **Changed table:** `tick_request.schema` may be `ohlcv-1m`.
- **Primary database:** `trading_holidays` rows from 2010-06-06 to 2019-12-31 for the two CME calendars, a new `trading_calendar_hours` table, and their sessions in the materialised sessions table.
- **Configuration:** `TICK_UNIVERSE` entries gain `history=()`, which buys nothing. The plan table gains one row.

## Technical Decisions

### D1 — `ohlcv-1m` is its own family, `BAR_1M`
Status: decided

Add `TickSchema.OHLCV_1M = "ohlcv-1m"`, `SchemaFamily.BAR_1M` with `(OHLCV_1M,)`, `ARCHIVED_SCHEMAS |= {OHLCV_1M}`, and `PLAN_INCLUDED_HISTORY[STANDARD][OHLCV_1M] = None`. The pricing page read on 2026-10-28 lists `ohlcv-1m` as included for 16+ years. `STORED_TIERS`, `TICK_TIERS` and `COMPANION_SCHEMAS` do not change: a bar schema is not a tick tier and not a companion.

300 D1 anticipated a single `BAR` family. Rank orders a family, so `ohlcv-1s` joining `BAR` later would make a 1-second unit supersede a 1-minute unit of the same day. Those are two different series that are both kept, so each bar interval is its own family. The family is named for its interval now, so the 1-second family can be added later without renaming this one.

- *Rejected: `BAR` holding both intervals.* Within-family rank would delete one interval's rows when the other arrives.

### D2 — Storage: `tick_ohlcv_1m` and `tick_bar_ledger`
Status: decided

`tick_ohlcv_1m` is an integer-nanosecond hypertable on `ts_event`. As delivered, `ts_event` is the bar's open (verified on the DBN sample: 13:00:00 and 13:01:00 UTC):

| Column | Type | Notes |
|---|---|---|
| `ts_event` | `BIGINT NOT NULL` | bar open, epoch ns |
| `instrument_id` | `BIGINT NOT NULL` | resolved through `tick_definition` |
| `publisher_id` | `INTEGER NOT NULL` | as delivered (as `tick_trade`) |
| `open`, `high`, `low`, `close` | `BIGINT NOT NULL` | DBN fixed price, scale 10⁹ (`TICK_PRICE_SCALE`) |
| `volume` | `BIGINT NOT NULL` | |
| `unit_id` | `BIGINT NOT NULL` | no foreign key (as `tick_trade`, 222 TD6) |

- **Key:** `PRIMARY KEY (instrument_id, ts_event)`. A provider bar is one per instrument and interval. A second current unit holding the same key is the existing `overlap` failure, never a silent `ON CONFLICT`.
- **Chunks:** `TICK_OHLCV_1M_CHUNK_INTERVAL = 7 days`. The journal 20260719 rule (span ÷ 1,000 to 2,000 chunks) gives about 1,040 chunks for 2010 to 2030, the same answer as `tick_trade`.
- **Columnstore:** segment by `instrument_id`, order by `ts_event`, compress after 14 days, through the existing `tick_now_ns` integer-now function. Most of the range is history, so chunks are compressed soon after ingest writes them. 226 measured supersession deletes on compressed chunks at 0.10 s.
- **Size:** about 24.6 M rows for ES and GC over the full range (8.7 M + 15.9 M, architecture cost table). That is a fraction of the held `tick_trade`, so no separate capacity step is needed.

`tick_bar_ledger` has `(unit_id, instrument_id, calendar_id, session_date, bar_count, volume, first_event_ns, last_event_ns)`, with the same key and `CHECK`s as `tick_ingest_ledger`. It is a separate table because the tick ledger's readers (catalog, the volume roll rule, status) mean trades by it. Adding bars there would double-count roll volume. It is one ledger for every bar interval: the interval is reached through unit → request, as the tier is for `tick_ingest_ledger`. 302's volume check reads it.

- *Rejected: rows in `tick_ingest_ledger`.* That would break the volume roll rule (300 D2).
- *Rejected: a ledger-free bar tier* (completeness from a raw `count(*)`). Status must never scan bars, by 220's rule that status reads the manifest and the ledger.

### D3 — The projector reuses the tick ingest primitives
Status: decided

`OhlcvProjector` implements 300's `IUnitProjector` with the same shape as `TickTradeProjector`: one `COPY` per unit, with `resolve`, `locate_sessions` and `LedgerAccumulator.add(ids, positions, volume, ts)` applied per batch. `stored_count` and `delete_unit` are bounded by the unit's own first and last bar times, which are read from `tick_bar_ledger` for a superseded unit. `_SUPERSEDED` in `ingest_plan` reads the ledger of the unit's family, not always `tick_ingest_ledger`. `BAR_1M` has one schema, so no bar unit is superseded today, but the path is correct for the day a family has two.

Reused checks: `resolution` (a bar with no definition window fails the unit), `session_boundary` (a bar in a break or outside the populated calendar), `overlap`, and `shape`. The ledger is defined for parent symbology, as 225 decided for ticks.

- *Rejected: a generic projector parameterised by table.* The `COPY` columns, the ledger's size column and the checks differ by schema. Two small classes that share the primitives are clearer than one class with switches.

### D4 — Load-complete: counts plus the unit's UTC day
Status: decided

A bar unit is load-complete when it is *ingested*, which already requires provider count = decoded count = stored count (225's `counts` check), and every bar's `ts_event` lies in `[unit_date 00:00 UTC, unit_date + 1 day)`. The manifest's statement of a unit is its UTC day and the provider's record count, so this is "first and last event time against the manifest". The day check is new: `IngestCheck.UNIT_DAY`, reason `unit_day: N records outside <date>; first at <ts>`. It is applied by the bar projector only. Ticks keep today's checks, so the tick pipeline does not change.

Load-complete is all this slice can prove. Content completeness (bar volume against `statistics` cleared volume) is 302's, and status says so: the bar section's header reads `load-complete only`.

### D5 — What the universe wants: `HistoryWant`
Status: decided

`TickUniverseEntry` gains `history: tuple[HistoryWant, ...] = ()`, with `HistoryWant(schema, start, end)` (UTC days, end exclusive, `None` meaning up to the edge). Validation at import:

- The schema is `ohlcv-1m` or `definition` (302 adds `statistics`).
- At most one want per schema.
- `start` is not before `CME_DATASET_START = 2010-06-06`.
- Every bar want's range lies inside the entry's `definition` want. A bar day with no definition day would wait on `awaiting definitions` forever, so this is refused when the module loads, not found later.

The planner adds history wants to tier wants. Both produce `(shape, days)` and go through the same classification, runs and ordering, so `_tier_wants` becomes `_wants` over the tier and the history wants. A definition want is a want of its own, not a companion. The bundle (`_companion_wants`) stays a tick-tier rule (300 D4), and the union of the two definition sets is what gets bought.

Production keeps `history=()` for ES and GC in this slice. With a definition want from 2010 and no plan configured, the pass would plan $2.72 of definitions against the $0.50 ceiling and refuse every run. Setting the ranges, and raising the ceiling for that run, is 303's job.

- *Rejected: one field per schema* (`ohlcv_1m_range`, `definition_range`). 302 would add a field, and 306 another. A tuple of wants grows by data, not by code.
- *Rejected: companion definitions for bar days.* That repeats the tick bundle for a family that does not need one, and it would hide the definition range the PM is buying, which is a range of its own (2010 onward).

### D6 — The CME calendars reach back to 2010-06-06
Status: decided

Placing a bar in a session needs a populated session. `CME_EQUITY_SEED_START` and `CME_METALS_SEED_START` are 2020-01-01. One primary-track migration extends both calendars back to `CME_DATASET_START`:

1. **Dated regular hours.** A new table, `trading_calendar_hours (calendar_id, effective_from, market_open, market_close, source)`, with primary key `(calendar_id, effective_from)`. `populate_trading_sessions` uses the era in force on each session's date when a calendar has eras. A calendar with no eras (NYSE, NASDAQ) uses `trading_calendars` as today. The latest era of each CME calendar must equal its `trading_calendars` row, and a unit test enforces this, so the two cannot disagree. This is needed because the Globex close moved: an unofficial forum report puts the CME equity daily maintenance moving to start at 16:00 CT on a Monday, 21 September (consistent with 2015). Before that, ES closed at 16:15 CT. A bar at 16:05 CT in 2014 would otherwise fail `session_boundary`. The forum report is not a source. The eras are entered only from dated CME documents (notices, contract specification pages on Wayback), one `source` per era, for ES and for GC independently.
2. **Pre-2020 exceptions.** Every holiday closure and early close from 2010-06-06 to 2019-12-31, one row per day, each citing the CME holiday schedule it was read from, in the existing seed modules. Unscheduled closures are included where CME documents them (for example the 2012-10-29 and 10-30 Hurricane Sandy closure and the 2018-12-05 national day of mourning). Nothing is generated from a holiday rule (221 D5).
3. **Sessions.** `extend_calendar_sessions` from 2010-06-06 to 2019-12-31 for both calendars. `*_SEED_START` moves to 2010-06-06. 2020 onward is untouched, and the migration checks this by comparing those sessions before and after.

A mis-seeded day cannot pass silently. Ingest fails any bar outside a session with `session_boundary`, naming the sessions either side. `scripts/verify_cme_sessions.py` runs any held job through the calendar. At 303 and 306 every bought day goes through the same check.

- *Rejected: era hours as exception rows.* An exception models a closure or an early close. A later regular close is neither, and stretching the vocabulary would make every pre-2015 weekday an "exception".
- *Rejected: populating pre-2020 sessions in a CME-only routine with no table.* `mt data extend` and the session repair paths rebuild from the tables. Hours known only to one routine would be overwritten the first time either ran.

### D7 — Status and coverage are per family
Status: decided

- `Shape.params` takes a family's schemas instead of `STORED_TIERS`. `SHAPE_WHERE` is unchanged and binds those schemas. Status, coverage, `ledger_totals` and `contract_lines` get the family from the caller.
- `tick_status.day_unit` ranks within the unit's family through `FAMILY_SCHEMAS` instead of `TICK_TIER_RANK`. Its result for tick tiers is unchanged.
- `mt data tick status` prints, per product, one section per family the entry wants (`tbbo` tier as today, then `ohlcv-1m`), each with its session counts by status, its condition findings and `caught up`. A family with no want is not printed. `--json` gains a `families` list, and the existing top-level product fields stay as the tick tier's, so consumers of today's JSON do not break.
- `mt data tick coverage` gains `--schema`. The default is the entry's tier, which is today's behaviour. `--schema ohlcv-1m` reports per session: status, condition, unit ids, ledger bars and volume, raw bars and volume, and the check. The raw read is the one bounded scan of `tick_ohlcv_1m`, as `raw_counts` is of `tick_trade`. A mismatch exits 3, as today.
- **Sparse bars.** Completeness is unit-level (225 TD10). A session whose units are ingested is `complete` with zero bars for any contract. Coverage shows the counts and judges only ledger against raw. A `degraded` or `missing` day condition is shown as the session's condition, and a `missing` day with no unit is `provider_hole` (225's rule, reused).

### D8 — Fixtures: DBN samples and one derived real-shape day; no purchase
Status: decided

No provider `ohlcv-1m` file for a parent-symbology CME day is held: the local archive and `/data/market-data/databento` hold only `trades` and `tbbo`. 303 is the first purchase by plan. So the tier is built on these:

- **DBN samples** (copied unmodified, recorded in `test/fixtures/databento/SOURCES.md`): `test_data.ohlcv-1m.v3.dbn.zst` and `.v1.dbn.zst`. These prove the reader decodes the bar layout in both DBN versions, and pin field names, scale and bar-open `ts_event`. Their instrument (5482, 2020-12-28) has no CME definition fixture, so projector tests with them seed one `tick_definition` row in the test database.
- **One derived day:** `real/glbx-mdp3-20241203.ohlcv-1m.dbn.zst`, built by extending `scripts/cut_tick_fixtures.py`. It keeps the provider's 2024-12-03 `tbbo` header (schema set to `ohlcv-1m`) and holds trade-only one-minute bars computed from that day's full held `tbbo` file. It is marked **Not purchased** in `SOURCES.md`, as the derived `trades` fixture of 225 is. With the real 2024-12-03 definition fixture, it exercises parent symbology, real instrument ids including spreads, both sides of the daily break, resolution and the ledger. It makes no claim about provider parity.

The first real provider bar file is ingested in 303, and its coverage verdict is that slice's evidence.

### D9 — `mt data tick estimate` shows `ohlcv-1m`
Status: decided

`ESTIMATE_SCHEMAS` appends `OHLCV_1M`. Its row shows list cost, records, bytes and `is_plan_included`, and its verdict judges its own marginal cost against the ceiling. It takes no part in the tick-tier bundle. 303 uses this to size "the most recent years of ES with the rest of the cap" with free metadata calls.

### D10 — Backup coverage is mechanical
Status: decided

- **Archive files.** Bar and definition files are written by the existing archive layout under `MT_TICK_ARCHIVE_DIR`, which is in `cron_system_backup.sh`'s `INCLUDE_PATHS` (223). A unit test pins that an `ohlcv-1m` unit's path resolves under the archive directory, so no new root can appear unbacked.
- **Database.** Both tables are in the tick database, which the tick cluster's base backup and WAL archive cover whole (227).
- **Restore drill.** `tick_bar_ledger` joins `drill_228_bookkeeping.BOOKKEEPING_TABLES`. `tick_ohlcv_1m` gets a fingerprint beside `tick_trade`'s in `drill_228_fingerprint`. The existing pin test (`test_drill_228_bookkeeping_pins.py`) asserts the drill's tables equal the migrated tick tables, so it fails until this is done.
- **Roles.** `provision_tick_roles.sql`'s enumerated write surface (now seven tables) gains the two tables, and `test_tick_role_privileges.py` covers them.

### D11 — No API change in this slice
Status: decided

The API question is answered in 305, which applies the source rule (provider `ohlcv-1m` is the default for 1m and coarser). Serving bars here would ship a second bars path before the rule that chooses between them. `/api/v1/futures/*` and `mt data tick get` are unchanged.

### D12 — Definition history: size and failure shape
Status: decided

A definition file re-sends each listed instrument daily. `tick_definition` stores one row per `(instrument_id, activation_ns)`, and a same-key re-send is a no-op (224 TD5). So the table grows by distinct instruments, not by the 3.3 M records Databento bills for ES and GC together. Even the worst case, every record distinct, is under the size of one month of `tick_trade`, so no capacity work is needed here. 303 records the measured row count. Pre-2020 definitions may carry an undefined activation or expiration. That fails its unit, naming the instrument (222 F007). 303 and 306 see it, and it is never patched with an invented window.

## Implementation Details

### Database / Storage Schema

`tick_009_ohlcv_1m` (tick track):

- Drop and re-add `tick_request_schema_check`, rendered from `ARCHIVED_SCHEMAS` (222 TD9).
- `CREATE TABLE IF NOT EXISTS tick_ohlcv_1m (...)` per D2. Then `create_hypertable(... chunk_time_interval => interval_to_ns(TICK_OHLCV_1M_CHUNK_INTERVAL), create_default_indexes => FALSE, if_not_exists => TRUE)`, `set_integer_now_func('tick_ohlcv_1m', 'tick_now_ns')`, the columnstore `ALTER` (applied to an empty table, so 226's "fails on compressed chunks" caveat cannot arise), and `add_columnstore_policy`.
- `CREATE TABLE IF NOT EXISTS tick_bar_ledger (...)` per D2.

Primary-track migration (D6): `CREATE TABLE trading_calendar_hours`, insert the eras, insert the 2010-06-06 to 2019-12-31 exceptions (`ON CONFLICT DO NOTHING`), and extend sessions over that range. It touches only `CME_EQUITY` and `CME_METALS`. Migration chain pins that name the last id are updated, as every primary migration does.

Both are applied in production by the existing `mt data migrate apply` commands. The tick preflight already refuses a pass while a tick migration is pending.

## Integration Points

### Provides to Other Slices

- **302:** contract identity back to 2010 (`tick_definition`), pre-2020 sessions, `tick_bar_ledger` per-session volume for the cross-tier check, and `HistoryWant` (302 adds `statistics` to its allowed schemas).
- **303:** set ES and GC `history` wants, and `estimate` with an `ohlcv-1m` row. A bought bar day goes straight through ingest, status and coverage.
- **304:** per-family status verdicts and `caught_up` for the bar tier, to bring into `health`, `accounting` and `overview`.
- **305:** `tick_ohlcv_1m` as the provider-bar source, keyed for range reads per instrument.
- **306:** the same path over the full range.

### Consumes from Other Slices

- **300:** families, `supersedes_sql`, the registry, plan inclusion. If 300's open review changes these, this design follows the merged contract. Only D1 and D3 touch it.
- **221 and 232:** the session model this slice extends backwards (D6).

## Success Criteria

### Functional Requirements

1. An `ohlcv-1m` unit is selected by ingest once its same-shape, same-day definition unit is ingested. Before that it is skipped as `awaiting definitions`.
2. Ingesting the derived 2024-12-03 bar fixture writes one `tick_ohlcv_1m` row per bar and `tick_bar_ledger` rows per instrument and session, both sides of the daily break included. The unit is `ingested`, and provider = decoded = stored.
3. A bar outside the unit's UTC day fails the unit with `unit_day`. A bar with no definition fails it with `resolution`. A bar in a break fails it with `session_boundary`. A second current unit holding a key fails it with `overlap`.
4. A tick-tier unit and an `ohlcv-1m` unit of the same shape and day never supersede each other. Both stay current.
5. `mt data tick status` shows a separate `ohlcv-1m` section for an entry with a bar want, headed `load-complete only`. A session with ingested units and zero bars for a contract is `complete`. A `missing` day with no unit is `provider_hole`.
6. `mt data tick coverage --schema ohlcv-1m` reports ledger and raw bar counts and volume per session, and exits 3 on any mismatch. Without `--schema`, output is identical to today's.
7. `CME_EQUITY` and `CME_METALS` have sessions from 2010-06-06. Every pre-2020 exception and hours era cites its document. Sessions from 2020-01-01 on are unchanged.
8. A universe entry whose bar want reaches outside its definition want, or starts before 2010-06-06, fails at import, naming the field.
9. With the production universe (`history=()`), `mt data tick pass --estimate-only` plans exactly the same requests as before this slice.
10. `mt data tick estimate` prints an `ohlcv-1m` row with `is_plan_included`, judged alone.

### Technical Requirements

- Unit tests:
  - families total and disjoint, and `BAR_1M` holds `ohlcv-1m` only;
  - plan table total;
  - reader decodes the v1 and v3 DBN samples;
  - `HistoryWant` validation;
  - planner history wants (union with companions, ordering, month runs);
  - the hours-era rule and "latest era equals `trading_calendars`";
  - `day_unit` per family;
  - archive path under `MT_TICK_ARCHIVE_DIR`.
- Integration tests:
  - `tick_009` on empty and populated tick databases;
  - bar ingest end to end on the derived fixture;
  - every failure in criterion 3;
  - cross-family no-supersession;
  - status and coverage for the bar family;
  - the D6 migration (pre-2020 sessions present, 2020+ sessions byte-identical);
  - the drill pin and role privilege tests.
- `ruff`, `ruff format` scoped to touched files, and `mypy` clean. Source files stay within about 300 lines. `constants.py` is already over that, so the new bar constants go in a new `data/tick/bar_constants.py` if adding them pushes it further.

### Integration Requirements

- 302 can add `statistics` by adding a family, a projector, a plan row and a `HistoryWant` schema, and can read bar volume per session from `tick_bar_ledger`.
- 303 can buy ES and GC `ohlcv-1m` and definitions by editing `TICK_UNIVERSE` alone.
- The production tick pass and tick ingest behave as before after both migrations are applied.

### Verification Walkthrough

Run from the checkout root, with the test database URL exported (project test setup).

1. **Tests.**
   ```bash
   uv run pytest test/unit/data/tick test/unit/market -q
   uv run pytest test/integration/data -q -k "tick or calendar or drill_228"
   ```
   Expected: all pass, including the new `test_ohlcv_projector.py`, `test_tick_009_ohlcv_1m.py`, `test_history_wants.py` and the calendar-history migration test.

2. **Bar ingest on a throwaway database.** Create a database from `MT_TIMESCALE_TEST_URL`, and point `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` at it and a scratch directory. Run `uv run mt data init --database tick`. The integration test `test_ohlcv_ingest_e2e` records the 2024-12-03 definition and bar fixtures as units of an `ES.FUT` parent request. Then:
   ```bash
   uv run mt data tick ingest --json
   uv run mt data tick coverage --product ES --schema ohlcv-1m --start 2024-12-03 --end 2024-12-05 --json
   ```
   Expected: ingest reports the definition-gated bar unit `ingested`. Coverage shows the sessions dated 12-03 and 12-04 with `check: ok`, and ledger bars equal raw bars. Drop the database afterwards.

3. **Calendar history.** On a migrated test primary database:
   ```sql
   SELECT calendar_id, min(session_date), count(*) FROM trading_sessions
    WHERE calendar_id IN ('CME_EQUITY', 'CME_METALS') GROUP BY 1;
   ```
   Expected: both start on or before 2010-06-07 (the first session after the dataset start). Then `uv run python scripts/verify_cme_sessions.py --calendar CME_EQUITY /data/market-data/databento/GLBX-20240930-USM7UXXJBA` exits 0, as before.

4. **No production change.**
   ```bash
   uv run mt data tick pass --estimate-only --json
   ```
   Expected: the same planned requests and spend lines as on the slice's base commit, which is 0 requests for today's universe.

5. **Estimate row** (free metadata only).
   ```bash
   uv run mt data tick estimate --symbols ES.FUT --stype parent --start 2024-12-02 --end 2024-12-07 --json
   ```
   Expected: an `ohlcv-1m` row with list `cost_usd`, `is_plan_included: false` (no plan configured) and its own verdict.

6. **Production, after merge.** Apply the primary migration with `mt data migrate apply` and the tick migration with `mt data migrate apply --track tick`, then run `sudo mt-run data health`. Expected: the tick lines are unchanged. `mt data tick status` shows no `ohlcv-1m` section, because no entry wants bars until 303.

## Risk Assessment

### Technical Risks

- **Pre-2020 calendar accuracy.** Ten years of holidays, unscheduled closures and hours eras for two calendars, read from archived documents. A missed day only shows when bars for it are ingested (303, 306).
- **Old definitions.** Records from 2010 to 2019 may carry undefined windows or reused ids in shapes the 2024 files never showed (D12).

### Mitigation Strategies

- A mis-seeded day fails ingest loudly (`session_boundary`, naming the neighbouring sessions) and is never stored in a wrong session. A fix is a sourced seed row plus `mt data tick reset` of the failed units. The era test and the "2020+ unchanged" test bound the change to the new range.
- A bad definition fails its unit, naming the instrument. Bar units for that day then wait as `awaiting definitions` in status, so the gap is named.

## Implementation Notes

### Development Approach

Relative effort 5/5. The slice plan's 4/5 did not include the calendar history (D6). Suggested order, committing at each step:

1. `OHLCV_1M`, `BAR_1M`, plan row, `tick_009`; reader tests on the DBN samples.
2. `OhlcvProjector`, `unit_day`, registry entry, `_SUPERSEDED` by family; the derived fixture; ingest integration tests.
3. Calendar history (D6): sources first, then eras table and session builder, then exceptions and migration.
4. `HistoryWant`, validation, planner.
5. Status and coverage per family; estimate row.
6. Drill tables, roles, archive path pin.

**Split candidate.** Step 3 is self-contained and is needed by 301, 302 and 306. It could be its own slice ahead of this one, which would bring this slice back to its planned 4/5 and renumber the plan. The PM decides. This design is written so that step 3 can move out unchanged.

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit.
- The bar tier adds no query per bar. Status reads only the manifest and `tick_bar_ledger`, and the one raw scan is `coverage`'s, bounded by the range. Manifest-scale timing is 304's.
- Every SQL fragment built from constants (`supersedes_sql`, the family schema lists) keeps 300's rule: arguments are written in code and every value is bound.
