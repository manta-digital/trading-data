---
docType: notes
layer: project
project: trading-data
slice: futures-completeness-surfaces
audience: [human, ai]
description: Slice 305 manifest-scale measurement of the futures completeness surfaces at 307's size, the D10 fixes and the two bounds still missed
dateCreated: 20261108
dateUpdated: 20261108
status: complete
---

# 305 manifest-scale measurement

Measured 2026-11-08 with `MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_305_manifest_scale_nfr.py -s -rx`
(the run's output is kept in `/tmp/305-measurement.log`). Result: 4 passed, 2 xfailed
(strict, issue [#74](https://github.com/manta-digital/trading-data/issues/74)).

## The seed

One throwaway tick database on the test cluster, migrated to head, built by
`test/load/manifest_scale_seed.py` and `manifest_scale_rows.py`, dropped by the
fixture's finalizer (verified: no `mt_test_m*` database remained).

- **30,729 units in 1,192 requests**, all `ingested`: `definition`, `statistics` and
  `ohlcv-1m` for ES and GC over every UTC day a real-calendar session touches
  (2010-06-08 to 2026-11-04), plus the held `tbbo` window (2024-11-02 to 2025-01-01).
- **19,540,026 `tick_statistics` rows** (target 19.4 M), every chunk compressed.
- Ledgers, definitions and day conditions at the proportions of the real 2024 fixtures
  (41 instruments a day: 21 outrights, 20 spreads; the fixtures' stat types per instrument;
  the five bar instruments of the derived `ohlcv-1m` day). Every outright reports each
  required stat type, so a clean seed has no content findings.
- Build time: about 100 s.

## Host

- **Database:** the dedicated test cluster (conftest: 20 cores, 62 GiB, reached over the LAN),
  PostgreSQL 17.11, TimescaleDB 2.29.1, `shared_buffers` 8 GB, `work_mem` 64 MB,
  `jit` on (`jit_above_cost` 100000), `max_parallel_workers_per_gather` 2.
- **Client:** manta9000 (AMD Ryzen 9 5950X, 32 threads, 125 GiB).
- Per `test/load/conftest.py`, a breach on different hardware is not automatically a
  regression; these bounds were set for the production host.

## Result (median of three after one warm-up)

| Operation | Bound | Samples (s) | Median | |
|---|---|---|---|---|
| `futures_completeness(cross_tier=True)`, both products, every family | ≤ 5 s; no statement ≥ 5 s | 7.50, 7.48, 7.44 | **7.48 s** | miss |
| slowest single statement of that read | < 5 s | | 0.93 s | pass |
| API tick block (`cross_tier=False`) | < 0.25 s | 3.85, 3.86, 3.82 | **3.85 s** | miss |
| `build_status`, both products | ≤ 30 s | 7.45, 7.76, 7.51 | 7.51 s | pass |
| `select_units`, nothing selectable | ≤ 2 s | 0.00, 0.00, 0.00 | 0.00 s | pass |
| tick preflight + steady `pass --estimate-only` | ≤ 10 s | 0.35, 0.40, 0.42 | 0.40 s | pass |

## Slowest statement and its plan

The slowest statement after the fixes (the findings statement, one product):

```
slowest statement (0.93 s): 
WITH 
complete (session_date, open_ns, close_ns) AS (
    SELECT * FROM unnest(
        %(dates)s::date[], %(opens)s::bigint[], %(closes)s::bigint[])
),
symbols AS (
    SELECT DISTINCT ON (instrument_id) instrument_id, raw_symbol
    FROM tick_definition
    WHERE asset = %(product)s AND instrument_class = %(outright)s
    ORDER BY instrument_id, activation_ns
),
listed AS (
    SELECT DISTINCT c.session_date, d.instrument_id
    FROM complete c JOIN tick_definition d
      ON d.asset = %(product)s AND d.instrument_class = %(outright)s
     AND d.activation_ns <= c.open_ns AND d.expiration_ns >= c.close_ns
)
,
held AS (
    SELECT l.instrument_id, l.session_date, l.stat_type
    FROM tick_statistics_ledger l JOIN tick_archive_unit u USING (unit_id) JOIN tick_request r USING (request_id)
    WHERE r.dataset = %(dataset)s AND r.symbols = %(symbols)s AND r.stype_in = %(stype_in)s AND r.schema = ANY(%(schemas)s) AND superseded_by_unit_id IS NULL AND reopened_at IS NULL AND l.record_count > 0
      AND l.session_date BETWEEN %(first)s AND %(last)s
    GROUP BY 1, 2, 3
),
reported AS (SELECT DISTINCT instrument_id, stat_type FROM held),
owed AS (
    SELECT instrument_id, stat_type FROM reported
    WHERE stat_type = ANY(%(required)s::smallint[])
    UNION
    SELECT s.instrument_id, a.stat_type
    FROM symbols s CROSS JOIN unnest(%(always)s::smallint[]) AS a (stat_type)
),
needed AS (
    SELECT l.session_date, l.instrument_id, o.stat_type
    FROM listed l JOIN owed o ON o.instrument_id = l.instrument_id
)
SELECT n.session_date, n.instrument_id, s.raw_symbol,
       array_agg(n.stat_type ORDER BY n.stat_type)
FROM needed n
JOIN symbols s ON s.instrument_id = n.instrument_id
LEFT JOIN held h ON h.instrument_id = n.instrument_id
 AND h.session_date = n.session_date AND h.stat_type = n.stat_type
WHERE h.instrument_id IS NULL
GROUP BY n.session_date, n.instrument_id, s.raw_symbol
ORDER BY n.session_date, n.instrument_id
parameters: one array each of the 4,238 sessions' dates, opens and closes (ns); product 'ES'; outright class 'F'; always [3]; required [3, 6, 9]; shape ES.FUT / parent / statistics
  GroupAggregate  (cost=197460.37..241489.17 rows=617839 width=76) (actual time=945.927..1070.691 rows=126 loops=1)
    Group Key: unnest.unnest, d.instrument_id, s.raw_symbol
    Buffers: shared hit=33351
    CTE symbols
      ->  Unique  (cost=67.98..69.07 rows=218 width=22) (actual time=0.135..0.153 rows=218 loops=1)
            Buffers: shared hit=26
            ->  Sort  (cost=67.98..68.52 rows=218 width=22) (actual time=0.135..0.140 rows=218 loops=1)
                  Sort Key: tick_definition.instrument_id, tick_definition.activation_ns
                  Sort Method: quicksort  Memory: 33kB
                  Buffers: shared hit=26
                  ->  Seq Scan on tick_definition  (cost=0.00..59.51 rows=218 width=22) (actual time=0.008..0.121 rows=218 loops=1)
                        Filter: ((asset = 'ES'::text) AND (instrument_class = 'F'::text))
                        Rows Removed by Filter: 2016
                        Buffers: shared hit=26
    CTE held
      ->  HashAggregate  (cost=66585.28..68739.38 rows=215410 width=14) (actual time=303.863..403.775 rows=893513 loops=1)
            Group Key: l.instrument_id, l.session_date, l.stat_type
            Batches: 1  Memory Usage: 86033kB
            Buffers: shared hit=33299
            ->  Nested Loop  (cost=51.26..63902.75 rows=357671 width=14) (actual time=1.178..150.595 rows=1076522 loops=1)
                  Buffers: shared hit=33299
                  ->  Hash Join  (cost=50.83..1122.07 rows=5104 width=8) (actual time=1.166..3.895 rows=5104 loops=1)
                        Hash Cond: (u.request_id = r.request_id)
                        Buffers: shared hit=709
                        ->  Seq Scan on tick_archive_unit u  (cost=0.00..990.29 rows=30729 width=16) (actual time=0.007..2.484 rows=30729 loops=1)
                              Filter: ((superseded_by_unit_id IS NULL) AND (reopened_at IS NULL))
                              Buffers: shared hit=683
                        ->  Hash  (cost=48.35..48.35 rows=198 width=8) (actual time=0.128..0.128 rows=198 loops=1)
                              Buckets: 1024  Batches: 1  Memory Usage: 16kB
                              Buffers: shared hit=26
                              ->  Seq Scan on tick_request r  (cost=0.00..48.35 rows=198 width=8) (actual time=0.034..0.116 rows=198 loops=1)
                                    Filter: ((schema = ANY ('{statistics}'::text[])) AND (dataset = 'GLBX.MDP3'::text) AND (symbols = '{ES.FUT}'::text[]) AND (stype_in = 'parent'::text))
                                    Rows Removed by Filter: 994
                                    Buffers: shared hit=26
                  ->  Index Scan using tick_statistics_ledger_pkey on tick_statistics_ledger l  (cost=0.43..10.20 rows=210 width=22) (actual time=0.005..0.020 rows=211 loops=5104)
                        Index Cond: ((unit_id = u.unit_id) AND (session_date >= '2010-06-09'::date) AND (session_date <= '2026-11-02'::date))
                        Filter: (record_count > 0)
                        Buffers: shared hit=32590
    ->  Incremental Sort  (cost=128651.92..158779.34 rows=617839 width=46) (actual time=945.920..1070.622 rows=378 loops=1)
          Sort Key: unnest.unnest, d.instrument_id, s.raw_symbol, held.stat_type
          Presorted Key: unnest.unnest, d.instrument_id
          Full-sort Groups: 12  Sort Method: quicksort  Average Memory: 26kB  Peak Memory: 26kB
          Buffers: shared hit=33351
          ->  Merge Anti Join  (cost=128651.38..138056.63 rows=617839 width=46) (actual time=945.905..1070.566 rows=378 loops=1)
                Merge Cond: ((unnest.unnest = h.session_date) AND (d.instrument_id = h.instrument_id) AND (held.stat_type = h.stat_type))
                Buffers: shared hit=33351
                ->  Sort  (cost=105261.38..107026.64 rows=706102 width=46) (actual time=710.067..723.675 rows=266607 loops=1)
                      Sort Key: unnest.unnest, d.instrument_id, held.stat_type
                      Sort Method: quicksort  Memory: 22703kB
                      Buffers: shared hit=33351
                      ->  Merge Join  (cost=25898.86..36665.28 rows=706102 width=46) (actual time=612.754..658.603 rows=266607 loops=1)
                            Merge Cond: (held.instrument_id = d.instrument_id)
                            Buffers: shared hit=33351
                            ->  Merge Join  (cost=5319.35..5486.14 rows=3239 width=50) (actual time=541.581..564.561 rows=654 loops=1)
                                  Merge Cond: (held.instrument_id = s.instrument_id)
                                  Buffers: shared hit=33325
                                  ->  Unique  (cost=5319.35..5392.71 rows=3239 width=10) (actual time=541.578..564.394 rows=655 loops=1)
                                        Buffers: shared hit=33325
                                        ->  Merge Append  (cost=5319.35..5376.52 rows=3239 width=10) (actual time=541.578..564.333 rows=873 loops=1)
                                              Sort Key: held.instrument_id, held.stat_type
                                              Buffers: shared hit=33325
                                              ->  Unique  (cost=5304.32..5328.55 rows=3021 width=10) (actual time=541.380..564.051 rows=655 loops=1)
                                                    Buffers: shared hit=33299
                                                    ->  Sort  (cost=5304.32..5312.40 rows=3231 width=10) (actual time=541.379..554.938 rows=266698 loops=1)
                                                          Sort Key: held.instrument_id, held.stat_type
                                                          Sort Method: quicksort  Memory: 23268kB
                                                          Buffers: shared hit=33299
                                                          ->  CTE Scan on held  (cost=0.00..5115.99 rows=3231 width=10) (actual time=303.866..503.957 rows=351341 loops=1)
                                                                Filter: (stat_type = ANY ('{3,6,9}'::smallint[]))
                                                                Rows Removed by Filter: 542172
                                                                Buffers: shared hit=33299
                                              ->  Sort  (cost=15.02..15.56 rows=218 width=10) (actual time=0.197..0.207 rows=218 loops=1)
                                                    Sort Key: s_1.instrument_id, a.stat_type
                                                    Sort Method: quicksort  Memory: 31kB
                                                    Buffers: shared hit=26
                                                    ->  Nested Loop  (cost=0.00..6.55 rows=218 width=10) (actual time=0.143..0.183 rows=218 loops=1)
                                                          Buffers: shared hit=26
                                                          ->  Function Scan on unnest a  (cost=0.00..0.01 rows=1 width=2) (actual time=0.006..0.006 rows=1 loops=1)
                                                          ->  CTE Scan on symbols s_1  (cost=0.00..4.36 rows=218 width=8) (actual time=0.136..0.167 rows=218 loops=1)
                                                                Buffers: shared hit=26
                                  ->  Materialize  (cost=0.00..4.91 rows=218 width=40) (actual time=0.001..0.068 rows=652 loops=1)
                                        ->  CTE Scan on symbols s  (cost=0.00..4.36 rows=218 width=40) (actual time=0.000..0.025 rows=218 loops=1)
                            ->  Sort  (cost=20579.51..20688.51 rows=43600 width=12) (actual time=71.171..79.832 rows=266605 loops=1)
                                  Sort Key: d.instrument_id
                                  Sort Method: quicksort  Memory: 5850kB
                                  Buffers: shared hit=26
                                  ->  HashAggregate  (cost=16783.68..17219.68 rows=43600 width=12) (actual time=59.325..64.434 rows=88869 loops=1)
                                        Group Key: unnest.unnest, d.instrument_id
                                        Batches: 1  Memory Usage: 7185kB
                                        Buffers: shared hit=26
                                        ->  Nested Loop  (cost=0.01..16270.41 rows=102654 width=12) (actual time=0.372..49.765 rows=88869 loops=1)
                                              Join Filter: ((d.activation_ns <= unnest.unnest_1) AND (d.expiration_ns >= unnest.unnest_2))
                                              Rows Removed by Join Filter: 835015
                                              Buffers: shared hit=26
                                              ->  Function Scan on unnest  (cost=0.01..42.39 rows=4238 width=20) (actual time=0.367..0.655 rows=4238 loops=1)
                                              ->  Materialize  (cost=0.00..60.60 rows=218 width=24) (actual time=0.000..0.004 rows=218 loops=4238)
                                                    Buffers: shared hit=26
                                                    ->  Seq Scan on tick_definition d  (cost=0.00..59.51 rows=218 width=24) (actual time=0.002..0.125 rows=218 loops=1)
                                                          Filter: ((asset = 'ES'::text) AND (instrument_class = 'F'::text))
                                                          Rows Removed by Filter: 2016
                                                          Buffers: shared hit=26
                ->  Sort  (cost=23390.00..23928.52 rows=215410 width=14) (actual time=222.717..279.923 rows=893428 loops=1)
                      Sort Key: h.session_date, h.instrument_id, h.stat_type
                      Sort Method: quicksort  Memory: 52499kB
                      ->  CTE Scan on held h  (cost=0.00..4308.20 rows=215410 width=14) (actual time=0.001..27.276 rows=893513 loops=1)
  Planning:
    Buffers: shared hit=44
  Planning Time: 0.471 ms
  Execution Time: 1076.669 ms
```

## D10: what was fixed, with before and after

The first run, before any fix:

| Operation | Median before | Median after |
|---|---|---|
| `futures_completeness(cross_tier=True)` | 48.82 s (samples 82.26, 48.82, 14.93) | 7.48 s |
| API tick block | 6.40 s | 3.85 s |
| `build_status` | 49.39 s | 7.51 s |

1. **JIT (the 37 s statement).** The slowest statement before the fixes was cleared-volume
   resolution (`resolved`, inside `volume_check`) over the full range: 37.38 s. Its
   `EXPLAIN (ANALYZE, BUFFERS)` showed 0.13 s of scanning and `JIT: Functions: 5204`,
   optimization 66.9 s and emission 41.7 s: the plan spans about 800 chunks, so its cost
   clears `jit_above_cost`. With `jit = off` the same statement ran in 0.42 s. Every tick
   read connection now sets it (`TICK_JIT_OPTION` for the store and composed connections,
   `TICK_JIT_STATEMENT` for the API pool). Not a query rewrite or an index, but a session
   setting with no stored state; unit tests pin all three paths.
2. **Content findings.** `content_findings` was per-session Python over a 940 k-row ledger
   read, about 6 s of the 9 s left once JIT was off. It is now one set-based statement
   (~0.8 s per product), with the same findings (the existing statistics status and real-fixture tests pass unchanged).
   A first version used a lateral join that scanned the held set once per row and ran for
   minutes; the plan showed it and it was replaced by one precomputed `owed` set.
3. **Listing outrights per session** in `volume_check` scanned every definition window per
   session in Python (1.5 s). It now shares the findings statement's listing CTEs.

## What remains, and why it is an issue

Two bounds are still missed: the cross-tier read (7.48 s against 5 s) and the API block
(3.85 s against 0.25 s). No statement is slow any more (the slowest is 0.93 s, so the 2x margin against the
composed 10 s statement timeout holds); the remaining time is session judging in Python
(about 25,000 sessions across three families and two products on every call) plus 27 to 39
statements of 0.2 to 0.9 s. Reaching the bounds needs a stored or cached per-family
summary refreshed at ingest, or judging moved into SQL: derived state or a change in what
`status` computes, which D10 puts outside this slice. Issue
[#74](https://github.com/manta-digital/trading-data/issues/74) carries the measurement;
**307 depends on it**, since 307 creates this scale in production. Until it is
fixed, `mt data health` and `mt data accounting` at full scale take about 7.5 s, still
inside the composed 10 s statement timeout per statement, and the two tests are
`xfail(strict=True)`, so they fail the day the bound is met and the marker is removed
with the fix. No bound was raised.
