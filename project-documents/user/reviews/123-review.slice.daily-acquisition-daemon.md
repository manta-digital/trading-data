---
docType: review
layer: project
reviewType: slice
slice: daily-acquisition-daemon
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/123-slice.daily-acquisition-daemon.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260412
dateUpdated: 20260412
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Architecture goal alignment"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Correct dependency direction and integration"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Appropriate scope boundaries"
  - id: F004
    severity: pass
    category: uncategorized
    summary: "`marketservice.py` removal is appropriate scope"
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Graceful shutdown design is sound"
  - id: F006
    severity: pass
    category: uncategorized
    summary: "`marketservice.py` removal boundary"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Testing strategy is well-designed"
---

# Review: slice — slice 123

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Architecture goal alignment

The slice implements the "Daily Acquisition Daemon" described in the Envisioned State section of the architecture. Key alignments:
- Caught-up definition matches exactly: "every active (non-delisted) symbol has data within 2 trading days of today"
- Continuous cycling through equity symbol universe with progress persistence via `acquisition_state`
- CLI (`mt data daily status`) queries daemon state through the heartbeat table
- Resumable by design: restart skips fresh symbols and retries failed ones per configured backoff

### [PASS] Correct dependency direction and integration

The daemon injects `DailyAcquisitionOrchestrator` (slice 122) and calls `update_symbol()`, never calling `run_acquisition_unit` directly. This preserves the "single code path principle" — the CLI and daemon share the same orchestrator code path, as specified in the architecture's "CLI is the baseline, daemon is the target" principle. The `SymbolSource` protocol provides a clean seam for testing without coupling to `MarketDB`.

### [PASS] Appropriate scope boundaries

Out-of-scope items are correctly identified:
- Minute acquisition (slice 125's concern)
- Daemon framework extraction (explicitly deferred per architecture's "Anticipated Slices")
- Rate limit coordination between daily and minute daemons (acknowledged as slice 125's problem)
- Trading calendar integration (Initiative 140's concern)

The architecture explicitly states: *"Not built as part of the initial equities slices... accept some duplication between the two daemons rather than build a framework speculatively."* This slice follows that guidance.

### [PASS] `marketservice.py` removal is appropriate scope

The architecture states: *"`marketservice.py` is legacy. CamelCase naming... This is the daily acquisition orchestrator and needs replacement."* The slice includes migration of `daily_symbols` away from `MarketService` and file deletion. This is the natural cleanup point after slice 122 stopped using it for update commands, completing the daily pipeline self-containment.

### [PASS] Graceful shutdown design is sound

Signal handling sets `_shutdown_requested`, the main loop checks before each symbol, and in-flight `update_symbol()` completes naturally. This bounds shutdown latency to one provider request (~2 seconds) and aligns with the architecture's "finish its current fetch, persist state, and exit" requirement.

### [PASS] `marketservice.py` removal boundary

The `marketservice.py` removal is correctly scoped: slice 122 already stopped the update CLI commands from using it; this slice migrates `daily_symbols` (the sole remaining caller) and deletes the file. No other callers exist. The migration is a "small, well-bounded change" as the slice notes.

### [PASS] Testing strategy is well-designed

The daemon loop is testable as a pure function of (current state, provider results) → (new state, writes), using fakes for all dependencies. Integration tests run against real databases when environment variables are set. This matches the architecture's testing guidance: *"unit tests mock the provider HTTP layer... integration tests run against a real database."*
