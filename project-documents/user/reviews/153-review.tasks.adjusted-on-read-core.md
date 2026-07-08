---
docType: review
layer: project
reviewType: tasks
slice: adjusted-on-read-core
project: squadron
verdict: PASS
sourceDocument: project-documents/user/tasks/153-tasks.adjusted-on-read-core.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260505
dateUpdated: 20260505
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Granularity enum completeness"
    location: 153-tasks.adjusted-on-read-core.md#T02
  - id: F002
    severity: pass
    category: uncategorized
    summary: "GRANULARITY_SOURCE single-source mapping"
    location: 153-tasks.adjusted-on-read-core.md#T02
  - id: F003
    severity: pass
    category: uncategorized
    summary: "adjusted() function — all behavioral requirements traced"
    location: 153-tasks.adjusted-on-read-core.md#T04,T05
  - id: F004
    severity: pass
    category: uncategorized
    summary: "80-line size constraint enforced"
    location: 153-tasks.adjusted-on-read-core.md#T04
  - id: F005
    severity: pass
    category: uncategorized
    summary: "TimescaleDailyDataDB routing requirements fully tested"
    location: 153-tasks.adjusted-on-read-core.md#T07,T08
  - id: F006
    severity: pass
    category: uncategorized
    summary: "TimescaleMinuteDataDB updated correctly"
    location: 153-tasks.adjusted-on-read-core.md#T09,T10
  - id: F007
    severity: pass
    category: uncategorized
    summary: "AGGREGATION_VIEWS grep audit precedes changes"
    location: 153-tasks.adjusted-on-read-core.md#T09
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Test-with pattern correctly applied"
    location: 153-tasks.adjusted-on-read-core.md
  - id: F009
    severity: pass
    category: uncategorized
    summary: "pyright checks at every commit and at T11 final"
    location: 153-tasks.adjusted-on-read-core.md#T06,T09,T11
  - id: F010
    severity: note
    category: uncategorized
    summary: "No NFR-driven load test task required"
    location: unverified
  - id: F011
    severity: pass
    category: uncategorized
    summary: "No scope creep — out-of-scope items from design absent from tasks"
    location: 153-tasks.adjusted-on-read-core.md
  - id: F012
    severity: pass
    category: uncategorized
    summary: "All four technical requirements covered"
    location: 153-tasks.adjusted-on-read-core.md
---

# Review: tasks — slice 153

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Granularity enum completeness

`T02` requires exactly 9 members (`M1` through `Q1`) with explicit string values, and `T03` tests that all 9 are present, all 9 expected values exist, and no duplicates exist. This directly maps to the slice design's "All 9 `Granularity` tokens are importable" and "No duplicate string values" success criteria.

---

### [PASS] GRANULARITY_SOURCE single-source mapping

`T02` requires `GRANULARITY_SOURCE: dict[Granularity, str]` with 9 entries encoded from the token table, and explicitly prohibits any granularity string appearing outside these two definitions. This fully covers the "no token maps to a hardcoded string outside this dict" success criterion.

---

### [PASS] adjusted() function — all behavioral requirements traced

The six behavioral requirements for `adjusted()` are each implemented and tested:
- OHLC scaled by per-date k-factor → `T04` sub-task + `T05` 4-for-1 split unit test
- No CAs → returns unchanged → `T04` ("Returns df unchanged if snapshot has no splits and no dividends") + `T05` unit test
- Missing `prev_close` → `KeyError` propagated → `T04` ("KeyError for missing prev_close propagates") + `T05` unit test ("KeyError raised, not swallowed")
- `ca_snapshot` skips DB reads → `T04` + `T05` ("ca_snapshot kwarg provided → _load_snapshot is NOT called")
- `df.empty` short-circuit → `T04` + `T05` unit test
- Integration test with AAPL split → `T05` integration test

---

### [PASS] 80-line size constraint enforced

`T04` explicitly states: "File stays ≤ 80 non-blank lines". This maps to the technical requirement that `data/adjustment.py` ≤ 80 lines (excluding blanks) and is a pure function.

---

### [PASS] TimescaleDailyDataDB routing requirements fully tested

- D1 → `daily_ohlcv`, W1 → `daily_weekly_ohlcv`, MO1 → `daily_monthly_ohlcv`, Q1 → `daily_quarterly_ohlcv` → `T08` unit test on `GRANULARITY_SOURCE` routing
- Minute tokens → `ValueError` → `T08` unit test covering all 5 minute-grain tokens
- Adjusted vs raw close continuity across AAPL split → `T08` integration tests

---

### [PASS] TimescaleMinuteDataDB updated correctly

`T09` covers all three sub-tasks from the slice design:
- `AGGREGATION_VIEWS` key migration: `"5min"` → `"5m"`, `"15min"` → `"15m"`, `"1hour"` → `"1h"`, `"4hour"` → `"4h"`
- `aggregation` parameter type annotation updated to `str | Granularity | None`
- `adjusted: bool = True` kwarg added; when `True` and DataFrame not empty, calls `adjusted_fn(df, symbol, conn)`

`T10` tests the kwarg behavior: `adjusted=False` bypasses `adjusted_fn` (mocked assert zero calls), and integration test checks the AAPL 4-for-1 split adjustment.

---

### [PASS] AGGREGATION_VIEWS grep audit precedes changes

`T09` explicitly requires running the grep audit before making changes: `grep -rn '"5min"\|"15min"\|"1hour"\|"4hour"' src/ tests/` and listing every file that uses old keys. This matches the slice design's "Implementation Notes → Development Order" and prevents silent breakage of internal callers.

---

### [PASS] Test-with pattern correctly applied

Every implementation task is immediately followed by its test task: T02→T03, T04→T05, T07→T08, T09→T10. Commit checkpoint T06 falls between the first functional block (enum + adjusted) and the second (daily DB), providing a logical break point.

---

### [PASS] pyright checks at every commit and at T11 final

`T06`: pyright clean before commit. `T09`: pyright strict clean before proceeding to tests. `T11`: pyright clean across all new and modified files before final commit. This satisfies the "strict mode" requirement on all files.

---

### [NOTE] No NFR-driven load test task required

The slice design specifies no non-functional performance requirements; `effort: 2` in the header indicates a small, well-defined feature. There are no load test NFRs stated in the parent slice design, so no load test task is required by the reviewer rule. No CI wiring task for load testing is needed.

---

### [PASS] No scope creep — out-of-scope items from design absent from tasks

The slice design explicitly excludes: CLI commands, daemon changes, ingest path changes, new schema migrations. None of these appear in the task breakdown. `TimescaleMinuteDataDB` update scope is limited to `AGGREGATION_VIEWS` key migration and the `adjusted` kwarg — no behavioral changes to the underlying minute read path beyond calling `adjusted_fn`.

---

### [PASS] All four technical requirements covered

1. `uv run pyright` zero errors → T06, T09, T11 ✓
2. `uv run pytest test/` all pass → T06, T11 ✓
3. `adjustment.py` ≤ 80 lines pure function → T04 ✓
4. `timescale_daily_db.py` mirrors `timescale_minute_db.py` structure → T07 ("mirroring `TimescaleMinuteDataDB` structure") ✓
