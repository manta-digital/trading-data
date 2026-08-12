---
docType: review
layer: project
reviewType: slice
slice: daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260809
dateUpdated: 20260809
reviewedSha: c23d7d42f93d1ad1aea8a12323afe6490dc54900
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Cagg list matches parent architecture"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#dependent-caggs-all-must-be-paused-during-the-run-per-the-166-a5-q3-lesson
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Sub-second latency NFR for the touched path is restated"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#success-criteria
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Pause/resume scope respects the minute/daily family boundary"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#d5-job-pauseresume-scope-runbook-cagg-maintenance-pausingmd
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Constants centralization matches architecture convention"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#d1-target-chunk-interval-70-days-daily_ohlcv_chunk_interval
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Scope kept minimal via registry pattern, no over-engineering"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#d2-generalize-mt-data-rechunk-via-a-target-registry
  - id: F006
    severity: pass
    category: uncategorized
    summary: "Migration-chain-as-source-of-truth preserved"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#d3-migration-pair-166-phase-b-pattern
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Failure modes handled via inherited 166 mechanism"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#risk-assessment
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Post-rewrite verification aligns with arch's \"verify after raw chunk restructuring\" rule"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#d6-verification-gate-r5-discriminator-not-exit-codes-alone
  - id: F009
    severity: pass
    category: uncategorized
    summary: "Integration sequencing with slice 169 is explicit"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#integration-points
  - id: F010
    severity: pass
    category: uncategorized
    summary: "Daemon hazard closed without changing daemon code"
    location: 170-slice.daily-ohlcv-rechunk-the-last-table-with-166-163-s-disease.md#integration-points
---

# Review: slice — slice 170

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Cagg list matches parent architecture

The four caggs paused during the run (`daily_weekly_ohlcv`, `daily_monthly_ohlcv`, `daily_quarterly_ohlcv`, `daily_coverage`) match exactly the post-152 cagg inventory declared in arch §"Continuous aggregates" (3 over `daily_ohlcv`) plus the `daily_coverage` cagg defined in arch §"One status view" and §Constants `COVERAGE_SOURCE_TABLE`. The slice correctly notes `daily_coverage` is "not hierarchical, so plain `alter_job` works on it," consistent with arch's distinction between hierarchical (`minute_coverage`) and direct-over-raw (`daily_coverage`) coverage caggs.

### [PASS] Sub-second latency NFR for the touched path is restated

Success Criterion #2 ("`SELECT MAX(time) FROM daily_ohlcv` returns sub-second") explicitly restates the latency target for the path this slice touches. This is the path that serves arch §"One status view" (`daily_coverage` reads raw `daily_ohlcv`, so daily timestamps stay exact), and which `assert_cagg_fresh` (slice 168) probes under the `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT = 10s` budget. Restating the target lets a reader verify the slice closes the planning-latency hazard the parent arch depends on.

### [PASS] Pause/resume scope respects the minute/daily family boundary

D5 pauses only the daily-family jobs and explicitly states "R1 holds: minute-family jobs stay running." This is consistent with the architecture's per-granularity operator discipline (arch §"Operator commands" — separate daily and minute paths, no cross-granularity mutation). The non-hierarchical `daily_coverage` is handled with the R2a form (NULL bounds, `force => true`), matching its single-hop refresh topology in arch §"COVERAGE_SOURCE_TABLE" vs. the two-hop minute chain.

### [PASS] Constants centralization matches architecture convention

D1 defines `DAILY_OHLCV_CHUNK_INTERVAL` once in `constants.py` and references it from both the new migration and the updated creation migration. This is consistent with arch §"Constants" ("Defined once, in `manta_trading.constants` ... Referenced by every module that needs them") and the 166 precedent cited. The choice (70 days, matching `MINUTE_CAGG_CHUNK_INTERVAL`) is justified by a wall-clock rule (span ÷ target count) and grid nesting (70 = 10 × 7 nests inside the existing 7-day chunks).

### [PASS] Scope kept minimal via registry pattern, no over-engineering

D2 limits generalization to a `RechunkTarget` enum + per-target registry supplying hypertable name, interval, dependent cagg views, and the migration id the pre-flight names in its error message. The CLI default (`minute`) preserves existing invocation semantics, gated by Success Criterion #7's regression guard. Driver logic (window classification, EXCLUSIVE lock, staged==reinserted guard, `SKIP_UNCOMPRESSED`, resumability) is explicitly "reused untouched" — minimal new surface, no hidden abstraction layers.

### [PASS] Migration-chain-as-source-of-truth preserved

D3 updates the slice-143 creation migration's literal `INTERVAL '7 days'` to render the new constant, so a cold start creates 70-day chunks directly. Combined with the new `set_chunk_time_interval` migration (which affects only future chunks and is safe regardless of rewrite timing), this satisfies the single-schema-source-of-truth contract. Success Criterion #6 ("Cold start creates 70-day `daily_ohlcv` chunks from the first migration run") verifies the property.

### [PASS] Failure modes handled via inherited 166 mechanism

The slice's genuinely new I/O surface is small: a CLI flag validated by a `StrEnum` (rejected by Typer on invalid values), a registry lookup (in-memory, no failure mode), a `set_chunk_time_interval` migration (framework-handled transactional semantics), and the new daily target's force-refresh. The runtime path (`mt data rechunk --table daily`) inherits 166's well-defined failure handling: per-window transactions ("an interrupted run leaves a valid, partially-improved table"), EXCLUSIVE per-window lock (concurrent writers "safe but stalled"), pre-flight (refuses to run while daily-family jobs are scheduled), `SKIP_UNCOMPRESSED` trailing windows (idempotent re-run picks them up later). The catastrophic mode (cagg corruption via concurrent refresh during chunk restructuring, cited from 166 A5-Q3) is explicitly mitigated by pre-flight + force-refresh on resume.

### [PASS] Post-rewrite verification aligns with arch's "verify after raw chunk restructuring" rule

D6 uses the R5 closed-window parity check (sum parity strictly before the newest window boundary must be exactly 0) alongside `mt data caggs verify`, applying the arch §"`mt data caggs` — continuous-aggregate maintenance" standing rule ("after any raw chunk restructuring, run `verify`; if parity fails, run `repair`"). The decision to combine exit-code + R5 discriminator (rather than treating `verify` exit 2 as failure) is well-justified — exit 2 covers benign trailing lag as well as real corruption.

### [PASS] Integration sequencing with slice 169 is explicit

The slice correctly identifies 169 (coverage-cagg refresh repair) as a downstream dependency that must run after this slice, and disambiguates two distinct defects: the `daily_coverage` content staleness (stuck at 2026-06-12) is healed as a side effect of step 8's full refresh, but the policy defect (365-day bucket never refreshed) re-accrues staleness from day one — so 169 is still required. This is consistent with arch's two-hop cagg chain and the role of slice 169's repair scope.

### [PASS] Daemon hazard closed without changing daemon code

The slice removes the planning-latency hazard for "every other query shape" while preserving the 0.7.6 daemon anti-join fix that treats the symptom. No daemon changes — the wedge class is closed at the storage layer (chunk count) rather than at the query layer. This respects arch §"`mt data daemon run`" as the long-running process whose interactions must remain uncontested.
