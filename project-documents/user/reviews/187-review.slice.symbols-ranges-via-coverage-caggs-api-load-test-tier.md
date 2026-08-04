---
docType: review
layer: project
reviewType: slice
slice: symbols-ranges-via-coverage-caggs-api-load-test-tier
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260804
dateUpdated: 20260804
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Architecture NFR restated with explicit target"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D10
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Architectural correction well-evidenced"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D12
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Pool consolidation decision driven by measurement"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D11
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Failure modes enumerated for every new I/O path"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D2
  - id: F005
    severity: note
    category: scope
    summary: "D6 deliberately flips production visibility to stale"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D6
  - id: F006
    severity: note
    category: scope
    summary: "Cross-layer changes are appropriately scoped"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#technical-scope
  - id: F007
    severity: note
    category: api-design
    summary: "`create_app(db_url=None)` seam is a small interface addition"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D9
  - id: F008
    severity: note
    category: uncategorized
    summary: "CycleGranularity reference to slice 912 is forward-citing, not a dependency"
    location: 187-slice.symbols-ranges-via-coverage-caggs-api-load-test-tier.md#D7
---

# Review: slice — slice 187

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Architecture NFR restated with explicit target

D10 explicitly restates the architecture's "`statement_timeout` bounds a statement, not a request" finding (from `180-arch.data-serving.md#error-handling`) and binds it to a concrete request-latency target: "< 15 s" for assertion 2 at the admission ceiling, "< 250 ms" for symbol detail, and "< 1.5 s" for status. The architecture's deferral language about consolidation ("deferred to slice 187, which builds the load-test tier that can size it") is honored as D11's mandate rather than re-litigated.

### [PASS] Architectural correction well-evidenced

The slice replaces the architecture's claim that the symbol-detail range queries are "sub-millisecond index seeks" with measured evidence (D1: 2.7–4.0 s, 96% planning across 3,371 chunks). The slice files this as an explicit architecture-document correction rather than silently diverging, which is the right pattern when the parent doc's premise is wrong.

### [PASS] Pool consolidation decision driven by measurement

The slice notes that the architecture defers the three-pool consolidation question to 187 and commits to deciding based on assertion 3's measurement rather than assuming the outcome. Slice 186 D2 declined the change for the same reason (no measurement); 187 explicitly removes that excuse by recording the decision either way with the numbers behind it.

### [PASS] Failure modes enumerated for every new I/O path

The three new statements (universe edges, per-symbol coverage, per-symbol head probe) are each bounded for chunk exclusion with measured costs. The four merge cases (data spanning the edge, entirely before, entirely after, no data) are each enumerated as real scenarios with measured latencies. D4 documents the tail-probe failure mode with measurements that justify rejecting it. D5/D6 enumerate the cagg-staleness failure mode with root-cause analysis.

### [NOTE] D6 deliberately flips production visibility to stale

The slice states that shipping will flip `mt data status`, `/api/v1/health`, and `/api/v1/status` to reporting coverage stale. The slice flags this as a visible operational change, attributes the underlying defect to slice 167's delivery (D5), and files repair as separate slice 169. This is appropriate scope discipline — the slice fixes the detection gap without absorbing the repair — but the operational impact should be coordinated before ship. The Risks section correctly documents the `ERROR`-log-volume knob as the lever if noise proves excessive.

### [NOTE] Cross-layer changes are appropriately scoped

The slice modifies `market/maintenance/cagg_freshness.py` and `data/maintenance/status_coverage.py` in addition to `api_server/`. The architecture's stated dependencies (`100-arch_data-storage.md`, `140-arch_data-quality-operations.md`) cover these layers, and D6's reasoning — the generic guard has a one-bucket detection floor that wide-bucket coverage caggs cannot escape — justifies why a coverage-specific content-edge check belongs in the coverage layer rather than the API layer. No concern, but worth noting that this slice is not strictly API-local.

### [NOTE] `create_app(db_url=None)` seam is a small interface addition

The slice adds an optional `db_url` parameter to `create_app` to keep `MT_TIMESCALE_DB_URL` out of the load tier (enforced by `test_load_tier_never_references_prod_db_url`). The default (`None` → read `Settings()`) preserves every production path unchanged, so this is a non-breaking seam. The slice correctly documents it as the unit tier's path too, which is appropriate reuse.

### [NOTE] CycleGranularity reference to slice 912 is forward-citing, not a dependency

D7 references `CycleGranularity` from slice 912 with the parenthetical "they are already what `data_gaps.granularity` and `acquisition_state.granularity` store," implying the enum exists today. The frontmatter `dependencies` field lists `[167, 185, 186]` and `interfaces` lists `[169, 907]`. If 912 has not landed, this reference should be promoted to the frontmatter; if it has, no action needed. Worth a quick check.
