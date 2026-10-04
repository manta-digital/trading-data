---
docType: slice-design
slice: proof-on-existing-data
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [223, 224, 225, 923]
interfaces: [227, 228, 229, 230, 231, 232, 233, 234]
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Slice Design: proof-on-existing-data

## Overview

This slice is the proof the initiative was built for. The two ES jobs bought
with free credits are already archived (223), their definitions are bought
(224), and the ingest pass loads them with every check passing (225). This
slice measures that pipeline on the production host and turns the numbers
into decisions:

- **Where the tick database lives.** The PM placed it on 2026-09-28: a second
  PostgreSQL cluster on the production host, with its data on the `/data`
  NVMe volume. No slice has created it yet. This slice does, because the
  contention measurement needs the ingest running on the production host.
- **What the numbers are.** Bytes per record by tier, compressed bytes per
  row, ingest rate, query latency, batch memory, worker count, batch-job
  timing, and contention with a production pass.
- **How the table is laid out.** The chunk interval is validated, and the
  compression layout is chosen from measurements. A tick-track migration
  applies it.
- **Whether the design still holds under compression.** Supersession's
  delete and overlap detection were built and tested on uncompressed chunks
  (225). They are re-verified on compressed ones.
- **What to do next.** A written go/no-go on the tier, on spreads, on GC,
  and on subscribing to the Standard plan.

It also carries one fix in from a late 225 review: a DBN file with a bad
header aborts the whole pass instead of failing that one unit.

Nothing is bought: every pass in this slice runs `--estimate-only`
(Technical Decision 2). Every provider call this slice makes is free metadata:
job records, record counts, and cost estimates.

## Value

- **The go/no-go is made on measured numbers.** The tier choice is the
  budget decision (architecture, "Schema tier is the cost decision"), and
  the Standard-plan subscription is the recurring cost. Both are decided
  from bytes measured on real ES data, not from projections.
- **The schema is proved before the data grows.** Minute storage failed on
  chunk geometry at 7 B rows. The tick table is validated at 27.7 M rows,
  where a wrong choice costs one rebuild from the archive in under a
  minute, not a restructuring sweep.
- **The tick database exists in production.** After this slice, 227 has a
  cluster to back up, 230 and 231 have a database to read, and 234 has a URL
  to write into the service environment.
- **Later slices get their inputs.** 227 gets the rebuild cost. 229 and 232
  get loaded data with a roll in each range. 233 gets the contention
  numbers.

## Technical Scope

**Included**

1. **The production tick cluster.** One root script,
   `scripts/provision_tick_cluster.sh`, creates the cluster, configures it,
   and applies `scripts/provision_tick_roles.sql` for two databases: the
   production tick database `trading_tick`, and the proof database
   `trading_tick_proof` where the measurements run. The PM runs it once
   (Technical Decision 1).
2. **The proof harness.** `scripts/proof_226_tick.py` runs every measurement
   in named steps. It writes one report per run under `user/notes/`
   (Technical Decision 2).
3. **The physical-grouping decision.** Two compression layouts are measured,
   and the tick-track migration `tick_007_trade_columnstore` applies the
   winner. It also adds the integer-time function and the compression
   policy. Space partitioning is rejected, with the reason recorded
   (Technical Decision 4).
4. **Compressed-chunk re-verification.** Integration tests run the
   supersession delete, overlap detection, ingest as `tick_app`, and the
   coverage raw count against compressed chunks (Technical Decision 5).
5. **Constants re-set from measurement.** The worker count, batch budget,
   wait budget, poll interval, lock timeout, and the connect and keepalive
   values. Each is kept or changed by a stated rule, and its docstring cites
   the measurement (Technical Decision 6).
6. **Contention against the Kalshi pass.** A looping tick ingest runs across
   hourly Kalshi firings, with the host and both database clusters sampled
   (Technical Decision 7).
7. **The bad-header fix.** `DbnFile` header checks raise
   `TickFileDecodeError`. Verify and ingest both turn it into a per-unit
   failure (Technical Decision 8).
8. **The go/no-go document** and the `TICK_UNIVERSE` edit for ES's tier and
   range (Technical Decision 9).
9. **The production rebuild.** `trading_tick` is migrated through `tick_007`
   and loaded from the archive. The rebuild is timed end to end, because
   that time is 227's input.
10. **Docs.** The data-correctness contract rows, slice plan Notes, the
    architecture's Revision Log, CHANGELOG, and README (Technical
    Requirements).

**Excluded**

- **Buying anything.** Mapping completeness, record counts and job timing
  all come from files and job records already held, and free metadata.
- **Contention against the minute pass.** PM decision, 2026-10-03: a minute
  firing outside its schedule walks 13,083 symbols on EODHD quota, and
  overlapping a scheduled firing means waiting for tomorrow. 233 repeats the
  contention run anyway, so it adds the minute overlap
  (Technical Decision 7).
- **Subscribing to the Standard plan, and pulling plan data.** The go/no-go
  recommends; the subscription is the PM's, and "the first data under the
  Standard plan follows the go/no-go".
- **Backing up the tick cluster.** 227 chooses the policy from this slice's
  rebuild cost. Until then, the cluster is a projection of a backed-up
  archive.
- **Service environment and wiring.** `MT_TICK_DB_URL` goes into the dev
  `.env` only. The service environment files belong to 234.
- **A NumPy binary `COPY` encoder, process workers, and intra-unit
  checkpoints.** Each is built only if a measurement fails its target. 225's
  numbers met the targets by orders of magnitude, so none is expected.
- **Read-side aggregates and indexes beyond the primary key.** They are
  added only if the query set misses its bound (Technical Decision 3).

## Dependencies

### Prerequisites

- **225 (complete, merged 2026-10-03):** ingest, `status` and `coverage`, the
  supersession and overlap paths, and the per-unit decode and write
  durations in the ingest report.
- **224 and 223:** the six archived jobs under `/data/tick-archive`: the two
  free-credit tier jobs and the four definition jobs bought on 2026-09-29.
  `adopt` rebuilds a manifest from them.
- **923:** `scripts/provision_tick_roles.sql`, the tick migration track, and
  `MT_TICK_DB_URL` / `MT_TICK_MAINTENANCE_URL` routing.
- **Host:** PostgreSQL 17.11 and TimescaleDB 2.29.1 binaries (already
  installed for the production cluster), 1.3 TB free on `/data`, and port
  5433 free. The script checks each.
- **One PM action:** running the provisioning script under `sudo`. `manta`
  has no password-free sudo for `pg_createcluster`.

### Interfaces Required

- `mt data tick adopt | pass | ingest | status | coverage | estimate`, unchanged.
- The ingest report's per-unit `decode_seconds` and `write_seconds`.
- `DbnFileReader.open_file` and `ITickFile.mappings`, for the
  mapping-completeness check.
- `ITickMetadataProvider` for job records (`batch_jobs_since`) and record
  counts. Both are free.
- `pass_runs` on the production database (read only), for the Kalshi
  pass's durations.

## Architecture

### Component Structure

```
scripts/provision_tick_cluster.sh     root, PM runs once; check-then-act; logs
  └─ pg_createcluster 17 tick (port 5433, data on /data)
  └─ provision_tick_roles.sql  × {trading_tick, trading_tick_proof}
  └─ writes MT_TICK_DB_URL / MT_TICK_MAINTENANCE_URL
     and MT_PROOF_226_DB_URL / MT_PROOF_226_MAINTENANCE_URL to .env

scripts/proof_226_tick.py             manta, no root; one step per measurement
  steps: rebuild · workers · batch · jobs · mapping · size · layouts
         · queries · contention · final
  └─ writes user/notes/<date>-226-proof-<step>.md

src/manta_trading/data/tick/constants.py         re-set values, new
                                                 TICK_TRADE_COMPRESS_AFTER,
                                                 TICK_TRADE_SEGMENT_BY/ORDER_BY
src/manta_trading/market/schema/migrations/tick.py   + tick_007_trade_columnstore
src/manta_trading/data/tick/databento/dbn_file.py    header errors → TickFileDecodeError
src/manta_trading/data/tick/verify.py                decode error → unit failure
src/manta_trading/data/tick/universe (TICK_UNIVERSE) ES tier and range

project-documents/user/analysis/226-analysis.tick-proof-go-no-go.md
```

The harness is a script, not an `mt` verb. It measures once and its output
is a document. An operator verb that nobody runs again is a surface to
maintain for nothing. Every pipeline action it runs is the shipped CLI or
the shipped pass function, so the harness measures what production runs.

### Data Flow

```
/data/tick-archive (6 jobs, read only)
   │ adopt ×6 → pass (projects definitions; nothing to buy) → ingest
   ▼
trading_tick_proof (tick cluster, port 5433)            measurements
   │ rebuild, workers 1/2/4, batch budgets, layout A, layout B,
   │ query set, contention loop (reset → ingest, repeated)
   ▼
user/notes/<date>-226-proof-*.md ──► 226-analysis.tick-proof-go-no-go.md
                                         │ decisions
                                         ▼
constants.py + tick_007 ──► test cluster (integration, compressed chunks)
                                         │
                                         ▼
trading_tick (production tick database): migrate → adopt → pass → ingest
   → compress eligible chunks → coverage → query set on the final layout
```

Side inputs: free provider metadata (job records, record counts, cost
estimates for ES and GC over the plan year), `pass_runs` on the production
database, and `/proc` on the host.

### State Management

- **`trading_tick_proof` is disposable.** The provisioning script creates it
  for this slice, and that designation is what lets the harness truncate
  it. Every destructive harness statement first checks
  `current_database() = TICK_PROOF_DB_NAME` and refuses otherwise. That is
  a structural guard, not operator care (tool guide, meta-rules). The last
  task drops it.
- **`trading_tick` is the keeper.** It is loaded only by the shipped
  `adopt`, `pass` and `ingest` commands, after `tick_007` is applied. The
  harness's one write there is `final`'s chunk compression, which is the
  policy's own work done early.
- **Reports are files, committed.** Each harness step writes its own report.
  The go/no-go cites them by file name, so every number has a source.
- **No new tables.** `tick_007` changes how `tick_trade` is stored, not what
  it holds.

## Technical Decisions

### Technical Decision 1: the tick cluster is postgres-owned, created by one root script

**Shape** (PM placement, 2026-09-28):

- `pg_createcluster 17 tick --port 5433 --datadir /data/postgresql/17/tick`,
  run by `postgres` and managed as `postgresql@17-tick`. The script creates
  `/data/postgresql`, owned by `postgres` mode 0700. `/data` is
  `manta:manta 0755` today.
- `shared_preload_libraries = 'timescaledb'`.
- `listen_addresses = '127.0.1.1'`. That is what `manta9000` resolves to on
  this host, so the URL uses the host name over TCP (architecture, "Keeping
  the move cheap"), and nothing listens on the LAN.
- `pg_hba.conf` allows `scram-sha-256` from the loopback route's source
  address (`ip route get 127.0.1.1` → `src 127.0.0.1` on this host: a
  connection dialled to 127.0.1.1 arrives from 127.0.0.1) for `tick_app`
  and `tick_migrate` only. Local-socket peer access stays for `postgres`.
- **Memory**, as starting values in one block at the top of the script:

  | Setting | Value | Why |
  |---|---|---|
  | `shared_buffers` | 4 GB | Production uses 8 GB on a 125 GiB host. The proof set is a few GB uncompressed and far less compressed. |
  | `effective_cache_size` | 16 GB | Planner hint only. |
  | `work_mem` | 64 MB | Explicit, as the architecture requires. |
  | `maintenance_work_mem` | 1 GB | Compression and index builds. |
  | `max_connections` | 30 | Two ingest workers, the pass's connection, and readers. |
  | `timescaledb.max_background_workers` | 4 | One compression policy and nothing else. |
  | `max_wal_size` | 8 GB | Bounds WAL on `/data` under the contention loop's repeated reloads. A checkpoint then recycles segments, so WAL cannot grow toward the archive's free space. |

  The go/no-go confirms these against the measured working set (buffer hit
  ratio during the query set, and the cluster's resident memory under
  ingest). If they change, the PM re-runs the script, which reconciles
  settings and restarts only the tick cluster.
- **Credentials.** The script generates both passwords, sets them with
  `ALTER ROLE`, and writes `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` and
  the proof database's two URLs, `MT_PROOF_226_DB_URL` and
  `MT_PROOF_226_MAINTENANCE_URL` (never `MT_TICK_*`: the tick preflight
  refuses an `MT_TICK_*` key no setting reads), into the checkout's `.env`. It adds only
  keys that are absent, keeps the file owned by `manta` with mode 0600, and
  never prints a password. No credential is committed.
  - **Atomic `.env` write** (re-review F004): the new content goes to a
    temporary file in the same directory with mode 0600, then `mv` replaces
    `.env`. A failure part-way leaves the old file whole.
  - **Scrubbed log** (re-review F004): before the script copies its log to
    `user/notes/`, it greps the log for each generated password and for any
    `postgresql://` URL that carries a password. A match fails the run, and
    the copy is not made. `--check` runs the same scan over any log already
    copied. The scan is tested with a log that contains a planted password.
    The test must fail it.

**The script follows the host-script convention.** It is check-then-act:
each step reads the current state, acts only if needed, and prints expected
against seen. It logs to `/var/log/manta-tick-provision-<timestamp>.log`
and a copy under `user/notes/`, exits 0 only when every check passes, and
is safe to re-run. `--check` runs without root and prints what it would
do, so it is exercised before the PM's run. It never touches the
production cluster (`17/main`). It
stops early if port 5433 is taken, if `/data` has less than 100 GB free, or
if the binaries' TimescaleDB version differs from production's (both
clusters must match, for the later move to hammerhead).

**Rejected: a cluster owned by `manta` under a user systemd unit.** It would
need no root at all. But it falls outside `pg_lsclusters` and the
`postgresql@` units, and 227's backup tooling and 913's role set assume
postgres-owned clusters. One PM command now is cheaper than a second
backup and operations path.

### Technical Decision 2: one harness, one report per step, in the proof database

`scripts/proof_226_tick.py <step>` takes one step name and writes
`user/notes/<date>-226-proof-<step>.md`. The steps:

| Step | What it does |
|---|---|
| `rebuild` | In the proof database, from empty: migrate, adopt all six job directories, `pass --estimate-only`, and `ingest`, each timed. Then `coverage` over both ranges. |
| `workers` | Resets the tier units and re-ingests with `TICK_INGEST_WORKERS` patched in process to 1, 2 and 4. Records wall time, the per-unit decode and write sums, and peak host CPU. |
| `batch` | Re-ingests the three largest units with `TICK_DECODE_BATCH_BYTES` at 8, 32 and 128 MiB. Records peak worker RSS and batch count. |
| `jobs` | Reads every account job record (free) and tabulates submit → done times. |
| `mapping` | Decodes every tier file and checks each record's `instrument_id` against its file's header mappings (Technical Decision 3). |
| `size` | Archive bytes, DBN record size, and uncompressed table bytes, all divided by exact row counts, per tier. |
| `layouts` | Compresses every chunk under layout A, measures, decompresses, then does the same under layout B (Technical Decision 4). |
| `queries` | Runs the query set uncompressed and under each layout, three times each, warm (Technical Decision 3). |
| `contention` | The Kalshi overlap run (Technical Decision 7). |
| `final` | On `trading_tick` after the production rebuild: compresses every eligible chunk, then the query set on the final layout, `coverage`, and table sizes. Read-only apart from the compression. |
| `drop-proof` | Drops `trading_tick_proof`. `DROP DATABASE` cannot run from inside the database it drops, so this step connects to `trading_tick` with the maintenance URL. Its guard is different (re-review F008). The target is the constant `TICK_PROOF_DB_NAME`, never a parameter or a value read from a URL. The step also refuses if that name equals the database named in `MT_TICK_DB_URL`. A unit test covers both refusals. |
| `archive-check` | Read-only (added at task breakdown, 11.3): for each job directory, every file `manifest.json` lists is compared against its listed size and `sha256:` hash with `tick.hashing.sha256_file`. Run after `drop-proof`, as the walkthrough's archive re-check. |

**Resetting the proof database** for a re-ingest: `TRUNCATE tick_trade,
tick_ingest_ledger`, then set every ingested tier unit back to *verified*.
This is the one place a state moves backward, so it is a harness function
behind the database-name guard (State Management), never product code.

**The harness drives the shipped code.** `rebuild` calls the CLI.
`workers` and `batch` call the ingest pass function with a patched module
constant, which is how the constants were designed to be tuned ("a
constant, not a flag, until a measurement gives a reason to vary it", 225).
Timing comes from the ingest report, not from wrappers.

**No purchase is possible anywhere in this slice** (review F002). Both
rebuilds run `pass --estimate-only`. That still reconciles, verifies,
captures availability, and projects definitions, because only an abort
stops the later phases (225's pass contract). It cannot submit a job. If
the estimate plans anything at all, for example because a definition job
is missing from the archive, the step stops and reports the plan. Buying
it is then a PM decision taken outside the harness, against
`trading_tick`, whose manifest the 30-day cap reads. A purchase recorded
only in the disposable proof database would hide spend from that cap.

**Failure modes** (review F003). Every step either completes and writes its
report, or exits non-zero with the reason and no report. A re-run starts
the step from its own beginning.

| Path | Hang, timeout, or disconnect | Partial completion | Handling |
|---|---|---|---|
| Free provider metadata (`adopt`, `pass`, `jobs`, `estimate`) | The adapter's timeouts and error mapping (220, 224): provider error, exit 2 | `adopt` is all-or-nothing per job; `pass` resumes from the manifest | Re-run the step. An auth or outage error is reported as it is, never retried in a loop. |
| Tick cluster down or restarting | Connect timeout (10 s) or keepalive break; storage exit 4 | Ingest commits whole units only | Re-run the step once the cluster is up. `rebuild` resumes: adopted jobs are skipped and ingested units are not selected again. |
| `workers`, `batch`, `contention` re-ingests | As above | The proof database holds whole units only | The step resets the proof database before each ingest, so a re-run starts clean. |
| `layouts`, between compressing and decompressing | As above | Some chunks are compressed under one layout | The step begins by decompressing every compressed chunk in the proof database, so a re-run is safe. |
| `final`'s compression on `trading_tick` | As above | Some chunks are compressed | Compressing an already compressed chunk is skipped (`if_not_compressed`). A re-run finishes the rest, and the policy would also finish them. |
| Production rebuild on `trading_tick` | As above | `adopt` per job, ingest per unit | The walkthrough's commands are safe to re-run as they stand (Verification Walkthrough, step 6). |
| Provisioning script | Each step checks before acting | Any step | Re-run. It exits 0 only when every check passes. |
| `/data` fills during a step | PostgreSQL fails the write and the step exits with storage exit 4 | The open unit rolls back; no archive file is opened for writing by any step | Every step that loads data first checks `/data` free space against the same 50 GB floor as the contention guard and refuses below it. On a trip, the operator frees space or truncates the proof database (its reset), then re-runs. |
| Production database unreachable | The connect timeout | Nothing is written to production by any step | `rebuild`, `final` and the production rebuild need the CME calendar there (an existing product edge, 225): storage exit 4, re-run later. `contention` checks production before starting any load and exits non-zero if it is down. A loss mid-run stops the loop and writes a partial report marked incomplete. |

**Production reads** (re-review F003). The harness reads the production
database in exactly two places, both in `contention`, both read only: the
Kalshi pass's rows in `pass_runs`, and `pg_stat_database`'s counters. It
connects with `MT_TIMESCALE_DB_URL`, the application credential the
checkout already holds and the CLI already uses. It sets
`default_transaction_read_only = on` and a 5 s `statement_timeout` on that
connection. Both tables are readable by the application role today, and the
913 role set needs no new role for a measurement that runs once. These are
harness reads, not product edges: no shipped command gains a production
read. The architecture's list of four tick → production edges stays
accurate, and the Revision Log notes the harness reads as one-off.

### Technical Decision 3: what is measured, and what passes

| Quantity | How | Pass / decision rule |
|---|---|---|
| Ingest rate (architecture's throughput target) | `rebuild` and `final`, on the production host | Slowest unit ≤ 120 s, against the 24 h of market time it covers. The whole proof set (78 tier units, about 3.5 months) ≤ 2 h. These are 225's bounds; 225 measured about 4 s and 55 s. |
| Mapping completeness (pass/fail) | `mapping`: for every record, some interval in its file's `mappings` names its `instrument_id` and covers the file's day | 100 % of the 27,691,412 records. Any miss adopts the raw-symbol fallback (`stype_in=raw_symbol` from local definitions) and is a NO-GO until redesigned. 224 found 100 % on the trades job; this covers tbbo. |
| Ingest checks | `rebuild` and `final` | 78/78 tier units ingested, 0 failed; `coverage` `ok` on every session of both ranges. |
| Bytes per record, by tier | `size` and `layouts` | Recorded: DBN record size (read from `DbnFile.record_size`, never assumed), archive bytes per record, uncompressed and compressed bytes per row. Rows of the two tiers are in separate chunks (Aug–Sep `trades`, Nov–Dec `tbbo`), so per-chunk sizes give per-tier figures. Exact `count(*)` only (tool guide, "Row-count authority"). |
| Chunk interval (7 days) | Chunk count projected over the table's 20-year span, and the query set's planning time | Validated if the projection stays within 1,000–2,000 chunks (7 days gives about 1,043) and planning ≤ 50 ms for Q1–Q4. Otherwise the go/no-go names a new interval, applied by a migration plus a rebuild from the archive (the architecture's remedy), never a restructuring sweep. |
| Query latency | `queries` and `final`: Q1–Q5 below | Q1–Q4 execute in ≤ 1 s warm on the chosen layout. Q5 is recorded. A miss gets an index or a read-side aggregate in `tick_007` and is re-measured. |
| Decode batch budget | `batch` | Keep 32 MiB unless a worker's peak RSS goes above 4× the budget, or a smaller budget changes the unit time by more than 10 %. |
| Worker count | `workers` | Keep 2 unless 4 workers ingest ≥ 1.5× faster and the contention run stays within its bound with 4. A flat curve means row building holds the interpreter lock. That is recorded, and the throughput target, not the curve, decides whether an encoder or process workers are needed. |
| Intra-unit checkpoints | Per-unit durations | Not needed while the slowest unit is ≤ 120 s. Future Work item 3 is then closed with the number. |
| Batch-job timing | `jobs`: every account job's submit → done | `TICK_WAIT_BUDGET_SECONDS` = max(1,800, 2 × the slowest job seen). The poll interval stays 15 s unless the fastest job finishes in under 15 s. |
| Capacity | `estimate` (free) for `ES.FUT` and `GC.FUT` over the latest available year, × the measured compressed bytes per row | Recorded against `/data`'s free space. A projection, labelled as one. |

**The query set** is chosen from the data, not hard-coded. The harness picks
the instrument-session with the most records in each range from the ledger.
In each range, that gives one contract and one session (Q5 uses its month).

| Query | Shape | Stands for |
|---|---|---|
| Q1 | Every tick of one contract in one session | `get` ticks by contract (230, 231) |
| Q2 | One-minute bars from Q1's ticks (`time_bucket` on nanoseconds) | Tick-derived bars (230, 231) |
| Q3 | `count(*)` per instrument for one session | `coverage`'s raw count (225) |
| Q4 | A contract's latest tick | Freshness lines (230, 231) |
| Q5 | One-minute bars for one contract over a month | Research reads |

Every query runs with `SET statement_timeout` and `EXPLAIN (ANALYZE,
BUFFERS)`. Planning and execution times are recorded separately, because
chunk count shows up in planning (tool guide, chunk sizing).

### Technical Decision 4: physical grouping, from two measured layouts

**TimescaleDB constraint.** A columnstore layout must put every column of a
unique key in `segmentby` or `orderby`. Otherwise the key cannot be
enforced on compressed chunks. The key is `(instrument_id, ts_event,
sequence, sequence_ordinal)` (222). Verify the exact rule in TimescaleDB
2.29 when writing `tick_007`. The test in Technical Decision 5 fails if it
is wrong.

**The two candidates:**

- **A:** `segmentby = instrument_id`, `orderby = ts_event, sequence,
  sequence_ordinal`. This is the tool guide's default shape, and it makes
  Q3 (per-instrument counts) answerable from batch metadata. Its cost is
  thin spread instruments getting small batches, which compress worse.
  Spreads were 2.35 % of trades.
- **B:** no `segmentby`, `orderby = instrument_id, ts_event, sequence,
  sequence_ordinal`. Full batches for every instrument, at the cost of the
  per-instrument metadata path.

**Rule:** choose the layout with the lower compressed bytes per row, unless
it misses a Q1–Q4 bound that the other meets. Within 10 % on both counts,
choose A. A is the documented default and keeps Q3 metadata-assisted.

**Space partitioning is rejected.** It multiplies chunk count by the
partition count (tool guide, chunk sizing). It pays off with several
tablespaces or disks, and the tick cluster has one. Per-instrument volume
is also skewed by orders of magnitude: the `size` step records rows per
instrument per chunk, and 225 saw ESZ4 at 52.2 M volume beside spreads with
a few thousand rows. A hash partition would then produce partitions of
very uneven size. The measured skew goes in the go/no-go as the evidence.

This is a deliberate narrowing of the architecture's wording, recorded in
its Revision Log (review F004). The architecture says the whole
physical-grouping class is decided "from the proof's measurements". The
two facts that decide space partitioning are not measurements: the cluster
has one data volume, and partitions multiply chunk count. No skew figure
the `size` step could report would change either. Space partitioning
comes back into question only if the tick cluster gains a second data
volume, for example on the move to hammerhead, and that slice decides it.

**`tick_007_trade_columnstore`** (tick track, maintenance credential):

1. `CREATE OR REPLACE FUNCTION tick_now_ns() RETURNS BIGINT` returning the
   current time in nanoseconds since the epoch, `STABLE`. Then
   `set_integer_now_func('tick_trade', 'tick_now_ns')`. An integer-time
   hypertable needs this for any policy (222 deferred it to "the first such
   object").
2. `ALTER TABLE tick_trade SET (timescaledb.enable_columnstore,
   timescaledb.segmentby = …, timescaledb.orderby = …)`, rendered from the
   new constants `TICK_TRADE_SEGMENT_BY` and `TICK_TRADE_ORDER_BY`.
3. `add_columnstore_policy('tick_trade', after => <ns>)` from
   `TICK_TRADE_COMPRESS_AFTER = timedelta(days=14)` (two chunk intervals),
   `if_not_exists => TRUE`.

**Why 14 days.** Historical data is always older, so the policy compresses
every chunk it finds. A future realtime writer keeps the two newest chunks
uncompressed. It is a constant because the realtime initiative may change
it.

**No waiting on the policy job.** After the production rebuild, the harness
step `final` compresses every eligible chunk explicitly (`compress_chunk`
over `show_chunks(older_than => …)`) and then measures. The policy covers
data loaded later.

**Applying it to a populated table.** `ALTER … SET` fails while compressed
chunks exist. In production, `tick_007` is applied to `trading_tick` before
its rebuild, so the table is empty. On the proof database, `layouts`
decompresses everything before switching layouts.

### Technical Decision 5: re-verify supersession and overlap on compressed chunks

225 built two paths on uncompressed chunks and handed their re-verification
to this slice. TimescaleDB handles deletes and unique checks differently on
compressed chunks. New integration tests, on the test cluster, compress
chunks explicitly with `compress_chunk`. The test cluster has no
TimescaleDB scheduler, so the policy never runs there.

| Test | Setup | Expectation |
|---|---|---|
| Supersession | Ingest a `trades` unit, compress its chunk, ingest the `tbbo` unit for the same day | One transaction deletes the `trades` rows (bounded by their ledger times) and loads the `tbbo` rows. The counts check passes, and `coverage` reads `ok`. |
| Overlap | A compressed chunk already holds a unit's rows; a second current unit with the same rows is ingested | `UniqueViolation` is raised, and the unit fails with `overlap:`. |
| Ingest as `tick_app` | A compressed chunk; the application role inserts through ingest | Succeeds. `tick_app` holds no privilege on TimescaleDB's internal compressed tables, so this proves grants propagate. If it fails, `provision_tick_roles.sql` gains the minimal grant, with a test. |
| Coverage on a partial chunk | Rows inserted into a compressed chunk, before recompression | Raw count equals the ledger. Then `compress_chunk` recompresses, and the count still equals the ledger. |
| Migration | `tick_007` on an empty database and on a populated uncompressed one | Settings read back from TimescaleDB's information views match the constants; the policy exists; `tick_now_ns()` is within a second of `now()`. |

The supersession delete's wall time on a compressed chunk is also measured
in the proof database. `TICK_INGEST_LOCK_TIMEOUT_SECONDS` must exceed it
(Technical Decision 6).

### Technical Decision 6: each constant is kept or changed by a stated rule

| Constant | Now | Rule |
|---|---|---|
| `TICK_INGEST_WORKERS` | 2 | `workers` step (Technical Decision 3) |
| `TICK_DECODE_BATCH_BYTES` | 32 MiB | `batch` step (Technical Decision 3) |
| `TICK_WAIT_BUDGET_SECONDS` | 1,800 | max(1,800, 2 × slowest account job) |
| `TICK_POLL_INTERVAL_SECONDS` | 15 | Kept unless a job finishes in under 15 s |
| `TICK_INGEST_LOCK_TIMEOUT_SECONDS` | 30 | ≥ 4 × the slowest measured supersession delete on a compressed chunk, and never below 30 |
| `TICK_DB_CONNECT_TIMEOUT_SECONDS` | 10 | Kept. The cluster is host-local, and the connect time is recorded in `rebuild`. |
| `TICK_DB_KEEPALIVES_*` | 60 / 10 / 6 | Kept. They bound a silent partition, which a host-local connection cannot have until the move to hammerhead. The move re-checks them. |
| `TICK_SUBMIT_RESOLVE_AGE` | 1 h | Kept. Every listing in 224 appeared on the first poll. |
| `TICK_TRADE_CHUNK_INTERVAL` | 7 days | Technical Decision 3 |

Each docstring is rewritten to name its measurement and the report that
holds it. A constant that keeps its value still gets a new docstring,
because "conservative until 226 measures" stops being true.

### Technical Decision 7: contention is measured against the Kalshi pass

**PM decision (2026-10-03).** The Kalshi pass fires hourly and writes to
the production database. This week it ran 168 times at 133–324 s, so its
solo duration is already measured, and `pass_runs` holds it. An overlap is
measurable within the hour and spends no quota. The minute-pass overlap
moves to 233, which re-runs contention to check the weights it sets.

**The run.** The `contention` step reads the Kalshi timer's next elapse
time. Two minutes before it, the step starts a loop in the proof database:
reset, ingest the whole proof set, repeat. The loop stops one minute after
the Kalshi pass ends. Overlapped firings: two. A third firing, with no tick
load, gives the host's solo sample. Every 5 seconds the step records:

- host CPU busy and iowait (`/proc/stat`);
- `MemAvailable` and `Committed_AS` against `CommitLimit` (`/proc/meminfo`).
  This host's allocation failures were commit-limit failures before;
- reads and writes per device (`/proc/diskstats`): `nvme0n1` carries the
  production cluster, `nvme1n1` carries `/data` and the tick cluster;
- the production cluster's `pg_stat_database` commit and tuple counters,
  read only, for the production write throughput (production reads below);
- the tick loop's rows per second.

**Reported:** each overlapped Kalshi duration against the week's
distribution; the per-second series summarised as median and 95th
percentile, overlapped against solo; and the tick ingest rate under
overlap against `rebuild`'s solo rate.

**Bound:** an overlapped Kalshi duration above the week's maximum (324 s) is
"measurable contention". The go/no-go says so, and 233 starts from it.
Within that, the result is "none measured at two ingest workers". Weights
are not set here; 233 sets them from these numbers, as the architecture
requires. A "none measured" result here is not a clearance for the minute
pass (re-review F006). Kalshi writes little and often; the minute pass walks
13,083 symbols. 233 measures the minute overlap before it relies on
either.

**Guards** (review F001). The loop runs on the production host, so it is
bounded, not only measured. Each threshold is a named constant in the
harness.

| Condition | Action |
|---|---|
| `MemAvailable` below 16 GiB | Stop the loop at once. The report records the trip and the step exits non-zero. If the first sample, before any load, is already below the floor, the step exits non-zero without starting the load. |
| `/data` free space below 50 GB | Stop the loop at once and exit non-zero. The archive's files are never written by the loop, so a full volume can stop new writes but cannot damage them. The floor leaves room for the archive's next job either way. |
| An overlapped Kalshi pass still running at 2 × 324 s | Stop the loop and record "Kalshi exceeded 2× its weekly maximum under tick load". The pass is left alone; it is production's. |
| The Kalshi timer has not fired within 75 minutes of the step's start, or its next elapse is missing (timer disabled) | Exit non-zero without starting any load. |
| The step is interrupted (Ctrl-C, SIGTERM) or raises | A `try/finally` stops the loop, awaits the in-flight ingest workers as the ingest pass already does on abort (225), and closes every connection. An interrupted worker's transaction rolls back, so the proof database holds only whole units. The next step's reset handles them. |
| The harness process is killed outright | The tick cluster ends each backend when its socket closes. The open transaction rolls back, and the advisory lock is released with the session (225). Nothing outside the proof database is touched. |

**Why there is no commit-limit guard** (re-review F001). An earlier draft
also stopped on `CommitLimit − Committed_AS` below 4 GiB. That headroom was
3.7 GiB on 2026-10-03 before any load, so the guard would have tripped on its
first sample. Overcommit is heuristic (`vm.overcommit_memory = 0`), so the
kernel does not enforce that limit, and it is not a failure point. Real
memory pressure is what `MemAvailable` measures. `Committed_AS` is still
sampled and reported, as data only.

The load cannot reach the production cluster's disk: the tick cluster is on
`nvme1n1`. CPU and memory are what is shared. The memory row guards memory,
and the Kalshi row is the CPU-pressure signal that reaches production.

### Technical Decision 8: a bad file header fails the unit, not the pass

Found by a review of 225 after merge. `DbnFile.__init__` and its helpers
raise a plain `ValueError` or `TypeError` for header content: mixed or
unsupported schema, unsupported `stype_in`, `ts_out` records, a wrong
`stype_out`, or a mapping date of the wrong type. The ingest worker catches
only `TickFileDecodeError` and `OSError`, and verify catches neither. So
either pass aborts with `storage_abort` on one bad file.

- In `dbn_file.py`, each of those raises becomes `TickFileDecodeError`,
  with the same message.
- `iter_batches`'s `ValueError` for a byte budget below one record **stays**.
  It is a configuration error and should stop the run.
- `verify._file_mismatch` catches `TickFileDecodeError` from `open_file` and
  returns `header: <message>`. The unit fails deterministically, like a
  checksum mismatch.
- The ingest worker already maps `TickFileDecodeError` to a `decode:`
  failure. It needs no change.
- Tests: one file per header case, built by rewriting a real fixture's
  header bytes (never an invented format). A unit test per case on
  `DbnFile`, and one integration test per pass: verify reports the unit
  failed and the pass continues, and ingest reports `decode:` and the pass
  continues.
- **Delivery and adopt** (PM decision 2026-10-03, found while testing the
  above). Both match a file to its unit by reading its header's start day,
  so a refused header there aborted the whole job. The shared
  `file_days.file_days` collects refused files instead of raising. A day no
  readable file claims then fails deterministically naming the refused
  files (`header: no readable file for this day; refused: …`) rather than
  being recorded as a provider hole, since a refused file may be that day's.
  With no refused file, holes are recorded as before. Integration tests:
  delivery in `test_tick_bad_header.py`, adopt in `test_tick_adopt.py`.

### Technical Decision 9: the go/no-go document, and ES's tier and range

`user/analysis/226-analysis.tick-proof-go-no-go.md` (`docType: analysis`)
holds the measured table and one section per decision, each with a
recommendation and its evidence:

- **Tier, `trades` or `tbbo`.** Under the Standard plan both are included,
  so the comparison is bytes stored and the value of the quote at each
  trade, not price. Whether plan data prices at $0 is verified at
  subscription (architecture).
- **Spreads** (224's open question). Either "parent including spreads",
  which matches the adopted jobs, or "outrights only". Decided from the
  measured spread share in both tiers.
- **GC.** Whether it follows at the same tier, from the free estimate and
  the bytes per row. 232 still decides its own spreads and calendar.
- **Subscribing to the Standard plan.** A technical go or no-go. The timing
  stays the PM's, ideally with realtime work.
- **Settled by measurement:** the chunk interval, the layout, the constants,
  intra-unit checkpoints, the tick cluster's memory, contention, and the
  rebuild cost handed to 227.

**`TICK_UNIVERSE` for ES** gets the recommended tier, with `start` and `end`
set to the range already held at that tier. For `tbbo`, that is 2024-11-02
to 2025-01-01 (implementation: the session dated 11-01 opens at 22:00 UTC on
10-31, a day the tbbo job does not hold, so the first session held whole is
the one dated 11-04). That is the honest wanted range before a subscription: a
pass finds nothing to buy, and status can read caught up. Widening it to
the plan year is a one-line edit made when the PM subscribes. The PM can
change the tier at review; it is the same edit.

**The other tier's range** (review F005). Its units stay loaded and
current, outside the wanted range. No tier-mixing feature is built, and
none is needed. 225's status already reports every session touched by a
current tier unit, wanted or not (225, Technical Decision 9), so:

- `status` lists those sessions, `complete`, under the product's one tier
  field. The configured tier names what is wanted; it does not relabel
  rows that were loaded at the other tier;
- "caught up" is judged over the wanted range only, so those sessions
  neither help nor hurt it;
- `coverage --start --end` proves raw counts over any range, tier aside.
  That is why success criterion 2 can require both ranges.

The walkthrough checks all three (step 7). If the PM later wants that
range at the chosen tier, buying it under the plan supersedes the loaded
days, by tier rank, at ingest (225).

### Technical Decision 10: what this changes outside the proof

- **Does this belong in the API?** No new surface. The go/no-go is a
  document, and the harness is a one-off script.
- **Realtime paths check.** Neither path is ruled out:
  - Path A, assembling recent history from realtime capture: the
    compression policy leaves the two newest chunks uncompressed for a
    live writer, and Technical Decision 5 proves that whole-day
    supersession works on compressed chunks. That is what
    historical-over-live precedence needs.
  - Path B, historical data with the lag: same tables, same pass.
  - The chunk interval and layout are neutral to both.
- **Kalshi contract diff.** No pass code changes apart from verify's
  decode-error mapping. The named task re-runs the parity test and diffs
  the tick and Kalshi contract files. A difference is recorded or absorbed.

### Patterns and Conventions

- **Host script:** check-then-act, expected against seen, logged, safe to
  re-run, and a single PM command (`scripts/cutover_265_trades.py`
  pattern).
- **Harness reports** follow the cutover scripts' report shape. Reuse
  `scripts/cutover_common.py`'s report helpers where they fit; nothing
  generic is extracted.
- **Constants rendered into the migration**, as `TICK_TRADE_CHUNK_INTERVAL`
  is today.
- Every proof number in a document names the report file it came from.

## Implementation Details

### Database / Storage Schema

`tick_007_trade_columnstore` is the only schema change (Technical
Decision 4). The function and policy are owned by `tick_migrate`, and the
policy job runs as the owner. `tick_app` needs no new grant unless the
`tick_app` ingest test in Technical Decision 5 shows otherwise.
`provision_tick_roles.sql`'s write surface already covers `tick_trade`.

**Applying it:** test cluster first, through the integration tier. Then
`trading_tick_proof`, then `trading_tick`, with `mt data migrate apply
--track tick` and the maintenance URL of each.

**New constants in `constants.py`:** `TICK_TRADE_SEGMENT_BY`,
`TICK_TRADE_ORDER_BY`, `TICK_TRADE_COMPRESS_AFTER`, and
`TICK_PROOF_DB_NAME`, the name the harness's guard compares against.

## Integration Points

### Provides to Other Slices

- **227 (backup):** a postgres-owned tick cluster at
  `/data/postgresql/17/tick` to enrol, and the measured rebuild cost (adopt
  + pass + ingest + compress on `trading_tick`, end to end) for choosing the
  policy.
- **229 (roll methods):** both ranges loaded in `trading_tick`, each with a
  quarterly roll (September and December 2024), and per-contract volume in
  the ledger.
- **230 / 231 (operator surface, API):** a production tick database, the
  query latencies for their reads, and a measured basis for any index they
  add.
- **232 (GC):** the GC estimate and the tier recommendation. 232 decides its
  own spreads and builds its calendar.
- **233 (arbitration):** the Kalshi contention numbers and the host series.
  It adds the minute-pass overlap.
- **234 (wiring):** the URL and credentials to copy into the service
  environment.

### Consumes from Other Slices

- **225:** everything under Prerequisites. If the compressed-chunk tests in
  Technical Decision 5 fail, the fix lands in 225's code inside this
  slice, because 225 handed that verification here.
- **224:** the four definition jobs, so a rebuild needs nothing bought. If
  one is missing from the archive, `pass --estimate-only` shows the
  purchase it would need, and the step stops (Technical Decision 2).

## Success Criteria

### Functional Requirements

1. The tick cluster runs as `postgresql@17-tick` on port 5433, with data
   under `/data/postgresql/17/tick`. `trading_tick` and
   `trading_tick_proof` exist with the 923 roles. The PM's script run
   exits 0, and its log is committed under `user/notes/`.
2. `trading_tick` is migrated through `tick_007`, holds 27,691,412 rows in
   `tick_trade` from 78 ingested tier units with 0 failed, and `coverage`
   reads `ok` on every session of both ranges.
3. Every eligible chunk of `trading_tick` is compressed under the chosen
   layout, and the compression policy exists.
4. Every row of Technical Decision 3's table has a measured value and a
   pass or decision recorded in the go/no-go, citing its report file.
5. Mapping completeness is 100 %, or the go/no-go is NO-GO with the
   raw-symbol fallback named.
6. The five compressed-chunk tests in Technical Decision 5 pass.
7. Each bad-header case fails its unit with a named reason in both verify
   and ingest, and the pass goes on to the next unit.
8. `TICK_UNIVERSE` names ES's tier and range per Technical Decision 9;
   `mt data tick pass --estimate-only` plans nothing to buy, and status
   reads caught up for ES.
9. `trading_tick_proof` is dropped, and `pg_database` on the tick cluster
   lists only `trading_tick` and the cluster's own databases.

### Technical Requirements

- Unit tier clean. Integration tier: only the known baseline failures
  (`test_cli_lists` priority1 ×2, `test_migration_051_052` ×2,
  `test_policy_advances_head` unaided ×2), plus `tick_007`'s migration-chain
  test updated. A test pinning the chain's last migration id goes stale
  with every migration (as 051/052's has).
- mypy and ruff clean on touched files. `shellcheck` clean on the
  provisioning script (`~/.local/bin/shellcheck`).
- The harness's guard has a unit test: every destructive harness function
  refuses on a database whose name is not `TICK_PROOF_DB_NAME`.
- No secret in any committed file. The `.env` writer has a test that it adds
  only absent keys and leaves mode 0600.
- **Docs:**
  - the data-correctness contract: 226's part in the I11 row (coverage
    proved on both ranges in production), the I12 row (supersession and
    overlap verified on compressed chunks), and the I13 row (session check
    on every unit);
  - the slice plan's Notes: the statements this design supersedes;
  - the architecture's Revision Log. It names the paragraphs it amends:
    "Cross-source arbitration" and the "Proof on existing data" entry
    under Anticipated Slices (contention moves from the minute pass to the
    Kalshi pass, and the minute-pass overlap goes to 233); "Pass form" and
    "Delivery mode and retention" (batch-job timing taken from the
    account's job records); and "Storage" (space partitioning decided from
    the disk layout, not from a measurement). It also records the harness's
    one-off production reads (Technical Decision 2), which add no product
    edge. The slice plan's 233 entry gains the minute-pass overlap
    measurement;
  - CHANGELOG, and a README storage paragraph naming the tick cluster and
    its layout;
  - the migrations README row for `tick_007`.

### Integration Requirements

- `mt data tick status | coverage | ingest` against `trading_tick` work
  unchanged from 225, with `MT_TICK_DB_URL` pointing at the new cluster.
- The production cluster (`17/main`) is untouched: its configuration files
  have the same checksums before and after the provisioning script.
- The Kalshi, minute, daily and health timers fire as scheduled throughout.
  The harness stops no timer and fires no pass.

### Verification Walkthrough

Run on manta9000 from the checkout. Nothing is exported: `mt` and the
harness load `.env` themselves (python-dotenv), and a `psql` step reads one
key with `deploy/lib/env_value.sh` (sourcing `.env` would shell-expand a `$`
in a password).

1. **Provision (PM, once).**

   ```bash
   sudo scripts/provision_tick_cluster.sh
   ```

   Expected: every step prints `ok` against its expected state, then the
   last line `PASS: tick cluster 17/tick on 5433; 2 databases; .env updated
   (4 keys added)`, exit 0. Re-running prints the same with `0 keys added`.

   ```bash
   pg_lsclusters
   psql "$(. deploy/lib/env_value.sh; env_value .env MT_TICK_MAINTENANCE_URL)" \
     -Atc "SELECT default_version FROM pg_available_extensions WHERE name='timescaledb'"
   ```

   Expected: two clusters (`main` 5432, `tick` 5433), both online, and
   `2.29.1`.

   **Result 2026-10-03:** PASS on the second run (`2026-10-03-226-tick-provision-20261003T202737Z.log`).
   The first run failed its login check: a connection dialled to 127.0.1.1
   arrives from 127.0.0.1, so `pg_hba.conf` now takes the loopback route's
   source address (commit `e3e3b90`). `--check` afterwards: `0 keys added`.
   Both clusters online; TimescaleDB 2.29.1. `17/main`'s configuration
   checksums were unchanged before and after. `pg_lsclusters` run as
   `manta` shows the tick cluster's owner as `<unknown>` (its data directory
   is mode 0700); as root it reads `postgres`.

2. **Rebuild the proof database and check the pipeline.**

   ```bash
   uv run python scripts/proof_226_tick.py rebuild
   ```

   Expected: 78 tier units ingested, 0 failed, 27,691,412 rows, coverage
   `ok` on every session, and the slowest unit ≤ 120 s. The report is
   written to `user/notes/<date>-226-proof-rebuild.md`.

   **Result:** 78/78 ingested, 0 failed, 27,691,412 rows, slowest unit 3.9 s,
   ingest 64 s, coverage ok on both ranges, estimate plans nothing.

3. **Measure.** Each step writes its own report:

   ```bash
   for s in mapping size jobs workers batch layouts queries; do
     uv run python scripts/proof_226_tick.py $s || break
   done
   ```

   Expected: mapping `100.00 %`, each table in Technical Decision 3
   filled in, and the layout step naming A or B by its rule.

   **Result:** run in the order mapping, size, jobs, workers, batch, queries,
   layouts. Mapping 100.00 %; layout A. `jobs` needs no tick cluster (free
   metadata calls) and lists jobs submitted since 2024-09-01: three earlier
   XNAS equities jobs have off-boundary ranges the job parser refuses.
   `layouts` leaves the table decompressed with layout B's settings until
   `tick_007` is applied (8.4).

4. **Contention.** Started in the background; it waits for the next Kalshi
   firing and spans three.

   ```bash
   uv run python scripts/proof_226_tick.py contention
   ```

   Expected: a report with two overlapped and one solo Kalshi firing, each
   duration set against the week's 133–324 s, and a verdict: `none
   measured` or `measurable contention`.

   **Result:** 20:48–23:25 UTC; overlapped 298 s and 286 s, solo 286 s;
   `none measured at 2 ingest workers`; no guard tripped. Caveat: nothing in
   the tick track may change in the checkout while it runs, because each
   loop iteration launches `mt data tick ingest`, whose preflight requires
   every tick migration to be applied. Interrupting the last iteration
   leaves only whole units (20,781,939 rows afterwards).

5. **Compressed-chunk tests and the bad-header fix.**

   ```bash
   uv run python scripts/run_tests.py integration -- \
     test/integration/data/test_tick_compressed.py \
     test/integration/data/test_tick_columnstore_migration.py \
     test/integration/data/test_tick_bad_header.py
   ```

   Expected: all pass. `test_tick_compressed.py` covers the five rows of
   Technical Decision 5; `test_tick_bad_header.py` covers verify and
   ingest continuing past a bad-header unit.

   **Result:** all pass, plus delivery and adopt continuing past a refused
   header (`test_tick_adopt.py`), added at the PM's direction.

6. **Production rebuild on the chosen layout.**

   ```bash
   uv run mt data migrate apply --track tick
   for d in /data/tick-archive/GLBX-*; do
     uv run mt data tick adopt --job-id "$(basename "$d")" --source "$d"
   done
   uv run mt data tick pass --estimate-only; echo "exit $?"
   time uv run mt data tick ingest
   uv run python scripts/proof_226_tick.py final
   ```

   Expected: migration through `tick_007`; `pass` plans nothing and
   projects the definitions (exit 0). If it plans anything, stop: that is a
   PM decision (Technical Decision 2). Ingest exits 0 with 78 ingested; `final` reports every eligible chunk
   compressed, Q1–Q4 ≤ 1 s, and coverage `ok` on both ranges. The rebuild's
   total wall time is the number 227 uses. Every command in this step is
   safe to re-run after an interruption: adopted jobs are skipped, and
   ingested units are not selected again.

   **Result:** migrate 1.0 s, six adopts 151.5 s, estimate 2.1 s (nothing
   planned), ingest 57.9 s: 212.5 s in all
   (`2026-10-03-226-proof-production-rebuild.md`). `final`: 14/14 chunks
   compressed (5.70 → 0.65 GiB), Q1–Q4 ≤ 258 ms, coverage ok.

7. **Status reads caught up.**

   ```bash
   uv run mt data tick pass --estimate-only
   uv run mt data tick status
   ```

   Expected: nothing to buy, and ES caught up over its configured range,
   the chosen tier named. The other tier's sessions are listed `complete`
   and do not count toward caught up. `coverage --start 2024-08-30 --end
   2025-01-01` reads `ok` on every session of both ranges (Technical
   Decision 9).

   **Result:** `verdict: nothing_to_buy`; `tier: tbbo, wanted from
   2024-11-02`, `caught up: yes`; coverage ok on all 87 sessions. Two
   corrections were needed: the wanted start is 2024-11-02 (the session
   dated 11-01 opens on 10-31, which is not held), and 225's `caught_up()`
   judged every session in scope, not the wanted range only (commit
   `8a9c47e`). Status still counts 2 `missing` and 1 `edge unknown`: edge
   sessions of the held ranges that touch unbought days, outside the wanted
   range.

8. **Tear down the proof database.**

   ```bash
   uv run python scripts/proof_226_tick.py drop-proof
   psql "$(. deploy/lib/env_value.sh; env_value .env MT_TICK_MAINTENANCE_URL)" \
     -Atc "SELECT datname FROM pg_database ORDER BY 1"
   uv run python scripts/proof_226_tick.py archive-check
   ```

   Expected: `trading_tick_proof` is gone. The archive is unchanged:
   `uv run python scripts/proof_226_tick.py archive-check` compares every
   file each job's `manifest.json` lists against its size and `sha256:`
   hash (`sha256sum -c` cannot parse that JSON).

   **Result:** dropped; `postgres`, `template0`, `template1`,
   `trading_tick`; 6 jobs, 170 files, 0 problems.

9. **Read the go/no-go.** `user/analysis/226-analysis.tick-proof-go-no-go.md`
   has a recommendation for each of the four decisions, and every number in
   it names its report.

   **Result:** GO; tier tbbo, spreads included, GC follows at tbbo, Standard
   plan a technical go.

## Risk Assessment

### Technical Risks

- **Compressed chunks may not support the shipped paths as designed.** The
  unique-key enforcement, the delete inside a `COPY` transaction, or
  `tick_app`'s rights on internal compressed tables may behave differently
  from the documentation.
- **Memory on the host.** The host carries production, the dev checkout,
  and a desktop. A second cluster's 4 GB of shared buffers is real memory.
  `Committed_AS` is close to `CommitLimit` (62.7 of 66.4 GiB on 2026-10-03),
  but overcommit is heuristic (`0`), so that limit is not enforced.
  `MemAvailable` is the measure that counts.

### Mitigation Strategies

- The compressed-chunk tests run on the test cluster before anything
  touches `trading_tick`. A failure is fixed in 225's code or with a grant,
  inside this slice, never by dropping compression. If a path cannot work
  on compressed chunks, the design falls back to compressing only chunks
  past a settled age, and the go/no-go records it.
- The contention step stops at a `MemAvailable` floor, records it every 5
  seconds, and the go/no-go confirms or lowers `shared_buffers` from it.
  Only the tick cluster restarts if it changes.

## Implementation Notes

### Development Approach

1. **The bad-header fix.** It is independent and small, and it removes a
   way for the harness's runs to abort.
2. **The provisioning script.** shellcheck, then a dry run (`--check`,
   which prints what it would do without root), then the PM runs it.
3. **The harness**, `rebuild` first, so the baseline is on the production
   host before anything is tuned. Then the measurement steps.
4. **Contention**, in the background, while the next items proceed.
5. **Layout decision → `tick_007` → compressed-chunk tests** on the test
   cluster.
6. **Constants re-set** from the reports.
7. **The production rebuild and `final`.**
8. **The go/no-go, `TICK_UNIVERSE`, and docs.** Drop the proof database
   last.

Testing: unit tests for the header cases, the harness guard and the `.env`
writer. Integration tests for the compressed-chunk paths and the bad-header
passes. The harness itself is a measurement, verified by the walkthrough,
not by a test of its numbers.

If the task breakdown does not fit one implementation session, split it
here: (a) the fix, the cluster, the harness and the uncompressed
measurements; (b) the layout, `tick_007`, the compressed-chunk tests, the
production rebuild and the go/no-go.

### Special Considerations

- **The production cluster is off limits.** No restart and no configuration
  change. The provisioning script reads `17/main`'s state only to compare
  versions and to avoid its port.
- **No timer is touched.** The contention step reads the Kalshi timer's next
  elapse and `pass_runs`; it never starts or stops a unit.
- **Archive files are read only.** Every step opens them read-only, and the
  walkthrough re-checks their SHA-256 at the end.
- **No purchase.** Every pass runs `--estimate-only`. If an estimate plans
  anything (a definition job missing from the archive), the step stops and
  reports it, and buying is the PM's call against `trading_tick`. Nothing
  works around it.

### Architecture and plan statements this design supersedes

- **Contention against the minute pass** (plan entry 226, architecture
  "Cross-source arbitration"): measured against the Kalshi pass here; the
  minute-pass overlap moves to 233 (PM, 2026-10-03).
- **"Typical batch-job duration and poll interval … measured on the first
  batch job the pass submits"** (architecture): measured from every account
  job's provider record (free), since 224's jobs and 220's seven are the
  submissions there are.
- **The decode benchmark re-run on purchased data** (220's handoff): the
  `workers` step measures the whole ingest at 1, 2 and 4 workers, which
  answers the threads question on real volume. The decode-only benchmark
  is not re-run.
- **Production tick-cluster provisioning** (224's noted gap, held by 225):
  lands here.
- **Future Work item 3, intra-unit checkpoints:** closed with the measured
  per-unit time, unless that time misses its bound.
