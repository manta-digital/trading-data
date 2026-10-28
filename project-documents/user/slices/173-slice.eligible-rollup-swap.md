---
docType: slice-design
slice: eligible-rollup-swap
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [172, 168, 169]
interfaces: [182, 186, 190]
dateCreated: 20261016
dateUpdated: 20261026
status: complete
---

# Slice Design: Eligible Rollup Swap

## Overview

Slice 172 builds eligible 5m, 15m, 1h and 4h rollups, and an eligible `minute_coverage`, beside the current ones and fills them in production. This slice:

- swaps them in by rename, with no read gap;
- makes `prints=eligible` the default for every granularity;
- adds a health check that keeps the rollups and the eligible 1m path from drifting apart;
- re-runs the NFR targets the swap touches.

It was split from 172 on 2026-10-16 (PM decision): this release can only go out after 172's build has verified in production. The design review findings on the swap (172 review, 2026-10-16) are answered here.

## Value

- The UI's 5m-and-up charts lose the spikes with no client change.
- Every bars consumer gets eligible bars by default, with raw still available at 1m.
- A standing health check catches a rollup definition that drifts from the 1m eligible path.

## Technical Scope

**Included:**

1. Migration 064: the rename swap, the `data_status` replacement, and the drop of the old objects.
2. A cutover script: precheck 172's build, install, 064, verify, restart, report.
3. The `/api/v1/bars` and `get_minute_data` default becomes `prints=eligible`. `prints=all` at 5m+ is a 422. NULL-price buckets are dropped on read.
4. A health check: rollup values equal an eligible recompute for a sampled symbol-session.
5. Post-swap re-runs of the NFR targets, plus one 5m bars load case.
6. Documentation: the 140 architecture caggs amendment; both API references (the default change, flagged as client-visible); CHANGELOG; runbook 100 (the cutover).

**Excluded:**

- **The `_next` build:** slice 172.
- **Daily-family rollups:** weekly, monthly and quarterly come from EOD bars.

## Dependencies

### Prerequisites

- **172, built in production:** `mt data caggs verify --target next` exits 0, and `rebuild-coverage --verify --target next` passes.
- **168:** `assert_cagg_fresh`, which guards the seeder if it ever reads an incomplete coverage rollup.
- **169:** the coverage helpers that create `data_status`.

### Interfaces Required

- `CAGG_NEXT_SUFFIX` and `CaggTarget` from 172.
- The `data_status` creation helper.

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| Swap | `market/schema/migrations/minute.py` (064) | Rename current → `_prev`, `_next` → current, replace `data_status`, drop `_prev` |
| Cutover | `scripts/cutover_173_eligible_rollup_swap.py` | Precheck → hold timers → install → 064 → verify → restart serve → release timers → report |
| Value parity check | `cli/commands/health.py` | Rollup vs eligible recompute for the sampled symbol-session |
| Read path | `market/timescale_minute_db.py`, `api_server/routes/bars.py` | Default `eligible`; 422 for `all` at 5m+; NULL-price buckets dropped |

### Data Flow

```
cutover: hold timers (minute timer stopped) ─► install ─► 064:
    rename minute_*_ohlcv, minute_coverage        → *_prev
    rename minute_*_ohlcv_next, minute_coverage_next → current names
    CREATE OR REPLACE VIEW data_status             (binds to the new coverage)
    drop *_prev                                    (coverage first)
─► verify ─► restart mt-serve ─► release timers

/api/v1/bars (default eligible) ◄─ rollups (NULL-price buckets dropped)
```

## Technical Decisions

### The swap

The renames and the `data_status` replacement run in one transaction, so readers see the old set or the new set and nothing in between. Creating and refreshing a cagg needs autocommit; renaming one is ordinary DDL.

Two behaviors are probed on the test cluster (TimescaleDB 2.29.1) before 064 is written:

- renaming a cagg inside a transaction;
- renaming a cagg that has a hierarchical cagg on top of it (`minute_coverage` sits on the 4h rollup).

If TimescaleDB refuses either, 064 falls back to guarded autocommit statements, and the read gap becomes the time between the first and last rename. The drops always run in their own statements after the commit.

**Probe results (2026-10-26, test cluster, TimescaleDB 2.29.1, throwaway database):**

- Renaming a cagg inside a transaction works: four `ALTER MATERIALIZED VIEW ... RENAME TO` statements (4h rollup and coverage, current → `_prev`, then `_next` → current) and a `CREATE OR REPLACE VIEW` over the new coverage committed as one transaction.
- Renaming a cagg with a hierarchical cagg on top works: the upper cagg binds to its parent by OID, so the renamed `_next` coverage reads from the renamed `_next` 4h rollup (its definition shows the new parent name).
- After the renames the upper cagg refreshes and reads, the policy jobs follow their caggs (`timescaledb_information.jobs` shows the new names, and `run_job` succeeds on both), and dropping coverage `_prev` then the 4h `_prev` leaves only the current caggs' jobs.
- **Path chosen: transactional renames.** The autocommit fallback is not built. Every step is guarded (`IF EXISTS`; a rename only if the source exists and the target name is free), so a rerun completes a partly applied 064.

**Cold start.** On a fresh database 063 and 064 run back to back on empty objects, so a cold start ends in the same state.

**Release gate.** The release is not cut until 172's build verifies in production, and it goes out through this cutover script, not `upgrade_production.py`. The default switch must not reach production before the rollups hold eligible values: until then, `prints=eligible` at 5m+ would label raw rollups as eligible. The value-parity check also fails by design against raw rollups. Within the script, 064 applies before mt-serve restarts. For that short window the old code serves eligible rollups and reports `prints=all`.

### Failure handling (cutover)

| Trigger | Effect | Handling |
|---|---|---|
| Precheck: 172's build not verified | Cutover refused | Non-zero exit naming the view; nothing changed |
| 064's rename transaction fails | Rolled back: the old set still serves | Timers released (nothing changed); non-zero exit naming the step |
| 064 fails in autocommit fallback, or a `_prev` drop fails | Names in a mixed state, or leftover `_prev` objects | Timers stay **held** and the script exits non-zero with the step named. `mt data health` fails on cagg freshness, and the operator overview shows the timer stopped. Rerun: 064's guarded steps finish the swap. A leftover `_prev` after a committed rename is harmless to readers |
| Post-swap `verify` or coverage freshness fails | New set in place but not proven | Timers stay held, non-zero exit; rerun re-checks |
| Script killed while timers are held | Timers stopped | Same signals; a rerun detects swap state and finishes |

`assert_cagg_fresh` (slice 168) is the second guard: if the seeder ever ran against an incomplete coverage rollup, it would refuse to seed rather than seed from it.

### What readers see during the cutover

- **Clean path:** the rename transaction swaps the whole set at once, so no reader sees a missing name. From the commit on, 5m+ bars carry eligible values.
- **Autocommit fallback, or a failure between renames:** a 5m+ bars request or a `data_status` read that hits a missing name gets the API's existing 500 (`UndefinedTable` reaches the global handler, which logs it). No 503 is added. The window is either the time between guarded statements or a failed cutover. A failed cutover is surfaced by the held timer, the failing `mt data health` and the script's non-zero exit, and the fix is to rerun the script.

### API default

- `prints` defaults to `eligible` for every granularity.
- `prints=all` is valid at 1m only. At 5m+ it returns `422` with "prints=all is served at 1m only; roll up the 1m bars". 171's `PRINTS_ELIGIBLE_1M_ONLY_MESSAGE` is removed.
- Daily and coarser granularities accept `eligible` and return their EOD-based bars.
- The rollup read drops NULL-price buckets (`WHERE open IS NOT NULL`), the same way an empty bucket is absent today, so `adjusted()` never sees a NULL price.
- This is a client-visible change: both API references and the CHANGELOG say so, and mt-ui is told before release.

### Standing value check

`cagg_parity` compares counts. It catches under-materialization, but not a rollup whose definition differs from the 1m eligible path. Both are rendered from `eligible_sql.py`, so they agree when the swap runs. They would drift if the renderer later changed without a new rollup migration. Add one `mt data health` check: over the last collected session, every 5m rollup row for the sampled symbol equals the eligible 1m bars rolled up in SQL. It fails on any difference, naming the first bucket that differs.

- **Sample.** The symbol is chosen by query, not by a constant: the one with the most marked minutes in that session, ties broken by symbol. The check therefore always exercises the marked-minute paths. A fixed liquid symbol such as SPY can have no marks on a given session and would only prove the unmarked path.
- **Columns.** Every rollup column, `minute_count` included, compared with `IS NOT DISTINCT FROM`, so an all-marked bucket (NULL prices and volume) is compared, not skipped.
- **No marked minute in the session.** The check fails with that reason. It has nothing to prove, and `off-tape share` fails on the same session anyway.

### NFR targets touched by the swap

From the 140 architecture, re-run after the swap and recorded. Each has a pass number:

| Target | Pass | Baseline to compare | Test |
|---|---|---|---|
| `data_status` full-universe read | < 3.9 s (half the 7.8 s raw scan slice 167 removed) | 2.636 s, the 140-arch figure at the 7-day coverage width | `test/load/test_167_data_status_nfr.py` |
| Coverage freshness probes (content edge, raw source) | < 2.5 s median (25% of the 10 s probe budget) | the 169 recorded medians | `test/load/test_169_coverage_freshness_probe_nfr.py` |
| `minute_coverage` bucket lag | within 8 d 4 h (`COVERAGE_BUCKET_LAG_BUDGET`) | n/a, a bound | `mt data health` coverage freshness |
| Bars request at the admission ceiling | < 15 s (`_BARS_BOUND_S`, 187 D10.2) | 4.794 s eligible / 4.740 s raw at 1m (171 calibration) | `test/load/test_187_api_nfr.py` |

The bars tier today covers 1m only. Add one 5m case at the 5m admission ceiling under the same 15 s bound, reading the new rollup with the NULL-bucket filter. 5m is the densest rollup, so 15m, 1h and 4h are not added.

## Implementation Details

### Database / Storage Schema

- **064:** the swap described above. It frees the 62 GB the old rollups held.

### API Contracts

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `prints` | `eligible`, `all` | `eligible` (was `all`) | `all` valid with `granularity=1m` only |

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | Phase 7, cutover script | Precheck 172's build; hold timers; install; 064; verify; restart mt-serve; release timers; report |

The timers are held and released through the existing NOPASSWD unit commands (`cutover_common`), so no PM host step is needed.

## Integration Points

### Provides to Other Slices

- **Eligible rollups at 5m, 15m, 1h and 4h** under their existing names: every current reader keeps working.
- **The value-parity health check:** a standing guard on the eligible definition.
- **182/186/190:** the bars default changes to eligible, documented as a client-visible change.

### Consumes from Other Slices

- **172:** the five verified `_next` objects; `CaggTarget`.
- **168:** the seeder guard.
- **169:** the `data_status` helper.

## Success Criteria

### Functional Requirements

1. The SPY, AVGO and MSFT values from slice 171's criteria are absent from the 5m and 1h bars.
2. 5m and up, `mt data status` and `/api/v1/status` serve without a gap through the swap on the transactional path.
3. After the cutover:
   - `mt data caggs verify` exits 0 on all four rollups;
   - coverage is fresh;
   - the value-parity check passes;
   - no `_next` or `_prev` object remains;
   - `/api/v1/bars` defaults to eligible, and `prints=all` at 5m returns 422.
4. Rerunning the cutover script after a kill at any step completes the swap. A failure after the timers are held, other than a rolled-back rename transaction, leaves them held.
5. Each NFR target meets its pass number in the table above.

### Technical Requirements

- **Probe first:** cagg rename inside a transaction, and rename of a cagg with a hierarchical cagg on top, on TimescaleDB 2.29.1. The result picks 064's path and is recorded in this design.
- **Migration integration tests:** 063 and 064 on a cold start; 064 on a database holding both sets with data; a 064 rerun after a partial apply; a reader of `data_status` and the 4h rollup that sees no missing name across 064 (transactional path).
- **Read path:** a bucket of only all-marked minutes is absent from the bars response.
- **Health:** unit tests for the value-parity check's failure states (a differing bucket, a differing NULL bucket, no marked minute); the sample query picks the most-marked symbol.
- **Quality bar:** `ruff` and `mypy` clean; API references, CHANGELOG and runbook 100 updated.

### Integration Requirements

- `mt data accounting`, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after the swap.
- mt-ui's 5m-and-up charts show no spikes with no client change.

### Verification Walkthrough

Steps 1 and 2 run in production in Phase 7, after the release is tagged and pushed. Step 3 ran in Phase 6 (results in `user/notes/173-nfr-results.md`).

1. Production (Phase 7), as `manta` from the dev checkout at the release ref:
   ```bash
   cd ~/source/repos/manta/trading-data && git fetch && git checkout vX.Y.Z
   uv run python scripts/cutover_173_eligible_rollup_swap.py vX.Y.Z
   # last line: "report written to project-documents/user/notes/<date>-173-cutover.md — PASS", exit 0
   uv run mt data caggs verify --granularity all          # exit 0, all four rollups in parity
   sudo -n /usr/local/bin/mt-run data health              # "value parity" PASS (no --record: only the timer records)
   psql "$MT_TIMESCALE_DB_URL" -Atc "SELECT count(*) FROM pg_class WHERE relname ~ '^minute_.*_(next|prev)$'"   # 0
   ```
   The health check reads production's settings through `mt-run`; a dev-checkout `mt data health` lacks `MT_MINUTE_FIRING_DAYS` and shows false minute FAILs. Rehearsed read-only on 2026-10-26 against production's `minute_5min_ohlcv_next` (the object 064 swaps in): the check sampled META on 2026-10-23 (29 marked minutes) and all 192 5m buckets equalled the eligible recompute, in 0.1 s.
2. Charts, against mt-serve (port 8100, `mt serve`'s default; listening there on manta9000 2026-10-26):
   ```bash
   B="localhost:8100/api/v1/bars/MSFT?start=2026-06-01&end=2026-06-02&adjusted=false"
   curl -s "$B&granularity=5m" | jq '.prints, ([.bars[]|select(.timestamp>="2026-06-01T20:40")|.high]|max)'
   # "eligible"; the after-hours high is the eligible one, below the raw 1m high for the same span
   curl -s -o /dev/null -w "%{http_code}\n" "$B&granularity=5m&prints=all"   # 422
   curl -s "$B&granularity=1m&prints=all" | jq .prints                       # "all": raw prints still served at 1m
   ```
   Then open mt-ui's 5m-and-up charts for SPY 2026-09-08 and MSFT 2026-06-01: no after-hours spikes, no client change.
3. NFR re-runs (Phase 6, test cluster, `MT_RUN_LOAD_TESTS=1` and `MT_TIMESCALE_TEST_URL` exported):
   ```bash
   uv run pytest -s test/load/test_167_data_status_nfr.py test/load/test_169_coverage_freshness_probe_nfr.py test/load/test_187_api_nfr.py -v
   ```
   Each passing test prints its measured median; record it against its pass number. The `minute_coverage` bucket lag is read from production's `mt data health` after the cutover (`cagg minute_coverage` row, threshold 8 days 4:00:00).

## Risk Assessment

### Technical Risks

- **The swap touches the coverage source** the acquisition seeder reads.
- **Client-visible default change:** a consumer that relied on raw 5m+ bars sees different highs and lows.

### Mitigation Strategies

- **The swap:**
  - coverage was built and verified by 172, so the swap is renames only;
  - it runs with the timers held;
  - any failure past a rolled-back rename leaves them held;
  - `assert_cagg_fresh` guards the seeder anyway.
- **The default change:**
  - raw stays available at 1m;
  - the change is announced in the CHANGELOG and both API references, and mt-ui is told before release.

## Implementation Notes

### Development Approach

1. The rename probe on the test cluster.
2. Migration 064, its guarded steps, and its tests (cold, populated, partial-rerun, no-gap reader).
3. The read path: default, 422, NULL-bucket filter.
4. The value-parity health check.
5. The cutover script and its failure-table tests.
6. The 5m bars load case.
7. Documentation, then the NFR re-runs, recorded.

### Special Considerations

- **The 140 architecture amendment** records three changes:
  - the four minute caggs project tape-eligible values, not raw OHLCV;
  - `get_minute_data`/`/api/v1/bars` default to eligible;
  - `verify` remains the count-parity detector, with the new value-parity health check as the guard on the definition.
