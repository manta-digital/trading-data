---
docType: review
layer: project
reviewType: slice
slice: cold-start-integrity
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/156-slice.cold-start-integrity.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260508
dateUpdated: 20260508
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Cold-start path restoration aligns with architecture goals"
    location: 156-slice.cold-start-integrity.md
  - id: F002
    severity: pass
    category: uncategorized
    summary: "`acquisition_state` column shape consistent with post-152 architecture"
    location: 156-slice.cold-start-integrity.md#migration-038-column-shape
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Fixup migration pattern avoids architecture-violating mutation"
    location: 156-slice.cold-start-integrity.md#why-a-fixup-migration-not-editing-existing-migrations
  - id: F004
    severity: pass
    category: uncategorized
    summary: "`mt data init` CLI command is consistent with architecture's operator-command design"
    location: 156-slice.cold-start-integrity.md#components
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Integration test provides the failure-mode coverage the architecture implies"
    location: 156-slice.cold-start-integrity.md#testintegrationtest_cold_startpy-new
---

# Review: slice — slice 156

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Cold-start path restoration aligns with architecture goals

The architecture's purpose is "Make the data layer transparent and trustworthy" with three questions the operator must be able to answer. The architecture specifies `data_status` view and gap functions that depend on `acquisition_state`, both of which require a working empty-to-working database path. Slice 156 restores this path, directly supporting the architecture's stated purpose. The three independent defects (missing CREATE, undocumented entry point, no CI gate) map precisely to three independent fixes that are architecturally sound.

### [PASS] `acquisition_state` column shape consistent with post-152 architecture

The migration 038 column shape (`symbol`, `granularity`, `provider`, `last_attempt_ts`, `last_attempt_outcome`) matches the architecture's "Slimmed `acquisition_state`" specification. The `last_adjusted_ca_snapshot_id` column is correctly omitted because the "Adjusted-on-read (slice 152)" section of the architecture explicitly states it is dropped. Migration 030's "drop a column from `acquisition_state`" refers to `last_adjusted_ca_snapshot_id` (added by migration 019, dropped by 030 — net: absent, matching the architecture). The fixup migration's `IF NOT EXISTS` ensures existing databases (`trading_test`) see a no-op on 038.

### [PASS] Fixup migration pattern avoids architecture-violating mutation

The slice correctly chooses an append-only fixup migration rather than editing existing deployed migrations. This is the only safe pattern once migrations are deployed — mutation would silently diverge existing databases from what the migration list claims. The placement immediately before `019_slim_acquisition_state` in `MINUTE_MIGRATIONS` is load-bearing for correctness (not alphabetical by id), and the rationale is well-documented.

### [PASS] `mt data init` CLI command is consistent with architecture's operator-command design

The architecture specifies four `mt data` command groups (`daemon run`, `ca`, `status`, `refetch`) and implicitly establishes the `mt data` family as the operator control surface. Adding `init` for cold-start bootstrap follows this pattern naturally. The command composes `timescale_init.initialize_database` + `apply_schema_migrations`, which aligns with the architecture's migration-from-current-state approach (slices 141–145 sequence: rebuild universe, apply schema, refetch).

### [PASS] Integration test provides the failure-mode coverage the architecture implies

The architecture defines precise gap-function and data-status semantics but relies on the implementation to guard against regressions. The integration test that creates an ephemeral DB, runs `mt data init`, and asserts a schema manifest is the appropriate mechanism — the architecture's gap functions (`compute_missing_ranges`, `update_data_gaps`) and `data_status` view all depend on `acquisition_state` existing. The parametrized negative test (delete a CREATE migration → clear failure) directly addresses the failure mode this slice was filed to prevent.
