---
docType: slice-design
slice: off-tape-prints
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163, 168, 169]
interfaces: [182, 186, 190]
dateCreated: 20261015
dateUpdated: 20261015
status: not_started
---

# Slice Design: Off-Tape Prints

## Overview

EODHD's 1-minute bars include trades that the consolidated tape rules keep out of a bar's high, low and last price: late-reported trades, average-price trades, and trades at a prior reference price. They show up as tall isolated wicks, mostly in pre-market and after-hours. Every rollup takes `max(high)`/`min(low)`, so each one reappears on the 5m, 15m, 1h and 4h bars. That is the "same vertical spikes on every timeframe" the trading UI showed on 2026-10-15.

This slice marks those values on the raw minute bars, without changing the raw values, and serves bars built only from the tape-eligible values by default. Raw bars stay available at 1m.

Evidence gathered for this design (production data, 2026-10-15):

- The rollups are arithmetically correct. SPY 5m from `/api/v1/bars` equals the API's 1m bars rolled up to 5m (0 differences in 2,684 bars). The stored minute bars equal EODHD's own `/intraday` response.
- The marked values are trades (the bars carry volume). EODHD's tick endpoint, which would show sale conditions, returns 403 on our plan.
- **Prior-reference prints are confirmed by price.** Of the values the prototype detector marks, 36% exactly equal one of the symbol's previous 30 official daily closes, against about 2.3% of unmarked values. Example: SPY 2026-09-08 07:35–07:38 ET prints at 770.19, the official close two sessions earlier.
- SPY 1m since 2026-06-01: 326 bars with a wick over 1% of price, 1 of them in regular hours. Universe-wide a fixed percentage cannot separate these from real thin-market moves: one session (2026-09-08, close ≥ $5) has 2,818 of 1.51 M bars with a 1% wick.
- Some bad prints do fall inside regular hours: AAPL 2026-06-08 15:15 ET high 313.04 with the market at 304.5. Those sit inside the official daily range: they are real trades from earlier in the day, reported late into the wrong minute.
- Databento ES trades (94,641 1m bars built from `tick_trade`): 3 bars with a wick over 0.3%, none over 1%. Futures have no late-reported prints. This says nothing about Databento's equity data.

## Value

- Charts at 5m and up show the market, not the tape's late reports. In the prototype, SPY's 5m bars with a wick over 1% fall from 249 to 1 over four months. MSFT falls from 859 to 95, NVDA from 835 to 28.
- Every consumer of bars (UI, `mt data get`, future strategies) gets tape-eligible highs and lows by default with no client-side cleaning.
- Raw bars are untouched and still served, so nothing about the provider's data is lost or hidden.
- The provider choice stays open. PM direction: if we buy equity data, it is Databento ($199/month, same as Polygon). For now we clean EODHD. The detector reads bars, not EODHD's format, so a switch drops in.

## Technical Scope

**Included:**

1. A nullable `off_tape smallint` column on `minute_ohlcv`: a bitmask saying which of open, high, low and close are off-tape. `NULL` means not evaluated, and `0` means evaluated and clean. Raw values are never modified.
2. The detector: a pure function from one symbol's bars to their `off_tape` masks.
3. Both minute insert paths compute the masks before they write: the acquisition pass (`_insert_minute_bars`) and `write_minute_data_bulk`.
4. A one-time sweep, `mt data off-tape sweep`, that evaluates all history chunk by chunk and is resumable.
5. New definitions for the four minute rollups that aggregate tape-eligible values. They are built beside the current rollups and swapped in, so 5m and up keep serving throughout.
6. `/api/v1/bars` and `get_minute_data` gain a `prints` choice: `eligible` (the default) or `all` (raw, 1m only).
7. Two `mt data health` checks: no unevaluated bars in the last collected session, and the marked share of that session inside measured bounds.
8. Documentation: both API references, runbook 100, and the "Outlier handling — Non-Goal" section of [120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md).

**Excluded:**

- Daily, weekly, monthly and quarterly bars. They come from EODHD's EOD endpoint, which already reports the official session range.
- Futures (Databento) and Kalshi data.
- Buying equity data with sale conditions.
- Removing, smoothing or forward-filling anything at storage time.
- Per-bar marks in API responses. Clients get eligible or raw bars, and the marks stay internal until someone needs them.

## Dependencies

### Prerequisites

- **163** (minute rollup chunk sizing and `mt data caggs repair`): its windowed refresh-and-compress loop materializes the new rollups.
- **168** (`assert_cagg_fresh`): it keeps the acquisition seeder from reading a rollup mid-swap.
- **169** (coverage rollup rebuild, `mt data caggs rebuild-coverage`): `minute_coverage` is recreated on the new 4h rollup.
- **0.23.2** (migration 061, 10-day refresh window): the new rollups' policies use the same constants.

### Interfaces Required

- `minute_ohlcv` (hypertable, compressed, `segmentby = symbol`, 7-day chunks, 1,215 compressed chunks, 82 GB).
- `GRANULARITY_SOURCE` / `MINUTE_CAGG_GRANULARITIES` in `constants.py` stay the single source for the rollup names.
- `cagg_parity` compares `SUM(minute_count)` against raw `COUNT(*)`. It is unchanged, because `minute_count` still counts every bar (see Technical Decisions).

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| `OffTape` (`IntFlag`) | `constants.py` | Bit values `OPEN=1, HIGH=2, LOW=4, CLOSE=8`, `ALL=15`. One definition; the SQL renders from it. |
| Detector | `data/quality/off_tape.py` | `mark_off_tape(bars) -> masks`. Pure, no I/O. |
| Eligible-value SQL | `market/schema/eligible_sql.py` | Renders the eligible expressions for open/high/low/close and the "bar has an eligible value" predicate. Used by the rollup migration, the 1m read path and tests, so the SQL is written once. |
| Insert paths | `data/acquisition/daemon/minute.py`, `market/timescale_minute_db.py` | Call the detector on each fetched frame and write `off_tape` with the bars. |
| Sweep | `market/maintenance/off_tape_sweep.py`, CLI `mt data off-tape sweep` | Evaluates history chunk by chunk. |
| Rollup swap | migrations 062–064 + `scripts/cutover_171_eligible_rollups.py` | Builds the `_next` rollups, materializes them, and swaps them in. |
| API | `api_server/routes/bars.py`, `market/timescale_minute_db.py` | The `prints` parameter. |
| Health | `cli/commands/health.py` | Unevaluated-bar and marked-share checks. |

### Data Flow

```
EODHD /intraday ─► fetch frame (one symbol, whole UTC days)
                     │
                     ├─► mark_off_tape(frame) ─► off_tape mask per bar
                     ▼
              minute_ohlcv (raw o/h/l/c/v unchanged, + off_tape)
                     │
       ┌─────────────┼────────────────────────────┐
       ▼             ▼                            ▼
 1m API read   rollups (5m/15m/1h/4h)       health: unevaluated rows,
 prints=eligible: eligible exprs            marked share of last session
 prints=all:      raw columns               │
                     ▼                      ▼
              /api/v1/bars, mt data get   minute_coverage (counts only)
```

History follows the same marking through the sweep instead of the insert paths.

### State Management

- **The `off_tape` column** is the only new state. It is written at insert for new bars and by the sweep for history, so whether a row has been evaluated is visible in the data itself.
- **Sweep progress needs no table.** A chunk is done when it holds no `NULL` `off_tape` rows. A rerun skips done chunks and redoes an interrupted one, the same parity-derived resume `mt data caggs repair` uses.
- **Rollup swap state** is the catalog. The `_next` views exist and are in parity, or they do not. The swap is one migration.

## Technical Decisions

### The detector rule

A value is off-tape when neither the minutes before it nor the minutes after it trade anywhere near it.

For each bar, within the same ET calendar day (04:00–20:00 ET; the window never crosses the overnight break, where real gaps happen):

- `before` = median close of the previous `K` bars; `after` = median close of the next `K` bars (by row, not clock, because pre-market bars are sparse). At the day's edges only the available side is used.
- `scale` = the larger of the two sides' median absolute close-to-close change.
- `threshold` = `max(M × scale, R × close)`.
- Each of open, high, low and close is marked when it lies above `max(before, after) + threshold` or below `min(before, after) − threshold`.

Why each part is there, from the prototype runs:

- **Two-sided envelope.** A real jump (an after-hours earnings release) lands near the `after` side, and a value between the two medians is never marked. The first one-sided version marked SMCI's real close in the 2026-07-21 earnings minute.
- **Closes, not ranges, for the scale.** Where bad prints are dense, every bar's range is inflated (MSFT 2026-06-01 after 16:45 ET had one in almost every minute), so a range-based scale stopped marking anything. Closes are rarely off-tape.
- **The larger side's scale.** At the onset of an earnings move the calm side would set a tight threshold and mark the bottom of a real V. META 2026-07-29 16:03 was marked under the averaged scale and is not marked under the larger one.

**Starting calibration:** `K = 10`, `M = 10`, `R = 0.003`. Over 40 symbols (3.1 M bars, 2026-06-01 to 10-09) this marks 1.0% of bars and 845 regular-hours bars. It marks 2 values within three minutes of 15 earnings jumps: AVGO 2026-06-03's 295.47 low, which was 38% below the market, and one marginal value. 722 bars (0.02%) have all four values marked; those are whole single-trade minutes at a stale price. The constants live in `constants.py` (`OFF_TAPE_WINDOW_BARS`, `OFF_TAPE_SCALE_MULTIPLE`, `OFF_TAPE_MIN_FRACTION`) and are finalized by the calibration task against the fixture set below. They are not re-tuned by eye.

**Known limit:** the closing-cross prints reported in the first minutes after 16:00 ET on earnings days stay unmarked. Examples are MSFT 2026-07-29 16:02–16:04 lows at 390.30–390.54 while the market traded 401–409. The minute's real range is so wide that no neighbour-based rule can tell them from real trades. They are recorded, not chased.

**Rejected:**

- **A fixed percentage cut.** It marks real small-cap trading; see Overview.
- **Clamping to the official daily range for regular hours.** The regular-hours bad prints are inside that range. They are real trades in the wrong minute.
- **Matching prior closes as the detector.** It finds only one of the three print types (36% of marks).

### Eligible bar values: clamp into what the bar's own eligible values allow

For a bar with mask `f`, let `E` be its unmarked values among open, high, low and close.

- `lo = min(E)` and `hi = max(E)`.
- Each value `v` becomes `clamp(v, lo, hi)`. An unmarked value is unchanged, and a marked high falls to the bar's highest eligible value.
- A bar with all four marked (`f = ALL`) has no eligible price. It is excluded from eligible price aggregates and from eligible 1m responses.
- **Volume is never adjusted.** Off-tape trades are real volume under the tape rules too. `minute_count` keeps counting every bar, so coverage, accounting and `cagg_parity` are unaffected.

This rule needs nothing outside the bar, so it can run inside a rollup definition. The eligible 1m bars rolled up equal the eligible 5m rollup exactly, the same invariant the raw bars have today. Rejected: replacing a marked value with the neighbours' median. Rollups cannot read neighbours, and it would invent a price no trade printed.

### Rollup SQL (verified on the test cluster, TimescaleDB 2.29.1)

A continuous aggregate rejects a sub-query in `FROM`. It does accept `FILTER` and inline `CASE`/`LEAST`/`GREATEST` expressions inside `first`/`last`/`max`/`min`. The probe built a cagg with exactly these expressions over a compressed hypertable, refreshed it, enabled columnstore on it, and returned the clamped high with the all-marked bar excluded. The definitions are rendered from `eligible_sql.py`:

```sql
first(<clamp(open)>, time)  FILTER (WHERE <has eligible>) AS open,
max(<clamp(high)>)          FILTER (WHERE <has eligible>) AS high,
min(<clamp(low)>)           FILTER (WHERE <has eligible>) AS low,
last(<clamp(close)>, time)  FILTER (WHERE <has eligible>) AS close,
SUM(volume) AS volume, COUNT(*) AS minute_count
```

`<clamp(v)>` is `LEAST(GREATEST(v, lo), hi)`. `lo`/`hi` are `LEAST`/`GREATEST` over `CASE WHEN COALESCE(off_tape,0) & <bit> = 0 THEN <col> END`. `COALESCE` serves unevaluated rows raw until the sweep reaches them. The probe also confirmed `ALTER TABLE ... ADD COLUMN` and `UPDATE` on compressed chunks.

### The swap: build beside, then rename

Dropping and recreating the rollups in place would empty 5m and up for the whole rematerialization. It would also empty the 4h rollup, which is the acquisition seeder's coverage source; the slice 163 incident re-seeded about 349 symbols off a frozen 4h rollup. Instead:

1. **Migration 063** creates `minute_{5min,15min,hourly,4hour}_ohlcv_next` with the eligible definitions `WITH NO DATA`. Each gets its refresh policy (same `MINUTE_CAGG_REFRESH_START_OFFSET`/end offsets) and columnstore policy (`MINUTE_CAGG_COMPRESS_AFTER`), so their heads track new data from creation.
2. **The cutover script** materializes each `_next` rollup through the `mt data caggs repair` loop, newest window first. A recent chart is right first, and history fills in behind it. `mt data caggs verify` must pass on all four.
3. **Migration 064** is the swap, with the minute pass stopped. It drops `data_status`, `minute_coverage` and the old rollups. It renames each `_next` to its final name, recreates `minute_coverage` on the new 4h rollup and `data_status`, using the existing creation helpers, and repoints the policies. `mt data caggs rebuild-coverage` then rematerializes coverage before the minute pass resumes.

On a fresh database 063 and 064 run back to back on empty tables, so a cold start ends in the same state.

**Rejected:** keeping the raw rollups and adding eligible ones beside them for good. That doubles about 62 GB of rollups and their refresh cost to serve a raw 5m view nobody has asked for.

### Sweep mechanics

For each compressed chunk of `minute_ohlcv` with any `NULL` `off_tape`, oldest first:

1. Read the chunk's bars plus one day either side, so ET days that straddle the chunk boundary have context.
2. Run the detector per symbol.
3. In one transaction: `decompress_chunk`, `UPDATE` every row of the chunk from a temp table of masks (0 or the mask), then `compress_chunk`. The transaction holds the chunk for its duration, so the compression policy and any deep backfill insert into that chunk wait, and no policy pause is needed.

Chunks are independent, so the sweep runs a bounded number of worker processes (`OFF_TAPE_SWEEP_WORKERS`). The detector is pandas: the prototype took 22.6 s for 3.1 M bars on one core. The sweep connects through the maintenance URL passed as an explicit argument. It never reads the URL from the environment inside the tool.

Every row is written, not only marked ones. That costs nothing extra, because the chunk is decompressed and recompressed anyway, and it gives "not evaluated" an explicit, checkable meaning.

### API: `prints`

- `GET /api/v1/bars/{symbol}?prints=eligible|all`, default `eligible`, as a `PrintMode` `StrEnum` in `constants.py`.
- `all` with any granularity other than `1m` returns `422` with the message "prints=all is served at 1m only; roll up the 1m bars". The rollups hold eligible values only.
- Daily and coarser granularities accept both values and return the same bars, because EOD bars already follow the official range. The response says which mode it served (`"prints": "eligible"`).
- `get_minute_data(..., prints=PrintMode.ELIGIBLE)` carries the same choice for `mt data get` and the CSV export.

### Health

There are two checks in `mt data health`, both over the last collected minute session, which is the session 921's session-mass check already reads:

- **Unevaluated bars:** any `NULL` `off_tape` in that session fails, naming the count. That means an insert path skipped the detector.
- **Marked share:** the fraction of that session's bars with a non-zero mask must lie within `[OFF_TAPE_SHARE_FLOOR, OFF_TAPE_SHARE_CEILING]`. Zero marks in a session means the detector is not running, because these prints are on the tape every day. A flood means the provider changed or the detector broke. The bounds are measured from a full universe week during implementation and recorded with their measurement. The 40-symbol prototype measured 1.0%.

### Patterns and Conventions

- Bit values, mode names and thresholds are defined once in `constants.py`, and the SQL is rendered from them.
- The detector is a pure function on a frame. The insert paths and the sweep call the same function.
- **No silent fallback:** a detector exception in an insert path fails that symbol's write and is logged with `logger.exception`. Bars are never inserted unevaluated to keep going.

## Implementation Details

### Database / Storage Schema

- **Migration 062:** `ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS off_tape smallint`. Nullable, no default; a metadata-only change on a compressed hypertable. Tested on a throwaway database with compressed chunks.
- **Migration 063:** the four `_next` rollups and their policies, as above.
- **Migration 064:** the swap, as above.
- The insert staging table `_minute_stage` gains `off_tape`.

### API Contracts

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `prints` | `eligible`, `all` | `eligible` | `all` is valid with `granularity=1m` only (422 otherwise) |

Response: the existing `BarsResponse` gains `prints` (string, echoing the mode served). In eligible 1m responses, all-marked minutes are absent, the same way a minute with no trades is absent today.

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | release | Migration 062 applied; insert paths mark new bars from the next pass on. |
| 2 | automation identity | `mt data off-tape sweep --maintenance-url …` over all history; resumable. |
| 3 | release | Migration 063 applied; `_next` rollups created and tracking the head. |
| 4 | automation identity | Cutover script: materialize `_next` newest-first, `verify` to parity on all four. |
| 5 | automation identity | Cutover script: stop the minute timer, apply 064, `rebuild-coverage`, start the timer, print the report. |

Steps 2 and 4 run with the passes live. The minute pass is stopped only around step 5, through the existing NOPASSWD unit commands, so no PM host step is needed. Consumer impact: until step 5, 5m and up serve the raw rollups; after it, eligible ones. Between steps 1 and 5, 1m `eligible` serves raw values for rows the sweep has not reached yet (`COALESCE`).

## Integration Points

### Provides to Other Slices

- **`off_tape` marks** on every minute bar, for any future reader: backtests, a data-quality report, a provider comparison.
- **`eligible_sql.py`**: one renderer for tape-eligible values, reusable by any future SQL that reads minute prices.
- **182/186/190:** the bars endpoint gains `prints`, inside the existing client contract (admission, staleness, 404 semantics unchanged); both API references document it.

### Consumes from Other Slices

- **163:** the repair loop materializes the `_next` rollups. It gains a newest-first order option, its only change.
- **168:** freshness probes resolve rollup names through `GRANULARITY_SOURCE`, so they follow the swap. The 4h coverage source is never empty while the seeder can run: it is swapped only with the minute pass stopped, and coverage is rebuilt before the pass resumes.
- **169:** `mt data caggs rebuild-coverage` recreates `minute_coverage` after the swap.
- **921:** the health check's "last collected session" definition.

## Success Criteria

### Functional Requirements

1. These known values are marked, and absent from the eligible 1m, 5m and 1h bars:
   - SPY 2026-09-08 07:35–07:38 ET (770.19), 16:01–16:04 ET highs (770.3516), and 16:52 and 17:26 ET lows (724.228)
   - SPY 2026-09-11 17:33 ET high (787.5169)
   - AVGO 2026-06-03 16:15 ET low (295.4665)
   - MSFT 2026-08-05 16:13 ET low (384.28)
2. The real moves in the earnings fixture set are not marked within three minutes of the jump: MSFT 2026-07-29, AAPL 07-30, SMCI 07-21, PLTR 08-03, TSLA 07-22, MU 06-24, LULU 09-03, AMZN 07-30, CRM 08-26, AMD 08-04, NFLX 07-16, UPST 08-04 and META 07-29. The one allowed exception is AVGO 06-03's 295.47.
3. Raw `open/high/low/close/volume` in `minute_ohlcv` are identical before and after the sweep: a checksum per chunk, compared.
4. `prints=all` at 1m returns today's bars exactly. `prints=all` at 5m returns 422.
5. The eligible 1m bars for any window, rolled up to 5m, equal the 5m rollup for that window.
6. After the swap, `mt data caggs verify` exits 0 on all four rollups, coverage is fresh, and `/api/v1/bars` serves 5m and up throughout steps 1–4.

### Technical Requirements

- Detector unit tests run on fixture frames cut from real production bars: the SPY, AVGO and MSFT examples above, the earnings set, a dense-print session (MSFT 2026-06-01 16:40–17:12 ET), and a quiet regular-hours day that must produce zero marks. No synthetic-only fixtures.
- The calibration task records the chosen `K/M/R`, the marked share, the prior-close match rate and the earnings-set result in the design's walkthrough when implementation completes.
- Migration tests: 062–064 on a cold start, and 064 on a database that holds the old rollups with data (integration tier, throwaway database).
- The eligible-SQL renderer has a unit test for each bit, and an integration test that the rollup and the 1m path agree.
- Sweep: an integration test on a throwaway database with compressed chunks covering resume after an interrupted chunk, raw columns unchanged, and the boundary-day context.
- Health checks: unit tests for both failure states.
- `ruff`, `mypy` clean on touched files; API references updated; the 120 non-goal section updated.

### Integration Requirements

- The acquisition pass inserts marked bars with no change to its quota, gap or outcome behaviour.
- `mt data accounting`, `mt data status`, `/api/v1/status` and the coverage index read the same counts before and after the swap.
- The trading UI (mt-ui) charts 5m and up without the spikes, with no client change; `prints` is optional.

### Verification Walkthrough

These commands do not exist yet unless noted.

1. Detector on a known bad session (unit level):
   ```bash
   uv run pytest test/unit/data/quality/test_off_tape.py -v
   ```
   SPY 2026-09-08, the earnings fixtures and the dense MSFT session pass. The quiet day yields zero marks.

2. New bars are marked at insert. After the first minute pass on the release:
   ```bash
   uv run mt data health          # "off-tape: 0 unevaluated, marked share 0.9% (floor …, ceiling …)" → PASS
   ```

3. Sweep history:
   ```bash
   uv run mt data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL" --dry-run   # chunks remaining
   uv run mt data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL"
   ```
   Interrupt it and rerun: it resumes at the interrupted chunk. When done:
   ```sql
   SELECT count(*) FROM minute_ohlcv WHERE off_tape IS NULL;   -- 0
   SELECT time, high, off_tape FROM minute_ohlcv
   WHERE symbol = 'SPY' AND time BETWEEN '2026-09-08 20:00Z' AND '2026-09-08 20:05Z';
   -- 20:01, 20:03, 20:04 carry off_tape & 2 (HIGH)
   ```

4. Eligible vs raw at 1m:
   ```bash
   curl -s "localhost:8100/api/v1/bars/SPY?granularity=1m&start=2026-09-08&end=2026-09-09&prints=all"      | jq '[.bars[]|.high]|max'   # includes the 770.x after-hours prints
   curl -s "localhost:8100/api/v1/bars/SPY?granularity=1m&start=2026-09-08&end=2026-09-09"                 | jq '.prints, ([.bars[]|.high]|max)'
   curl -s -o /dev/null -w "%{http_code}\n" "localhost:8100/api/v1/bars/SPY?granularity=5m&start=2026-09-08&end=2026-09-09&prints=all"   # 422
   ```
   The eligible maximum is the session's real high, with no 770.x after-hours print.

5. Build and swap the rollups:
   ```bash
   uv run python scripts/cutover_171_eligible_rollups.py   # materializes _next newest-first, verifies, swaps, rebuilds coverage, prints a report
   uv run mt data caggs verify                             # exit 0, all four
   ```

6. The chart check: SPY 5m for 2026-09-08 from `/api/v1/bars` has no bar whose high exceeds the 1m eligible maximum. MSFT 5m 2026-06-01 16:40–17:15 ET shows a normal after-hours range (≈ 454–460), not 425–488. The trading UI's 5m chart for the same window confirms it visually.

7. Raw untouched: the per-chunk checksums recorded before the sweep match after it, as printed by the sweep's report.

## Risk Assessment

### Technical Risks

- **Marking real moves.** A wrong threshold erases real highs and lows, the failure the 120 non-goal warns about.
- **The sweep rewrites every compressed chunk** of an 82 GB hypertable. That is a full-table rewrite, the same order of work as the slice 166 rechunk.
- **The swap touches the coverage source** the acquisition seeder reads.

### Mitigation Strategies

- **Marking real moves:**
  - Raw is never modified, and `prints=all` serves it.
  - Calibration is against real fixtures, including an earnings set, with results recorded.
  - The marked-share health ceiling catches a detector that starts marking broadly.
- **The sweep:**
  - One chunk per transaction, resumable from data state.
  - Raw checksums per chunk.
  - Passes stay live (a chunk is locked only while it is rewritten).
  - The chunk set is the same one the slice 163 and 166 tools walked.
- **The swap:** it runs only with the minute pass stopped, `assert_cagg_fresh` guards the seeder anyway, and coverage is rebuilt before the timer restarts.

## Implementation Notes

### Development Approach

1. Fixtures cut from production bars; detector and calibration; constants.
2. `OffTape`/`PrintMode` and `eligible_sql.py`, with tests.
3. Migration 062 and both insert paths; health checks.
4. Sweep and its CLI; integration tests on compressed chunks.
5. `get_minute_data` and `/api/v1/bars` `prints`; API references.
6. Migrations 063/064, the repair loop's newest-first option, and the cutover script; migration integration tests.
7. Update the 120 non-goal section and runbook 100 (sweep, cutover, health lines).

Steps 1–5 are useful on their own: new bars are marked, history is swept, and 1m is served eligible. Steps 6–7 bring the rollups across. **If this does not fit one session, split it there:** 171 marks and serves 1m; a following slice rebuilds and swaps the rollups.

### Special Considerations

- **Architecture decision:** this slice amends [120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md)'s "Outlier handling — Non-Goal".
  - **Unchanged:** storage-time removal stays forbidden, and raw values stay immutable.
  - **Added:** tape-eligibility marks are annotations, and serving defaults to eligible values with raw available.
  - **Why it's not outlier-cleaning:** these prints are excluded from high and low by the exchanges' own rules, not by a strategy's opinion. The PM signs off on this amendment at design review.
- **Extended hours** are where nearly all marks fall. The detector treats the whole ET day (04:00–20:00) as one sequence, because the bad prints sit at the session edges, where a regular-hours-only window would have no context.
- **Adjusted-on-read** multiplies after the eligible values are computed. Marks are on raw prices and do not move with corporate actions.
