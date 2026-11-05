---
docType: notes
project: trading-data
slice: 303-slice.statistics-and-the-open-interest-roll-rule
dateCreated: 20261104
dateUpdated: 20261105
status: in_progress
---

# Statistics evaluation (slice 303, D11)

The real purchase of ES and GC `statistics` and the definitions they need, run on
throwaway databases, and what it shows. Sections are filled as the tasks that
produce them run (8.2 to 8.5, 14.4, 15.1).

## Purchase attempt, 2026-11-04 (task 8.2, refused; resolved 2026-11-05)

**Setup (8.1).** A primary and a tick database were created on the test cluster
(`mt_303_primary_20261104163444`, `mt_303_tick_20261104163444`), migrated by
`mt data init` (primary 69 migrations) and `mt data init --database tick` (11
migrations, through `tick_010_statistics`). The tick database holds a copy of
production's manifest and availability rows (read from `trading_tick`
read-only: 10 requests, 258 units, 571 definitions, 24,437 ledger rows, the
dataset edge, 106 day conditions), so the tier days are owned and the pass plans
no `tbbo`. The wrapper universe keeps each entry's production tier range and adds
`definition` and `statistics` wants over 2024-08-30 to 2025-01-01.

**Estimate.** `pass --estimate-only --json`: 16 requests, `planned_usd`
0.064747896046, per-pass ceiling 0.50, trailing 30-day spend 8.43457497283817.
Statistics: ES 0.0127 and GC 0.0242, as the design says. Definitions came to
about 0.028 rather than the design's October-only 0.0143: GC has no definitions
before November in the manifest, so its August to September days (about 0.0125)
are planned as well, and one-day ES/GC edges (2024-08-30, 2024-09-30,
2024-11-01) add about 0.001. The plan is inside the 0.50 per-pass ceiling.

**Guard refusal.** `pass --json` exits 5, nothing bought: verdict `refused`,
reason "both MT_TICK_SPEND_CEILING_USD and MT_TICK_SPEND_30D_CEILING_USD must be
set to buy; absent: MT_TICK_SPEND_30D_CEILING_USD". The development `.env` sets
only the per-pass ceiling. The runbook (`100-production-operations.md`, step 7:
"The PM sets ... No agent edits them") reserves the 30-day figure to the PM, and
the trailing spend (8.43) already exceeds the `.env_sample` example of 5.00, so
there is no existing value to reuse. The refusal is recorded and not forced.

The PM then set `MT_TICK_SPEND_30D_CEILING_USD=10` in `.env`. The wrapper copied
only the per-pass ceiling out of it, so the wrapper was fixed to copy the 30-day
one too, and the purchase ran (next section).

## Production pass unchanged (task 15.1, 2026-11-04)

The base commit (`2bf3739`, tick track at `tick_009`) and this branch (`tick_010`
applied) each ran the shipped universe's `mt data tick pass --estimate-only
--json` against a throwaway tick database holding a copy of production's
manifest and availability rows (read-only: 10 requests, 258 units, 571
definitions, 24,437 ledger rows, the edge, 106 day conditions), with the
base commit's code first and the branch's after `mt data init --database tick`.
The two reports are identical after masking the run id, timestamps and
durations: every phase `ok`, no days wanted, 0 requests, planned $0, trailing
30-day spend $0 (the copy holds no spend rows to count), verdict `nothing_to_buy`.
The throwaway database and the base worktree were removed afterwards.

## The purchase (tasks 8.2 and 8.3, 2026-11-05)

`pass --json` verdict `allowed` (planned 0.064747896046, trailing 30-day spend
8.43457497283817, per-pass ceiling 0.50, 30-day cap 10); outcome `ok`. 16 jobs,
295 units delivered, downloaded and verified (0 failed, 0 holed); definitions
projected (83 units: 288 rows inserted). **Databento's reported charge equals the
estimate for every job** (`actual_cost_usd` = `estimated_cost_usd`, total
0.06474789604545).

| request | job id | schema | product | days | cost USD |
|---|---|---|---|---|---|
| 11 | GLBX-20261105-53M8XFU68L | definition | GC | 2024-08-30 | 0.000496 |
| 12 | GLBX-20261105-WA8HTKRYE6 | statistics | ES | 2024-08-30 | 0.000110 |
| 13 | GLBX-20261105-UBUNCSUC3M | statistics | GC | 2024-08-30 | 0.000170 |
| 14 | GLBX-20261105-VM3UCT44GU | definition | GC | 2024-09-01 to 09-30 | 0.012531 |
| 15 | GLBX-20261105-JWBUGS9B6T | statistics | ES | 2024-09-01 to 09-30 | 0.003554 |
| 16 | GLBX-20261105-JN3XL7WVJB | statistics | GC | 2024-09-01 to 09-30 | 0.005570 |
| 17 | GLBX-20261105-MFSB945MEQ | definition | ES | 2024-09-30 | 0.000050 |
| 18 | GLBX-20261105-RX3P3T3LFJ | definition | ES | 2024-10-01 to 10-31 | 0.001356 |
| 19 | GLBX-20261105-KUGLEPNNVK | definition | GC | 2024-10-01 to 10-31 | 0.012949 |
| 20 | GLBX-20261105-VFRXRTBEG8 | statistics | ES | 2024-10-01 to 10-31 | 0.002869 |
| 21 | GLBX-20261105-UEWNBQV3A7 | statistics | GC | 2024-10-01 to 10-31 | 0.006142 |
| 22 | GLBX-20261105-RAC73FKWM6 | definition | GC | 2024-11-01 | 0.000504 |
| 23 | GLBX-20261105-XY38TFRVNR | statistics | ES | 2024-11-01 to 11-29 | 0.002823 |
| 24 | GLBX-20261105-7MPJX43SSL | statistics | GC | 2024-11-01 to 11-29 | 0.006315 |
| 25 | GLBX-20261105-7C6BWV8WXS | statistics | ES | 2024-12-01 to 12-31 | 0.003348 |
| 26 | GLBX-20261105-RYXE76XYRF | statistics | GC | 2024-12-01 to 12-31 | 0.005960 |

**Reuse (14.2, 15.1, 15.3, and 304 adopting these jobs).** Databases
`mt_303_primary_20261104163444` and `mt_303_tick_20261104163444` on the test
cluster; archive `~/scratch-303/archive` (one directory per job id; 304 adopts
that path); wrapper `cli303.sh` + `walk_cli.py` + `run.env` in the session
scratchpad (they copy `MT_DATABENTO_API_KEY`, `MT_TICK_SPEND_CEILING_USD` and
`MT_TICK_SPEND_30D_CEILING_USD` out of `.env` and set `MT_TIMESCALE_DB_URL`,
`MT_TIMESCALE_MAINTENANCE_URL`, `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL`,
`MT_TICK_ARCHIVE_DIR`).

## What the real data broke, and the fixes (task 8.2 success)

The first `ingest` ingested 84 units and failed 128, all with `resolution`: the
design resolved a statistics record's instrument through the definition window
that holds its `ts_event`. Measured on the failing files, the provider publishes
statistics **before** a definition activates (spreads and some outrights, 22 to
335 hours ahead) and a final settlement **after** it expires (the expiry day's
last records arrive hours past the window's end). Fixes, in order:

1. Statistics resolve by id only (`resolve(..., by_id=True)`), and their plan
   loads every window of the product (`_definitions(..., by_id=True)`); other
   families keep the window rule. Ten units still failed on the expiry shape
   until the plan loaded all windows rather than those meeting the unit's day.
2. After the fixes 4 units of 128 were left, then 1: unit 506 (ES, 2024-12-08) has
   one settlement record, ESZ9 (a 2029 contract), with `ts_ref` Saturday
   2024-12-07, which is not a session. `stat_reference` rejects it by design (D4,
   FR3), so the unit stays failed (`retry_exhausted`) and the finding is real
   provider data, not a defect. It leaves ES sessions 2024-12-06 and 2024-12-09
   `retry_exhausted` in status.

Final: 211 of 212 statistics units ingested and 83 of 83 definition units
projected; one statistics unit (506) refused for the Saturday `ts_ref`. Raw-count
proof: ledger = raw in every session of both products.

## Data facts (task 8.4; `scripts/measure_303_statistics.py`)

Run over 2024-09-01 to 2025-01-01 on the throwaway tick database.

- **`ts_ref`**: always 00:00:00 UTC of the trading date, for settlement, cleared
  volume and open interest, both products.
- **`stat_flags`**: cleared volume and open interest always 0. Settlement price
  1 (preliminary), 2 (final), 3 (both), 7 (once, ES), 0 (twice, GC): ES 10 / 5,427 /
  4,091 / 1; GC 2 / 787 / 25,749 / 27,252 (flags 0 / 1 / 2 / 3).
- **Reference dates**: 86 distinct per product. Only 2024-08-29 and 2024-08-30 are
  not in the judged range (the calendar read starts 2024-09-01), so they are not
  anomalies; every date within the range is a session.
- **Latest receive after the session's close** (hours): settlement min 0.6
  (ES) / 0.7 (GC), median 2.2, max 206 (ES) / 302 (GC); cleared volume and open
  interest min 16.4, median 16.6, max 68 (ES) / 67 (GC). Volume and interest sit
  inside one session after, as `STATISTICS_PUBLICATION_SESSIONS = 1` assumes.
  **Settlement does not**: the 206 and 302 hour maxima are late corrections to
  deferred contracts, so a session judged complete can still gain a settlement
  record days later. Status reads load-completeness over a one-session window
  only; the late records land in a later unit and the resolved read (D5) takes the
  latest per key, so no value is lost, but the content finding for a session can
  be stale. Not changed; 305 should know.
- **V / C** (tick tier, traded over cleared): ES 58 sessions, 343 contract-sessions,
  149 agree, 194 under, V/C p10 0.0, p50 0.43, p90 0.95; GC 40 sessions, 601
  contract-sessions, 198 agree, 403 under, p10 0.0, p50 0.18, p90 0.88. **No
  `over`.** On 2024-11-04 the front month's V/C is 0.87 (GC Z4) and 0.95 (ES Z4); deferred months sit far
  lower because their cleared volume is mostly spread-leg volume, which no
  outright trade record carries (see below).
- **Resolved read** over the four months: ES 4,537 rows in 0.04 s; GC 26,103 rows
  in 0.12 s. (The full range reaches about 19.4 M rows; 307 times that.)

## Check: back-month records (task 8.5), and what changed

`status` and `coverage --schema statistics` first exited 3 with, for each
product, hundreds of content findings and 62 `no_trades` verdicts (ES 17, GC 45).
The findings were real provider behavior, not load gaps:

1. **Deferred contracts report a settlement and nothing else.** ESZ7 to ESZ9 and
   GCZ8 to GCM0 never have cleared volume or open interest in any session (GC's
   GCM7 has them on 8 of 40 sessions, GCV6 on 11 of 24). Fix: cleared volume and
   open interest are owed by a contract only once it has reported them in the
   range judged (`UNCONDITIONAL_STAT_TYPES` in `statistics_constants.py`,
   `content_findings`). Findings fell from 501 to 169 (GC) and 1,248 to 42 (ES).
   What remains: two holiday sessions per product (2024-09-02 and 2024-11-28,
   every outright missing everything; CME gives a holiday session no statistics
   of its own), and a GC sparse tail (a contract that stops reporting volume and
   interest part-way). The holiday sessions are genuine gaps in what is held and
   stay findings.
2. **`no_trades` for a deferred month is spread-leg volume, not a gap.** ESZ5
   2024-11-04: cleared 138, 0 traded, while the same session's front month agreed
   within 5% and ESZ5 itself shows only 14 outright contracts over 3 sessions in
   the tier. Fix: `no_trades` is a finding only when nothing at all traded in the
   session (`settle_no_trades`); a contract with V = 0, C > 0 beside a session
   that traded is `under`.
3. **Open interest `n` could not resolve.** D9 refused a session when any
   candidate lacked open interest at L, which with deferred months holding none
   made `n.0` unresolved nearly everywhere (GC 2024-11-04 to 2025-01-01). Fix: a
   candidate with no record at L is not ranked (never read as zero); `n` is
   unresolved only when no candidate has open interest, or L is not load-complete.

After the three fixes: coverage exits 0 for ES and GC over 2024-11-04 to
2025-01-01 (ledger = raw in every session, no `over`, no `no_trades`). Status
shows the statistics sessions `complete` (GC 85 of 87, ES 83 of 87) except two
`edge_unknown` sessions per product at the end of the range, whose window reaches
past the purchase, and ES's two `retry_exhausted` sessions from the Saturday
`ts_ref`. A fourth fix is in the
status window: it ran Friday through the next session's close day and so held a
Saturday no session touches and nothing is wanted for, which read `edge_unknown`
for every Friday session. The window is now the UTC days the session and its
publication session touch.

GC `n.0` over 2024-11-04 to 2025-01-01: GCZ4, then GCG5; one session unresolved,
2024-12-02, because Thanksgiving's lag session (2024-11-28) has no statistics.
`v.0`: GCZ4 then GCG5, unresolved only 2024-11-04 and 2024-11-05 (no tick data
before 2024-11-02: lag sessions 2024-10-31 and 2024-11-01 are missing).

## Real-file tests (task 9)

Whole purchased ES days 2024-12-03 (1,099 records) and 2024-12-04 (1,503) are
committed (`test/fixtures/databento/real/`, job `GLBX-20261105-7C6BWV8WXS`, under
1 MB each) and ingested by `test_tick_statistics_real.py` with the committed real
definitions: provider = decoded = stored, every referenced record has a session,
revisions resolve to one value per contract, statistic and session, and coverage
agrees (a deleted row exits 3). The volume check against the committed real `tbbo`
cut and the derived `ohlcv-1m` day gives no `over` and no `no_trades`. Those two
files are cuts (about 3,700 trades of ~266,000), so their V / C is not a measure;
the full-day figure for the same session, from the purchased tick tier in the
throwaway database, is: ESZ4 cleared 991,961, traded 920,768 (0.928, `under`);
ESH5 8,432 against 2,099 (0.249, `under`); ESM5 49 against 48 (0.98, `under`);
ESU5, ESZ5 and ESZ6 0 against 0 (`agree`). So 3 of 6 outrights under, none over.
