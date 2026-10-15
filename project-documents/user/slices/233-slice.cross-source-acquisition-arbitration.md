---
docType: slice-design
slice: cross-source-acquisition-arbitration
project: trading-data
parent: user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md
dependencies: [226-slice.proof-on-existing-data]
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
initiative: set resource limits on the slice, and choose one of the 900
plan's three mechanisms. Those are a resource-class lock the passes take, a
scheduler inside `mt`, or keeping calendar stagger.

This slice does three things:

1. **Sets the one limit this host can enforce.** `MemoryMax` goes on
   `manta-acquisition.slice`, derived from the journal's measured memory
   peaks. `CPUWeight` and `IOWeight` are not set, because on this host
   neither would have any effect (Technical Decision 1).
2. **Keeps calendar stagger,** on evidence already in hand: 226's Kalshi ×
   tick result, 24 production firings in which Kalshi overlapped a heavy
   minute pass, and the host facts in Technical Decision 1. No new contention
   run is built (Technical Decision 3).
3. **Closes 900 Future Work item 4,** and names what reopens it.

**Scope cut (PM, 2026-10-14).** The plan entry asked for weights, a minute ×
tick contention run, and a re-run under the chosen setting. All three are
dropped. The weights are inert here. The re-run has nothing to show without
them. The contention run would cost a scratch server and four scripts to
confirm an expected "no contention", and stagger keeps the minute and tick
passes apart in any case (Technical Decision 3). The slice plan entry is
updated to match.

**Cutover timing (decided):** the install is held for the release that
follows 234. 233 closes with the derivation script, the unit file, the drift
guard and the documents. It builds no cutover script. 234's cutover installs
the file and verifies the bound (issue #33, Technical Decision 5).

## Value

- **A runaway pass can no longer starve production PostgreSQL.** Today
  nothing bounds the acquisition passes' memory. The production cluster
  holds about 100 GiB of resident memory and page cache. A leak in any pass
  (or in 234's tick pass) could push the host into the global OOM killer.
  With `MemoryMax`, such a failure is killed inside its own slice.
- **Nothing inert gets installed.** On this host a weight on this slice
  would look like protection and do nothing. The unit file says why it is
  not set, so a later reader does not "fix" it.
- **234 inherits a decided rule.** Production wiring knows where a tick pass
  unit goes, how to add its memory peak to the bound, and how to pick a
  firing time if it gets a timer. It needs no new mechanism.

## Technical Scope

**Included**

1. **The memory-bound derivation** (`scripts/acquisition_memory_bound.py`).
   Members are the units whose shipped unit file declares
   `Slice=manta-acquisition.slice`. For each one, it reads the peak memory
   from the journal over the last 30 days and applies the rule in Technical
   Decision 2. It prints the members, their peaks and the result, and writes
   nothing. The name is not slice-numbered, because the runbook's
   add-a-source step runs it every time a unit joins the slice.
2. **`deploy/systemd/manta-acquisition.slice`** gains `MemoryMax=` with a
   comment that states the derivation and why `CPUWeight` and `IOWeight` are
   absent. It replaces the current "deliberately carries no resource
   settings" text.
3. **A drift guard** in `test/unit/deploy/test_units.py`. The slice declares
   a parseable `MemoryMax`, and every unit in `deploy/systemd/` that names
   the slice is a `Type=oneshot` pass.
4. **Documents** (Technical Decision 4).

**Excluded**

- **Any contention run,** including minute × tick (Technical Decision 3).
- **A resource-class lock or a scheduler inside `mt`** (Technical
  Decision 3).
- **`CPUWeight`/`IOWeight` anywhere,** including a drop-in on
  `postgresql@17-tick.service` (Technical Decision 1).
- **Enabling the kernel's `io.cost` controller or changing the NVMe
  schedulers.** That is host-kernel tuning for a contention nobody has seen.
- **Tick pass units, timers, or a tick firing time.** All of these are
  234's.
- **The Saturday 6.2 GiB minute-pass peak itself.** It is recorded and
  bounded, not investigated (Special Considerations).

## Dependencies

### Prerequisites

- **226 (complete):** the Kalshi × tick result: none measured, with CPU
  busy at 9.8 % median and 15.4 % p95 under overlap.
- **916 (complete):** the slice and the production units.
- **No sudoers change and no PM host step.** Nothing is installed in this
  slice (Technical Decision 5).

### Interfaces Required

- **`journalctl -u <unit> -o cat --since -30d`**, read only, for the
  `Consumed … memory peak` lines of each member.
- **`deploy/systemd/*.service`**, read from the checkout, for member
  discovery.

## Architecture

### Component Structure

```
scripts/acquisition_memory_bound.py   members from unit files · journal peaks · the rule · prints
test/unit/scripts/test_acquisition_memory_bound.py   parser on real journal lines · rule · discovery
test/unit/deploy/test_units.py        drift guard on the slice file
deploy/systemd/manta-acquisition.slice   MemoryMax= plus its comment
```

The parser and the rule are pure functions. The journal read is the only
I/O, and it sits at the entry point.

### Data Flow

```
deploy/systemd/*.service ──► members (Slice=manta-acquisition.slice)
journalctl per member     ──► max 30-day "memory peak"
                                    │
                         MemoryMax = ceil_GiB(2 × Σ peaks)
                                    │
                         printed derivation ──► copied by hand into the slice file,
                                                with the derivation in its comment
```

### State Management

- **Production:** untouched. The script only reads the journal.
- **Host configuration:** one file, `manta-acquisition.slice`, installed by
  `install-production.sh` at 234's release. systemd applies a changed
  slice's properties on `daemon-reload`, which the installer runs. **The
  slice is never restarted**, because that would stop every pass in it.

## Technical Decisions

#### Technical Decision 1: only `MemoryMax`; no `CPUWeight` or `IOWeight`

The baseline was measured read-only on 2026-10-14.

| Fact | Seen | Consequence |
|---|---|---|
| The slice's cgroup | `/manta.slice/manta-acquisition.slice`, the only child of `manta.slice` | Weights divide a parent's share among siblings. With no sibling, a weight on this slice divides nothing. |
| Where the database work runs | `system.slice/system-postgresql.slice/postgresql@17-{main,tick}.service` | A pass's write load is mostly spent in its cluster's backends, which are outside the acquisition slice. |
| NVMe schedulers | `nvme0n1 [none]`, `nvme1n1 [none]` | `io.weight` needs BFQ or the `io.cost` controller. |
| `io.cost` | `/sys/fs/cgroup/io.cost.qos` and `io.cost.model` present but empty: no device configured (corrected 2026-10-14; first read said "absent") | Neither is active, so `IOWeight` is inert on every unit. |
| Devices | `nvme0n1`: `/` and production `17/main`; `nvme1n1`: `/data` (`17/tick`, the archive, backups) | The minute and tick write paths share no device. 226 confirmed this: production's device stayed flat under tick load. |
| CPU | Ryzen 9 5950X, 32 threads; 226 under tick load: 9.8 % / 15.4 % busy (median / p95) | Weights are work-conserving and act only under saturation. There was none to arbitrate. |
| Memory | 125.7 GiB; production cluster ≈ 100 GiB resident plus cache; slice members unbounded | This is the one shared resource where a single bad pass can hurt production, and `MemoryMax` bounds it. |

**Where the weights came from.** 916 left the slice empty and named all
three settings as the standard knobs to add once contention was measured.
Nobody had checked whether this host could use them, and the plan and
architecture inherited "set all three" as the expected outcome. PM decision,
2026-10-14: no weights.

#### Technical Decision 2: the `MemoryMax` rule

```
MemoryMax = ceil_GiB( 2 × Σ over slice members of max 30-day journal memory peak )
```

- **Members** are discovered from `deploy/systemd/*.service` files that
  declare `Slice=manta-acquisition.slice`, never listed by hand. Today they
  are minute, daily and Kalshi. Health, accounting and serve run in
  `system.slice`.
- **The sum** assumes every member peaks at once, which is the worst case;
  the 2× covers growth. The journal's `memory peak` is cgroup memory,
  including page cache, so it overstates resident memory. That errs toward
  a larger bound.
- **No tick term.** 234's unit does not exist yet. When 234 adds it, the
  unit becomes a member and its peak joins the sum through the add-a-source
  step.
- **Current inputs**, from journal peaks since 2026-09-11 (the uptime
  window): minute 6.2 G (Saturdays; other days 75–144 M), daily 426 M,
  Kalshi 317 M. That gives ≈ 6.9 GiB → 14 GiB. The script computes the
  final value, and the unit file's comment records the inputs and the date.
- **A member with no peak line** in the window fails the derivation. No
  zero or default is used in its place.
- **What happens at the bound.** Page cache is reclaimed first. Only
  anonymous memory beyond the bound triggers the slice-local OOM killer,
  which picks the largest process. The alternative is the global OOM killer
  choosing among production's PostgreSQL backends. The killed pass is closed
  as abandoned by the existing `pass_runs` mechanism (`close_abandoned`),
  and its unit fails. `mt-run status` and `systemctl --failed` show it like
  any other failed pass.
- **Accepted tradeoff: one budget for the whole slice.** The OOM killer
  picks by size, not by fault, so in principle it can kill a pass other than
  the leaking one. At a bound of 2 × the summed peaks, a runaway must grow
  past every member's worst case before the bound bites, so it is the
  largest process when the kill comes. A per-unit `MemoryMax` would isolate
  each pass more tightly, but it means one derived value per unit file
  against one value today. Reopen this if an `oom_kill` ever hits a unit
  other than the leaking one.

#### Technical Decision 3: keep calendar stagger; no lock, no scheduler, no new measurement

The evidence:

1. **Kalshi × tick (226):** none measured. Overlapped durations were 298 and
   286 s against a weekly maximum of 324 s, and tick throughput did not move.
2. **Kalshi × heavy minute (production history, 30 days, read 2026-10-14):**
   24 Kalshi firings overlapped a heavy minute pass: median 223 s, p95
   254 s, max 264 s. The 696 solo firings: median 234 s, p95 280 s, max
   427 s. Overlapping a long minute pass has never slowed Kalshi.
3. **Minute × tick, by construction:** the two write to different clusters
   on different devices (Technical Decision 1), and the host runs at about
   10–15 % CPU under tick load.

**Why minute × tick is not measured.** Stagger is the kept mechanism, so a
scheduled tick pass is placed so it does not overlap the minute pass at all
(234's firing-time rule, Integration Points). With no planned overlap, a
measurement of the overlap answers a question nobody is asking. A manual
tick pass run during a minute pass is an operator choice, and the evidence
above says it is harmless.

**The alternatives.** A resource-class lock would serialise passes that do
not contend. It would also make a Kalshi firing wait behind an 88-minute
Saturday minute pass, which costs freshness for no gain. A scheduler inside
`mt` brings back the long-running daemon form that 916 declined, with
nothing for it to schedule.

The existing stagger stays for its real reason: provider quota. The minute
and daily passes share EODHD's allowance, which is why they start 30 minutes
apart.

**Reopen triggers**, written into the 900 plan item and the runbook:

- a pass that slows measurably when it overlaps another, seen in
  `pass_runs` durations;
- a `Type=simple` streaming source (the realtime initiative). A continuous
  load cannot be staggered.

#### Technical Decision 4: documents that change

- **Slice plan, 233 entry:** rewritten to the scope above (done with this
  design, 2026-10-14).
- **Architecture Revision Log, entry for 233:** "Cross-source arbitration".
  `MemoryMax` only; the weights are not set, for the reasons in Technical
  Decision 1. The mechanism is calendar stagger, kept on existing evidence.
- **900 plan, Future Work item 4:** marked closed by 233, with the decision
  and the two reopen triggers in one sentence each.
- **Runbook 100:**
  - The unit table row for `manta-acquisition.slice` changes from "carries
    no resource limits yet" to the bound and where it comes from.
  - The add-a-source checklist gains a step: a unit that joins the slice
    re-derives `MemoryMax` with `scripts/acquisition_memory_bound.py`.
  - The second "limit of the pattern" is rewritten. Stagger is kept, for
    quota reasons, and the bullet names the reopen triggers.
- **CHANGELOG.**

#### Technical Decision 5: the install and its check ride 234's cutover

**Timing.** `install-production.sh` installs the whole checkout at a ref.
Production `/opt` is at `v0.19.0`, 351 commits behind main, so any install
is a production code upgrade. The PM already plans one release after 233
and 234, so the one-line bound rides that release. That also puts 234's
tick unit, and its peak, in the same derivation before anything is
installed.

**Deferred to 234's cutover** (issue #33; recorded in 234's slice plan
entry): after the install, read the slice cgroup's `memory.max` and
`memory.events`, and fail on a `memory.max` that differs from the declared
value or on any `oom_kill`. **Until 234 ships, the bound is declared but
inert:** the unit file in the repo carries it, and production runs without
it.

### Patterns and Conventions

- `MEMORY_BOUND_HEADROOM = 2` and `JOURNAL_WINDOW_DAYS = 30` are named
  constants whose docstrings cite this design.
- The journal parser is lenient about format. It reads the
  `<size> memory peak` clause wherever it appears in a `Consumed …` line,
  because systemd versions differ in what precedes and follows it (CPU,
  wall clock, swap peak). The unit-test fixture uses real lines from this
  host's journal, including the 1h-plus wall-clock form.
- Exceptions follow the project rule. A failed `journalctl` call raises
  with its stderr; nothing catches broadly.

## Implementation Details

### API Contracts

No product surface changes: no CLI verb, endpoint, or schema. On "does
this belong in the API?": no. The bound is host configuration, and a pass
killed by it already shows on existing surfaces as a failed or abandoned
run.

| Invocation | Does | Exit |
|---|---|---|
| `uv run python scripts/acquisition_memory_bound.py` | Members, their 30-day peaks, the result in GiB. | 0 / 2 a member's journal could not be read or has no peak line |

## Integration Points

### Provides to Other Slices

- **234 (Tick Pass Production Wiring):**
  - the tick pass unit sets `Slice=manta-acquisition.slice`;
  - adding the unit re-runs `acquisition_memory_bound.py`, which adds the
    unit's peak to the bound;
  - only if the cadence decision gives the tick pass a timer: a firing time
    that does not overlap the minute pass's walk window, shares no start
    minute with existing timers, and stays outside the backup cron firings
    that share `nvme1n1` (`deploy/cron.d/manta-trading-backup.cluster`). A
    manual-only tick pass needs none of this.
- **The realtime initiative:** the reopen trigger for a coordinator, stated
  in the 900 plan and the runbook.
- **Every later source:** the add-a-source step that keeps the bound honest.

### Consumes from Other Slices

- **226:** the Kalshi × tick result, as evidence only.
- **916:** the slice and the unit files.

## Success Criteria

### Functional Requirements

1. `acquisition_memory_bound.py` prints three members (minute, daily,
   Kalshi) with their 30-day peaks and a result of about 14 GiB on
   manta9000.
2. `manta-acquisition.slice` declares `MemoryMax` equal to that result. Its
   comment records the inputs, the date, and why `CPUWeight`/`IOWeight` are
   absent.
3. Nothing is installed in this slice, so it claims only that the bound is
   declared and derived. That the bound is in force is checked by 234's
   cutover (issue #33).
4. Calendar stagger is the recorded mechanism, with its evidence and reopen
   triggers in the 900 plan and the runbook.

### Technical Requirements

- Unit tests (no host, no database) cover:
  - the journal memory-peak parser, on real lines from this host;
  - the `MemoryMax` rule, including a member with no peak line;
  - member discovery from the unit files;
  - the drift guard on the slice file.
- `ruff check`, `ruff format` (scoped to touched files), and the mypy
  invocation from project memory are clean. No change to `src/`, so the
  integration and load tiers are not required. Running the unit tier is.
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

### Verification Walkthrough

Run from the checkout root on manta9000.

1. **Derive the bound.**

   ```bash
   uv run python scripts/acquisition_memory_bound.py
   ```

   Expected: three members (minute, daily, Kalshi), each with its 30-day
   peak (minute about 6.2 G), and the result in GiB (about 14). Exit 0.
2. **Set the bound.** Write the result into
   `deploy/systemd/manta-acquisition.slice`, then run:

   ```bash
   uv run pytest test/unit/deploy/test_units.py test/unit/scripts/test_acquisition_memory_bound.py -q
   ```

   Expected: all pass.
3. **Check the file.**

   ```bash
   systemd-analyze verify deploy/systemd/manta-acquisition.slice
   ```

   Expected: no output about `MemoryMax`.

## Risk Assessment

### Technical Risks

- **The bound is too low for a pass not yet seen at its peak.** The
  Saturday minute peak dominates the sum and is covered by the 2× headroom.
  A new peak shape from 234's tick pass is covered by the add-a-source
  step, which re-derives the bound.

### Mitigation Strategies

- An OOM kill at the bound is detected by the `memory.events` check in
  234's cutover (issue #33, Technical Decision 5).

## Implementation Notes

### Development Approach

1. **Baseline** (read only): re-read the facts in Technical Decision 1 into
   the findings, and copy fixture lines from this host's journal for the
   parser test.
2. **The derivation script** with its unit tests.
3. **Unit file and drift guard** from the script's output.
4. **Documents:** runbook, 900 plan, architecture Revision Log, CHANGELOG.

### Special Considerations

- **Effort re-rated to 1/5** after the scope cut.
- **The Saturday 6.2 GiB minute-pass peak.** It occurs on every firing day
  and only then (other days: 75–144 M). It is probably the trailing phase's
  seeding of about 13k symbols' gap rows. It is recorded in the findings
  and drives the bound. Whether it is worth reducing is a question for the
  minute track, not for this slice.
