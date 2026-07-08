---
docType: review
layer: project
reviewType: tasks
slice: polish-gaps-endpoint
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/184-tasks.polish-gaps-endpoint.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260514
dateUpdated: 20260514
findings:
  - id: F001
    severity: note
    category: uncategorized
    summary: "T9 verification checks description but not title"
    location: 184-tasks.polish-gaps-endpoint.md:task-T9
  - id: F002
    severity: concern
    category: uncategorized
    summary: "Window filter integration verification is too weak"
    location: 184-tasks.polish-gaps-endpoint.md:task-T13
  - id: F003
    severity: note
    category: uncategorized
    summary: "T8 500 handler test implicitly checks sanitization but success criteria is thin"
    location: 184-tasks.polish-gaps-endpoint.md:task-T8
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Success criterion coverage complete"
    location: 184-tasks.polish-gaps-endpoint.md:task-T2
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Test-with pattern and sequencing are correct"
    location: 184-tasks.polish-gaps-endpoint.md
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Task granularity and commit distribution"
    location: 184-tasks.polish-gaps-endpoint.md
---

# Review: tasks — slice 184

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [NOTE] T9 verification checks description but not title

The success criterion #8 states "`GET /docs` shows all five endpoints with correct **title and description**." The verification step in T9 only checks `d['info']['description']` and never verifies `d['info']['title']`. Since `title` was already set in a prior slice and is not being changed by this slice, this is low risk — but it is inconsistent with the stated criterion.

---

### [CONCERN] Window filter integration verification is too weak

The T13 integration verification for `start`/`end` reads:
> `GET /api/v1/gaps/SPY?start=2024-01-01&end=2024-01-31` → 200, `count >= 0`.

Success criterion #3 states the endpoint "returns **only** gaps overlapping that window." The verification step only asserts `count >= 0`, which confirms the endpoint works but does **not** verify the window filter is applied correctly. A result where every gap in the DB is returned would satisfy this check. The unit test `test_gaps_window_filter` in T6 does check that `gap_start <` and `gap_end >` appear in the SQL, providing some coverage — but the integration step should at minimum assert that every returned `gap_start` falls within the window or that the count is consistent with the known DB state for that symbol/date range. Alternatively, the integration test could be split: one path asserting SQL shape (unit-test territory) and one asserting correctness with real data (integration territory).

---

### [NOTE] T8 500 handler test implicitly checks sanitization but success criteria is thin

The task says: "assert response is `500` with body `{\"error\": \"internal server error\"}`". Success criterion #6 requires that "no SQL text, no traceback, no DB info appears in the JSON body." The test assertion `response.json() == {"error": "internal server error"}` does implicitly reject extra keys, but it does not explicitly verify the absence of leaked fields. Since the test uses `RuntimeError("secret sql detail")`, a naive implementation that returned `{"error": "internal server error", "detail": str(exc)}` would fail this test — so the assertion is sufficient in practice. This is a NOTE rather than a CONCERN because the test would catch the common error. However, if "no traceback" were interpreted to mean "no `traceback` key", the current assertion does not cover that.

---

### [PASS] Success criterion coverage complete

All 10 success criteria from the slice design have corresponding tasks:
- SC1 (gaps/SPY returns count≥0) → T13 step 2
- SC2 (granularity=1m filters to "minute") → T6 `test_gaps_granularity_filter` + T13 step 3
- SC3 (window filter) → T6 `test_gaps_window_filter` + T13 step 4
- SC4 (FAKESYMBOL returns 200) → T6 `test_gaps_unknown_symbol_returns_200` + T13 step 5
- SC5 (invalid granularity returns 422) → T6 `test_gaps_invalid_granularity` + T13 step 6
- SC6 (500 sanitized) → T8 + T11
- SC7 (--workers 2) → T10 + T13 (implicit)
- SC8 (OpenAPI docs) → T9 + T13 step 7
- SC9 (unit tests no live DB) → T3, T6, T12
- SC10 (ruff/pyright zero errors) → T11

No success criterion is orphaned. No task lacks a parent criterion.

---

### [PASS] Test-with pattern and sequencing are correct

Implementation followed by test: T2→T3, T4→T5→T6, T7 (router registration, no test required — registration is a wiring step), T8→T8 (unit test in same task), T9→T9 (server check, no unit test), T10→T11. No circular dependencies. T11 static analysis precedes T12 regression, which precedes T13 integration, which precedes T14 commit.

---

### [PASS] Task granularity and commit distribution

Tasks are appropriately scoped: T2 creates two models, T4 defines constants and SQL, T5 implements the handler. None are oversized. Commit T14 covers code; T15 covers docs/CHANGELOG — both after integration. Commit distribution is adequate (not batched at end only).
