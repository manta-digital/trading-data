---
docType: slice-design
slice: cross-source-acquisition-arbitration
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226-slice.proof-on-existing-data, 228-slice.tick-restore-drill]
interfaces: [234-slice.tick-pass-production-wiring]
dateCreated: 20261014
dateUpdated: 20261014
status: not_started
---

# Slice Design: Cross-Source Acquisition Arbitration

## Overview

Slice 916 installed `manta-acquisition.slice` with no resource settings and
left cross-source arbitration as Future Work item 4 of the 900 plan. The
architecture ("Cross-source arbitration") assigns the answer to this
initiative in two steps: measure first, then set `IOWeight`/`CPUWeight`/
`MemoryMax` on the slice and choose one of the 900 plan's three mechanisms:
a resource-class lock the passes take, a scheduler inside `mt`, or keeping
calendar stagger. Slice 226 measured the Kalshi pass against a looping tick
ingest and found no contention at two ingest workers. It also stated that
this does not clear the minute pass, which walks 13,083 symbols. That
measurement moved here (PM, 2026-10-03).

This slice does three things:

1. **Measures the minute pass overlapped with the tick ingest loop**, now,
   on a weekday. It fires production's own minute unit by hand. On a day
   that is not a firing day, that unit does a backfill-only walk of the
   whole universe in about 190 s. The walk is database-bound and spends no
   EODHD quota. The tick load runs on a scratch PostgreSQL server under
   `/data`. Running it on the real tick cluster would ship throwaway WAL
   into the tick backup archive and on to B2 (Technical Decision 2).
2. **Sets what the host can actually enforce.** `MemoryMax` goes on
   `manta-acquisition.slice`, derived from measured peaks. `CPUWeight` and
   `IOWeight` are not set, because on this host neither would have any
   effect (Technical Decision 1).
3. **Keeps calendar stagger.** The decision rests on the measurement, on
   226's result, and on 24 recorded production firings in which Kalshi
   overlapped a heavy minute pass. The slice names what reopens the
   question, and closes 900 Future Work item 4.

The measurement is repeated after the slice file is installed. It shows the
bound is in force and that no pass under it is slowed or killed.

## Value

- **The operator gets an answer, not a deferral.** "Can a tick ingest run
  while production passes are running?" gets a measured yes or no. Until
  now that was known for Kalshi only.
- **A runaway pass can no longer starve production PostgreSQL.** Today
  nothing bounds the acquisition passes' memory. The production cluster
  holds about 100 GiB of resident memory and page cache. A leak in any pass
  (or in 234's tick pass) could push the host into the global OOM killer.
  With `MemoryMax`, such a failure is killed inside its own slice.
- **Nothing inert gets installed.** On this host, a weight on this slice
  would look like protection and do nothing. The design records why it is
  not set, so a later reader does not "fix" it.
- **234 inherits a decided rule.** Production wiring knows where a tick pass
  unit goes, what its memory peak adds to the bound, and that its firing
  time follows the existing stagger convention. It needs no new mechanism.

## Technical Scope

**Included**

1. **The minute × tick contention run** (`scripts/contention_233.py`). Each
   run does the following:
   - builds a scratch tick server and rebuilds it from the live archive;
   - fires one control (solo) minute walk;
   - starts the tick ingest loop and fires two overlapped minute walks;
   - samples the host, the production cluster, the tick loop and the
     slice's cgroup;
   - tears the scratch server down;
   - writes a report with a verdict.

   It reuses 226's sampler, tick loop and guarded reset, and 228's
   scratch-server lifecycle (Technical Decision 3).
2. **The memory-bound derivation**, in the same report. Members are the
   units whose shipped unit file declares `Slice=manta-acquisition.slice`.
   For each one, the run reads its peak memory from the journal over the
   last 30 days. It adds the tick ingest client's peak RSS, measured by the
   run, and applies the rule in Technical Decision 4.
3. **`deploy/systemd/manta-acquisition.slice`** gains `MemoryMax=` with a
   comment that states the derivation. The comment also records why
   `CPUWeight` and `IOWeight` are absent, replacing the current "deliberately
   carries no resource settings" text.
4. **A drift guard** in `test/unit/deploy/test_units.py`. The slice declares
   a parseable `MemoryMax` and no `CPUWeight`/`IOWeight`. Every unit in
   `deploy/systemd/` that names the slice is a `Type=oneshot` pass.
5. **The cutover script** (`scripts/cutover_233_arbitration.py`). It does
   the following in order:
   - installs the given ref through `cutover_common` (preflight, install);
   - verifies that the slice's `MemoryMax` is live in systemd and in
     `memory.max`;
   - re-runs the contention run with the bound in force;
   - checks the slice's `memory.events` for `max` and `oom_kill`;
   - writes the cutover report.
6. **Documents**:
   - Runbook 100: the unit table row; a new add-a-source step to re-derive
     the bound when a unit joins the slice; and the "two limits" bullet,
     rewritten from "arbitration does not exist yet" to the decision.
   - The 900 plan: Future Work item 4 closed with a pointer.
   - The 220 architecture's Revision Log, plus the slice plan's
     superseded-statements note (Technical Decision 6).
   - The CHANGELOG.

**Excluded**

- **A resource-class lock or a scheduler inside `mt`.** Rejected on the
  evidence (Technical Decision 5). If the minute run measures contention,
  the slice stops and goes to the PM instead (see "If contention is
  measured").
- **`CPUWeight`/`IOWeight` anywhere.** That includes a drop-in on
  `postgresql@17-tick.service`, which would be the only placement where a
  CPU weight could act (Technical Decision 1).
- **Enabling the kernel's `io.cost` controller or changing the NVMe
  schedulers.** That is host-kernel tuning for a contention nobody has
  measured.
- **Overlapping a Saturday (firing-day) minute pass.** The trailing phase
  runs only on a firing day. It is paced by the provider (about 75 s per
  250 symbols, 5 CPU-minutes over 88 minutes), so host contention cannot be
  what limits it. Waiting for Saturday is a wait-blocked task. The weekday
  walk is the database-bound part, where contention would show. The next
  Saturday pass under the bound is an observation for normal monitoring,
  not a task.
- **Tick pass units, timers, or a tick firing time.** All of these are
  234's.
- **The Saturday 6.2 GiB minute-pass peak itself.** It is recorded and
  bounded, not investigated (Special Considerations).

## Dependencies

### Prerequisites

- **226 (complete):** the Kalshi × tick result (none measured; CPU busy
  9.8 % median and 15.4 % p95 under overlap), and the harness modules this
  slice reuses: `scripts/proof_226/contention_sampler.py`, the `TickLoop`
  in `scripts/proof_226/contention.py`, and the guarded
  `reset_proof_database`.
- **228 (complete):** the scratch-server pattern: a socket-only `pg_ctl`
  server under `/data/restore-test`, run as `manta`; directory create,
  assert and remove; stop on every exit path. Also the archive-rebuild
  command sequence (`drill_228_rebuild.rebuild_commands`).
- **227 / 232 (complete):** the archive at `/data/tick-archive` holds the ES
  and GC tbbo jobs. Rebuilding from it is the tick load.
- **916 / 921 / 922 (complete):** the production units, the narrow
  `manta-ops` sudoers list (blocking and `--no-block` start of
  `mt-minute-pass.service`, the install script), `cutover_common`, and
  `pass_runs`.
- **No sudoers change and no PM host step** apart from running the cutover
  script (see Special Considerations about what that install deploys).

### Interfaces Required

- **Production `pass_runs`** (read only, 5 s statement timeout, the same
  posture as 226): minute and Kalshi rows for the 30-day baselines and for
  each fired walk's duration and outcome.
- **systemd, read only:** `systemctl show` (`ActiveState`,
  `NextElapseUSecRealtime` of `mt-kalshi-pass.timer`, `MemoryMax`), and
  `journalctl -u <unit> -o cat --since -30d` for the
  `Consumed … memory peak` lines.
- **cgroup v2 files, read only, no root:**
  `/sys/fs/cgroup/manta.slice/manta-acquisition.slice/memory.{current,peak,max,events}`
  and `/sys/fs/cgroup/io.cost.qos`. The slice's path is read from
  `systemctl show -p ControlGroup`, never typed.
- **One write to production:** the minute pass's own run rows, written by
  the minute pass itself when the run fires it. That is the same as any
  timer firing.

## Architecture

### Component Structure

```
scripts/contention_233.py                 entry: --check | run | --label after-install
scripts/contention_233_scratch.py         initdb + conf + start/stop of the scratch tick server
scripts/contention_233_run.py             the firing schedule, guards, verdict (pure, unit-tested)
scripts/contention_233_memory.py          journal-peak parse + MemoryMax rule (pure, unit-tested)
scripts/contention_233_report.py          report writer
scripts/cutover_233_arbitration.py        install → verify bound → contention again → report

reused, unchanged:
  proof_226.contention_sampler            /proc + production pg_stat_database + tick rows/s
  proof_226.contention.TickLoop           reset → `mt data tick ingest`, until stopped
  proof_226.guard.reset_proof_database    the guarded reset (name = TICK_PROOF_DB_NAME)
  drill_228_lifecycle                     pg_ctl, drill-dir create/assert/remove, lock
  drill_228_rebuild.rebuild_commands      migrate → adopt each job → estimate → ingest
  cutover_common                          preflight, install, report log
```

Where a reused function's signature is tied to its slice's context object
(`rebuild_commands(ctx, jobs)` reads `ctx.restored_archive`), it is
generalised to take the archive path. It is not copied.

### Data Flow

```
preflight (read only)
  today not a firing day · today's 13:05 minute run COMPLETE, full progress, a walk
  minute unit inactive · MemAvailable ≥ floor · /data free ≥ floor · credits read
        │
scratch: initdb /data/restore-test/233-contention-<stamp>/pgdata (socket-only)
         settings copied from 17/tick via SHOW · roles + database via provision_tick_roles.sql
         rebuild from /data/tick-archive  ──►  "tick alone" rate
        │
control walk   sudo systemctl start mt-minute-pass.service   (blocking; solo)
        │      sampler every 5 s · duration from pass_runs · must be < WALK_CEILING
        │
TickLoop start (reset → ingest, repeated) ─┐
overlapped walk 1   (clear window)          │  sampler every 5 s, slice memory.current
overlapped walk 2   (clear window)          │
TickLoop stop  ◄───────────────────────────┘  child peak RSS via getrusage(RUSAGE_CHILDREN)
        │
teardown (finally): stop scratch server · remove the run's directory
        │
report  project-documents/user/notes/<date>-233-contention-minute[-after-install].md
        durations vs control and 30-day walk max · series overlapped / solo / tick alone
        memory-bound derivation · credits before/after · verdict
```

A **clear window** means `mt-kalshi-pass.service` is inactive and the Kalshi
timer's next elapse is at least `CLEAR_WINDOW` away. Overlap with Kalshi was
already measured (226, and the history in Technical Decision 5), so keeping
it out isolates minute × tick. The 00:35/12:35 daily and 13:05 minute
firings are excluded by the preflight's time-of-day bounds.

### State Management

- **Production:** the run adds three minute `pass_runs` rows and walk
  anchors, the same as three firings. It writes nothing else.
- **Scratch:** everything else lives in the run's own directory under
  `/data/restore-test/` and is removed in `finally`. A leftover from a
  killed run is swept on the next start by 228's `sweep_leftovers` pattern,
  matched on the `233-contention-` prefix only.
- **Host configuration:** one file, `manta-acquisition.slice`, installed by
  `install-production.sh`. systemd applies a changed slice's properties on
  `daemon-reload`, which the installer runs. The cutover verifies this by
  reading `memory.max`. **The slice is never restarted**, because that would
  stop every pass in it.

## Technical Decisions

### Technology Choices

#### Technical Decision 1: only `MemoryMax`; no `CPUWeight` or `IOWeight`

The baseline was measured read-only on 2026-10-14.

| Fact | Seen | Consequence |
|---|---|---|
| The slice's cgroup | `/manta.slice/manta-acquisition.slice`, the only child of `manta.slice` | Weights divide a parent's share among siblings. With no sibling, a weight on this slice divides nothing. |
| Where the database work runs | `system.slice/system-postgresql.slice/postgresql@17-{main,tick}.service` | A pass's write load is mostly spent in its cluster's backends, which are outside the acquisition slice. |
| NVMe schedulers | `nvme0n1 [none]`, `nvme1n1 [none]` | `io.weight` needs BFQ or the `io.cost` controller. |
| `io.cost` | `/sys/fs/cgroup/io.cost.qos` absent | Neither is active, so `IOWeight` is inert on every unit. |
| Devices | `nvme0n1`: `/` and production `17/main`; `nvme1n1`: `/data` (`17/tick`, the archive, backups) | The minute and tick write paths share no device. 226 confirmed this: production's device stayed flat under tick load. |
| CPU | Ryzen 9 5950X, 32 threads; 226 under tick load: 9.8 % / 15.4 % busy (median / p95) | Weights are work-conserving and act only under saturation. There was none to arbitrate. |
| Memory | 125.7 GiB; production cluster ≈ 100 GiB resident plus cache; slice members unbounded | This is the one shared resource where a single bad pass can hurt production, and `MemoryMax` bounds it. |

`CPUWeight` could act only on siblings that both carry load. Those are the
two cluster units, or a future tick pass unit beside the minute and Kalshi
units inside the slice. Neither placement is justified without a measured
CPU contention. A drop-in on `postgresql@17-tick.service` would also slow
the API's tick reads, and the installer's unit list does not carry it. The
reopen trigger is a "measurable contention" verdict from this run, or from
234's run once a tick pass unit exists.

#### Technical Decision 2: the tick load runs on a scratch server, not on `17/tick`

226 looped on `trading_tick_proof` inside `17/tick`. Since 227 that cluster
archives WAL and ships it offsite. A looping rebuild would push gigabytes of
throwaway WAL per iteration into `/data/backup/17-tick` and then to B2. It
would also need superuser to recreate a database there, and 227 TD5 retired
the proof database. The scratch server needs none of that:

- it is created by `initdb` as `manta` with the TimescaleDB preload, under
  `/data/restore-test/233-contention-<stamp>/`, listening on a socket in
  that directory only;
- the memory and WAL settings that shape ingest are copied by name from
  `17/tick` through `SHOW` on the tick URL (`shared_buffers`, `work_mem`,
  `maintenance_work_mem`, `effective_cache_size`, `max_wal_size`,
  `wal_compression`, `max_worker_processes`, and
  `timescaledb.max_background_workers`). A setting that cannot be read
  fails the run; nothing is typed in its place;
- it uses the same binaries, the same device (`nvme1n1`) and the same
  ingest command, with the same worker count (`TICK_INGEST_WORKERS`) and
  batch budget.

**Known difference, reported, not modelled.** The scratch server does not
archive WAL. On `17/tick`, archiving adds a `zstd -T2` compression of every
16 MB segment. At 226's write rate that is under one core of 32. The report
states this beside the verdict.

The scratch database is named `TICK_PROOF_DB_NAME`, so 226's guarded reset
admits it unchanged. It is reachable only through the run's own socket path,
and the run never calls `load_proof_urls`, which reads the retired `.env`
keys. The `ProofUrls` it hands to `TickLoop` are built from the socket path.
This double guard (database name and private socket) is what lets
`TRUNCATE` run there (`sql.md`: a database the current process created).

#### Technical Decision 3: the minute load is production's own unit, fired on a weekday

`sudo systemctl start mt-minute-pass.service` is already in `manta-ops`.
On a day `MT_MINUTE_FIRING_DAYS` (currently `Sat`) does not name, the pass
runs backfill only. Over 30 days, Tuesday to Friday, those runs walked
13,083/13,083 symbols in 185–216 s, `COMPLETE`, at about 22 CPU-seconds
each. Sunday and Monday runs are catch-up passes (1,031–6,450 s) and are
refused by the preflight.

- **Walk:** a minute run that ends `COMPLETE` with full progress in under
  `WALK_CEILING_SECONDS = 600`. The 30-day history has walks at 185–216 s
  and the shortest catch-up at 1,031 s; 600 sits between them with margin on
  both sides. The constant's docstring cites those numbers.
- **The preflight requires** today's 13:05 run to have been a walk. The
  backfill rows are then drained, so further walks fetch next to nothing.
  The run records EODHD credits before and after.
- **The control walk must itself be a walk.** If it is not (backfill rows
  appeared), the run stops before any overlapped firing, reports "not a walk
  day", and exits non-zero. A second, quota-spending pass never runs.
- **Verdict.** An overlapped walk longer than
  `max(control, 30-day walk max)` is **measurable contention**. Otherwise
  the verdict is **none measured at `TICK_INGEST_WORKERS` workers**. This is
  226's rule, applied to the minute pass.

**Guards** (constants in `contention_233_run.py`; the first two are 226's
floors, imported):

| Condition | Action |
|---|---|
| `MemAvailable` < 16 GiB, or `/data` free < 50 GB | Stop the loop at once, report the trip, exit non-zero. If the first sample is already below a floor, exit before any load. |
| An overlapped walk still running at 2 × the walk bound | Stop the loop and record the trip. The pass is production's and is left alone. |
| No clear window within `WINDOW_HORIZON` | Exit non-zero without firing. |
| Interrupted or raised | `finally`: stop `TickLoop` (SIGINT, then settle), stop the scratch server, remove the directory. A minute pass already started finishes on its own. |

#### Technical Decision 4: the `MemoryMax` rule

```
MemoryMax = ceil_GiB( 2 × ( Σ over slice members of max 30-day journal memory peak
                            + tick ingest client peak RSS ) )
```

- **Members** are discovered from `deploy/systemd/*.service` files that
  declare `Slice=manta-acquisition.slice`, never listed by hand. Today they
  are minute, daily and Kalshi. Health, accounting and serve run in
  `system.slice`.
- **The sum** assumes every member peaks at once, which is the worst case;
  the 2× covers growth. The journal's `memory peak` is cgroup memory,
  including page cache, so it overstates resident memory. That errs toward
  a larger bound.
- **The tick term** is included now, so 234 can put its pass unit in the
  slice without changing the bound unless its own measured peak differs.
- **Current inputs**, from journal peaks since 2026-09-11 (the uptime
  window): minute 6.2 G (Saturdays; other days 75–144 M), daily 426 M,
  Kalshi 317 M. Before the tick term, that gives ≈ 6.9 GiB → 14 GiB. The
  run computes the final value, and the unit file's comment cites the
  report.
- **What happens at the bound.** Page cache is reclaimed first. Only
  anonymous memory beyond the bound triggers the slice-local OOM killer,
  which picks the largest process. That is the runaway: the alternative is
  the global OOM killer choosing among production's PostgreSQL backends.
  The killed pass is closed as abandoned by the existing `pass_runs`
  mechanism (`close_abandoned`), and its unit fails. `mt-run status` and
  `systemctl --failed` show it like any other failed pass.

#### Technical Decision 5: keep calendar stagger; no lock, no scheduler

The evidence:

1. **Kalshi × tick (226):** none measured. Overlapped durations were 298 and
   286 s against a weekly maximum of 324 s, and tick throughput did not move.
2. **Kalshi × heavy minute (production history, 30 days, read 2026-10-14):**
   24 Kalshi firings overlapped a heavy minute pass: median 223 s, p95
   254 s, max 264 s. The 696 solo firings: median 234 s, p95 280 s, max
   427 s. Overlapping a long minute pass has never slowed Kalshi.
3. **Minute × tick:** this slice's run. The design expects "none measured",
   because the two write paths use different devices and different
   clusters, and the host runs at about 10–15 % CPU under tick load.

**The alternatives.** A resource-class lock would serialise passes that do
not contend. It would also make a Kalshi firing wait behind an 88-minute
Saturday minute pass, which costs freshness for no measured gain. A
scheduler inside `mt` brings back the long-running daemon form that 916
declined, with nothing for it to schedule.

The existing stagger stays for its real reason: provider quota. The minute
and daily passes share EODHD's allowance, which is why they start 30 minutes
apart. That reason does not apply across providers.

**Reopen triggers**, written into the 900 plan item and the runbook:

- a "measurable contention" verdict from any later contention run (234 runs
  one when it adds the tick pass unit);
- a `Type=simple` streaming source (the realtime initiative). A continuous
  load cannot be staggered.

**If contention is measured.** The slice stops after the report and goes to
the PM with the numbers. The choice then is between a CPU weight on the
cluster units and the lock, which is a new decision, not a branch built in
advance here. `MemoryMax` still lands, because it does not depend on the
verdict.

#### Technical Decision 6: documents that change

- **Slice plan, "statements superseded by 233's slice design":** the 233
  entry's "sets `IOWeight`, `CPUWeight`, and `MemoryMax` on
  `manta-acquisition.slice`" becomes "sets `MemoryMax`". Weights on the
  slice have no sibling to act against, and `io.weight` is inert on this
  host (Technical Decision 1).
- **Architecture Revision Log, entry for 233:** "Cross-source arbitration".
  The weights are not set, for the reasons above. The mechanism is calendar
  stagger, kept. The tick load runs on a scratch server because `17/tick`
  archives WAL.
- **900 plan, Future Work item 4:** marked closed by 233, with the decision
  and the two reopen triggers in one sentence each.
- **Runbook 100:**
  - The unit table row for `manta-acquisition.slice` changes from "carries
    no resource limits yet" to the bound and where it comes from.
  - The add-a-source checklist gains a step: a unit that joins the slice
    re-derives `MemoryMax` with `scripts/contention_233.py --memory-bound`,
    which prints the derivation and loads nothing.
  - The second "limit of the pattern" is rewritten. Concurrency was measured
    to be harmless, stagger is kept for quota reasons, and the bullet names
    the reopen triggers.

### Patterns and Conventions

- Host scripts follow the established convention: check-then-act, expected
  against seen, a report under `project-documents/user/notes/`, and exit 0
  only when every check passes (`cutover_265_trades.py`, `cutover_common`).
- `--check` runs the preflight only: no scratch server, no firing.
- Every threshold is a named constant whose docstring cites its measurement:
  `WALK_CEILING_SECONDS`, `CLEAR_WINDOW`, `WINDOW_HORIZON`,
  `MEMORY_BOUND_HEADROOM = 2`, `JOURNAL_WINDOW = 30 days`.
- The journal parser is lenient about format. It reads the
  `<size> memory peak` clause wherever it appears in a `Consumed …` line,
  because systemd versions differ in what precedes and follows it (CPU,
  wall clock, swap peak). The unit-test fixture uses real lines from this
  host's journal, including the 1h-plus wall-clock form.
- Exceptions follow the project rule. The `TickLoop` thread boundary already
  logs and records failures. Nothing else catches broadly.

## Implementation Details

### API Contracts

No product surface changes: no CLI verb, endpoint, or schema. On "does
this belong in the API?": no. The bound is host configuration, and a pass
killed by it already shows on existing surfaces as a failed or abandoned
run.

The harness's command line:

| Invocation | Does | Exit |
|---|---|---|
| `uv run python scripts/contention_233.py --check` | Preflight only. Prints each condition, expected against seen. | 0 ready / 1 not ready |
| `uv run python scripts/contention_233.py --memory-bound` | Derivation only: members, peaks, tick term from the latest report, the result. | 0 / 2 could not read the journal |
| `uv run python scripts/contention_233.py` | The full run. Writes `<date>-233-contention-minute.md`. | 0 none measured / 1 measurable contention / 2 guard trip or setup failure |
| `uv run python scripts/contention_233.py --label after-install` | The same run, with the report suffixed and the slice's `memory.events` checked. | As above; also 1 if `memory.events` `max` or `oom_kill` rose |
| `uv run python scripts/cutover_233_arbitration.py --ref <ref>` | `cutover_common` preflight and install, bound verification, the after-install run, then `<date>-233-cutover.md`. | 0 only when every step passes |

## Integration Points

### Provides to Other Slices

- **234 (Tick Pass Production Wiring):**
  - the tick pass unit sets `Slice=manta-acquisition.slice`;
  - the bound already includes a tick ingest client term;
  - adding the unit re-runs `--memory-bound`, and a full contention run if
    the unit makes the tick pass scheduled;
  - its firing time follows the stagger convention: no shared start minute
    with existing timers, and outside the backup window that shares
    `nvme1n1`.
- **The realtime initiative:** the reopen trigger for a coordinator, stated
  in the 900 plan and the runbook.
- **Every later source:** the add-a-source step that keeps the bound honest.

### Consumes from Other Slices

- **226:** sampler, tick loop and guarded reset, imported unchanged. If a
  reused module's behaviour no longer fits, it is fixed in place with a
  test, not forked.
- **228:** the scratch-server lifecycle and the rebuild command sequence.
- **916 / 921 / 922:** the minute unit, the sudoers entries, `pass_runs`,
  and `cutover_common`.
- **Failure handling:**
  - production unreachable at preflight: exit 2 before any load;
  - unreachable mid-run: the loop stops and a partial report marked
    incomplete is written (226's behaviour).

## Success Criteria

### Functional Requirements

1. The contention run completes on manta9000 on a weekday. The report
   shows one control walk and two overlapped walks, each set against the
   30-day walk distribution, plus the tick loop's iterations, its overlapped
   rate against the alone rate, and a verdict.
2. If the verdict is "none measured", calendar stagger is the recorded
   mechanism. If it is "measurable contention", the slice stops at the
   report and the PM decides (Technical Decision 5).
3. `manta-acquisition.slice` declares `MemoryMax` equal to the
   derivation's result. The unit file's comment cites the report and states
   why `CPUWeight`/`IOWeight` are absent.
4. After the cutover, the following hold:
   - `systemctl show manta-acquisition.slice -p MemoryMax` and the cgroup's
     `memory.max` both equal the declared value;
   - the after-install run's verdict is again "none measured";
   - the slice's `memory.events` `max` and `oom_kill` counters did not rise
     during the run.
5. The scratch directory is gone after every run, including an interrupted
   one. Production received only the minute pass's own run rows.
6. EODHD credits spent by all of the run's walks are reported. A walk that
   was not a walk stops the run before any overlapped firing.

### Technical Requirements

- Unit tests (no host, no database) cover:
  - the verdict rule;
  - clear-window selection, against the Kalshi timer and the active state;
  - the walk classifier;
  - the journal memory-peak parser, on real lines;
  - the `MemoryMax` rule and member discovery from the unit files;
  - the guard trips;
  - the drift guard on the slice file.
- `ruff check`, `ruff format` (scoped to touched files), and the mypy
  invocation from project memory are clean. No change to `src/`, so the
  integration and load tiers are not required. Running the unit tier is.
- Reused harness modules are imported, not copied. A signature
  generalisation (`rebuild_commands`) keeps 228's drill tests passing.
- Documents: runbook 100, the 900 plan item, the 220 architecture's Revision
  Log, the slice plan note, and the CHANGELOG.
- **Standing 220 obligations:**
  - Kalshi contract diff: no pass code changes, recorded as "none" in the
    findings.
  - Realtime paths: neither path is ruled out. A streaming source is named
    as the reopen trigger.
  - Data-correctness contract: no rows, since the slice touches no data
    path.

### Integration Requirements

- 234 can add its tick pass unit to the slice by following the runbook's
  add-a-source steps, with no arbitration decision left open.
- Minute, daily, Kalshi, health and accounting firings continue as
  scheduled throughout. The overview shows the three extra minute runs per
  contention run as ordinary `complete` runs.

### Verification Walkthrough

Run from the checkout root on manta9000, on a Tuesday to Friday after the
13:05 UTC minute pass has finished.

1. **Preflight.**

   ```bash
   uv run python scripts/contention_233.py --check
   ```

   Expected lines, each with expected and seen values:
   - not a firing day;
   - today's minute run is a walk (about 190 s, 13083/13083);
   - minute unit inactive;
   - MemAvailable and `/data` above their floors;
   - the Kalshi timer readable;
   - the tick settings readable from `17/tick`;
   - the archive present.

   Exit 0.
2. **Bound derivation alone.**

   ```bash
   uv run python scripts/contention_233.py --memory-bound
   ```

   Expected: three members (minute, daily, Kalshi), each with its 30-day
   peak (minute about 6.2 G), the tick term (or "no report yet" before the
   first run), and the result in GiB.
3. **The minute overlap.**

   ```bash
   uv run python scripts/contention_233.py
   ```

   Expected: scratch built and rebuilt, the control walk, the loop started,
   two overlapped walks, teardown. The report sits at
   `project-documents/user/notes/<date>-233-contention-minute.md`. The
   expected verdict is `none measured at 2 ingest workers`.
   `ls /data/restore-test` shows no `233-contention-*` directory afterwards.
4. **Set the bound.** Write the report's `MemoryMax` into
   `deploy/systemd/manta-acquisition.slice`, then run:

   ```bash
   uv run pytest test/unit/deploy/test_units.py -q
   ```
5. **Cutover**, after merge, on the merged commit, which must be on origin:

   ```bash
   uv run python scripts/cutover_233_arbitration.py --ref <sha>
   ```

   Expected: install OK; `MemoryMax=<n>G` seen in systemd and in
   `memory.max`; the after-install run's verdict `none measured`;
   `memory.events max 0 → 0, oom_kill 0 → 0`. The report sits at
   `<date>-233-cutover.md`. Exit 0.
6. **Spot check.**

   ```bash
   systemctl show manta-acquisition.slice -p MemoryMax -p MemoryPeak
   cat /sys/fs/cgroup/manta.slice/manta-acquisition.slice/memory.max
   uv run mt data overview
   ```

   Expected: the declared bound; the overview shows the extra minute runs
   as `complete` and every pass idle or running normally.

## Risk Assessment

### Technical Risks

- **The cutover installs all of main.** Production `/opt/manta-trading` is
  at `v0.19.0`, and main is 351 commits ahead. `install-production.sh` is
  the only root path for a unit file in `manta-ops`, and it installs the
  whole checkout at the ref. So 233's cutover is also the first production
  code upgrade since 923.
- **The bound is too low for a pass not yet seen at its peak.** The
  Saturday minute peak dominates the sum and is covered by the 2× headroom.
  A new peak shape from 234's tick pass is covered by the add-a-source
  step, which re-derives the bound.

### Mitigation Strategies

- **For the install:** the PM chooses the timing at slice review. Either
  233's cutover runs at merge, or it is held for the release that follows
  234 (project memory: one release after 233 and 234). In the second case,
  233 closes with the run, the unit file and the documents, and its cutover
  script runs at that release unchanged.
- `cutover_common` holds the minute timer during the install and verifies
  the installed commit. The cutover never restarts the slice. If
  `memory.max` does not show the value after `daemon-reload`, the script
  fails and says so. It does not try a remedy that would stop running
  passes.

## Implementation Notes

### Development Approach

1. **Baseline** (read only):
   - re-read the facts in Technical Decision 1 into the findings;
   - fixture lines from this host's journal for the parser test.
2. **Pure modules first:** `contention_233_memory.py` and
   `contention_233_run.py`, with unit tests.
3. **Scratch server:** `contention_233_scratch.py`. This includes the
   `rebuild_commands` generalisation with 228's tests kept green. A dry
   build-and-teardown with no minute firing proves the lifecycle and gives
   the "tick alone" rate.
4. **Entry and report:** the `--check` and `--memory-bound` modes, then the
   full run on a weekday afternoon. Commit after each section.
5. **Unit file and drift guard** from the report's numbers.
6. **Documents:** runbook, plans, architecture, CHANGELOG.
7. **Cutover script:** dry preflight (`--check`), then the cutover at the
   point the PM chose (Risk Assessment).

### Special Considerations

- **Effort re-rated to 3/5.** The plan's 2/5 assumed 226's proof database
  could be reused. Since 227 it cannot (Technical Decision 2), so the
  scratch server is new work, though most of its parts come from 228.
- **The Saturday 6.2 GiB minute-pass peak.** It occurs on every firing day
  and only then (other days: 75–144 M). It is probably the trailing phase's
  seeding of about 13k symbols' gap rows. It is recorded in the findings
  and drives the bound. Whether it is worth reducing is a question for the
  minute track, not for this slice.
- **Security:** the scratch server listens on a socket in a `0700`
  directory owned by `manta`, with `trust` auth for the local superuser
  only. It never listens on TCP and is removed at the end. No credential
  is written to `.env`.
