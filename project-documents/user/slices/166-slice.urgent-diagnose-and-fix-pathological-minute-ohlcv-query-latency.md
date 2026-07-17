---
docType: slice-design
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: []
interfaces: [163, 164, 182]
dateCreated: 20260717
dateUpdated: 20260717
status: not_started
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
   merge works in 2.23, verify cagg survives and still refreshes.
5. Decision gate (PM): confirm root cause matches hypothesis; select Option
   A/B/C per rehearsal outcome; confirm a DB snapshot/backup point exists
   before bulk mutation.

### Phase B — Migration & config

6. New schema migration: `set_chunk_time_interval('minute_ohlcv',
   MINUTE_OHLCV_CHUNK_INTERVAL)`; update the original `create_hypertable`
   call (`migrations/minute.py:531`) to reference the same constant so a cold
   start creates 7-day chunks directly (migration chain remains the single
   schema source of truth, per slice 156).

### Phase C — Execute remediation

7. Implement the merge driver per the decision above.
8. Run it against prod (daemon is stopped; no writer contention). Interrupt /
   resume at least once deliberately to prove resumability early in the run.
9. `ANALYZE minute_ohlcv` on completion.

### Phase D — Verify

10. Re-run the exact three T15 queries (below) and capture timings + `EXPLAIN
    (ANALYZE, BUFFERS)`; append before/after as the root-cause record in this
    document.
11. Integrity checks (see Success Criteria).
12. Storage re-measurement (`hypertable_detailed_size`, compression stats).

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
7. Storage total for `minute_ohlcv` drops materially from 126 GB (expected
   ~30–40 GB; the 85 GB TOAST pathology collapses with proper batch sizes).
   Record actual.

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
```

```bash
# 5. Cold-start still correct (fixture/dev DB):
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
