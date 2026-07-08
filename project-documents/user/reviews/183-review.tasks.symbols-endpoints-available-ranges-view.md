---
docType: review
layer: project
reviewType: tasks
slice: symbols-endpoints-available-ranges-view
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/183-tasks.symbols-endpoints-available-ranges-view.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260513
dateUpdated: 20260513
findings:
  - id: F001
    severity: concern
    category: test-accuracy
    summary: "404 error response key mismatch between unit test and HTTP exception specification"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md:T7
  - id: F002
    severity: pass
    category: coverage
    summary: "All 8 success criteria are covered by tasks"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md
  - id: F003
    severity: pass
    category: scope-management
    summary: "No scope creep detected"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md
  - id: F004
    severity: pass
    category: sequencing
    summary: "Task sequencing is correct"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md
  - id: F005
    severity: pass
    category: commit-structure
    summary: "Single commit checkpoint is appropriate"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md
  - id: F006
    severity: pass
    category: nfr
    summary: "No load test requirement triggered"
    location: unverified
  - id: F007
    severity: pass
    category: verification
    summary: "Integration verification task is comprehensive"
    location: 183-tasks.symbols-endpoints-available-ranges-view.md:T11
---

# Review: tasks — slice 183

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] 404 error response key mismatch between unit test and HTTP exception specification

T7 specifies that `test_symbol_detail_not_found` should assert the response JSON has an `"error"` key (`"error": "..."}`), but T5 specifies the implementation uses `HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")`. Raising `HTTPException` with a `detail` field will produce `{"detail": "..."}` in the JSON response, not `{"error": "..."}`.

The slice design states this reuses the "existing `_custom_http_exception_handler` registered in `app.py`" — so the format depends entirely on what that handler does. If the handler transforms `detail` into `{"error": ...}`, the test will pass. If the handler returns `{"detail": ...}` directly (which is FastAPI's default), T7 will fail even though the implementation is correct per T5.

**Resolution needed:** Either T5 should specify a response format that explicitly uses `"error"` key (not `HTTPException` with `detail`), or T7 should expect `"detail"` key to match the stated implementation, or confirm that the existing handler transforms `detail → error`.

### [PASS] All 8 success criteria are covered by tasks

The mapping is complete:
- SC1 (count matches array length) → T4, T6
- SC2 (prefix search) → T4, T6  
- SC3 (detail returns "1d" range) → T5, T7
- SC4 (omits granularities with no data) → T5
- SC5 (404 on unknown symbol) → T5, T7, T11
- SC6 (health regression) → T11
- SC7 (unit tests without live DB) → T3, T6, T7
- SC8 (static analysis clean) → T10

### [PASS] No scope creep detected

All tasks trace to slice requirements. The exclusion of `get_symbols_db` (the slice design mentioned adding this dependency) is consistent with the stated approach of using the existing `get_db` pool directly. No tasks implement pagination, materialized views, or other excluded features.

### [PASS] Task sequencing is correct

Dependencies respected: T2 (models) → T3 (model tests) → T4/T5 (routes) → T6/T7 (route tests) → T8 (app integration) → T9 (regression suite) → T10 (static analysis) → T11 (integration) → T12 (commit). The test-after-implementation pattern holds throughout.

### [PASS] Single commit checkpoint is appropriate

One commit at the end after all verification tasks is appropriate for a slice with tightly coupled features (list and detail endpoints share models, router, and test file). The commit encompasses all four modified/created files as specified in T12.

### [PASS] No load test requirement triggered

The slice design contains no NFRs related to load/performance. No load test tasks are required, and none are present.

### [PASS] Integration verification task is comprehensive

T11 verifies all key behaviors end-to-end: list endpoint, search filter, detail with ranges, 404 handling, health regression, and OpenAPI schema inclusion. All six checks align with the slice's success criteria.
