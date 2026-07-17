---
docType: review
layer: project
reviewType: slice
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md
aiModel: claude-fable-5
status: complete
dateCreated: 20260717
dateUpdated: 20260717
findings:
  - id: F001
    severity: concern
    category: nfr
    summary: "Parent-arch sub-second `data_status` NFR not restated or verified"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md#success-criteria
  - id: F002
    severity: concern
    category: error-handling
    summary: "Background jobs (compression policy 1009, cagg refresh) during Phase C merge — failure mode unhandled"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:224-228
  - id: F003
    severity: concern
    category: under-specification
    summary: "Storage-reclaim success criterion depends on a recompression step no phase performs"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:272-274
  - id: F004
    severity: concern
    category: doc-drift
    summary: "Changes a chunk interval specified in 100-arch.data-storage without flagging the arch-doc update"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:216-222
  - id: F005
    severity: note
    category: consistency
    summary: "Frontmatter `dependencies: []` vs Consumes section listing slices 156 and 160"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:6
  - id: F006
    severity: note
    category: under-specification
    summary: "Mixed compressed/uncompressed windows near the head of the table"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md:157
  - id: F007
    severity: pass
    category: alignment
    summary: "Scope, plan alignment, and prerequisite framing are correct"
    location: project-documents/user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md#overview
---

# Review: slice — slice 166

**Verdict:** CONCERNS
**Model:** claude-fable-5

## Findings

### [CONCERN] Parent-arch sub-second `data_status` NFR not restated or verified

The parent architecture states "View latency stays sub-second at full-universe scope" for `data_status` (140-arch.data-quality-operations.md:150), and that view computes `first_bar_ts = MIN(time)`, `last_bar_ts = MAX(time)`, `bars_stored = COUNT(*)` from the data tables per (symbol, granularity) — for minute granularity, this is exactly the unbounded single-symbol MIN/MAX shape this slice measured at 10m47s. The slice touches this path directly but never restates the sub-second NFR nor includes `mt data status` full-universe latency in Phase D verification or Success Criteria. Per the review contract, an NFR on a touched path must be restated with its specific target. Adding a full-universe `mt data status` timing to Phase D would close this cheaply.

### [CONCERN] Background jobs (compression policy 1009, cagg refresh) during Phase C merge — failure mode unhandled

Phase C runs ~600 merge transactions against prod with only writer contention addressed ("daemon is stopped"). But the slice's own baseline records columnstore policy job 1009 firing **every 2 hours** (line 66), and the four attached caggs have refresh policies (≤1h per the 162 plan entry). A multi-hour merge run will overlap these background jobs; a policy job compressing or a cagg refresh materializing over a chunk mid-merge is a concrete lock-conflict/hang path with no stated handling — pause the jobs, tolerate-and-retry the window, or prove non-interference in the Phase A rehearsal. "Stops cleanly on first error" (line 184) is a backstop, not a strategy; on a run this long, a predictable 2-hourly collision would repeatedly halt it. The Consumes section acknowledges the policy "interacts with the merge strategy" (line 252) but defers entirely to rehearsal without naming this failure mode or its handling.

### [CONCERN] Storage-reclaim success criterion depends on a recompression step no phase performs

Success Criterion 7 expects total size to drop from 126 GB to ~30–40 GB because "the 85 GB TOAST pathology collapses with proper batch sizes." Batch sizes only improve when data is **recompressed** into the merged 7-day chunks. Phase C is merge → ANALYZE only (steps 7–9); no step decompresses/recompresses, and no criterion verifies the ~1,180 merged chunks end up compressed. If `merge_chunks` in 2.23 merges compressed chunks without rewriting batches, TOAST fragmentation persists and criterion 7 silently fails; if it requires decompression first, disk transiently grows toward the uncompressed 214 GB and a recompress pass becomes mandatory — neither branch is specified. The Phase A rehearsal should be tasked with answering this explicitly, and Phase C should include the resulting compress step (or a stated reason none is needed).

### [CONCERN] Changes a chunk interval specified in 100-arch.data-storage without flagging the arch-doc update

100-arch.data-storage.md:67 specifies "Hypertable: `minute_ohlcv` (4hr chunks)" and line 124 reiterates 4hr chunk sizing as a design rationale. This slice (under the 140 band) changes that storage-layer parameter to 7 days and updates the migration, but nowhere lists updating 100-arch — leaving the upstream architecture document (a declared dependency of the 140 arch) stating a value the system no longer uses. This is the "changing a value should require editing exactly one place" concern at the document level: the interval constant is centralized in code, but the stale 4hr statement in 100-arch will mislead future chunk-sizing decisions (the 10×-source cagg default this slice itself analyzes makes that a live risk). Add a Phase B/D item to update 100-arch (and any other doc restating 4hr).

### [NOTE] Frontmatter `dependencies: []` vs Consumes section listing slices 156 and 160

The frontmatter declares no dependencies (matching the plan entry), yet "Consumes from Other Slices" names 156 (cold-start contract) and 160 (compression configuration). Since both are complete, empty blocking-dependencies is defensible, but the two representations should agree on what "dependency" means; listing [156, 160] would make the migration-chain and compression-policy coupling machine-visible.

### [NOTE] Mixed compressed/uncompressed windows near the head of the table

The baseline records 21 uncompressed chunks (the trailing <7 days, per `compress_after = 7 days`). The merge driver's grouping logic doesn't say whether a target window mixing compressed and uncompressed chunks is merged, skipped until compression completes, or handled specially — worth one sentence in the driver spec so the rehearsal covers it.

### [PASS] Scope, plan alignment, and prerequisite framing are correct

Slice 166 has a proper plan entry (140-slices entry 26) whose scope — EXPLAIN-first diagnosis, fix the root cause, re-run the exact three T15 queries — the design matches without creep. The "prerequisite for 163/164" claim matches the plan's 2026-07-17 priority note verbatim. Exclusions (cagg re-chunking → 163, bounded-time convention → 164, daemon restart → PM) keep boundaries with adjacent slices clean, and the interfaces list (163, 164, 182) matches real documents/plan entries. The evidence-before-fix structure (Phase A gate, "do not fix speculatively," stop-and-revise on contradicting EXPLAIN) directly honors the project's debugging rule, and the single `MINUTE_OHLCV_CHUNK_INTERVAL` constant referenced by both migrations honors the no-scattered-values rule.
