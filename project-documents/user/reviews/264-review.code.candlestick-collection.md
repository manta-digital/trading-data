---
docType: review
layer: project
reviewType: code
slice: candlestick-collection
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/264-slice.candlestick-collection.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260828
dateUpdated: 20260828
reviewedSha: d125d4f3d01d5bda06865a06a7b3caf641f6dc2f
findings:
  - id: F001
    severity: note
    category: structure
    summary: "File slightly exceeds ~300-line guideline"
    location: "src/manta_trading/data/kalshi/candle_repository.py"
  - id: F002
    severity: note
    category: consistency
    summary: "assert vs raise inconsistency for query invariant"
    location: "src/manta_trading/data/kalshi/status.py:293"
---

# Review: code — slice 264

**Verdict:** PASS
**Model:** claude-sonnet-5

## Findings

### [NOTE] File slightly exceeds ~300-line guideline

`candle_repository.py` is 303 lines, marginally over the project's ~300-line file guideline. Not a real problem — the file is clearly sectioned (pending sets / counts / writes) and splitting further would add indirection for little benefit.

### [NOTE] assert vs raise inconsistency for query invariant

`candle_plan.py`'s `_check_caps` deliberately raises `AssertionError` explicitly (not via a bare `assert`) with a comment explaining that `-O` must not strip the guard on the planner's cap invariant. The new `read_candle_status` function in this same PR (`status.py:293`, mirroring the pre-existing pattern at `status.py:151`) uses a bare `assert row is not None # an aggregate query always returns one row` for a comparably load-bearing invariant. This is a pre-existing pattern in the file (not newly introduced), and risk is low since the query has no `GROUP BY`, but it sits in tension with the precedent the same author set a few files away in `candle_plan.py`.
