---
docType: slice-design
slice: statistics-and-the-open-interest-roll-rule
project: trading
parent: user/architecture/300-slices.futures-historical-bars-statistics-and-tick-aggregation.md
dependencies:
- 300
- 301
- 302
interfaces:
- 304
- 305
- 306
- 307
dateCreated: 20261103
dateUpdated: 20261103
status: not_started
turns: 54
promptTokens: 6906274
cachedTokens: 6725864
cacheWriteTokens: 180302
completionTokens: 59479
durationSeconds: 717.8
costUsd: 3.977941
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-opus-5-5:
    rounds: 3
    costUsd: 3.9779413000000003
    completionTokens: 59479
revision_number: 2
---

# Slice Design: Statistics and the Open-Interest Roll Rule

## Overview

This slice adds Databento's `statistics` schema as an archive schema with its own table, `tick_statistics`, and builds the open-interest roll rule on it.

A statistics record describes a session that has already closed. CME publishes session D's settlement, cleared volume and open interest during the next session, so the record arrives in a later UTC-day unit than the session it describes. Each record is assigned to a session from its reference time (`ts_ref`), never its event time. A correction can arrive later, so the value the system reads is resolved per contract, statistic and session by update action and receive time.

The tier ships with its completeness check: load-complete per unit, a content check that every listed outright has its settlement, cleared volume and open interest, and the cross-tier check of a session's traded volume (ticks or `ohlcv-1m` bars) against `statistics` cleared volume.

The open-interest rule (`n`, Databento's letter) ranks contracts by stored open interest. It is evaluated against calendar, volume and Databento's own `.n.0` for ES and GC on real data: the slice buys ES and GC `statistics` for the window where ticks are held (2024-08-30 to 2024-12-31), with the October definitions it lacks: $0.051 at list price (measured 2026-11-03), usage-billed, inside the existing $0.50 ceiling. GC is the case that matters, because Databento's `GC.n.0` rolls 6 to 12 days before `GC.v.0` (229 TD6).

The volume rule also learns to read bar volume for sessions with no ticks, as 302 handed over: before 2024 only bars exist.

## Value

- **The deferred roll rule ships.** 229 deferred open interest because ES gained nothing from it. GC rolls on open interest a week or more before volume, so research on GC needs it.
- **Each roll rule can answer over the whole history.** With bar volume as a basis, `v.0` can answer for 2010 to 2023 once bars are bought (307). `n.0` needs only `statistics`, which cost $1.45 for both products over all history.
- **Bars get a content check.** Load-complete was all 302 could prove. Cleared volume per contract and session tells whether a session's bars or ticks hold the traded volume, and an excess shows a load defect.
- **Statistics are proven on real data before 304 relies on them.** The record definition's semantics are confirmed on purchased files, and 304's purchase runs on a path already exercised.

## Technical Scope

**Included**

- `TickSchema.STATISTICS`, family `STATISTICS`, its plan row, archived-schema and estimate membership (D1).
- Tick migration `tick_010_statistics`: re-render `tick_request_schema_check`, create `tick_statistics` and `tick_statistics_ledger` (D2).
- `StatisticsProjector`, registered in `INGEST_PROJECTORS`, with session assignment from `ts_ref` and the new `stat_reference` check (D3, D4).
- Revision resolution, read in one place (D5).
- `statistics` as a `HistoryWant` schema (D6).
- Status and coverage for the `STATISTICS` family, including the content check and the cross-tier volume check (D7, D8).
- `RollMethod.OPEN_INTEREST = "n"`, its inputs and resolver (D9).
- Volume-rule basis per session: tick ledger where the tick tier is complete, else bar ledger (D10).
- One small real purchase, the evaluation and the recorded fixtures (D11).
- Backup and roles, as 302 D9 (D12).
- API: `n` accepted wherever a rule is, with documentation (D13).

**Excluded**

- Serving open interest or settlement through `/api/v1/futures/*` (306 decides).
- `health`, `accounting`, `overview` and the status coverage aggregates (305).
- Interpreting `stat_flags` (final or preliminary, actual or theoretical). Flags are stored as delivered; no reader here needs them (D5).
- Any change to the production universe. Production keeps `history=()` until 304.
- Pre-2020 statistics. Nothing before 2024-08-30 is bought here (304, 307).
- Making `n.0` the project default or adding it to `STATUS_ROLL_RULES`.

## Dependencies

### Prerequisites

- **300 (archive generalisation):** families, `supersedes_sql`, `IUnitProjector`, `INGEST_PROJECTORS`, plan inclusion, the `has_definitions` prerequisite.
- **301 (CME calendar history):** sessions from 2010-06-07 and `CME_DATASET_START`.
- **302 (ohlcv-1m and definition history), merged:** `HistoryWant` and its validation, `FAMILY_LEDGER`, per-family status and coverage, `unit_day_check`, `tick_bar_ledger`, and the derived 2024-12-03 bar fixture.
- **DBN statistics record**, confirmed against `databento/dbn` at tag `v0.70.0` (`rust/dbn/src/record.rs`, `enums.rs`), read 2026-11-03:
  - `StatMsg` fields: `ts_recv` (the index timestamp, which Databento splits files on), `ts_ref`, `price`, `quantity`, `sequence`, `ts_in_delta`, `stat_type`, `channel_id`, `update_action`, `stat_flags`. There is no separate trading-date field.
  - `ts_ref` "will indicate the trading date" for `SETTLEMENT_PRICE` (3), `CLEARED_VOLUME` (6, "contracts cleared … on the previous trading date") and `OPEN_INTEREST` (9). It is `UNDEF_TIMESTAMP` when unused.
  - `update_action` is `NEW` (1) or `DELETE` (2). Delete is "only used with some stat types".
  - The settlement price's flags say final or preliminary and actual or theoretical. The bit layout is not in the SDK source.
- **DBN samples:** `test_data.statistics.v1.dbn.zst` and `.v3.dbn.zst` in `databento/dbn` `tests/data/` at `v0.70.0`. Each holds two `GLBX.MDP3` records for instrument 146945 (`LOWEST_OFFER`, `TRADING_SESSION_HIGH_PRICE`) with `ts_ref` undefined. They pin decoding in both versions, not the referenced statistics.
- **Held data:** ES and GC `tbbo` units and definitions for 2024-08-30 to 2024-12-31 (definitions for October are not held; the purchase buys them), and the 229/232 roll fixtures under `test/fixtures/tick/roll/`.
- **Free metadata:** `metadata.get_cost` for `statistics`, parent symbols, 2024-08-30 to 2025-01-01, read 2026-11-03: ES.FUT 170,528 records, $0.0127; GC.FUT 324,229 records, $0.0242.

### Interfaces Required

- `ingest_records.resolve` and `LedgerAccumulator` patterns; `OrdinalCarry` for the duplicate-key ordinal.
- `ingest_select`'s `has_definitions`: a statistics unit waits for its same-shape, same-day definition unit.
- `TradingCalendar` and `SessionIndex` to turn a reference date into a session and to find the next session's close.
- `roll_inputs`, `roll_resolve`, `roll_types` and `active_contract` (229, 232).
- `scripts/record_229_roll_fixture.py` and `test/tick_support/roll_fixture.py`.

## Architecture

### Component Structure

```
data/tick/constants.py            TickSchema.STATISTICS; HISTORY_SCHEMAS,
                                  ARCHIVED_SCHEMAS, ESTIMATE_SCHEMAS gain it;
                                  RollMethod.OPEN_INTEREST = "n";
                                  VOLUME_RANK_LAG_SESSIONS renamed
                                  RANK_LAG_SESSIONS (both rules)
data/tick/statistics_constants.py table names, columns, key, chunk and
                                  columnstore values; REFERENCED_STAT_TYPES,
                                  REQUIRED_STAT_TYPES;
                                  STATISTICS_PUBLICATION_SESSIONS (new)
data/tick/schema_families.py      SchemaFamily.STATISTICS; FAMILY_LEDGER entry
config/databento_plans.py         STANDARD[STATISTICS] = None (all history)
data/tick/statistics_projector.py StatisticsProjector (new)
data/tick/ingest_checks.py        IngestCheck.STAT_REFERENCE and its reason
data/tick/projectors.py           INGEST_PROJECTORS[STATISTICS]
data/tick/statistics_reads.py     resolved values; content and cross-tier
                                  reads (new)
data/tick/statistics_status.py    the family's session verdict and findings (new)
data/tick/tick_coverage.py        coverage --schema statistics
data/tick/roll_inputs.py          open-interest read; volume read per family
data/tick/roll_types.py           SessionOpenInterest; basis family on answers
data/tick/roll_resolve.py         _open_interest; volume basis per session
data/tick/active_contract.py      reads the inputs the rule needs
market/schema/migrations/tick.py  tick_010_statistics
scripts/record_229_roll_fixture.py  also records open interest and n.0/n.1
scripts/drill_228_*.py, scripts/provision_tick_roles.sql   D12
docs/api/reference.md, agents.md, openapi.json             D13
```

### Data Flow

**Acquisition.** A `statistics` history want plans exactly as a bar want does (302 D5): session days inside the want's range, covered days dropped, month runs, definitions ordered first within a day.

**Ingest.** `select_units` selects verified `statistics` units because the schema is a key of `INGEST_PROJECTORS`. Each waits for its same-day definition unit. Per decoded batch, `StatisticsProjector.load`:

1. checks that every `ts_recv` lies in the unit's UTC day (`unit_day`, 302 D4, applied to the index timestamp);
2. resolves every `instrument_id` through the product's definitions by id alone (`resolution`): the provider publishes statistics before a definition activates and a final settlement after it expires, so the activation and expiration bounds that bars and trades use do not apply (measured, task 8);
3. assigns a session to every record whose `ts_ref` is defined (D4), failing `stat_reference` on any it cannot place;
4. derives `record_ordinal` for repeated keys;
5. adds the referenced records to the ledger accumulator, keyed `(instrument_id, session_date, stat_type)`;
6. `COPY`s the batch into `tick_statistics`.

The worker checks provider = decoded = stored over all records, and `finish` writes `tick_statistics_ledger`.

**Reads.** Every reader of a statistic value (status content, coverage, the roll rule, the recorder) goes through `statistics_reads.resolved(...)`: one value per `(instrument_id, stat_type, session_date)` over current units, chosen by D5.

**Roll.** `active_contract` reads definitions, calendar sessions and, per rule: nothing more for `c`; per-session volume from the family chosen by D10 for `v`; resolved open interest and the statistics verdicts for `n`. Resolution stays pure.

### State Management

- **New tables (tick database):** `tick_statistics` (every delivered record) and `tick_statistics_ledger` (per unit, instrument, session and stat type: record count and first and last receive time). Both are written once per unit inside its ingest transaction.
- **Changed table:** `tick_request.schema` may be `statistics`.
- **Configuration:** `HISTORY_SCHEMAS` gains `statistics`; the plan table gains one row. Production's universe is unchanged.

## Technical Decisions

### D1 — `statistics` is its own family
Status: decided

`TickSchema.STATISTICS = "statistics"`, `SchemaFamily.STATISTICS = (STATISTICS,)`, `ARCHIVED_SCHEMAS`, `HISTORY_SCHEMAS` and `ESTIMATE_SCHEMAS` gain it, and `PLAN_INCLUDED_HISTORY[STANDARD][STATISTICS] = None` (the pricing page read on 2026-10-28 lists `statistics` for all history). It is not a tick tier and not a companion. Its estimate row is judged on its own marginal cost, as `ohlcv-1m`'s is (302 D8).

### D2 — Storage: `tick_statistics` and its ledger
Status: decided

`tick_statistics` is an integer-nanosecond hypertable on `ts_recv`, the record's index time:

| Column | Type | Notes |
|---|---|---|
| `ts_recv` | `BIGINT NOT NULL` | epoch ns; the unit's UTC day |
| `ts_event` | `BIGINT NOT NULL` | as delivered |
| `ts_ref` | `BIGINT` | NULL when `UNDEF_TIMESTAMP` |
| `instrument_id` | `BIGINT NOT NULL` | resolved through `tick_definition` |
| `publisher_id` | `INTEGER NOT NULL` | |
| `stat_type` | `SMALLINT NOT NULL` | DBN `StatType` value |
| `update_action` | `SMALLINT NOT NULL` | `NEW` or `DELETE` |
| `stat_flags` | `SMALLINT NOT NULL` | as delivered, not interpreted |
| `price` | `BIGINT` | scale 10⁹; NULL when `UNDEF_PRICE` |
| `quantity` | `BIGINT` | NULL when `UNDEF_STAT_QUANTITY` |
| `sequence` | `BIGINT NOT NULL` | |
| `channel_id` | `INTEGER NOT NULL` | |
| `ts_in_delta` | `INTEGER NOT NULL` | |
| `record_ordinal` | `SMALLINT NOT NULL` | count of earlier records in the file with the same key prefix |
| `calendar_id` | `TEXT` | set with `session_date` |
| `session_date` | `DATE` | from `ts_ref` (D4); NULL when `ts_ref` is NULL or before `CME_DATASET_START` |
| `unit_id` | `BIGINT NOT NULL` | no foreign key, as `tick_trade` |

- **Undefined values are NULL, never the DBN sentinels.** A sentinel stored as a number would read as a price or a quantity.
- **Key:** `PRIMARY KEY (instrument_id, stat_type, ts_recv, sequence, record_ordinal)`. The ordinal follows `tick_trade`'s `sequence_ordinal` (222 TD1), so a record repeated within a file is kept, not lost or failed. A key held by two current units fails `overlap`.
- **CHECKs:** `calendar_id` and `session_date` are both NULL or both set; `session_date` set implies `ts_ref` set.
- **Chunks:** 7 days, by the journal 20260719 rule, as `tick_trade` and `tick_ohlcv_1m`. Columnstore segment by `instrument_id`, order by `ts_recv`, compress after 14 days through `tick_now_ns`. The values live in `statistics_constants.py`.
- **Size:** 19.4 M records for ES and GC over the full range (architecture cost table). No capacity step.

`tick_statistics_ledger` has `unit_id`, `instrument_id`, `calendar_id`, `session_date`, `stat_type`, `record_count`, `first_event_ns`, `last_event_ns` (receive times; named as the other ledgers' so `ingest_plan`'s superseded-unit read works through `FAMILY_LEDGER` unchanged). Key `(unit_id, instrument_id, session_date, stat_type)`, foreign key to `tick_archive_unit`. It holds referenced records only: a record with no session has nothing to ledger against, and the unit-level counts check already covers every record. `FAMILY_LEDGER[STATISTICS] = "tick_statistics_ledger"`.

- *Rejected: a plain table.* Size alone would allow it, but the hypertable gives the same compression and retention mechanics as the other tick tables at no extra code.
- *Rejected: keeping only the referenced stat types.* File-first projection stores what was delivered; the counts check needs every record stored.

### D3 — A statistics projector
Status: decided

`StatisticsProjector` implements `IUnitProjector` for `tick_statistics`, in the shape of `Ohlcv1mProjector`: one `COPY` per unit, `stored_count` and `delete_unit` bounded by the unit's first and last receive times. Reused checks: `unit_day` on `ts_recv`, `resolution`, `overlap`, `shape`, `decode`, `counts`. `session_boundary` does not apply: no statistics record is placed by its own time.

`record_ordinal` uses `OrdinalCarry` with `stat_type` folded into the key, so the carry across batches is the tick path's, not a second implementation.

### D4 — Session assignment from `ts_ref`
Status: decided

`REFERENCED_STAT_TYPES = {SETTLEMENT_PRICE, CLEARED_VOLUME, OPEN_INTEREST}`, the three the record definition gives a trading date. For a record with `ts_ref` defined:

- `session_date` is the UTC date of `ts_ref`, and `calendar_id` the product's calendar. Sessions are dated by their close day (`Session`), which is CME's trading date.
- The date must be a session of the calendar, and no later than the unit's date. Otherwise the unit fails `stat_reference`, naming the instrument, stat type, `ts_ref` and the reason (`not a session`, `after the unit`).
- A date before `CME_DATASET_START` gets no session and is stored with `session_date` NULL. The unit result counts them (`N records reference dates before 2010-06-06`). The first units from 2010 carry statistics for sessions the dataset does not have; failing them would block the start of history for a fact we cannot change.

A record of a referenced type with `ts_ref` undefined fails `stat_reference`. A record of any other type is stored with no session, and the projector's only branch on stat type is membership of `REFERENCED_STAT_TYPES`: those are intraday statistics (opening price, session high, indicative opening price), some sent in pre-open outside any session, and nothing in this initiative reads them.

Which UTC instant `ts_ref` carries within the trading date is not documented. Taking the UTC date is correct for any instant from 00:00 to 23:59 UTC of that date. The purchase confirms it (D11): the observed `ts_ref` values are recorded, and the cross-tier check would show every session shifted by one if the date were wrong.

- *Rejected: the session containing `ts_event` or `ts_recv`.* Settlement, cleared volume and open interest arrive in the next session.

### D5 — Revisions: one resolved value per contract, statistic and session
Status: decided

`statistics_reads.resolved(conn, shape, stat_types, first, last)` returns one row per `(instrument_id, stat_type, session_date)` over current units of the shape (`SHAPE_WHERE`): the record with the greatest `(ts_recv, sequence, record_ordinal)`. If that record is a `DELETE`, the key has no value. The SQL is one `DISTINCT ON` written in code; every value is bound. It returns the winning record's `unit_id`, so a roll answer names the units its basis came from.

Resolution is at read time, not in a stored table: corrections arrive in later units, and a stored winner would need rewriting on every ingest. Its cost at scale is not known from this slice: D11 times it over the four-month window only, and the table reaches 19.4 M rows over the full range. 307, which first holds the full range, times the read over a `TICK_ROLL_MAX_SESSIONS` schedule. If it exceeds `TICK_ROLL_STATEMENT_TIMEOUT_SECONDS`, the request fails as every roll-path database error does (229 TD9): the CLI exits with its database error, naming the product and range. It never returns a partial or empty schedule. A stored resolved table is reconsidered then, on that measurement.

Settlement's final and preliminary flags are not interpreted. The latest settlement record wins whatever its flag, and no reader in this slice depends on finality. The bit layout is not in the SDK source; 306 decides whether settlement is served and reads the flags then. The purchase records the flag values observed.

- *Rejected: a resolved materialised table or continuous aggregate.* Derived state would need its own invalidation, and an aggregate never answers "what do I have" (journal 20260725).

### D6 — `statistics` history wants
Status: decided

`HISTORY_SCHEMAS` gains `statistics`. Validation follows the bar want's (302 D5): one want per schema, start no earlier than `CME_DATASET_START`, an explicit tier, and a range inside the entry's `definition` want. Statistics need definitions for contract identity exactly as bars do. The refusal message names the entry and field.

Production keeps `history=()`. 304 sets ES and GC ranges.

### D7 — The family's verdict: load, then content
Status: decided

**Load.** Session D's statistics arrive from D (preliminary settlement) through the next session. `STATISTICS_PUBLICATION_SESSIONS = 1` states it: D's window is the UTC days from D through the close day of the session one after D, read from the calendar. The session's status is 225's `classify_session` over the statistics units of those days, the same statuses and precedence as every family. A day with a `missing` condition and no unit is `provider_hole`, as elsewhere. The constant cites 220's pinned publication timing (`RANK_LAG_SESSIONS`'s note), and the purchase measures it (D11).

**Content.** In a load-complete session, every outright listed through the session (definition window `activation_ns ≤ open`, `expiration_ns ≥ close`) must have ledger records for each of `REQUIRED_STAT_TYPES` (settlement, cleared volume, open interest). Cleared volume and open interest are owed by a contract only once it has reported them in the range judged (deferred months carry a settlement and nothing else; measured, task 8), so the settlement is the one type owed unconditionally. Each gap is a finding naming the contract and the missing types. A contract expiring inside the session is not required; its records are kept if present. Spreads are never required.

Status reads only the manifest, `tick_statistics_ledger` and `tick_definition`, never the raw table (220). It therefore checks presence of records, not resolved values; a key whose last record is a `DELETE` shows in coverage, which reads resolved values.

`mt data tick status` prints a `statistics` section for an entry with a statistics want: session counts by status, the content findings, and `caught up`. `--json`'s `families` list gains it.

### D8 — Coverage and the cross-tier volume check
Status: decided

`mt data tick coverage --schema statistics` reports per session: status, condition, unit ids, ledger and raw record counts per required stat type (a mismatch exits 3, as for every family), and the volume check.

**Volume check.** For each listed outright in a session that is load-complete in both families, it compares the resolved cleared volume C with the traded volume V from each volume family complete in that session: `tick_bar_ledger` for `BAR_1M`, `tick_ingest_ledger` for the tick tier. Where both are complete, both are checked and reported separately; nothing is chosen between them. Verdicts per contract and family:

- `agree`: V = C;
- `under`: V < C, shown with V / C;
- `over`: V > C. A finding: more trades than CME cleared means a double load or a wrong session;
- `no_trades`: V = 0 and C > 0 in a session where nothing at all traded. A finding: the ledger proves the unit loaded, so the session's traded volume is missing. A contract with V = 0 and C > 0 in a session that did trade is `under`: a deferred month's cleared volume is mostly spread-leg volume, which no outright trade carries (measured, task 8).

`over` and `no_trades` set the exit code to 3. `under` does not. CME cleared volume includes trades made away from Globex (block trades, exchanges for physical and for related positions), which neither ticks nor provider bars carry, so V below C is normal. The architecture's "agrees" is refined to this. The purchase measures V / C over the held window and records its distribution.

**One implementation, parameterised by family.** `statistics_reads.volume_check(conn, shape, family, first, last) -> tuple[VolumeVerdict, ...]` is a connection-bound query: one resolved read of cleared volume (D5) and one ledger read of the given family, both bounded by `[first, last]`. The verdict is assigned by a pure `classify_volume(cleared, traded)`. Coverage calls it once per volume family complete in the range. In this slice the tick-tier run over the purchased window is the evidence; the bar run is exercised only on the derived 2024-12-03 day, since no provider bars are held until 304. Its cost is a raw-table read proportional to the range; the evaluation note records it per session over the window. 305 calls the same function for `health` and the other surfaces and chooses the range those surfaces read.

### D9 — The open-interest rule, `n`
Status: decided

`RollMethod.OPEN_INTEREST = "n"`, Databento's letter, so `ES.n.0` and our `n.0` name the same rule. The grammar and parser do not change: `n.0`, `n.1` parse, `n.0+2s` is refused (offset is calendar only).

Resolution for session S mirrors the volume rule (229 TD3), with open interest in place of volume:

- L is the session `RANK_LAG_SESSIONS` before S. Both rules rest on the same fact (CME publishes D's statistics during D + 1, and Databento's continuous rules rank on them), so they share one constant: `VOLUME_RANK_LAG_SESSIONS` is renamed `RANK_LAG_SESSIONS` and stays 2. The equivalence test pins it for `v` and `n` together. Only a test failure that one lag cannot satisfy splits it.
- Candidates are eligible at S and listed in L (229's predicate). They are ranked by resolved open interest at L, ties to the earlier expiration.
- S is `Unresolved` when L's statistics status is not `complete` (reason names L and its status), or when no candidate has resolved open interest at L (reason names L). A candidate with no record at L is not ranked and never read as zero: CME reports open interest only for contracts that hold some (measured, task 8). A content gap is a provider fact, not a broken invariant, so it is an answer, not a `RollInputError`.
- The answer's `basis_unit_ids` are the units of the open-interest records read.
- The forecast follows the volume rule's: exact for the lag sessions, then the bound at the active contract's expiration.

`RollInputs` gains `open_interest: tuple[SessionOpenInterest, ...]` and `statistics_statuses`, read only when the rule is `n`. `DEFAULT_ROLL_RULE` and `STATUS_ROLL_RULES` do not change.

- *Rejected: open interest of S itself.* It is published after S, so the rule would look ahead.

### D10 — The volume rule's basis per session
Status: decided

`session_volumes` takes a family and reads that family's ledger through `FAMILY_LEDGER`. For each session L the volume rule needs, the basis is:

1. `TICK_TIER`, when the tick tier's session is `complete`;
2. else `BAR_1M`, when the bar session is `complete`;
3. else the session is unresolved, naming both statuses.

One session never mixes families. Ticks come first so every answer the product gives today is unchanged, and the pinned equivalence test still holds. 302 writes bar ledger rows for every listed contract, zero-bar rows included (the same `ingest_plan._definitions` predicate as ticks), so 229's I13 invariant holds for bar volume too. `ActiveContract` gains `basis_family` (`tick_tier`, `bar_1m`, `statistics` for `n`, NULL for `c`). `RollSchedule.segments()` also breaks a run where the basis family changes, so one segment never spans two bases. The API's `SegmentRecord`, used by the schedule, ticks and bars responses, gains `basis`, so every roll answer the API serves names the tier it was decided from (the architecture's "never splice tiers silently"). `mt data tick active --json` shows it too.

- *Rejected: bars first.* Uniform over the history, but it would change today's answers the day bars are bought, on a basis no test pins.

### D11 — The real purchase and the evaluation
Status: decided

The rule cannot be evaluated without stored open interest, and none is held. Waiting for 304 would ship an unproven rule and an unconfirmed reading of `ts_ref`. So this slice buys the smallest window that evaluates everything: ES and GC `statistics`, plus the missing October definitions, for 2024-08-30 to 2025-01-01. This matches the span of the held `tbbo` units and of the 229/232 roll fixtures. It covers ES's September and December rolls and GC's rolls through December, including the 2024-11-21 against 2024-11-29 case. Free metadata, read 2026-11-03: statistics ES $0.0127 and GC $0.0242; October definitions ES $0.0014 and GC $0.0129; **$0.051 together**, inside the existing $0.50 ceiling, so no ceiling changes.

**Plan state.** The account has no subscription, and the throwaway run leaves `MT_TICK_DATABENTO_PLAN` unset, as production does. Every request of the run is therefore recorded with its plan-inclusion marker false, the guard counts its full list price, and Databento bills it by usage. D1's plan row takes effect only once a plan is configured; it does not apply here. The manifest records each request's estimate, and the evaluation note records the charge Databento reports for the jobs beside it. Definitions were bought through the same path in 226's proof (2026-09-30), so this does not pre-empt 304's purpose, which is the first purchase large enough to reconcile against an invoice. The architecture's "only the purchase test spends money" is amended to name this exception.

The purchase runs the branch's pass against a throwaway tick database and a scratch archive directory, with a wrapper universe giving ES and GC the two history wants (as 302's walkthrough does). Production's manifest and universe are untouched. The spend guard counts the job either way, through the provider's job list. The job ids go in the evaluation note; 304 adopts those jobs (`mt data tick adopt`) instead of re-buying the window while they are within retention.

The evaluation (`user/notes/303-statistics-evaluation.md`) records:

- the observed `ts_ref` instants and `stat_flags` values per required stat type, and how many distinct reference dates were checked against the calendar (the first pre-2024 ingest in 304 and 307 meets dates this window does not);
- the latest receive time of each required statistic relative to its session's close, against `STATISTICS_PUBLICATION_SESSIONS`;
- V / C per contract and session for ES and GC (tick volume against cleared volume), and any `over` or `no_trades`;
- per product, the roll dates of `c.0`, `v.0`, our `n.0` and Databento's `.n.0` and `.v.0`;
- the resolved-read timing over the window (window scale only; see D5).

**Recorded fixtures.** `record_229_roll_fixture.py` also writes `{product}_open_interest.json` (resolved outright open interest per session, with unit ids, and the statistics verdicts) and adds `.n.0` and `.n.1` to `{product}_databento.json` (free `symbology.resolve`). `test_roll_provider_equivalence.py` adds `n.0` and `n.1` for ES and GC. A session where our `n.0` and Databento's differ is either fixed by the lag (one lag for both products, never per roll) or listed in `KNOWN_DIVERGENCE` with the evidence, and reported in the slice's completion summary.

**File fixtures.** The purchased ES statistics files for 2024-12-03 and 2024-12-04 are copied whole into `test/fixtures/databento/real/` (session 2024-12-03's window is those two days). `SOURCES.md` records the job. With the held definition and `tbbo` fixtures and the derived bar day, they exercise ingest, session assignment, revisions as delivered, and the cross-tier check in integration tests. If a file is too large for the repository (above 1 MB), it is cut by `cut_tick_fixtures.py` to the outrights and both days' referenced records.

### D12 — Backup and roles
Status: decided

As 302 D9: the files are under `MT_TICK_ARCHIVE_DIR`; both tables are in the tick database; `tick_statistics_ledger` joins `drill_228_bookkeeping.BOOKKEEPING_TABLES` and `drill_228_fingerprint` gains a `tick_statistics` fingerprint; both tables join the enumerated write surface in `provision_tick_roles.sql` and `test_tick_role_privileges.py`. The default privileges already cover tables created by the migrate role, so no production re-run is needed.

### D13 — The API takes `n`
Status: decided

Every route that takes a `rule` parses it with `RollRule.parse`, so `n.0` works with no route change. `SegmentRecord` gains `basis` (D10), an additive response field. `RULE_DESCRIPTION` lists `n.0`. `docs/api/reference.md` and `agents.md` describe `n` beside `v` and `c` (ranked on an earlier session's open interest, never looking ahead) and the segment's `basis`, and `openapi.json` is regenerated. Serving open interest and settlement themselves is 306's question.

## Implementation Details

### Database / Storage Schema

`tick_010_statistics` (tick track, after `tick_009_ohlcv_1m`; confirm the last id at implementation):

- Drop and re-add `tick_request_schema_check`, rendered from `ARCHIVED_SCHEMAS`.
- `CREATE TABLE IF NOT EXISTS tick_statistics (...)` per D2; `create_hypertable(..., chunk_time_interval => <7 days in ns>, create_default_indexes => FALSE, if_not_exists => TRUE)`; `set_integer_now_func('tick_statistics', 'tick_now_ns')`; the columnstore `ALTER` on the empty table; `add_columnstore_policy`. An index on `(session_date, stat_type)` serves the resolved read.
- `CREATE TABLE IF NOT EXISTS tick_statistics_ledger (...)` per D2.

Applied in production by `mt data migrate apply --track tick` in the release that ships the slice.

### API Contracts

- `GET /api/v1/futures/...?rule=n.0` on every route taking `rule`: answers by open interest; `422` with the parser's message for `n.0+2s`.
- Segments in the schedule, ticks and bars responses carry `basis`: `tick_tier`, `bar_1m`, `statistics` or null (calendar).
- `mt data tick active --rule n.0 [--start --end] [--json]`: the schedule or the latest line; JSON answers carry `basis_family`.

## Integration Points

### Provides to Other Slices

- **304:** a proven `statistics` path, the window's job ids to adopt, and `statistics` history wants to set for ES and GC.
- **305:** the statistics session verdict, content findings and the cross-tier check, for `health`, `accounting` and `overview`.
- **306:** resolved settlement and open interest per contract and session, for its decision whether to serve them; `n` in the rule vocabulary.
- **307:** the same path over the full range, and `v.0` answering on bar volume where no ticks exist; it times the resolved read over a `TICK_ROLL_MAX_SESSIONS` schedule at full size (D5).

### Consumes from Other Slices

- **302:** `HistoryWant`, `FAMILY_LEDGER`, per-family status and coverage, `tick_bar_ledger` rows for every listed contract. If a bar session lacks a listed contract's row, the volume rule raises I13's error naming it, never reads 0.
- **301:** sessions back to 2010. A reference date that is not a session fails its unit naming it.

## Success Criteria

### Functional Requirements

1. A `statistics` unit is ingested once its same-day definition unit is; before that it is skipped as `awaiting definitions`.
2. Every record is stored. Settlement, cleared volume and open interest records get the session of their `ts_ref` date; other types get none. Provider = decoded = stored.
3. A referenced record with undefined `ts_ref`, or a `ts_ref` date that is not a session or is after the unit's date, fails the unit with `stat_reference` naming the instrument, stat type and `ts_ref`. A reference before `CME_DATASET_START` is stored with no session and counted in the result.
4. A record outside the unit's UTC day by `ts_recv` fails `unit_day`; an unresolvable instrument fails `resolution`; a key held by two current units fails `overlap`.
5. The resolved read returns the latest record by `(ts_recv, sequence, record_ordinal)` per contract, statistic and session over current units, and no value when that record is a `DELETE`, including when the correction is in a later unit.
6. `mt data tick status` shows a `statistics` section: a session is judged over its publication window's units, and a listed outright missing a required statistic is a finding naming it.
7. `mt data tick coverage --schema statistics` reports ledger and raw counts per required type and the volume check per contract; a count mismatch, `over` or `no_trades` exits 3, `under` does not.
8. `n.0` and `n.1` resolve on stored open interest with the lag; a session whose lag session is not statistics-complete, or whose candidate lacks open interest, is unresolved with a reason naming it. `n.0+2s` is refused.
9. The volume rule reads tick volume where the tick session is complete and bar volume otherwise; every answer it gives over the recorded fixtures is unchanged. Every API segment names its `basis`, and no segment spans two bases.
10. Over the purchased window, our `n.0` names the contract Databento's `.n.0` names on every session, apart from divergences listed with evidence.
11. A `statistics` want outside its entry's definition want, before `CME_DATASET_START`, repeated, or on an entry with `tier=None` fails at import naming the field.
12. With the production universe, `mt data tick pass --estimate-only` plans exactly what it planned before the slice. `mt data tick estimate` prints a `statistics` row, judged alone.

### Technical Requirements

- Unit tests: families total and disjoint; plan table total; decoding the v1 and v3 statistics samples (v1's narrower quantity); session assignment and each `stat_reference` reason; undefined values to NULL; revision resolution, including ties and a later-unit `DELETE`; the publication window from the calendar, including a Friday session and a holiday; content findings; each volume-check verdict; `n` parsing; the open-interest resolver on synthetic inputs (lag, ties, unlisted contract, missing value, incomplete lag session); the per-session volume basis; history-want validation.
- Integration tests: `tick_010` on an empty and a populated tick database; statistics ingest end to end on the real 2024-12-03/04 files; each failure in criteria 3 and 4; status and coverage for the family, including exit 3; the cross-tier check against the derived bar day and the `tbbo` fixture; the drill pin and role privilege tests.
- The provider equivalence test covers `n.0` and `n.1` for ES and GC on the recorded fixtures.
- `ruff check`, `ruff format` on touched files only, `mypy` clean. Files stay within about 300 lines: statistics constants go in `statistics_constants.py`, and `constants.py` (442 lines) takes only the schema, the roll method and the lag.
- `SOURCES.md` records the two DBN samples and the purchased files.
- The evaluation note holds every item listed in D11.

### Integration Requirements

- 304 can buy ES and GC `statistics` over the full range by editing `TICK_UNIVERSE`, and can adopt this slice's jobs.
- 305 reads the family's verdicts through status's reads and the volume check through `statistics_reads.volume_check`; it writes no new query of its own.
- The production tick pass, ingest and every existing roll answer behave as before once `tick_010` is applied.

### Verification Walkthrough

Run from the checkout root. Steps 1 and 4 need `MT_TIMESCALE_TEST_URL` exported from `.env`, and only that variable (302's walkthrough explains why). Step 2 spends about $0.05 of real money, usage-billed, with no plan configured. Commands that do not exist yet are marked.

1. **Tests.**
   ```bash
   export MT_TIMESCALE_TEST_URL="$(grep '^MT_TIMESCALE_TEST_URL=' .env | cut -d= -f2- | tr -d '"')"
   env -i HOME=$HOME PATH=$PATH MT_TIMESCALE_TEST_URL="$MT_TIMESCALE_TEST_URL" uv run pytest test/unit/data/tick -q
   env -i HOME=$HOME PATH=$PATH MT_TIMESCALE_TEST_URL="$MT_TIMESCALE_TEST_URL" uv run pytest test/integration/data -q -k "tick or drill_228"
   ```
   Expected: all pass apart from the known `test_health_tick` pair (they need `MT_BACKUP_ROOTS`).

2. **Buy and ingest the window on throwaway databases.** Create a primary and a tick database on the test cluster and point `MT_TIMESCALE_DB_URL`, `MT_TIMESCALE_MAINTENANCE_URL`, `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and `MT_TICK_ARCHIVE_DIR` at them and at a scratch directory that is kept. `CLI` is the 302-style wrapper with ES and GC wanting `definition` and `statistics` over 2024-08-30 to 2025-01-01, and `tier` and range unchanged from production.
   ```bash
   uv run mt data init && uv run mt data init --database tick      # tick through tick_010_statistics
   CLI data tick pass --estimate-only --json    # statistics ≈ $0.0127 (ES), $0.0242 (GC), October definitions ≈ $0.0014, $0.0129; plan-included false; verdict within
   CLI data tick pass --json                    # buys, downloads, verifies; definitions projected
   CLI data tick ingest --json                  # every statistics unit "ingested"
   ```
   Expected: every statistics unit ingests; no `stat_reference` failure. Record the job ids.

3. **Read it.**
   ```bash
   CLI data tick status --product GC
   CLI data tick coverage --product GC --schema statistics --start 2024-11-04 --end 2025-01-01 --json
   CLI data tick active --product GC --rule n.0 --start 2024-11-04 --end 2025-01-01
   CLI data tick active --product GC --rule v.0 --start 2024-11-04 --end 2025-01-01
   ```
   Expected: a `statistics` section with complete sessions and any content findings named; coverage with counts equal, volume verdicts `agree` or `under`, exit 0; `n.0` rolls GC around 2024-11-21 and `v.0` around 2024-11-29, as Databento's continuous symbols do (229 TD6). ES `n.0` and `v.0` roll on the same sessions.

4. **Equivalence and evaluation.** Run `scripts/record_229_roll_fixture.py --product ES` and `--product GC` against the throwaway databases, then:
   ```bash
   env -i HOME=$HOME PATH=$PATH uv run pytest test/unit/data/tick/test_roll_provider_equivalence.py -q
   ```
   Expected: pass, with `n.0` and `n.1` included. Read `user/notes/303-statistics-evaluation.md` for the measurements. Drop the throwaway databases; keep the scratch archive until 304 adopts the jobs.

5. **API.** With `mt serve` on the throwaway databases: `curl -s 'localhost:<port>/api/v1/futures/products/GC/schedule?rule=n.0&start=2024-11-04&end=2025-01-01'` returns segments; `rule=n.0%2B2s` returns `422`.

6. **Production pass unchanged.** On a throwaway tick database holding a copy of production's manifest and availability rows, the base commit's and the branch's `mt data tick pass --estimate-only --json` are identical apart from run id and timings (302's method).

7. **Production, after merge.** Apply `tick_010` with `mt data migrate apply --track tick`, then `sudo mt-run data health`. Expected: tick lines unchanged; no `statistics` section until 304 sets a want.

## Risk Assessment

### Technical Risks

- **Required statistics may be absent for deferred contracts.** If CME sends no open interest or settlement for a listed back month, every session shows a content finding, and `n` is unresolved when a candidate lacks a value.
- **Cleared volume's relation to Globex volume** is stated from CME's definition, not yet measured on our data.

### Mitigation Strategies

- Both are measured on the purchased window before merge. If back months lack records, the required set is narrowed to the contracts CME reports, with the evidence in the evaluation note; the rule is changed in this slice, never patched with zeros.
- The volume check fails only on `over` and `no_trades`, which hold whatever share of volume trades off Globex.

## Implementation Notes

### Development Approach

Relative effort 4/5. Two task files fit the slice: the tier, then the rule.

1. `STATISTICS`, family, plan row, `statistics_constants.py`, `tick_010`; sample decode tests.
2. `StatisticsProjector`, `stat_reference`, registry; ingest integration tests (on the purchased files once step 4 has run; on the samples and seeded definitions before).
3. Resolved read; status and coverage for the family; volume check; history want; estimate row; drill and roles.
4. The purchase and ingest on throwaway databases (walkthrough step 2); file fixtures; evaluation measurements.
5. `n` and its resolver; volume basis per family; recorder and equivalence test; API docs; evaluation note.

### Special Considerations

- The purchase is the only spend. It runs through estimate and guard; if the guard refuses, the reason is recorded and the window is not forced through.
- `ruff format` touched files only, and check `git diff` for swept lines before each commit.
- SQL built from constants keeps 300's rule: fragments are written in code and every value is bound.
- The resolved read's cost over `TICK_ROLL_MAX_SESSIONS` is unmeasured here; 307 measures it at full size (D5). A timeout is an error, never a partial schedule.
