---
docType: slice-design
slice: ingest-pass-and-proof-parity
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [221, 222, 223, 224]
interfaces: [226, 227, 228, 229, 230, 231, 233]
dateCreated: 20260930
dateUpdated: 20261003
status: complete
---

# Slice Design: ingest-pass-and-proof-parity

## Overview

After 224, a manually run pass fills the tick archive: tier units (the
`trades` or `tbbo` file for one UTC day) rest at *verified*, and definition
units are already projected into `tick_definition`. Nothing is in
`tick_trade` yet, and no command answers "what do I have?".

This slice adds the two missing halves:

1. **`mt data tick ingest`**, a manually run pass that loads every
   *verified* tier unit into `tick_trade`. Each unit is decoded in a worker
   thread through 220's bounded iterator, every record is resolved to a
   contract and assigned a CME session, and the unit is written with `COPY`
   over the worker's own connection. Three checks (counts, resolution,
   session boundaries) gate the commit. The unit's rows, its ingest-ledger
   rows and its *ingested* transition commit in one transaction.
2. **`mt data tick status` and `mt data tick coverage`**, the operator
   surface at minute parity. Status reports sessions per product and
   contract by condition. Coverage reports one line per session and checks
   raw-table counts against the ledger. Both read only the manifest, the
   ledger, the availability tables and raw counts.

After this slice the tick database fills and status answers. The plan's
Notes list this as the state 225 leaves behind.

## Value

- **The archive becomes queryable data.** The two adopted free-credit ES
  jobs (27.7 M records over the September and December 2024 rolls) load
  into `tick_trade` with nothing bought. 226's proof measures this load.
- **Every loaded tick is proved, not assumed.** A unit becomes *ingested*
  only when the provider's count, the decoded count and the stored row
  count agree, every record belongs to a known contract, and every record
  falls inside a CME session. A failure names the check and the evidence.
- **The operator can answer "what do I have?" for ticks.** One command
  shows how many sessions are complete, awaiting ingest, in flight,
  pending at the provider, missing, failed, exhausted, holed or of unknown
  condition, per product and per contract. Coverage adds the raw-count
  proof per session.
- **Tier upgrades are safe.** When 226 picks `tbbo` over a range where
  `trades` is already loaded, ingest replaces the old rows in one
  transaction instead of failing on the shared key (Technical Decision 5).
- **It closes three contract invariants.** I11 (tick completeness from the
  manifest), I12 (provenance and supersession) and I13 (session-assigned
  ticks) each list 225 as a closing slice.

## Technical Scope

**Included**

- The ingest pass: unit selection, a thread worker per unit with its own
  tick-database connection, decode, contract resolution, session
  assignment, `sequence_ordinal`, `COPY` into `tick_trade`, the three
  checks, ledger rows including zero-record rows, supersession of a
  lower-tier loaded unit, and the *ingested* transition.
- `mt data tick ingest [--unit-id N ...] [--json]`, on the tick pass
  contract, with exit codes from `tick_exit.py`.
- A store-only run context, so ingest needs no API key and status needs
  neither the key nor the lock (Technical Decision 1).
- `mt data tick status [--product P] [--all-instruments] [--json]`.
- `mt data tick coverage --start D --end D [--product P] [--json]`.
- The status computation as a library function that returns plain
  dataclasses, so 230 can serve it without reshaping.
- The data-correctness contract rows for I10, I11, I12 and I13.
- README tick section, CHANGELOG.

**Excluded**

- **Buying anything.** Ingest makes no provider call. The provider's
  per-day record count is already on the unit (224 stores it at
  *verified*).
- **Schedule code.** There is no `PassKind.TICK`, timer, `mt-run tick`
  or `pass_runs` row; all of that is 233's.
- **Measuring.** Ingest rate, bytes per row, the decode batch budget, the
  worker count, chunk interval and physical grouping are 226's. This
  slice sets conservative named constants and does not tune them.
- **The active contract, roll rules and the next roll** in status. They
  are 228's.
- **`get`, `debug`, `backfill`** and every API endpoint. They are 229's
  and 230's.
- **Intra-unit checkpoints.** Resumption is at the unit boundary
  (architecture; Future Work item 3 waits for 226's numbers).
- **Provisioning the production tick cluster.** 224 recommended 225 or
  226. This slice needs only a scratch database on the test cluster, so
  it stays with 226, whose contention measurement is the first step that
  needs the production host.
- **Extending the CME calendar from ingest.** 221 listed ingest as an
  extension trigger. This design drops it (Technical Decision 7).

## Dependencies

### Prerequisites

- **221 (complete):** `CME_EQUITY` sessions from 2020-01-01 23:00 UTC,
  `TradingCalendar.sessions_between` and `populated_span`,
  `SessionIndex.locate_ns`, `OutOfPopulatedRangeError`,
  `FUTURES_PRODUCT_CALENDAR`.
- **222 (complete):** `tick_trade`, `tick_ingest_ledger`,
  `TICK_TRADE_COLUMNS`, `TICK_TRADE_KEY`, the `sequence_ordinal`
  definition (222 Technical Decision 1), and `superseded_by_unit_id`.
- **223 and 224 (complete, v0.22.0):** adopted and purchased units at
  *verified* with `provider_record_count`, `tick_definition` populated for
  the same request shape, the availability tables, the compare-and-set
  transitions in `manifest_repo.py`, `reset` for exhausted units, the pass
  contract and `run_phase`.
- **220 (complete):** `DbnFileReader`, `ITickFile.iter_batches`,
  `RecordBatch`, `TICK_DECODE_BATCH_BYTES`, and the finding that the
  array path releases the interpreter lock during decompression
  (220 Technical Decision 6: worker threads).
- **Test cluster:** a scratch tick database for the walkthrough, as in
  224. The archive at `/data/tick-archive` already holds the two adopted
  jobs and the four definition jobs `GLBX-20260930-*`.
- **No new package.** `psycopg`'s `Cursor.copy` covers the write path.

### Interfaces Required

- The manifest: a tier unit's `request_id`, `unit_date`, `state`,
  `fetch_status`, `reopened_at`, `superseded_by_unit_id`, `file_path`
  (relative to `MT_TICK_ARCHIVE_DIR`) and `provider_record_count`. The
  request's `dataset`, `schema`, `symbols` and `stype_in`.
- `tick_definition` rows with `asset` equal to the product root. Measured
  on the 78 archived definition files: 61 or 70 definition records per
  day, which are 41 or 46 instruments: 21–22 outrights
  (`instrument_class = 'F'`) and 20–24 spreads (`'S'`), most spreads sent
  twice under one key, all with `asset = 'ES'`. (Corrected at
  implementation, task 3.4: the design first counted records as
  instruments.)
- The production database, read-only, for sessions.
- `tick_day_condition` and `tick_dataset_edge` for status.

## Architecture

### Component Structure

New modules in `src/manta_trading/data/tick/`:

| Module | Role |
|---|---|
| `store_context.py` | `TickStore` and `open_tick_store(settings, *, lock_key)`: tick connection, migrations check, optional lock, archive root. `TickRun` becomes `TickStore` plus the provider (Technical Decision 1). |
| `ingest_select.py` | Which units ingest this run, which are waiting for definitions, and which are outranked by a higher tier (Technical Decisions 4, 5). |
| `ingest_plan.py` | Built on the loop for one unit, before it is handed to a worker: the product and calendar, the `SessionIndex`, the populated span, the definitions windows, and the loaded units it supersedes. Immutable. |
| `ingest_records.py` | Pure NumPy over one `RecordBatch`: contract resolution, session positions, `sequence_ordinal` with a carry across batches, ledger accumulation, and COPY row building. No I/O. |
| `ingest_worker.py` | The synchronous per-unit function a thread runs: open the file, open its own connection, one transaction (supersede, `COPY`, count check, ledger, *ingested*), close. Returns a `UnitOutcome`. |
| `ingest_pass.py` | `IngestPhase` and `run_ingest`: selection, one plan per unit, a bounded pool of worker threads, failure recording on the run connection. |
| `ingest_checks.py` | `IngestCheck` enum and the failure-reason text for each check, spelled once. |
| `tick_status.py` | `build_status` and `build_coverage`: the session classification, per-product and per-contract tallies, and the raw-count comparison. Returns dataclasses. |
| `status_reads.py` | The SQL reads status and coverage use (units per shape and day, ledger sums, raw counts, conditions, edge). |

CLI: `tick.py` gains `ingest`, `status` and `coverage`. Rendering goes in
`tick_status_render.py`, and the ingest report reuses
`tick_pass_render.py`'s phase rendering. `tick_exit.py` is unchanged.

Changed modules:

- `run_context.py`: split per Technical Decision 1. Every 224 call site
  keeps `run.conn`, `run.archive_root`, `run.clock`.
- `pass_contract.py`: `TickPassPhaseName.INGEST`, and `PassPhase` and
  `TickPass` become generic in the run type (Technical Decision 1). The
  Kalshi parity test is updated to allow the declared divergence.
- `manifest_repo.py`: each compare-and-set transition is split into a
  statement builder and an executor, with one sync executor added, so the
  worker can run `mark_ingested` inside its own transaction without a
  second copy of the SQL (Technical Decision 3).
- `constants.py`: `TICK_INGEST_LOCK_KEY`, `TICK_INGEST_WORKERS`, and the
  tier rank (Technical Decision 5).

### Data Flow

```
mt data tick ingest
  └─ open_tick_store(lock_key=TICK_INGEST_LOCK_KEY)        # no provider, no API key
      └─ IngestPhase
          1. select   verified tier units, open, not reopened, current
                      ├─ outranked by a current higher-tier unit (same shape, day) → skip "outranked"
                      └─ companion definition unit for (shape, day) not ingested → skip "awaiting definitions"
          2. for each selected unit, earliest day first, at most TICK_INGEST_WORKERS at once:
             a. plan (on the loop, via asyncio.to_thread, one at a time)
                  product ← planning_product(stype_in, symbols); calendar ← calendar_for_product
                  sessions ← TradingCalendar.sessions_between(day 00:00Z, day+1 00:00Z)   # production DB
                  populated span; SessionIndex(sessions)
                  definitions ← tick_definition WHERE asset = product AND window meets [first open, last close]
                  superseded ← current ingested tier units of the same shape and day, lower tier
             b. worker thread (own sync connection to the tick DB):
                  open file ─► iter_batches() ─► per batch:
                      resolve (instrument_id, ts_event) against definitions     → resolution check
                      populated span, then locate_ns(ts_event)                  → session-boundary check
                      sequence_ordinal (carry keyed by the triple, whole unit)
                      accumulate ledger (instrument, session): count, volume, first, last
                      COPY rows (binary) into tick_trade with unit_id
                  in the same transaction, first: supersede and delete old rows
                  after the last batch:
                      counts check: provider == decoded == rows WHERE unit_id = N
                      INSERT ledger rows (every definition-valid instrument × touched session)
                      mark_ingested (compare-and-set) ─► COMMIT
                  any check failed ─► ROLLBACK, return the failure
             c. on the loop: a failed unit → record_failure(deterministic=True, reason names the check)
  └─ PassResult ─► tick_pass_render / --json ─► EXIT_BY_OUTCOME

mt data tick status / coverage
  └─ tick connection (migrations checked, no lock) + production calendar (sessions)
      └─ manifest + ledger + conditions + edge (+ raw counts for coverage) ─► dataclasses ─► Rich / --json
```

Only a `UnitOutcome` (unit id, counts, check failure or none, duration)
returns from a worker to the loop. Records never cross into the event
loop. There is no per-batch progress between a unit's start and its
commit. The architecture asks for it so that a long ingest stays
observable under a start timeout. 225 is a manual command with no timeout,
and its units take seconds, so the omission is recorded as a deviation. It
is handed to 233, which adds the timer and systemd timeouts, and to 226,
which measures the larger days. The worker takes no progress hook today,
and adding one later means adding a callback argument, not a
restructure.

### State Management

No new table and no migration. The state this slice writes:

- **`tick_trade`**: a unit's rows, each carrying its `unit_id`.
- **`tick_ingest_ledger`**: one row per (unit, instrument, session),
  written in the same transaction as the rows.
- **`tick_archive_unit`**:
  - *verified* → *ingested*, with `decoded_record_count`;
  - a failed ingest keeps *verified* and gets `RETRY_EXHAUSTED` with the
    check named;
  - a superseded unit gets `superseded_by_unit_id`.

A unit's transaction is all or nothing. A worker that dies, a lost
connection or a failed check leaves no rows, no ledger rows and no
transition. That is stronger than the architecture's "raw rows the next
attempt conflict-ignores" (Technical Decision 2).

`reset` (224) reopens an exhausted ingest exactly as it reopens an
exhausted download: `fetch_status` goes back to `UNKNOWN`, and the unit is
still *verified*, so the next ingest run picks it up.

Status and coverage write nothing.

## Technical Decisions

### Technical Decision 1: a store-only run context, and a pass contract generic in its run

`open_tick_run` (223) builds the provider first, and the provider refuses
without `MT_DATABENTO_API_KEY`. Ingest makes no provider call, and status
must work on a host that holds no key. So the run context is split:

```python
@dataclass(frozen=True)
class TickStore:            # store_context.py
    settings: Settings
    conn: psycopg.AsyncConnection[Any]
    archive_root: Path
    run_id: uuid.UUID
    clock: Clock

@dataclass(frozen=True)
class TickRun(TickStore):   # run_context.py, unchanged for every 224 caller
    provider: TickProvider
```

- `open_tick_store(settings, *, lock_key)` runs the preflight in 223's
  order without the provider: unknown `MT_TICK_*` keys, the tick URL, the
  archive directory, connect, migrations, lock.
- `open_tick_run` composes it with `lock_key=TICK_ACQUISITION_LOCK_KEY`
  and the provider.
- Status and coverage do not use either. They call the shared
  connect-and-check-migrations helper directly, take no lock and need no
  archive directory.

`PassPhase` is typed `run(self, run: TickRun)`, so an ingest phase that
takes a `TickStore` cannot join a `TickPass`. `PassPhase` and `TickPass`
become generic in the run type (`RunT` bound to `TickStore`). The
acquisition pass is `TickPass[TickRun]`, and ingest is `TickPass[TickStore]`
with one phase. The Kalshi original is not generic. The parity test's
declared-divergence list gains this item, and the slice's task file carries
the standing contract-diff task.

*Rejected:* a null provider passed to `open_tick_run` for ingest. It is
the cheap hack the project rules forbid, and a status run would still fail
on the missing key. *Rejected:* `provider: TickProvider | None`. Every 224
call site would need a narrowing check for a field that is never `None`
there.

### Technical Decision 2: one transaction per unit, on the worker's own connection, with a plain `COPY`

For each unit, the worker opens a synchronous `psycopg.Connection` to the
tick database and runs one transaction:

1. Supersede and delete the lower-tier loaded units' rows (Technical
   Decision 5).
2. `COPY tick_trade (<TICK_TRADE_COLUMNS>, sequence_ordinal, unit_id) FROM
   STDIN (FORMAT BINARY)`, fed batch by batch with `write_row` and
   `set_types`.
3. The counts check.
4. Insert the ledger rows.
5. `mark_ingested`, compare-and-set.
6. `COMMIT`.

No staging table, no `ON CONFLICT`. Because the unit's rows commit or
vanish together, there are never leftover rows of the same unit to skip.
A key conflict can only come from a *different* current unit's rows, which
is the overlap 222 Technical Decision 1 wants to fail loudly. So `COPY`
raises `UniqueViolation`. The worker rolls back and reports the unit as
failed with the overlap check, naming the key and the other current units
on that day.

This supersedes the architecture's sentence that a pass dying mid-unit
"leaves raw rows the next attempt's natural-key conflict-ignore skips".
Nothing is left, so nothing is skipped, and completeness still never sees
a half-ingested unit.

**Size of one transaction.** A unit is one UTC day. On the adopted jobs
that is at most 511,965 `trades` records (2024-09-03) and on average
about 340,000 `tbbo` records. That is a small `COPY` for PostgreSQL. 226
measures the larger days of the Standard-plan range. If a day ever needs
splitting, that is Future Work item 3 (intra-unit checkpoints).

**`write_row`, not a hand-built binary buffer.** Row building is
per-record Python and holds the interpreter lock. At an estimated few
microseconds a row, one unit takes seconds, and the whole 27.7 M-record
proof set takes minutes. The architecture's pass/fail has two targets:

- a day's sessions ingest far faster than a day of market time;
- a month of sessions ingests within an operator's working session.

The estimate meets both by orders of magnitude. Walkthrough step 4 checks
both against the measured wall time (Success Criteria), so 226 starts from
a stated baseline. A NumPy-built binary `COPY` buffer would be faster, but it is
an encoder to write and test. It is 226's option if the measured rate
calls for it, and nothing in this design blocks it: row building sits
behind one function in `ingest_records.py`.

**Workers.** `TICK_INGEST_WORKERS = 2` in `constants.py`: one unit
decoding while the other writes, and at most two batches of
`TICK_DECODE_BATCH_BYTES` in memory. 226 re-sets it from measurement and
rewrites its docstring. It is a constant, not a flag, until a measurement
gives a reason to vary it.

**Thread state review** (python rules; 220 Technical Decision 6 requires
it to be repeated when the worker is built). Each worker owns:

- its `DbnFile`;
- its `psycopg.Connection`;
- its ordinal carry and ledger accumulator.

It receives an immutable `UnitIngestPlan`: a `SessionIndex`, NumPy arrays
of definitions, the unit row and the superseded unit rows. It shares
nothing mutable with the loop or the other worker. The `DbnFileReader` is
stateless and shared. The async run connection is never touched from a
worker thread.

### Technical Decision 3: one statement per transition, two executors

The worker must run `mark_ingested` inside its own transaction on a
synchronous connection. The compare-and-set `UPDATE` in `manifest_repo.py`
is written once against the async connection. Copying its SQL would put
the same state rule in two places. So each transition becomes a
**statement builder** that returns `(sql, params, expected)`, plus:

- the existing async `_transition` executor, which opens its own
  transaction;
- one sync executor, `execute_transition_sync(cur, statement, unit_id)`,
  which runs inside the caller's transaction and raises
  `ManifestTransitionError` on zero rows.

Only `mark_ingested` and the new `mark_superseded` need the sync executor.
`record_failure` stays async, on the run connection, after the worker
returns. Every existing async function keeps its signature.

### Technical Decision 4: what ingests, and in what order

A unit is selected when all of these hold:

- its request schema is in `STORED_TIERS`;
- `state = verified` and `fetch_status` is open (`UNKNOWN` or
  `FAILED_RETRYABLE`);
- it is current (`COVERAGE_PREDICATE`: not superseded, not reopened);
- no current unit of a **higher tier** exists for the same shape and day
  (Technical Decision 5);
- the **companion definition unit** for its shape and day is *ingested*.
  The companion has the same `dataset`, `symbols`, `stype_in` and
  `unit_date`, with schema `definition` (224 Technical Decision 5).

A unit that fails the last two conditions is **skipped, not failed**. The
report lists it as "outranked by unit N" or "awaiting definitions". A
definition that has not been projected yet is a state the next acquisition
pass fixes, not a defect.

`--unit-id` narrows the selection. It never overrides a condition: a named
unit that is not selectable is reported with the reason.

Order: `unit_date` ascending, then `unit_id`. Units are independent (each
is its own transaction), so completion order does not change the result.

**The shape must be a product shape.** A unit whose request `stype_in` is
not `parent` fails deterministically ("ledger instrument set is defined
for parent symbology only"). The ledger's instrument set is "every
definition of this product valid in the session" (Technical Decision 6).
That is the instruments a parent request (`ES.FUT`) delivers. A
`raw_symbol` or `continuous` request asks for fewer. Zero-record rows for
contracts it never asked for would then claim sessions complete that were
never bought. The universe uses `parent` only (224 Technical Decision 3),
and `planning_product` already refuses non-rooted symbologies, so no
current unit is affected. A future universe that needs other symbologies
defines its own instrument set.

**Lock.** Ingest holds `TICK_INGEST_LOCK_KEY = 220_000_002`, distinct from
acquisition's `220_000_001`, as 224 Technical Decision 2 requires. Two
ingests serialize. Ingest and acquisition may run at the same time: their
transitions are disjoint (acquisition moves units up to *verified*, ingest
from *verified* to *ingested*), and every transition is compare-and-set.

### Technical Decision 5: a higher tier supersedes a loaded lower tier at ingest

224's planner keys coverage by schema. When 226 sets ES to `tbbo` over a
range that includes 2024-08-30 → 09-29, the pass buys `tbbo` days where
the adopted `trades` days are already loaded. `tbbo` and `trades` share
the natural key, so loading both would conflict on every row. The
architecture names this case, "a tier upgrade over loaded history", as a
supersession. No step writes that link today: 224 writes supersession
only for reopened units, which hold no file.

**Rule.** The tier rank is `TICK_TIER_RANK`, derived from `TICK_TIERS`
order (`trades` < `tbbo`), and it is the only place the order is stated.
For one shape and day, the highest-ranked current tier unit is the one
that loads. In its own transaction (Technical Decision 2, step 1), for
every other current tier unit of that shape and day:

- set `superseded_by_unit_id = N`, compare-and-set on "current";
- if that unit was *ingested*, delete its `tick_trade` rows:
  `WHERE unit_id = O AND ts_event BETWEEN <O's ledger min first_event_ns>
  AND <O's ledger max last_event_ns>`. The bounds come from O's own
  ledger, so chunk exclusion limits the scan to the chunks O touched, with
  no guessed margin. A unit with only zero-record ledger rows has nothing
  to delete.

The superseded unit's ledger rows stay. Every session-total read already
excludes superseded units through the join (222 Technical Decision 7), and
the rows record what was loaded and later replaced.

A lower-tier unit that is outranked is never loaded (Technical Decision
4). If the higher unit fails, the lower one stays *verified* and status
shows the day as failed, because status uses the same "highest-ranked
current unit" rule. A loaded higher tier is never replaced by a lower
one.

The same mechanism covers the architecture's other two cases when they
are built: a provider correction (a same-tier repurchase) and historical
over live. Neither has a producer yet. The realtime initiative adds live
units to the rank (plan Notes: historical replaces live by default).

### Technical Decision 6: resolution, sessions and the ledger's instrument set

**Resolution.** The plan holds the definitions whose validity window
`[activation_ns, expiration_ns]` meets `[first session open, last session
close]` of the unit, with `asset = product`. These are loaded as NumPy
arrays sorted by `(instrument_id, activation_ns)`. A record resolves when
some definition has its `instrument_id` and a window containing its
`ts_event`. This is vectorized: `searchsorted` on the id, then the window
test, and a loop only over the rare ids with more than one window meeting
the day. An unresolved record fails the unit, and the reason names the
first unresolved `instrument_id`, its `ts_event` and the count of
unresolved records. The `asset = product` filter means a record resolving
only to another product's definition also fails. Two products never share
an instrument id within a window, so this catches a shape or symbol
mix-up.

**Sessions.** The plan calls `sessions_between(day 00:00Z, day+1 00:00Z)`
once per unit on the production database, as the plan entry requires. A
production outage therefore stops ingest (`storage_abort`), and nothing is
lost. Per batch:

1. A `ts_event` outside `populated_span()` fails the unit as "outside the
   populated calendar range". `locate_ns` cannot tell that apart from a
   break (221 D7), so this test comes first.
2. `locate_ns` gives each record a session position. `-1` fails the unit
   as "in no session". The reason names the first such `ts_event` in UTC
   and in the calendar's time zone, the neighbouring sessions, and the
   count.

A record whose `ts_event` falls just before 00:00 UTC is still found.
Midnight UTC is inside a CME session, so its session is one the day
touches. On the 2024-09-03 file, no `ts_event` lies outside the file's
day.

**Ledger rows.** For each session S the unit's day touches, a row is
written for every definition with `asset = product` whose window meets
`[S.open, S.close]`: `record_count`, `volume` (sum of `size`),
`first_event_ns`, `last_event_ns`. Instruments with no records in S get a
zero-record row (NULL times), which the table's CHECKs require. A day
typically touches two sessions and holds 41–46 valid instruments, so a
unit writes about 82–92 ledger rows. The ledger's `calendar_id` comes from
`FUTURES_PRODUCT_CALENDAR`, and `session_date` from the session.

**`sequence_ordinal`.** 222 Technical Decision 1 defines it as the number
of earlier records in the file with the same `(instrument_id, ts_event,
sequence)`. It must not depend on contiguity, and it must carry across
batch boundaries. `ingest_records.py` computes ordinals within a batch
with a stable lexsort and run lengths. It then adds each triple's count
from earlier batches, held as a sorted NumPy array of distinct triples
with counts and merged per batch: about 32 bytes per distinct triple,
roughly 16 MB for the largest adopted day. Tests cover a repeat that is
not adjacent and a repeat that straddles a batch boundary.

### Technical Decision 7: ingest does not extend the calendar

221 listed "the ingest pass" as a trigger for `extend_calendar_sessions`.
It is not needed. Every unit's day was produced by `session_days` over the
populated calendar: 224's planner for bought units, and 223's adoption for
adopted ones. Both call `product_session_days(url, product, start, end)`,
which calls `sessions_between(start 00:00Z, end 00:00Z)`. That raises
`OutOfPopulatedRangeError` unless `end 00:00Z` is at or before the last
populated close. Every day `d` it returns is `< end`, so `d+1 00:00Z` is at
or before the last close too, and that is exactly the upper bound of
ingest's own `sessions_between(d 00:00Z, d+1 00:00Z)`. The lower bound
holds by the same argument against the first open. The session that holds
`d`'s evening reopen closes after `d+1 00:00Z`, and it is populated because
the populated span reaches that instant. The review's horizon case (a day
whose evening session is not populated) therefore cannot be emitted:
asking for it raises in the planner or in adoption first.

Sessions are only ever added, so the bound still holds at ingest time. A
unit test pins this: `session_days` over a calendar whose last close is
mid-day X returns no day at or after X, and raises when asked for
`[.., X+1)`. If rows were deleted from `trading_sessions` by hand,
ingest's planning call raises `OutOfPopulatedRangeError`. The unit then
fails with `session_boundary:` naming the day and the populated span
(Technical Decision 8). The remedy is `mt data extend` and then `reset`.

Ingest stays read-only on the production database, and the other triggers
(`mt data extend`, `mt data status`, the daemon idle hook) are unchanged.

### Technical Decision 8: how failures are classified

| Failure | Where | Unit result | Run outcome |
|---|---|---|---|
| Counts: provider ≠ decoded, or decoded ≠ rows with `unit_id = N` | worker | `RETRY_EXHAUSTED`, reason `counts: provider P, decoded D, stored S` | `partial` |
| Resolution: a record resolves to no definition of the product | worker | exhausted, reason names id, time, count | `partial` |
| Session boundary: outside the populated range, or in no session | worker | exhausted, reason names time, sessions, count | `partial` |
| Session boundary: `OutOfPopulatedRangeError` while planning (calendar rows deleted by hand; Technical Decision 7) | plan | exhausted, reason names the day and the populated span | `partial` |
| Overlap: `UniqueViolation` on `COPY` | worker | exhausted, reason names the key and the other current units that day | `partial` |
| Unsupported shape (`stype_in` not `parent`) | plan | exhausted | `partial` |
| Decode error, or the archive file missing or unreadable | worker | exhausted, reason names the path and the error | `partial` |
| Tick database lost (`OperationalError`) | worker or loop | none: no attempt counted | `storage_abort` |
| Tick database hung: a silent partition, or a row lock held past `lock_timeout` (`QueryCanceled`, `LockNotAvailable`; both are `OperationalError`) | worker | none | `storage_abort` |
| Lost compare-and-set: `ManifestTransitionError` from `mark_ingested` or `mark_superseded` (an operator reopened or reset the unit mid-run) | worker | rolled back, no failure recorded; reported as skipped "changed during ingest" | `ok` |
| Any other database error (`DataError`, `CheckViolation`, an `IntegrityError` other than `UniqueViolation`) | worker | rolled back, nothing recorded | propagates: a code defect, traceback, non-zero exit |
| Calendar unreachable (`TickCalendarError`) | plan | none | `storage_abort` |

**No hang.** The worker's connection uses `connect_timeout =
TICK_DB_CONNECT_TIMEOUT_SECONDS` (223's constant), TCP keepalives
(`TICK_DB_KEEPALIVES_IDLE_SECONDS`, `_INTERVAL_SECONDS`, `_COUNT`), and
`lock_timeout = TICK_INGEST_LOCK_TIMEOUT_SECONDS`, all defined in
`constants.py` as starting values. A silent partition breaks the socket
within the keepalive window. A delete or update stuck behind another
session's row lock is cancelled. psycopg raises both as
`OperationalError`, so both land in the `storage_abort` row. No
`statement_timeout` is set: a large day's `COPY` is legitimate work, and
226 sizes units.

**Abort with a worker still running.** The loop does not cancel threads.
When one unit raises, the loop stops starting units and awaits every
in-flight `to_thread` future (`asyncio.gather(..., return_exceptions=True)`).
Each in-flight unit then commits, fails its checks or rolls back. The
timeouts above bound that wait. Only then does the first exception
propagate to `run_phase`. Units that committed during the wait are in the
report.

Every unit failure is deterministic. The file is immutable and
SHA-256-verified, the definitions and calendar are fixed inputs, and
retrying without a change produces the same result. So a failure goes
straight to `RETRY_EXHAUSTED` (`record_failure(deterministic=True)`), and
the operator runs `reset` after fixing the cause (buying definitions,
seeding a calendar exception, restoring the file). A host fault is not a
unit fault, as in 224. The run stops with `storage_abort` after the in-flight
units settle, as described under "Abort with a worker still running".

`IngestCheck` (`counts`, `resolution`, `session_boundary`, `overlap`,
`shape`, `decode`) is the one spelling of check names. Every reason starts
with `<check>:`, so status can group failures by check without parsing
free text.

### Technical Decision 9: session classification for status and coverage

**The sessions in scope.** For a product P with calendar C:

- If the universe entry has a tier and `start`: the sessions of C from
  `start` up to `min(end, the dataset's available end)`.
- Always: every session touched by a current tier unit of P's shape.

Before 226 sets a tier, ES has no wanted range. Status then reports only
what is held, and it says so ("no wanted range: ES has no tier
configured").

**A session's days.** The UTC days the session touches (`session_days`,
one call per status run). **A day's unit** is the highest-ranked current
tier unit of the shape on that day (Technical Decision 5).

**Classification**, worst first. The first rule that matches wins. The
order is one tuple in `tick_status.py`, and the labels are one `StrEnum`,
`TickSessionStatus`.

| # | Status | When |
|---|---|---|
| 1 | `retry_exhausted` | a day's unit is `RETRY_EXHAUSTED` |
| 2 | `failed` | a day's unit is `FAILED_RETRYABLE` |
| 3 | `provider_hole` | a day's unit is `PROVIDER_HOLE` and not reopened, or a day with no unit has condition `missing` |
| 4 | `in_flight` | a day's unit is *requested*, *submitted* or *delivered* |
| 5 | `awaiting_ingest` | a day's unit is *downloaded* or *verified* |
| 6 | `missing` | a day has no unit and its condition is `available` or `degraded` |
| 7 | `pending` | a day has no unit and its condition is `pending` |
| 8 | `edge_unknown` | a day has no unit and no condition row |
| 9 | `complete` | every day's unit is *ingested* |

`awaiting_ingest` is not in the plan entry's list. A *verified* unit is
neither in flight (it is on disk) nor complete (it is not loaded), and
without the label status would have to misreport it as one of those.

A session's **condition** is the worst condition of its days, in the
architecture's order `missing` > `degraded` > `pending` > `available`, or
`unknown` if any day lacks a row. Saturdays are exempt: the provider never
lists them (`CONDITION_ABSENT_WEEKDAY`), and no session touches one.
Status prints it beside the bucket. A `complete` session with a `degraded`
condition is counted as complete and also tallied as "degraded". The
architecture requires degraded to be surfaced, and a degraded day already
bought is kept (224).

**The edge.** Status prints the dataset's available end and how long ago
it was observed (`tick_dataset_edge.observed_at`). 224 said a stale
observation would be reported as "edge unknown". That needs a staleness
threshold, and with manual passes no interval is "normal". Status prints
the observation's age instead, and `edge_unknown` means only that a day
has no condition row. No guessed constant.

**Caught up.** For a product with a wanted range: every session in scope
is `complete`, or `provider_hole` for a day the provider reports
`missing`. The architecture excludes holed sessions from "caught up" as
minute does; status lists them beside the verdict. Without a wanted
range: "n/a".

### Technical Decision 10: status is unit-complete, coverage proves raw counts

The architecture defines an instrument-session as complete when every
unit covering it is complete **and** the raw count equals the sum of its
current units' ledger counts. The raw count is a `count(*)` over
`tick_trade`. Over the full planned universe that means scanning about
1 B rows, which is too slow for a status command.

- **`status`** reads the manifest, the ledger, the conditions and the
  edge. It never scans `tick_trade`. Its `complete` means every covering
  unit is *ingested*. That is the architecture's unit completeness, and
  the counts check proved raw = decoded at the moment each unit
  committed. This is weaker than the architecture's definition, so it is
  in the supersedes list, and every status output (Rich and `--json`,
  as `complete_basis: "units"`) says that `complete` is unit-level and
  points to `coverage` for the raw proof.
- **`coverage --start --end`** adds the raw-count proof for the sessions
  in the range. It runs one grouped count over `tick_trade`, bounded by
  the range's first open and last close in `ts_event`. Each row is
  assigned to its session by a join against the in-scope sessions passed
  as arrays. The count per (instrument, session) is compared with the
  ledger sum over current units. A mismatch marks the session `mismatch`
  and names the instruments and both counts. `--start` and `--end` are
  required: there is no default range to guess, and the scan cost is
  proportional to the range.

Raw rows can change after commit only through supersession (whose
ledger is excluded) or by hand. So a mismatch is evidence of an
out-of-band edit or a defect, and coverage exits 3 when it finds one.

### Technical Decision 11: does this belong in the API?

Yes, but not in this slice. Tick coverage and freshness belong in
`/api/v1/status` and `/api/v1/overview`, under the `/api/v1/futures/*`
namespace (220 Technical Decision 3; 230's scope). This slice prepares for
that by returning `build_status` and `build_coverage` results as frozen
dataclasses with `to_dict()`. The `--json` output is `to_dict()`, so the
CLI and 230 serialize the same object. No endpoint is added here.

### Patterns and Conventions

- `data/tick` never imports `data/kalshi`. The pass contract stays the
  diffed copy.
- Every comparison value is defined once: `IngestCheck`,
  `TickSessionStatus` and its precedence tuple, `TICK_TIER_RANK`, and the
  lock key and worker count in `constants.py`. The tier-rank SQL
  (`array_position(ARRAY[...], schema)`) is rendered from `TICK_TIERS`.
- Blocking work goes through `asyncio.to_thread`, as in 224. The worker is
  a plain synchronous function, so it can later move to a subprocess
  unchanged (220 Technical Decision 6).
- Exception handling follows the project rule. The worker catches
  `UniqueViolation` and the named decode errors specifically, rolls back
  and returns a failure. It catches `ManifestTransitionError`, rolls back
  and returns a "changed during ingest" skip. `OperationalError` propagates to `run_phase`,
  which logs it and maps it to `storage_abort`. There is no bare
  `except Exception`.

## Implementation Details

### API Contracts

**`mt data tick ingest [--unit-id N]... [--json]`**

```
$ mt data tick ingest
ingest  run 3f2c…  2 workers
  unit <id>  <day> <tier>  <n> rec  → ingested  (<s> s)
  …
  skipped: 0 awaiting definitions, 0 outranked, 0 changed during ingest
  ingested <n> units, <n> records; failed <n>
outcome ok  (exit 0)
```

The `--json` payload is `PassResult.to_dict()` with one phase, `ingest`,
plus `exit_code`. The phase summary has:

- `ingested`, `failed` and `records`;
- `skipped`, broken down into `awaiting_definitions`, `outranked` and
  `changed_during_ingest`;
- `superseded`, the ids of units this run replaced;
- `units`, one entry per unit: `unit_id`, `unit_date`, `schema`,
  `outcome`, `records`, `reason`.

Exit codes: 0 ok (skips included), 1 preflight, 3 partial, 4 storage.

**`mt data tick status [--product P] [--all-instruments] [--json]`**

```
$ mt data tick status
ES  (CME_EQUITY, GLBX.MDP3, ES.FUT parent)   tier: not configured — no wanted range
  edge: available to <UTC time>, observed <age> ago
  sessions held: <n>
    complete <n>   awaiting ingest <n>   in flight <n>   pending <n>   missing <n>
    failed <n>   retry exhausted <n>   provider hole <n>   edge unknown <n>   (degraded <n>)
  caught up: n/a (no wanted range)
  complete = every unit ingested; raw-count proof: mt data tick coverage
  contracts (outrights; 20 spreads hidden, --all-instruments to list):
    <raw_symbol>   exp <date>   sessions <n>   records <n>   volume <n>   <first session> → <last session>
```

A job's first session is honestly incomplete. The trades job starts at 2024-08-30 00:00 UTC, but
the session dated 08-30 opened on 08-29 at 22:00 UTC, so that session's
first day is not held. The same applies to the `tbbo` job. Such a
session shows `missing` when the availability phase recorded a condition
for the unheld day, and `edge_unknown` when it did not.

The per-contract lines come from the current units' ledger rows joined to
`tick_definition` (`raw_symbol`, `instrument_class`, `expiration_ns`).
Sessions are counted where the contract had at least one record. Exit: 0,
or 1 on preflight, or 4 if the tick or production database is
unreachable.

Reading sessions from production is a coupling edge the architecture did
not list. Its "Operational state has one home" principle names three tick →
production edges, and this is a fourth, recorded in the supersedes list.
A production outage fails `status` with exit 4 instead of degrading it.
Classifying sessions is the whole job of the command. A tally of units and
ledger rows with no sessions would look like an answer and hide the gaps
status exists to show, so it is not offered.

**`mt data tick coverage --start D --end D [--product P] [--json]`**

```
$ mt data tick coverage --start 2024-09-18 --end 2024-09-21
ES  CME_EQUITY
  session     condition  status    units    ledger     raw        check
  2024-09-18  available  complete  <ids>    <n>        <n>        ok
  2024-09-19  …
```

Exit: 0, 3 if any session shows `mismatch`, and 1 or 4 as for status.

**Library (for 230):**

```python
def build_status(tick_conn, calendar_url, universe, now) -> TickStatus: ...        # async
def build_coverage(tick_conn, calendar_url, product, start, end) -> TickCoverage: ...  # async
```

Both return frozen dataclasses with `to_dict()`.

### Database / Storage Schema

No migration. The tables exist from 222 and 223. What changes is use:

- `tick_trade` is written for the first time. `unit_id` has no index.
  The delete in Technical Decision 5 and the counts check read it
  bounded by `ts_event`, which limits the scan to one or two 7-day
  chunks.
- `tick_ingest_ledger` is written for the first time.
- `tick_app` needs `INSERT` and `DELETE` on `tick_trade`, `INSERT` on
  `tick_ingest_ledger`, and `UPDATE` on `tick_archive_unit`. 222 listed
  the write surface in `scripts/provision_tick_roles.sql`. The task file
  verifies each grant there before code depends on it. The `DELETE` on
  `tick_trade` is the one grant 222 may not have anticipated, since until
  now only supersession needed it.

## Integration Points

### Provides to Other Slices

- **226 (proof):** a working ingest to measure, covering:
  - rate;
  - decode versus write time per unit, from the per-unit durations in the
    ingest report;
  - worker count;
  - the batch budget;
  - bytes per row, from the loaded table.

  It also gets the counts, resolution and session checks passing on every
  adopted unit, and `coverage` to prove raw counts on the whole proof
  range. `TICK_INGEST_WORKERS` and the `write_row` path are what 226 may
  replace. 226 must also re-verify supersession's time-bounded `DELETE`
  and overlap detection by `UniqueViolation` under the compression
  policy it chooses. Both are designed and tested on uncompressed chunks,
  and TimescaleDB handles deletes and unique constraints differently on
  compressed chunks.
- **227 (backup):** the rebuild-from-archive path is `adopt` plus
  `pass` (definitions) plus `ingest`, so the measured rebuild cost is one
  ingest run.
- **228 (roll methods):** per-contract, per-session `volume` in the ledger
  (the volume rule's input), and status's per-contract section to extend
  with the active contract and next roll.
- **229 (operator surface):** status and coverage, to extend with `get`,
  `debug` and `backfill`.
- **230 (API):** `build_status` and `build_coverage` with `to_dict()`.
- **231 (GC):** nothing product-specific. A second product works through
  `FUTURES_PRODUCT_CALENDAR`, `asset = product` and the universe.
- **233 (production wiring):** `mt data tick ingest` as a pass on the pass
  contract, ready for a `PassKind` and a timer. 233 owns the per-batch
  progress heartbeat that 225 omits (Data Flow), because its systemd
  timeouts are what the heartbeat serves.

### Consumes from Other Slices

- **221:** a production database outage becomes `TickCalendarError`
  during planning, so the run ends with `storage_abort` and nothing is
  lost. A calendar defect shows up as named `session_boundary` failures,
  never as silently mis-assigned rows. 221 notes the fix: an additive
  migration, then `reset` on the affected units.
- **222:** the key and ordinal definition, the ledger CHECKs (which
  enforce zero-record rows at the server) and the current-unit predicate.
- **224:**
  - `provider_record_count` on each *verified* unit;
  - definitions projected before tier units become selectable;
  - the compare-and-set transitions;
  - `reset`;
  - the pass contract;
  - `run_phase`;
  - `tick_pass_render`.

## Success Criteria

### Functional Requirements

1. `mt data tick ingest` on a database holding the two adopted jobs and
   their definitions loads all 78 tier units (26 `trades`, 52 `tbbo`).
   Every unit ends *ingested* with `decoded_record_count =
   provider_record_count`, and `SELECT count(*) FROM tick_trade` equals
   the sum of the jobs' record counts. The adoption reports and the two
   jobs' records give 10,049,172 for `trades`; the task file records the
   `tbbo` total from the manifest before the run.
2. Re-running ingest selects nothing and exits 0. The row count is
   unchanged.
3. For every ingested unit, the ledger has a row for every
   `asset = 'ES'` definition valid in each session the day touches,
   including zero-record rows. The ledger's `record_count` sums to the
   unit's `decoded_record_count`.
4. A tier unit whose companion definition unit is not *ingested* is
   skipped as "awaiting definitions". It is not failed, and it ingests on
   the next run after the definitions are projected.
5. Each check fails its unit with the check named, leaves no `tick_trade`
   or ledger rows for that unit, and exits 3:
   - a unit whose file decodes fewer records than `provider_record_count`
     (counts);
   - a record with an unknown instrument id (resolution);
   - a record in the daily break (session boundary);
   - a record outside the populated calendar (session boundary);
   - a second current unit overlapping loaded rows (overlap).

   `reset` then reopens it, and it ingests once the cause is fixed.
6. A `tbbo` unit for a day whose `trades` unit is *ingested* supersedes
   it in one transaction. Afterwards the `trades` rows for that day are
   gone, the `tbbo` rows are present, the `trades` unit has
   `superseded_by_unit_id` set, and the day's session totals equal the
   `tbbo` ledger. A `trades` unit is never loaded over a current `tbbo`
   unit; it is reported as "outranked".
7. A tick database lost mid-run ends the run with `storage_abort` (exit 4)
   and counts no attempts. Units already committed stay *ingested*; the
   unit in progress leaves nothing. A row lock held by another session
   past `TICK_INGEST_LOCK_TIMEOUT_SECONDS` ends the same way instead of
   hanging. A unit reopened by another session mid-ingest rolls back and
   is reported as skipped "changed during ingest", with no failure
   recorded. When one worker aborts, the loop waits for the other unit
   to settle before the run ends.
8. `sequence_ordinal` matches 222's definition on a fixture with a
   non-adjacent repeat and a repeat across a batch boundary. Re-ingesting
   the same file in a fresh database produces identical rows.
9. `mt data tick status` shows every session in scope in exactly one
   bucket of Technical Decision 9, by the stated precedence. A test covers
   each bucket, the worst-condition rule across a session's two days, the
   Saturday exemption, and the degraded tally. It shows the per-contract
   lines, with spreads hidden unless `--all-instruments` is given.
10. `mt data tick coverage` reports `ok` on every proof-range session.
    After one `tick_trade` row is deleted by hand in a test database, it
    reports `mismatch` for that session, naming the instrument, and exits
    3.
11. `status` and `coverage` run with `MT_DATABENTO_API_KEY` unset and no
    `MT_TICK_ARCHIVE_DIR`. `ingest` runs with the key unset.

### Technical Requirements

- The unit tests for `ingest_records.py` run on real DBN records: the
  220 sample files, plus small real slices cut from the adopted `trades`
  and `tbbo` files as fixtures. That covers resolution, locate, ordinals,
  ledger accumulation and row building. Synthetic arrays are used only for
  the failure shapes real files cannot produce (a record in the break, an
  unknown id), and they are built by editing a real batch.
- Integration tests on a test-cluster tick database cover each
  functional requirement from 3 to 8. At least one runs a real adopted day
  file end to end.
- The pass-contract parity test is updated for the generic
  `TickPass[RunT]` divergence and passes.
- A unit test asserts `TICK_TIER_RANK` is derived from `TICK_TIERS` and
  that the SQL rank rendering matches it.
- mypy is clean on touched files (with the kalshi-support path run as the
  memory notes). `ruff format` is scoped to touched files.
- Documentation:
  - the README futures tick section adds `ingest`, `status` and
    `coverage`;
  - CHANGELOG under [Unreleased];
  - the data-correctness contract updates:
    - the I10 row adds the three verbs;
    - the I11 row defines the checks and completeness grains, with status
      unit-complete and coverage raw-proved;
    - the I12 row adds supersession at ingest by tier rank;
    - the I13 row records the check as shipped.

### Integration Requirements

- 226 can run `adopt` → `pass` (definitions only, $0 when already bought)
  → `ingest` → `coverage` over the proof range and take every
  measurement from the pass report and the database.
- Acquisition and ingest run at the same time without interfering.
  Separate locks and disjoint compare-and-set transitions make that true,
  and an integration test runs both concurrently against one scratch
  database.
- Minute, daily and Kalshi commands are untouched. No production-database
  write is added.

### Verification Walkthrough

These steps run on manta9000 against a scratch tick database on the test
cluster, as in 224. Export `MT_TIMESCALE_TEST_URL` from `.env` first, with
the quotes stripped. `/data/tick-archive` already holds the two adopted
jobs and the four definition jobs `GLBX-20260930-*`, so nothing is bought.

1. **Test suites.** Unit and integration are separate invocations.

   ```bash
   uv run --extra dev pytest test/unit/data/tick test/unit/cli/commands/test_data_tick.py -q
   uv run --extra dev pytest test/integration/data -k tick -q
   ```

   Expected: all pass. The full tiers show no failure beyond the baseline
   taken on the slice branch before any change.

2. **Scratch database and manifest from the archive.**

   ```bash
   createdb --maintenance-db="$MT_TIMESCALE_TEST_URL" mt_scratch_tick_225
   export MT_TICK_DB_URL="${MT_TIMESCALE_TEST_URL%/*}/mt_scratch_tick_225"
   export MT_TICK_MAINTENANCE_URL="$MT_TICK_DB_URL"
   export MT_TICK_ARCHIVE_DIR=/data/tick-archive
   uv run mt data init --database tick
   for job in /data/tick-archive/GLBX-*; do
     uv run mt data tick adopt --job-id "$(basename "$job")" --source "$job"
   done
   uv run mt data tick pass     # definitions phase projects the four definition jobs; nothing to buy
   ```

   Expected: six adoptions exit 0, and the pass exits 0 with the
   definition units *ingested* and no purchase. `adopt` reads each job's
   record (a free call) and needs the API key; nothing after this step
   does.

3. **Status before ingest.**

   ```bash
   env -u MT_DATABENTO_API_KEY uv run mt data tick status
   ```

   Expected: runs without the key. Every held session is
   `awaiting_ingest`, except the job-boundary sessions, which show
   `missing` or `edge_unknown` (see the status example).
   Caught up is "n/a (no wanted range)".

4. **Ingest.**

   ```bash
   env -u MT_DATABENTO_API_KEY uv run mt data tick ingest; echo "exit $?"
   psql "$MT_TICK_DB_URL" -c "SELECT r.schema, count(*) units, sum(u.provider_record_count) provider,
       sum(u.decoded_record_count) decoded
     FROM tick_archive_unit u JOIN tick_request r USING (request_id)
     WHERE r.schema IN ('trades','tbbo') GROUP BY 1"
   psql "$MT_TICK_DB_URL" -c "SELECT count(*) FROM tick_trade"
   ```

   Expected: exit 0, 78 units ingested and none failed. `provider =
   decoded` per schema, and the `tick_trade` count equals their sum. Record
   the wall time and the per-unit durations in the task file; 226 starts
   from them. Check both of the architecture's throughput targets
   (Technical Decision 2):
   - every per-unit duration is far below 24 hours, the market time one
     unit covers;
   - the whole run fits inside an operator's working session. The run
     covers about three and a half months of sessions (26 `trades` days
     plus 52 `tbbo` days), so this meets the one-month target with margin.

   A miss on either target fails the walkthrough.

5. **Idempotence.** Run the ingest again. Expected: nothing selected,
   exit 0, row count unchanged.

6. **Status and coverage after ingest.**

   ```bash
   uv run mt data tick status
   uv run mt data tick status --all-instruments --json | jq '.products[0].contracts | length'
   uv run mt data tick coverage --start 2024-09-15 --end 2024-09-22   # over the September roll
   uv run mt data tick coverage --start 2024-12-12 --end 2024-12-21   # over the December roll
   ```

   Expected: sessions are `complete` except the job-boundary sessions.
   Each is listed and explained. Contracts include ESU4, ESZ4 and ESH5,
   with the roll visible as ESU4's last session and ESZ4's volume
   overtaking it. Every coverage line reads `ok`, exit 0.

7. **A check failing loudly.** Every check failure is covered by
   integration tests. The one to watch by hand is the raw-count proof:

   ```bash
   psql "$MT_TICK_DB_URL" -c "DELETE FROM tick_trade WHERE
     (instrument_id, ts_event, sequence, sequence_ordinal) =
     (SELECT instrument_id, ts_event, sequence, sequence_ordinal FROM tick_trade
      WHERE ts_event BETWEEN
        extract(epoch FROM timestamptz '2024-09-18 14:00Z')::bigint*1000000000
    AND extract(epoch FROM timestamptz '2024-09-18 15:00Z')::bigint*1000000000 LIMIT 1)"
   uv run mt data tick coverage --start 2024-09-18 --end 2024-09-19; echo "exit $?"
   ```

   Delete by the primary key, not `ctid`: `ctid` is unique only within a
   chunk, so `WHERE ctid IN (… LIMIT 1)` deletes that position in every
   chunk (at implementation it deleted 9 rows, and coverage over the whole
   range named all 9).

   Expected: the 2024-09-18 session shows `mismatch`, naming the
   instrument, ledger 1 more than raw, exit 3. This scratch database is
   dropped in step 9, so the edit is never repaired.

8. **Tier supersession on real data.** The integration test for
   functional requirement 6 runs on a fixture built from a real `trades`
   and `tbbo` slice of the same day. It covers the tier upgrade because
   the adopted jobs do not overlap. Point to the test's name and its
   passing output in the task file.

9. **Tear down.**

   ```bash
   dropdb --maintenance-db="$MT_TIMESCALE_TEST_URL" mt_scratch_tick_225
   ```

   The archive stays. It is the source of truth, and 226 rebuilds from it.

**Results, 2026-09-30** (manta9000, test cluster, scratch database
`mt_scratch_tick_225`, dropped afterwards; nothing bought):

- **Step 1.** Unit tier clean (4,233 passed). Integration: the 6 baseline
  failures only, after fixing 4 new ones caused by 225's own CLI tests
  (they left logging bound to a closed CliRunner stream; they now patch
  `setup_logging` like every other CLI test).
- **Step 2.** Six adoptions; `pass --estimate-only` planned $0.0000
  (`nothing_to_buy`), and the real pass exited 0 with nothing bought. The
  manifest: 26 `trades` units, provider sum **10,049,172** (as expected);
  52 `tbbo` units, provider sum **17,642,240**; 78 definition units
  *ingested*.
- **Step 3.** Status ran without the key: 64 sessions held, all
  `awaiting_ingest`; caught up n/a. The job-boundary sessions do not show
  `missing` or `edge_unknown` before ingest, because their held day is
  `awaiting_ingest`, which ranks worse (TD9's precedence). They surface
  after ingest (step 6).
- **Step 4.** Exit 0; 78 ingested, 0 failed. Provider = decoded per schema
  (`trades` 10,049,172; `tbbo` 17,642,240); `tick_trade` holds
  **27,691,412** rows, their sum. Whole run **55 s** wall (54.3 s in the
  pass). Per unit 0.04–3.98 s (sum 106 s over two workers); decode at most
  0.52 s, write at most 3.76 s. Both targets met: the slowest unit is
  1/21,700 of the day it covers (bound 120 s), and 3.5 months of sessions
  took under a minute (bound 2 h).
- **Step 5.** Second run: nothing selected, exit 0, 27,691,412 rows.
- **Step 6.** 61 sessions `complete`, 3 not, each a job boundary:
  - 2024-08-30 `edge_unknown`: its first day, 08-29, is before the trades
    job and has no condition row;
  - 2024-09-30 `missing`: its second day, 09-30, is after the trades job;
  - 2024-11-01 `missing`: its first day, 10-31, is before the tbbo job.

  Contracts ESU4, ESZ4 and ESH5 are present (18 instruments with records,
  12 of them spreads); ESU4's last session is 2024-09-20, its expiry, with
  ESZ4's volume (52.2 M) far above it. Both coverage ranges read `ok` on
  every session (ledger = raw), exit 0.
- **Step 7.** The session 2024-09-18 showed `mismatch` naming instrument
  183748 (ESZ4), ledger 573,847 against raw 573,846, exit 3. The original
  `ctid` delete removed 9 rows, one per chunk; coverage over
  2024-08-30 → 2025-01-01 named all 9, each in its own session.
- **Step 8.** `test_tick_ingest_supersession.py::test_tbbo_replaces_an_ingested_trades_unit_in_one_transaction`
  PASSED.
- **Step 9.** Dropped; `pg_database` count 0. The archive is unchanged.

## Risk Assessment

### Technical Risks

- **Row-building speed under the interpreter lock.** `write_row` is
  per-record Python, and two workers contend for the lock while building
  rows. If one unit's write time is well above its decode time, adding
  workers does not help.
- **The definitions scope assumption.** The ledger's instrument set
  assumes every `asset = 'ES'` definition was delivered because the
  request was `ES.FUT` parent. 224's design-time check found the spreads
  in both the definitions and the trades. A parent request whose
  definitions omit an instrument that trades would fail resolution. That
  failure is loud and not silent, but it would block a unit.

### Mitigation Strategies

- Step 4 of the walkthrough records the per-unit decode and write
  durations, so 226 starts with the split. The row builder is one
  function, and replacing it with a NumPy binary `COPY` encoder changes
  nothing else.
- A resolution failure names the instrument id. The remedy is buying
  that day's definitions again under the same shape, which 224's
  companion-want logic already does after a `reset`. No silent fallback
  to loading unresolved rows exists.

## Implementation Notes

### Development Approach

Suggested order, each step leaving the suite green:

1. **Refactors with no behavior change:**
   - split the store context from the run context;
   - make the pass contract generic, with the parity test updated;
   - split each transition in `manifest_repo.py` into a statement and
     its executors.

   All 224 tests must still pass unchanged.
2. **`ingest_records.py`:** pure functions over real batches: resolution,
   session location, ordinals with the cross-batch carry, ledger
   accumulation and row building. This is the bulk of the unit tests.
3. **Selection and plan:** `ingest_select.py` and `ingest_plan.py`,
   including the tier rank and the definition gate. Integration tests use
   manifest fixtures.
4. **`ingest_worker.py`:** the single transaction, the three checks, the
   overlap handling and supersession. Integration tests on the test
   cluster cover each failure and the tier upgrade.
5. **`ingest_pass.py` and `mt data tick ingest`:** the worker pool,
   failure recording, the report and exit codes. Include the concurrent
   acquisition-and-ingest test.
6. **`tick_status.py`, `status_reads.py`, and `status` and `coverage`:**
   the classification table first, as a pure function with one test per
   bucket, then the reads and rendering.
7. **Docs and contract rows, then the walkthrough.**

Standing tasks for every 220 slice, named in the task file:

- diff the tick pass contract against the Kalshi original, now including
  the generic divergence;
- run the realtime paths check (below);
- add the slice's rows to the data-correctness contract;
- answer "does this belong in the API?" (Technical Decision 11).

Commit at each numbered step.

### Special Considerations

- **Realtime paths.** Nothing here rules out assembling from realtime
  (path A) or historical with delay (path B):
  - the unit transaction is bounded by the unit, and a captured live
    segment is also a bounded unit;
  - supersession by rank at ingest is where historical-over-live plugs
    in;
  - the ledger stays per unit.

  One constraint is recorded for the realtime initiative. The ledger's
  instrument set rule (every definition valid in the session) assumes
  parent-shaped units, and a live segment for a subset of contracts needs
  its own rule (Technical Decision 4).
- **Production safety.** Ingest writes only to the tick database and
  reads sessions from production. No destructive statement touches the
  production database. The walkthrough's hand `DELETE` targets a scratch
  database created in step 2 and dropped in step 9.
- **Memory.** Per worker: one decoded batch (≤ 32 MiB), the ordinal carry
  (≈ 16 MB on the largest adopted day), and the ledger accumulator
  (≈ 82–92 entries). Two workers stay under about 150 MB.

### Architecture and plan statements this design supersedes

1. **Architecture, "Ingest pass":** "a pass that dies mid-unit leaves raw
   rows the next attempt's natural-key conflict-ignore skips". One
   transaction per unit leaves nothing, so there is no conflict-ignore
   (Technical Decision 2).
2. **221, forward extension:** "from 225, the ingest pass" calls
   `extend_calendar_sessions`. Ingest does not extend; every unit's day is
   already inside the populated span (Technical Decision 7).
3. **224, availability table note:** "225 reports a stale observation as
   'edge unknown'". Status prints the observation's age instead, and
   `edge_unknown` means a day with no condition row (Technical Decision
   9).
4. **Plan entry's status list:** it gains `awaiting_ingest` for units
   *downloaded* or *verified* but not loaded (Technical Decision 9).
5. **Architecture, supersession:** it names tier upgrades as a
   supersession but assigns no step to write the link. Ingest writes it,
   by tier rank, in the loading transaction (Technical Decision 5).
6. **Architecture, completeness:** "complete" needs every covering unit
   complete **and** raw count = ledger sum. `status` reports unit-level
   completeness only, labelled as such. `coverage` does the raw-count
   proof over a bounded range (Technical Decision 10).
7. **Architecture, "Operational state has one home":** it lists three
   tick → production edges. `status` and `coverage` reading sessions from
   the production calendar is a fourth. A production outage fails them
   with exit 4. Flag this for the architecture's Revision Log, as 224 did
   (API Contracts, status).
8. **Architecture and plan, "only progress returns to the loop":** 225
   returns one outcome per unit and no per-batch progress. The heartbeat
   is handed to 233 (Data Flow).
9. **Architecture, failure states:** a failed attempt is
   `FAILED_RETRYABLE` until an attempt limit makes it `RETRY_EXHAUSTED`.
   Every ingest failure is deterministic, so it goes straight to
   `RETRY_EXHAUSTED` and the unit stays *verified*. That matches 224's
   fetch-status model (Technical Decision 8).
