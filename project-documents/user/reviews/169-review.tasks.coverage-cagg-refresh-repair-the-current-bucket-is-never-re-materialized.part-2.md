---
docType: review
layer: project
reviewType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: 452b0b367d03dd87337a1c1fcec74a64e853c76e
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All 19 success criteria are addressed across Part 1 and Part 2"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Clear cross-references to slice design sections"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Non-negotiables are carried through to Part 2"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F004
    severity: concern
    category: operational-gap
    summary: "API server restart is not an explicit task"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F005
    severity: concern
    category: nfr-coverage
    summary: "NFR load test missing for restated NFRs (criterion 12, 17, 19)"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F006
    severity: concern
    category: ambiguity
    summary: "G.2 success criterion reads as if both services are stopped for the whole window"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F007
    severity: concern
    category: nfr-coverage
    summary: "Criterion 12 prod measurement is not explicit on prod"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
  - id: F008
    severity: note
    category: sequencing
    summary: "G.6 absorbs the G.5 success gate"
    location: "169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-2.md"
---

# Review: tasks — slice 169

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [PASS] All 19 success criteria are addressed across Part 1 and Part 2

Each of the 19 success criteria in the slice design maps to at least one task. Part 1 (Tasks A–F) covers criteria 1, 2, 3 (partial), 4, 12, 15, 17, 19 (design-time measurement in B1, plus constants/migrations/architecture). Part 2 covers criteria 5 (G.5/G.6/G.7/G.8), 6/7/8/16 (G.9), 9 (G.15), 10 (G.14), 11 (G.3/G.11), 13 (G.4), 14 (G.10), 18 (G.13). The H.2 task audits all 19 with evidence, providing a final check.

### [PASS] Clear cross-references to slice design sections

Tasks explicitly cite the relevant slice-design sections — e.g., G.1 (walkthrough step 2), G.3 (walkthrough step 3), G.4 (walkthrough step 4), G.6 (walkthrough step 5), G.8 (walkthrough step 6), G.9 (walkthrough step 7), G.10 (walkthrough step 7a), G.11 (walkthrough step 8), G.13 (walkthrough step 8a, criterion 18), G.14 (walkthrough step 9), G.15 (slice 187 walkthrough step 4, criterion 9). The D4/D7 lessons and the runbook/journal convention for prod execution are surfaced.

### [PASS] Non-negotiables are carried through to Part 2

The five non-negotiables (jobs resolved from catalog by name, bounded sub-window materialization, exact `count(*)` for verification, content-based partial-materialization detection, `minute_4hour_ohlcv`'s refresh never paused) are repeated at the top of the file and embedded in the relevant G.* tasks. G.3 explicitly verifies `minute_4hour_ohlcv`'s refresh remains `scheduled = true`; G.7 explicitly invokes content-based detection; G.14 explicitly uses exact counts.

### [CONCERN] API server restart is not an explicit task

G.2 stops the API server for "the 051 DDL window specifically (Window A)" and G.4 applies migrations 051/052, but no task restarts the API server after Window A. The daemon restart is explicit (G.12), but the API server restart is implicit — a junior AI following the task list literally could leave the API server stopped throughout the materialization window (G.5–G.6) and into the verification (G.7–G.11), extending the user-facing 500 exposure far beyond the intended 051 DDL window. This should be either an explicit "Restart the API server" task between G.4 and G.5, or merged into G.4's success criteria with a clear "after applying 051, restart the API server" sub-step.

### [CONCERN] NFR load test missing for restated NFRs (criterion 12, 17, 19)

The slice restates three NFRs: the sub-second `data_status` read (criterion 12, originally 167's NFR), the 10s `CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT` budget (criterion 17 / D3b), and the 1-hour policy schedule interval (criterion 19 / D4a). Per the review criteria, restated NFRs require a `tests/load/` task covering them, or the breakdown must add one. The current Part 2 only records the values via H.3 (which says "Replace the '(draft)' marker and fill in the actual measured sub-window span, wall-clock times, and row counts from Task G" — this references Task G's measurements, not the NFR probe/policy-cost numbers from Part 1's B1). No `tests/load/` test is added, and the Part 1 measurement (in B1) is on an ephemeral/test database, not a load test that can run in CI. A load test task (e.g., a `tests/load/test_data_status_read_meets_nfr.py` and `tests/load/test_content_edge_probe_within_budget.py`) would close this.

### [CONCERN] G.2 success criterion reads as if both services are stopped for the whole window

G.2's prose distinguishes clearly: "Daemon stopped for the whole window… API server stopped for the 051 DDL window specifically (Window A)". But the success criterion says "both confirmed stopped before G.3 proceeds" — the word "both" applied to the same "stopped" state suggests both are stopped for the same duration, which contradicts the prose. Combined with the missing API server restart (see prior finding), this could lead a junior AI to leave the API server down through the materialization. The success criterion should be split, or worded to convey "daemon stopped for the whole window; API server stopped for Window A (until 051 applies)".

### [CONCERN] Criterion 12 prod measurement is not explicit on prod

Criterion 12 requires the full-universe `data_status` read to meet the sub-second NFR, "measured as `SELECT count(*) FROM data_status`". The walkthrough step 1 times this on a test/ephemeral database (Part 1's B1), and H.3 records the number in the slice design's walkthrough section. But no Part 2 task times it on prod to confirm the NFR holds in the actual prod environment. H.2 audits the criterion with recorded evidence, but if no prod timing is taken, the audit can only cite the test-DB measurement. An explicit prod timing (e.g., as a sub-step in G.9 or a dedicated `G.X — Measure prod `data_status` read latency and record against 1s NFR`) would close this — and would also feed the test that should exist per the NFR-load-test finding above.

### [NOTE] G.6 absorbs the G.5 success gate

G.5 is described as "One measured sub-window before the full sweep" with success "one sub-window's cost measured; confirms the chosen sub-window span is safe on the host as configured" — but no task gates G.6 on the G.5 measurement being acceptable. The slice design's Migration Plan and Risks sections both treat the sub-window measurement as a check that, if it fails, aborts the full sweep ("if the width proves wrong after 051 applies, the path back is another migration pair at a corrected width"). The success criteria of G.5 could be tightened to "if peak memory or wall-clock exceeds the host's safe envelope, stop and re-plan rather than proceeding to G.6" — a junior AI might otherwise treat the measurement as informational and proceed regardless.
