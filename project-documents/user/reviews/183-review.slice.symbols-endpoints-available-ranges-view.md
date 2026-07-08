---
docType: review
layer: project
reviewType: slice
slice: symbols-endpoints-available-ranges-view
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/183-slice.symbols-endpoints-available-ranges-view.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260513
dateUpdated: 20260513
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Thin Wrapper principle is correctly followed"
    location: 183-slice.symbols-endpoints-available-ranges-view.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "No server-side pagination policy is honored"
    location: 183-slice.symbols-endpoints-available-ranges-view.md#Scope
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Error shape aligns with architecture"
    location: 183-slice.symbols-endpoints-available-ranges-view.md#GET-/api/v1/symbols/{symbol}—Detail-Endpoint
  - id: F004
    severity: pass
    category: uncategorized
    summary: "No authentication/rate limiting/caching introduced"
    location: 183-slice.symbols-endpoints-available-ranges-view.md#Scope
  - id: F005
    severity: concern
    category: api-design
    summary: "`name` field is missing from response models"
    location: 183-slice.symbols-endpoints-available-ranges-view.md#Field-Mapping:-instruments-Table
  - id: F006
    severity: concern
    category: scope
    summary: "`?list=` query parameter from architecture is not implemented"
    location: 180-arch.data-serving.md#Symbols
  - id: F007
    severity: concern
    category: code-organization
    summary: "Code location path divergence"
    location: 183-slice.symbols-endpoints-available-ranges-view.md#Scope
  - id: F008
    severity: concern
    category: design-approach
    summary: "Architecture specifies materialized view as source of available ranges"
    location: 180-arch.data-serving.md#Symbol-Detail
---

# Review: slice — slice 183

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Thin Wrapper principle is correctly followed

The slice implements endpoints that parse HTTP parameters, fire indexed SQL queries, and serialize results. No business logic is introduced — consistent with the architecture's "thin wrapper" design principle.

### [PASS] No server-side pagination policy is honored

The slice explicitly states "No pagination — consistent with `/bars`." This aligns with the architecture's range policy which states callers should request bounded ranges and no server-side pagination is provided.

### [PASS] Error shape aligns with architecture

404 returns `{"error": "Symbol 'FAKESYMBOL' not found"}`, matching the architecture's stated error handling pattern and consistent response shape `{"error": "<message>"}`.

### [PASS] No authentication/rate limiting/caching introduced

The slice correctly excludes auth, rate limiting, and caching infrastructure — consistent with the architecture's "What This Does NOT Include" section.

### [CONCERN] `name` field is missing from response models

The architecture specifies the symbols list response should include `name: "Apple Inc."` and the detail response should include `name: "SPDR S&P 500 ETF Trust"`. The slice explicitly omits the `name` field, acknowledging it is a gap ("If a name field is added to `instruments` in a future slice, it can be added to the response then"). This creates an API surface that does not match the architecture's stated response contract. While the field mapping justification is reasonable, the architecture should be updated to reflect this gap, or the slice should note this as a tracked deviation.

### [CONCERN] `?list=` query parameter from architecture is not implemented

The architecture explicitly defines `?list=sp500` as an optional parameter on `GET /api/v1/symbols`. The slice implements `?search=` but omits the `list` filter entirely. The slice's "Excluded" section does not acknowledge this parameter. If the named-list filter is deferred, this should be explicitly stated to avoid a mismatch between architecture and implementation.

### [CONCERN] Code location path divergence

The architecture specifies `src/manta_trading/api/` as the base path. The slice uses `src/manta_trading/api_server/`. While this is likely a deliberate project-wide path decision, the slice does not acknowledge or justify this divergence from the architecture's stated code location.

### [CONCERN] Architecture specifies materialized view as source of available ranges

The architecture states: "The `available` ranges come from a materialized view: `MIN(bucket)` and `MAX(bucket)` per symbol per granularity view, refreshed on a schedule (hourly is fine)." The slice replaces this with lazy per-symbol indexed queries at request time. The slice provides a thorough justification (index seeks are sub-millisecond, no schema migration, avoids expensive hourly refresh scan at scale), and the architecture explicitly states "The slice implementing this endpoint will define the view DDL" — indicating flexibility. This is a reasonable architectural deviation that should be documented as an alternative approach. The slice's rationale is sound, but since the architecture was prescriptive about the materialized view approach, a note flagging this as an approved deviation would strengthen alignment.
