---
docType: slice-design
slice: ohlcv-1m-and-definition-history
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 300
- 301
interfaces:
- 303
- 304
- 305
- 306
- 307
dateCreated: 20261101
dateUpdated: 20261101
status: not_started
turns: 34
promptTokens: 3149976
cachedTokens: 3010012
cacheWriteTokens: 139906
completionTokens: 26702
durationSeconds: 254.4
costUsd: 2.255863
usageRounds: 2
pricedRounds: 2
usageByModel:
  claude-opus-5-5:
    rounds: 2
    costUsd: 2.2558633
    completionTokens: 26702
revision_number: 1
---

# Slice Design: ohlcv-1m and Definition History

## Overview

This slice adds Databento's `ohlcv-1m` (provider one-minute bars) as an archive schema, and lets a universe entry want `definition` over a range of its own, back to the start of the CME dataset (2010-06-06). Bars are stored in their own hypertable, `tick_ohlcv_1m`, keyed `(instrument_id, ts_event)`, where `ts_event` is the bar's open. They never go into `minute_ohlcv` (equities, symbol-keyed) or `tick_trade`.

A bar record carries only an `instrument_id`. It becomes a contract through `tick_definition` validity windows, so definitions over the whole bar range are a prerequisite of bar ingest. A 2010 to 2019 bar is placed in a CME session that slice 301 (CME calendar history) creates.

The tier ships with its completeness check. A bar unit is load-complete when the provider, decoded and stored counts agree and every bar lies in the unit's UTC day. `mt data tick status` and `mt data tick coverage` report the bar tier per CME session, beside the tick tier. A quiet session with few bars is not a hole.

Nothing is bought. No provider `ohlcv-1m` file is held, so the code is built and tested on the DBN project's sample files and on one real-shape bar day computed from a held `tbbo` day. Production configures no history wants, so the pass plans exactly what it plans today. Slice 304 (first real purchase) ingests the first real bar file.

## Value

- **Unblocks the rest of the plan.** 303 (statistics and the open-interest roll rule) needs contract identity back to 2010 and per-session bar volume. 304 buys bars and definitions through the path built here. 305 brings the bar verdicts into the operator surfaces. 306 serves time bars from `tick_ohlcv_1m`. 307 fills the full range.
- **History before 2024 becomes possible.** Once bought, research can reach back to 2010 on bars. Ticks cover only what is bought.
- **A verdict, not a hope.** `mt data tick status --product ES` says whether a bar range is load-complete, and `coverage --schema ohlcv-1m` proves stored rows match the ledger per session.
- **No spend and no behaviour change today.** The tick pipeline, the production pass and its spend lines are unchanged until 304 configures a range.

## Technical Scope

**Included**

- `TickSchema.OHLCV_1M`, family `BAR_1M`, its plan-inclusion row and its archived-schema membership (D1).
- Tick migration `tick_009_ohlcv_1m`: widen `tick_request_schema_check`, create `tick_ohlcv_1m` with its columnstore policy, create `tick_bar_ledger` (D2).
- `data/tick/ohlcv_1m_projector.py`: the `ohlcv-1m` projector, registered in `INGEST_PROJECTORS` (D3).
- A new ingest check, `unit_day`, applied by the bar projector: the load-complete rule (D4).
- Universe history wants (`HistoryWant`) for `ohlcv-1m` and `definition`, validated at import, planned beside tier wants (D5). Production configures none.
- `mt data tick status` and `coverage` per schema family (D6).
- Fixtures: the DBN `ohlcv-1m` samples and one derived real-shape bar day (D7).
- An `ohlcv-1m` row in `mt data tick estimate`, judged alone (D8).
- Backup and roles: archive path pinned, `tick_bar_ledger` in the restore drill's table set, a `tick_ohlcv_1m` fingerprint, both tables in the enumerated tick write surface (D9).
- Definition history: size check and failure shape for pre-2020 records (D10).

**Excluded**

- Any purchase, or any change to the production universe's ranges (304, 307).
- `statistics`, content completeness (bar volume against `statistics` cleared volume) and the open-interest roll rule (303).
- `health`, `accounting`, `overview` and the status coverage aggregates. The slice plan assigns these to 305, the futures completeness surfaces slice; this slice ships `status` and `coverage`, the two the plan entry names.
- `ohlcv-1s` and the explicit "no ticks wanted" marker it needs (future work in the slice plan). Provider `ohlcv-1h` and `ohlcv-1d` are not archived here. They are never served, and whether to archive them is decided at the architecture's recorded revisit (PM, 2026-10-28).
- Serving bars through `/api/v1/futures/*` or `mt data tick get` (306). No API change here (D11).
- Tick-derived against provider bar parity. It needs provider bars over a window where ticks are held, which first exists after 304; 306 measures it when it retires the read-time tick bars.
- CME calendar data before 2020 (301).
- Renaming the `tick` track, `MT_TICK_*` or `mt data tick` (architecture, "Generalising the archive beyond tick tiers").

## Dependencies

### Prerequisites

- **300 (archive generalisation), merged:** `SchemaFamily`, `FAMILY_SCHEMAS`, `supersedes_sql`, `IUnitProjector`, `Loaded`, `INGEST_PROJECTORS`, `projector_for`, `PLAN_INCLUDED_HISTORY`, `is_plan_included`, and the `has_definitions` ingest prerequisite.
- **301 (CME calendar history):** `CME_EQUITY` and `CME_METALS` sessions from 2010-06-07, dated regular hours, and `CME_DATASET_START = date(2010, 6, 6)` in `market/schema/seed_cme_hours.py`. This slice's branch is forked from the target after 301 merges.
- **DBN sample files:** `test_data.ohlcv-1m.v1.dbn.zst` and `test_data.ohlcv-1m.v3.dbn.zst` in `databento/dbn` `tests/data/` at tag `v0.70.0`, the tag the existing fixtures pin. Both are listed there (checked 2026-11-01). The first 302 design read them: 2 `GLBX.MDP3` ESH1 bars (instrument 5482) on 2020-12-28, with `ts_event` at the minute's open (13:00:00 and 13:01:00 UTC).
- **Held data:** the 2024-12-03 `tbbo` day in the tick archive and the `real/glbx-mdp3-20241203.definition.dbn.zst` fixture.

### Interfaces Required

- `IUnitProjector` and `Loaded` (300 D2), unchanged.
- `ingest_records.resolve`, `locate_sessions`, `LedgerAccumulator` and `LedgerRow`, reused unchanged.
- `ingest_select`'s `has_definitions`: a bar unit waits for an ingested definition unit of the same shape and day.
- `tick_day_condition` and `tick_dataset_edge` (223), unchanged. Conditions are per dataset and day, not per schema.
- `TradingCalendar` and `SessionIndex` over 301's sessions.

## Architecture

### Component Structure

```
data/tick/constants.py          TickSchema.OHLCV_1M; ARCHIVED_SCHEMAS gains it;
                                ESTIMATE_SCHEMAS appends it
data/tick/bar_constants.py      tick_ohlcv_1m chunk interval, compress-after,
                                segment/order columns (new; constants.py is
                                already 392 lines)
data/tick/schema_families.py    SchemaFamily.BAR_1M: (OHLCV_1M,);
                                FAMILY_LEDGER: family -> ledger table
config/databento_plans.py       STANDARD[OHLCV_1M] = None (all history)
data/tick/ohlcv_1m_projector.py Ohlcv1mProjector: load, stored_count,
                                finish (tick_bar_ledger), delete_unit
data/tick/projectors.py         INGEST_PROJECTORS[OHLCV_1M] = OHLCV_1M
data/tick/ingest_checks.py      IngestCheck.UNIT_DAY, unit_day_reason
data/tick/ingest_plan.py        superseded-unit times read from the unit
                                family's ledger
data/tick/universe.py           HistoryWant; TickUniverseEntry.history
data/tick/planner.py            history wants beside tier and companion wants
data/tick/status_reads.py       Shape and ledger/raw reads take a family
data/tick/tick_status.py        day_unit ranks within the unit's family
data/tick/tick_status_build.py  one verdict set per wanted family
data/tick/tick_coverage.py      build_coverage(..., family)
cli/commands/tick_*             status sections per family; coverage --schema
market/schema/migrations/tick.py   tick_009_ohlcv_1m
scripts/drill_228_*.py          tick_bar_ledger and the tick_ohlcv_1m fingerprint
scripts/provision_tick_roles.sql   two tables added to the write surface
scripts/cut_tick_fixtures.py    derives the bar fixture from a tbbo day
```

### Data Flow

**Acquisition (from 304, when a range is configured).** The planner reads each entry's `history` wants beside its tier. A want's days are the product's session days inside the want's range, built the same way as tier days. Covered days are dropped, and the rest are tallied by condition and grouped into month runs exactly as tier days are. Within a day, definitions are ordered before every other schema (224 TD4). Plan inclusion, the spend guard and the manifest writes are 300's and do not change. Definition units are projected by the acquisition pass, as today (300 D3).

**Ingest.** `select_units` now also selects `ohlcv-1m` units, because the schema is a key of `INGEST_PROJECTORS`. Each waits until its same-shape, same-day definition unit is ingested. For each decoded batch, `Ohlcv1mProjector.load`:

1. checks that every `ts_event` lies in the unit's UTC day (D4);
2. resolves every `instrument_id` through the product's definitions (`resolve`);
3. places every bar in a session (`locate_sessions`);
4. adds the batch to a `LedgerAccumulator`, with `volume` as the size;
5. `COPY`s the batch into `tick_ohlcv_1m`.

The worker then checks provider = decoded = stored, and `finish` writes the `tick_bar_ledger` rows. A key collision is the existing `overlap` failure.

**Status and coverage.** Status builds one verdict set per family an entry wants: the tick tier as today, and `BAR_1M` when the entry has an `ohlcv-1m` want over that want's range. A day's unit is the highest-ranked current unit of that family and shape. Conditions come from `tick_day_condition`. `coverage --schema ohlcv-1m` compares, per instrument and session, the ledger's bar count and volume with `count(*)` and `sum(volume)` over `tick_ohlcv_1m`.

### State Management

- **New tables (tick database):** `tick_ohlcv_1m` (provider bars) and `tick_bar_ledger` (per unit, instrument and session: bar count, volume, first and last bar time). Both are written once per unit, inside the unit's ingest transaction.
- **Changed table:** `tick_request.schema` may be `ohlcv-1m`.
- **Configuration:** `TickUniverseEntry` gains `history`, defaulting to `()`. The plan table gains one row.

## Technical Decisions

### D1 — `ohlcv-1m` is its own family, `BAR_1M`
Status: decided

`TickSchema.OHLCV_1M = "ohlcv-1m"`, `SchemaFamily.BAR_1M = (OHLCV_1M,)`, `ARCHIVED_SCHEMAS |= {OHLCV_1M}`, and `PLAN_INCLUDED_HISTORY[STANDARD][OHLCV_1M] = None` (the pricing page read on 2026-10-28 lists `ohlcv-1m` for all history). `STORED_TIERS`, `TICK_TIERS` and `COMPANION_SCHEMAS` do not change: a bar schema is neither a tick tier nor a companion.

The family is named for its interval. Rank orders schemas within a family, so a shared `BAR` family would let a later `ohlcv-1s` unit supersede, and delete, the `ohlcv-1m` unit of the same day. The two are different series and both are kept.

- *Rejected: one `BAR` family for every interval.* Within-family rank would delete one interval when the other arrived.

### D2 — Storage: `tick_ohlcv_1m` and one bar ledger
Status: decided

`tick_ohlcv_1m` is an integer-nanosecond hypertable on `ts_event`:

| Column | Type | Notes |
|---|---|---|
| `ts_event` | `BIGINT NOT NULL` | bar open, epoch ns |
| `instrument_id` | `BIGINT NOT NULL` | resolved through `tick_definition` |
| `publisher_id` | `INTEGER NOT NULL` | as delivered, as in `tick_trade` |
| `open`, `high`, `low`, `close` | `BIGINT NOT NULL` | DBN fixed price, scale 10⁹ (`TICK_PRICE_SCALE`) |
| `volume` | `BIGINT NOT NULL` | |
| `unit_id` | `BIGINT NOT NULL` | no foreign key, as in `tick_trade` (222 TD6) |

- **Key:** `PRIMARY KEY (instrument_id, ts_event)`. There is one provider bar per instrument and minute. Two current units holding the same key fail with `overlap`, never a silent `ON CONFLICT`.
- **Named for its interval.** The slice plan gives each bar interval its own table, because `ohlcv-1s` is about 20 times larger (ES, all history: 8.7 M 1m bars against 170.6 M 1s bars) and needs its own chunk sizing. This slice creates only the 1m table.
- **Chunks:** 7 days. By the journal 20260719 rule (span ÷ 1,000 to 2,000 chunks), 2010 to 2030 gives about 1,040 chunks.
- **Columnstore:** segment by `instrument_id`, order by `ts_event`, compress after 14 days, through the existing `tick_now_ns` integer-now function. History is compressed soon after it is written; 226 measured supersession deletes on compressed chunks at 0.10 s.
- **Size:** about 24.6 M rows for ES and GC over the full range (architecture cost table), a fraction of the held `tick_trade`. No capacity step.

`tick_bar_ledger` has the columns, key and `CHECK`s of `tick_ingest_ledger` (`unit_id`, `instrument_id`, `calendar_id`, `session_date`, `record_count`, `volume`, `first_event_ns`, `last_event_ns`), where `record_count` is the bar count. It has no interval column: the slice plan makes it the one ledger for bars, with the schema reached through unit and request, as the tier is for `tick_ingest_ledger`. It is kept apart from `tick_ingest_ledger` because that ledger's readers (catalog, the volume roll rule, status, `get_plan`) mean trades. Bar volume there would double-count the roll rule's volume.

`FAMILY_LEDGER` in `schema_families.py` maps `TICK_TIER` to `tick_ingest_ledger` and `BAR_1M` to `tick_bar_ledger`, so status, coverage and the superseded-unit read name a ledger from one table. It is needed in this slice because two families now have a ledger.

- *Rejected: bar rows in `tick_ingest_ledger`.* Breaks the volume roll rule.
- *Rejected: no bar ledger, completeness from `count(*)`.* Status reads the manifest and the ledger, never the raw table (220).

### D3 — An `ohlcv-1m` projector, built for one interval
Status: decided

`Ohlcv1mProjector` implements `IUnitProjector` for `tick_ohlcv_1m` only; `OHLCV_1M_PROJECTOR` is the registry entry. It follows `TickTradeProjector`'s shape: one `COPY` per unit, with `resolve`, `locate_sessions` and `LedgerAccumulator.add(ids, positions, volume, ts)` per batch. `stored_count` and `delete_unit` are bounded by the unit's first and last bar times. The table name is a code constant interpolated into SQL; every value is bound. No interval parameter or storage abstraction is built: the `ohlcv-1s` slice extracts the shared parts when a second interval exists.

`ingest_plan`'s superseded-unit read joins the ledger of the unit's family through `FAMILY_LEDGER` instead of always `tick_ingest_ledger`, because a bar unit's times are in `tick_bar_ledger`.

Reused checks: `resolution` (a bar with no definition window), `session_boundary` (a bar in a daily break or outside the populated calendar), `overlap`, `shape` and `decode`. The ledger is defined for parent symbology, as 225 decided for ticks.

- *Rejected: one projector for ticks and bars, parameterised by table.* The columns, the ledger's size field and the checks differ between `tick_trade` and bars; a shared class would be switches.

### D4 — Load-complete: the counts plus the unit's UTC day
Status: decided

A bar unit is load-complete when it is ingested. Ingest already requires provider = decoded = stored (225's `counts` check). The bar projector adds that every `ts_event` lies in `[unit_date 00:00 UTC, unit_date + 1 day)`. The manifest states a unit as its UTC day and the provider's record count, so this is the plan's "counts and first and last event time against the manifest". The new check is `IngestCheck.UNIT_DAY`, reason `unit_day: N records outside <date>; first at <ts>`. Tick projection keeps today's checks.

Load-complete is all this slice can prove. Content completeness needs `statistics` (303), so the bar section of `status` is headed `load-complete only`.

### D5 — What the universe wants: `HistoryWant`
Status: decided

`TickUniverseEntry` gains `history: tuple[HistoryWant, ...] = ()`, with `HistoryWant(schema, start, end)`: UTC days, end exclusive, `end=None` meaning up to the availability edge. A want takes the entry's symbols and stype, so its units share the entry's request shape and the `has_definitions` prerequisite matches bar units to definition units. Validation at import, each failure naming the entry and field:

- the schema is `ohlcv-1m` or `definition` (303 adds `statistics`);
- at most one want per schema;
- `start` is not before `CME_DATASET_START`;
- the entry has an explicit `tier`. `tier=None` means "not chosen yet", not "no ticks wanted", so a history want on such an entry is refused. A bars-only product needs the explicit no-ticks value the `ohlcv-1s` slice adds;
- an `ohlcv-1m` want needs a `definition` want, and its range must lie inside it (an open `end` needs an open definition `end`). A bar day without a definition day would wait on `awaiting definitions` forever, so this is refused at import, not found later.

The planner's `_tier_wants` becomes `_wants` over the tier and the history wants. Both produce `(shape, days)` and go through the same classification, runs and ordering. A definition want is a want of its own, not a companion. The companion bundle (`_companion_wants`) stays a tick-tier rule (300 D4), and the union of the two definition sets is what gets bought.

Production keeps `history=()` for ES and GC. With a 2010 definition want and no plan configured, the pass would plan $2.72 of definitions against the $0.50 ceiling and refuse every run. Setting the ranges, and the ceiling for that run, is 304's job.

- *Rejected: one field per schema* (`ohlcv_1m_range`, `definition_range`). Each later schema would add a field. A tuple of wants grows by data.
- *Rejected: companion definitions for bar days.* It would hide the definition range being bought, which is a range of its own.

### D6 — Status and coverage per family
Status: decided

- `Shape.params` takes a family's schemas instead of `STORED_TIERS`, and ledger reads take the family's ledger from `FAMILY_LEDGER`. Status, coverage, `ledger_totals` and contract lines get the family from the caller.
- `tick_status.day_unit` ranks within the unit's family through `FAMILY_SCHEMAS` instead of `TICK_TIER_RANK`. Its tick-tier result is unchanged.
- `mt data tick status` prints, per product, one section per wanted family: the tick tier as today, then `ohlcv-1m` headed `load-complete only`, each with session counts by status, condition findings and `caught up`. A family with no want is not printed. `--json` gains a `families` list, because an entry now has two families to report; the existing top-level product fields stay the tick tier's, so today's JSON consumers do not break.
- `mt data tick coverage` gains `--schema`. The default is the entry's tier, which is today's output. `--schema ohlcv-1m` reports per session: status, condition, unit ids, ledger bars and volume, raw bars and volume, and the check. The raw read is the one bounded scan of `tick_ohlcv_1m`, as `raw_counts` is of `tick_trade`. A mismatch exits 3, as today.
- **Sparse bars.** Completeness is unit-level (225 TD10). A session whose units are ingested is `complete`, however few bars any contract has. A `degraded` or `missing` day condition is shown as the session's condition, and a `missing` day with no unit is `provider_hole` (225's rule, reused).

### D7 — Fixtures: DBN samples and one derived real-shape day
Status: decided

No provider `ohlcv-1m` file for a parent-symbology CME day is held; the archive and `/data/market-data/databento` hold `trades` and `tbbo` only. So:

- **DBN samples**, copied unmodified and recorded in `test/fixtures/databento/SOURCES.md`: `test_data.ohlcv-1m.v1.dbn.zst` and `.v3.dbn.zst`. They prove the reader decodes the bar record in both DBN versions and pin field names, price scale and bar-open `ts_event`. Their instrument (5482, 2020-12-28) has no CME definition fixture, so projector tests that use them seed one `tick_definition` row.
- **One derived day:** `real/glbx-mdp3-20241203.ohlcv-1m.dbn.zst`, built by extending `scripts/cut_tick_fixtures.py`. It keeps the provider's 2024-12-03 `tbbo` header with the schema field set to `ohlcv-1m`, and holds trade-only one-minute bars computed from that day's full held `tbbo` file. `SOURCES.md` marks it **Not purchased**, as 225's derived `trades` fixture is. With the real 2024-12-03 definition fixture it exercises parent symbology, real instrument ids including spreads, both sides of the daily break, resolution and the ledger. It makes no claim of provider parity.

304 ingests the first real bar file, and its coverage verdict is that slice's evidence.

### D8 — `mt data tick estimate` shows `ohlcv-1m`
Status: decided

`ESTIMATE_SCHEMAS` appends `OHLCV_1M`. Its row shows list cost, records, bytes and `is_plan_included`, and its verdict judges its own marginal cost against the ceiling. It is not part of the tick-tier bundle (`estimate._row` keeps the bundle for `STORED_TIERS`). 304 sizes "the most recent years of ES with the rest of the cap" with it, on free metadata calls.

### D9 — Backup and roles are mechanical
Status: decided

- **Archive files.** Bar and definition files use the existing archive layout under `MT_TICK_ARCHIVE_DIR`, which `cron_system_backup.sh` already includes (223). A unit test pins that an `ohlcv-1m` unit's path resolves under the archive directory.
- **Database.** Both tables are in the tick database, which the tick cluster's base backup and WAL archive cover whole (227).
- **Restore drill.** `tick_bar_ledger` joins `drill_228_bookkeeping.BOOKKEEPING_TABLES`, and `drill_228_fingerprint` gains a `tick_ohlcv_1m` fingerprint beside `tick_trade`'s. The existing pin test asserts the drill's tables equal the migrated tick tables, so it fails until this is done.
- **Roles.** The tables are created by the migrate role, so the default privileges in `provision_tick_roles.sql` already give the app role DML on them; no production re-run is needed. The script's enumerated write surface (seven tables today) gains both, and `test_tick_role_privileges.py` covers them.

### D10 — Definition history: size and failure shape
Status: decided

A definition file re-sends each listed instrument daily, and `tick_definition` keeps one row per `(instrument_id, activation_ns)`; a same-key re-send is a no-op (224 TD5). The table grows by distinct instruments, not by the 3.3 M records billed for ES and GC together. Even if every record were distinct, that is under one month of `tick_trade`, so no capacity work is needed; 304 records the measured row count.

A pre-2020 record with an undefined activation or expiration fails its unit, naming the instrument (222 F007). It is never patched with an invented window. Bar units for that day then wait as `awaiting definitions`, so the gap is named in status.

### D11 — No API change
Status: decided

306 answers the API question for bars: it serves provider `ohlcv-1m` for 1m and every coarser bar and retires the read-time tick bars (journal 20261101 ADR). Serving bars here would ship a second bars path before that retirement. `/api/v1/futures/*` and `mt data tick get` do not change.

## Implementation Details

### Database / Storage Schema

`tick_009_ohlcv_1m` (tick track, next after `tick_008_plan_inclusion`; confirm the last id at implementation):

- Drop and re-add `tick_request_schema_check`, rendered from `ARCHIVED_SCHEMAS` (222 TD9).
- `CREATE TABLE IF NOT EXISTS tick_ohlcv_1m (...)` per D2; `create_hypertable(..., chunk_time_interval => <7 days in ns>, create_default_indexes => FALSE, if_not_exists => TRUE)`; `set_integer_now_func('tick_ohlcv_1m', 'tick_now_ns')`; the columnstore `ALTER` on the empty table; `add_columnstore_policy`. The interval and policy values come from `bar_constants.py`.
- `CREATE TABLE IF NOT EXISTS tick_bar_ledger (...)` per D2, with a foreign key to `tick_archive_unit` as `tick_ingest_ledger` has.

Applied in production by `mt data migrate apply --track tick` in the release that ships the slice; the tick preflight already refuses a pass while a tick migration is pending.

## Integration Points

### Provides to Other Slices

- **303:** contract identity back to 2010 in `tick_definition`; per-session bar volume in `tick_bar_ledger` for the cross-tier check; `HistoryWant`, to which 303 adds `statistics`; and the family, ledger and projector pattern. Before 2024 there are no ticks, so a volume-based roll rule over 2010 to 2023 can read only bar volume: 303 must read `tick_bar_ledger` for bar-only ranges, not `tick_ingest_ledger`.
- **304:** ES and GC `history` wants set by editing `TICK_UNIVERSE`, and the `ohlcv-1m` estimate row. A bought bar day goes straight through ingest, status and coverage.
- **305:** per-family session verdicts and `caught up` for the bar tier, to bring into `health`, `accounting` and `overview`.
- **306:** `tick_ohlcv_1m` as the provider-bar source, keyed for range reads per instrument.
- **307:** the same path over the full range.

### Consumes from Other Slices

- **300:** families, `supersedes_sql`, the projector registry, plan inclusion. Only D1, D3 and D6 touch them.
- **301:** pre-2020 sessions, dated hours and `CME_DATASET_START`. A bar outside a populated session fails with `session_boundary`, naming the sessions either side; it is never stored in a wrong session.

## Success Criteria

### Functional Requirements

1. An `ohlcv-1m` unit is selected by ingest once its same-shape, same-day definition unit is ingested; before that it is skipped as `awaiting definitions`.
2. Ingesting the derived 2024-12-03 bar fixture writes one `tick_ohlcv_1m` row per bar and `tick_bar_ledger` rows per instrument and session, on both sides of the daily break. The unit is `ingested`, and provider = decoded = stored.
3. A bar outside the unit's UTC day fails the unit with `unit_day`. A bar with no definition fails with `resolution`, a bar in a daily break with `session_boundary`, and a second current unit holding a key with `overlap`.
4. A tick-tier unit and an `ohlcv-1m` unit of the same shape and day never supersede each other; both stay current.
5. `mt data tick status` shows a separate `ohlcv-1m` section, headed `load-complete only`, for an entry with a bar want. A session with ingested units and few or no bars for a contract is `complete`. A `missing` day with no unit is `provider_hole`.
6. `mt data tick coverage --schema ohlcv-1m` reports ledger and raw bar counts and volume per session, and exits 3 on any mismatch. Without `--schema`, the output is identical to today's.
7. On a database migrated through 301, a 2012 bar ingests into its CME session. Against a calendar without that session it fails with `session_boundary`.
8. A universe entry fails at import, naming the field, when its bar want reaches outside its definition want, has no definition want, starts before `CME_DATASET_START`, repeats a schema, or sits on an entry with `tier=None`.
9. With the production universe (`history=()`), `mt data tick pass --estimate-only` plans exactly the requests it planned before this slice.
10. `mt data tick estimate` prints an `ohlcv-1m` row with `is_plan_included`, judged alone.
11. A pre-2020 definition record with an undefined activation or expiration fails its unit, naming the instrument.

### Technical Requirements

- Unit tests:
  - families total and disjoint; `BAR_1M` holds `ohlcv-1m` only; `FAMILY_LEDGER` has a key for every family that appears as a key of `INGEST_PROJECTORS` (`TICK_TIER` and `BAR_1M`), and the `DEFINITION` family is deliberately absent because definitions are projected by the acquisition pass, not by ingest;
  - plan table total over `TickSchema`;
  - the reader decodes the v1 and v3 DBN samples, with bar-open `ts_event`;
  - `HistoryWant` validation, one test per rule;
  - planner history wants: union with companions, ordering, month runs;
  - `day_unit` per family;
  - `unit_day_reason` text;
  - an `ohlcv-1m` unit's archive path lies under `MT_TICK_ARCHIVE_DIR`.
- Integration tests:
  - `tick_009` on an empty and on a populated tick database;
  - bar ingest end to end on the derived fixture;
  - every failure in criterion 3;
  - no supersession across families;
  - status and coverage for the bar family, including the exit-3 mismatch;
  - a 2012 bar placed in its session on a database migrated through 301;
  - the drill pin test and the role privilege test.
- `ruff check`, `ruff format` scoped to touched files, and `mypy` clean. Source files stay within about 300 lines; `tick_ohlcv_1m` storage constants go in `bar_constants.py`, not `constants.py`.
- `SOURCES.md` records the two samples and the derived day.

### Integration Requirements

- 303 can add `statistics` with a family, a projector, a plan row and a `HistoryWant` schema, and reads bar volume per session from `tick_bar_ledger`.
- 304 can buy ES and GC `ohlcv-1m` and definitions by editing `TICK_UNIVERSE` alone.
- The production tick pass and tick ingest behave as before once `tick_009` is applied.

### Verification Walkthrough

Run from the checkout root with the test database URL exported from `.env` (project test setup). Steps 2 and 3 need 301 merged.

1. **Tests.**
   ```bash
   uv run pytest test/unit/data/tick -q
   uv run pytest test/integration/data -q -k "tick or drill_228"
   ```
   Expected: all pass, including the new bar projector, `tick_009`, history-want and pre-2020 placement tests.

2. **Bar ingest on throwaway databases.** Create two databases from `MT_TIMESCALE_TEST_URL`: a primary one, migrated through 301 with `uv run mt data init` under `MT_TIMESCALE_DB_URL`, for the calendars; and a tick one, with `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` pointed at it and a scratch directory. Run `uv run mt data init --database tick`. Record the 2024-12-03 definition and bar fixtures as units of an `ES.FUT` parent request; the helper module `test/integration/data/tick_bar_helpers.py` (used by `test_bar_ingest_e2e`) does this, and no CLI command records an arbitrary file as a unit. Then:
   ```bash
   uv run mt data tick ingest --json
   uv run mt data tick coverage --product ES --schema ohlcv-1m --start 2024-12-03 --end 2024-12-05 --json
   uv run mt data tick status --product ES
   ```
   Expected: ingest reports the bar unit `ingested` after the definition unit. Coverage shows the sessions dated 2024-12-03 and 2024-12-04 with `check: ok` and ledger bars equal to raw bars. Status shows an `ohlcv-1m` section headed `load-complete only`. Drop both databases afterwards.

3. **Pre-2020 placement.**
   ```bash
   uv run pytest test/integration/data -q -k "bar_pre2020"
   ```
   Expected: pass. A 2012 bar lands in its CME session; the same bar against a calendar without that session fails with `session_boundary`.

4. **Production pass unchanged.**
   ```bash
   uv run mt data tick pass --estimate-only --json
   ```
   Expected: the same planned requests and spend lines as on the slice's base commit (0 requests for today's universe).

5. **Estimate row** (free metadata calls only).
   ```bash
   uv run mt data tick estimate --symbols ES.FUT --stype parent --start 2024-12-02 --end 2024-12-07 --json
   ```
   Expected: an `ohlcv-1m` row with list `cost_usd`, `is_plan_included: false` (no plan configured) and its own verdict.

6. **Production, after merge.** Apply `tick_009` with `mt data migrate apply --track tick`, then `sudo mt-run data health`. Expected: the tick lines are unchanged, and `mt data tick status` shows no `ohlcv-1m` section, because no entry wants bars until 304.

## Risk Assessment

### Technical Risks

- **Old definitions.** Records from 2010 to 2019 may carry undefined windows or reused instrument ids in shapes the 2024 files never showed. No pre-2020 definition file is held, so this cannot be exercised before 304.
- **Bar layout assumptions.** The derived fixture is computed by us. A provider detail it does not show (a bar for an instrument with no definition that day, or a publisher id we do not expect) first appears in 304.

### Mitigation Strategies

- Both fail loudly, never silently: a bad definition fails its unit naming the instrument and leaves its bar days `awaiting definitions`; an unresolvable bar fails `resolution`. 304's purchase is small, so a defect found there costs little.
- The DBN samples pin the provider's record layout in two versions, so the derived fixture differs from a real file only in its values.

## Implementation Notes

### Development Approach

Relative effort 4/5. Suggested order, committing at each step:

1. `OHLCV_1M`, `BAR_1M`, `FAMILY_LEDGER`, plan row, `bar_constants.py`, `tick_009`; reader tests on the DBN samples.
2. `Ohlcv1mProjector`, `unit_day`, the registry entry, the superseded read by family; the derived fixture; ingest integration tests.
3. `HistoryWant`, validation, planner.
4. Status and coverage per family; the estimate row.
5. Drill tables, roles, archive path pin.

### Special Considerations

- Run `ruff format` on touched files only, and check `git diff` for swept lines before each commit.
- The bar tier adds no query per bar. Status reads only the manifest and `tick_bar_ledger`; the one raw scan is `coverage`'s, bounded by the range. Manifest-scale timing is 305's.
- SQL built from constants (`supersedes_sql`, family schema lists, the bar table name) keeps 300's rule: fragments are written in code and every value is bound.
