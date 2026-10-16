---
docType: slice-design
slice: eligible-rollups
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [171, 163, 168, 169]
interfaces: [182, 186, 190]
dateCreated: 20261016
dateUpdated: 20261016
status: not_started
---

# Slice Design: Eligible Rollups

## Overview

Slice 171 marks off-tape prints on every minute bar and serves eligible 1m bars on request. The 5m, 15m, 1h and 4h rollups still aggregate raw values, so the UI's spikes remain at those granularities. This slice:

- rebuilds the four minute rollups from tape-eligible values;
- swaps them in without a serving gap;
- makes `prints=eligible` the default for every granularity.

It was split out of 171 after the design review (2026-10-16). The review findings on the rollup swap are answered here.

## Value

- The UI's 5m-and-up charts lose the spikes with no client change. In the 171 prototype, SPY 5m bars with a wick over 1% fall from 249 to 1, MSFT from 859 to 95, and NVDA from 835 to 28.
- Every bars consumer gets eligible bars by default, with raw still available at 1m.
- A standing health check keeps the rollups and the 1m eligible path from drifting apart.

## Technical Scope

**Included:**

1. Migrations 063/064: build `_next` rollups with eligible definitions (plus a `minute_coverage_next` over the new 4h rollup), then swap them in by rename.
2. `mt data caggs repair`, `verify` and `rebuild-coverage` learn to target the `_next` views (`--target next`); `repair` gains `--order newest-first`.
3. A cutover script that materializes, verifies and swaps, with a failure table.
4. The `/api/v1/bars` and `get_minute_data` default becomes `prints=eligible`. `prints=all` at 5m+ is a 422.
5. A health check: rollup values equal an eligible recompute for a fixed sample.
6. Post-swap re-runs of the NFR targets the swap touches.
7. Documentation: an amendment to the 140 architecture's caggs section; both API references (default change, flagged as a client-visible change); runbook 100 (the cutover); runbook 310 (coverage rebuild in the swap).

**Excluded:**

- **Marking:** the marks themselves, the detector, and the sweep belong to slice 171.
- **Daily-family rollups:** weekly, monthly and quarterly come from EOD bars, not from minute data.

## Dependencies

### Prerequisites

- **171, complete:** `off_tape` evaluated on every row (its health check passing), plus `eligible_sql.py`, `PrintMode` and the eligible-volume rule.
- **163:** the windowed refresh-and-compress `repair` loop, and `cagg_parity`.
- **168:** `assert_cagg_fresh`, which guards the seeder if it ever reads a rollup mid-swap.
- **169:** `mt data caggs rebuild-coverage` and the coverage rollup design.
- **0.23.2 (migration 061):** the refresh window (10 days) and compression (14 days) constants the new policies reuse.

### Interfaces Required

- `GRANULARITY_SOURCE` / `MINUTE_CAGG_GRANULARITIES` stay the single source for the final rollup names. The `_next` names are derived from them with one suffix constant (`CAGG_NEXT_SUFFIX`), never spelled out.
- The existing creation helpers for `minute_coverage` and the `data_status` view, parameterized by the parent view name.
- **Which tools see `_next` during phase 1.** Today `repair`, `verify` and `rebuild-coverage` resolve view names only from `GRANULARITY_SOURCE` (`cagg_parity.cagg_view`), so they cannot reach a `_next` view; `--target next` is what lets them. The health checks (`OHLCV_CAGG_VIEWS` in `health.py`) and `assert_cagg_fresh` (`_VIEW_GRANULARITY`) know only the final names, so a partly built `_next` never fails `mt data health` or a freshness probe. `mt data caggs status` lists every catalog cagg, so it shows the `_next` views; that listing is informational, and runbook 100 says a `_next` row is expected during the build.

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| Rollup definitions | `market/schema/migrations/minute.py` (063) | Render the four eligible rollups from `eligible_sql.py`, plus `minute_coverage_next` over the 4h one |
| Swap | migration 064 | Rename old → `_prev`, `_next` → final, replace `data_status`, drop `_prev` |
| Repair/verify/coverage targeting | `market/maintenance/cagg_repair.py`, `cagg_parity.py`, `coverage_rebuild.py` | `--target next`; `repair --order newest-first` |
| Cutover | `scripts/cutover_172_eligible_rollups.py` | Materialize → verify → stop timer → 064 → verify → start timer → report |
| Value parity check | `cli/commands/health.py` | Rollup vs eligible recompute for a fixed sample |
| Read path | `market/timescale_minute_db.py`, `api_server/routes/bars.py` | Default `eligible`; NULL-price buckets dropped |

### Data Flow

```
minute_ohlcv (+ off_tape, from 171)
      │
      ├─► minute_*_ohlcv_next  (eligible definitions; policies track the head from creation)
      │        ├─ repair --target next, newest window first, until verify = parity
      │        └─► minute_coverage_next ─ rebuild-coverage --target next
      │
      ├─ cutover: stop minute timer ─► 064: rename old → _prev, _next → final,
      │           replace data_status, drop _prev ─► verify ─► start timer
      ▼
/api/v1/bars (default eligible) ◄─ rollups (NULL-price buckets dropped)
```

### State Management

- **Rollup identity** is the catalog. Before 064 there are old rollups plus `_next`; after it, the final names hold the eligible definitions.
- **Materialization progress** is parity-derived, as `repair` is today (no progress table).
- **The cutover script** is check-then-act at each step, so a rerun resumes at the first step that is not done.

## Technical Decisions

### Rollup definition

Rendered from 171's `eligible_sql.py`. `<has eligible>` is `COALESCE(off_tape,0) <> 15`:

```sql
first(<clamp(open)>, time) FILTER (WHERE <has eligible>) AS open,
max(<clamp(high)>)         FILTER (WHERE <has eligible>) AS high,
min(<clamp(low)>)          FILTER (WHERE <has eligible>) AS low,
last(<clamp(close)>, time) FILTER (WHERE <has eligible>) AS close,
SUM(volume)                FILTER (WHERE <has eligible>) AS volume,
COUNT(*)                                                  AS minute_count
```

- **Volume** follows 171's rule. All-marked minutes are left out of eligible price and volume alike, so eligible 1m rolled up equals the rollup exactly, volume included. Nothing reads rollup volume for accounting: `mt data accounting` reads daily volume.
- **`minute_count` keeps counting every bar.** Coverage, accounting and `cagg_parity` read it, and they must not change meaning.
- **A bucket made only of all-marked minutes** yields a row with NULL prices and volume and a non-zero `minute_count`. That row stays in the rollup for coverage. The read path drops it (`WHERE open IS NOT NULL`), the same way an empty bucket is absent today, so `adjusted()` never sees a NULL price. Fixtures cover a bucket with one all-marked minute and a bucket of only all-marked minutes.

The test cluster probe from 171 (TimescaleDB 2.29.1) confirmed `FILTER` and these inline expressions in a cagg over a compressed hypertable, with columnstore enabled. A cagg rejects a sub-query in `FROM`, which is why the expressions are inline.

### The swap: build beside, then rename

Dropping and recreating in place would empty 5m and up for the whole rematerialization. It would also empty the 4h rollup, the acquisition seeder's coverage source; the slice 163 incident re-seeded about 349 symbols off a frozen 4h rollup. Instead:

1. **Migration 063** creates `minute_{5min,15min,hourly,4hour}_ohlcv_next` `WITH NO DATA`, and `minute_coverage_next` over `minute_4hour_ohlcv_next` with the existing coverage helper. Each gets its refresh policy (same constants as migration 061 and slice 169) and its columnstore policy, so the heads track new data from creation.
2. **The cutover script, phase 1 (passes live)** runs `mt data caggs repair --target next --order newest-first` for each rollup, 4h first, then `mt data caggs rebuild-coverage --family minute --target next`. Recent data is right first and history fills behind. `mt data caggs verify --target next` must report parity on all four, and `rebuild-coverage --verify --target next` must pass.
3. **The cutover script, phase 2 (minute timer stopped)** applies migration 064, which:
   - renames the four old rollups and `minute_coverage` to `_prev`;
   - renames each `_next` to its final name;
   - replaces `data_status` (`CREATE OR REPLACE VIEW`, so it binds to the new `minute_coverage`);
   - drops the `_prev` objects, coverage first.

   The script then runs `verify` and the coverage freshness check on the final names, and starts the timer.

Building coverage beside the rollups, instead of rebuilding it after the swap, is what keeps phase 2 down to renames: nothing is empty while the timer is stopped, and nothing has to be rematerialized.

On a fresh database 063 and 064 run back to back on empty objects, so a cold start ends in the same state.

**Transaction.** The renames and the `data_status` replacement run in one transaction, so readers see the old set or the new set and nothing in between. Creating and refreshing a cagg needs autocommit; renaming one is ordinary DDL. Two behaviors are probed on the test cluster (TimescaleDB 2.29.1) before 064 is written: renaming a cagg inside a transaction, and renaming a cagg that has a hierarchical cagg on top of it (`minute_coverage` sits on the 4h rollup). If TimescaleDB refuses either, 064 falls back to guarded autocommit statements, and the read gap becomes the time between the first and last rename. The drops always run in their own statements after the commit. Every step is guarded (`IF EXISTS`; a rename only if the source exists and the target name is free), so a rerun completes a partly applied 064.

Rejected: keeping raw rollups beside eligible ones permanently. That doubles about 62 GB of rollups and their refresh cost, for a raw 5m view nobody has asked for.

### Failure handling (cutover)

| Trigger | Effect | Handling |
|---|---|---|
| `repair` or `rebuild-coverage` fails or is interrupted in phase 1 | Some `_next` windows not materialized; old objects still serving | Rerun; parity-derived resume. Nothing user-visible changes. |
| `verify` not at parity on a `_next` view, or coverage verify fails | Phase 2 refused | Script exits non-zero naming the view; rerun phase 1 |
| 064's rename transaction fails | Rolled back: the old set still serves | Timer started again (nothing changed); non-zero exit naming the step |
| 064 fails in autocommit fallback, or a `_prev` drop fails | Names in a mixed state, or leftover `_prev` objects | The script leaves the minute timer **stopped** and exits non-zero with the step named. `mt data health` fails on cagg freshness, and the operator overview shows the timer stopped. Rerun the script: 064's guarded steps finish the swap. A leftover `_prev` after a committed rename is harmless to readers; the timer stays stopped only until the rerun drops it. |
| Post-swap `verify` or coverage freshness fails | New set in place but not proven | Timer stays stopped, non-zero exit; rerun re-checks |
| Script killed between stopping and starting the timer | Timer stopped | Same signals as above; a rerun detects swap state and finishes |

`assert_cagg_fresh` (slice 168) is the second guard: if the seeder ever ran against an incomplete coverage rollup, it would refuse to seed rather than seed from it.

### What readers see during the cutover

- **Phase 1:** nothing changes. Bars, `mt data status`, `/api/v1/status` and the seeder read the old objects. The `_next` views are invisible to them.
- **Phase 2, clean path:** the rename transaction swaps the whole set at once, so no reader sees a missing name. The only reader-visible effect is that 5m+ bars carry eligible values from the commit on.
- **Phase 2, autocommit fallback or a failure between renames:** a 5m+ bars request, or a `data_status` read, that hits a missing name gets the API's existing 500 (`UndefinedTable` reaches the global handler, which logs it). No 503 or special message is added: the window is either the time between guarded statements or a failed cutover, and a failed cutover is surfaced by the stopped timer, the failing `mt data health`, and the script's non-zero exit, not by the API. The fix in either case is to rerun the script.

### API default

- `prints` defaults to `eligible` for every granularity.
- `prints=all` is valid at 1m only. At 5m+ it returns `422` with "prints=all is served at 1m only; roll up the 1m bars".
- Daily and coarser granularities accept `eligible` and return their EOD-based bars.
- This is a client-visible change: both API references and the CHANGELOG say so, and mt-ui is told.

### Standing value check

`cagg_parity` compares counts. It catches under-materialization, but not a rollup whose definition differs from the 1m eligible path. Both are rendered from `eligible_sql.py`, so they agree by construction when 064 runs. They would drift if the renderer later changed without a new rollup migration. Add one `mt data health` check: over the last collected session, every 5m rollup row for the sampled symbol equals the eligible 1m bars rolled up in SQL. It is one symbol and one session, so it is cheap. It fails on any difference, naming the first bucket that differs.

- **Sample.** The symbol is chosen by query, not by a constant: the one with the most marked minutes in that session, ties broken by symbol. The check therefore always exercises the marked-minute paths this slice adds. A fixed liquid symbol such as SPY can have no marks on a given session and would only prove the unmarked path.
- **Columns.** Every rollup column, `minute_count` included, compared with `IS NOT DISTINCT FROM`, so an all-marked bucket (NULL prices and volume) is compared, not skipped.
- **No marked minute in the session.** The check fails with that reason. It has nothing to prove, and `off-tape share` fails on the same session anyway.

### NFR targets touched by the swap

From the 140 architecture, re-run after phase 2 and recorded. Each has a pass number:

| Target | Pass | Baseline to compare | Test |
|---|---|---|---|
| `data_status` full-universe read | < 3.9 s (half the 7.8 s raw scan slice 167 removed) | 2.636 s, the 140-arch figure at the 7-day coverage width | `test/load/test_167_data_status_nfr.py` |
| Coverage freshness probes (content edge, raw source) | < 2.5 s median (25% of the 10 s probe budget) | the 169 recorded medians | `test/load/test_169_coverage_freshness_probe_nfr.py` |
| `minute_coverage` bucket lag | within 8 d 4 h (`COVERAGE_BUCKET_LAG_BUDGET`) | n/a, a bound | `mt data health` coverage freshness |
| Bars request at the admission ceiling | < 15 s (`_BARS_BOUND_S`, 187 D10.2) | 4.794 s eligible / 4.740 s raw at 1m (171 calibration) | `test/load/test_187_api_nfr.py` |

The bars tier today covers 1m only. Add one 5m case at the 5m admission ceiling under the same 15 s bound, reading the new rollup with the read path's NULL-bucket filter. 5m is the densest rollup, so 15m, 1h and 4h are not added.

## Implementation Details

### Database / Storage Schema

- **063:** four `_next` rollups and `minute_coverage_next`, plus refresh and columnstore policies.
- **064:** the swap described above.
- **Disk:** the `_next` objects need about 62 GB beside the current ones during phase 1. The host has 1.2 TB free.

### API Contracts

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `prints` | `eligible`, `all` | `eligible` (was `all`) | `all` valid with `granularity=1m` only |

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | release A | 063, `--target next`, `--order newest-first`, the cutover script. `_next` objects created and tracking the head |
| 2 | automation identity | Cutover phase 1: materialize `_next` newest-first; verify parity and coverage |
| 3 | release B | Cut only after step 2 verifies. Contains 064, the API default switch, the 422, and the value-parity check |
| 4 | automation identity | Cutover phase 2: stop minute timer, 064, verify, start timer; then `upgrade_production.py` for release B's code |

**Release gate.** Release B is not cut until phase 1 has verified, and its upgrade is the cutover phase 2, not a plain `upgrade_production.py` run. Two reasons. The default switch must not reach production before the rollups hold eligible values: until then, `prints=eligible` at 5m+ would label raw rollups as eligible. And the value-parity check fails by design against raw rollups. Within step 4, 064 applies first and the code second, so the window where old code serves the new rollups is the restart. Old code reads them correctly; it just reports `prints=all` as the default.

The minute timer is stopped and started through the existing NOPASSWD unit commands, so no PM host step is needed.

## Integration Points

### Provides to Other Slices

- **Eligible rollups at 5m, 15m, 1h and 4h** under their existing names: every current reader keeps working.
- **The value-parity health check:** a standing guard on the eligible definition.
- **182/186/190:** the bars default changes to eligible, documented as a client-visible change.

### Consumes from Other Slices

- **171:** marks on every row; the renderer; `PrintMode`; the volume rule.
- **163:** `repair`/`verify`.
- **168:** the seeder guard.
- **169:** `rebuild-coverage`.

## Success Criteria

### Functional Requirements

1. The SPY, AVGO and MSFT values from slice 171's criteria are absent from the 5m and 1h bars.
2. For any window, eligible 1m bars rolled up to 5m equal the 5m rollup, volume included.
3. 5m and up, `mt data status` and `/api/v1/status` serve without a gap through phase 1, and through phase 2 on the transactional path.
4. During phase 1, `mt data health` does not fail because of a `_next` view.
5. After phase 2:
   - `mt data caggs verify` exits 0 on all four rollups;
   - coverage is fresh;
   - the value-parity check passes;
   - no `_next` or `_prev` object remains;
   - `/api/v1/bars` defaults to eligible, and `prints=all` at 5m returns 422.
6. Rerunning the cutover script after a kill at any step completes the swap. A failure after the timer stops, other than a rolled-back rename transaction, leaves it stopped.
7. Each NFR target meets its pass number in the table above.

### Technical Requirements

- **Probe first:** cagg rename inside a transaction, and rename of a cagg with a hierarchical cagg on top, on TimescaleDB 2.29.1. The result picks 064's path and is recorded in the slice.
- **Migration integration tests:** 063 and 064 on a cold start; 064 on a database holding the old objects with data; a 064 rerun after a partial apply; and a reader of `data_status` and the 4h rollup that sees no missing name across 064 (transactional path).
- **Fixtures:** a bucket with one all-marked minute, and a bucket of only all-marked minutes (row kept, read path drops it).
- **Targeting:** `repair`, `verify` and `rebuild-coverage` with `--target next` act on the `_next` names and never touch the final ones; `--order newest-first` tested.
- **Health:** unit tests for the value-parity check's failure states (a differing bucket, a differing NULL bucket, no marked minute); the sample query picks the most-marked symbol.
- **NFR re-runs:** recorded in the walkthrough.
- **Quality bar:** `ruff` and `mypy` clean; API references, CHANGELOG, and runbooks 100 and 310 updated.

### Integration Requirements

- `mt data accounting`, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after the swap.
- mt-ui's 5m-and-up charts show no spikes with no client change.

### Verification Walkthrough

1. Phase 1 (passes live):
   ```bash
   uv run python scripts/cutover_172_eligible_rollups.py --phase build    # newest-first materialize, coverage, verify
   uv run mt data caggs verify --target next                               # _next views at parity
   uv run mt data health                                                   # unaffected by the _next views
   ```
2. Phase 2 (release B):
   ```bash
   uv run python scripts/cutover_172_eligible_rollups.py --phase swap     # stop timer, 064, verify, start timer, upgrade, report
   uv run mt data caggs verify                                             # exit 0
   uv run mt data health                                                   # includes the value-parity check → PASS
   ```
3. Charts:
   ```bash
   B="localhost:8100/api/v1/bars/MSFT?start=2026-06-01&end=2026-06-02"
   curl -s "$B&granularity=5m" | jq '.prints, [.bars[]|select(.timestamp>="2026-06-01T20:40")|.high]|max'
   # "eligible"; after-hours highs near 460, not 487
   curl -s -o /dev/null -w "%{http_code}\n" "$B&granularity=5m&prints=all"   # 422
   ```
4. NFR re-runs:
   ```bash
   uv run pytest test/load/test_167_data_status_nfr.py test/load/test_169_coverage_freshness_probe_nfr.py test/load/test_187_api_nfr.py -v
   ```
   Record each result against its pass number.

## Risk Assessment

### Technical Risks

- **The swap touches the coverage source** the acquisition seeder reads.
- **Client-visible default change:** a consumer that relied on raw 5m+ bars sees different highs and lows.

### Mitigation Strategies

- **The swap:**
  - coverage is built and verified beside the rollups, so phase 2 is renames only;
  - phase 2 runs only with the minute timer stopped;
  - any failure past a rolled-back rename leaves the timer stopped;
  - `assert_cagg_fresh` guards the seeder anyway.
- **The default change:**
  - raw stays available at 1m;
  - the change is announced in the CHANGELOG and both API references, and mt-ui is told before release.

## Implementation Notes

### Development Approach

1. The rename probe on the test cluster.
2. `--target next` for `repair`/`verify`/`rebuild-coverage`, and `--order newest-first` for `repair`.
3. Migration 063 and its tests.
4. Migration 064, its guarded steps, and its tests (cold, populated, partial-rerun, no-gap reader).
5. The cutover script and its failure-table tests.
6. The value-parity health check and the 5m bars load case.
7. The API default switch and the 422.
8. Documentation, then the cutover and the NFR re-runs, recorded.

### Special Considerations

- **The 140 architecture amendment** records three changes:
  - the four minute caggs project tape-eligible values, not raw OHLCV;
  - `get_minute_data`/`/api/v1/bars` default to eligible;
  - `verify` remains the count-parity detector, with the new value-parity health check as the guard on the definition.
