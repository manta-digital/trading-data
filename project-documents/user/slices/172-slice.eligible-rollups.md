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

1. Migrations 063/064: build `_next` rollups with eligible definitions, then swap them in.
2. A newest-first order option for the `mt data caggs repair` loop.
3. A cutover script that materializes, verifies, swaps and rebuilds coverage, with a failure table.
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

- `GRANULARITY_SOURCE` / `MINUTE_CAGG_GRANULARITIES` stay the single source for the final rollup names. The `_next` names are derived from them.
- The existing creation helpers for `minute_coverage` and the `data_status` view.

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| Rollup definitions | `market/schema/migrations/minute.py` (063) | Render the four eligible rollups from `eligible_sql.py` |
| Swap | migration 064 | Drop the old rollups, rename `_next`, and recreate coverage and `data_status` |
| Repair ordering | `market/maintenance/cagg_repair.py` | `--order newest-first` |
| Cutover | `scripts/cutover_172_eligible_rollups.py` | Materialize → verify → stop timer → swap → rebuild coverage → start timer → report |
| Value parity check | `cli/commands/health.py` | Rollup vs eligible recompute for a fixed sample |
| Read path | `market/timescale_minute_db.py`, `api_server/routes/bars.py` | Default `eligible`; NULL-price buckets dropped |

### Data Flow

```
minute_ohlcv (+ off_tape, from 171)
      │
      ├─► minute_*_ohlcv_next  (eligible definitions; policies track the head from creation)
      │        └─ repair loop, newest window first, until verify = parity
      │
      ├─ cutover: stop minute timer ─► 064: drop old, rename _next → final,
      │           recreate minute_coverage + data_status ─► rebuild-coverage ─► start timer
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

1. **Migration 063** creates `minute_{5min,15min,hourly,4hour}_ohlcv_next` `WITH NO DATA`. Each gets its refresh policy (same constants as migration 061) and its columnstore policy, so the heads track new data from creation.
2. **The cutover script, phase 1 (passes live)** runs `mt data caggs repair --order newest-first` for each `_next`, 4h first. Recent charts are right first and history fills behind. `mt data caggs verify` must report parity on all four `_next` views.
3. **The cutover script, phase 2 (minute timer stopped)** applies migration 064, which:
   - drops `data_status`, `minute_coverage` and the four old rollups;
   - renames each `_next` to its final name;
   - recreates `minute_coverage` over the new 4h rollup, and `data_status`, with the existing helpers.

   `mt data caggs rebuild-coverage` then rematerializes coverage, the freshness checks pass, and the timer starts.

On a fresh database 063 and 064 run back to back on empty tables, so a cold start ends in the same state.

Continuous-aggregate DDL needs autocommit, so 064 cannot be one transaction. Each statement is guarded (`IF EXISTS`, plus a rename only if `_next` exists and the final name is free), so a rerun completes a partly applied 064.

Rejected: keeping raw rollups beside eligible ones permanently. That doubles about 62 GB of rollups and their refresh cost, for a raw 5m view nobody has asked for.

### Failure handling (cutover)

| Trigger | Effect | Handling |
|---|---|---|
| `repair` fails or is interrupted in phase 1 | Some `_next` windows not materialized; old rollups still serving | Rerun; parity-derived resume. Nothing user-visible changes. |
| `verify` not at parity on a `_next` view | Phase 2 refused | Script exits non-zero naming the view; rerun phase 1 |
| 064 fails partway | Old rollups possibly dropped, `_next` possibly not renamed; coverage missing | The script leaves the minute timer **stopped** and exits non-zero with the step named. The seeder must never run against missing coverage. `mt data health` fails on cagg freshness, and the operator overview shows the timer stopped. Rerun the script: 064's guarded steps finish the swap. |
| `rebuild-coverage` fails | Coverage incomplete | Same: timer stays stopped, non-zero exit, rerun resumes |
| Script killed between stopping and starting the timer | Timer stopped | Same signals as above; a rerun detects swap state and finishes |

`assert_cagg_fresh` (slice 168) is the second guard: if the seeder ever ran against an incomplete coverage rollup, it would refuse to seed rather than seed from it.

### API default

- `prints` defaults to `eligible` for every granularity.
- `prints=all` is valid at 1m only. At 5m+ it returns `422` with "prints=all is served at 1m only; roll up the 1m bars".
- Daily and coarser granularities accept `eligible` and return their EOD-based bars.
- This is a client-visible change: both API references and the CHANGELOG say so, and mt-ui is told.

### Standing value check

`cagg_parity` compares counts. It catches under-materialization, but not a rollup whose definition differs from the 1m eligible path. Both are rendered from `eligible_sql.py`, so they agree by construction when 064 runs. They would drift if the renderer later changed without a new rollup migration. Add one `mt data health` check: for `OFF_TAPE_VALUE_CHECK_SYMBOL` (SPY), over the last collected session, every 5m rollup row equals the eligible 1m bars rolled up in SQL. That is one symbol and one session, so it is cheap. It fails on any difference, naming the first bucket that differs.

### NFR targets touched by the swap

From the 140 architecture, re-run after phase 2 and recorded:

- **`data_status` full-universe read:** within the no-regression margin against the 7.8 s raw scan (`test/load/test_167_data_status_nfr.py`).
- **Freshness probes:** under the 25%-of-10 s ceiling.
- **Coverage lag:** within `COVERAGE_BUCKET_LAG_BUDGET` for `minute_coverage`.
- **Bars reads:** the bars load tier, which includes 171's eligible 1m case, plus 5m/1h/4h reads from the new rollups under their existing budgets.

## Implementation Details

### Database / Storage Schema

- **063:** four `_next` continuous aggregates plus refresh and columnstore policies.
- **064:** the swap described above.
- **Disk:** the `_next` rollups need about 62 GB beside the current ones during phase 1. The host has 1.2 TB free.

### API Contracts

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `prints` | `eligible`, `all` | `eligible` (was `all`) | `all` valid with `granularity=1m` only |

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | release | 063 applied; `_next` rollups created and tracking the head |
| 2 | automation identity | Cutover phase 1: materialize `_next` newest-first; verify parity |
| 3 | automation identity | Cutover phase 2: stop minute timer, 064, rebuild coverage, start timer, report |
| 4 | release | API default switches to `eligible` |

Steps 3 and 4 can ship in one release if the release is cut after step 2 is verified. The minute timer is stopped and started through the existing NOPASSWD unit commands, so no PM host step is needed.

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
3. 5m and up serve without a gap through phase 1, and are absent only around 064 and the coverage rebuild in phase 2.
4. After phase 2:
   - `mt data caggs verify` exits 0 on all four rollups;
   - coverage is fresh;
   - the value-parity check passes;
   - `/api/v1/bars` defaults to eligible, and `prints=all` at 5m returns 422.
5. Rerunning the cutover script after a kill at any step completes the swap. A failure at 064 or the coverage rebuild leaves the minute timer stopped.

### Technical Requirements

- **Migration integration tests:** 063 and 064 on a cold start; 064 on a database holding the old rollups with data; and a 064 rerun after a partial apply.
- **Fixtures:** a bucket with one all-marked minute, and a bucket of only all-marked minutes (row kept, read path drops it).
- **Repair:** `--order newest-first` tested.
- **Health:** unit tests for the value-parity check's failure state.
- **NFR re-runs:** recorded in the walkthrough.
- **Quality bar:** `ruff` and `mypy` clean; API references, CHANGELOG, and runbooks 100 and 310 updated.

### Integration Requirements

- `mt data accounting`, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after the swap.
- mt-ui's 5m-and-up charts show no spikes with no client change.

### Verification Walkthrough

1. Phase 1 (passes live):
   ```bash
   uv run python scripts/cutover_172_eligible_rollups.py --phase build    # newest-first materialize, then verify
   uv run mt data caggs verify --granularity all                           # _next views at parity
   ```
2. Phase 2:
   ```bash
   uv run python scripts/cutover_172_eligible_rollups.py --phase swap     # stop timer, 064, rebuild coverage, start timer, report
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
   uv run pytest test/load/test_167_data_status_nfr.py -v
   ```
   Record the results here, along with the bars load tier.

## Risk Assessment

### Technical Risks

- **The swap touches the coverage source** the acquisition seeder reads.
- **Client-visible default change:** a consumer that relied on raw 5m+ bars sees different highs and lows.

### Mitigation Strategies

- **The swap:**
  - phase 2 runs only with the minute timer stopped;
  - any failure leaves the timer stopped;
  - `assert_cagg_fresh` guards the seeder anyway;
  - coverage is rebuilt and verified fresh before the timer starts.
- **The default change:**
  - raw stays available at 1m;
  - the change is announced in the CHANGELOG and both API references, and mt-ui is told before release.

## Implementation Notes

### Development Approach

1. `--order newest-first` for `repair`.
2. Migration 063 and its tests.
3. Migration 064, its guarded steps, and its tests (cold, populated, partial-rerun).
4. The cutover script and its failure-table tests.
5. The value-parity health check.
6. The API default switch and the 422.
7. Documentation, then the cutover and the NFR re-runs, recorded.

### Special Considerations

- **The 140 architecture amendment** records three changes:
  - the four minute caggs project tape-eligible values, not raw OHLCV;
  - `get_minute_data`/`/api/v1/bars` default to eligible;
  - `verify` remains the count-parity detector, with the new value-parity health check as the guard on the definition.
