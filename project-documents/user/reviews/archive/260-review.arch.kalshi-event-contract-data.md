---
docType: review
layer: project
reviewType: arch
slice: kalshi-event-contract-data
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/architecture/260-arch.kalshi-event-contract-data.md
aiModel: deepseek/deepseek-v4-pro
status: complete
dateCreated: 20260823
dateUpdated: 20260823
reviewedSha: fa2f5ff99dcac43ef45498576884e0e433a67853
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "Central catalog sync algorithm is deferred to slice design"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#technical-considerations"
  - id: F002
    severity: concern
    category: completeness
    summary: "No explicit ordering between catalog sync and time‑series collection within the daemon cycle"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#envisioned-state"
  - id: F003
    severity: concern
    category: consistency
    summary: "Ambiguous idempotent‑write key for trades risks implementation mistakes"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#architectural-principles"
  - id: F004
    severity: note
    category: completeness
    summary: "Rate‑limit budget allocation across surfaces is unresolved"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md#technical-considerations"
---

# Review: arch — slice 260

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4-pro

## Findings

### [CONCERN] Central catalog sync algorithm is deferred to slice design

The document identifies the sync strategy (full vs incremental, cadence) as “the main algorithmic decision for slice design” but provides no architectural guidance. Because the catalog is the spine of the system and its sync strategy directly impacts rate‑limit budgeting, consistency with time‑series collectors, and the ability to keep up with Kalshi’s changing data, this deferral leaves a critical gap. Without at least a high‑level strategy the feasibility of the whole collector remains uncertain, and later slice design could require significant rework of the stated architecture.

### [CONCERN] No explicit ordering between catalog sync and time‑series collection within the daemon cycle

The architectural principle states “catalog sync must therefore lead the time‑series collectors”, yet the envisioned daemon merely “cycles three collection surfaces” with no specification of ordering or guard. If the loop interleaves or runs time‑series collection first, orphan records could appear (violating referential integrity) or foreign‑key constraints could cause failures. The architecture should define the cycle order and any inter‑cycle synchronization to ensure the catalog is up‑to‑date before candles/trades are fetched.

### [CONCERN] Ambiguous idempotent‑write key for trades risks implementation mistakes

The sentence “candles and trades insert with natural keys (market_ticker + period + timestamp, trade id)” blends descriptions for two different surfaces. It is unclear whether the trade insert key is just `trade_id` or includes market_ticker/period/timestamp. If a reader interprets this as a composite key for trades, the implementation could wrongly use a period‑based key and miss deduplication or incorrectly reject trades. This ambiguity undermines the critical idempotent‑write promise.

### [NOTE] Rate‑limit budget allocation across surfaces is unresolved

The architecture mentions that the three surfaces share one rate‑limit budget, but it does not outline how the budget is divided or prioritised (round‑robin, per‑surface caps, dynamic allocation). Without guidance, slice design may introduce contention or starvation between catalog sync and time‑series collection, potentially delaying settlement detection or backfilling.
