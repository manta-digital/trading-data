---
docType: review
layer: project
reviewType: slice
slice: minute-provider-fixes-and-orchestrator-hardening
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/124-slice.minute-provider-fixes-and-orchestrator-hardening.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260413
dateUpdated: 20260413
findings:
  - id: F001
    severity: pass
    category: architectural-pattern
    summary: "Per-month-chunk write-and-checkpoint pattern correctly implements architectural principle"
    location: slice design, Data Flow section
  - id: F002
    severity: pass
    category: bug-fix
    summary: "RateLimiter fix addresses known architectural issue"
    location: Technical Decisions / Fix 2
  - id: F003
    severity: pass
    category: interface-design
    summary: "Provider interface extension follows architecture pattern"
    location: New: `_MinuteChunkProviderAdapter`
  - id: F004
    severity: pass
    category: correctness
    summary: "AlphaVantage partial-month behavior handled correctly"
    location: State Management section
  - id: F005
    severity: pass
    category: package-structure
    summary: "Service-per-concern package structure respects architectural principle"
    location: Component Structure diagram
  - id: F006
    severity: pass
    category: state-management
    summary: "State management aligns with architecture schema"
    location: State Management section, Integration Points
  - id: F007
    severity: pass
    category: cli-design
    summary: "CLI-and-daemon-share-code-path principle respected"
    location: CLI Additions section, Integration Points
  - id: F008
    severity: pass
    category: scope-management
    summary: "Out-of-scope boundaries correctly respected"
    location: Overview section
  - id: F009
    severity: pass
    category: dependency-management
    summary: "Dependencies and interfaces correctly specified"
    location: Dependencies section, Interfaces Required section
  - id: F010
    severity: pass
    category: risk-management
    summary: "Risk mitigation strategy aligns with architecture's operational concerns"
    location: Overview section
---

# Review: slice — slice 124

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Per-month-chunk write-and-checkpoint pattern correctly implements architectural principle

The slice establishes `MinuteAcquisitionOrchestrator` with per-month-chunk checkpointing, directly satisfying the architecture's "Progress is persisted, not inferred" principle. The current `HistoricalMinuteService` "gathers all months then writes once; this must change to per-chunk write-and-checkpoint" — exactly what the slice implements.

### [PASS] RateLimiter fix addresses known architectural issue

The `RateLimiter.__aenter__` fix (release lock before sleeping, re-acquire and re-check) resolves the explicit bug noted in the architecture's "What's broken or missing" section: "Rate limiting (`RateLimiter`) uses async token bucket, precise enforcement" with the known issue that it "holds lock during sleep." The slice correctly identifies this as a correctness fix for concurrent symbol fetches in the minute daemon (slice 125).

### [PASS] Provider interface extension follows architecture pattern

The slice creates `_MinuteChunkProviderAdapter` to bridge `IMinuteDataProvider` into the slice 121 `ChunkProvider` protocol, following the same pattern as the daily adapter. This extends the provider interface layer that the architecture describes as a design goal without requiring a full rewrite.

### [PASS] AlphaVantage partial-month behavior handled correctly

The slice correctly addresses the architecture's explicit warning that "AlphaVantage minute data volume per request is limited and variable" — with `month` specified and `outputsize=full`, the API returns ~10 trading days, not a full calendar month. The slice's state management correctly: (a) uses actual data extent for the watermark rather than the requested month boundary, (b) handles partial months by re-requesting the same month until covered, and (c) advances only when the month is fully covered.

### [PASS] Service-per-concern package structure respects architectural principle

The new `data/acquisition/minute/` package sits alongside `data/acquisition/daily/` at the same level under `data/acquisition/`, following the architecture's "Service-per-concern, not service-per-provider" principle. The daily and minute services run as independent daemons with operational independence.

### [PASS] State management aligns with architecture schema

The slice correctly uses the `acquisition_state` table from slice 121 with primary key `(symbol, 'minute', 'alphavantage')`, `last_success_ts` as watermark reflecting actual data extent, and UPSERT semantics. This matches the architecture's state schema description exactly.

### [PASS] CLI-and-daemon-share-code-path principle respected

The slice wires `mt data minute update SYMBOL` through the new `MinuteAcquisitionOrchestrator` path, and correctly notes that the daemon wrapper is deferred to slice 125 (analogous to slice 123 wrapping slice 122). This matches the architecture's "CLI is the baseline, daemon is the target" principle — one code path, no state divergence.

### [PASS] Out-of-scope boundaries correctly respected

The slice correctly defers:
- Minute acquisition daemon → slice 125
- Concurrent symbol fetching → slice 125
- Shared rate limit coordination between daily and minute daemons → slice 125 concern
- Trading-calendar-aware gap detection → Initiative 140
- Quality metrics beyond basic OHLCV validation → Initiative 140

These boundaries are consistent with the architecture's scope definition.

### [PASS] Dependencies and interfaces correctly specified

The slice correctly lists dependencies on completed slices 100 (storage), 121 (orchestrator core), and 900 (CLI framework). The required interfaces — `run_acquisition_unit`, `TimescaleMinuteDataDB.write_minute_data_bulk`, `AlphaVantageMinuteProvider.fetch_minute_data`, `DataProcessor.process`, and `AcquisitionStateRepository` — are all provided by the referenced slices with no hidden dependencies.

### [PASS] Risk mitigation strategy aligns with architecture's operational concerns

The slice explicitly identifies the ~45M rows of irreplaceable data as the highest-risk aspect and mitigates through: (a) using the proven slice 121 orchestrator core, (b) restricting all changes to the test DB until PM confirms backup, and (c) leaving the storage layer (`TimescaleMinuteDataDB`) and processor (`DataProcessor`) unchanged. This aligns with the architecture's concern that "no minute pipeline changes should be deployed to production until backup is confirmed."
