---
docType: notes
slice: active-contract-and-roll-methods
project: trading-data
dateCreated: 20261012
dateUpdated: 20261012
---

# Slice 229 walkthrough on `trading_tick` (task 8.1)

Run 2026-10-12 on manta9000, branch `229-slice.active-contract-and-roll-methods`,
against `trading_tick` and the production calendar. The `TradingCalendar
initialized` log line (stderr) is omitted below.

## Step 1: default answer and next roll (matches)

```
$ uv run mt data tick active --product ES
ES  v.0 (default)  ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31
    inputs: volume of session 2024-12-27; definitions as of 2024-12-31
    next roll: none through session 2025-01-03 (held volume);
               no later than session 2025-03-24 (expiry)
ES  c.0            ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31
    next roll: session 2025-03-24 → ESM5 (4916)
exit 0
```

## Step 2: September volume roll (matches)

```
$ uv run mt data tick active --product ES --rule v.0 --start 2024-09-09 --end 2024-09-28
ES  v.0 (default)  2024-09-09 → 2024-09-17  ESU4 (118)
                   2024-09-18 → 2024-09-27  ESZ4 (183748)
exit 0
```

## Step 3: calendar, and with an offset (matches)

```
$ uv run mt data tick active --product ES --rule c.0 --start 2024-09-09 --end 2024-09-28
ES  c.0            2024-09-09 → 2024-09-20  ESU4 (118)
                   2024-09-23 → 2024-09-27  ESZ4 (183748)
exit 0
$ uv run mt data tick active --product ES --rule c.0+5s --start 2024-09-09 --end 2024-09-28
ES  c.0+5s         2024-09-09 → 2024-09-13  ESU4 (118)
                   2024-09-16 → 2024-09-27  ESZ4 (183748)
exit 0
```

## Step 4: December roll, rank 1 (matches)

```
$ uv run mt data tick active --product ES --rule v.1 --start 2024-12-13 --end 2024-12-24
ES  v.1            2024-12-13 → 2024-12-17  ESH5 (5002)
                   2024-12-18 → 2024-12-20  ESZ4 (183748)
                   2024-12-23 → 2024-12-23  ESM5 (4916)
exit 0
```

## Step 5: unheld inputs reported (one correction)

```
$ uv run mt data tick active --product ES --start 2024-11-01 --end 2024-11-08; echo "exit $?"
ES  v.0 (default)  2024-11-06 → 2024-11-07  ESZ4 (183748)
    unresolved 2024-11-01: volume inputs: session 2024-10-30 is missing
    unresolved 2024-11-04: volume inputs: session 2024-10-31 is missing
    unresolved 2024-11-05: volume inputs: session 2024-11-01 is missing
ES  c.0            2024-11-01 → 2024-11-07  ESZ4 (183748)
exit 3
```

**Correction to the design's expected output:** session 2024-11-01 is
unresolved too. It is inside `[11-01, 11-08)` and its lag session, 10-30, is
not held (the trades job ends 09-29). The design listed only 11-04 and 11-05,
counting from the first session held whole (11-04). With no `--rule` both
status rules print; calendar resolves every session.

## Step 6: bad rules fail loudly (matches)

```
$ uv run mt data tick active --rule n.0; echo "exit $?"
Error: unknown roll method 'n'; known: c, v
exit 1
$ uv run mt data tick active --rule v.0+2s; echo "exit $?"
Error: roll rule 'v.0+2s': an offset suffix is allowed only on calendar (c)
exit 1
$ uv run mt data tick active --product ES --start 2010-01-04 --end 2026-01-02; echo "exit $?"
Error: 2010-01-04 → 2026-01-02 spans 1551 sessions, over TICK_ROLL_MAX_SESSIONS (1300); ask for a narrower range
exit 1
```

The cap counts the sessions the calendar holds (2020-01-02 onward), so it
refuses before the range is checked against the calendar's span.

## Step 7: status names the active contract (matches)

`uv run mt data tick status --product ES` prints the existing report, then the
two roll lines of step 1 under the contract list (indented two spaces), exit 0.
`--json | jq '.products[0].rolls'` gives two objects with `rule`,
`is_default`, `instrument_id`, `raw_symbol`, `expiration`, `basis_session`,
`basis_unit_ids`, `definition_unit_id`, `next_roll`, `known_through`,
`roll_no_later_than` and `error`, plus `product`, `session_date`,
`definitions_as_of`, `unresolved` and `reason`. For `v.0`: ESH5, basis
2024-12-27, `basis_unit_ids` [74, 75], `definition_unit_id` 131,
`known_through` 2025-01-03, `roll_no_later_than` 2025-03-24. For `c.0`:
`next_roll` ESM5 on 2025-03-24, `known_through` 2027-12-31 (the calendar's
last session).

## Step 8: tests (path corrected)

```
$ uv run python scripts/run_tests.py unit -- test/unit/data/tick -k roll -q
115 passed
```

The design's `tests/unit/data/tick` is `test/unit/data/tick` in this repo.

## TD10 timing

Full held-range schedule, `v.0`, 2024-08-30 → 2025-01-02 (87 sessions, 28
unresolved: the gap between the two held jobs and their first sessions):

| Measured | Runs |
|---|---|
| `build_schedule` (calendar read, snapshot, resolution) | 0.056 s, 0.049 s, 0.048 s |
| `mt data tick active` whole process | 0.97 s, 0.96 s, 0.96 s |

The schedule is well inside 1 s. The process time is almost all Python
start-up and imports, not the roll path.
