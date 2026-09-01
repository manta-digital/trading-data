---
docType: review
layer: project
reviewType: slice
slice: historical-backfill-phase
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/267-slice.historical-backfill-phase.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260831
dateUpdated: 20260831
reviewedSha: 9b7004c210a05a7c59c7f79ffb7120bbffc3c7ff
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "\"Shrinking backlog\" value claim does not hold for the trades surface"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:35-41"
  - id: F002
    severity: concern
    category: under-specification
    summary: "Historical trades floor left as an unratified \"(recommended)\" constant"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:110-114"
  - id: F003
    severity: note
    category: risk-disclosure
    summary: "Candle writes into compressed chunks are honestly flagged as unmeasured"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:115-119"
  - id: F004
    severity: pass
    category: error-handling
    summary: "New I/O paths reuse the client's established error taxonomy rather than reinventing it"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:120-131"
  - id: F005
    severity: pass
    category: alignment
    summary: "Phase sequencing and rate-budget sharing align with architectural principles"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:57-96"
  - id: F006
    severity: pass
    category: scope
    summary: "Scope boundaries match the architecture's anticipated historical-backfill slice"
    location: "project-documents/user/slices/267-slice.historical-backfill-phase.md:43-56"
---

# Review: slice — slice 267

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] "Shrinking backlog" value claim does not hold for the trades surface

The Value section claims "the two 'known-lost' counts become a shrinking backlog with a visible floor" — referring to the candle `behind cutoff, uncollected` count (8,394) and the trades `before coverage` count (20,937). Success Criterion 5 confirms the candle count genuinely shrinks as markets are stamped in `market_candle_state`. But Success Criterion 7 states "The live `trades` row's `coverage_from_ts` and the `before coverage` count are unchanged by any number of historical firings" (lines 180–181), and the State section (lines 82–90) confirms `before coverage` "keeps its meaning" permanently. So for trades, the number Kalshi operators see labeled `before coverage` will never decrease even though the slice is actively backfilling that exact range with real trade rows — only a separate new `historical` status line reflects the real progress. A market whose trades are now fully present will still be bucketed as "before coverage," which is the pre-267 meaning of "no data here." This is internally consistent once explained, but it directly contradicts the Value section's framing that both known-lost counts shrink, and it's a genuine observability trap: an operator glancing only at the existing (285/265-established) status block would conclude progress isn't happening for trades when it is. Recommend either updating the `before_coverage` semantics to reflect actual data presence, or rewording the Value section so it doesn't claim the trades bucket shrinks.

### [CONCERN] Historical trades floor left as an unratified "(recommended)" constant

Decision 3 sets `HISTORICAL_TRADES_FLOOR = 2026-01-01T00:00Z (recommended)` and says "Floor is a constant the PM ratifies" — future tense, not yet closed. This constant directly determines total request volume (~100–150k requests, 3–4 weeks of trickle per the doc's own estimate) and how much history is ultimately recoverable before Kalshi's retention window potentially closes further. Sibling slices in the same initiative (264 Decision 2, Decision 4, Decision 5, Decision 6; 265 Decision 2, Decision 3, Decision 4) each carry an explicit "PM-ratified {date}" tag before being treated as final designs. Leaving a scope-defining constant open in a document otherwise formatted like a finalized design is a process gap: if the PM later picks a different floor, several of the effort/timeline claims in Value and Technical Decisions (the "3-4 weeks," the "~100-150k requests" sizing) would need to be re-derived. Recommend closing this decision (with a dated PM ratification note, per the established pattern) before this slice proceeds to implementation.

### [NOTE] Candle writes into compressed chunks are honestly flagged as unmeasured

Unlike the trades sub-drain (which reuses 265's measured "no penalty" result for inserts into compressed chunks), the candle sub-drain writes into markets that are, by construction, well past the 14-day compression horizon (the "behind cutoff, uncollected" set is inherently old). Decision 4 acknowledges this is unmeasured and mitigates with per-market wall-time logging plus a warn threshold (`HISTORICAL_SLOW_MARKET_SECONDS`) and the existing manual runbook pause lever (never automated, consistent with 265's rationale that the application role cannot `alter_job`). This is explicit, not a "TBD," and is also carried into the Risks section (lines 199-200) — satisfies the review bar for enumerated failure handling even though the performance characteristic itself is unverified pre-implementation.

### [PASS] New I/O paths reuse the client's established error taxonomy rather than reinventing it

The two new client methods (`get_historical_trades`, `get_historical_market_candlesticks`) are explicitly framed as mirrors of already-verified methods (261's `get_trades`/`get_market_candlesticks`), and Decision 6 states failure semantics are "identical to `TradesPhase`": `ProviderError`/`OperationalError` fail the phase, page-level failures re-walk idempotently, and rate-limit escalation is explicitly covered in Risks ("the client's existing 429 backoff applies... a sustained escalation shows in the health check's Kalshi phase-recency rule and in the journal," lines 201-203). This satisfies the requirement to enumerate failure handling for new I/O paths — by explicit inheritance rather than silent omission — consistent with the architecture's "pattern reuse, not framework invention" principle.

### [PASS] Phase sequencing and rate-budget sharing align with architectural principles

The design correctly treats the historical drain as a fourth sequential phase within the existing one-process pass, matching the architecture's "surfaces are sequential phases of a pass, not concurrent tasks" principle and its rate-limit-budget-sharing intent. Decision 1 explicitly ties the "no waiting" design to removing rate contention "by construction" via sequencing rather than a new coordination mechanism, which is consistent with the architecture's deferred cross-source-arbitration gap (it doesn't attempt to solve arbitration, it sidesteps needing it — appropriately scoped).

### [PASS] Scope boundaries match the architecture's anticipated historical-backfill slice

Technical Scope's "Out" list (no change to live phase behavior/caps, no automated compression-pause, no hand-driven CLI) correctly respects boundaries established by 264/265 and the parent architecture's "Historical-endpoint migration" consideration, which calls for "a one-time historical backfill slice that drains the historical endpoints while they remain available" without expanding scope into orderbook, streaming, or retention concerns (all explicitly out per the architecture's own Anticipated Slices / Future Work sections).
