---
docType: notes
slice: active-contract-and-roll-methods
project: trading-data
dateCreated: 20261012
dateUpdated: 20261012
---

# Roll fixture record (slice 229, task 5.2)

Recorded 2026-10-12 on manta9000, from `trading_tick` (read-only), the
production calendar, and Databento's free `symbology.resolve`.

```bash
uv run python scripts/record_229_roll_fixture.py
```

## Files

All under `test/fixtures/tick/roll/`. Each carries `source` and `read_at`.

| File | Rows | What |
|---|---|---|
| `es_definitions.json` | 23 | ES outright definitions; `definitions_as_of` 2024-12-31 |
| `es_volumes.json` | 2,629 | outright ledger rows per (session, instrument, unit), current units, sessions 2024-08-28 → 2025-03-31; plus the verdict for each of the 153 calendar sessions |
| `es_calendar.json` | 153 | CME_EQUITY sessions 2024-08-28 → 2025-03-31 |
| `es_databento.json` | 4 symbols | intervals for `ES.c.0`, `ES.c.1`, `ES.v.0`, `ES.v.1`, 2024-08-30 → 2025-01-03 (end exclusive) |

Verdicts: 61 sessions `complete` (2024-09-02 → 09-27 and 2024-11-04 →
12-31), 25 `missing`, 67 `edge_unknown`. Inside the held span, 08-30, 09-30
→ 11-01 and 2025-01-02 are not complete (the trades job ends 09-29; the tbbo
job starts 11-01, so session 11-01, which opens 10-31, is not whole).

## Databento switch dates seen

Interval starts are provider dates; a Sunday start means the next session.

| Symbol | Intervals |
|---|---|
| `ES.c.0` | ESU4 → 09-22 ESZ4 → 12-22 ESH5 |
| `ES.c.1` | ESZ4 → 09-22 ESH5 → 12-22 ESM5 |
| `ES.v.0` | ESU4 → 09-18 ESZ4 → 12-18 ESH5 |
| `ES.v.1` | ESZ4 → 09-18 ESU4 → 09-22 ESH5 → 12-18 ESZ4 → 12-22 ESM5 |

As expected: `ES.v.0` switches 2024-09-18 and 2024-12-18; `ES.c.0` switches
2024-09-22 (a Sunday, so session 09-23).
