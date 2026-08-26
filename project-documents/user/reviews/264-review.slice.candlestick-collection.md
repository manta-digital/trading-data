---
docType: review
layer: project
reviewType: slice
slice: candlestick-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/264-slice.candlestick-collection.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260826
dateUpdated: 20260826
reviewedSha: 6a33a746870aa01921b0b457df4ef2ae944aa874
findings:
  - id: F001
    severity: concern
    category: scope-alignment
    summary: "Candle collection scope narrows the architecture's completeness goal without updating the parent document"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:194-199"
  - id: F002
    severity: note
    category: scope-alignment
    summary: "Hypertable-at-creation deviates from architecture's stated default storage posture, but is well-justified and explicitly ratified"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:203"
  - id: F003
    severity: pass
    category: layering
    summary: "Module boundaries and layering match the established 262/263 pattern"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:166"
  - id: F004
    severity: pass
    category: dependency-direction
    summary: "Catalog-leads-time-series sequencing and dependency direction are correctly enforced"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:150"
  - id: F005
    severity: pass
    category: error-handling
    summary: "Provider/transport failure modes for the new batch endpoint are explicitly enumerated, not left TBD"
    location: "project-documents/user/slices/264-slice.candlestick-collection.md:209"
---

# Review: slice — slice 264

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Candle collection scope narrows the architecture's completeness goal without updating the parent document

The architecture's top Design Goal is "**Capture before it disappears** — The primary goal is completeness of the record while it is still reachable: full catalog lifecycle..., candlestick history, and public trades" (260-arch.kalshi-event-contract-data.md:29), and the Envisioned State describes candlestick collection as appending "candlestick history per market from each market's open through its close" for the synced market set (260-arch.kalshi-event-contract-data.md:74), with no mention of any category- or activity-based exclusion. Decision 2 in this slice introduces a configurable selection rule that permanently drops Sports (58% of the catalog by market count) and Mentions categories, and skips any market that has never traded (55% of open markets) — reducing collected volume from ~600 GB/yr (everything) to ~31 GB/yr. The slice is explicit and self-aware about this trade-off ("an interest limitation the PM accepted knowingly," "what it deliberately gives up: the quote history of markets that never trade... and Sports/Mentions entirely"), and cites PM ratification (20260826) plus an earlier "PM direction 20260824" recorded at the 260 *slice plan* level. However, the 260 **architecture** document itself (dateUpdated 20260824) still states the unqualified completeness goal and full open-to-close collection with no note of this narrowing, no cross-reference to the ratifying decision, and no updated Envisioned State language. A reader of the architecture doc alone would not learn that ~90%+ of candle-eligible markets are permanently excluded by design. This is a real, material deviation from a stated Design Goal that should be reflected back into the architecture document (or its Envisioned State/Design Goals section amended) rather than left implicit in a slice-level "Decisions ratified" note.

### [NOTE] Hypertable-at-creation deviates from architecture's stated default storage posture, but is well-justified and explicitly ratified

Architecture states: "Plain relational tables with proper indexes are the default posture; promotion of trades/candles to hypertables is a measured decision after real volume is observed" (260-arch.kalshi-event-contract-data.md:106). Decision 4 makes `kalshi.candlesticks` a hypertable **from creation** rather than deferring promotion, reasoning that at the measured/projected volume (hundreds of millions of rows within a year) "260's 'promote later' becomes a long maintenance window." The slice explicitly cites the architecture's language and reasons past it, and the decision is PM-ratified with concrete measurement backing (Discovery Findings storage-cost section). This is a legitimate, evidence-driven override rather than a hidden violation, but it is still a divergence from an explicit default stated in the parent doc that the architecture document does not yet acknowledge.

### [PASS] Module boundaries and layering match the established 262/263 pattern

"Module boundaries follow 262: the core has no httpx, no typer, no SQL; it depends on a `CandleSource` Protocol... and a `CandleRepository`; the planner is pure." This directly satisfies the architecture's "Pattern reuse, not framework invention" principle (260-arch.kalshi-event-contract-data.md:35) and keeps the candle phase substitutable/testable the same way the catalog phase is, with no new orchestration framework introduced.

### [PASS] Catalog-leads-time-series sequencing and dependency direction are correctly enforced

`PASS_PHASES = (CatalogPhase(), CandlesPhase())` and the pending queries join off the post-sync `markets ⋈ events ⋈ series` set, matching the architecture's principle that "candle/trade collection operates on the post-sync market set... A candle or trade for an unknown market therefore indicates a sync defect, not an acceptable race" (260-arch.kalshi-event-contract-data.md:41). The abort-interaction rule ("a catalog abort skips this phase; a candle abort cannot affect the catalog phase, which has already finished," line 181) is explicit rather than implicit.

### [PASS] Provider/transport failure modes for the new batch endpoint are explicitly enumerated, not left TBD

Decision 7 enumerates the specific failure modes for the new `/markets/candlesticks` I/O path: over-cap 400 (planner-bug, propagates and fails the pass visibly), 429/5xx (delegated to the existing bounded-retry transport path from 261), and per-market omission from the response (treated as `item_error`, state left untouched, retried next pass) — satisfying the "Fail loud, back off hard" principle (260-arch.kalshi-event-contract-data.md:55) with concrete, non-generic handling rather than a placeholder.
