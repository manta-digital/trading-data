---
docType: slice-design
slice: off-tape-prints
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163, 921]
interfaces: [172, 182, 186, 190]
dateCreated: 20261015
dateUpdated: 20261016
status: in_progress
---

# Slice Design: Off-Tape Prints

## Overview

EODHD's 1-minute bars include trades that the consolidated tape rules keep out of a bar's high, low and last price: late-reported trades, average-price trades, and trades at a prior reference price. They show up as tall isolated wicks, mostly in pre-market and after-hours. Every rollup takes `max(high)`/`min(low)`, so each one reappears on the 5m, 15m, 1h and 4h bars. That is the "same vertical spikes on every timeframe" the trading UI showed on 2026-10-15.

This slice marks those values on the raw minute bars, without changing the raw values. It marks every bar as it is inserted and sweeps all history. It serves tape-eligible 1m bars on request. Slice 172 builds the 5m-and-up rollups from the marks and makes eligible bars the default. The split was decided with the PM after the design review (2026-10-16).

Evidence gathered for this design (production data, 2026-10-15):

- The rollups are arithmetically correct. SPY 5m from `/api/v1/bars` equals the API's 1m bars rolled up to 5m (0 differences in 2,684 bars). The stored minute bars equal EODHD's own `/intraday` response.
- The marked values are trades (the bars carry volume). EODHD's tick endpoint, which would show sale conditions, returns 403 on our plan.
- **Prior-reference prints are confirmed by price.** Of the values the prototype detector marks, 36% exactly equal one of the symbol's previous 30 official daily closes, against about 2.3% of unmarked values. Example: SPY 2026-09-08 07:35–07:38 ET prints at 770.19, the official close two sessions earlier.
- A fixed percentage cannot separate these from real thin-market moves: one session (2026-09-08, close ≥ $5) has 2,818 of 1.51 M bars with a 1% wick.
- Some bad prints fall inside regular hours: AAPL 2026-06-08 15:15 ET high 313.04 with the market at 304.5. They sit inside the official daily range: real trades from earlier in the day, reported late into the wrong minute.

## Value

- Every minute bar carries a mark saying which of its open, high, low and close are off-tape. Slice 172 needs this to build clean rollups, and any later reader (backtests, a provider comparison) can use it too.
- `/api/v1/bars?granularity=1m&prints=eligible` serves 1m bars without the spikes. Raw stays the default until 172 switches the default for every granularity at once.
- The provider choice stays open. PM direction: if we buy equity data, it is Databento ($199/month, same as Polygon). For now we clean EODHD. The detector reads bars, not EODHD's format.

## Technical Scope

**Included:**

1. A nullable `off_tape smallint` column on `minute_ohlcv`: a bitmask of which of open, high, low and close are off-tape. `NULL` means not evaluated, and `0` means evaluated and clean. Raw values are never modified.
2. The detector: a pure function from one symbol's ET day of bars to their masks.
3. Both minute insert paths mark bars in the same transaction that inserts them: the acquisition pass (`_insert_minute_bars`) and `write_minute_data_bulk`.
4. A one-time, resumable sweep that marks all history: `mt data off-tape sweep`.
5. The eligible-value SQL renderer, used by the 1m read path now and by 172's rollups.
6. `get_minute_data` and `/api/v1/bars` gain `prints`, at 1m only: `all` (default, today's bars) or `eligible`.
7. Two `mt data health` checks: no unevaluated bars in the last collected session, and the marked share of that session inside measured bounds.
8. Documentation:
   - a dated amendment to [140-arch.data-quality-operations.md](../architecture/140-arch.data-quality-operations.md) for the new column and the read API's `prints`;
   - the amended "Outlier handling — Non-Goal" section of [120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md);
   - both API references;
   - runbook 100 (the sweep and the health lines).

**Excluded:**

- **5m-and-up and the default switch:** slice 172 rebuilds the rollups, serves eligible 5m+ bars, and makes eligible the default for every granularity.
- **Daily and coarser bars:** they come from EODHD's EOD endpoint, which already reports the official session range.
- **Other data:** futures (Databento) and Kalshi.
- **Buying equity data with sale conditions.**
- **Changing stored data:** no removal, smoothing or forward-filling at storage time.
- **Exposing the marks:** per-bar marks in API responses.

## Dependencies

### Prerequisites

- **163:** the compressed-chunk layout of `minute_ohlcv` (7-day chunks, `segmentby = symbol`) that the sweep walks.
- **921:** the "last collected session" definition the health checks read, and the full-UTC-day fetch window the insert paths receive.
- **External:** EODHD `/intraday` responses, unchanged.

### Interfaces Required

- **`minute_ohlcv`:** a compressed hypertable of 1,215 compressed chunks, 82 GB.
- **Maintenance URL:** the sweep needs it for `decompress_chunk`/`compress_chunk`, passed as an explicit argument.

## Architecture

### Component Structure

| Component | Location | Role |
|---|---|---|
| `OffTape` (`IntFlag`), `PrintMode` (`StrEnum`) | `constants.py` | Bits `OPEN=1, HIGH=2, LOW=4, CLOSE=8, ALL=15`; modes `all`, `eligible`. One definition; the SQL renders from them. |
| Detector | `data/quality/off_tape.py` | `mark_day(bars) -> masks` for one symbol's ET day. Pure, no I/O. |
| Day marking | `data/quality/off_tape_store.py` | `mark_days(conn, symbol, et_days)`: reads those days' bars, runs the detector, and writes masks that differ. Shared by both insert paths and the sweep. |
| Eligible-value SQL | `market/schema/eligible_sql.py` | Renders the clamped open/high/low/close expressions and the "bar has an eligible price" predicate. |
| Sweep | `market/maintenance/off_tape_sweep.py`, CLI `mt data off-tape sweep` | Marks history chunk by chunk. |
| Read path | `market/timescale_minute_db.py`, `api_server/routes/bars.py` | `prints` at 1m. |
| Health | `cli/commands/health.py` | The two checks. |

### Data Flow

```
EODHD /intraday ─► fetch frame (one symbol, whole UTC days)
                     │  COPY → stage → INSERT ... ON CONFLICT DO NOTHING
                     ▼
              minute_ohlcv  ──(same transaction)──►  mark_days(symbol, ET days the frame touched)
                     │                                   reads each whole ET day back,
                     │                                   runs the detector, writes masks
                     ▼
     1m read: prints=all → raw columns;  prints=eligible → eligible expressions
                     │
     health: NULL masks + marked share of the last collected session
```

History reaches the same `mark_days` call through the sweep.

### State Management

- **The `off_tape` column** is the only new state.
- **The rule that makes the two paths agree:** a bar's mask is a function of all bars of its symbol's ET day and nothing else. The insert path re-marks every ET day a frame touches, after the frame's rows are in the table. A frame boundary is therefore never a detection boundary. An after-hours tail that lands in the next UTC day's frame, as it does every winter (20:00 ET = 01:00 UTC), is judged with the whole day. A day that gains bars later is re-marked when they arrive. The insert path and the sweep produce the same masks for the same stored day, and a test asserts it.
- **Sweep progress needs no table.** A chunk is done when it holds no `NULL` `off_tape` rows. A rerun skips done chunks and redoes an interrupted one.

## Technical Decisions

### The detector rule

A value is off-tape when neither the minutes before it nor the minutes after it trade anywhere near it.

For each bar of one symbol's ET day:

- `before` = median close of the previous `K` bars; `after` = median close of the next `K` bars (by row, not clock, because pre-market bars are sparse). At the day's first and last bars only the available side is used.
- `scale` = the larger of the two sides' median absolute close-to-close change.
- `threshold` = `max(M × scale, R × close, A)`, where `A` is a dollar floor (added 2026-10-16, below).
- Each of open, high, low and close is marked when it lies above `max(before, after) + threshold` or below `min(before, after) − threshold`.

Why each part is there, from the prototype runs:

- **Two-sided envelope.** A real jump (an after-hours earnings release) lands near the `after` side, and a value between the two medians is never marked. A one-sided version marked SMCI's real close in the 2026-07-21 earnings minute.
- **Closes, not ranges, for the scale.** Where bad prints are dense, every bar's range is inflated (MSFT 2026-06-01 after 16:45 ET had one in almost every minute), and a range-based scale stopped marking anything. Closes are rarely off-tape.
- **A dollar floor (`A`, PM decision 2026-10-16).** Measured over the whole universe, tick-bound prices (a $3.75 stock moving one cent at a time) have a median close change of 0, so the threshold fell to `R × close`, about two ticks, and marked the bottom of slow real dips. ARRY 2026-10-08 15:34 ET is the fixture. Universe-wide this put 42% of marks in regular hours, concentrated in illiquid names. `A = $0.02` cut regular-hours marks on 2026-10-08 from 4,470 to 1,440 with every fixture still passing.
- **The larger side's scale.** At the onset of an earnings move the calm side would set a tight threshold and mark the bottom of a real V. META 2026-07-29 16:03 was marked under an averaged scale and is not marked under the larger one.

**Starting calibration:** `K = 10`, `M = 10`, `R = 0.003`. Over 40 symbols (3.1 M bars, 2026-06-01 to 10-09):
- 1.0% of bars marked, 845 of them in regular hours;
- 36% of marked values equal a prior official close;
- of the 15 earnings jumps, 2 values marked within three minutes of a jump: AVGO 2026-06-03's 295.47 low, 38% below the market, and one marginal value;
- SPY 5m bars with a wick over 1% fall from 249 to 1 once rolled up eligible, MSFT from 859 to 95, NVDA from 835 to 28.

The constants (`OFF_TAPE_WINDOW_BARS`, `OFF_TAPE_SCALE_MULTIPLE`, `OFF_TAPE_MIN_FRACTION`, `OFF_TAPE_MIN_ABSOLUTE`) live in `constants.py`. Final calibration (2026-10-16): `K = 10`, `M = 12`, `R = 0.003`, `A = $0.02`; `M = 10` marked META 2026-07-29 16:04 ET's real earnings low, and `M` from 11 to 20 passes every fixture. The calibration task finalizes them against the fixture set and records the result; they are not re-tuned by eye.

**Why open and close are marked, not only high and low.** In the prototype, 8,171 bars (0.26%) have an off-tape open or close, and every one of them also has an off-tape high or low. A high/low-only mask would clamp the wick to the bar's body. But the body *is* the bad print when the open or close is one. SPY 2026-09-08 07:35–07:38 ET closes at 770.19 against a 767.9 market. Those bars would keep a 770.19 body and the spike would survive in every rollup.

**Known limit:** in the first minutes after 16:00 ET on earnings days, prints at the old closing price stay unmarked. Examples are MSFT 2026-07-29 16:02–16:04 ET lows at 390.30–390.54 while the market traded 401–409. Those minutes' real range is so wide that no neighbour-based rule can separate them.

**Rejected:**

- **A fixed percentage cut.** It marks real small-cap trading.
- **Clamping regular hours to the official daily range.** The regular-hours bad prints are inside it.
- **Matching prior closes as the detector.** It finds one of three print types.

### Eligible bar values

For a bar with mask `f`, let `E` be its unmarked values among open, high, low and close.

- `lo = min(E)` and `hi = max(E)`.
- Each value `v` becomes `clamp(v, lo, hi)`. An unmarked value is unchanged, and a marked high falls to the bar's highest eligible value.
- **A bar with all four values marked (`f = ALL`) has no eligible price.** It is left out of eligible results entirely, price *and volume*. Its trades are tape-reportable volume, but a bar with volume and no price has no place in a price series. Leaving volume out keeps one invariant across both slices: eligible 1m bars rolled up equal 172's eligible rollups exactly, volume included. It is 0.02% of bars (722 in the prototype). Raw volume stays available through `prints=all`.

The rule needs nothing outside the bar, so 172 can run it inside a rollup definition. Rejected: replacing a marked value with a neighbour median, which would invent a price no trade printed and cannot run inside a rollup.

`eligible_sql.py` renders `lo`/`hi` as `LEAST`/`GREATEST` over `CASE WHEN COALESCE(off_tape,0) & <bit> = 0 THEN <col> END`, `clamp(v)` as `LEAST(GREATEST(v, lo), hi)`, and the predicate as `COALESCE(off_tape,0) <> 15`. A test cluster probe (TimescaleDB 2.29.1) confirmed these expressions in a continuous aggregate over a compressed hypertable, and confirmed `ADD COLUMN` and `UPDATE` on compressed chunks.

### Insert paths

Inside the existing insert transaction, after `INSERT ... ON CONFLICT DO NOTHING`:

1. Compute the ET dates of the frame's bars.
2. Call `mark_days(conn, symbol, dates)`.
3. Commit.

`mark_days` reads those whole days back (one indexed range read on `(symbol, time)`), runs the detector, and updates the rows whose mask differs. New rows are always written, because they are `NULL`. A committed row is therefore never unevaluated, and the health check can treat any `NULL` in a collected session as a defect. A backfill frame into an old, compressed chunk updates rows there too, which the probe confirmed works.

### Sweep

For each chunk of `minute_ohlcv` holding any `NULL` `off_tape`, oldest first, single process:

1. Read the chunk's bars, widened to whole ET days at both edges.
2. Compute masks per symbol-day with the same detector.
3. For a compressed chunk, in one transaction under `lock_timeout = OFF_TAPE_SWEEP_LOCK_TIMEOUT`:
   - record a checksum of the chunk's raw columns;
   - `decompress_chunk`, then `UPDATE` every row from a temp table of masks;
   - check the checksum again, then `compress_chunk`.

   An uncompressed chunk needs only the `UPDATE`.

Every row is written, not only marked ones. The chunk is being rewritten anyway, and "not evaluated" keeps an explicit meaning.

**No parallel workers.** The prototype detector ran 3.1 M bars in 22.6 s on one core. The first sweep task measures a full chunk and records it. Workers get added only if that measurement says the sweep cannot finish between weekly passes.

The sweep connects through the maintenance URL passed as an argument. It never reads the URL from the environment inside the tool.

### Failure handling

| Trigger | Effect | Handling |
|---|---|---|
| Detector or `mark_days` raises in an insert path | That symbol's insert transaction rolls back; nothing is written unevaluated | Logged with `logger.exception`, then handled by the pass's existing failed-write path, the same as a database error on that insert today. The task confirms what that path does to the symbol's gap rows and records it |
| Sweep cannot lock a chunk within `OFF_TAPE_SWEEP_LOCK_TIMEOUT` | Chunk skipped this round | Retried at the end of the run; the run reports any chunk still unlocked and exits non-zero |
| A minute pass is running (`pass_runs` shows it) | The newest chunk is the one the pass writes into | The sweep defers that chunk until the pass ends, so an acquisition insert never waits on a chunk rewrite |
| Deep backfill inserts into a chunk the sweep holds | The insert waits for that one chunk's transaction | Accepted; bounded by one chunk's rewrite, measured and recorded by the first sweep task |
| Sweep process killed mid-chunk | The transaction rolls back and the chunk keeps its `NULL`s | Rerun redoes it |
| Raw checksum differs after the `UPDATE` | Should be impossible | The transaction rolls back; the sweep stops with the chunk named, non-zero exit |

### API: `prints` (1m)

- `GET /api/v1/bars/{symbol}?granularity=1m&prints=all|eligible`, default `all`, so no current client sees a change before 172.
- `prints=eligible` at any other granularity returns `422` with "prints=eligible is served at 1m only until the eligible rollups are built". Slice 172 removes this.
- The response gains `prints`, echoing the mode served.
- In eligible 1m responses, all-marked minutes are absent, the way a minute with no trades is absent today.
- `get_minute_data(..., prints=PrintMode.ALL)` carries the same choice for `mt data get` and the CSV export.

### Health

There are two checks in `mt data health`, over the last collected minute session (921's definition):

- **Unevaluated bars:** any `NULL` `off_tape` in that session fails, naming the count. Insert paths never commit unevaluated rows, so this means a path skipped marking.
- **Marked share:** the fraction of the session's bars with a non-zero mask must lie within `[OFF_TAPE_SHARE_FLOOR, OFF_TAPE_SHARE_CEILING]`. Zero marks means the detector is not running, because these prints are on the tape every day. A flood means the provider changed or the detector broke. The bounds are measured from a full universe week during implementation and recorded with the measurement. The 40-symbol prototype measured 1.0%.

### Patterns and Conventions

- **One definition each:** bits, modes and thresholds live in `constants.py`, and the SQL is rendered from them.
- **One marking routine:** both insert paths and the sweep call `mark_days`, so there is no second way to mark a bar.
- **No silent fallback:** a marking failure fails the write. It never inserts unevaluated bars to keep going.

## Implementation Details

### Database / Storage Schema

- **Migration 062:** `ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS off_tape smallint`. Nullable and without a default, so it is a metadata-only change on a compressed hypertable.
- **No index:** reads filter by `(symbol, time)` as today.

### API Contracts

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `prints` | `all`, `eligible` | `all` | `eligible` valid with `granularity=1m` only in this slice |

`BarsResponse` gains `prints` (string).

### Migration Plan

| Step | Who | What |
|---|---|---|
| 1 | release | Migration 062 applied; both insert paths mark from the next pass on |
| 2 | automation identity | `mt data off-tape sweep --maintenance-url …` over all history; resumable, passes stay live |

## Integration Points

### Provides to Other Slices

- **172:**
  - the `off_tape` marks on every minute bar;
  - `eligible_sql.py`, the one renderer its rollup definitions use;
  - the `prints` parameter and `PrintMode`, whose default it switches;
  - the eligible-volume rule its rollups must match.
- **182/186/190:** `prints` on the bars endpoint, inside the existing client contract (admission, staleness, 404 semantics unchanged), documented in both API references.

### Consumes from Other Slices

- **921:**
  - the full-UTC-day fetch frame;
  - the per-symbol insert transaction;
  - the "last collected session" the health checks read.
- **163:** the chunk layout the sweep walks.

## Success Criteria

### Functional Requirements

1. These known values are marked, and absent from eligible 1m bars:
   - SPY 2026-09-08 07:35–07:38 ET (770.19), 16:01–16:04 ET highs (770.3516), and 16:52 and 17:26 ET lows (724.228);
   - SPY 2026-09-11 17:33 ET high (787.5169);
   - AVGO 2026-06-03 16:15 ET low (295.4665);
   - MSFT 2026-08-05 16:13 ET low (384.28).
2. Within three minutes of these earnings jumps nothing is marked, except AVGO 06-03's 295.47: MSFT 2026-07-29, AAPL 07-30, SMCI 07-21, PLTR 08-03, TSLA 07-22, MU 06-24, LULU 09-03, AMZN 07-30, CRM 08-26, AMD 08-04, NFLX 07-16, UPST 08-04, META 07-29 and AVGO 06-03.
3. Raw `open/high/low/close/volume` are identical before and after marking: per-chunk checksums in the sweep, plus a test for the insert path.
4. Masks are the same whether a day arrived through an insert path, in several frames, or through the sweep.
5. `prints=all` at 1m returns today's bars exactly. `prints=eligible` at 5m returns 422.
6. After the sweep, no `minute_ohlcv` row has a `NULL` `off_tape`, and both health checks pass.

### Technical Requirements

- **Detector fixtures** are cut from real production bars:
  - the SPY, AVGO and MSFT examples above;
  - the earnings set;
  - a dense-print session (MSFT 2026-06-01 16:40–17:12 ET);
  - a winter day whose after-hours tail crosses UTC midnight;
  - a quiet regular-hours day that must produce zero marks.

  No synthetic-only fixtures.
- **Insert/sweep agreement test:** one stored day, inserted as two frames split at UTC midnight, then swept. The masks must be identical.
- **Calibration record:** the chosen `K/M/R`, the universe-week marked share, the prior-close match rate and the earnings-set result go into this design's walkthrough when implementation completes.
- **Eligible-SQL renderer:** a unit test per bit and for the all-marked bar.
- **Sweep integration test** on a throwaway database with compressed chunks:
  - resume after a killed chunk;
  - lock-timeout skip;
  - raw checksums unchanged.
- **Failure-table tests:** each row has a test; the deferred-newest-chunk row uses a fake `pass_runs` row.
- **Health checks:** unit tests for both failure states.
- **Read latency:** the bars load test gains a `prints=eligible` 1m case under the same latency budget as `prints=all`.
- **Quality bar:** `ruff` and `mypy` clean on touched files; API references and both architecture documents updated.

### Integration Requirements

- The acquisition pass inserts marked bars with no change to its quota, gap or outcome behaviour.
- `mt data accounting`, `mt data status` and the coverage index read the same counts before and after the sweep. `minute_count` and the rollups are untouched in this slice.

### Verification Walkthrough

1. Detector and agreement tests:
   ```bash
   uv run pytest test/unit/data/quality/test_off_tape.py test/integration/test_off_tape_marking.py -v
   ```
2. New bars are marked at insert. After the first minute pass on the release:
   ```bash
   uv run mt data health      # off-tape: 0 unevaluated, marked share within bounds → PASS
   ```
3. Sweep history; interrupt it once and rerun to see it resume:
   ```bash
   uv run mt data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL" --dry-run   # chunks remaining
   uv run mt data off-tape sweep --maintenance-url "$MT_TIMESCALE_MAINTENANCE_URL"
   ```
   ```sql
   SELECT count(*) FROM minute_ohlcv WHERE off_tape IS NULL;   -- 0
   SELECT time, high, off_tape FROM minute_ohlcv
   WHERE symbol = 'SPY' AND time BETWEEN '2026-09-08 20:00Z' AND '2026-09-08 20:05Z';
   -- 20:01, 20:03, 20:04 carry off_tape & 2 (HIGH)
   ```
4. Eligible vs raw at 1m:
   ```bash
   B="localhost:8100/api/v1/bars/SPY?granularity=1m&start=2026-09-08&end=2026-09-09"
   curl -s "$B"                 | jq '.prints, ([.bars[]|.high]|max)'   # "all", includes the 770.x after-hours prints
   curl -s "$B&prints=eligible" | jq '.prints, ([.bars[]|.high]|max)'   # "eligible", the session's real high
   curl -s -o /dev/null -w "%{http_code}\n" "${B/1m/5m}&prints=eligible"  # 422
   ```

## Risk Assessment

### Technical Risks

- **Marking real moves.** A wrong threshold hides real highs and lows from eligible readers, which is the failure the 120 non-goal warns about.
- **The sweep rewrites every compressed chunk** of an 82 GB hypertable. That is the same order of work as the slice 166 rechunk.

### Mitigation Strategies

- **Marking real moves:**
  - raw is never modified, and is the default until 172;
  - calibration runs against real fixtures, including the earnings set, and the results are recorded;
  - the marked-share ceiling catches a detector that starts marking broadly.
- **The sweep:**
  - one chunk per transaction, resumable from data state;
  - per-chunk raw checksums;
  - the newest chunk is deferred while a pass runs;
  - the failure table above.

## Implementation Notes

### Development Approach

1. Fixtures cut from production bars; the detector; calibration and constants.
2. `OffTape`/`PrintMode` and `eligible_sql.py`, with tests.
3. Migration 062, `mark_days`, both insert paths, and the agreement test.
4. Health checks.
5. The sweep, its CLI, and its integration tests.
6. `prints` at 1m in `get_minute_data` and `/api/v1/bars`; the load-test case; API references.
7. The 140 amendment, the 120 non-goal amendment, and runbook 100.

### Special Considerations

- **Architecture decision:** this slice amends [120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md)'s "Outlier handling — Non-Goal".
  - Unchanged: removal at storage time stays forbidden, and raw values stay immutable.
  - Added: tape-eligibility marks are annotations, and serving can choose eligible values.
  - Why it's not outlier cleaning: the exchanges' own rules already keep these prints out of high and low, so this isn't one strategy's opinion. The PM signs off on the amendment at review.
- **Extended hours** are where nearly all marks fall. The detector treats the whole ET day (04:00–20:00) as one sequence, because the bad prints sit at the session edges.
- **Adjusted-on-read** multiplies after the eligible values are computed. Marks are on raw prices and do not move with corporate actions.
