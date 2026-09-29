---
docType: slice-design
slice: historical-acquisition-pass
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [923, 220, 221, 222, 223]
interfaces: [225, 226, 227, 228, 229, 231, 233]
dateCreated: 20260928
dateUpdated: 20260928
status: not_started
review: none
---

# Slice Design: historical-acquisition-pass

## Overview

Slices 223 and 224 build the tick tier's acquisition side (see Slice Split
below). It is the first code
that writes the manifest 222 created and the first code that can spend
money. It delivers three operator verbs under `mt data tick`:

- **`pass`**, the acquisition pass. It runs five phases on the
  Kalshi pass contract:
  1. reconcile in-flight units;
  2. capture the availability edge;
  3. plan, estimate, guard and submit purchases;
  4. await delivery within a wait budget;
  5. project definition units into `tick_definition`.
  
  Every purchase is a batch job. Nothing is bought unless both spend
  ceilings are set and neither would be exceeded.
- **`adopt`**, which takes a batch job already on disk (a directory or
  the provider's zip), verifies every file against the job's
  `manifest.json`, copies it into the archive, and records it as a
  purchase. The two free-credit ES jobs enter the manifest this way.
- **`reset`**, the manual reopen for exhausted and holed units, at minute
  parity.

It also adds:

- the configured tick universe;
- the run-context preflight (it refuses to run while tick migrations are
  pending);
- one tick-track migration for availability metadata and reopened units;
- the 30-day spend setting and the archive-directory setting;
- enrolment of the archive directory in the nightly restic backup.

No schedule code is built. Every run is manual.

## Slice Split

On 2026-09-28, at task breakdown, the PM split this design into two slices
so each fits one implementation session. This document remains the design
of record for both. Reviews of it, and of its first task files, were written
under the number 223.

| Built by | Scope in this document |
|---|---|
| **223 Tick Archive Adoption** (`223-slice.tick-archive-adoption.md`) | Settings; the constants TD2, TD8 and TD11 use; migration `tick_006` and its grants; `session_days` (TD4, Days); the run context, preflight and lock (TD2); `manifest_repo.py`'s compare-and-set transitions for adoption, verification, failure and reset; `verify.py` (TD9, Verification); adoption (TD10); `reset` (TD8, reset); archive backup enrolment (TD11). FR1, FR2 and the reset part of FR6. Walkthrough steps 1–4, 8 and 9. |
| **224 Historical Acquisition Pass** (this slice) | The pass contract (TD1); the universe (TD3); planning and grouping (TD4); definitions (TD5); the calendar placement in the pass (TD6); the spend guard (TD7); reopening and supersession by the pass (TD8); submit, reconcile, delivery and await (TD9); `mt data tick pass`. FR3–FR5, FR6 (except reset), FR7–FR10. Walkthrough steps 5–7 and 10. |

Where the sections below say "this slice", read the table above for which
of the two builds it.

## Design-Time Verification Findings

Measured on 2026-09-28 against the adopted files and the provider's free
endpoints, through the project's own adapter
(`DatabentoTickProvider.from_settings`). No billable call was made.

| Item | Finding | Consequence |
|---|---|---|
| Old job records | `batch_job` still answers for both free-credit jobs, although both are `expired`. `GLBX-20240930-USM7UXXJBA`: `trades`, `ES.FUT` (`parent`), 2024-08-30 → 2024-09-30, 10,049,172 records, $12.5785. `GLBX-20250123-XT4GD5UM6C`: `tbbo`, `ES.FUT` (`parent`), 2024-11-01 → 2025-01-01, 17,642,240 records, $36.8046. | Adoption reads the job's cost, record count and request from the provider for free. It never parses them out of local JSON. |
| Day-file header | The DBN header of a day file spans exactly that UTC day. For example, `glbx-mdp3-20240903.trades.dbn.zst` has start `2024-09-03T00:00Z` and end `2024-09-04T00:00Z`. | A file is matched to its unit by its header, not by parsing the file name. |
| Per-day record count | The free `record_count` for `[day, day+1)` with the job's symbols equals the file's decoded count on all three days checked: 3,191 (Sunday 09-01), 511,965 (09-03) and 6,985 (09-29). | Verification records each unit's provider count. 225's count check can then compare per unit. |
| Spreads in the parent symbol | `ES.FUT` covers the outrights and the calendar spreads. The job's `symbology.json` maps `ESH6-ESU6` → 42004904. The trades contain spread trades. | Definitions must be bought with the same symbols as the tier data, or 225's resolution check fails on the spread trades (Technical Decision 5). |
| Spread share | Across all 26 files of the `trades` job, 236,017 of 10,049,172 records (2.35%) are spread trades, on 7 spread and 5 outright instruments. Every record's `instrument_id` appears in its file header's mappings. | Including spreads is an explicit configuration decision with a stated cost (Technical Decision 3). The header-mapping premise held on this job, which is early evidence for 226's mapping-completeness criterion. |
| Definition cost | With `ES.FUT` parent there are 61 definition records per day (each instrument is sent again every day). The job-1 range is 1,694 records, 880,880 billable bytes, $0.0014. The job-2 range is 3,280 records, 1,705,600 bytes, $0.0027. | The proof's definition purchase is about $0.004 across four monthly requests. |
| Job files | Each job holds one `.dbn.zst` file per UTC day that has data, plus `condition.json`, `metadata.json`, `symbology.json` and `manifest.json`. `manifest.json` lists the other files (`filename`, `size`, `hash` as `sha256:<hex>`) but not itself. The `tbbo` job exists only as its zip, and the zip contains `manifest.json`. | Adoption reads either a directory or a zip, and verifies against `manifest.json` in both cases. |
| Existing final file | `_download.download_file` re-verifies a file that already has its final name and returns it. It refuses to touch one that does not match. | A crash between the download and the database update costs one re-hash, not a re-download. |
| Archive location | `/data` is `manta:manta 0755` and is not in any backup: restic covers `/etc /root /var/spool/cron/crontabs /home/manta` with `--one-file-system`, and `/data` is a separate device. The root cron runs `scripts/cron_system_backup.sh` directly from this checkout. | Enrolment is an edit to the include list in this repository. No root is needed to create the directory (Technical Decision 11). |

## Value

- **The tick tier gets data it can use.** After this slice the two
  free-credit jobs are archived and recorded as purchases. The
  definitions that make their ticks attributable can be bought for about
  $0.004 through a guarded path. 225 needs both before it can ingest a
  single tick.
- **Money is safe by construction.** A request is recorded before it is
  submitted, so an unknown submit outcome is reconciled rather than bought
  again. Both ceilings are checked before every submit, from the manifest.
  Deliveries are downloaded earliest deadline first, before anything new
  is bought. A second concurrent run is refused, not raced.
- **The archive is backed up from the first byte.** Losing it would mean
  buying the data again, or losing it for good if it is no longer sold.
- **Acquisition state has minute parity.** Attempts, retryable failures,
  exhausted units, provider holes and a manual reset all work without a
  schedule.

## Technical Scope

**Included**

1. The tick pass contract: a copy of the Kalshi phase, report and result
   shapes in the tick package, plus a unit test that diffs its fields
   against the Kalshi original (Technical Decision 1).
2. The run context and its preflight. It checks the API key, the tick
   database URL, the archive directory and pending tick migrations, and
   takes an advisory lock (Technical Decision 2).
3. The configured tick universe, `data/tick/universe.py` (Technical
   Decision 3).
4. The acquisition pass: the five phases and the pure planning and guard
   cores behind them (Technical Decisions 4–9).
5. `mt data tick adopt` for a job directory or zip (Technical Decision 10).
6. `mt data tick reset` (Technical Decision 8).
7. Definition projection into `tick_definition`, with the validity-window
   and changed-definition rules (Technical Decision 5).
8. Migration `tick_006_availability` (Database / Storage Schema), and its
   two tables added to the write surface of
   `scripts/provision_tick_roles.sql`.
9. Settings: `MT_TICK_SPEND_30D_CEILING_USD` and `MT_TICK_ARCHIVE_DIR`.
10. Archive backup enrolment (Technical Decision 11):
    - the include list in `scripts/cron_system_backup.sh`;
    - a `.partial` exclusion in `deploy/restic-excludes.txt`;
    - `scripts/verify_tick_archive_backup.sh`, a root script that backs up,
      restores one archived file, and compares hashes;
    - runbook 200's include set.
11. Documentation:
    - the data-correctness contract rows for 223 and 224;
    - the slice plan Notes (statements this design supersedes);
    - the README's futures tick section and environment table;
    - `.env_sample`;
    - the CHANGELOG.

**Excluded**

- **Ingesting tier data.** Decode, `COPY`, the ledger, and the count,
  resolution and session checks are 225's. After this slice, tier units
  rest at *verified*.
- **`mt data tick status`, `coverage`, `debug` and `get`.** Status and
  coverage are 225's, and the rest are 229's. 224's operator output is the
  pass report. Its walkthrough reads the manifest with `psql`.
- **Schedule code.** There is no `PassKind.TICK`, no `schedule_for`
  branch, no timer and no `mt-run tick`; all of these are 233's. A manual
  run therefore writes no `pass_runs` row, and its record is the manifest.
- **Direct-range purchases.** No pass calls `fetch_range` (PM direction:
  batch jobs only).
- **Degraded-day repurchase.** A day bought while `degraded` is kept. If
  the provider later improves it, repurchasing is an operator decision,
  not an automatic one (see Risk Assessment).
- **Creating the production tick database.** No slice in the plan
  provisions the second cluster the PM chose on 2026-09-28. This slice
  runs against a scratch tick database, which is enough because the
  archive can rebuild the manifest (Dependencies).
- **Choosing the tier.** The universe's ES entry ships with no tier. The
  choice is 226's go/no-go.

## Dependencies

### Prerequisites

- **222 Tick Storage Track (complete).** `tick_request`,
  `tick_archive_unit` and `tick_definition`, the `UnitState` order,
  `UNIT_STATES_WITH_FILE`, `TICK_DEFINITION_COLUMNS`, and the
  `migrated_tick_db` and `provisioned_tick_db` fixtures with the row
  helpers in `test/tick_support/rows.py`.
- **220 Databento Adapter (complete).** From it this slice uses:
  - `ITickMetadataProvider` and `ITickAcquisitionProvider`;
  - `ProviderOutcomeUnknownError`;
  - the verified download and its file-name rule;
  - `DbnFileReader`;
  - `TickRequest` (UTC dates, end exclusive);
  - `BatchJob` and `BatchJobState`;
  - `DatasetCondition` and `DayCondition`;
  - `Settings.tick_spend_ceiling_usd`.

  An unknown job state already raises `ProviderPermanentError` from
  `batch_job`, and this slice maps that refusal onto the unit.
- **221 CME Session Model (complete).** `FUTURES_PRODUCT_CALENDAR`,
  `calendar_for_product`, and `TradingCalendar.sessions_between`, which
  raises `OutOfPopulatedRangeError` outside the populated span. The CME
  span starts 2020-01-01.
- **923 (complete).** The routed `tick` track, `resolve_database_url(…,
  Database.TICK, Credential.APPLICATION)`, `list_migration_state`, and the
  grant artifact.
- **The production database (read only).** The CME calendar lives there,
  under the architecture's carve-out. The pass needs it only for planning
  and adoption (Technical Decision 6).
- No new Python packages. `zipfile` and `hashlib` are in the standard
  library.

**The PM's one decision.** The only live purchase in this slice's
walkthrough is the definitions for the two adopted jobs, about $0.004. It
happens only after the PM sets `MT_TICK_SPEND_CEILING_USD` and
`MT_TICK_SPEND_30D_CEILING_USD` in the dev `.env`. Without them the
walkthrough stops at the pass's refusal (exit 5), and the purchase moves
to 226. No host step needs the PM.

**Gap in the plan: the production tick cluster.** The PM decided on
2026-09-28 that the tick database is a second cluster on the production
host. No slice in the plan creates it. 227 extends the backup to it, and
233 writes the service environment. This slice does not need it, for two
reasons:

- The archive is the record. Re-running `adopt` on every job directory
  under `MT_TICK_ARCHIVE_DIR` rebuilds the manifest, including costs and
  commit times, which come from the provider's job records. Technical
  Decision 10 covers this.
- The walkthrough's manifest lives in a scratch database, and nothing in
  the design depends on keeping it.

226 is the latest point at which the cluster must exist, because its
contention measurement runs the tick ingest on the production host. The
recommendation is to assign cluster provisioning to 225 or 226.

### Interfaces Required

- `FetchStatus` (`data/quality/fetch_status.py`) and `MAX_RETRY_COUNT`
  (`constants.py`, 5): the minute tier's attempt limit, reused as it is.
- `providers.errors`: `ProviderError`, `ProviderAuthError`,
  `ProviderTransientError`, `ProviderPermanentError` and
  `ProviderOutcomeUnknownError`.
- `cli.output` helpers and the `--json` convention.
- The shape of Kalshi's `open_sync_connection`, copied rather than
  imported: autocommit, a connect timeout, the pending-migration check,
  and `pg_try_advisory_lock`.

## Architecture

### Component Structure

```
src/manta_trading/data/tick/
  constants.py        + wait budget, poll interval, job-match skew, submit-resolve age,
                        spend window, lock key, DB connect timeout, env-name constants
                        (TICK_DOWNLOAD_TIMEOUT_SECONDS, 100 s, is 220's and reused)
  universe.py         TickUniverseEntry, TICK_UNIVERSE (ES, tier unset)
  pass_contract.py    TickPassPhaseName, TickOutcome, PhaseReport, PassResult,
                      classify_pass, TickPass runner      (copy of the Kalshi contract)
  run_context.py      TickRun, open_tick_run(): preflight + advisory lock
  manifest_repo.py    every SQL statement on tick_request / tick_archive_unit
  availability.py     edge + day-condition capture, hole reopen
  session_days.py     session-touched UTC days from TradingCalendar
  planner.py          PURE: wants → PlannedRequest[]      (Technical Decisions 4, 5)
  spend_guard.py      PURE: planned costs + trailing rows → SpendVerdict (Technical Decision 7)
  in_flight.py        poll → deliver → download → verify → expire (Technical Decision 9)
  verify.py           one unit's file checks + provider count
  definitions.py      definition-unit projection            (Technical Decision 5)
  adopt.py            job dir/zip → archive → manifest       (Technical Decision 10)
  acquisition_pass.py the five phases + PASS_PHASES

cli/commands/tick.py         + pass, adopt, reset verbs; exit codes (once)
cli/commands/tick_pass_render.py   pass / adopt / reset reports (Rich + --json)
market/schema/migrations/tick.py   + tick_006_availability
scripts/provision_tick_roles.sql   + tick_dataset_edge, tick_day_condition
scripts/cron_system_backup.sh      + /data/tick-archive in INCLUDE_PATHS
deploy/restic-excludes.txt         + /data/tick-archive/**/*.partial
scripts/verify_tick_archive_backup.sh   root: back up, restore one file, compare
```

Boundaries:

- Only `adapter.py` and `dbn_file.py` import `databento`. 220's
  import-boundary test keeps enforcing that.
- Nothing in `data/tick` imports `data/kalshi`. The contract is a copy, and
  a test compares the copy with the original (Technical Decision 1).
- `planner.py` and `spend_guard.py` perform no I/O. They are tested as
  pure functions of manifest rows, conditions, session days and costs,
  which is the architecture's testing strategy for passes.

### Data Flow

**The pass** (`mt data tick pass [--start D] [--end D] [--estimate-only] [--json]`):

```
open_tick_run(settings)            preflight: key, MT_TICK_DB_URL, MT_TICK_ARCHIVE_DIR,
                                   tick migrations applied, advisory lock (else exit 1)
 1 reconcile     unknown submits ─► match batch_jobs_since(requested_at − skew) ─► adopt id
                 expiry sweep     ─► deadline passed, no file ─► RETRY_EXHAUSTED + reopened
                 in-flight step   ─► in_flight.advance() once        (Technical Decision 9)
 2 availability  dataset_range + dataset_condition(span) ─► tick_dataset_edge,
                 tick_day_condition; a changed day reopens its holed units
 3 purchase      wants = companion definitions for owned tier days
                       ∪ universe tier bundles (narrowed by --start/--end)
                 filter by condition; group into monthly requests; cost() each (free)
                 guard (both ceilings) ─► refuse all | submit in day order:
                   txn: INSERT request + units at requested  ─► submit_batch (PAID)
                   txn: job id, committed_at, units → submitted
 4 await         loop in_flight.advance() every poll interval until none in flight
                 or the wait budget ends (remaining jobs printed with their deadlines)
 5 definitions   verified definition units ─► tick_definition ─► ingested
PassResult ─► Rich table or --json; exit code from the outcome
```

**`in_flight.advance()`** is shared by reconcile and await, so there is
one code path for delivery:

```
for each request with units at submitted:  batch_job(job_id)   (free)
    done       → units delivered; request gets actual cost, counts, deadline
    expired    → units RETRY_EXHAUSTED "retention expired", reopened_at = now
    queued/processing → rest
    unknown state (ProviderPermanentError) → units RETRY_EXHAUSTED naming it
for each job with delivered units, earliest download_deadline first:
    download_batch(job_id, <archive>/<job_id>)   (free; verified; resumable)
    each .dbn.zst file: header → (dataset, schema, day) → its unit → downloaded
    a unit whose day has no file → PROVIDER_HOLE
    write <archive>/<job_id>/manifest.json if the job did not deliver one
for each downloaded unit: verify.check(unit) → verified
```

**Adoption** (`mt data tick adopt --job-id ID --source PATH [--json]`):

```
open_tick_run  ─► refuse when job id is already in tick_request ("already adopted", exit 0)
batch_job(ID)  ─► state done|expired (else refuse); request, cost, counts, ts_received
session days of the job range (calendar; refusal → nothing copied or written, exit 4)
read manifest.json from PATH (dir or zip); manifest.job_id == ID
for each listed file: copy/extract → <archive>/<ID>/<name>.partial, hashing
                      size + sha256 match → rename   (any mismatch: nothing written, exit 1)
one txn: request (is_adopted) + units:
    day with a file → downloaded;  session day without a file → delivered + PROVIDER_HOLE
verify.check(each downloaded unit) → verified
```

### State Management

All state is in the tick database's manifest. Nothing is in memory
between runs, and nothing is on disk except the archive files.

A unit's lifecycle is 222's two columns plus one new one:

- **`state`** is the furthest step reached: *requested → submitted →
  delivered → downloaded → verified → ingested*. It never moves backward.
  This slice advances tier units to *verified* and definition units to
  *ingested*.
- **`fetch_status`** is the failure lifecycle of the next step (222's
  meaning). This slice's rules for setting it:
  - A **transient** failure (a network error, a 5xx, a checksum mismatch
    that deletes the `.partial`, a 429) → `FAILED_RETRYABLE`, with
    `attempt_count + 1` and `last_attempt_at`. When `attempt_count`
    reaches `MAX_RETRY_COUNT`, the status becomes `RETRY_EXHAUSTED`.
  - A **deterministic** failure goes straight to `RETRY_EXHAUSTED`, with
    the reason named. These failures are: a header that does not match
    its unit, a definition with no validity window, a changed definition,
    an unknown job state, and the provider refusing a submit with a 4xx
    other than 429. Retrying them would only spend passes on reaching the
    same terminal state. The minute tier has no deterministic outcome in
    its mapping, so this is the tick tier's rule, stated rather than
    borrowed.
  - `PROVIDER_HOLE` → a job completed and delivered no file for a day
    that a session touches.
  - A **provider outage** aborts the pass (`PROVIDER_ABORT`) at the first
    free call that fails. So a unit is charged at most one attempt per
    pass, and an outage cannot exhaust units by itself.
- **`reopened_at`** is new in `tick_006` and is Technical Decision 8's
  subject. It marks a unit that can never produce its file and whose day
  is wanted again. That happens when the retention window has expired, or
  when a hole has been reopened (automatically or by reset).

**Coverage** is the one predicate the planner uses. A `(dataset, schema,
symbols, stype_in, day)` is *covered* when some unit for it has
`superseded_by_unit_id IS NULL` and `reopened_at IS NULL`. Everything
else is wanted, if the calendar and the day's condition allow it.

**Availability** is two small tables that this slice writes and 225
reads: the dataset's edge and each day's condition, with the time each
was observed.

## Technical Decisions

### Technical Decision 1: the pass contract is a copy of Kalshi's, diffed by a test

`data/tick/pass_contract.py` holds the tick copy:

- **Field for field with Kalshi:**
  - `PhaseReport(name, outcome, summary, duration_ms, error=None)`;
  - `PassResult(run_id, started_at, reports, outcome, duration_ms)` with
    `to_dict()`;
  - `PassPhase` (a protocol with `name` and `async run(run) -> PhaseReport`);
  - the `SKIPPED` literal;
  - `classify_pass` (worst outcome, ignoring skipped phases);
  - the runner. After an aborting phase, every later phase is reported
    `SKIPPED`, and a `PARTIAL` phase does not stop the pass.
- **`TickOutcome`** has Kalshi's four `SyncOutcome` members (`ok`,
  `partial`, `provider_abort`, `storage_abort`) plus two of its own:
  - `refused`: a guard refused a purchase while there were wants.
  - `in_flight`: the wait budget ended with jobs still processing.

  Precedence, worst first: `storage_abort`, `provider_abort`, `partial`,
  `refused`, `in_flight`, `ok`. A refused or in-flight pass still ran
  every phase. Those two outcomes do not abort.

**Declared divergences.** The unit test fails if any of these changes
without the test changing:

- The tick copy adds the two outcome members above.
- It has no event sink and no `on_phase` callback. Kalshi uses both for
  `pass_runs` progress and its JSONL event stream. The tick pass has no
  `PassKind` until 233, and it logs the same two lines Kalshi logs ("tick
  pass started run_id=… phases=…" and "tick pass finished outcome=…").
- There is no historical phase. That phase is specific to Kalshi.

**The test** (`test/unit/data/tick/test_pass_contract_parity.py`) is
allowed to import both packages, because the import boundary binds `src`
and not tests. It checks four things:

1. It compares `dataclasses.fields` of the two `PhaseReport`s and of the
   two `PassResult`s: names, order and annotations (the annotations
   differ only by the outcome type).
2. It asserts that `set(TickOutcome) − set(SyncOutcome)` equals
   `{refused, in_flight}` exactly.
3. It asserts that the other members' values are equal.
4. It asserts that the abort-then-skip behaviour matches on the same
   report sequence.

The review item for the rest of the diff (anything Kalshi's contract
grows) is a named task in the task file, as every 220 slice requires.

Exit codes are defined once, in `cli/commands/tick.py`, which extends the
existing `EXIT_OK = 0`, `EXIT_PREFLIGHT = 1` and `EXIT_PROVIDER = 2`. The
new codes, after Kalshi's pattern:

| Code | Name |
|---|---|
| 3 | `EXIT_PARTIAL` |
| 4 | `EXIT_STORAGE` |
| 5 | `EXIT_REFUSED` |
| 6 | `EXIT_IN_FLIGHT` |

`EXIT_BY_OUTCOME` has a module-level exhaustiveness assert, as Kalshi's
does.

### Technical Decision 2: the run context refuses loudly, and one advisory lock serializes every manifest writer

`open_tick_run(settings)` is an async context manager. It yields
`TickRun(settings, provider, conn, archive_root, run_id, clock)`. Before
it yields, it refuses (`TickPreflightError` → exit 1, with the variable
or command named) when:

- `MT_DATABENTO_API_KEY` is unset. `from_settings` raises
  `ProviderAuthError`.
- A key beginning `MT_TICK_`, in the process environment or the `.env`
  file, is not a known tick setting. `Settings` ignores unknown keys, so
  a misspelt ceiling would otherwise read as "no ceiling". This happened
  on 2026-09-28: `MT_TICK_DATA_SPEND_CEILING_USD` was written instead of
  `MT_TICK_SPEND_CEILING_USD`. The refusal names the key and the closest
  known name. The known set is derived from the `Settings` fields that
  start with `tick_`, so it is never spelled out as a second list.
- `MT_TICK_DB_URL` does not resolve (`DatabaseNotConfiguredError`, whose
  `.env_var` is shown).
- `MT_TICK_ARCHIVE_DIR` is unset, is not an existing directory, or is not
  writable. The directory is never created implicitly: a typo must not
  build a second archive.
- The tick database is unreachable within
  `TICK_DB_CONNECT_TIMEOUT_SECONDS` (10).
- A migration in `TRACKS["tick"]` is missing from `schema_migrations`. A
  missing ledger table counts as every migration pending. The message
  ends with `mt data migrate apply --track tick`.
- `pg_try_advisory_lock(TICK_ACQUISITION_LOCK_KEY)` returns false. The
  message says that another tick acquisition run holds the lock.

`pass`, `adopt` and `reset` all take the lock, because each one writes
manifest rows. Two concurrent passes would otherwise compute the same
wanted set and submit it twice, and double buying is the failure this
initiative exists to prevent. The lock is session-level, so it is
released when the connection closes, including on a crash. The key is a
named constant, distinct from Kalshi's `262_000_001`.

**Every unit transition is compare-and-set** (review F009). Each
`UPDATE` in `manifest_repo.py` names the state and `fetch_status` it
expects to move from, in its `WHERE` clause (for example `reset` updates
only `WHERE fetch_status = 'RETRY_EXHAUSTED' AND reopened_at IS NULL`).
An update that matches zero rows raises, and it is never skipped
quietly. So the lock is not what keeps concurrent writers correct. It
exists to stop a double purchase, and 225's ingest does not need it:

- Acquisition moves units up to *verified* (and definition units to
  *ingested*).
- Ingest moves tier units from *verified* to *ingested*.
- The two sets of transitions are disjoint.
- `reset` touches only exhausted units that are not reopened, which no
  pass is advancing.

**225's obligation:** ingest takes its own advisory lock key, so two
ingest runs serialize, and it uses the same compare-and-set transitions
from `manifest_repo.py`. Neither lock waits for the other.

The connection is one `psycopg.AsyncConnection` in autocommit. Each
manifest transition is an explicit transaction. Blocking calls go
through `asyncio.to_thread`: every provider call, hashing, header reads
and zip extraction. That is 220's Technical Decision 2 applied: one
provider instance per run, never shared across threads.

### Technical Decision 3: the universe is a code constant, and ES ships without a tier

`data/tick/universe.py`:

```python
@dataclass(frozen=True)
class TickUniverseEntry:
    product: str                 # key into FUTURES_PRODUCT_CALENDAR
    symbols: tuple[str, ...]     # stored sorted
    stype_in: SType
    tier: TickSchema | None      # a STORED_TIERS member, or None = not chosen yet
    start: date | None           # wanted range, UTC days, end exclusive
    end: date | None             # None = up to the availability edge

TICK_UNIVERSE: tuple[TickUniverseEntry, ...] = (
    TickUniverseEntry("ES", ("ES.FUT",), SType.PARENT, tier=None, start=None, end=None),
)
```

- **Validated at import.** The product must have a calendar. Symbols must
  be non-empty and sorted. The tier must be in `STORED_TIERS` when set.
  `start` is required when a tier is set, `end > start` when both are
  set, and products must be unique. A bad universe fails every `mt`
  command at import, with the field named. That is loud, and it is
  caught by the unit tier.
- **ES matches the adopted jobs** (`ES.FUT`, `parent`). So coverage
  computed from adopted units applies unchanged once 226 chooses the tier
  and a range.
- **Spreads are included in ES by explicit configuration** (review F004).
  - The architecture excludes calendar spreads "unless explicitly
    configured", and `ES.FUT` (`parent`) includes them by construction.
    This entry is that explicit configuration, and the design records it
    as a departure from the default.
  - The reason is the data in hand: both adopted jobs were bought with
    `ES.FUT` and already contain spread trades. On the `trades` job,
    spreads are 236,017 of 10,049,172 records (2.35%), across 7 spread
    and 5 outright instruments (design-time findings). Excluding them
    would mean different symbols from the adopted jobs, so no adopted day
    would count as covered.
  - **Cost bound:** spreads add about 2.35% to an ES tier purchase at
    these rates. The planner reports cost per request, so the share is
    visible in every estimate.
  - **226's go/no-go must re-confirm it**, with the choices "parent
    including spreads" or "outrights only". Outrights only means raw
    contract symbols or continuous symbols, and the adopted days would
    then be re-bought or kept as a separate request shape. 231 decides
    the same question for GC and does not inherit ES's answer.
- **`tier=None` is the honest state before the go/no-go.** An entry with
  no tier produces no tier wants. The pass still buys the definitions for
  tier days the manifest already owns, which after this slice means the
  adopted jobs (Technical Decision 5).
- **`--start/--end` on `pass` only narrow.** They intersect every want
  (universe and companion) with the window for one run. They never widen
  the configured range, which is the long-term target. This is how "take
  the plan's year a month at a time" is run.

*Rejected: a TOML or YAML file named by a setting.* It adds a parser, a
path in each environment, and the chance of a silent misparse. *Rejected:
a database table.* It needs administrative verbs this initiative does not
otherwise have. The constant sits next to `FUTURES_PRODUCT_CALENDAR`,
which 231 already edits to add GC, so "GC is a configuration edit" means
one reviewed edit to two adjacent constants.

### Technical Decision 4: wanted days are session-touched UTC days, grouped into monthly requests

- **Days.** The unit is one UTC day (222). The days a product can have
  data on are the UTC days that one of its calendar's sessions touches.
  `session_days(calendar, start, end)` calls
  `TradingCalendar.sessions_between` once per product per run. For each
  session it takes every UTC date from `open_utc` to `close_utc − 1 ns`,
  clipped to `[start, end)`. Saturdays and full closures are never
  wanted. A holiday whose next session opens that evening is wanted (the
  UTC day of 2024-12-25 is touched by the 12-26 session, which opens at
  23:00 UTC). A range outside the calendar's populated span raises
  `OutOfPopulatedRangeError`. The purchase phase reports that as
  `STORAGE_ABORT` with the calendar named, and it never guesses.
- **Condition filter.** A wanted day is purchasable when its condition is
  `available` or `degraded`.
  - A `pending` day, or a day past the edge, is reported as pending and
    left for a later run.
  - A `missing` day is reported as a provider hole and is not bought.
    No unit is created for it (Technical Decision 8 explains why).
- **Grouping.** Purchasable days are grouped per `(schema, symbols,
  stype_in)` into runs of consecutive session days, and a run never
  crosses a UTC calendar month. A run breaks at any session day that is
  not purchasable (covered, pending or missing). A non-session day (a
  Saturday) does not break a run: it sits inside the request's range and
  gets no unit. Each run becomes one `TickRequest(start = first day, end =
  last day + 1)` and one batch job.
- **Why monthly.** A job is the unit of failure, retention and cost. A
  month bounds all three. It also matches the architecture's pace ("the
  plan's included year, taken a month at a time"), and on the adopted ES
  data a month of `tbbo` is about 0.9 GB billable. There is no size
  constant to tune: 226 can change the grouping rule if it measures a
  reason to.

Requests are submitted in order of first day. Within a day, definition
requests go before tier requests, so a partly completed pass never leaves
ticks without their definitions.

### Technical Decision 5: definitions mirror the tier request, and 224 projects them

**Scope.** 220's Technical Decision 5 left the definition scope to this
design. For every tier day the manifest owns, or is about to buy, the
wanted definition day has the same `dataset`, `symbols` and `stype_in`.
The consequences:

- The definitions cover exactly the instruments that can appear in the
  tier files: the outrights and the spreads under `ES.FUT`, which the
  design-time check found in the trades. This is what 225's resolution
  check needs.
- It is exactly the bundle `mt data tick estimate` already prices (tier
  plus definition for the same request shape).
- Companion wants are computed from the manifest, not the universe. So
  the adopted jobs get their definitions with no universe change, and a
  tier day never exists without a definition want.

*Rejected: definitions per configured product.* A per-product request
(for example `ES.FUT` over the whole universe range) would buy
definitions for days that have no ticks, and it would miss instruments
if a tier request ever used different symbols.

**Who projects them.** The plan's Notes say that 222 creates
`tick_definition` and that 224 writes it, and 222's data flow says the
same. The plan's 225 entry ("projects definition units first") is
therefore read as "requires definition units ingested" (see the
supersession list). The definitions phase:

1. **Scope.** It takes every definition unit at *verified* with
   `fetch_status` open, earliest day first. There is one transaction per
   unit.
2. **Decode.** It decodes the unit through `DbnFileReader`. There are 61
   records per day. It maps each record through `TICK_DEFINITION_COLUMNS`,
   translates provider "undefined" sentinels to `NULL` (222's model-table
   rule), and sets `unit_id` and `ts_recv_ns`.
3. **Window rule.** A record with an undefined `activation` or
   `expiration` fails the unit as `RETRY_EXHAUSTED`, naming the
   `instrument_id` and `raw_symbol`. This is 222 code-review note F007: a
   window cannot be placed, and an unbounded one is never invented.
4. **Existing row, same key.** The existing row has the same
   `(instrument_id, activation_ns)`. Every kept column except `unit_id`
   and `ts_recv_ns` is compared:
   - All equal → no-op. This is the daily re-send, which happens about 27
     times per instrument per month.
   - Any difference → the unit fails as `RETRY_EXHAUSTED`, naming the
     instrument and the fields. It is never a bare `ON CONFLICT DO
     NOTHING` (222, Technical Decision 5).
5. **No row.** `INSERT`. An exclusion violation, meaning an overlapping
   window for a reused id, fails the unit with both windows named.
6. **Commit.** The unit becomes *ingested* with `decoded_record_count`
   set.

Two records with the same key inside one file are compared the same way
before any insert.

**This exception is bounded** (review F005). Definitions are the only
schema the acquisition pass projects, and the phase never grows to other
schemas:

- **Tier data** always goes through 225's ingest pass, with its
  per-unit worker connection, its ledger and its three checks.
- **`statistics`** also goes through 225's ingest, if 228 adopts it,
  because it carries per-session figures that need sessions and a
  ledger.
- **A unit test enforces it.** It asserts that the definitions phase
  selects only units whose request schema is `TickSchema.DEFINITION`.

The architecture's reasons for a dedicated per-unit connection do not
apply to definitions:

- They are multi-gigabyte `COPY` loads, while a definition day is about
  61 rows.
- The ledger rows must commit with the load, but definitions have no
  sessions and so no ledger rows.

So the phase writes on the run's connection, in one transaction per
unit. *ingested* on a definition unit means "projected into
`tick_definition`". It says nothing about a ledger.

### Technical Decision 6: planning reads the calendar; reconcile, download and verify do not

The architecture says tick acquisition "needs no calendar" and continues
through a production outage. Exact wanted days need the calendar (an
unconditional Sunday-to-Friday rule would buy full-closure days and then
treat them as holes). So the dependency is placed where it costs
nothing:

- **Needs the calendar:** the purchase phase and `adopt`. If the
  production database is unreachable, they end with `STORAGE_ABORT`, and
  nothing is bought.
- **Does not need it:** the reconcile, availability, await and
  definitions phases. So a production outage never stops a paid delivery
  from being downloaded inside its window.

This is recorded as a refinement of the architecture statement.

### Technical Decision 7: the spend guard is all-or-nothing, both ceilings are required, and the 30-day sum comes from the manifest

The guard is a pure function, `evaluate_spend(planned, trailing, per_pass,
cap_30d, now) -> SpendVerdict`.

- **Planned** is `cost(request)` for each planned request. The call is
  free and is summed as `Decimal`. Re-submits of requests that reconcile
  found unaccepted are counted at their recorded estimate.
- **Trailing** is `Σ COALESCE(actual_cost_usd, estimated_cost_usd)` over
  every `tick_request` row whose `COALESCE(committed_at, requested_at)` is
  within `TICK_SPEND_WINDOW` (30 days) of now.
  - It counts adopted rows too. A job bought in the portal and adopted
    later was real money. The free-credit jobs fall outside the window by
    date.
  - It counts rows that were never accepted, conservatively. A refused
    submit is over-counted for 30 days; an accepted one is never missed.
  - A re-submit reuses its row, so nothing is counted twice.
- **The verdict** allows purchase only when all four of these hold:
  1. both settings are present;
  2. `Σ planned ≤ MT_TICK_SPEND_CEILING_USD`;
  3. `trailing + Σ planned` (re-submits already in `trailing` are not
     added again) `≤ MT_TICK_SPEND_30D_CEILING_USD`;
  4. `--estimate-only` is not set.
- **When it refuses**, it reports four things:
  - the planned total and each ceiling;
  - the amount over each ceiling;
  - which settings are absent;
  - for the 30-day cap, the date from which the planned total fits, found
    by ageing the oldest in-window rows out one at a time. If the planned
    total alone exceeds the cap, the report says the cap must be raised.
- **All-or-nothing.** A refusal buys nothing in that run, definitions
  included. The architecture's remedy for a pass that needs more is to
  raise the per-pass ceiling for that run, or to narrow the run with
  `--start/--end`. Partial purchase was rejected: it makes what a run
  buys depend on ordering, which is harder to predict and to review than
  "this plan, or nothing".
- **A $0 plan-included request** passes on its reported cost. The guard
  compares whatever `get_cost` returns.
- **Outcomes:**
  - A refusal while there are wants → `refused` (exit 5).
  - No wants → `ok`, whatever the settings.
  - `--estimate-only` → `ok`, with the plan reported.

`MT_TICK_SPEND_30D_CEILING_USD` is `Decimal | None = Field(default=None,
gt=0)` with no default value, like the per-pass setting (220, Technical
Decision 5). Its env name is spelled once, as `TICK_SPEND_30D_CEILING_ENV`.

Tests use mocked cost responses and manifest rows only. The guard is
never exercised against the live account.

### Technical Decision 8: a dead unit is reopened, and its repurchase supersedes it

Some units can never produce their file:

- an **expired** unit, whose retention window passed before download;
- a **holed** unit, whose job delivered no file for a session day.

The architecture wants both days bought again: expiry re-enters the
wanted set, and a hole reopens when the provider's day metadata changes
or on a manual reset. 222 has no column that says "this day is wanted
again". `state` never moves backward, and `superseded_by_unit_id` needs
the successor's id, which does not exist yet. So `tick_006` adds
`reopened_at TIMESTAMPTZ`. It is set in three places:

1. **The expiry sweep.** The unit is at *submitted* or *delivered*, its
   request's `download_deadline` is before now, and `fetch_status` is not
   `PROVIDER_HOLE`. The sweep sets `RETRY_EXHAUSTED` with the reason
   "retention expired at …" and sets `reopened_at`. The job may also
   report `expired`, which is handled the same way.
2. **The availability phase.** When it records a changed condition for a
   day (a newer `last_modified_date`, or a different condition), it
   reopens that day's holed units in the same transaction.
3. **`mt data tick reset`** on a holed unit.

Reopened units are excluded from coverage, so the planner wants the day
again. When the repurchase is written at *requested*, the same
transaction sets `new.repurchase_of_unit_id = old` and
`old.superseded_by_unit_id = new`. The old unit then leaves every
"current" read, and the new one carries the repeat cost for accounting.

A CHECK keeps the column honest: `reopened_at IS NULL OR state NOT IN
(<UNIT_STATES_WITH_FILE>)`, rendered from the enum. A unit that holds a
file is never reopened. Replacing loaded data is a supersession, which is
225's operation.

**`mt data tick reset`** (`--unit-id N` repeatable, or `--all`, with
`--yes` and `--json`) follows the minute reset:

- It asks the operator to type `reset` unless `--yes` or `--json` is
  given.
- A `RETRY_EXHAUSTED` unit that is not reopened → `UNKNOWN`,
  `attempt_count = 0`, `failure_reason = NULL`. It is retried where it
  stands. This covers units that 225's ingest exhausts, too.
- A `PROVIDER_HOLE` unit that is not reopened → `reopened_at = now`.
- An already reopened unit, or any other state → unchanged, and listed
  as such.

**Missing days get no unit.** The architecture lets `PROVIDER_HOLE` come
from days the provider reports as missing. Recording a hole unit for a
day that was never requested would need a request row with no job,
invented only to hold it. Instead:

- A `missing` day is not bought (buying it would buy nothing).
- The day's condition row records it. 225's status already reads that
  table to give each session the worst condition of its days.
- When the condition improves, the day becomes purchasable with no
  reopen step.

A missing session is still excluded from "caught up" and still shown as a
hole. Only the row that says so differs.

### Technical Decision 9: a submit that may have been charged is reconciled, never re-bought blind

- **Before submitting.** One transaction inserts the `tick_request` row
  (estimate, `requested_at`, `is_adopted = FALSE`, `delivery_mode =
  batch_job`) and its units at *requested*.
- **Success.** A second transaction records `provider_job_id`,
  `committed_at = job.ts_received` (the provider's clock, the same one
  adoption uses) and moves the units to *submitted*.
- **`ProviderOutcomeUnknownError`.** The units get `FAILED_RETRYABLE`
  ("submit outcome unknown") with `attempt_count + 1`. The phase stops
  submitting and ends `PROVIDER_ABORT`, because stacking unknowns
  compounds the risk.
- **Reconcile, at the start of every pass.** For each request with no
  `provider_job_id`, reconcile calls `batch_jobs_since(requested_at −
  TICK_JOB_MATCH_SKEW)` (5 minutes, which absorbs clock skew between host
  and provider). It looks for a job whose `TickRequest` equals the row's
  (dataset, schema, sorted symbols, `stype_in`, start, end) and whose id
  no `tick_request` row holds yet.
  - **Found:** reconcile adopts the job id and `ts_received`, and the
    units move to *submitted*. It was bought once.
  - **Not found:** this is never treated as proof that nothing was
    bought. A listing that lags the submit would make a real job look
    absent, and a manual run can follow immediately. So an unresolved
    request is **never re-submitted automatically** (review F002):
    - While the submit attempt (`last_attempt_at`) is younger than
      `TICK_SUBMIT_RESOLVE_AGE` (1 hour), the units stay
      `FAILED_RETRYABLE` ("submit outcome unresolved"). Every run
      searches for the job again, and the purchase phase skips the row.
    - Once the attempt is older than that and still no job is listed,
      the units become `RETRY_EXHAUSTED` ("submit outcome unknown; no
      job listed after 1 h; `reset` to re-submit").
    - Only `mt data tick reset` puts the row back to `UNKNOWN`. The next
      run's reconcile searches once more, and only then does the purchase
      phase re-submit the same row. Its estimate is already counted by
      the guard.

    Re-buying after an unknown outcome is therefore always an operator
    decision, made at least an hour after the attempt, and the job list
    is searched before every re-submit.

    This rule covers only the *unknown* outcome. A submit the provider
    definitely refused charged nothing, and it follows the two rules
    below.
- **Other refusals.** A 4xx refusal other than 429 (nothing charged) →
  the units are `RETRY_EXHAUSTED`, and the phase continues with the other
  requests (`partial`). A 429 → `FAILED_RETRYABLE` and `PROVIDER_ABORT`.

**Deadlines.** A job's `download_deadline` is its `ts_expiration`, set
when it reports `done` (220). In-flight jobs are downloaded earliest
deadline first, before the purchase phase runs, so free work that
protects money always precedes new spending.

**Waiting.** The await phase polls every `TICK_POLL_INTERVAL_SECONDS`
(15) for up to `TICK_WAIT_BUDGET_SECONDS` (1,800). The account's jobs took
5–84 s from submit to done, so the budget is deliberately generous. Both
are conservative constants that 226 re-sets from measurement. When the
budget ends:

- each in-flight job is printed with its id and deadline;
- the outcome is `in_flight` (exit 6);
- the next manual run's reconcile continues from that point.

This is the architecture's manual regime: a submitting run does not exit
quietly with units in flight.

**Verification** (*downloaded → verified*, `verify.check`):

- The file exists under its final name. Its size and SHA-256 equal the
  unit's recorded values (re-hashed, so a file changed on disk is
  caught).
- Its header's dataset, schema and `stype_in` equal the request's, and
  its start and end are exactly the unit's UTC day.
- The provider's free `record_count` for that one day is stored in
  `provider_record_count`. The design-time check showed it equals the
  file's decoded count.

A decode of the whole file is 225's count check, not this step.

### Technical Decision 10: adoption is all-or-nothing, and it also rebuilds the manifest from the archive

The command is `mt data tick adopt --job-id ID --source PATH`. `PATH` is
a job directory or the provider's zip.

- **Trust comes from the provider.** The job's request, cost, record
  count and `ts_received` come from `batch_job(ID)`, a free call. The
  job's state must be `done` or `expired`. Local JSON is used only for
  the file list.
- **Files.** Every file listed in `manifest.json` is copied or extracted
  into `<archive>/<ID>/`. This includes the four JSON files, which are
  part of the record, and `manifest.json` itself. Each file is written as
  `<name>.partial`, hashed while it is written, and renamed only when its
  size and SHA-256 match. A file already present under its final name
  with a matching hash is skipped. So re-adopting from the archive itself
  (`--source <archive>/<ID>`) copies nothing.
- **Safety.**
  - `--job-id` must equal `manifest.json`'s `job_id`.
  - Zip members are read only by the names `manifest.json` lists, and a
    name containing `/`, `\` or `..` is refused (no path traversal).
- **All or nothing.** Any mismatch, missing file or unreadable member
  refuses the whole adoption. No manifest row is written, `.partial`
  files are left for inspection, and the file is named (exit 1). The
  architecture allows adopting per file, but a partial adoption would
  give a job's days mixed provenance for no benefit.
- **Rows.** One transaction writes the request (`is_adopted = TRUE`,
  `estimated = actual = cost_usd`, `committed_at = ts_received`,
  `download_deadline = NULL`) and one unit per session day of the job's
  range:
  - a day with a file (matched by its header) → *downloaded*;
  - a session day with no file → *delivered* with `PROVIDER_HOLE`;
  - a file for a day no session touches → reported by name, with no
    unit. This is loud, so a calendar–provider disagreement is seen.

  `verify.check` then runs on each downloaded unit in the same command.
- **Idempotent.** A job id already in the manifest is reported as
  "already adopted" with exit 0, and nothing is written.

**Rebuilding the manifest.** Every job under the archive can be adopted
again:

- A job the pass bought already has `manifest.json` written beside it
  (see `in_flight.advance()` under Architecture, Data Flow) when the provider did
  not supply one.
- Job records stay readable after expiry, as the design-time check
  showed.

So a lost or scratch tick database is rebuilt by running `adopt` once per
job directory. The manifest's facts, costs included, come back from the
provider's own records. This is why the missing production cluster does
not block this slice.

### Technical Decision 11: the archive is `/data/tick-archive`, enrolled in the nightly restic backup

- **Layout.** `<MT_TICK_ARCHIVE_DIR>/<job_id>/<provider file name>`,
  which is the provider's own download layout. `file_path` is stored
  relative to the archive root (222), so the archive can move.
- **Production value.** `MT_TICK_ARCHIVE_DIR=/data/tick-archive`. It is on
  the `/data` NVMe volume, which is manta-owned, so no root is needed. It
  is outside `/home`, so a future service with `ProtectHome=true` (233)
  can reach it. It is separate from `/data/market-data/databento`, which
  holds the PM's original copies and stays read-only.
- **Backup.** `/data/tick-archive` is added to `INCLUDE_PATHS` in
  `scripts/cron_system_backup.sh`. `/data` is a separate device and the
  script runs with `--one-file-system`, so the path is listed
  explicitly. `deploy/restic-excludes.txt` gains
  `/data/tick-archive/**/*.partial`, because an unfinished download is
  not the record.
- **Retention and dedup.** Retention stays 920's: 7 daily, 4 weekly and 3
  monthly snapshots. Archive files are immutable, so restic
  deduplicates them across snapshots, and the archive costs its size
  once in B2.
- **The cron needs no change.** It already runs this checkout's script,
  so merging the slice enrols the directory.
- **Proof, now.** `scripts/verify_tick_archive_backup.sh` is a root
  script. It checks before it acts and logs to
  `/data/backup/tick-archive-verify.log`. It takes the same arguments as
  the cron line, and it:
  1. runs `cron_system_backup.sh` once;
  2. runs `restic ls latest /data/tick-archive` and compares the file
     count with the archive's own count, excluding `.partial` files;
  3. restores one data file into `/data/restore-test/tick-archive/`,
     which it creates;
  4. compares the SHA-256 of the restored file with the original;
  5. removes only the directory it created.

  It prints the expected and observed value at each step. Claude runs it
  under the sudo grant, and the PM reads the log. The full restore drill
  for both the archive and the database stays 227's.
- **Runbook.** Runbook 200's D9 include set and restore section gain the
  path and the procedure.

The path appears in two places that cannot share a constant: the
setting's value, and the backup script's include list, which is bash.
`.env_sample`, the README and runbook 200 all state the same value, and
the verify script fails if `$MT_TICK_ARCHIVE_DIR` (read from the env file
it is given) is not in `INCLUDE_PATHS`.

### Patterns and Conventions

- **Constants.** Every new comparison value is defined once in
  `data/tick/constants.py`: the wait budget, poll interval, match skew,
  submit-resolve age, spend window, lock key, connect timeout, and the env names
  `TICK_SPEND_30D_CEILING_ENV` and `TICK_ARCHIVE_DIR_ENV`. `TickOutcome`
  and the phase names are defined in `pass_contract.py`.
- **SQL.** Every statement lives in `manifest_repo.py` or
  `availability.py`. Phases call named functions and never build SQL
  inline. The coverage predicate is written once.
- **Errors.** Unit-level failures go into `fetch_status` and
  `failure_reason`, and the phase continues. Run-level failures become
  the phase outcome, following Kalshi's template: `ProviderError` →
  `provider_abort`, `psycopg.OperationalError` → `storage_abort`. Anything
  else propagates. There is no catch-all.
- **Sizes.** Files stay under about 300 lines. `cli/commands/tick.py`
  grows by three verbs, and rendering moves to `tick_pass_render.py` so
  both files stay under that limit.

## Implementation Details

### API Contracts

**CLI**

```
mt data tick pass   [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--estimate-only] [--json]
mt data tick adopt  --job-id ID --source PATH [--json]
mt data tick reset  (--unit-id N ... | --all) [--yes] [--json]
```

- `--end` is exclusive, as in `estimate`.
- `pass` prints the Kalshi-shaped table: Phase, Outcome, Duration. Under
  it goes each phase's summary:
  - **reconcile:** jobs polled, delivered, expired, and units
    downloaded, verified, holed and failed;
  - **availability:** the edge, and a condition tally over the planned
    span;
  - **purchase:** wanted days per product and schema, pending and missing
    days, planned requests with their costs, the trailing 30-day sum,
    both ceilings, the verdict, and the jobs submitted;
  - **await:** seconds waited, jobs delivered, and jobs still in flight
    with their deadlines;
  - **definitions:** units projected, rows inserted, no-ops, and failures.
  
  A closing line gives the outcome, exit code and duration. `--json`
  emits `{**PassResult.to_dict(), "exit_code": n}`.
- `adopt` prints the job, its cost, files verified, units by state, and
  holes and stray files by name.
- `reset` prints each unit with its before and after state.

**Exit codes** are 0–6, as in Technical Decision 1. `adopt` and `reset`
use 0, 1, 2 and 4.

**Settings**

```python
tick_spend_30d_ceiling_usd: Decimal | None = Field(default=None, gt=0)  # MT_TICK_SPEND_30D_CEILING_USD
tick_archive_dir: Path | None = None                                    # MT_TICK_ARCHIVE_DIR
```

**Does this belong in the API?** No. Acquisition is an operator action
that spends money. It is never a client request. What it records
(coverage, conditions, units) reaches clients through 230's
`/api/v1/futures/*` status surfaces, which read these tables.

### Database / Storage Schema

`tick_006_availability`, on the tick track, idempotent like `tick_001`–`tick_005`:

`tick_dataset_edge`: one row per dataset, upserted by each pass.

| Column | Type | Notes |
|---|---|---|
| `dataset` | `TEXT` PK | |
| `available_start`, `available_end` | `TIMESTAMPTZ NOT NULL` | from `dataset_range`; end exclusive |
| `observed_at` | `TIMESTAMPTZ NOT NULL` | 225 reports a stale observation as "edge unknown" |

`tick_day_condition`: one row per dataset and UTC day for which the
provider has been asked.

| Column | Type | Notes |
|---|---|---|
| `dataset` | `TEXT NOT NULL` | |
| `condition_date` | `DATE NOT NULL` | PK `(dataset, condition_date)` |
| `condition` | `TEXT NOT NULL` | CHECK rendered from `DatasetCondition` |
| `last_modified_date` | `DATE` | as the provider reports it |
| `observed_at` | `TIMESTAMPTZ NOT NULL` | |

`tick_archive_unit` gains `reopened_at TIMESTAMPTZ`, with
`tick_archive_unit_reopened_check` rendered from `UNIT_STATES_WITH_FILE`
(Technical Decision 8).

- **No index** beyond the primary keys. The manifest holds one unit per
  day per request, thousands of rows over the plan's year. The coverage
  and trailing-spend queries scan it in memory. 229 adds indexes if it
  measures a need.
- **Grants.** Both new tables join the enumerated `GRANT SELECT, INSERT,
  UPDATE, DELETE` list in `scripts/provision_tick_roles.sql`. 222's
  privilege test is extended to cover them.
- **Constraints.** A new `DatasetCondition` member needs a tick-track
  migration that re-renders the CHECK (journal 20260901), as 222's
  Technical Decision 9 requires.

**Availability capture.** The span is `[min, max]` over:

- the universe's tier ranges;
- every tier day the manifest owns (the source of companion wants);
- every holed unit's day.

The span is narrowed by `--start/--end` and clipped to the available
range. It needs one `dataset_condition` call per run, which is free and
returns one row per day. A day whose `(condition, last_modified_date)`
differs from its stored row reopens that day's holed units in the same
transaction (Technical Decision 8).

### Failure modes of the new I/O paths

| Path | Cost | Bound on waiting | Outcome of a failure | Left behind |
|---|---|---|---|---|
| Free provider calls (job, jobs since, range, condition, cost, record count) | free | SDK's fixed 100 s | `ProviderTransientError`/`AuthError` → phase `provider_abort`; later phases skipped | nothing |
| `submit_batch` | **paid** | SDK's 100 s | outcome unknown → units `FAILED_RETRYABLE`. Every run searches the job list; the row is never re-submitted without `reset`, and it is exhausted after `TICK_SUBMIT_RESOLVE_AGE`. 429 → `FAILED_RETRYABLE`; other 4xx → `RETRY_EXHAUSTED` | request + units at *requested* |
| `download_batch` | free | `TICK_DOWNLOAD_TIMEOUT_SECONDS` per file | unit `FAILED_RETRYABLE` (attempt counted); `provider_abort` | `.partial`, resumed next call |
| Verify (hash, header, count) | free | file read; one 100 s call | header mismatch → `RETRY_EXHAUSTED`; call failure → `provider_abort` | nothing |
| Await loop | free | `TICK_WAIT_BUDGET_SECONDS` | `in_flight`, jobs listed with deadlines | units at *submitted* |
| Tick database | — | `TICK_DB_CONNECT_TIMEOUT_SECONDS` at connect | `storage_abort`; each transition is one transaction | a consistent manifest |
| Calendar (production DB) | — | `TradingCalendar`'s connection | purchase / adopt `storage_abort`; nothing bought | nothing |
| Adoption file copy | — | local I/O | refusal naming the file; no rows | `.partial` for inspection |
| Archive volume full or unwritable (download, adoption copy, `manifest.json` write) | — | local I/O | Checked first: before any submit, the purchase phase requires free space on the archive volume ≥ Σ `billable_size` of the planned requests (free calls; uncompressed, so a safe upper bound for the zstd files), else `refused` naming the shortfall. `adopt` requires free space ≥ Σ `manifest.json` sizes before copying, else exit 1. A write failure that happens anyway (`OSError`, for example `ENOSPC`) is a host fault, not a unit fault: the run ends `storage_abort`, no attempt is counted, and the path and errno are named. | `.partial`, resumed by the next run once space exists |
| Advisory lock | — | `pg_try_advisory_lock` never waits | exit 1, lock holder named as "another tick acquisition run" | nothing |

## Integration Points

### Provides to Other Slices

- **225 (ingest):**
  - tier units at *verified*, with `provider_record_count` per day;
  - `tick_definition` populated for every instrument that can appear in
    those units;
  - `tick_day_condition` and `tick_dataset_edge` for status's pending,
    missing and edge-unknown lines;
  - `reopened_at` as a state status must show ("awaiting repurchase");
  - `mt data tick reset` for units that ingest exhausts;
  - the compare-and-set transitions in `manifest_repo.py`. 225 uses
    them, and takes its own lock key (Technical Decision 2);
  - the rule that definitions are the only schema acquisition projects
    (Technical Decision 5).
- **226 (proof):** both adopted jobs archived and verified, their
  definitions projected, and `TICK_WAIT_BUDGET_SECONDS` and
  `TICK_POLL_INTERVAL_SECONDS` to re-set from the first measured jobs.
  226 also sets ES's tier and range in `TICK_UNIVERSE`.
- **227 (backup):** the archive already in the nightly backup, with a
  one-file restore proven. The drill and the database policy are 227's.
- **228 (roll methods):** `statistics` units, if chosen, come in through
  this pass after a `TickSchema` member and a CHECK re-render. The
  planner's companion rule is the place to add them.
- **229 / 230:** manifest and definitions data to read, and the reopen
  vocabulary for debug and status output.
- **231 (GC):** a `TICK_UNIVERSE` entry next to its
  `FUTURES_PRODUCT_CALENDAR` edit.
- **233 (wiring):** the pass is the command its unit will run. The
  retention-deadline health finding reads `download_deadline` on units at
  *submitted* and *delivered*.

### Consumes from Other Slices

- **220:** the provider protocols and error taxonomy. If a later SDK
  changes job fields, the adapter's strict parsing raises, and this
  slice's unknown-state rule turns that into an exhausted unit that names
  it, never a stall.
- **221:** `sessions_between`. An unseeded range raises and stops
  planning, and it is never widened.
- **222:** the tables and constraints. Every transition is shaped to pass
  them. A constraint failure is a defect that surfaces as `storage_abort`
  and is not worked around.
- **923:** the track registry for the preflight, and the fixtures.

## Success Criteria

### Functional Requirements

1. **Preflight.** `pass`, `adopt` and `reset` exit 1 with the variable or
   command named when:
   - the key is unset;
   - `MT_TICK_DB_URL` is unset;
   - `MT_TICK_ARCHIVE_DIR` is unset or missing;
   - a tick migration is pending;
   - the lock is held (two concurrent runs, in a test).
2. **Adoption.**
   - `adopt` of the `trades` job directory and of the `tbbo` zip archives
     every listed file under `<archive>/<job_id>/`. It records one
     adopted request with the job's cost and `ts_received`, and one unit
     per session day of the job's range, each ending *verified* with
     `provider_record_count` set.
   - For these two jobs that is 26 and 52 units with no holes, as each
     session day has a file (walkthrough).
   - A second `adopt` of either job writes nothing and exits 0.
   - One corrupted byte in any listed file refuses the whole adoption
     with the file named and no rows written.
3. **Companion definitions.** With `TICK_UNIVERSE` as shipped (no tier),
   `pass --estimate-only` plans only definition requests covering exactly
   the adopted units' days. The requests have the adopted jobs' symbols
   and `stype_in`, and are grouped by UTC month (four requests for the two
   jobs).
4. **Spend guard** (unit tests over mocked costs and manifest rows):
   - With either ceiling absent and wants present, the outcome is
     `refused`, exit 5, both variables are named, and no submit happens.
   - A plan within the per-pass ceiling but over the 30-day cap is
     refused. The overage and the date from which the plan fits are
     reported.
   - A $0 plan passes.
   - Unaccepted rows and adopted rows inside the window count toward the
     trailing sum.
   - A plan whose Σ `billable_size` exceeds the archive volume's free
     space is refused before any submit, and the shortfall is named. So is
     an `adopt` whose files do not fit.
5. **Money-safety paths** (unit tests over fake providers):
   - An outcome-unknown submit leaves the units at *requested*. The next
     run matches the job from `batch_jobs_since` and moves them to
     *submitted* without a second submit.
   - With no matching job, no run re-submits the row automatically. It
     stays `FAILED_RETRYABLE` for `TICK_SUBMIT_RESOLVE_AGE`, then becomes
     `RETRY_EXHAUSTED`. Only after `reset` does a run re-submit it, and
     it searches the job list again first. A test runs two passes back to
     back and asserts exactly one `submit_batch` call.
   - A job past its deadline is swept to `RETRY_EXHAUSTED` with
     `reopened_at`. The next plan re-buys the day, with the repurchase
     and supersession links written in one transaction.
   - An unknown job state exhausts the units and names the state.
   - Delivered jobs download in deadline order before any submit.
6. **Delivery.**
   - A job that delivers no file for a session day yields a
     `PROVIDER_HOLE` unit.
   - A later condition change for that day reopens it.
   - `reset` of a hole reopens it, and `reset` of an exhausted unit sets
     `UNKNOWN` with `attempt_count = 0`.
   - A transient download failure counts one attempt, and the fifth
     exhausts the unit.
7. **Definitions.**
   - Projecting a definition unit inserts new instruments and treats an
     identical re-send as a no-op.
   - A changed kept field fails the unit, naming the instrument and the
     field.
   - An undefined activation or expiration fails the unit, naming the
     instrument.
   - An overlapping window for a reused id fails the unit, naming both
     windows.
   - After success the unit is *ingested*.
8. **Waiting.** The await phase ends `in_flight` (exit 6) when the budget
   passes with a job processing, and lists the job and its deadline.
9. **Contract parity.** The contract parity test passes, and it fails if
   a field is added to either copy's `PhaseReport` or `PassResult`.
10. **Idempotence.** A second `pass` right after a successful one plans
    nothing and exits 0.

### Technical Requirements

- **Unit tests** for the planner, guard, session days, universe
  validation, contract parity, verify, the definition rules and adoption
  (over real fixture files and zips built from them).
- **Integration tests** on `migrated_tick_db` for `tick_006`, every
  manifest transition, the lock, the reset, and adoption end to end with
  a fake provider over real DBN fixtures. Privilege tests on
  `provisioned_tick_db` cover the two new tables. The calendar is read
  from the test cluster's migrated minute database, which holds
  `CME_EQUITY` from 221.
- **No test calls a paid method on a real client.** A unit test asserts
  that the purchase phase's only paid call is `submit_batch` and that
  nothing calls `fetch_range`.
- **Quality bar.** ruff is clean on touched files, and mypy is clean per
  the kalshi_support path note. Files stay under about 300 lines.
- **Documentation** as listed in Technical Scope. The contract rows are:
  - I9 (loud refusals, the lock, the unknown-outcome reconcile);
  - I10 (the `pass`, `adopt` and `reset` verbs);
  - I11 (the manifest writes and availability tables);
  - I12 (the repurchase and supersession links);
  - I14 (definitions captured with their windows enforced).

### Integration Requirements

- 225 can ingest a *verified* adopted tier unit using only 222's tables,
  this slice's definitions and `provider_record_count`, with no change to
  this slice.
- The full unit and integration tiers stay green, apart from the known
  failures that already exist.

### Verification Walkthrough

These steps run on manta9000 with the tick database as a scratch
database on the test cluster (see Dependencies). Export
`MT_TIMESCALE_TEST_URL` from `.env` first, with the quotes stripped.

1. **Test suites.** Unit and integration are two invocations, because
   `test/unit/data` and `test/integration/data` both import as `data` (222
   finding).

   ```bash
   uv run --extra dev pytest test/unit/data/tick test/unit/cli/commands/test_data_tick.py -q
   uv run --extra dev pytest test/integration/data -k tick -q
   ```

   Expected: all pass.

2. **A scratch tick database, and the real archive directory.**

   ```bash
   createdb --maintenance-db="$MT_TIMESCALE_TEST_URL" mt_scratch_tick_223
   export MT_TICK_DB_URL="${MT_TIMESCALE_TEST_URL%/*}/mt_scratch_tick_223"
   export MT_TICK_MAINTENANCE_URL="$MT_TICK_DB_URL"
   mkdir -p /data/tick-archive && export MT_TICK_ARCHIVE_DIR=/data/tick-archive
   uv run mt data tick pass --estimate-only; echo "exit $?"   # before init
   ```

   Expected: exit 1, and the message names `mt data migrate apply --track
   tick`. Then run `uv run mt data init --database tick`. `migrate status
   --track tick` should list `tick_006_availability` as applied.

   Also add `MT_TICK_ARCHIVE_DIR=/data/tick-archive` to the dev `.env`.
   Step 9's backup check reads it from there.

3. **Adopt the two free-credit jobs.**

   ```bash
   uv run mt data tick adopt --job-id GLBX-20240930-USM7UXXJBA \
       --source /data/market-data/databento/GLBX-20240930-USM7UXXJBA
   uv run mt data tick adopt --job-id GLBX-20250123-XT4GD5UM6C \
       --source /data/market-data/databento/GLBX-20250123-XT4GD5UM6C.zip
   uv run mt data tick adopt --job-id GLBX-20240930-USM7UXXJBA \
       --source /data/tick-archive/GLBX-20240930-USM7UXXJBA           # again
   ```

   Expected:
   - The first two exit 0, with cost $12.58 / $36.80, 26 / 52 units
     *verified*, and no holes and no stray files.
   - The third reports "already adopted" and exits 0.
   - `sha256sum -c` against each job's `manifest.json` in
     `/data/tick-archive/<job>/` passes.
   - The originals under `/data/market-data/databento` are unchanged
     (`ls -la` times).

4. **Look at the manifest.**

   ```bash
   psql "$MT_TICK_DB_URL" -c "SELECT r.provider_job_id, r.schema, r.is_adopted, r.actual_cost_usd,
     count(*) AS units, count(*) FILTER (WHERE u.state='verified') AS verified,
     sum(u.provider_record_count) AS provider_records, r.provider_record_count AS job_records
     FROM tick_request r JOIN tick_archive_unit u USING (request_id) GROUP BY r.request_id"
   ```

   Expected: two rows. For each, `provider_records` equals
   `job_records`: 10,049,172 and 17,642,240. This is the per-day count
   finding holding over whole jobs.

5. **Plan without buying.**

   ```bash
   uv run mt data tick pass --estimate-only
   uv run mt data tick pass; echo "exit $?"
   ```

   Expected:
   - The first shows four `definition` requests, 2024-08 to 2024-12, about
     $0.004 in total, and exits 0.
   - With the ceilings unset, the second exits 5, names both
     `MT_TICK_SPEND_CEILING_USD` and `MT_TICK_SPEND_30D_CEILING_USD`, and
     submits nothing.
   - The Databento portal shows no new job.

6. **The first purchase (only after the PM sets both ceilings in `.env`,
   for example 1 and 5).**

   ```bash
   uv run mt data tick pass; echo "exit $?"
   psql "$MT_TICK_DB_URL" -c "SELECT count(*), count(DISTINCT instrument_id),
     min(to_timestamp(activation_ns/1e9)), max(to_timestamp(expiration_ns/1e9)) FROM tick_definition"
   ```

   Expected:
   - Four jobs are submitted, each delivered inside the wait budget,
     downloaded, verified, and projected. Exit 0.
   - The definition units are *ingested*.
   - `tick_definition` holds one row per instrument seen (outrights and
     spreads), each with a defined window. This confirms the risk item in
     222's Technical Decision 5 live.
   - The portal shows four jobs totalling under $0.01.
   - The run log shows, for each job, whether `batch_jobs_since` listed
     it on the first poll after its submit. This is the listing-lag
     measurement (see Risk Assessment). It is copied into the findings
     table.
   - Each of the four job directories under `/data/tick-archive` holds a
     `manifest.json`, and `sha256sum -c` against it passes. This is what
     makes the purchase re-adoptable once step 10 drops the scratch
     database (review F008).

   Without the ceilings, this step moves to 226.

7. **Idempotence.** `uv run mt data tick pass` again. Expected: nothing
   planned, no submit, exit 0.

8. **Reset.** Force one unit's state in the scratch database, then reset
   it:

   ```bash
   psql "$MT_TICK_DB_URL" -c "UPDATE tick_archive_unit SET fetch_status='RETRY_EXHAUSTED',
     failure_reason='walkthrough', attempt_count=5 WHERE unit_id=(SELECT min(unit_id) FROM tick_archive_unit)"
   uv run mt data tick reset --unit-id <that id> --yes
   ```

   Expected: before `RETRY_EXHAUSTED`, after `UNKNOWN`, `attempt_count`
   0.

9. **The archive is in the backup.**

   ```bash
   sudo scripts/verify_tick_archive_backup.sh --env-file .env --repo-prefix system \
       --exclude-file deploy/restic-excludes.txt --log /data/backup/tick-archive-verify.log
   ```

   Expected: each step prints the expected and observed value. The
   snapshot's file count under `/data/tick-archive` equals the
   archive's, and the restored file's SHA-256 equals the original's.
   Claude runs this under the sudo grant, and the log is what the PM
   reads.

10. **Prove the rebuild, then tear down.** The archive stays: it is the
    record. Re-adopting its job directories (step 3's form, with
    `--source /data/tick-archive/<job>`) rebuilds the manifest in
    whichever tick database comes next. Prove that before dropping
    anything:

    1. Create a second scratch database, `mt_scratch_tick_223b`, point
       `MT_TICK_DB_URL` at it, and `init` it.
    2. Re-adopt all six job directories: the two free-credit jobs and
       the four definition jobs from step 6.
    3. Check that each `tick_request` row shows the same
       `provider_job_id`, `actual_cost_usd` and `committed_at` as in
       `mt_scratch_tick_223`.

    Then drop both databases (both created by this walkthrough) with
    `dropdb --maintenance-db="$MT_TIMESCALE_TEST_URL" <name>`. Confirm
    with a count of 0 from `pg_database` for each.

    Until a durable tick database exists, the archive plus the provider's
    job records are the durable spend record, with the provider-side
    account limit behind them (review F008).

No `mt data tick status` exists yet; that is 225's.

## Risk Assessment

### Technical Risks

- **Spread definitions may lack validity windows.** No CME definition has
  been decoded yet. If some instruments under `ES.FUT` carry an undefined
  activation or expiration, the definitions phase fails those units.
- **How fast the provider's job listing shows a new job is unmeasured.**
  It no longer decides money safety, because an unresolved submit is
  never re-submitted without `reset` (Technical Decision 9). It only
  decides how long `TICK_SUBMIT_RESOLVE_AGE` must be before "not listed"
  can be trusted enough to exhaust the units.
- **A kept definition column may change across the daily re-sends**
  (review F006). Each instrument's definition is re-sent about 27 times a
  month, and any difference fails the unit terminally.
  `TICK_DEFINITION_COLUMNS` keeps only fixed contract terms:
  - identity, activation and expiration, symbol, asset and exchange;
  - class, security type, CFI and currency;
  - minimum price increment, display factor, unit of measure and its
    quantity, and contract multiplier.

  It keeps no daily limit or reference price. So a legitimate change is
  unlikely, but no CME definition has been decoded yet to prove it.
- **Degraded days are kept once bought.** A later provider correction is
  not picked up automatically.

### Mitigation Strategies

- **Spread windows:** the walkthrough's step 6 purchase (about $0.004) is
  the check, and it runs before 225 depends on it. If spreads lack
  windows, the design is revised: the likely remedy is to skip
  instruments with no window only when no tier record references them,
  which 225's resolution check then proves. The constraint is never
  widened silently (222, Technical Decision 5).
- **Listing lag:** the measurement is a design input, taken in
  walkthrough step 6. For each of the four real definition jobs, the pass
  logs whether `batch_jobs_since(requested_at − TICK_JOB_MATCH_SKEW)`
  lists the job on the first poll after the submit. The observed values
  are recorded in this document's findings table at implementation. If
  any job is not listed at once, `TICK_SUBMIT_RESOLVE_AGE` is re-set from
  the measurement before the slice closes. The 1-hour starting value is
  conservative against jobs that finished in 5–84 s.
- **Definition changes:** walkthrough step 6 is the first observation.
  It covers about 78 days of re-sends across two roll periods, and the
  run either passes or names the instrument and the field that changed.
  If a column does change legitimately:
  - it is dropped from the model table by a tick-track migration, and
    from `TICK_DEFINITION_COLUMNS`, because a value that changes daily is
    not a fixed contract term;
  - the comparison stays strict on every remaining column.

  The compared set is never loosened while the column is kept. That
  would store one day's value as if it were permanent.
- **Degraded days:** the day's condition row is kept, and 225's status
  shows the session as degraded. Repurchasing is a supersession the
  operator runs through `reset` semantics once 225 exists. Automatic
  replacement is left out on purpose: it would spend money without a
  human decision.

## Implementation Notes

### Development Approach

1. **Vocabulary and settings.** The constants, `TickOutcome` and the
   contract copy, with the parity test. The two settings and their tests.
2. **Migration.** `tick_006` and the grant list, with integration and
   privilege tests.
3. **Pure cores.** The universe with its validation, `session_days`, the
   planner and the spend guard, with unit tests over hand-built manifest
   rows and recorded metadata fixtures.
4. **Run context and repository.** The preflight, the lock, and
   `manifest_repo`, with integration tests for every transition.
5. **Adoption.** Adoption and `verify`, over real DBN fixtures and a zip
   built from them in the test.
6. **Delivery.** `in_flight` (poll, deliver, download, expire), with a
   fake provider over `test/tick_support/batch_responses.py`.
7. **Definitions.** The definitions phase, over the `definition` fixture
   (XNAS shape) and hand-set windows.
8. **The pass.** The phases, the CLI verbs, and the renderer.
9. **Backup.** The include list, the exclusion, the verify script and the
   runbook. Run the script.
10. **Documentation and the walkthrough.** The contract rows, plan Notes,
    README, `.env_sample` and CHANGELOG. Then the walkthrough against the
    real archive.

There is a checkpoint commit per numbered step. The diff against the
Kalshi contract is its own task in the task file.

### Special Considerations

- **Realtime paths check.**
  - *Path A (assemble recent history from realtime):* not foreclosed.
    Live segments will be units under a new `delivery_mode` member.
    Coverage counts every current unit today. Whether a live unit counts
    as coverage, which decides whether the pass buys over it, is a
    one-predicate decision left to the realtime initiative. The default
    precedence (historical over live) means "it does not count", so the
    predicate would exclude the live delivery mode.
  - *Path B (historical with its ~8-hour lag):* this slice is that path's
    mechanism. The monthly grouping and manual runs do not constrain a
    later catch-up cadence. 233 decides the cadence.
  - No decision here rules out either path.
- **Journal citations.**
  - 20260725, rule 2: no aggregate informs acquisition. The planner reads
    only manifest rows, conditions and the calendar.
  - 20260901: the new CHECKs are rendered from their enums.
  - 20260823: pass form, without its schedule (deferred to 233).
- **Security.**
  - The API key comes only from `Settings`, and the adapter never logs
    it.
  - Zip names are restricted to `manifest.json`'s list, and
    path-separator and `..` names are refused.
  - The archive directory must already exist and is never created by the
    pass.
  - The root script checks before it acts and deletes only the restore
    directory it created.
- **Money.**
  - The only paid method the pass calls is `submit_batch`, and only
    after the guard allows it.
  - Tests never hold a real client with a paid method reachable.
  - The walkthrough's single purchase is the PM's decision, made by
    setting the ceilings.

### Architecture and plan statements this design supersedes

Each is recorded in the architecture's Revision Log (entry 2026-09-28,
added with this design after review F003). The architecture's coupling
paragraph now lists the calendar edge. Each is also recorded in the slice
plan's Notes when the slice is implemented. Explicitly including spreads
in ES (Technical Decision 3) is recorded in the same Revision Log entry.

1. **"Tick acquisition, which needs no calendar."** Planning and adoption
   read the CME calendar to find session days. Reconcile, download,
   verify and definitions do not, so a production outage stops new
   purchases, not deliveries (Technical Decision 6).
2. **225 "projects definition units first."** 224 projects them, as the
   plan's Notes and 222's data flow already say. 225 requires definition
   units to be *ingested* before tier units (Technical Decision 5).
3. **Definition scope.** Definitions are bought per tier request shape
   (same symbols, `stype_in` and days), not per configured product
   (Technical Decision 5).
4. **`PROVIDER_HOLE` from missing days.** A `missing` day is recorded in
   `tick_day_condition` and not bought. `PROVIDER_HOLE` marks a unit only
   when a completed job delivered no file for a session day (Technical
   Decision 8).
5. **222 "no schema change of their own."** 223 adds `tick_006`: the two
   availability tables and `reopened_at` (Technical Decision 8, Database /
   Storage Schema).
6. **"A file that fails verification is not adopted."** Adoption is
   all-or-nothing per job (Technical Decision 10).
