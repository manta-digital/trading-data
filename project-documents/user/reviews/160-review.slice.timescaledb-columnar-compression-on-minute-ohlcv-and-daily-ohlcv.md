---
docType: review
layer: project
reviewType: slice
slice: timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260515
dateUpdated: 20260515
findings:
  - id: F001
    severity: pass
    category: architecture-alignment
    summary: "Correct alignment with adjusted-on-read architecture"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Overview
  - id: F002
    severity: pass
    category: correctness
    summary: "Write path compatibility correctly analyzed"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Write Path Compatibility
  - id: F003
    severity: pass
    category: architecture-alignment
    summary: "Compression settings preserve data access patterns"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Compression Settings
  - id: F004
    severity: pass
    category: dependency-alignment
    summary: "Prerequisites correctly reference slice 156"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Dependencies
  - id: F005
    severity: pass
    category: architecture-alignment
    summary: "Backtest contract remains unaffected"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Technical Scope
  - id: F006
    severity: pass
    category: correctness
    summary: "Cagg behavior correctly addressed"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Technical Scope
  - id: F007
    severity: pass
    category: error-handling
    summary: "Migration idempotency documented"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Success Criteria
  - id: F008
    severity: note
    category: completeness
    summary: "No explicit NFR enumeration"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md
  - id: F009
    severity: note
    category: completeness
    summary: "Backfill duration considered but not formally error-handled"
    location: src/slices/160-slice.timescaledb-columnar-compression-on-minute-ohlcv-and-daily-ohlcv.md#Migration Structure
---

# Review: slice — slice 160

**Verdict:** PASS
**Model:** minimax/minimax-m2.7

## Findings

### [PASS] Correct alignment with adjusted-on-read architecture

The slice correctly notes that post-152, there is no `UPDATE` or `DELETE` path for OHLCV bar data. The `adj_*` columns and CA recomputation band-writer were deleted in slice 152. The exclusion of "CA recomputation path" is appropriate — this concern from the slice plan entry is obsolete as documented. This confirms the slice understands the post-slice-152 adjusted-on-read model.

### [PASS] Write path compatibility correctly analyzed

The slice correctly documents that `INSERT ... ON CONFLICT DO NOTHING` is safe on compressed chunks:
- New rows route to the uncompressed DML buffer, which TimescaleDB flushes and recompresses during the next policy run
- Existing rows are detected via the maintained chunk index; `DO NOTHING` requires no mutation

This is architecturally sound and requires no changes to the write path defined by the architecture.

### [PASS] Compression settings preserve data access patterns

`compress_segmentby = 'symbol'` enables per-symbol block pruning, and `compress_orderby = 'time DESC'` optimizes for trailing-window queries (the common case per architecture's status view). Both settings cover the uniqueness constraints `(symbol, time)` on both tables. This preserves the gap detection and status query patterns defined in the parent architecture without architectural violations.

### [PASS] Prerequisites correctly reference slice 156

The prerequisite from slice 156 (cold-start integrity, migration chain as single schema source of truth) is appropriate. Compression relies on a stable migration chain to ensure `mt data init` bootstraps a clean database correctly.

### [PASS] Backtest contract remains unaffected

The architecture's backtest contract (strict/skip-and-mark/forbid-symbol policies with `read_data_gaps_consistent_for`) is unchanged. Compression is transparent to reads; compressed chunks are read natively via standard SQL. No changes to `data_gaps`, `acquisition_state`, or the backtest contract are needed or proposed.

### [PASS] Cagg behavior correctly addressed

The slice correctly notes that compressed chunks are read natively by the continuous aggregates, and cagg refresh policies continue working unchanged. This aligns with the architecture's CAGGs specification in the [Adjusted-on-read (slice 152)](#adjusted-on-read-slice-152) section.

### [PASS] Migration idempotency documented

The success criteria include re-applying the migration as a no-op. The migration callable is idempotent by design: `ALTER TABLE SET` is a no-op if compression is already enabled, and the policy check against `timescaledb_information.jobs` prevents duplicate policy installation. This is appropriate failure handling for a production operator tool.

### [NOTE] No explicit NFR enumeration

The parent architecture document does not state specific NFRs for storage compression (latency per query, throughput targets). The slice does not restate NFRs because none exist in the parent that apply to this change. This is not a concern — the slice's expected 85–95% disk savings and I/O throughput improvement are improvements, not compliance requirements.

### [NOTE] Backfill duration considered but not formally error-handled

The slice documents that the backfill step "may run for minutes to tens of minutes" on production with thousands of chunks, and provides progress logging at 50-chunk intervals. While explicit failure modes for partial backfill (connection loss mid-backfill, peer disconnect) are not enumerated, the design is sound: the migration records itself only after all three steps complete, so partial runs are retried by re-running `mt data migrate apply`. This is acceptable given that TimescaleDB chunk state (`is_compressed`) is checked before each `compress_chunk` call, making the backfill loop safe to re-run.
