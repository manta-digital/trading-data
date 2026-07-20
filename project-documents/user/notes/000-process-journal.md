---
docType: notes
layer: project
project: trading-data
audience: [human, ai]
description: Append-only log of process decisions and design reasoning that has no home in other document types
dateCreated: 20260719
dateUpdated: 20260720
status: in_progress
---

# Overview

Each entry is an h2 heading `## YYYYMMDD — Title`, newest first, followed by
**Context** (what prompted it), **Decision** (what was settled), **Rationale**
(why), and optionally **Follow-ups** (issues/slices/docs affected). Entries are
written in timeless decision language — no session transcripts, no line numbers
that drift. When the file exceeds the standard size limit, split per
file-naming-conventions (`-1`, `-2`, …).

# Entries

## 20260720 — Row-scale claims require exact counts; `approximate_row_count` on compressed hypertables is unreliable even post-ANALYZE; ad-hoc prod aggregates require a statement_timeout

**Context:** `minute_ohlcv`'s row count bounced across four figures in two days:
~7.27 B (`approximate_row_count` post-ANALYZE, recorded by slice 166 as fact),
~918 M ("corrected" during slice 167 design from `SUM(minute_count)` over a
cagg that was, unknown at the time, ~79% under-materialized), ~1.2 B (a
planning-era anchor from the SP500-only / 24-month-cap scope), and finally
**4,405,379,285 — the exact `count(*)`**, which post-166 runs in ~1.3 s using
compressed-batch metadata. Each wrong figure came from trusting a derived or
estimated source because the exact count *used to be* prohibitively slow.
Verifying the exact figure with a full-table `GROUP BY extract(year ...)`
then caused a production incident: the expression GROUP BY cannot use batch
metadata, so it decompressed the entire table server-side; the client timed
out (which does not cancel the backend), follow-up queries stacked behind it,
a backend died ("server closed the connection unexpectedly"), and the server
required a reboot.

**Decision:** (1) The only authoritative row-scale source is an exact
`count(*)` — cheap now on a healthy chunk layout; `approximate_row_count` is
treated as order-of-magnitude only on compressed hypertables (it was still
~66% high *after* ANALYZE: 7.31 B vs 4.41 B), and a cagg-derived count is
valid only after cagg-vs-raw parity is verified. (2) Capacity planning uses
the corrected compressed floor: 75 GB TOAST ÷ 4.405 B rows ≈ **17 bytes/row**
(supersedes the ~10 bytes/row figure in the 20260719 chunk-interval entry,
which divided by the estimate). (3) Ad-hoc analytical queries against prod
run under `SET statement_timeout` sized to intent (seconds for probes,
minutes only deliberately); after any client-side timeout the server-side
backend is explicitly cancelled (`pg_cancel_backend`) before anything else is
run. (4) Full-table aggregates over expressions (anything the columnstore
cannot answer from batch metadata) are treated as decompress-everything
operations and bounded or windowed accordingly.

**Rationale:** Three independent readers (slice 166's docs, slice 167's
design, the PM's recollection) each held a different number for the same
table, and each source *looked* authoritative. Estimates and derived tables
drift silently; only the source table counts. The incident half of the lesson
is the operational mirror of the same error — treating a query as cheap
because a superficially similar one (metadata-assisted plain `count(*)`) was
cheap.

**Follow-ups:** Corrected figures in slices 163/167 designs and 140-plan
entries 23/27 (raw = 4.405 B exact; caggs ~79% under-materialized overall —
per-year 9.5–21% in 2019+, higher pre-2019; full uncompressed cagg
materialization ≈ 300 GB, not 500 GB). Tick-capacity math must use
17 bytes/row. `mt data cagg verify` (slice 163) makes the parity
precondition checkable. Daemon must be verified running after the
2026-07-20 server reboot.

## 20260719 — Stage-then-drop rewrite cycles must take the exclusive lock BEFORE the snapshot, and safety must come from the transaction, not from operational circumstance

**Context:** The slice 166 rechunk driver's window cycle was: snapshot the
window's rows into a temp table, `drop_chunks()`, reinsert, all in one
transaction, with a staged==reinserted rowcount guard. Code review (166
code-review F001) identified a silent-loss race the guard cannot catch: a
concurrent application writer (daemon, `mt data pull`, gap seeding) that
commits rows into the window *after* the stage snapshot but *before*
`drop_chunks` acquires its exclusive lock has those rows destroyed by the
drop and never reinserted — and both sides of the rowcount guard equally
exclude them, so the check passes. The production run happened to be safe
because the daemon was stopped and jobs were paused, but nothing in the tool
enforced that safety, and the tool is reusable.

**Decision:** Any stage-then-drop rewrite takes `LOCK TABLE <hypertable> IN
EXCLUSIVE MODE` as the **first statement of the window transaction, before
the stage snapshot**. EXCLUSIVE blocks all writers for the window's duration
(seconds) while leaving readers unaffected; a writer therefore lands either
wholly before the snapshot (and is staged) or wholly after the commit (and
writes into the new chunk) — never in between. Row-count guards are kept as
invariant checks but are **not** accepted as concurrency protection: a guard
that compares two views taken under the same snapshot cannot detect rows
that neither view saw. Operational preconditions ("daemon is stopped")
remain documented operator guidance but never substitute for the lock.
The lock's presence is proven by a deterministic test using an
`after_stage` injection seam that attempts a concurrent write inside the
critical span and asserts it is refused — concurrency claims get
deterministic tests via seams, not sleep-based races.

**Rationale:** This is the second silent-loss mechanism found in one slice
(after the cagg-refresh collision), and both share a shape: the danger
window is invisible to the code's own consistency checks, and the loss
leaves no error. Correctness against concurrent writers must be structural
(a lock ordering guarantee) rather than circumstantial (a process someone
remembered to stop), because the circumstance is exactly what erodes first
when a tool is reused months later by an operator who didn't read the
original slice.

**Follow-ups:** Implementation and test:
`market/maintenance/rechunk.py` (`_rewrite_window`), integration test
`test_concurrent_writer_blocked_during_window`; review record in
`166-review.code.*.md` (F001, Resolution). Related journal entry: the
background-job pause (cagg refresh corruption) — the two together define the
concurrency preconditions for any future chunk-restructuring tool,
including slice 163's cagg re-chunk.

## 20260719 — Chunk intervals must be sized against wall-clock span, not data volume; the archived tick schema's 1-hour interval would reproduce the minute_ohlcv pathology

**Context:** Slice 166's root-cause work established that `minute_ohlcv`'s
disease was purely *chunk count*: 25,256 four-hour chunks over 22.5 years made
query **planning** — not execution — the dominant cost (846 s planning vs
4.2 s execution on a trivial single-symbol MIN/MAX; ~7 catalog locks per chunk,
176k total). Per-chunk data volume was irrelevant to the failure: chunks
averaged only ~5 MB. Separately, the storage post-mortem corrected an earlier
belief: after rebuilding compression batches at proper size, `minute_ohlcv`'s
7.27 B rows still occupy ~75 GB of TOAST — ~10 bytes/row is the *true*
compressed size of six numeric/bigint OHLCV columns under
`segmentby=symbol, orderby=time DESC`, not batch overhead. Both facts bear
directly on the future tick initiative: the archived tick outline
(100-arch.data-storage.md, "Tick schema design") specifies **1-hour chunks**,
and tick volume is ~28× minute volume at modest scale.

**Decision:** Chunk-interval choices for any future hypertable (tick included)
are made from **wall-clock span ÷ target chunk count (~1,000–2,000 chunks over
the table's lifetime)**, never from per-chunk data-volume guidance alone. The
tick schema's 1-hour interval is flagged as **invalid as written** — over one
decade it implies ~87,000 chunks (3.5× the interval that made minute_ohlcv
unusable) before space partitioning multiplies it further. Capacity planning
for tick storage uses the measured ~10 bytes/row compressed floor, not
optimistic compression-ratio projections.

**Rationale:** The planner and lock manager pay per *chunk*, and that cost is
paid on every unpruned query regardless of how small the chunks are. Data
volume determines how large a chunk gets; wall-clock span determines how many
chunks exist. A table that lives for decades needs its interval derived from
the decade, and minute_ohlcv is the measured proof (14-minute plan time on a
trivial query at 25k chunks; 0.7 s at 1.2k). The tick schema was outlined
before this was learned — without this entry the 1-hour figure would be
implemented as designed and fail at a scale where the rewrite is far more
expensive than minute_ohlcv's 13-hour repair.

**Follow-ups:** Slice 166 design doc (Root-Cause Record + Phase D storage
note) carries the full evidence. The tick schema slice, when scoped, must
revisit `chunk_time_interval` and cite this entry. `MINUTE_OHLCV_CHUNK_INTERVAL`
(constants.py) is the single source of truth for the minute table's interval.

## 20260719 — Re-chunking a live gap-structured hypertable: merge_chunks cannot cross empty ranges; the working mechanism is atomic per-window drop-and-reinsert

**Context:** Slice 166 planned to consolidate minute_ohlcv's chunks with
TimescaleDB's `merge_chunks` (Option A) or `compress_chunk_time_interval`
roll-up (Option B). Rehearsal on a gap-faithful scratch table surfaced a
documented but easily-missed restriction: chunks separated by an **empty
range** cannot be merged ("cannot create new chunk partition boundaries").
Market-hours data guarantees empty ranges — no chunks exist overnight or on
weekends — so prod's 25,256 chunks form 5,671 per-trading-day contiguous runs,
capping both mechanisms at ~4.4× improvement when ~21× was required.

**Decision:** The standard mechanism for re-chunking a gap-structured
hypertable in this project is **in-place per-window rewrite** (slice 166's
`mt data rechunk`): for each target-interval window on TimescaleDB's epoch
grid (1970-01-01 + k×interval — 7-day windows start on Thursdays), one
transaction stages the window's rows to a temp table, `drop_chunks()` removes
the old chunks *and their dimension slices*, and reinsert routes tuples into
one fresh full-width chunk; compression follows outside the transaction.
Windows must be **grid-aligned** (a straddling window yields two chunks).
Interrupted runs are safe by construction: the window either committed or
rolled back, and window state is re-derived from the catalog on every run.

**Rationale:** The per-window transaction gives resumability and a
never-broken intermediate state (proven on prod by killing the run mid-window:
clean rollback, exact row-count match on completed windows, seamless resume).
It preserves the hypertable's identity, so all attached continuous aggregates
survive untouched — the copy-and-swap alternative destroys them. It also fixes
compression-batch fragmentation for free, because compression happens on
freshly inserted full-window data. Cost is a full rewrite of the table
(~7.27 B rows in ~13 h), which is acceptable for a one-shot repair and
irrelevant for correctly-chunked tables going forward.

**Follow-ups:** `mt data rechunk` (market/maintenance/rechunk.py) is the
reusable implementation. Slice 163 (minute-cagg re-chunking) faces the same
constraints on the caggs' materialized hypertables and must not re-derive
them. Re-run `mt data rechunk` periodically (jobs paused) to fold in trailing
windows that were inside `compress_after` during the main run.

## 20260719 — Background jobs must be paused during any chunk restructuring: a concurrent cagg refresh silently and permanently loses materialized rows

**Context:** Slice 166's Phase A rehearsal tested what happens when a cagg
refresh policy fires against a chunk mid-restructure. Expected: blocking
(performance nuisance). Observed: the refresh blocked behind the
restructuring transaction, then completed against a source snapshot taken
before the restructure committed — it **consumed the invalidation log but
materialized nothing** for the affected range. The cagg was left permanently
missing rows while reporting "already up-to-date"; only
`refresh_continuous_aggregate(..., force => true)` over the range repairs it.

**Decision:** Any operation that restructures hypertable chunks (merge,
rechunk, decompress/recompress sweeps) **must pause the full family of
background jobs first** — the table's columnstore policy plus every attached
cagg's refresh policy — with job IDs resolved from
`timescaledb_information.jobs` at runtime, never hardcoded. Maintenance
tooling enforces this in pre-flight (refuse to run, don't warn). Job pause is
classified as a **correctness** control, not a performance courtesy. After
resume, verify `last_run_status = 'Success'` and zero unscheduled jobs.

**Rationale:** Silent, permanent, self-hiding data loss is the worst failure
class: the cagg's own bookkeeping says nothing is wrong, so no later refresh
heals it and only a direct cagg-vs-source comparison can detect it. The
rehearsal methodology that caught this is itself part of the decision: 
rehearse destructive operations on a **gap-faithful scratch hypertable**
(weekday/market-hours synthetic data with an attached cagg and live policy) —
a 24/7-continuous scratch table hides both the adjacency restriction and
realistic policy collision windows.

**Follow-ups:** Slice 166 design doc §Root-Cause Record (A5 rehearsal, Q3)
carries the reproduction. `mt data rechunk` pre-flight implements the refusal.
Slice 163 must adopt the same pause/verify discipline for the cagg
materialized hypertables' own jobs.
