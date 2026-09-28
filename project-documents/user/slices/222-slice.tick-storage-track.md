---
docType: slice-design
slice: tick-storage-track
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221]
interfaces: [223, 224, 225, 226, 227, 228, 229]
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
---

# Slice Design: tick-storage-track

## Overview

Slice 222 creates the tick database's tables on the `tick` migration track
that 923 registered. The track holds only the ledger bootstrap today. It
adds five tables:

- `tick_request`: one row per provider request, holding the facts that exist
  once per batch job (job id, cost, download deadline).
- `tick_archive_unit`: one row per UTC day of a request, holding the
  state machine, the fetch-state vocabulary, the file, and the
  supersession and repurchase links. With `tick_request` this is the
  manifest.
- `tick_definition`: the futures instrument model, with each identifier's
  validity window enforced by the server.
- `tick_trade`: the hypertable for both stored tiers (`trades`, `tbbo`).
- `tick_ingest_ledger`: one row per instrument, session and unit.

It fills the write surface of 923's tick grant artifact and removes slice
105's `TickEventType` leftovers. It writes no market data and adds no pass,
CLI verb or API route.

Measuring the adopted ES files during design found that the architecture's
natural key does not hold. `(instrument, event time, sequence)` repeats for
1.95% of real trades. Many of those rows are byte-identical and are still
distinct fills. The key therefore gains an ordinal (Technical Decision 1).
Four other architecture statements are refined here for reasons found in
the data or the provider's grain. They are listed under "Architecture
statements this design supersedes" and added to the slice plan's Notes.

## Value

- **Architectural enablement.** 223 writes requests, units and definitions.
  224 writes ticks and ledger rows. 225 measures the table. None of them can
  start without these tables, and every later completeness, status and API
  surface reads them.
- **Correctness found before any data is loaded.** Under the architecture's
  key, conflict-ignore would have silently dropped about 2% of real trades.
  That is 196,202 of the 10,049,172 records in the adopted trades job.
  224's count check would then fail every unit, with no explanation in the
  schema. Fixing it now costs one column. Fixing it after 225 would cost a
  rebuild.
- **Server-enforced guarantees instead of reviewer memory.** No duplicate
  tick row can exist. An instrument id's validity windows cannot overlap, so
  resolving an id to a contract is never ambiguous. BBO fields are null
  together or present together. A unit cannot claim a file state without a
  file. Every lifecycle value is rendered from the one enum that defines it.

## Technical Scope

**Included**

1. Five tick-track migrations in `market/schema/migrations/tick.py`
   (Database / Storage Schema below): extensions, manifest, definitions,
   trades hypertable, ingest ledger.
2. New vocabulary in `data/tick/constants.py`: `UnitState`,
   `UNIT_STATES_WITH_FILE`, `ARCHIVED_SCHEMAS`, and
   `TICK_TRADE_CHUNK_INTERVAL`.
3. A column contract module, `data/tick/storage_columns.py`. It maps each
   provider record field to its table column, for `tick_trade` and
   `tick_definition`. It is parity-tested against both the migrated tables
   and the real DBN fixture layouts. 223 and 224 write through it.
4. The write surface of `scripts/provision_tick_roles.sql`: an enumerated
   `GRANT SELECT, INSERT, UPDATE, DELETE` on the five tables, filtered on
   `pg_tables` as 923 prescribed.
5. Removal of `data/base/tick_schema.py` (`TickEventType`) and
   `test/unit/data/base/test_tick_schema.py`.
6. Data-correctness contract: the *archive unit* vocabulary entry is
   corrected, and 222's part is added to the I11 and I12 mapping rows.
7. Slice plan Notes: the architecture statements this design supersedes.
8. Docs: the migrations README's tick row names the tables, a CHANGELOG
   entry is added, and the README's futures tick section gets a storage
   paragraph.

**Excluded**

- **Writing any row outside tests.** Requests, units and definitions belong
  to 223. Ticks and ledger rows belong to 224.
- **Compression, space partitioning, and any index beyond the primary
  key.** Physical grouping is one decision, made by 225 from measurements
  and applied by a tick-track migration (architecture, "Storage"). No
  compression policy exists before then.
- **An `integer_now` function.** A policy or continuous aggregate on an
  integer-time hypertable needs one. The first such object, if any, adds it
  in the same migration (225 or later).
- **Read-side aggregates, `PassKind.TICK`, and `granularity = 'tick'` in
  minute-track enumerations.** None has a reader yet (contract vocabulary
  rules).
- **Applying the track to a production tick database.** None exists.
  Placement is the PM's decision, informed by 225's sizes. This slice is
  proven on the test cluster.
- **Second connection pools** for overview, health, status and the API
  (223 onward).

## Dependencies

### Prerequisites

- **923 Multi-Database Migration and Credential Plumbing (complete).** It
  provides the routed `tick` track, the misroute guard, `mt data init
  --database tick`, the grant artifact with an empty write list, and the
  `migrated_tick_db` and `provisioned_tick_db` fixtures. It also measured
  that `tick_migrate`, as database owner, can install TimescaleDB.
- **220 Databento Adapter and Cost Preflight (complete).** `TickSchema`,
  `STORED_TIERS`, `COMPANION_SCHEMAS`, `SType`, `DeliveryMode`,
  `TickRequest` (UTC `date` range, end exclusive), `BatchJob`, and the DBN
  reader with its real fixtures.
- **221 CME Session Model (complete).** A session is
  `(calendar_id, session_date)`. `FUTURES_PRODUCT_CALENDAR` supplies the
  calendar id the ledger stores.
- No new Python packages. `btree_gist` ships with PostgreSQL contrib and is
  a *trusted* extension (PostgreSQL 13+), so the database owner can create
  it. The privilege test asserts this.

### Interfaces Required

- `FetchStatus` (`data/quality/fetch_status.py`), reused exactly. Its CHECK
  renders the way minute's `_fetch_status_check_sql()` does.
- The migration list-of-dicts form, and the Kalshi track's `_in_list`
  rendering pattern. The tick track gets its own copy, because the
  architecture forbids importing from `data/kalshi`.
- `test/tick_support/database.py` (`ProvisionedTickDb`,
  `apply_tick_track_as`).

## Architecture

### Component Structure

```
data/tick/constants.py          UnitState, UNIT_STATES_WITH_FILE,
                                ARCHIVED_SCHEMAS, TICK_TRADE_CHUNK_INTERVAL
            │ rendered into CHECKs / interval
            ▼
market/schema/migrations/tick.py   001 bootstrap (923)
                                   tick_001 … tick_005 (this slice)
            │ applied by mt data migrate apply --track tick / init --database tick
            ▼
tick database:  tick_request ─1:N─ tick_archive_unit ─1:N─ tick_ingest_ledger
                                        │ unit_id (no FK, see TD 6)
                                        ├──────────── tick_trade (hypertable)
                                        └──────────── tick_definition.unit_id

data/tick/storage_columns.py    DBN field → column maps for tick_trade and
                                tick_definition (223/224 write through them)
scripts/provision_tick_roles.sql  write surface = the five tables
```

### Data Flow

Nothing flows yet. The shapes below are the contract 223 and 224 build to:

1. **223 acquire.** Insert a `tick_request` row with its estimate, and one
   `tick_archive_unit` per UTC day it wants at `requested`, in one
   transaction immediately before the submit. The submit sets
   `provider_job_id` and `committed_at`, and the units move to `submitted`.
   Delivery, download and verify advance the units and fill their file
   columns. Adoption writes both at `downloaded` with `is_adopted`. Each
   definition-schema unit's records are upserted into `tick_definition`.
2. **224 ingest.** For a `verified` tier unit, decode it, resolve each
   `(instrument_id, ts_event)` against `tick_definition`, assign sessions,
   and compute each record's `sequence_ordinal`. Load `tick_trade` with
   `unit_id`, then write the unit's ledger rows and its `ingested`
   transition in one transaction on the loading connection.
3. **Readers (224 status, 228, 229).** Completeness comes from units,
   ledger and raw counts. Resolution comes from definitions. Session totals
   are summed over units whose `superseded_by_unit_id IS NULL`.

### State Management

The manifest is the tick tier's only acquisition state (contract rule: tick
completeness never reads `acquisition_state`, `data_gaps` or
`data_status`). A unit's state is two orthogonal columns. Technical
Decision 4 explains why.

- `state` is the furthest step reached, in `UnitState` order: `requested →
  submitted → delivered → downloaded → verified → ingested`. It never moves
  backward, except when a manual reset of a failed verify re-downloads,
  which is 223's rule to state.
- `fetch_status` (`FetchStatus`) is the failure lifecycle of the *next*
  step:
  - `UNKNOWN`: no failure outstanding (minute's meaning: open, never
    failed).
  - `FAILED_RETRYABLE`: the next step failed. `failure_reason` names why,
    and it will be retried.
  - `RETRY_EXHAUSTED`: terminal until a manual reset, which sets `UNKNOWN`
    as minute's gap reset does.
  - `PROVIDER_HOLE`: terminal. It reopens when the day's condition
    metadata changes.
  - `attempt_count` and `last_attempt_at` mirror `data_gaps`.

The architecture's *failed* is `fetch_status <> 'UNKNOWN'`. Because the
resume point is kept, a failed ingest is retried from `verified`, not
re-downloaded.

## Technical Decisions

### Technical Decision 1: the tick key is the provider's triple plus a delivery-order ordinal

Measured on the adopted files with the 220 reader's SDK:

| Data | Records | Rows sharing `(instrument_id, ts_event, sequence)` with an earlier row | Byte-identical to an earlier row | Largest group |
|---|---|---|---|---|
| `trades` job, 26 day files | 10,049,172 | 196,202 (1.95%) | measured per day: 4,122 of 511,965 on 2024-09-03 | 26 |
| `tbbo` 2024-12-10 | 318,306 | 5,460 | 3,285 | 14 |

One CME match event has one sequence number and one event time, and it
emits one trade record per fill. Two fills of the same size at the same
price are identical in every field. **No key made of provider fields alone
can tell them apart**, so conflict-ignore on the architecture's key would
silently drop real trades.

The primary key is `(instrument_id, ts_event, sequence, sequence_ordinal)`.
`sequence_ordinal` is the number of earlier records in the same archive
file with the same `(instrument_id, ts_event, sequence)`, in file order,
starting at 0.

- **Deterministic.** A unit's file is immutable and SHA-256 verified, so
  re-ingesting it produces the same ordinals, and the architecture's
  unit-idempotence through conflict-ignore holds unchanged.
- **Cheap.** Every colliding group was contiguous in file order (2,760,955
  records checked, zero exceptions). 224 can count within a run of equal
  keys, but its definition must not depend on contiguity: it keys a counter
  by the triple, and a test covers a non-adjacent repeat.
- **Two units never meet through the key.** Supersession deletes the old
  unit's rows before loading the new ones (architecture), so ordinals never
  need to agree across deliveries. If two overlapping units are both loaded
  by mistake, the second unit's rows conflict, and 224's per-unit count
  check fails loudly. The server turns a would-be silent merge into a
  failure, which is I12 enforced rather than reviewed.

*Rejected: no unique key, idempotence by delete-unit-then-reload.* It
allows a plain `COPY` with no uniqueness check. But duplicate rows would
then be possible, and nothing in the server would catch two overlapping
current units. A unique key still lets 224 choose delete-then-`COPY`,
because a conflicting `COPY` errors loudly. The write mechanics stay 224's
decision.

### Technical Decision 2: times are `BIGINT` nanoseconds, and the hypertable partitions on integer time

PostgreSQL `timestamptz` has microsecond resolution. CME event times are
nanoseconds, many trades share a microsecond, and the 940 analysis requires
event and receive times at nanosecond precision. `ts_event` and `ts_recv`
are `BIGINT` nanoseconds since the Unix epoch, exactly as delivered. That is
also NautilusTrader's native form. `tick_trade` is a hypertable on
`ts_event` with an integer `chunk_time_interval`.

Consequences, stated so later slices do not rediscover them:

- Readers convert for display. 228 and 229 own that conversion. Exact
  comparison stays in integers.
- `time_bucket` takes integer widths.
- Any policy or continuous aggregate needs `set_integer_now_func` first.
- The ledger's first and last event times use the same representation, so
  a later per-unit time range (realtime) compares exactly.

Manifest timestamps (`requested_at`, `committed_at`, deadlines) are
operational instants, not provider event times, so they are `timestamptz`.
The request range is `DATE`, matching `TickRequest`'s UTC days with an
exclusive end.

### Technical Decision 3: provider types map by domain, and sentinels are preserved in ticks and nulled in the model

**Type rule.** Each provider type maps to the smallest signed PostgreSQL
type that holds its whole domain:

| Provider type | PostgreSQL type |
|---|---|
| `uint8` | `SMALLINT` |
| `uint16` | `INTEGER` |
| `uint32` | `BIGINT` |
| `int32` | `INTEGER` |
| `int64` | `BIGINT` |
| `uint64` timestamps | `BIGINT` |
| one-byte `char` | `TEXT` |

No field needs its own argument, and no in-range value can fail to load.
Instrument ids reach 42,035,063 in the adopted files, and `uint32` sizes
use `4294967295` as the undefined-size value, which `INTEGER` cannot hold.
Fixed-point prices (`int64`, 1e-9 units) stay exact as `BIGINT`.
Compression, decided by 225, absorbs the wider types.

**Sentinel rule.**

- **`tick_trade` is a raw-record table.** It stores provider values as
  delivered, including `UNDEF_PRICE` (`INT64_MAX`) in a BBO price. That
  keeps "`NULL` BBO means a trades-tier row" true by construction, instead
  of overloading `NULL` with "empty book side". Readers (228, 229) translate
  the sentinel. Neither sentinel appeared in two days of ES `tbbo`.
- **`tick_definition` is a model table.** A provider "undefined" value
  becomes SQL `NULL`. `UNDEF_TIMESTAMP` is 2^64−1, which `BIGINT` cannot
  hold at all, so mapping it is required, not a choice.

The DBN framing fields `length` and `rtype` are not stored. `rtype` encodes
the schema, which is the tier, and the architecture keeps tier off the row.
Every other field of the record body is stored.

### Technical Decision 4: the manifest is two tables, request and unit, and failure is a column rather than a state

**Two tables.** The provider bills, retains and identifies at the job, but
delivers data in day files:

- A 30-day adopted job is one `job_id` with one cost and one expiry, and
  26 files.
- `BatchJob` (220) carries `cost_usd`, `ts_expiration` and `record_count`
  per job.

Putting job facts on each unit would duplicate or apportion them.
Apportioning makes the 30-day cap and accounting inexact. So:

- `tick_request` holds each job-grain fact once.
- `tick_archive_unit` holds the architecture's per-unit lifecycle
  unchanged. The whole state machine stays on the unit, so status never
  derives a state from two tables. At submit, the units of one request move
  together in one `UPDATE`.

**The unit is one UTC day of a request, not one file.** The provider writes
no file for a day with no data: the trades job has no Saturday files. A
provider hole on a trading day is therefore a day *without* a file, and
only a row created when the day is requested can carry `PROVIDER_HOLE`.
223 requests only days that some session touches, so a Saturday is never a
unit. The file columns are required from `downloaded` onward (a CHECK
rendered from `UNIT_STATES_WITH_FILE`).

**Failure as a column.** The architecture lists *failed* as a state
"reachable from any state". A single failed state loses the step to resume
from, and it duplicates `FetchStatus`, which the PM asked tick to reuse. See
State Management.

*Rejected: one request per day, so request equals unit.* It would force 223
to submit one job per day, 365 jobs for the plan's year, and adopted
multi-day jobs would still break it.

### Technical Decision 5: definitions carry a server-enforced validity window

Databento instrument ids are dataset-scoped and reused, and raw symbols
recycle every decade (`ESZ4` is 2014 and 2024). Each definition row has
`activation_ns` and `expiration_ns`, both `NOT NULL`, and:

```sql
EXCLUDE USING gist (
    instrument_id WITH =,
    int8range(activation_ns, expiration_ns, '[]') WITH &&
)
```

This makes "which contract did id N mean at time t" have at most one
answer. It requires `btree_gist`, created in `tick_001`.

- **Primary key:** `(instrument_id, activation_ns)`, unique by the
  exclusion.
- **Columns:** the architecture's model (contract symbol, id, expiration,
  tick size, multiplier), plus what resolution and NautilusTrader-style ids
  need:
  - `raw_symbol`, `asset` (the product, such as `ES`), `exchange`,
    `instrument_class`, `security_type`, `cfi`, `currency`;
  - `min_price_increment`, `display_factor`, `unit_of_measure`,
    `unit_of_measure_qty`;
  - `ts_recv_ns` of the first record seen, and the `unit_id` that supplied
    it.
- **Nautilus-style id.** `raw_symbol` plus the dataset's venue gives a
  NautilusTrader id (`ESZ5.GLBX`). Because symbols recycle, a later export
  pairs it with the expiration.
- **Refused definitions.** A definition with an undefined activation or
  expiration cannot be placed in a window, so 223 refuses it loudly. It
  never inserts an unbounded one. No real ES definition is in hand: the
  free fixture is an equity record with both undefined, and the adopted
  jobs hold none. 223's first purchase confirms that CME futures and spreads
  carry both (see Risk Assessment).
- **Re-delivery.** An identical re-sent definition is a no-op. A changed
  one that overlaps fails the exclusion, and 223 must handle that
  explicitly, never with a bare `ON CONFLICT DO NOTHING` that would hide it.

### Technical Decision 6: `unit_id` on every tick row, with no foreign key

`tick_trade.unit_id BIGINT NOT NULL` is the architecture's integer
provenance id, and the PM confirmed it over a string. It is not part of the
key. There is no FK to `tick_archive_unit`:

- A foreign key is checked row by row through a trigger, so on a
  multi-million-row `COPY` it roughly doubles write cost for a value that
  is constant for the whole load.
- Unit rows are never deleted.
- 224's count check (rows with `unit_id = N` equals the records decoded)
  catches a wrong id loudly.

The ledger's `unit_id` does have an FK, because its rows are few. Measuring
compressed size is 225's job; the id compresses to a near-constant run per
unit.

### Technical Decision 7: the ledger carries `calendar_id`, not the tier

221 left open whether the ledger carries `calendar_id` or derives it from
the product. **It carries it.** Deriving it needs code (`FUTURES_PRODUCT_CALENDAR`)
and cannot be done in SQL. The session rows live in the production database,
so no join exists across the two databases. A ledger row should state its
session's full identity by itself. There is no CHECK on the calendar id:
the set grows with 230 (GC), and 221's session lookup already refuses an
unknown calendar at ingest.

The tier is not on the ledger. It is `tick_request.schema`, one join away,
and a copy could disagree with it. The architecture's "recorded on each
archive unit and ledger row" is satisfied by reaching it through the unit.

### Technical Decision 8: chunk interval of 7 days, from wall-clock span

`TICK_TRADE_CHUNK_INTERVAL = timedelta(days=7)`, rendered to nanoseconds
(604,800,000,000,000) in the migration.

- **The rule** (journal 20260719): wall-clock span ÷ 1,000–2,000 target
  chunks. The table's plausible data span is about 20 years: the plan's
  year of L1, rare older purchases such as the COVID era, and the realtime
  future. That gives about 1,040 chunks at 7 days.
- **Minute tier.** The same interval as the minute tier's proven geometry,
  `MINUTE_OHLCV_CHUNK_INTERVAL`, as the architecture asks for.
- **Size.** Volume sanity: ES averaged ~390,000 trades per day file in the
  adopted job. A 7-day chunk holds a few million rows for the initial
  universe, well inside comfortable bounds.
- **Revision.** 225 validates it and may re-set it with a tick-track
  migration.

No space dimension (physical grouping waits for 225).
`create_default_indexes => FALSE`, because the primary key, led by
`instrument_id, ts_event`, serves the per-instrument range reads and the
instrument-session counts. 225 adds indexes from measured queries.

### Technical Decision 9: every CHECK is rendered from its enum

| Column | Source |
|---|---|
| `tick_request.schema` | `ARCHIVED_SCHEMAS = STORED_TIERS \| COMPANION_SCHEMAS` (`trades`, `tbbo`, `definition`; `mbp-1` stays estimate-only) |
| `tick_request.stype_in` | `SType` |
| `tick_request.delivery_mode` | `DeliveryMode` |
| `tick_archive_unit.state` | `UnitState` |
| `tick_archive_unit.fetch_status` | `FetchStatus` |
| file-required rule | `UNIT_STATES_WITH_FILE` |

A new member (for example `statistics` in 227, or a live-segment delivery
mode in the realtime initiative) needs a tick-track migration that
re-renders the constraint (journal 20260901). A unit test asserts that
every rendered list equals its enum, so hand-listing cannot creep in.

### Patterns and Conventions

- Migration ids are `tick_NNN_*` (923). SQL is idempotent (`IF NOT EXISTS`,
  `if_not_exists => TRUE`, and named constraints added inside the CREATE).
- Tables sit in `public` with a `tick_` prefix. The tick database holds
  nothing else, and the prefix keeps names unambiguous in logs and in the
  shared-cluster placement option.
- Each migration's comment cites the Technical Decision it implements by
  name and number, following the Kalshi track's style.
- No grants appear inside migrations. On the tick database, `tick_migrate`'s
  default privileges already give `tick_app` DML, and the artifact's
  enumerated list is the audited statement (923 Integration Requirements).
  The Kalshi track's in-migration `GRANT … TO trading_app` does not carry
  over, because the role names differ per database.

## Implementation Details

### Database / Storage Schema

Column types follow Technical Decision 3. "ns" means `BIGINT` nanoseconds
since the epoch.

**`tick_001_extensions`**: `CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS btree_gist;`

**`tick_002_manifest`**

`tick_request`

| Column | Type | Notes |
|---|---|---|
| `request_id` | `BIGINT GENERATED ALWAYS AS IDENTITY` PK | |
| `dataset` | `TEXT NOT NULL` | |
| `schema` | `TEXT NOT NULL` | CHECK from `ARCHIVED_SCHEMAS` |
| `symbols` | `TEXT[] NOT NULL` | `cardinality(symbols) > 0` |
| `stype_in` | `TEXT NOT NULL` | CHECK from `SType` |
| `range_start`, `range_end` | `DATE NOT NULL` | end exclusive; `range_end > range_start` |
| `delivery_mode` | `TEXT NOT NULL` | CHECK from `DeliveryMode` |
| `is_adopted` | `BOOLEAN NOT NULL` | no default; the writer states it |
| `provider_job_id` | `TEXT UNIQUE` | NULL until submitted, and for direct range |
| `estimated_cost_usd` | `NUMERIC NOT NULL` | `>= 0`; written at *requested* |
| `actual_cost_usd` | `NUMERIC` | `>= 0`; set when the provider reports it |
| `provider_record_count`, `billed_size_bytes` | `BIGINT` | job-level, from `BatchJob` |
| `requested_at` | `TIMESTAMPTZ NOT NULL` | |
| `committed_at` | `TIMESTAMPTZ` | submit time (adopted: the job's `ts_received`); the 30-day cap's clock |
| `download_deadline` | `TIMESTAMPTZ` | `BatchJob.ts_expiration`; NULL for adopted and direct range |

`tick_archive_unit`

| Column | Type | Notes |
|---|---|---|
| `unit_id` | `BIGINT GENERATED ALWAYS AS IDENTITY` PK | the id every tick row carries |
| `request_id` | `BIGINT NOT NULL` FK → `tick_request` | |
| `unit_date` | `DATE NOT NULL` | the UTC day `[unit_date, unit_date + 1)`; `UNIQUE (request_id, unit_date)`; `range_start <= unit_date < range_end` is enforced by 223, since it spans tables |
| `state` | `TEXT NOT NULL` | CHECK from `UnitState` |
| `state_changed_at` | `TIMESTAMPTZ NOT NULL` | |
| `fetch_status` | `TEXT NOT NULL` | CHECK from `FetchStatus` |
| `failure_reason` | `TEXT` | display only; `(fetch_status = 'UNKNOWN') = (failure_reason IS NULL)` |
| `attempt_count` | `INTEGER NOT NULL` | `>= 0` |
| `last_attempt_at` | `TIMESTAMPTZ` | |
| `file_path` | `TEXT` | relative to the archive root, a 223 setting, so the archive can move |
| `file_size_bytes` | `BIGINT` | |
| `file_sha256` | `TEXT` | the three file columns are all non-NULL when `state` is in `UNIT_STATES_WITH_FILE` |
| `provider_record_count` | `BIGINT` | the day's count from the free record-count query |
| `decoded_record_count` | `BIGINT` | set by 224 |
| `superseded_by_unit_id` | `BIGINT` FK → self | `<> unit_id`; current means `IS NULL` |
| `repurchase_of_unit_id` | `BIGINT UNIQUE` FK → self | the expired unit this one re-buys |

**`tick_003_definitions`**: `tick_definition`, with the columns of
Technical Decision 5. `activation_ns` and `expiration_ns` are
`NOT NULL`, with `CHECK (expiration_ns >= activation_ns)`, PK
`(instrument_id, activation_ns)`, the `EXCLUDE` constraint, and
`unit_id BIGINT NOT NULL` FK → `tick_archive_unit`.

**`tick_004_trades`**: `tick_trade`

| Column | Type | Source field |
|---|---|---|
| `ts_event` | `BIGINT NOT NULL` | `ts_event` (ns) |
| `ts_recv` | `BIGINT NOT NULL` | `ts_recv` (ns) |
| `instrument_id` | `BIGINT NOT NULL` | `instrument_id` |
| `publisher_id` | `INTEGER NOT NULL` | `publisher_id` |
| `sequence` | `BIGINT NOT NULL` | `sequence` |
| `sequence_ordinal` | `SMALLINT NOT NULL` | computed, `>= 0` (Technical Decision 1) |
| `price` | `BIGINT NOT NULL` | `price` (1e-9 fixed point) |
| `size` | `BIGINT NOT NULL` | `size` |
| `action` | `TEXT NOT NULL` | `action` |
| `side` | `TEXT NOT NULL` | `side` |
| `flags` | `SMALLINT NOT NULL` | `flags` |
| `depth` | `SMALLINT NOT NULL` | `depth` |
| `ts_in_delta` | `INTEGER NOT NULL` | `ts_in_delta` |
| `bid_px_00`, `ask_px_00` | `BIGINT` | `tbbo` only |
| `bid_sz_00`, `ask_sz_00`, `bid_ct_00`, `ask_ct_00` | `BIGINT` | `tbbo` only |
| `unit_id` | `BIGINT NOT NULL` | the archive unit (Technical Decision 6) |

- **Primary key:** `(instrument_id, ts_event, sequence, sequence_ordinal)`.
- **BBO check:** `CHECK` that the six BBO columns are all NULL or all
  non-NULL, stated as pairwise `(x IS NULL) = (bid_px_00 IS NULL)`.
- **Hypertable:** `create_hypertable('tick_trade', 'ts_event',
  chunk_time_interval => 604800000000000, create_default_indexes => FALSE,
  if_not_exists => TRUE)`. The interval is rendered from
  `TICK_TRADE_CHUNK_INTERVAL`.

**`tick_005_ingest_ledger`**: `tick_ingest_ledger`

| Column | Type | Notes |
|---|---|---|
| `unit_id` | `BIGINT NOT NULL` FK → `tick_archive_unit` | |
| `instrument_id` | `BIGINT NOT NULL` | |
| `calendar_id` | `TEXT NOT NULL` | Technical Decision 7 |
| `session_date` | `DATE NOT NULL` | |
| `record_count` | `BIGINT NOT NULL` | `>= 0` |
| `volume` | `BIGINT NOT NULL` | `>= 0`; sum of `size` |
| `first_event_ns`, `last_event_ns` | `BIGINT` | NULL exactly when `record_count = 0`; `first <= last` |

The primary key is `(unit_id, instrument_id, session_date)`. A
zero-record row is legal, because a quiet instrument-session is complete
with zero records (architecture, "Completeness definitions").

### Column contract (`data/tick/storage_columns.py`)

- `TICK_TRADE_COLUMNS` is an ordered mapping from DBN field to column, for
  the trades body plus the six `tbbo` fields.
- `TICK_TRADE_KEY` lists the key columns.
- `TICK_DEFINITION_COLUMNS` maps the definition fields the model keeps.

The parity tests assert:

- **Tables:** each map equals the migrated table's columns, from
  `information_schema`, minus the computed and provenance columns, which
  are named explicitly.
- **Real layouts:** every mapped field exists in the DBN fixture dtype for
  `trades` (v2 and v3 fixtures), `tbbo` (v3) and `definition` (v3). This is
  the "fixture is the real format" rule. The adopted v1 files have the same
  trades and tbbo layout, which was measured during this design.

## Integration Points

### Provides to Other Slices

- **223 (acquisition):**
  - `tick_request` and `tick_archive_unit` with their lifecycle columns;
  - the `requested` → `submitted` transaction shape;
  - `repurchase_of_unit_id` for expired units;
  - `committed_at` and the cost columns for the per-pass and 30-day
    ceilings;
  - `tick_definition` and `TICK_DEFINITION_COLUMNS`;
  - `UnitState`.
- **224 (ingest):**
  - `tick_trade` with its key and the `sequence_ordinal` definition;
  - `TICK_TRADE_COLUMNS`;
  - `tick_ingest_ledger`;
  - `superseded_by_unit_id` as the "current" predicate;
  - `tick_app`'s `TEMPORARY` privilege (923) for COPY staging, if 224
    stages.
- **225 (proof):** a hypertable with no compression and no extra index, so
  it measures from a clean baseline, plus `TICK_TRADE_CHUNK_INTERVAL` as the
  value to validate.
- **226 (backup):** five named tables, with the manifest and definitions
  small and the trades table rebuildable from the archive.
- **227–229:** `tick_definition` for resolution and catalog. The sentinel
  and nanosecond conventions are theirs to translate on read.

### Consumes from Other Slices

- **923:** the track, the guard, the fixtures and the artifact. If the
  artifact or fixtures change, 222's privilege tests fail. Nothing is
  worked around.
- **220:** the enums and the DBN fixture layouts. A new DBN version that
  renames a field fails the parity test, which is the intended alarm.
- **221:** calendar ids (stored, not joined).

## Success Criteria

### Functional Requirements

1. On a bare database, `mt data init --database tick` applies the bootstrap
   and `tick_001`–`tick_005`. A second apply is a no-op, and `migrate status
   --track tick` shows all applied.
2. `tick_trade` is a hypertable on `ts_event` with exactly one dimension, an
   integer interval equal to `TICK_TRADE_CHUNK_INTERVAL` in nanoseconds, no
   compression settings, and no index other than the primary key.
3. Two rows identical in every field except `sequence_ordinal` both insert.
   A repeat of the same ordinal conflicts.
4. A `tick_trade` row with some but not all BBO fields NULL is rejected.
5. Overlapping validity windows for one `instrument_id` are rejected.
   Disjoint windows for a reused id are accepted.
6. Every enum-rendered CHECK rejects a value outside its enum and accepts
   every member. A unit in `downloaded`, `verified` or `ingested` without
   file columns is rejected. `fetch_status = 'UNKNOWN'` with a
   `failure_reason` is rejected.
7. A ledger row with `record_count = 0` and non-NULL event times is
   rejected. A zero-record row with NULL times is accepted.
8. Exactness round trip: real `trades` and `tbbo` fixture records, written
   through `TICK_TRADE_COLUMNS`, read back equal field for field. This
   includes the nanosecond timestamps, the fixed-point prices, and a `size`
   of `4294967295`.
9. `TickEventType` and its unit test no longer exist, and nothing imports
   them.

### Technical Requirements

- **Privileges.** After provision → `init --database tick` → provision
  again:
  - `tick_app` can SELECT, INSERT, UPDATE and DELETE on each of the five
    tables, including inserts that draw identity values with no sequence
    grant.
  - `tick_app` cannot TRUNCATE them or run DDL.
  - `tick_migrate` created `btree_gist` as owner with no added attribute.
- Unit tests for rendering, the enum lists, `ARCHIVED_SCHEMAS`, the
  `UnitState` order and the column-map parity. Integration tests on
  `migrated_tick_db` and `provisioned_tick_db`.
- ruff clean on touched files, and mypy per the kalshi_support path note.
- The data-correctness contract, slice plan Notes, migrations README,
  README and CHANGELOG are updated as listed in Technical Scope.

### Integration Requirements

- 223 and 224 can be designed against the table, column-map and vocabulary
  names in this document with no schema change of their own, except
  227's future `statistics` CHECK re-render.
- The full unit and integration tiers stay green apart from the known
  pre-existing failures.

### Verification Walkthrough

These steps run against the test cluster, because no production tick
database exists. Export `MT_TIMESCALE_TEST_URL` from `.env` first (strip
the quotes).

1. **The storage suites.**

   ```bash
   uv run pytest test/integration/data/test_tick_storage_track.py \
                 test/integration/data/test_tick_role_privileges.py \
                 test/unit/data/tick/ -q
   ```

   Expected: all pass. The privilege file now includes the write-surface
   and `btree_gist` cases.

2. **Apply by hand to a scratch database and look at it.** Point both tick
   variables at a throwaway database created on the test cluster:

   ```bash
   createdb --maintenance-db="$MT_TIMESCALE_TEST_URL" mt_scratch_tick_222
   export MT_TICK_DB_URL="${MT_TIMESCALE_TEST_URL%/*}/mt_scratch_tick_222"
   export MT_TICK_MAINTENANCE_URL="$MT_TICK_DB_URL"
   uv run mt data init --database tick
   uv run mt data migrate status --track tick
   ```

   Expected: `001_schema_migrations` and `tick_001`–`tick_005` are listed
   as applied. A second `init` reports nothing to apply.

3. **Inspect the geometry.**

   ```bash
   psql "$MT_TICK_DB_URL" -c "SELECT column_name, column_type, integer_interval
     FROM timescaledb_information.dimensions WHERE hypertable_name='tick_trade'"
   psql "$MT_TICK_DB_URL" -c "\d tick_trade"
   ```

   Expected: one dimension, `ts_event`, `bigint`, `604800000000000`. The
   primary key is `(instrument_id, ts_event, sequence, sequence_ordinal)`,
   there is no other index, and the BBO CHECK is present.

4. **See the key decision's evidence on real data (read-only).** This
   repeats the design measurement on one adopted day:

   ```bash
   uv run python -c "
   import databento as db, numpy as np
   a = db.DBNStore.from_file('/data/market-data/databento/GLBX-20240930-USM7UXXJBA/glbx-mdp3-20240903.trades.dbn.zst').to_ndarray()
   k = a[['instrument_id','ts_event','sequence']]
   print(len(a), len(a) - len(np.unique(k)))"
   ```

   Expected: `511965 9922`. Nearly 10,000 rows would collide on the
   architecture's key.

5. **Tear down the scratch database** (it was created by step 2):
   `dropdb --maintenance-db="$MT_TIMESCALE_TEST_URL" mt_scratch_tick_222`.

No command lists tick tables or units yet. `mt data tick status` is 224's.

## Risk Assessment

### Technical Risks

- **Definition windows unverified on real CME records.** No ES definition
  is in hand. If CME spreads or other configured instruments carry an
  undefined activation or expiration, `NOT NULL` refuses them.
- **Integer-time hypertable is new to this codebase.** Every other
  hypertable partitions on `timestamptz`, so tooling that assumes
  `time_interval` (for example health or rechunk helpers, if ever pointed
  at the tick database) reads NULL there.

### Mitigation Strategies

- 223's first definition purchase for the proof ranges (under $0.01) is
  checked against these constraints before 224 depends on it. If windows
  are missing for non-universe instruments, 223 skips non-universe
  definitions. If they are missing for configured futures, 223 raises and
  the design is revised. It is never widened silently.
- 222's tests read the interval through `integer_interval`. Any tick
  surface (224 onward) that reads hypertable metadata has its own test on
  the tick database. No minute-tier helper is reused against it unless it
  is tested there.

## Implementation Notes

### Development Approach

1. Constants and the column contract, with unit tests (enum rendering,
   parity against the DBN fixtures).
2. Migrations `tick_001`–`tick_005`, one at a time, each with its
   integration tests on `migrated_tick_db`. Commit per migration.
3. The grant artifact's write list and the privilege cases on
   `provisioned_tick_db`.
4. Remove `TickEventType` and its test. Grep for imports.
5. Docs: the contract, plan Notes, READMEs and CHANGELOG. Run the full unit
   and integration tiers.

### Special Considerations

- **Realtime paths check.**
  - *Path A (assemble from realtime):* not foreclosed. A live segment
    becomes a `tick_request` with a new `delivery_mode` member (a CHECK
    re-render) and units with sub-day ranges. That needs additive columns
    on `tick_archive_unit` and the per-unit time range the architecture
    already assigns to the realtime initiative. `sequence_ordinal` is
    defined by delivery order, which a live stream has too. Supersession
    stays delete-by-unit.
  - *Path B (historical with delay):* neutral. It is the same request and
    unit shape.
  - No decision here rules out either path.
- **Journal citations.**
  - 20260719: chunk interval from wall-clock span (Technical Decision 8).
  - 20260725: no aggregate informs completeness; none is created.
  - 20260901: served enums grow, so CHECKs are rendered and re-rendered by
    migration (Technical Decision 9).
- **Security.** No credentials or new settings. The artifact stays
  password-free. `tick_app` keeps no TRUNCATE and no DDL.
- **Does this belong in the API?** Not in this slice: it has no reader.
  229 serves ticks and the contract catalog from these tables.

### Architecture statements this design supersedes

Each is recorded in the slice plan's Notes when this slice is implemented:

1. **The natural key.** It becomes `(instrument, event time, sequence,
   sequence_ordinal)`, not the provider's triple, which is not unique in
   real data (Technical Decision 1).
2. **The *failed* state.** It is `fetch_status`, alongside a
   furthest-reached `state` (Technical Decision 4).
3. **The manifest.** It is two tables. Job-grain facts sit on
   `tick_request`, and the unit lifecycle stays on `tick_archive_unit`
   (Technical Decision 4).
4. **The archive unit.** It is one UTC day of a request, normally one
   provider file. It is not "one provider file", because a hole has no
   file (Technical Decision 4). The contract's vocabulary entry is
   corrected to match.
5. **The tier.** It is reached through the unit's request, not stored on
   the ledger (Technical Decision 7).
