# Task B.7 — width selection and supporting measurements (slice 169)

Selected: **COVERAGE_BUCKET_INTERVAL = 7 days**
Selected: **start_offset = 14 days** (engine floor = COVERAGE_REFRESH_MIN_WINDOW_BUCKETS x 7d)
PM decision, 2026-08-14.

## Measurement environment

Database `mt_169_b1` on 192.168.1.144 — the **same PostgreSQL cluster as prod**,
so parallelism/memory settings are prod's exactly, not an approximation:

    max_parallel_workers_per_gather = 16
    max_parallel_workers            = 32
    max_worker_processes            = 51
    shared_buffers                  = 32179MB
    work_mem                        = 512MB

Host (PM-supplied): AMD 5950X, 16 physical cores / 32 threads (SMT enabled),
128 GB RAM. `max_parallel_workers=32` therefore maps to threads, not cores —
fully subscribed but not oversubscribed. A daemon (minute fetch, tick daemon,
or Kalshi gathering) is normally running and shares the host, though not
necessarily the same tables.

### Seed shape (B.1)

| Table | Seeded | Prod | Delta |
|---|---|---|---|
| daily symbols | 12,040 | 12,040 | 0% |
| daily span | 1962-01-01 .. 2026-08-04 | 1962..2026 | 0% |
| minute symbols | 5,871 | 5,871 | 0% |
| minute span | 2004-01-02 .. 2026-08-04 | 2004..2026 | 0% |
| instruments | 12,040 | ~12,040 | 0% |
| acquisition_state | 17,911 | — | n/a |
| data_gaps | 326 | — | n/a |
| trading_sessions | 4,560 | — | n/a |
| daily_ohlcv rows | 16,742,957 | 65,652,505 | -74% (deliberate) |
| minute_ohlcv rows | 3,019,870 | 4,414,650,928 | -99.9% (deliberate) |

Raw row counts are deliberately far below prod. The fixture reproduces the
**coverage-row shape** (symbols x buckets-with-data), which is what drives the
`data_status` read — the same basis `test_167_data_status_nfr.py` uses. Symbol
counts and spans, which *do* drive coverage rows, match prod exactly.

Listing dates are non-uniform (10% span full history, remainder biased recent)
rather than every symbol spanning 1962-2026, so coverage row counts are
realistic rather than worst-case.

## B.2 — rows materialized (actual)

| Width | minute_coverage | daily_coverage | mat. time | mat-hypertable chunks (min/daily) |
|---|---|---|---|---|
| 7 d | 3,019,870 | 16,742,957 | 110 s | 119 / 35 |
| 14 d | 1,513,344 | 8,377,890 | — | — |
| 30 d | 708,568 | 3,914,188 | 29 s | 118 / 35 |
| 90 d | 237,811 | 1,309,052 | 13 s | 92 / 35 |

D2's worst-case table assumed every symbol spans full history; these actuals
are ~2.4x below it at 7 days (16.7M vs 40.6M), consistent with that note.

## B.3 / B.4 — read cost, parallelism controlled

**Methodological correction.** The first sweep compared widths whose plans
launched different worker counts (7d got 6, 14d/30d got 2), producing a
non-monotonic ranking where 14d appeared *slower* than 7d despite touching half
the buffers. That measured planner nondeterminism. All numbers below hold
parallelism fixed.

Worker scaling on `SELECT health, COUNT(*) FROM data_status GROUP BY health`:

| Workers | 7 d | 30 d |
|---|---|---|
| 0 (serial) | 2.847 s | 0.789 s |
| 1 | 1.862 s | 0.534 s |
| 2 | 1.856 s | 0.532 s |
| 4 | 1.857 s | 0.532 s |
| 6 | 1.868 s | 0.531 s |
| 8 | 1.868 s | 0.532 s |
| 16 | 1.867 s | 0.531 s |
| planner's own choice | 6 → 1.858 s | 2 → 0.531 s |

**Parallelism saturates at one worker.** Beyond that both widths are flat to the
millisecond — the query is bound by the sequential aggregate over the coverage
cagg, not scan throughput. Contention for the 32-worker pool is therefore NOT a
risk for either width, and no width depends on winning maximum parallelism.

Cost is linear in coverage rows: 7d/30d ratio is 3.5x against a 4.3x row ratio.

### Real query shapes (what callers actually issue)

Both call sites (`cli/commands/data.py:922`, `api_server/routes/status.py:128`)
pair a row fetch with an **always-unfiltered** health-count aggregate.

| Shape | 7 d | 30 d |
|---|---|---|
| `rows_one_symbol` (serial) | 0.010 s | 0.005 s |
| `rows_all` (serial) | 4.549 s | 1.722 s |
| `health_counts` (serial) | 2.861 s | 0.784 s |
| `nfr_count_star` (serial) | 2.850 s | 0.777 s |
| **`mt data status SPY`** (planner default) | **~1.86 s** | ~0.53 s |
| `mt data status` full dump (par6) | 2.933 s | 2.506 s |

**~99% of single-symbol status latency is the unfiltered health-count scan**,
not the symbol's own rows (0.010 s at 7 days). See the filed follow-on issues.

## B.5 — content-edge probe (criterion 17)

Budget: `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` = 10 s.

| Width | minute_coverage | daily_coverage | margin vs 10 s |
|---|---|---|---|
| 7 d | 0.068 s | 0.220 s | **45x** |
| 30 d | 0.039 s | 0.076 s | 130x |
| 90 d | 0.024 s | 0.040 s | 250x |

D3b's rejection condition does not bind at any candidate width. 7 days passes
with 45x margin.

## B.6a / B.6b — policy run cost (criterion 19)

Measured at the engine-floor `start_offset` (2 x width) on the seeded database:

| Width | start_offset | policy run |
|---|---|---|
| 7 d | 14 d | 0.023 s |
| 30 d | 60 d | 0.024 s |
| 90 d | 180 d | 0.024 s |

**Flat across widths — and this is a FLOOR, not a prediction.** The measurement
database is quiescent: nothing writes to it, so after initial materialization a
refresh has almost no invalidations to process. It establishes that width does
not drive policy cost; it CANNOT establish that a run fits the 1-hour schedule
interval under prod's live ingest.

Consequence for D4a: the "measured runtime fits schedule interval" constraint
could not bind at selection time. `start_offset` is therefore set to the engine
floor (14 days) — the smallest value the engine accepts, which minimises
per-run work and maximises schedule-interval margin by construction. Part 2's
Task G takes the real number under live ingest.

This also relaxes D4a's concern directly: the old 750-day start_offset was
forced by the 365-day width. At 7 days the floor is 14 days, a 53x reduction in
window size, so the "policy begins doing real work every hour over 750 days"
risk is removed rather than merely measured.

## B.7 — selection rationale

Criterion: smallest width holding the sub-second `data_status` NFR, the 10 s
probe budget with margin, and a policy run inside its schedule interval.

- **14 days: eliminated.** Dominated — strictly worse than 30 days on every
  measured metric (1.51 s vs 0.79 s serial), and the planner refuses to
  parallelize it at all.
- **90 days: eliminated.** Too coarse a staleness bound (90 d 4 h) to be useful.
- **7 vs 30:** 7 days costs ~3.5x on the interactive path (1.86 s vs 0.53 s) and
  buys a 4.3x better staleness ceiling (7 d 4 h vs 30 d 4 h).

**Selected 7 days.** The deciding factor is that ~99% of the 7-day latency
penalty lands in the unfiltered health-count scan, which GitHub #14 and the
health-count follow-on both target and which the PM confirmed is being fixed.
Width is a structural property requiring a full drop/rebuild to change
(TimescaleDB has no re-bucket operation); the latency it costs is a transient
that a separate, already-planned fix removes. Taking the permanent win and
paying a temporary cost on a low-frequency interactive command is the correct
trade.

Frequency context supporting the trade:
- `/api/v1/health` — most-polled endpoint — does **not** read `data_status` at
  all; it calls `check_coverage_freshness` (two `max()` probes, 60 s TTL cache).
  Unaffected by width.
- The daemon never reads `data_status`. No background loop pays this.
- `mt data status` is operator-initiated, ones-to-tens of times per day.
- `/api/v1/status` is the only unbounded-frequency reader, with no response
  cache.

## Criterion 12 status

**NOT satisfied by these numbers — they are a prediction.** Criterion 12 is a
prod NFR. Two caveats recorded for part 2's Task G:

1. The seed's raw tables are far smaller than prod's; the coverage-row shape
   matches but planning cost over prod's chunk counts does not.
2. **The NFR as stated (`SELECT count(*) FROM data_status`) is not a query any
   caller issues.** At 7 days it measures 2.850 s serial / ~0.98 s parallel,
   while a real full `mt data status` costs ~2.9 s and a single-symbol one
   ~1.86 s. The full-dump case already exceeds one second at every width
   including 365 days — i.e. **the NFR has been passing while the actual
   operator experience was multi-second.** Worth restating in 140-arch against a
   shape callers actually issue.

## D6 — materialization-hypertable chunk interval

Default (10x bucket width) left in place per D6. At 7 days that is a 70-day
chunk interval, yielding 119 chunks for `minute_coverage` and 35 for
`daily_coverage` — comfortably inside the healthy low-hundreds band from slices
166/170, and far from the over-chunking disease. No `set_chunk_time_interval`
needed.

## Follow-on issues to file

1. **Health-count full-universe scan** (highest value). `fetch_all_health_counts_with_freshness`
   (`status_queries.py:170`) ignores the symbol filter, so `mt data status SPY`
   pays a 12,040-symbol `GROUP BY health` — ~99% of its latency. Options in
   increasing value: (a) skip when a symbol filter is present, (b) TTL-cache the
   whole-registry summary, (c) accept the same filters as the row fetch. Any of
   these takes single-symbol status to ~0.01 s **at any width**.
2. **GitHub #14** — floor-plus-head-probe reshape of `bars_summary`, the only
   path to a genuinely good (~8 h) coverage bound.
3. **Restate the `data_status` NFR** against a caller-issued shape rather than
   `SELECT count(*)`.
