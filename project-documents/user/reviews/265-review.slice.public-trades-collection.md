---
docType: review
layer: project
reviewType: slice
slice: public-trades-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/265-slice.public-trades-collection.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260828
dateUpdated: 20260828
reviewedSha: 3a9e8993653b9fad4a052d55cc3d4c1dc803cbc1
findings:
  - id: F001
    severity: concern
    category: dependency-direction
    summary: "Frontmatter dependency omits the design's own production prerequisite on 264"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:6"
  - id: F002
    severity: note
    category: doc-drift
    summary: "Parent architecture's idempotency principle text is stale relative to the PM-ratified composite key"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md:47"
  - id: F003
    severity: note
    category: doc-drift
    summary: "Architecture's \"unknown market = sync defect\" principle isn't reconciled with the already-decided permanent MVE exclusion"
    location: "project-documents/user/architecture/260-arch.kalshi-event-contract-data.md:41"
  - id: F004
    severity: pass
    category: alignment
    summary: "Settings rename follows the \"no silent fallback\" discipline"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:185"
  - id: F005
    severity: pass
    category: alignment
    summary: "Exclusion accounting and completeness reporting match the architecture's stated design goal"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:375"
  - id: F006
    severity: pass
    category: integration
    summary: "Integration point with 266 matches what the historical-backfill slice is expected to consume"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:360"
---

# Review: slice — slice 265

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Frontmatter dependency omits the design's own production prerequisite on 264

The frontmatter declares `dependencies: [263]` only. The slice's own Prerequisites section (line 124) states "264 complete and cut over on manta9000 (`v0.10.0`; candle backlog drained — the trades drain should not start while the candle backlog is still using the hour)" — a real production-sequencing gate on 264 that is absent from the machine-readable `dependencies` field. This also sits in tension with the parent slice-plan document (`260-slices.kalshi-event-contract-data.md:28,37`), which twice characterizes 264 and 265 as "mutually independent" / "can land in either order." If the "either order" framing was meant only for build order and not deploy order, that distinction isn't stated anywhere, so anything gating work off the frontmatter `dependencies` list (task tooling, `cf status`) would miss the real ordering constraint and could deploy 265's drain concurrently with an active 264 backlog drain, exactly the contention scenario the slice itself warns against.

### [NOTE] Parent architecture's idempotency principle text is stale relative to the PM-ratified composite key

"Idempotent writes everywhere" states "trades insert on Kalshi's trade id alone," but slice 265 Decision 4 (line 187) uses `PRIMARY KEY (market_ticker, created_time, trade_id)`, PM-ratified 20260828, because `trade_id` must accompany the partitioning column for hypertable promotion. This is functionally equivalent (trade_id is globally unique, so composite-key conflict-ignore rejects exactly the same duplicates as trade_id-alone would), and the companion slice-plan document was updated to reflect it (`260-slices...md:43`) — but the architecture document itself (dateUpdated 20260826, before this ratification) was never amended, so a reader of the architecture in isolation would see a principle the actual schema no longer literally follows.

### [NOTE] Architecture's "unknown market = sync defect" principle isn't reconciled with the already-decided permanent MVE exclusion

Architectural Principles states flatly: "A candle or trade for an unknown market therefore indicates a sync defect, not an acceptable race." Slice 265 Decision 5 (line 189) correctly identifies that ~8% of the tape is *permanently* unknown to the catalog by design (MVE markets, excluded via `mve_filter=exclude` since 262) rather than a race, and handles it accordingly (count, log distinct prefixes for operator visibility, never store, never error) — a defensible refinement, not a violation. The gap is that the architecture document's principle text was never updated to carve out the MVE case even though the MVE exclusion itself is already documented elsewhere in the same architecture doc (Technical Considerations, line 96). This is worth a one-line amendment to the architecture doc so the principle doesn't read as contradicted by design.

### [PASS] Settings rename follows the "no silent fallback" discipline

Decision 3's rename of `MT_KALSHI_CANDLE_*` → `MT_KALSHI_COLLECTION_*` includes a loud `model_validator` guard that fails startup if any old-prefixed variable is set, rather than silently ignoring it (a real risk with pydantic-settings prefix changes). This directly satisfies both the parent architecture's "Fail loud, back off hard" principle and the project's "never use silent fallback values" rule, and is scoped as a tracked breaking change (CHANGELOG, runbook step, migration-style guard) rather than a quiet behavior shift.

### [PASS] Exclusion accounting and completeness reporting match the architecture's stated design goal

Architecture's Design Goals (line 29) requires excluded markets to be "counted and reported by `mt data kalshi status`, never silently dropped." Slice 265's Success Criterion 2 and the `TradeResult` fields (`unknown_market`, `excluded_by_rule`, `duplicates`) make `fetched = written + unknown + excluded + duplicates` an explicit, tested invariant, and `status` surfaces `before_coverage`/`partial_history`/`short_of_close` per market — a faithful, verifiable instantiation of the architecture's completeness definitions.

### [PASS] Integration point with 266 matches what the historical-backfill slice is expected to consume

The architecture (Technical Considerations, line 94) requires the design to "leave room for a one-time historical backfill slice," and the slice-plan's Historical Backfill entry (`260-slices...md:32`) expects a persisted boundary and idempotent target table. 265 provides exactly that: `coverage_from_ts` as the exact upper bound for 266's drain, `before_coverage` as 266's market-set input, the same page/cursor shape reusable against `/historical/trades`, and the same pause/resume-by-hypertable-name compression lever already established as the standing convention. Dependency direction is correct (265 produces, 266 consumes) and matches the slice-plan's `Dependencies: [264, 265]` for 266.
