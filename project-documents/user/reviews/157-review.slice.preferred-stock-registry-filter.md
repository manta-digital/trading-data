---
docType: review
layer: project
reviewType: slice
slice: preferred-stock-registry-filter
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/157-slice.preferred-stock-registry-filter.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260512
dateUpdated: 20260512
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Slice scope is a direct implementation of architecture's deferred refinement"
    location: 157-slice.preferred-stock-registry-filter.md#Overview
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Cascade behavior is intentionally scoped — no architectural violation"
    location: 157-slice.preferred-stock-registry-filter.md#Technical Scope
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Constraint re-render strategy is architecturally sound"
    location: 157-slice.preferred-stock-registry-filter.md#Technical Decisions
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Failure modes are enumerated with explicit handling strategies"
    location: 157-slice.preferred-stock-registry-filter.md#Idempotency
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Dependency ordering is correct and consistent with the architecture's sequencing"
    location: 157-slice.preferred-stock-registry-filter.md#Dependencies
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Architecture's NFRs are not violated"
    location: 157-slice.preferred-stock-registry-filter.md
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Test updates cover the universe layer completely"
    location: 157-slice.preferred-stock-registry-filter.md#Implementation Details
---

# Review: slice — slice 157

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Slice scope is a direct implementation of architecture's deferred refinement

The architecture's "Step 1 — Universe rebuild (slice 141)" specifies filtering `instruments` to `('Common Stock', 'ETF', 'Preferred Stock', 'INDEX')` with an explicit note that Preferred Stock is a candidate for removal. This slice implements exactly that deferred refinement: it removes `PREFERRED_STOCK` from `EodhdType` and tightens the CHECK constraint so that slice 158's delisted-filter logic operates on a clean three-type universe. No scope creep.

### [PASS] Cascade behavior is intentionally scoped — no architectural violation

The slice correctly excludes changes to `ohlcv_minute`, `ohlcv_daily`, and `data_gaps`. Orphan bar rows for deleted preferred symbols become unreachable through the registry but do not corrupt gap detection, since `compute_missing_ranges` in the architecture derives lifecycle from `instruments` rows. The "left to cascade or be cleaned up by a future vacuum slice" decision is documented and appropriate for a 1/5-effort slice.

### [PASS] Constraint re-render strategy is architecturally sound

`_eodhd_type_check_sql()` derives the CHECK constraint from `EodhdType` at call time. By removing the enum member before the migration dict is built, the rendered SQL automatically excludes `'Preferred Stock'` — matching the pattern established by migration 016. No separate helper change is needed; this is the correct use of the existing abstraction.

### [PASS] Failure modes are enumerated with explicit handling strategies

All three migration steps have explicit failure-mode handling:
- `DROP CONSTRAINT IF EXISTS` — safe if already gone
- `DELETE WHERE eodhd_type = 'Preferred Stock'` — safe when zero rows match
- `ADD CONSTRAINT ... CHECK` — guarded by `pg_constraint` existence check

None are "TBD" or implicit. The verification walkthrough includes a step confirming that insertion of a preferred row is correctly rejected by the tightened constraint.

### [PASS] Dependency ordering is correct and consistent with the architecture's sequencing

- Consumes: Slice 156 (migration runner and schema stability) — correctly listed as prerequisite
- Produces for: Slice 158 (delisted filter depends on instruments containing only `Common Stock | ETF | INDEX`)

The downstream consumer (slice 158) expects exactly the three-type universe this slice produces. Integration point is correctly documented.

### [PASS] Architecture's NFRs are not violated

The architecture document specifies NFRs for gap detection (sub-second status queries via the small CTE lookup), API quota (1000 credits/min), and backtest contract. This slice touches none of those paths — it modifies only `instruments` and the classification layer. No NFR is restated because none are implicated.

### [PASS] Test updates cover the universe layer completely

Test coverage spans unit tests (`test_eodhd_classification.py`, `conftest.py`) and integration tests (`test_rebuild_orchestrator.py`). The new test `test_preferred_stock_filtered` asserts that `filter_v1_universe` drops preferred rows, which is the key behavioral contract for downstream consumers. All references to `PREFERRED_STOCK` and `"Preferred Stock"` in the universe layer are accounted for.
