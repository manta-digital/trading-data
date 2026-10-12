---
docType: notes
slice: operator-surface-full-parity
project: trading-data
dateCreated: 20261012
dateUpdated: 20261012
---

# Slice 230 walkthrough on `trading_tick`

Run on 2026-10-12 on manta9000, from branch
`230-slice.operator-surface-full-parity`, against `trading_tick` and the
production calendar, with the repo's `.env` loaded. Every read runs in a
`READ ONLY` transaction. The output below leaves out the
`TradingCalendar initialized` log lines on stderr.

## Step 1: continuous 1-minute bars across the September roll (task 5.7)

```
$ mt data tick get 1m --product ES --start 2024-09-16 --end 2024-09-20
ES  v.0 (default)  definitions as of 2024-12-31  1m  2024-09-16 → 2024-09-20 (end exclusive)
  2024-09-16 → 2024-09-17  ESU4 (118)
  2024-09-18 → 2024-09-19  ESZ4 (183748)
time                            session_date  raw_symbol  instrument_id  open     high     low      close    volume
2024-09-15T22:00:00.000000000Z  2024-09-16  ESU4  118     5622.75  5629.25  5622.75  5627.75  460
...
2024-09-19T20:59:00.000000000Z  2024-09-19  ESZ4  183748  5773.5   5774.5   5773.25  5774.5   736
5511 row(s)
exit 0 (1.8 s)
```

- The segments match `mt data tick active --product ES --rule v.0 --start
  2024-09-16 --end 2024-09-20` exactly:
  - `2024-09-16 → 2024-09-17  ESU4 (118)`
  - `2024-09-18 → 2024-09-19  ESZ4 (183748)`
- No ESU4 bar is at or after 09-18's open (2024-09-17T22:00Z), and no ESZ4
  bar is before it. Both counts were checked with `awk` and are 0.
- The first ESZ4 bar is `2024-09-17T22:00:00Z`, which is session 09-18's
  open.

## Step 2: ticks for one contract, checked against the ledger

```
$ mt data tick get ticks --contract ESZ4 --start 2024-09-18 --end 2024-09-19 --csv > esz4.csv
exit 0 (6.7 s)
$ wc -l < esz4.csv
573848
$ head -2 esz4.csv
ts_event,session_date,raw_symbol,instrument_id,price,size,side,bid_px,ask_px,bid_sz,ask_sz,bid_ct,ask_ct,sequence
2024-09-17T22:00:00.000000000Z,2024-09-18,ESZ4,183748,5701,6,N,,,,,,,32676281
```

- stderr carries the header and the segment, and stdout is CSV only:
  ```
  ESZ4 (183748)  ticks  2024-09-18 → 2024-09-19 (end exclusive)
    2024-09-18 → 2024-09-18  ESZ4 (183748)
  ```
- The rows match the ledger exactly. 573,848 lines minus the header is
  573,847 rows.
  - That equals ESZ4's ledger `record_count` for session 09-18 under
    current units, which is also 573,847.
  - That figure comes from a read-only query of `tick_ingest_ledger` joined
    to `tick_archive_unit` on `COVERAGE_PREDICATE`. ESU4 has 148,080 rows
    that session.
- **Correction to the design.** `coverage`'s `ledger` column (748,819)
  counts every instrument in the session, not just ESZ4, so it is not the
  number to compare. The per-instrument ledger figure is the right one.
- These are trades-tier rows, so the BBO columns are empty. Prices are exact
  (`5701`, `5650.25`).

## Step 3: session bars and the unresolved head of the tbbo range

```
$ mt data tick get 1d --product ES --start 2024-11-04 --end 2024-11-08 --json | jq -c '{unresolved, n: .row_count, exit_code}'
{"unresolved":[{"session_date":"2024-11-04","reason":"volume inputs: session 2024-10-31 is missing"},{"session_date":"2024-11-05","reason":"volume inputs: session 2024-11-01 is missing"}],"n":2,"exit_code":3}
exit 3
```

The text form shows one bar for each resolved session, stamped at the
session open (23:00Z after the DST change):

```
2024-11-05T23:00:00.000000000Z  2024-11-06  ESZ4  183748  5820.25  5967  5814.75  5966.25  1900812
2024-11-06T23:00:00.000000000Z  2024-11-07  ESZ4  183748  5963.5   6013  5951     6006.5   1209868
2 row(s)
unresolved 2024-11-04  volume inputs: session 2024-10-31 is missing
unresolved 2024-11-05  volume inputs: session 2024-11-01 is missing
```

The first run of `--json` crashed: `sum(size)` is Postgres `numeric`, which
JSON cannot encode. Fixed in 3709f34 with a `bigint` cast. A read test now
asserts the type, and a CLI test checks bars in JSON.

## Step 4: an unknown contract

```
$ mt data tick get ticks --contract ESM4; echo $?
Error: no definition held for contract ESM4
1
```

- **Correction to the design.** The walkthrough used `ESZ9`, but ESZ9 *is*
  defined: instrument 42004134, the December 2029 contract, which CME lists
  years ahead. With no range and nothing held, it is an empty read, which
  exits 0 and prints the header line:
  ```
  ESZ9 (42004134)  ticks  nothing held
  ```
- ESM4 expired in June 2024, before any held definition file, so it has no
  definition. The defined ES outrights are ESU4, ESZ4, and the H/M/U/Z
  contracts from 2025 to 2029, plus ESH0.
- The first run of the ESZ9 case printed `None → None`, which 3709f34
  fixed.
- The ambiguous case (one raw symbol naming two contracts a decade apart)
  cannot be shown on today's data. It is covered by
  `test_tick_get_plan.py::test_ambiguous_contract_lists_every_candidate`
  and `test_tick_get_cli.py::test_an_ambiguous_contract_exits_one_listing_both`.
