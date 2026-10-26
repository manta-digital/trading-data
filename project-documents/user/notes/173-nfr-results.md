---
docType: notes
slice: eligible-rollup-swap
project: trading-data
dateCreated: 20261026
dateUpdated: 20261026
---

# Slice 173 NFR re-runs and traceability

## NFR results

Run 2026-10-26 on the test cluster (TimescaleDB 2.29.1), branch `173-slice.eligible-rollup-swap`. The fixtures apply the whole migration chain, so they read the swapped-in eligible caggs under their current names:

```bash
export MT_RUN_LOAD_TESTS=1 MT_TIMESCALE_TEST_URL=…   # from .env, quotes stripped
uv run pytest -s -v test/load/test_167_data_status_nfr.py \
  test/load/test_169_coverage_freshness_probe_nfr.py test/load/test_187_api_nfr.py
# 16 passed, 1 failed (test_status_endpoint_latency, pre-existing; below) in 43 min
```

| Target | Pass number | Measured | Baseline | Result |
|---|---|---|---|---|
| `data_status` full-universe read | < 3.9 s | median 1.914 s (1.942, 1.900, 1.914) | 2.636 s (140-arch, 7-day coverage width) | PASS |
| `data_status` default summary (same test file) | < 3.9 s | median 1.066 s | — | PASS |
| Content-edge probe, `minute_coverage` | < 2.5 s median | 0.105 s | 169 recorded medians | PASS |
| Content-edge probe, `daily_coverage` | < 2.5 s median | 0.107 s | 169 recorded medians | PASS |
| Raw-edge probe, `minute_ohlcv` | < 2.5 s median | 0.046 s | 169 recorded medians | PASS |
| Raw-edge probe, `daily_ohlcv` | < 2.5 s median | 0.007 s | 169 recorded medians | PASS |
| Coverage policy runs fit their schedule (both families) | within schedule interval | passed | — | PASS |
| Bars at the 1m admission ceiling, `prints=all` | < 15 s | 4.804 s | 4.740 s raw (171 calibration) | PASS |
| Bars at the 1m admission ceiling, `prints=eligible` | < 15 s | 4.871 s | 4.794 s eligible (171 calibration) | PASS |
| **Bars at the 5m admission ceiling, default `prints` (new, slice 173)** | < 15 s | 4.878 s (window 2024-01-01..2025-07-18, eligible rollup, NULL-bucket filter) | — | PASS |
| `minute_coverage` bucket lag | within 8 d 4 h (`COVERAGE_BUCKET_LAG_BUDGET`) | pre-swap production: lag 0:00:00 (`cagg minute_coverage` row, 2026-10-26); post-swap: Phase 7 | — | Phase 7 |

**The one failure is pre-existing.** `test_187_api_nfr.py::test_status_endpoint_latency` measured a median of 2.852 s against its 1.5 s bound. Measured on `main` the same day in a worktree, it was 2.822 s. 064 re-issues `data_status` with unchanged SQL, so the swap does not move it. It is one of the four known load-tier failures on main.

**Bucket lag after the swap** is read in Phase 7 from production's `mt data health` (`cagg minute_coverage` row). The cutover's verify step also refuses unless that row passes.

## Traceability

| Design item | Covered by |
|---|---|
| SC1: the 171 SPY, AVGO and MSFT values are absent from 5m and 1h bars | `test/integration/api_server/test_bars_prints.py::test_known_off_tape_value_absent_from_rollup_bars` (6 cases × 5m, 1h, through the API); Phase 7 walkthrough step 2 |
| SC2: 5m+, `mt data status` and `/api/v1/status` serve without a gap through the swap (transactional path) | `test_migration_064_eligible_rollup_swap.py::test_a_reader_sees_no_missing_name_across_the_swap` (`data_status` and the 4h rollup read in a loop across 064); the rename probe in the design; `LOCK TABLE data_status` first in the transaction, so a live reader cannot deadlock the renames |
| SC3: after the cutover, verify exits 0, coverage fresh, value parity passes, no `_next`/`_prev`, default eligible, `prints=all` at 5m is 422 | The cutover's verify step (`caggs verify`, swap state DONE, `mt-run data health` rows); `test_cutover_173.py` failure cases for each; `_assert_swapped` in the 064 tests; `test_bars_prints.py` default and 422 tests; Phase 7 walkthrough step 1 |
| SC4: a rerun after a kill at any step completes the swap; a failure past a rolled-back rename leaves the timers held | `test_cutover_173.py::test_kill_mid_swap_then_rerun_completes` plus one test per failure-table row (precheck, rolled-back rename, failed drop, post-swap verify, health, leftover `_prev`, install, unreadable state); `test_migration_064…::test_rerun_after_a_failure_before_the_drops_converges` and `…finished_swap_changes_nothing` |
| SC5: NFR targets | The table above |
| TR: probe first | Design, Technical Decisions → The swap, "Probe results" |
| TR: 064 tests (cold, populated, partial rerun, no-gap reader) | `test/integration/test_migration_064_eligible_rollup_swap.py` (6 tests) |
| TR: a bucket of only all-marked minutes is absent from the bars response | `test_bars_prints.py::test_a_bucket_of_only_marked_minutes_is_absent`; `test_timescale_minute_db.py::test_rollup_read_drops_buckets_with_no_eligible_price` |
| TR: value-parity failure states and the most-marked sample | `test/unit/cli/commands/test_health_value_parity.py` (differing bucket, differing NULL bucket, `minute_count`, missing side, first bucket named, no marked minute, no session); `test/integration/test_health_value_parity.py` (pass, all-marked bucket compared, altered row named, no marks, most-marked symbol, ties by symbol) |
| TR: quality bar | `ruff` and `mypy` report nothing new on the slice's files (the findings left are on lines already on main); API references, CHANGELOG and runbook 100 updated; `check_api_docs.py` exits 0 |
| IR: accounting, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after | `test_migration_064…::test_readers_count_the_same_before_and_after` (`data_status` rows, health counts behind `/api/v1/status`, `compute_minute_accounting`, `build_minute_coverage_index`) |
| IR: mt-ui's 5m-and-up charts show no spikes with no client change | **Not automated.** Phase 7 walkthrough step 2: open mt-ui on SPY 2026-09-08 and MSFT 2026-06-01 at 5m and up after the cutover |

## Production rehearsal (read-only)

On 2026-10-26 the value-parity check ran read-only against production's `minute_5min_ohlcv_next`, the object 064 swaps in under the current name. It sampled META on 2026-10-23 (29 marked minutes), and all 192 5m buckets equalled the eligible recompute. The query took 0.1 s.

## Validation tiers

- Unit: 5153 passed, 5 skipped.
- Integration: 1136 passed, 128 skipped, 11 failed. One was this slice's stale test (`test_eligible_minute_read::test_eligible_rejects_aggregation`), now fixed and passing. Six are the known main failures: `test_cli_lists` priority1 ×2, `test_migration_051_052` ×2, `test_policy_advances_head` ×2. Four fail identically on main and are filed as #56: `test_health_tick` ×2, where 924's backup row fails without `MT_BACKUP_ROOTS`, and `test_coverage_content_edge` ×2, where the daily lag is one hour short of its expectation.
