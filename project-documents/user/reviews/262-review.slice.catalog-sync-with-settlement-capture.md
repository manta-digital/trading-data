---
docType: review
layer: project
reviewType: slice
slice: catalog-sync-with-settlement-capture
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260824
dateUpdated: 20260824
reviewedSha: 78fbd90ffb80d26fd94dc52124a17853758c088c
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Async DB write path has no enumerated failure-mode handling"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md:166-176"
  - id: F002
    severity: note
    category: scope
    summary: "MVE exclusion narrows the architecture's stated binding constraint"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md:155"
  - id: F003
    severity: pass
    category: alignment
    summary: "Awaiting-settlement design matches the architecture's settlement principle precisely"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md:157"
  - id: F004
    severity: pass
    category: nfr
    summary: "Storage-growth NFR restated with concrete targets"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md:343"
  - id: F005
    severity: pass
    category: alignment
    summary: "Deferred algorithmic decision resolved by measurement, not assumption"
    location: "project-documents/user/slices/262-slice.catalog-sync-with-settlement-capture.md#Sync-Sizing-Survey-(live-API,-2026-08-24-~19:30-UTC,-public-mode)"
---

# Review: slice — slice 262

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Async DB write path has no enumerated failure-mode handling

Technical Decision 8 introduces a new I/O path for this slice — the `AsyncConnectionPool` used for all catalog/settlement/awaiting-set writes — but only describes session-setting parity (timezone, `work_mem`, `statement_timeout`) with the sync hook. It never enumerates what happens on connection loss mid-write, pool exhaustion/hang, statement-timeout expiry, or a deadlock during the upsert-heavy phases (2, 4, 5). Decision 11's exit-code taxonomy (`0` success, `1` preflight, `2` provider abort, `3` item errors) only classifies *provider* (Kalshi API) failures and a startup DB-reachability check — there is no exit code or handling strategy for a DB fault occurring after phase 1 begins. This is the same class of gap 261 was held to (its client's error mapping explicitly enumerates every `httpx.TransportError` subclass per "review 261 F004 — connection-level failures on a new outbound I/O path must be enumerated, not implied"); 262 does not hold its new DB async path to the same bar, and CLAUDE.md's exception-handling rule (every try/except must re-raise-with-log, handle a named exception with justification, or be a boundary handler) has nothing to anchor to here since the failure taxonomy for this path is simply absent.

### [NOTE] MVE exclusion narrows the architecture's stated binding constraint

The architecture states, twice, that "no market may reach settlement unobserved" is a binding constraint on whatever sync strategy is chosen (260-arch.kalshi-event-contract-data.md, Technical Considerations §"Catalog scale and incremental sync"; restated in this slice's own Overview). Technical Decision 2 excludes all MVE/parlay markets from capture entirely, meaning those markets' settlements are, by design, never observed. The economic justification (zero analytical value, ~20×/year volume multiplier, `/events` doesn't even list them) is reasonable, and the decision is explicitly labeled "PM-visible" for stakeholder sign-off — which is the right mitigation — but the document does not point to any architecture-level or slice-plan-level sanction for narrowing a constraint described as binding. Worth confirming the PM visibility flag is actually acted on before/at implementation, not just noted.

### [PASS] Awaiting-settlement design matches the architecture's settlement principle precisely

Technical Decision 3 implements the architecture's "Settlement is a first-class collection event" principle almost verbatim: entry is driven by the collector's own stored `close_time` (never by which filter/status the API happened to return), retirement requires a `finalized` row with `result`, and vanished-from-walk markets get a direct ticker-lookup reconciliation. This is a faithful, non-diluted translation of the architecture's guarantee.

### [PASS] Storage-growth NFR restated with concrete targets

The architecture's "Volume and storage posture" consideration (260-arch.kalshi-event-contract-data.md:106) is qualitative ("modest... measured decision after real volume is observed"). This slice's Risk Assessment restates it with slice-specific, measured numbers (~200 MB/day, ~70 GB/year, ~11 GB first-run drain) derived from the Sync-Sizing Survey, giving the PM an actionable figure rather than leaving the concern implicit — exactly what the parent NFR needed at this level of detail.

### [PASS] Deferred algorithmic decision resolved by measurement, not assumption

The architecture explicitly deferred the full-vs-incremental sync strategy to slice design and flagged `min_updated_ts`'s incompatibility risk. Technical Decision 1 makes the call from live-measured request/timing data and gives a specific, falsifiable rejection rationale for incremental sync (cursor walks a mutating set; would make the awaiting set filter-dependent — "exactly what the architecture forbids"). This is the kind of evidence-based closure of a deferred decision the architecture asked for.
