---
docType: review
layer: project
reviewType: slice
slice: compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/slices/143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260501
dateUpdated: 20260501
findings:
  - id: F001
    severity: concern
    category: alignment
    summary: "`compute_snapshot_id` algorithm deviates from architecture specification with `fetched_at` None guard"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#technical-decisions
  - id: F002
    severity: concern
    category: error-handling
    summary: "Risk R2 mitigation for `fetched_at` update semantics is insufficiently strong"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#risks
  - id: F003
    severity: pass
    category: alignment
    summary: "Core deliverables align with architectural goals and principles"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#overview
  - id: F004
    severity: pass
    category: error-handling
    summary: "Failure modes are explicitly enumerated with handling strategies"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#in-scope
  - id: F005
    severity: pass
    category: nfr
    summary: "NFR for data_status view latency is restated with specific target"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#technical-decisions
  - id: F006
    severity: pass
    category: scope
    summary: "Scope boundaries are well-defined and consistent with architecture"
    location: 143-slice.compute-k-factor-single-source-of-truth-daily-ohlcv-hypertable.md#out-of-scope-explicit-non-goals
  - id: F007
    severity: note
    category: design
    summary: "`prev_closes` mutability in frozen `CaSnapshot` is an acknowledged tradeoff"
    location: unverified
---

# Review: slice — slice 143

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] `compute_snapshot_id` algorithm deviates from architecture specification with `fetched_at` None guard

The architecture document specifies the `compute_snapshot_id` algorithm in §"`snapshot_id` computation (stable, cross-process)" with `s.fetched_at.isoformat()` — no None guard. The slice's implementation adds `if s.fetched_at is not None else ""`, which produces different output for `None` values than the arch spec (which would raise `AttributeError`). The slice acknowledges this in D4, noting that `Split`/`Dividend` dataclasses don't carry `fetched_at` today and it's being added with a `None` default.

For production data where `fetched_at` is always populated from the DB, the behavior is identical. However, the deviation means any implementer following the arch spec literally will crash on `None`, while the slice silently produces a stable hash with an empty-string sentinel. This creates a subtle contract divergence. The slice should propose an update to the architecture document to formally codify the `None` handling so the arch remains the authoritative algorithm specification.

### [CONCERN] Risk R2 mitigation for `fetched_at` update semantics is insufficiently strong

The slice correctly identifies that if `fetched_at` is ever updated (rather than only inserted), the `snapshot_id` will change without any actual CA-data change, triggering spurious daemon recomputes in slice 144. The mitigation is described as "a quick audit during P5 to confirm `fetched_at` is set once at insert and never updated." A manual audit is a point-in-time check that doesn't prevent future regressions. Since `snapshot_id` stability is a correctness requirement (spurious recomputes waste resources and could mask real CA changes), a stronger mitigation would be appropriate — for example, a CHECK constraint or trigger preventing updates to `fetched_at`, or at minimum a code-level guard in the ingest path that asserts `fetched_at` is immutable. The fallback plan (omit `fetched_at` from canonicalization) is sound but would itself be an arch deviation requiring coordination.

### [PASS] Core deliverables align with architectural goals and principles

The slice's three deliverables — promoting `compute_k_factor` to single source of truth, implementing stable `snapshot_id`, and creating the `daily_ohlcv` hypertable — directly address the architecture's §"One adjustment function" and §"`ca_snapshot` shape" specifications. The `CaSnapshot` dataclass fields match the arch's `ca_snapshot` shape (symbol, splits, dividends, prev_closes, snapshot_id). The `compute_k_factor` signature `compute_k_factor(symbol, target_date, ca_snapshot=...)` aligns with the arch's `compute_k_factor(symbol, target_date, ca_snapshot) -> Decimal`. The `daily_ohlcv` schema includes all columns required by the arch's band-based UPDATE pattern (adj_open/high/low/close, k_factor, adjusted_at). Dependency direction is correct: this slice provides primitives consumed by slices 144 and 147.

### [PASS] Failure modes are explicitly enumerated with handling strategies

The slice specifies four failure modes for the `current_ca_snapshot` I/O path: (1) Market DB unreachable/connection timeout — propagate `OperationalError`; (2) Query timeout — propagate, with caller-side statement_timeout guard documented; (3) Connection drop mid-query — propagate; (4) Missing `prev_close` for a dividend ex_date — log WARNING, omit from dict, surface as `KeyError` at compute time. Each has an explicit handling strategy, not "TBD" or implicit. The propagation-based approach matches the arch's model where callers (daemon, audit command) handle errors at the ingest-loop boundary.

### [PASS] NFR for data_status view latency is restated with specific target

The architecture states "View latency stays sub-second at full-universe scope." The slice restates this in D5: "The architecture specifies that `data_status` view latency stays sub-second at full-universe scope." Success criterion 12 quantifies it: "`data_status` view returns in under 1 second for `SELECT COUNT(*) FROM data_status` against the post-migration dev DB." The walkthrough Step 2 provides a concrete verification command with `\timing`. The contingency plan (adding a `symbol` index if regression is measured) is documented. This satisfies the NFR restatement requirement.

### [PASS] Scope boundaries are well-defined and consistent with architecture

The slice explicitly marks out of scope: daemon CA-detection logic (slice 144), band-based UPDATE writes (slice 144), refetching daily history (slice 144), migrating legacy `dailyOHLCVAdjusted` rows, and modifying `splits`/`dividends` schema. These boundaries are consistent with the architecture, which assigns daemon behaviors and band-based writes to slice 144. The slice delivers exactly what the arch requires as prerequisites for later slices without overreaching.

### [NOTE] `prev_closes` mutability in frozen `CaSnapshot` is an acknowledged tradeoff

The `CaSnapshot` dataclass uses `frozen=True` but contains `prev_closes: dict[date, Decimal]`, which remains mutable (field reassignment is blocked, but dict content mutation is not). The slice documents this explicitly: the `frozen=True` prevents field reassignment, not mutation, and `dict` is retained for idiomatic lookup performance in the inner loop. No caller uses `CaSnapshot` as a dict key or set member. While a `MappingProxyType` wrapper would provide true immutability, the slice's choice is defensible given the performance-sensitive inner-loop usage and the clear documentation of the tradeoff. Not action-required, but worth noting for slice 144/147 consumers who may not read this design closely.
