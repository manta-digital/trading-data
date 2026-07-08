---
docType: review
layer: project
reviewType: slice
slice: universe-rebuild-from-eodhd-instruments-schema-migration
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md
aiModel: moonshotai/kimi-k2.6
status: complete
dateCreated: 20260430
dateUpdated: 20260430
findings:
  - id: F001
    severity: concern
    category: error-handling
    summary: "Network-level failure modes not explicitly enumerated for new HTTP clients"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#api-contracts
  - id: F002
    severity: concern
    category: integration
    summary: "Slice 142 data_status view compatibility assumption unsupported by architecture"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#d5-venue-authority-existing-row--finnhub--conservative-default
  - id: F003
    severity: concern
    category: data-integrity
    summary: "Risk of migration 016 failure due to unmatched legacy AV symbols"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#migration-plan
  - id: F004
    severity: note
    category: scope
    summary: "Schema expansion beyond architecture's explicit column list"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#d1-new-columns-are-added-in-one-migration
  - id: F005
    severity: pass
    category: alignment
    summary: "Non-destructive rebuild and correct cross-slice sequencing"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#overview
  - id: F006
    severity: pass
    category: alignment
    summary: "Lifecycle date contract correctly staged for downstream slices"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#value
  - id: F007
    severity: pass
    category: design-quality
    summary: "Database-driven resumability eliminates cursor-file drift risk"
    location: 141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md#d7-no-cursor-file-for-finnhub-resumability
---

# Review: slice — slice 141

**Verdict:** CONCERNS
**Model:** moonshotai/kimi-k2.6

## Findings

### [CONCERN] Network-level failure modes not explicitly enumerated for new HTTP clients

Description: The slice documents HTTP status-code errors (403, 401, 429) and malformed JSON for the EODHD bulk symbol-list and Finnhub `/stock/profile2` endpoints, but does not explicitly enumerate hang, timeout, or peer-disconnect-mid-send failure modes with explicit handling strategies. The document notes a generic "retry-with-backoff wrapper" without specifying how each of these network-level failures is handled (e.g., retry count, backoff ceiling, circuit breaker), which the review criteria require for each new I/O path.

### [CONCERN] Slice 142 data_status view compatibility assumption unsupported by architecture

Description: The slice asserts that rows with the transient `venue='US'` placeholder will "fall back to the NYSE calendar via `trading_calendar_id`" in slice 142's `data_status` view. However, the parent architecture (`140-arch.data-quality-operations.md#one-status-view`) specifies the view joins the `exchange_completed_close` CTE on `ec.exchange = i.venue`. Since `trading_calendar.exchange` values are actual exchange names (NASDAQ, NYSE, etc.) and not the placeholder `'US'`, the architecture's pseudocode would exclude `venue='US'` rows from the view entirely rather than falling back via `trading_calendar_id`. The slice's integration contract assumes a view shape that contradicts the architecture's specification.

### [CONCERN] Risk of migration 016 failure due to unmatched legacy AV symbols

Description: Migration `016_instruments_eodhd_type_not_null` requires `eodhd_type` to be populated for every row. The rebuild upsert is driven by EODHD bulk-list rows ("For each EODHD row..."), updating existing symbols only when they appear in the EODHD feed. If an existing AV-seeded symbol is absent from the EODHD bulk lists, its `eodhd_type` remains NULL, causing migration 016 to fail and violating the pre-flight contract that slice 142 expects (`eodhd_type IS NOT NULL` for every row). The slice does not specify a fallback or sentinel-value strategy for residual AV-only symbols before tightening the column to `NOT NULL`.

### [NOTE] Schema expansion beyond architecture's explicit column list

Description: The parent architecture explicitly lists five columns to be added by the universe rebuild step: `first_listing_date`, `first_data_date`, `delisted_date`, `eodhd_type`, and `delisted_at_eodhd`. The slice adds a sixth column, `eodhd_exchange`, to store the raw EODHD provider field. While well-justified, this is a minor expansion beyond the architecture's explicit inventory.

### [PASS] Non-destructive rebuild and correct cross-slice sequencing

Description: The slice correctly implements the architecture's Step 1 as non-destructive: bars, acquisition_state, and gap tables are untouched. It defers TRUNCATE, `data_gaps`, `data_status`, `acquisition_state` slimming, and daemon changes to slices 142 and 144, matching the architecture's prescribed two-step migration sequence.

### [PASS] Lifecycle date contract correctly staged for downstream slices

Description: The slice creates `first_listing_date` (populated from Finnhub where available), `first_data_date`, and `delisted_date`, and correctly leaves the latter two NULL for slice 144 to populate during backfill. This directly enables the architecture's `effective_start = COALESCE(first_listing_date, first_data_date)` contract and the backfill behavior described in the parent architecture.

### [PASS] Database-driven resumability eliminates cursor-file drift risk

Description: The slice achieves resumability for the ~17-hour Finnhub enrichment loop by selecting rows based on `first_listing_date IS NULL OR venue = 'US'` rather than maintaining a separate cursor file. This avoids denormalized state drift and aligns with the architecture's goal of making the data layer transparent and trustworthy.
