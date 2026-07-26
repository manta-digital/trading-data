---
docType: review
layer: project
reviewType: tasks
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260726
dateUpdated: 20260726
findings:
  - id: F001
    severity: fail
    category: process-gap
    summary: "No commit checkpoints anywhere in the task breakdown"
    location: project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Load test claims \"CI-gated\" but no task wires actual CI"
    location: project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:344
  - id: F003
    severity: note
    category: test-coverage
    summary: "Migration 048's doc-comment test claim isn't a real task"
    location: project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md:220
  - id: F004
    severity: pass
    category: completeness
    summary: "Coverage, sequencing, and factual grounding are solid"
    location: project-documents/user/tasks/167-tasks.cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr.md
---

# Review: tasks — slice 167

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [FAIL] No commit checkpoints anywhere in the task breakdown

Every section (1–10) lacks the `- [ ] **Commit**: ...` checklist items that the project convention requires and that this slice's own hard dependency (168) uses after nearly every section. Without them, work either batches into one commit at close-out or commit timing is left ad hoc — both violate "commit per task, not batched at end."

### [CONCERN] Load test claims "CI-gated" but no task wires actual CI

The repo has no CI config anywhere (`.github/workflows` doesn't exist). The precedent load tests (`test/load/test_146_part1_nfrs.py`) gate on `MT_RUN_LOAD_TESTS=1` via a docstring asserting "CI must enable" — never mechanically wired. Task 8.1 repeats the same pattern (bare "CI-gated" bullet at 8.1.4) without referencing that env-gate convention or adding a task to make gating concrete, so criterion 6 stays exactly as implicit as it was left in slice 146.

### [NOTE] Migration 048's doc-comment test claim isn't a real task

4.3.4 asserts "an integration test asserts it is non-empty and names both bounds," but no task instantiates this — section 7's equivalence tests cover `bars_summary` output, not the `COMMENT ON VIEW` content. Minor; likely fine to fold into 4.3 or 7.1 explicitly.

### [PASS] Coverage, sequencing, and factual grounding are solid

All 8 success criteria (including the F002-derived 8th) map to tasks; dependencies flow linearly (docs → constants → migrations → view rewrite → guarded accessor → CLI surfacing → equivalence → load test → prod verification → close-out) with no circularity; effort scores stay ≤3/5; and spot-checked references (`minute.py:171`, `migrate_cold_start.py:300`, migration IDs, `MAX_COVERAGE_SOURCE_STALENESS`) all check out against the current tree.
