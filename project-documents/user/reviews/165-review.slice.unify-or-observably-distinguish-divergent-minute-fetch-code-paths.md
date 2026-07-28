---
docType: review
layer: project
reviewType: slice
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260727
dateUpdated: 20260727
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Failure modes for new DB-connection I/O path not individually enumerated"
    location: project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md#integration-points
  - id: F002
    severity: pass
    category: architectural-alignment
    summary: "Orthogonality of force_reset_terminal and seeding algorithm matches architecture"
    location: project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md#technical-decisions
  - id: F003
    severity: note
    category: scope-creep
    summary: "via log marker added to daily.py beyond the minute-only defect scope"
    location: project-documents/user/slices/165-slice.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md#component-structure
---

# Review: slice — slice 165

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [CONCERN] Failure modes for new DB-connection I/O path not individually enumerated

The slice introduces a new `pool.connection()` block in `run_minute_refetch` to call `build_minute_coverage_index`. While the slice states the fallback strategy ("If the coverage index build fails (`build_minute_coverage_index` returns `None`), `run_minute_refetch` falls back to the same legacy single-span behavior"), it references slice 162's handling rather than enumerating the specific failure modes in-place. The evaluation criteria require failure modes (hang, timeout, peer disconnect mid-send) to be explicitly enumerated with handling strategies. The `None`-return fallback covers the logical failure case, but the physical failure modes of the new DB connection (connection timeout during the ~3s grouped scan, pool exhaustion, mid-query disconnect) are not individually addressed. Since this is the same function `run_minute_cycle` already calls, the risk is mitigated by inherited behavior, but the criteria ask for explicit enumeration, not implicit inheritance from a dependency.

### [PASS] Orthogonality of force_reset_terminal and seeding algorithm matches architecture

The slice's core design decision — making `force_reset_terminal` and coverage-aware seeding independent axes — correctly mirrors the architecture's `update_data_gaps` algorithm, where step 2 (force-reset terminal rows) and step 4 (compute new ranges via `compute_missing_ranges`) are already separate steps. The architecture specifies `mt data refetch` passes `force_reset_terminal=True` while the daemon never sets it; the slice preserves this distinction while unifying the seeding algorithm. This is exactly the kind of orthogonality the architecture's algorithm structure implies.

### [NOTE] via log marker added to daily.py beyond the minute-only defect scope

The slice adds the `via` log-marker threading to `_do_daily_symbol` / `_process_daily_symbol` in `daily.py`, even though the defect class is minute-only. The slice justifies this as "observability parity, not a fix for a real bug" and notes "it costs nothing." The architecture does not prohibit this, and the audit findings correctly establish that daily does not have the same defect. This is a minor scope expansion but is well-justified and low-risk.
