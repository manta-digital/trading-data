---
docType: review
layer: project
reviewType: tasks
slice: minute-cagg-chunk-re-sizing
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260722
dateUpdated: 20260722
findings:
  - id: F001
    severity: concern
    category: commit-checkpoints
    summary: "Phase D commits are batched into the closing task instead of distributed across D5–D7"
    location: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md:340-376
  - id: F002
    severity: note
    category: task-scoping
    summary: "B1's \"reuse or relocate\" instruction for shared grid logic has no explicit test/success gate"
    location: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md:156-166
  - id: F003
    severity: pass
    category: coverage
    summary: "All 8 success criteria are explicitly traced to tasks, most with inline \"(success criterion N)\" citations"
    location: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md:322-366
  - id: F004
    severity: pass
    category: sequencing
    summary: "Test-with-implementation pattern consistently followed across all four phases"
    location: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md:105-298
  - id: F005
    severity: pass
    category: nfr-coverage
    summary: "Load-test/CI-gating criterion correctly not applicable to this slice"
    location: project-documents/user/architecture/140-slices.data-quality-operations.md:87
  - id: F006
    severity: note
    category: task-sizing
    summary: "Task C3 (window sweep) bundles several concerns into one task"
    location: project-documents/user/tasks/163-tasks.minute-cagg-chunk-re-sizing.md:247-267
---

# Review: tasks — slice 163

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Phase D commits are batched into the closing task instead of distributed across D5–D7

Tasks D5 ("Record final chunk counts... and total minute-cagg footprint"), D6 ("Resume jobs and steady-state check... recorded"), and D7 ("162 regression and cold-start verification... both outcomes recorded") each produce artifacts/evidence appended to the baseline notes file, but none of their bullets includes a commit action — unlike B5, C7, and D4, which each end with an explicit commit. The next commit callout is D8's "final commit," which means three tasks' worth of recorded evidence (final chunk counts, job-resume status, 162-regression results, cold-start verification) sit uncommitted until slice close-out. This is exactly the batching-at-the-end pattern the review checklist calls out, and it's a real risk here: D5–D7 span prod operations across the remaining three granularities plus a wait for "next trading day" (D6) — if work is interrupted after D5 but before D8, the recorded evidence isn't preserved. Recommend adding a short commit bullet to at least D6 and D7 (e.g., `docs: record 163 steady-state and regression results`), mirroring the pattern already used in B5/D4.

### [NOTE] B1's "reuse or relocate" instruction for shared grid logic has no explicit test/success gate

Task B1 says to "reuse/share `rechunk.py`'s `_window_start` grid logic rather than duplicating," and the anchors section says to relocate `MINUTE_CAGG_GRANULARITIES` "to a shared module only if the import direction is wrong." Both give a implementer a judgment call (reuse-in-place vs. extract-to-shared-module vs. duplicate) without a task-level success criterion distinguishing which choice is acceptable, and no task covers updating `rechunk.py`'s own tests if it's refactored to expose shared logic. This is bounded well enough for a competent implementer (the conditions are stated), so it doesn't block progress, but a stricter task would say explicitly "if you extract, add/adjust `test_rechunk.py` accordingly" so that outcome isn't left implicit.

### [PASS] All 8 success criteria are explicitly traced to tasks, most with inline "(success criterion N)" citations

SC1 (chunk count) → D5; SC2 (full parity) → D5; SC3 (EXPLAIN before/after) → B5 (baseline) + D4; SC4 (compression footprint) → D5; SC5 (resumability) → D3 (kill/resume exercise, also unit-tested in C4); SC6 (jobs resumed, Success status) → D6; SC7 (cold start) → D7; SC8 (162 regression) → D7. No gaps and no orphaned success criteria found — this is stronger traceability than typical task breakdowns in this project.

### [PASS] Test-with-implementation pattern consistently followed across all four phases

Every implementation task is immediately followed by its test task: A2→A3 (migration 044 tests), A4→A5 (migration 045 tests), B1→B2 (parity module tests), B3→B4 (verify CLI tests), C1→C2 (pre-flight tests), C3→C4 (sweep tests, explicitly covering all three D1 crash-window outcomes), C5→C6 (repair CLI tests). No test task is deferred or batched at phase end.

### [PASS] Load-test/CI-gating criterion correctly not applicable to this slice

The parent architecture doc explicitly assigns the "load-test-tier decision" and the sub-second NFR regression test to slice 167 ("Revisit the load-test-tier decision explicitly here... the NFR regression load test is this slice's concern" — referring to 167, not 163). 163's own success criteria are single-query EXPLAIN comparisons (SC3) and parity/compression checks, not a service-level NFR needing `tests/load/` coverage. No gap here — the absence of a load-test task in 163 is correct per the explicit cross-slice division of responsibility, not an omission.

### [NOTE] Task C3 (window sweep) bundles several concerns into one task

C3 covers parity-skip reuse, drop_chunks, refresh_continuous_aggregate, compress_chunk, progress output, Ctrl-C handling, and multi-granularity ordering in one task. This is on the larger side for a single task, but it mirrors the existing `rechunk.py` precedent (364 lines, same shape) explicitly cited as reference, and splitting it would fragment tightly-coupled sequential logic (drop→refresh→compress must be authored and reasoned about together). Acceptable as scoped; flagging only as an observation, not a required split.
