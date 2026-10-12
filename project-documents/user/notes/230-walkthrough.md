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

## Step 5: debug reads (task 6.5)

```
$ mt data tick debug requests
requests: 6 row(s)
request_id  schema      symbols  range                    is_adopted  provider_job_id  estimated / actual cost  records
1           trades      ES.FUT   2024-08-30 → 2024-09-30  True        GLBX-20240930-…  12.5785…                 10049172
2           tbbo        ES.FUT   2024-11-01 → 2025-01-01  True        GLBX-20250123-…  36.8045…                 17642240
3–6         definition  ES.FUT   (the four definition jobs, $0.00005–$0.0014 each)
exit 0
$ mt data tick debug units --state verified --json | jq '.rows | length'
0
$ mt data tick debug definitions --product ES --as-of 2024-09-18
definitions: 21 row(s)
118       ESU4  F  2022-06-17T13:30:00.000000000Z  2024-09-20T13:30:00.000000000Z  0.25  -  USD  131
183748    ESZ4  F  2021-06-04T21:30:00.000000000Z  2024-12-20T14:30:00.000000000Z  0.25  -  USD  131
5002      ESH5  F  2022-12-16T14:30:00.000000000Z  2025-03-21T13:30:00.000000000Z  0.25  -  USD  131
... (ESM5 through ESU9)
exit 0
```

(The tables above are condensed. The real ones are Rich tables with every
TD6 column.)

- **Requests.** Both adopted jobs appear with their costs: trades
  $12.58 and tbbo $36.80. The four definition jobs appear too.
- **Units.** No unit is in the `verified` state, which matches
  `mt data tick status --product ES`'s `awaiting ingest 0`.
- **Definitions.** ESU4, ESZ4, ESH5 and the later outrights are listed with
  an exact tick size of `0.25` and no spreads.
  - `contract_multiplier` is null on these rows, so it shows as `-`.
  - All 21 rows are valid at session 09-18's open.

## Step 6: accounting and overview, healthy (task 7.9)

```
$ time mt data accounting
...
minute universe: 11,733,755/13,905,612 symbol-sessions covered (84.4%); 464,737 untraded; 1,707,120 fillable (...)
futures tick universe:
  ES  61/64 sessions complete (95.3%); 2 missing; 1 edge unknown  · 27,691,412 records
exit 0    1:29.26 total
```

- **Timing.** The tick section's own read, timed in isolation through
  `tick_composed.read_line`, took **0.05 s**. That is far inside
  `TICK_COMPOSED_STATEMENT_TIMEOUT_SECONDS` (10). Almost all of the
  1:29 total is the minute scan against the production database.
- **The counts match status.** `mt data tick status --product ES` shows
  `complete 61 … missing 2 … edge unknown 1`, the same 64 sessions in
  scope.

```
$ mt data overview
SOURCES          newest                      health (15:50 UTC): healthy
minute bars      2026-10-09 18:00 UTC (2 d ago)
daily bars       2026-10-08 18:00 UTC (3 d ago)
kalshi candles   2026-10-12 15:21 UTC (47 min ago)
kalshi trades    2026-10-12 15:19 UTC (49 min ago)
futures ticks    2024-12-31 21:59 UTC (650 d ago)
exit 0
```

**Correction to the design.** The design expected "around 2025-01-01 21:59
UTC". The tbbo range ends on 2025-01-01 *exclusive* (New Year's Day, no
session), so the last held session is dated 2024-12-31 and closes at
22:00 UTC. The newest event is at 21:59, just before that close.

## Step 7: the degraded lines

```
$ MT_TICK_DB_URL= mt data overview
futures ticks    not configured (MT_TICK_DB_URL unset)
exit 0
$ MT_TICK_DB_URL=postgresql://nobody@127.0.0.1:1/x mt data overview
futures ticks    unreachable: connection failed: connection to server at "127.0.0.1", port 1 failed: Connection refused
exit 0
$ MT_TICK_DB_URL=postgresql://nobody@127.0.0.1:1/x mt data accounting
... (the minute section, unchanged)
futures tick universe:  unreachable: connection failed: connection to server at "127.0.0.1", port 1 failed: Connection refused
exit 0
```

The other source lines and the minute section print as usual. The
`migrations pending` line is proven on the test cluster by
`test_tick_accounting.py` and `test_tick_overview.py` (parametrized
`migrations_pending`).

## Step 8: production

`mt-run` runs the installed release (`/opt/manta-trading/.venv/bin/mt`),
which predates this slice, so `mt-run data overview` does not show the
tick line yet:

```
$ mt-run data overview      # 2026-10-12, release before 230
SOURCES ... minute bars / daily bars / kalshi candles / kalshi trades (no futures ticks line)
exit 0
$ grep -c '^MT_TICK_DB_URL' /etc/manta-trading.env
0
```

The production line is proven by two things together:

- Step 7's unset case prints the exact text
  `futures ticks    not configured (MT_TICK_DB_URL unset)` and exits 0.
- The key is absent from production's env file.

After 230 is deployed, `mt-run data overview` prints that line. Run it as
part of the deploy check. Slice 234 adds the key, which turns the line into
a real timestamp.

## Functional requirement coverage (task 8.5)

Each FR maps to a passing test, a walkthrough step, or both. Test paths are
under `test/`.

| FR | Proven by |
|---|---|
| 1. Contract ticks in key order, count = ledger | `integration/data/test_tick_get_cli.py::test_a_clean_contract_read_exits_zero_with_every_tick`; `test_tick_reads.py::test_ticks_in_key_order_inside_the_window`; walkthrough step 2 (573,847 = ESZ4 ledger) |
| 2. Product 1m follows `active`, no mixed bar, volume = Σ size | `test_tick_get_plan.py::test_product_across_the_roll_matches_active`; `test_tick_get_cli.py::test_product_bars_follow_the_schedule`; `test_tick_reads.py::test_1m_volume_sums_sizes_and_skips_empty_buckets`; walkthrough step 1 |
| 3. A bar never spans a session boundary | `test_tick_reads.py::test_4h_bucket_across_the_break_is_two_bars` |
| 4. `1d` OHLCV, ties by `(sequence, sequence_ordinal)` | `test_tick_reads.py::test_session_bar_matches_the_ticks_with_ties_by_key`; walkthrough step 3 |
| 5. Unresolved head listed, never filled, exit 3 | `test_tick_get_plan.py::test_first_held_sessions_are_unresolved_never_filled`; `test_tick_get_cli.py::test_unresolved_sessions_print_and_exit_three`; walkthrough step 3 |
| 6. Incomplete session served, listed, exit 3 | `test_tick_get_plan.py::test_contract_lists_an_incomplete_session_and_still_plans_it`, `…product_lists_an_incomplete…`; `test_tick_get_cli.py::test_an_incomplete_session_is_read_listed_and_exits_three` |
| 7. Ambiguous / unknown contract exit 1 | `test_tick_get_plan.py::test_ambiguous_contract_lists_every_candidate`, `::test_unknown_contract_names_the_symbol`; `test_tick_get_cli.py::test_an_ambiguous_contract_exits_one_listing_both`; walkthrough step 4 |
| 8. No range: held sessions or the cap refusal; contract held sessions | `test_tick_get_plan.py::test_missing_ends_default_to_the_held_sessions`, `::test_a_range_over_the_cap_refuses`, `::test_contract_without_a_range_covers_its_held_sessions` |
| 9. Exact prices; undefined BBO null/empty | `unit/data/tick/test_tick_format.py`; `unit/cli/commands/test_tick_get_writers.py::test_json_has_td5_keys_in_order_and_exact_prices`, `::test_csv_stdout_is_header_and_rows_only` |
| 10. CSV stdout is header + rows only | `test_tick_get_writers.py::test_csv_stdout_is_header_and_rows_only`; `test_tick_get_cli.py::test_an_incomplete_session_is_read_listed_and_exits_three`; walkthrough step 2 |
| 10a. Partial output per format exit 4; closed pipe exit 0, clean stderr, no open transaction | `test_tick_get_cli.py::test_partial_json_stays_valid_and_exits_four`, `::test_partial_csv_keeps_stdout_clean_and_exits_four`, `::test_partial_text_ends_with_the_partial_footer`, `::test_a_reader_that_closes_the_pipe_gets_exit_zero` |
| 11. Debug filters, enum rejection, spreads default | `integration/data/test_tick_debug_reads.py`; `test_tick_debug_cli.py`; walkthrough step 5 |
| 12. Accounting figures = status buckets; JSON `tick` | `integration/data/test_tick_accounting.py::test_the_line_equals_status_buckets`, `::test_text_and_json_with_a_reachable_tick_database`; walkthrough step 6 |
| 13. Overview newest event; `/api/v1/overview` unchanged | `integration/data/test_tick_overview.py::test_the_line_shows_the_newest_ingested_event`; `unit/api_server/test_operations_models.py` (unedited); `unit/cli/commands/test_overview_tick.py::test_the_api_record_never_carries_the_tick_state`, `::test_gather_appends_the_tick_line_and_gather_db_facts_does_not`; walkthrough step 6 |
| 14. Unset / unreachable / pending lines, exit unchanged | `test_tick_accounting.py::test_each_degraded_state_is_a_line_and_keeps_the_exit`; `test_tick_overview.py::test_each_degraded_state_keeps_the_screen_and_the_exit`; `unit/cli/test_tick_composed.py`; walkthrough step 7 |
