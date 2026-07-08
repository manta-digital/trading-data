---
docType: review
layer: project
reviewType: slice
slice: universe-delisted-filter
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/158-slice.universe-delisted-filter.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260512
dateUpdated: 20260512
findings:
  - id: F001
    severity: pass
    category: scope-management
    summary: "Scope is appropriately narrow and targets a gap in the architecture"
    location: 158-slice.universe-delisted-filter.md
  - id: F002
    severity: pass
    category: dependency-management
    summary: "Dependency direction is correct — `iter_active_instruments` is explicitly preserved"
    location: 158-slice.universe-delisted-filter.md#interfaces-required
  - id: F003
    severity: pass
    category: nfr-handling
    summary: "No NFRs are violated or misstated"
    location: 158-slice.universe-delisted-filter.md
  - id: F004
    severity: pass
    category: error-handling
    summary: "Error handling is specified for the new flag interaction"
    location: 158-slice.universe-delisted-filter.md#cli-change
  - id: F005
    severity: pass
    category: migration-safety
    summary: "No migration, no schema change, no cross-cutting effects"
    location: 158-slice.universe-delisted-filter.md#excluded
---

# Review: slice — slice 158

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Scope is appropriately narrow and targets a gap in the architecture

The slice modifies symbol resolution behavior in the data pull command — a specific CLI path — without touching `data_gaps`, `acquisition_state`, gap computation functions, or daemon behavior. The architecture defines `symbols_active_on(date D)` with a precise SQL predicate for "active universe," and this slice implements a version of that for the pull command's universe option. No architectural boundaries are violated.

### [PASS] Dependency direction is correct — `iter_active_instruments` is explicitly preserved

The slice correctly identifies that `iter_active_instruments` in `symbols.py` must not be modified, since its "one final pass" daemon semantics are correct for that context. The pull path diverges by using a direct SQL query parameterized by `include_delisted`. This is a clean split — no hidden coupling to daemon behavior.

### [PASS] No NFRs are violated or misstated

The architecture states EODHD quota constraints (100k/day, 1000/min) and performance requirements. This slice touches symbol resolution — an O(1) DB query in `_resolve_symbols_for_pull`. Both SQL variants (`WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL` for default; no filter for opt-in) are simple indexed reads against `instruments`. No new I/O paths are introduced that could stress the quota or performance model.

### [PASS] Error handling is specified for the new flag interaction

`--include-delisted` without `--universe` exits 1 with a clear error message. The validation is placed in `_resolve_symbols_for_pull` before the existing mutual-exclusivity guard. No TBD or hand-waving.

### [PASS] No migration, no schema change, no cross-cutting effects

The slice explicitly excludes: no change to `iter_active_instruments`, no change to `--list`/`--symbol`/`--symbols` paths, no schema/migration/DB state changes, no interaction with `data_gaps` or `acquisition_state`. This is consistent with the architecture's clean separation between data quality operations and pull command behavior.
