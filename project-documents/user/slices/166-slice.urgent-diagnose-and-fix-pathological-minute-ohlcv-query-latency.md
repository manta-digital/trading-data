---
docType: slice-design
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [156, 160]
interfaces: [163, 164, 182]
dateCreated: 20260717
dateUpdated: 20260718
status: in_progress
---

# Slice Design: URGENT — Diagnose and Fix Pathological `minute_ohlcv` Query Latency

## Overview

During slice 162's production verification (2026-07-17), trivial ad-hoc queries
against `minute_ohlcv` took minutes: a single-symbol
`SELECT MIN(time), MAX(time)` with no join and no time filter ran **10m47s**;
a universe-wide per-symbol `NOT EXISTS` probe ran **8m8s**. This is the
simplest possible query shape against the table, on a 128 GB / 5950X host with
no competing load — the table is effectively unusable for ad-hoc operator
queries and diagnostics.

This slice diagnoses the root cause with real `EXPLAIN` evidence, fixes the
underlying table health, and proves the fix by re-running the exact queries
that surfaced the problem. It is a **prerequisite** for slices 163 (cagg
re-sizing) and 164 (bounded-time query convention): those should build on a
table that is not already pathological for trivial reads.

## Value

**Operational:** ad-hoc operator queries and production-verification
walkthroughs (like 162's T15) become feasible again — low seconds instead of
tens of minutes. Every future slice's verification depends on this.

**Architectural:** slice 182's bars endpoint and slice 163/164's work inherit a
structurally healthy source table. The cagg over-chunking slice 163 fixes was
itself *caused* by this table's chunk interval (TimescaleDB sizes a cagg's
materialized hypertable at 10× the source interval); fixing the source closes
the factory for that defect class.

**Storage:** the same root cause wastes ~100 GB of disk (see baseline below).
The fix should reclaim most of it.

## Measured Baseline (captured 2026-07-17, prod `trading` DB, design phase)

Environment: PostgreSQL 17.7, TimescaleDB **2.23.0**,
`max_locks_per_transaction = 2048`, `shared_buffers = 32 GB`.

| Fact | `minute_ohlcv` | `daily_ohlcv` (reference) |
|---|---|---|
| `chunk_time_interval` | **4 hours** | 7 days |
| Chunk count | **25,256** (25,235 compressed, 21 not) | 3,364 |
| Data span | 2003-12-31 → 2026-07-16 | similar |
| Total size | **126 GB** | 4.4 GB |
| — table | 36 GB | |
| — TOAST | **85 GB** | |
| — index | 5.5 GB | |
| Compression stats | 214 GB before → 25 GB after | |

Additional facts:

- Interval origin: `create_hypertable(..., chunk_time_interval => INTERVAL
  '4 hours')` in `src/manta_trading/market/schema/migrations/minute.py:531`.
- Compression: `segmentby = symbol`, `orderby = time DESC` (slice 160);
  columnstore policy job 1009, `compress_after = 7 days`, every 2h.
- Indexes (uncompressed side): `(time DESC)`, `(symbol, time DESC)`,
  `(time DESC, symbol)`, unique `(symbol, time)`.
- `approximate_row_count('minute_ohlcv')` returns **64.2 B rows** — implausible
  against 214 GB uncompressed (~3 bytes/row); catalog statistics are stale or
  distorted. Treat any planner estimate on this table as suspect until
  re-analyzed post-fix.
- TimescaleDB 2.23 provides `merge_chunks` and `split_chunk` (present in
  `pg_proc`).
- **The `data_status` view sits on the affected path** (verified via
  `pg_get_viewdef`, 2026-07-17): its `bars_summary` CTE computes
  `MIN(time) / MAX(time) / COUNT(*) FROM minute_ohlcv GROUP BY symbol` —
  unbounded, the exact query shape measured at 10m47s. The parent
  architecture's NFR (`140-arch.data-quality-operations.md`: "View latency
  stays sub-second at full-universe scope") therefore binds this slice; see
  Success Criteria.
- Background jobs against the minute family (from
  `timescaledb_information.jobs`, 2026-07-17): cagg refresh every **5 min**
  (`minute_5min_ohlcv`, job 1007), **15 min** (job 1008), **1 h** (jobs
  1002/1003), plus columnstore policy every **2 h** (job 1009). Any
  multi-hour Phase C run will overlap these; see Phase C.

## Root-Cause Analysis (hypothesis, to be confirmed in Phase A)

**Primary hypothesis — chunk-count pathology.** At a 4-hour interval over 22.5
years, the table has 25,256 chunks averaging ~5 MB (vs the ~1 GB-order guidance
for chunk sizing). Any query without a `time` predicate cannot prune, so it
must plan, lock, open, and read metadata for all ~25k chunks — per-chunk
overhead × 25k dominates regardless of how little data each chunk contributes.
This also pressures the lock table (the reason `max_locks_per_transaction` was
raised to 2048).

**Secondary effect — compression batch fragmentation.** A 4-hour chunk holds at
most ~240 minute-bars per symbol (a 390-minute regular session spans two
chunks). With `segmentby = symbol`, every per-symbol compression batch is far
below the 1000-row batch target, so per-batch metadata and TOAST overhead
dominate: 85 GB TOAST against 25 GB of actual compressed data, and a
compression ratio of ~8.6× where 10–20× was expected (slice 160). Fixing the
chunk interval fixes batch sizes as a direct consequence.

Alternative hypotheses to rule in/out with `EXPLAIN` evidence in Phase A (do
not fix speculatively, per project rule):

- MIN/MAX unable to use compressed-chunk sparse metadata → full decompression
  per chunk (would compound, not replace, the chunk-count cost).
- Missing/ineligible index path on compressed chunks for `symbol = X`.
- Lock/catalog contention effects specific to the 25k-chunk catalog.

## Technical Scope

**Included:**

1. **Diagnosis (Phase A):** captured `EXPLAIN` / `EXPLAIN (ANALYZE, BUFFERS)`
   for the exact T15 queries; confirmation or correction of the hypothesis
   above; the output becomes the root-cause record appended to this design.
2. **Remediation (Phases B–C):** re-chunk `minute_ohlcv` itself to a sane
   interval — both for **future** chunks (`set_chunk_time_interval` + migration
   so cold start creates the right interval) and for the **existing** ~25k
   chunks (in-place merge; strategy decision gate below).
3. **Verification (Phase D):** re-run the three queries that surfaced the
   problem; integrity and storage checks; before/after `EXPLAIN` attached here.

**Excluded:**

- Cagg re-chunking (`_materialized_hypertable_3–6`) — slice 163. Note: after
  this slice, the 10×-source default for any *future* cagg becomes 70 days,
  which is exactly the interval 163 targets.
- Bounded-time query convention / read-helper enforcement — slice 164. Still
  worthwhile afterward; unbounded scans of a billion-row table should remain
  the explicit exception, just not a catastrophic one.
- Restarting the production minute daemon — separate PM go/no-go decision.
- Re-tuning `max_locks_per_transaction` downward post-fix — record the
  possibility; do not change it in this slice.
- `daily_ohlcv` (3,364 chunks at 7 days is not pathological; leave alone).

## Technical Decisions

### Target chunk interval: 7 days

- Chunk count drops **25,256 → ~1,180** (~21×), plus slow future growth.
- Matches `daily_ohlcv`'s proven-healthy interval and the compression policy
  cadence (`compress_after = 7 days` — a chunk compresses once, when complete).
- Per-symbol batch size becomes ~5 sessions × 390 bars ≈ 1,950 rows → healthy
  full 1000-row compression batches, eliminating the fragmentation/TOAST
  pathology.
- Projected compressed chunk size ~25–100 MB today, growing with backfill
  density — comfortably within guidance on this hardware.

*Alternative considered:* 30 days (~270 chunks, larger batches still). Rejected
as the default: coarser exclusion granularity for time-bounded hot-path reads
(slice 164's pattern), and a larger decompress/recompress unit whenever late
data lands in an old chunk. 7 days is the conservative choice; the decision
gate in Phase B may revisit if Phase A evidence argues for it.

The interval value is defined **once** (constants module, e.g.
`MINUTE_OHLCV_CHUNK_INTERVAL`) and referenced by both the hypertable-creation
migration and the new remediation migration — no scattered `'7 days'` literals.

### Remediation strategy: in-place `merge_chunks` (preferred), decision-gated

| Option | Mechanics | Caggs | Disk | Resumable | Risk |
|---|---|---|---|---|---|
| **A. `merge_chunks` in place** (preferred) | ~600 merge operations, each merging ~42 adjacent 4-hour chunks into one 7-day window | **Preserved** (hypertable identity unchanged) | No transient copy | Yes — per-merge transactions; catalog scan finds remaining work | `merge_chunks` maturity in 2.23; compressed-chunk support must be verified in Phase A |
| B. `compress_chunk_time_interval` roll-up | Set the parameter; decompress + recompress chunks so recompression merges neighbors | Preserved | Low | Yes | Older mechanism; known perf caveats; two knobs governing one behavior |
| C. New hypertable + copy + swap | `CREATE`, `INSERT SELECT` by time slice, compress, rename | **Broken** — all four minute caggs bind to hypertable identity and would need recreation + full re-materialization | ~2× transient | Partially | Largest blast radius; last resort |

Option C's cagg destruction is the decisive strike against it. Phase A includes
a rehearsal of Option A on a scratch hypertable (mirroring compression settings
and a cagg) to verify 2.23 `merge_chunks` handles compressed chunks and
cagg-attached hypertables; if rehearsal fails, fall back to B, then C — each
step is a PM checkpoint, not an automatic escalation.

All options are preceded by `set_chunk_time_interval('minute_ohlcv',
INTERVAL '7 days')`, which is correct and safe regardless of strategy (affects
only future chunks).

### Merge driver: resumable maintenance command, not ad-hoc SQL

~600 merge operations need progress output, per-operation commit, error
handling, and resume-after-interrupt (the same lesson as the minute-fetch
transaction pattern: one big transaction loses everything on Ctrl-C). A small
operator-run maintenance entry point (location/naming decided at task
breakdown; consistent with existing CLI structure) that:

- enumerates current chunks from the Timescale catalog, groups them into
  target 7-day windows, skips windows already consisting of a single chunk
  (idempotent — safe to re-run until done),
- skips (and logs) windows containing any **uncompressed** chunk — the
  trailing ~21 chunks inside the `compress_after` horizon; they are handled
  by a later idempotent re-run once the compression policy has caught up
  (subject to the Phase A rehearsal's mixed-window answer),
- merges one window per transaction, logging `merged W/<total> windows`
  progress,
- stops cleanly on first error with the failing window identified.

This is one-shot operational tooling: keep it minimal, no configuration
surface beyond a `--dry-run`.

### Statistics refresh

After the merge completes: `ANALYZE minute_ohlcv`, then re-check
`approximate_row_count` for sanity. Record the corrected row count in the
Phase D results.

## Implementation Details

### Phase A — Diagnose (evidence before fix)

1. `EXPLAIN` (no ANALYZE) of `SELECT MIN(time), MAX(time) FROM minute_ohlcv
   WHERE symbol = '<sym>'` — capture **planning time** and plan shape (expect
   a ~25k-way chunk append).
2. `EXPLAIN (ANALYZE, BUFFERS)` of the same query, once, in a controlled run
   (expected to take on the order of the original 10m47s — this is the
   diagnostic cost). Capture where execution time concentrates.
3. During (2), from a second connection: sample `pg_locks` count for the
   backend, confirming lock-table pressure.
4. Rehearse `merge_chunks` on a scratch hypertable (small synthetic data,
   same compression settings, one attached cagg): verify compressed-chunk
   merge works in 2.23, verify cagg survives and still refreshes. The
   rehearsal must explicitly answer three questions:
   - **Batch rewrite:** does merging compressed chunks rewrite compression
     batches (compare per-batch row counts and TOAST size before/after on the
     scratch table)? If batches are carried over fragmented, Phase C gains a
     mandatory recompression pass; if merge requires decompress-first, size
     the transient disk cost. Success Criterion 7 depends on this answer —
     it must not be assumed.
   - **Mixed windows:** behavior when a target window contains both
     compressed and uncompressed chunks (the trailing ~21 chunks inside the
     `compress_after` horizon).
   - **Job collision:** behavior when a cagg refresh or compression policy
     job fires against a chunk mid-merge (confirming the Phase C job-pause
     approach is necessary and sufficient).
5. Decision gate (PM): confirm root cause matches hypothesis; select Option
   A/B/C per rehearsal outcome; confirm a DB snapshot/backup point exists
   before bulk mutation.

### Phase B — Migration & config

6. New schema migration: `set_chunk_time_interval('minute_ohlcv',
   MINUTE_OHLCV_CHUNK_INTERVAL)`; update the original `create_hypertable`
   call (`migrations/minute.py:531`) to reference the same constant so a cold
   start creates 7-day chunks directly (migration chain remains the single
   schema source of truth, per slice 156).
6a. Update architecture docs that state the old interval:
    `100-arch.data-storage.md:67` ("Hypertable: `minute_ohlcv` (4hr chunks)")
    and the comparative chunk-sizing rationale at `:124` ("1hr vs 4hr"),
    plus a grep sweep for any other doc restating 4-hour minute chunks. The
    interval is centralized in code; the docs must not keep teaching the
    superseded value (the 10×-source cagg default makes stale sizing text a
    live hazard for future decisions).

### Phase C — Execute remediation

7. Implement the merge driver per the decision above.
8. **Pause background jobs** for the minute family before the run: minute
   cagg refresh policies (jobs 1002, 1003, 1007, 1008) and the minute
   columnstore policy (job 1009) via `alter_job(..., scheduled => false)`.
   These fire every 5 min–2 h (see baseline); a multi-hour merge run *will*
   otherwise collide with them mid-merge. The driver pre-flight asserts the
   jobs are paused and refuses to run otherwise; job IDs are resolved from
   the catalog at runtime, not hardcoded.
9. Run the driver against prod (daemon stopped, jobs paused; no contention).
   Interrupt / resume at least once deliberately to prove resumability early
   in the run.
10. **Recompression pass, if the Phase A rehearsal showed merged batches are
    carried over fragmented:** recompress each merged chunk so batches are
    rebuilt at proper size (this, not the merge itself, is what collapses
    the 85 GB TOAST pathology). If rehearsal showed merge already rewrites
    batches, record that and skip.
11. **Resume the paused jobs**; verify the cagg refresh policies catch up
    over their normal windows and the compression policy re-engages.
12. `ANALYZE minute_ohlcv` on completion.

### Phase D — Verify

13. Re-run the exact three T15 queries (below) and capture timings + `EXPLAIN
    (ANALYZE, BUFFERS)`; append before/after as the root-cause record in this
    document.
14. Time a full-universe `data_status` read (`mt data status` / `SELECT` over
    the view) before and after; restate the 140-arch sub-second NFR against
    the result (see Success Criterion 8).
15. Integrity checks (see Success Criteria).
16. Storage re-measurement (`hypertable_detailed_size`, compression stats).

## Integration Points

### Provides to Other Slices

- **163 (cagg re-sizing):** a healthy source table; 163's 70-day cagg interval
  becomes consistent with the new 10×-source default. 163 remains separate,
  unblocked work — its ~4,235-chunk materialized hypertables are distinct
  objects untouched here.
- **164 (bounded-time convention):** enforcement lands on a table where an
  unbounded read is merely slow, not catastrophic.
- **182 (bars endpoint):** inherits sane worst-case latency.

### Consumes from Other Slices

- Slice 160's compression configuration (unchanged, but its policy interacts
  with the merge strategy — rehearsed in Phase A).
- Slice 156's cold-start contract (migration chain updated in Phase B).

## Success Criteria

1. `SELECT MIN(time), MAX(time) FROM minute_ohlcv WHERE symbol = '<sym>'`
   returns in **low seconds**, not minutes, for an arbitrary symbol.
2. The universe-wide `NOT EXISTS` probe from 162's walkthrough returns in the
   same order of magnitude as the `minute_4hour_ohlcv` cagg equivalent
   (~3–20 s), not 8+ minutes.
3. Chunk count for `minute_ohlcv` is ~1,200, and a fresh chunk created by new
   inserts has a 7-day interval; a cold-start DB creates 7-day chunks from the
   first migration run.
4. **No data loss:** for ≥3 sampled symbols, bounded-window `count(*)`,
   `MIN(time)`, `MAX(time)` are identical before vs after the merge; the 162
   grouped coverage query returns identical results; total bar count per the
   caggs is unchanged.
5. All four minute caggs still refresh and serve identical query results.
6. Before/after `EXPLAIN` evidence and timings are recorded in this document
   (root-cause record), including the corrected `approximate_row_count`.
7. All ~1,180 merged chunks end up **compressed with properly-sized batches**
   (via the merge itself or the conditional Phase C recompression pass, per
   the rehearsal's batch-rewrite answer), and storage total for
   `minute_ohlcv` drops materially from 126 GB (expected ~30–40 GB; the
   85 GB TOAST pathology collapses only when batches are rebuilt). Record
   actual.
8. Full-universe `data_status` latency is measured before and after and
   recorded against the 140-arch NFR ("view latency stays sub-second at
   full-universe scope"). The view's `bars_summary` CTE full-scans
   `minute_ohlcv`, so this slice must leave it dramatically faster; if the
   post-fix measurement still misses the sub-second target, record the
   actual and raise to the PM whether the NFR requires a view rewrite (e.g.
   cagg-backed `bars_summary`) as a follow-up slice — do not silently leave
   the NFR unmet or widen this slice's scope to a view redesign.
9. Background jobs (minute cagg refresh + columnstore policies) are running
   again post-remediation, caggs have caught up, and no job is left paused.

## Verification Walkthrough (draft — refined at Phase 6 completion)

```sql
-- 1. The query that took 10m47s on 2026-07-17 (use any active symbol):
\timing on
SELECT MIN(time), MAX(time) FROM minute_ohlcv WHERE symbol = 'AACB';
-- Expected: low seconds. Was: 10m47s.

-- 2. The universe-wide existence probe that took 8m8s:
SELECT count(*) FROM instruments i
WHERE NOT EXISTS (SELECT 1 FROM minute_ohlcv m WHERE m.symbol = i.symbol);
-- Expected: same order of magnitude as the cagg version (~3–20s). Was: 8m8s.

-- 3. Chunk health:
SELECT num_chunks FROM timescaledb_information.hypertables
WHERE hypertable_name = 'minute_ohlcv';
-- Expected: ~1,200 (was 25,256).

-- 4. Storage reclaimed:
SELECT pg_size_pretty(total_bytes), pg_size_pretty(toast_bytes)
FROM hypertable_detailed_size('minute_ohlcv');
-- Expected: total well under 126 GB; TOAST far under 85 GB. Record actuals.

-- 5. data_status NFR (140-arch: sub-second at full-universe scope):
SELECT count(*) FROM data_status;
-- Record before/after timing. If post-fix latency still exceeds the NFR
-- target, that is a PM escalation per Success Criterion 8, not a pass.

-- 6. No job left paused:
SELECT job_id, application_name, scheduled FROM timescaledb_information.jobs
WHERE scheduled = false;
-- Expected: zero rows.
```

```bash
# 7. Cold-start still correct (fixture/dev DB):
mt data init   # then confirm minute_ohlcv chunk_time_interval = 7 days
```

Integrity spot-check (before/after capture during Phase C/D):

```sql
SELECT count(*), MIN(time), MAX(time) FROM minute_ohlcv
WHERE symbol = 'AAPL' AND time >= '2024-01-01' AND time < '2024-02-01';
-- Identical results pre- and post-merge.
```

## Risk Assessment

- **`merge_chunks` maturity (2.23):** the API is recent; compressed-chunk and
  cagg-attached behavior must be proven in the Phase A rehearsal before
  touching prod. Fallback ladder (B, then C) is defined above with PM
  checkpoints at each escalation. Mitigation: per-window transactions mean an
  interrupted or failed run leaves a valid, partially-improved table — never a
  broken one.
- **Bulk mutation of a 126 GB production table:** PM confirms a
  snapshot/backup point before Phase C begins (gate in Phase A step 5).
- **Hidden second bottleneck:** if Phase A's `EXPLAIN` contradicts the
  chunk-count hypothesis, stop at the decision gate and revise this design
  with the PM rather than proceeding on momentum — the fix must follow the
  evidence.

Effort: 3/5. Risk: High (production bulk operation), mitigated by
rehearsal + resumable per-window execution.

---

## Root-Cause Record (Phase A, executed 2026-07-18)

Method: all diagnostics run against prod `trading` (PostgreSQL 17.7,
TimescaleDB 2.23.0) via psql; lock sampling from a second connection.
Full EXPLAIN outputs are large (278k / 152k lines); verbatim excerpts below,
full captures retained in the session scratchpad during execution.

### A1 — `EXPLAIN (VERBOSE, COSTS)`, no ANALYZE

`SELECT MIN(time), MAX(time) FROM minute_ohlcv WHERE symbol = 'AAPL'`

- **Plan-only EXPLAIN took 868,195 ms (14m28s)** — longer than the original
  10m47s full query. Producing the plan is the pathology.
- Plan text is **277,749 lines**: two InitPlans (MIN, MAX), each a
  `Custom Scan (ChunkAppend)` over all ~25k chunks, each chunk a
  `ColumnarScan` + `Index Scan` on its compressed chunk
  (`Index Cond: symbol = 'AAPL'`). Per-chunk work is trivial; there are just
  25,256 of them, twice.

### A2 — `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)` — one deliberate run

```
Planning:
  Buffers: shared hit=16452348 dirtied=1
Planning Time: 846597.124 ms        -- 14m07s
Execution Time: 4188.955 ms         -- 4.2s
Total: 854,129 ms (14m14s)
```

- **>99.5% of wall time is planning**, not execution. Planning touched
  16.45M buffer pages (catalog + index metadata for ~25k chunks × ~7
  relations each).
- Execution itself: 4.2s, 784,994 buffer hits; the vast majority of chunk
  subplans report `(never executed)` — runtime chunk exclusion works, but
  only after planning has already paid for every chunk.
- Verdict: **primary hypothesis (chunk-count pathology) CONFIRMED.** The
  alternative hypotheses (sparse-metadata MIN/MAX failure, missing index
  path, decompression cost) are ruled out: the compressed-chunk index path
  is used and execution is cheap.

### A3 — lock-table pressure

- Peak locks held by the diagnostic backend: **176,764** (42 samples, 20s
  interval; grew from ~126k early in planning to a 176,764 plateau).
- ~7 locks per chunk (chunk + compressed chunk + their indexes),
  corroborating why `max_locks_per_transaction` had to be raised to 2048.
- Backend state throughout: `active`, no wait events — pure CPU/catalog
  work, no blocking.

### A4/A5 — scratch-hypertable rehearsal (three gating questions)

Scratch: 126 four-hour chunks (108 compressed), 151,200 rows, 5 symbols,
`segmentby=symbol`, `orderby=time DESC`, one 5-min cagg with refresh policy.

1. **Batch rewrite: NO.** `CALL merge_chunks(ARRAY[42 compressed chunks])`
   succeeded in one call (~124 ms scratch-scale) and produced a single
   *compressed* chunk — but batches were carried over unchanged
   (210 batches, avg 240 rows before AND after). Merge alone does **not**
   collapse the TOAST pathology. A decompress+recompress of the merged
   chunk rebuilt batches to healthy size (210 → 55 batches, avg 916 ≈ the
   1000-row target; 984 kB → 336 kB). **A recompression pass is mandatory**
   wherever merged chunks retain old batches.
2. **Mixed windows: merge succeeds.** A window of 6 compressed + 6
   uncompressed chunks merged into one compressed chunk, integrity intact.
   The driver still skips windows containing uncompressed chunks (the
   active insert region) as designed — the failure mode is benign, not an
   error.
3. **Job collision: blocks AND corrupts.** With a merge transaction open, a
   concurrent cagg refresh over an invalidated range blocked ~16s until the
   merge committed, then **silently lost the invalidated buckets**: the
   refresh consumed the invalidation log but materialized nothing for the
   range (its snapshot predated the merge commit). The cagg then reported
   "already up-to-date" while permanently missing rows. Repair required
   `refresh_continuous_aggregate(..., force => true)`. **Pausing the five
   minute-family jobs during any chunk restructuring is therefore
   correctness-critical, not merely a performance nicety.**

### A6 — documentation + the adjacency discovery

Official `merge_chunks` docs (TimescaleDB ≥2.18): *"You can only merge
chunks that have directly adjacent partitions. It is not possible to merge
chunks that have another chunk, or an empty range between them."* Also: no
tiered data, no writes to chunks mid-merge; merged chunk keeps the first
chunk's name/constraints/triggers.

**This restriction is enforced and is decisive for prod.** On a gap-faithful
scratch table (market-hours-only data → chunks separated by empty
overnight/weekend ranges):

- Merging a week's chunks across gaps fails:
  `ERROR: cannot create new chunk partition boundaries` /
  `HINT: Try merging chunks that have adjacent partitions.`
- Range-touching chunks merge fine.
- **Option B (`compress_chunk_time_interval` roll-up) has the same
  limitation**: recompressing every chunk with a 7-day roll-up interval
  consolidated only same-day touching chunks (19 → 10, one chunk per
  trading day) — it also cannot cross empty ranges.

Prod contiguity (catalog measurement, 2026-07-18): `minute_ohlcv`'s 25,256
chunks form **5,671 contiguous runs** (run lengths: 4 chunks × 2,792 runs,
5 × 2,728, 3 × 147, ≤2 × 4) — one run per trading day, split by empty
overnight/weekend chunk ranges.

**Consequence: Options A and B cannot go below ~5,671 chunks** (~4.4×
reduction; planning ≈ 14m28s / 4.4 ≈ ~3 min — still failing Success
Criteria 1 and 3).

### Option D rehearsal — in-place window rewrite (drop_chunks + reinsert)

Because A and B cannot reach the target, a fourth option was rehearsed on the
gap-faithful scratch table (with attached cagg):

Per 7-day window, in **one transaction**: stage the window's rows into a
temp table → `drop_chunks()` for the window (removes chunks *and* their
dimension slices) → `INSERT` the rows back (tuple routing finds no slices,
creates fresh chunks at the now-7-day interval) → commit; then
`compress_chunk()` the new chunk.

Rehearsal results:

- The window collapsed to a single 7-day chunk; compression produced
  healthy batches directly (no separate recompression pass needed — the
  batch-rewrite problem from A5-Q1 does not arise on freshly inserted data).
- **The cagg was untouched**: identical row count and aggregate values
  before the cycle, after the cycle, and after a `force => true` refresh
  over the window (`drop_chunks` does not invalidate caggs; the reinsert's
  invalidations recompute to identical values over identical data).
- **Atomicity proven**: a deliberate mid-cycle `ROLLBACK` (after
  `drop_chunks`, before reinsert) restored the table exactly.
- **Grid alignment caveat**: new 7-day slices are epoch-anchored
  (1970-01-01 + k×7d), not window-anchored. A window straddling two grid
  weeks produces two chunks. The driver must iterate **grid-aligned**
  windows; 4-hour slices nest exactly inside the 7-day grid, so each grid
  window then yields exactly one chunk.

Cost model (bounded prod samples): ~4.46M rows/week recent, ~3.3M (2015),
~1.2M (2005); ~1,175 grid weeks ≈ **~3.5B rows rewritten** over the run —
hours of unattended runtime, resumable per-window, transient disk one
window's staging (≤ ~1 GB).

### Revised remediation options (supersedes the design's A/B/C table)

| Option | Result on prod | Caggs | Meets criteria? | Status |
|---|---|---|---|---|
| A′. `merge_chunks` per contiguous run (+recompress) | ~5,671 day-chunks; planning ~3 min | Preserved | **No** (Criteria 1, 3) | Rehearsed, works, insufficient |
| B. `compress_chunk_time_interval` roll-up | Same ~5,671 cap (cannot cross gaps) | Preserved | **No** | Rehearsed, works, insufficient |
| C. New hypertable + copy + swap | ~1,175 chunks | **Destroyed** (4 caggs recreate + re-materialize) | Yes | Not rehearsed; last resort |
| **D. In-place per-window rewrite (drop_chunks + reinsert + compress)** | ~1,175 grid-week chunks | **Preserved** (verified incl. force-refresh identity) | Yes | **Rehearsed successfully** |

Senior-AI recommendation to PM: **Option D**, with the A5-Q3 job-pause
(correctness-critical) and the C2-style resumable driver (windows derived
from the catalog: any grid week still holding >1 chunk or any 4-hour-slice
chunk is unfinished — idempotent re-run). Migration 043
(`set_chunk_time_interval`) remains required and unchanged. Note: the
planned command name `caggs merge-chunks` no longer matches the mechanism;
propose `mt data rechunk` (or PM's preferred name) at implementation.

### A7 gate status

- (a) Root cause: **confirmed** — planning/locking over 25,256 chunks
  (846.6s planning vs 4.2s execution).
- (b) Remediation selection: **awaiting PM** — rehearsal evidence above;
  recommendation Option D. (Design's preferred Option A is proven
  insufficient on prod's gap structure; this is the "revise with the PM"
  path, not automatic escalation.)
- (c) Backup point before bulk mutation: **awaiting PM confirmation**.

*Phase D before/after evidence to be appended after remediation.*
