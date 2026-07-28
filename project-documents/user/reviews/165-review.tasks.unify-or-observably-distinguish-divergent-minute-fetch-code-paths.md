---
docType: review
layer: project
reviewType: tasks
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260728
dateUpdated: 20260728
findings:
  - id: F001
    severity: concern
    category: task-completeness
    summary: "Missing explicit instruction to forward `via` through `_process_*_symbol`'s internal call"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md:50
  - id: F002
    severity: concern
    category: gap
    summary: "No task covers Verification Walkthrough step 2 (force_reset_terminal integration check)"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md:141
  - id: F003
    severity: pass
    category: correctness
    summary: "Line-number and code references verified accurate"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing, granularity, and commit distribution sound"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
  - id: F005
    severity: pass
    category: test-coverage
    summary: "No load-test task needed"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
---

# Review: tasks — slice 165

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Missing explicit instruction to forward `via` through `_process_*_symbol`'s internal call

Task 1.1 doesn't call out that `_process_minute_symbol`'s internal call to `_do_minute_symbol` (minute.py:215-221) needs `via=via` forwarded, unlike Task 1.2 which explicitly flags the analogous `run_daily_refetch`→`_do_daily_symbol` site. The same silent gap exists for `_process_daily_symbol`→`_do_daily_symbol` (daily.py:306). Since `via` becomes a required keyword-only arg, forgetting either forward breaks the unmocked call chain exercised by `TestRunMinuteCycle`/`TestRunDailyCycleHappyPath`, so it self-corrects via the "tests pass" success criterion in 1.3 — but the task text should say so explicitly rather than relying on incidental test failure to catch it, especially given 1.2 sets that precedent for the sibling call site.

### [CONCERN] No task covers Verification Walkthrough step 2 (force_reset_terminal integration check)

The slice design's Verification Walkthrough step 2 (seed a `RETRY_EXHAUSTED` row on `trading_test`, run `mt data pull 1m`, confirm reset+re-attempt) has no corresponding task. Steps 1, 3, 4, 5 map cleanly to tasks 2.3, 4.3, 3.1/3.2, and 4.1 respectively, but step 2 is only indirectly covered by the unit-level `test_force_reset_terminal_always_true` (task 2.2), which checks the boolean is passed through but not the actual DB-level reset behavior post-unification.

### [PASS] Line-number and code references verified accurate

All specific file/line hints checked against current source and matched: `run_minute_cycle`'s coverage-index build (minute.py:149), `run_minute_refetch` (minute.py:438), `TestRunMinuteRefetch` (test_minute.py:477), `test_force_reset_terminal_always_true` (test_minute.py:551), the `run_daily_refetch`→`_do_daily_symbol` call (daily.py:426), and the reference doc's `status: draft` frontmatter.

### [PASS] Sequencing, granularity, and commit distribution sound

Four phases each end in their own commit (not batched at end); test tasks (1.3, 2.2) immediately follow their implementation tasks; Phase 1 (mechanical) correctly precedes Phase 2 (the actual fix), matching the design's stated Development Approach; docs (Phase 3) follow the fix; close-out (Phase 4) is last and correctly delegates checklist updates to `task-checker` per project convention.

### [PASS] No load-test task needed

The slice's only performance-adjacent note (the ~3s added coverage-index scan) is explicitly framed as an accepted one-time interactive-command cost, not a restated NFR/SLA — correctly no `tests/load/` task or CI-gating task was added.
