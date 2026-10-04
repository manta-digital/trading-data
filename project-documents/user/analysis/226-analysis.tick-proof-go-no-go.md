---
docType: analysis
project: trading-data
topic: tick proof on existing data — go/no-go
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Slice 226 go/no-go: the tick pipeline on the production host

Every number below names the report it came from. Reports are under
`user/notes/`, written by `scripts/proof_226_tick.py` on manta9000 on
2026-10-03, against `trading_tick_proof` (measurements) and `trading_tick`
(the production rebuild and `final`) on the tick cluster `17/tick`.

**Verdict: GO.** Every pass/fail row passes, every decision has a
recommendation, and the production tick database is built and proven.

## Measured table (LLD 226 TD3)

| Quantity | Measured | Rule / bound | Result | Report |
|---|---|---|---|---|
| Ingest rate, slowest unit | 3.9 s (tbbo 2024-12-18, 889,373 records) | ≤ 120 s | pass | `2026-10-03-226-proof-rebuild.md` |
| Ingest rate, whole set | 64 s for 78 units (57.9 s on `trading_tick`) | ≤ 2 h | pass | `…-rebuild.md`, `…-production-rebuild.md` |
| Mapping completeness | 100.00 % of 27,691,412 records, 0 misses | 100 % | pass | `…-mapping.md` |
| Ingest checks | 78/78 ingested, 0 failed; coverage ok on all 87 sessions of both ranges | 78/78, 0, ok | pass | `…-rebuild.md`, `…-final.md` |
| Bytes per record, trades | DBN 48; archive 15.2; uncompressed table 194.7; compressed 19.26 | recorded | — | `…-size.md`, `…-layouts.md` |
| Bytes per record, tbbo | DBN 80; archive 21.0; uncompressed table 237.0; compressed 28.62 | recorded | — | `…-size.md`, `…-layouts.md` |
| Chunk interval 7 days | 1,044 chunks over 20 years; Q1–Q4 planning ≤ 0.44 ms | 1,000–2,000; ≤ 50 ms | validated | `…-queries.md` |
| Query latency, Q1–Q4 (final layout, `trading_tick`) | worst 258 ms (trades Q2) | ≤ 1 s warm | pass | `…-final.md` |
| Query latency, Q5 (a month of bars) | 976–1,180 ms | recorded | — | `…-final.md` |
| Decode batch budget | a worker holds 2.2× its budget; no budget > 10 % faster | ≤ 4×; 10 % | keep 32 MiB | `…-batch.md` |
| Worker count | 1/2/4 workers: 92.5/56.9/44.8 s (4 is 1.27× faster than 2) | ≥ 1.5× and contention within bound | keep 2 | `…-workers.md`, `…-contention.md` |
| Intra-unit checkpoints | slowest unit 3.9 s | needed only above 120 s | not needed | `…-rebuild.md` |
| Batch-job timing | 8 jobs, 24–287 s submit → done | wait budget max(1,800, 2 × 287) | keep 1,800 s; poll 15 s | `…-jobs.md` |
| Capacity (projection) | ES+GC tbbo: 4.70 GB/yr stored, 3.45 GB/yr archived | against 1,339 GB free | fits (projection) | `…-capacity.md` |

## Decisions

### Tier: **tbbo**

Under the Standard plan both tiers are included, so the comparison is bytes
and value, not price. tbbo stores 28.62 B/row compressed against trades'
19.26 (1.49×; `…-layouts.md`) and carries the best bid and offer at every
trade, which a trades record cannot reconstruct. Projected, ES at tbbo is
3.70 GB/year stored (`…-capacity.md`): cheap against `/data`. tbbo already
outranks trades in supersession (225 TD5), so buying it later replaces held
trades days without new code.

`TICK_UNIVERSE` for ES is set to tbbo, **2024-11-02** to 2025-01-01. TD9
named 2024-11-01; the session dated 11-01 opens at 22:00 UTC on 10-31, a day
the tbbo job does not hold, so it can never be complete. The first session
held whole is the one dated 11-04. On `trading_tick` a pass plans nothing,
status reads caught up with tier tbbo, and the trades range's sessions list
complete outside the wanted range. Making status judge caught-up over the
wanted range only needed a fix to 225's `caught_up()` (commit `8a9c47e`): it
had judged every session in scope.

### Spreads: **parent including spreads**

Spreads are 2.3 % of trades rows and 1.8 % of tbbo rows (`…-size.md`), about
120 MB of 4.6 GB uncompressed per tier, so the bytes are immaterial. Both
adopted jobs were bought as `ES.FUT` parent, so outrights-only would make the
held days a different shape from new purchases. Keep `ES.FUT` parent.

### GC: **follow at tbbo**

The free estimate for `GC.FUT` over the latest year is 35,035,188 records
(27 % of ES), projected at 1.00 GB/year stored at tbbo (`…-capacity.md`).
Same dataset, same tables, same layout. Slice 231 still decides GC's spreads
and calendar.

### Standard plan subscription: **technical GO**

Nothing measured blocks it: ingest runs at about 470,000 rows/s
(`…-contention.md`), a year of ES+GC tbbo is about 164 M records (minutes of
ingest), storage fits for decades (projection), and the pipeline is proven
end to end on compressed chunks. Whether plan data prices at $0 is verified
at subscription (architecture). Timing stays the PM's, ideally with realtime
work.

## Settled by measurement

- **Chunk interval:** 7 days kept (`…-queries.md`); no `tick_008`.
- **Layout:** A, `segmentby instrument_id`, `orderby ts_event, sequence,
  sequence_ordinal` (`…-layouts.md`). Bytes tie with B (25.22 vs 25.28 B/row
  whole table); B slows Q4 (a contract's latest tick) to 559 ms against A's
  0.1 ms. Compression 5.70 GiB → 0.65 GiB on `trading_tick` (`…-final.md`).
- **Constants:** every measured constant kept by its rule; docstrings cite
  the reports (commit `refactor: re-set tick constants from the 226 proof`).
  Lock timeout stays 30 s: the supersession delete on compressed chunks took
  0.10 s (`…-layouts.md`).
- **Intra-unit checkpoints:** not needed; Future Work item 3 is closed with
  the slowest unit at 3.9 s against 120 s (`…-rebuild.md`).
- **Tick cluster memory: unchanged.** Every query ran at a 100 % shared-buffer
  hit ratio (`…-queries.md`, `…-final.md`); the whole compressed table is
  0.65 GiB against 4 GB `shared_buffers`; the cluster's summed RSS peaked at
  5.4 GiB under 4 workers, an upper bound (`…-workers.md`); MemAvailable never
  fell below 99 GiB under load (`…-contention.md`).
- **Contention: none measured at 2 ingest workers.** Two Kalshi firings
  overlapped with the tick loop took 298 s and 286 s against the week's
  maximum of 324 s; the solo firing 286 s; tick ingest 474k rows/s overlapped
  against 473k alone (`…-contention.md`). This is not a clearance for the
  minute pass: Kalshi writes little and often, the minute pass walks 13,083
  symbols, and slice 232 measures that overlap.
- **Space partitioning:** rejected as designed. The skew evidence: in every
  chunk one instrument holds 48–99.9 % of the rows, and the top five hold
  100 % (`…-size.md`).
- **Rebuild cost for 227:** 212.5 s from an empty `trading_tick` to 78
  ingested units (migrate 1.0 s, six adopts 151.5 s, estimate 2.1 s, ingest
  57.9 s; `…-production-rebuild.md`), plus the `final` compression.
- **Compressed chunks (TD5):** all five tests pass (commit `67ee8ed`). After
  supersession the chunk goes partially compressed and coverage holds before
  and after recompression; an overlap on a compressed chunk raises without
  PostgreSQL's key detail, so 225's fallback reason names the table;
  `tick_app` needs no new grant. The 8.8 fallback was **not used**.

## Kalshi contract diff

The parity test passes (31 tests). The tick and Kalshi contract files differ
only where TD1 declares (phase names, outcome type, the generic run type);
neither changed in this slice.

Pass code changed in three places, each for a measured or reviewed reason:
verify's decode-error mapping (TD8); delivery and adopt failing the days a
refused header leaves unclaimed instead of aborting (TD8, PM decision
2026-10-03); and status's caught-up scope (above).

## Realtime paths (TD10)

Neither is ruled out. **Path A** (assemble recent history from realtime
capture): the policy leaves chunks younger than 14 days uncompressed for a
live writer, and whole-day supersession works on compressed chunks (TD5).
**Path B** (historical with the lag): same tables, same pass. The chunk
interval and the layout are neutral to both.

## Does this belong in the API?

No new surface. The go/no-go is a document and the harness a one-off script.
