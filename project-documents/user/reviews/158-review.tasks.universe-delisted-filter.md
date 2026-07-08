---
docType: review
layer: project
reviewType: tasks
slice: universe-delisted-filter
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/158-tasks.universe-delisted-filter.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260512
dateUpdated: 20260512
findings:
  - id: F001
    severity: concern
    category: success-criteria-coverage
    summary: "`iter_active_instruments` unchanged verification is incomplete"
    location: project-documents/user/tasks/158-tasks.universe-delisted-filter.md:T6
  - id: F002
    severity: concern
    category: documentation-accuracy
    summary: "Slice design's Technical Scope contains a contradiction"
    location: project-documents/user/slices/158-slice.universe-delisted-filter.md:23-25
  - id: F003
    severity: pass
    category: success-criteria-coverage
    summary: "All success criteria have corresponding tasks"
    location: project-documents/user/tasks/158-tasks.universe-delisted-filter.md
  - id: F004
    severity: pass
    category: task-sequencing
    summary: "Task sequencing is correct"
    location: project-documents/user/tasks/158-tasks.universe-delisted-filter.md
  - id: F005
    severity: pass
    category: scope-creep
    summary: "No scope creep detected"
    location: project-documents/user/tasks.universe-delisted-filter.md
  - id: F006
    severity: pass
    category: task-granularity
    summary: "All tasks are appropriately sized for a junior AI"
    location: project-documents/user/tasks/158-tasks.universe-delisted-filter.md
  - id: F007
    severity: pass
    category: commit-checkpoints
    summary: "Commit checkpoints are distributed throughout"
    location: project-documents/user/tasks/158-tasks.universe-delisted-filter.md
---

# Review: tasks — slice 158

**Verdict:** CONCERNS
**Model:** minimax/minimax-m2.7

## Findings

### [CONCERN] `iter_active_instruments` unchanged verification is incomplete

Success Criterion 4 states: "iter_active_instruments in symbols.py is **unchanged** — all existing daemon tests continue to pass."

Task T6 runs `test_symbols.py` to verify existing tests pass, which implicitly assumes `iter_active_instruments` was not modified. However, this does not *prove* the function is unchanged — it only proves it still works. A junior AI could accidentally modify `iter_active_instruments` while implementing T2 (replacing the call) if they mistakenly edit `symbols.py` instead of `data.py`.

T2 specifies replacing the call in `data.py`, but the task should include an explicit verification that `symbols.py` is unmodified (e.g., a `git diff src/manta_trading/cli/data/acquisition/symbols.py` returning empty, or a `git diff` in T2's checklist).

---

### [CONCERN] Slice design's Technical Scope contains a contradiction

The Technical Scope states: "Update unit tests in `test_data_pull.py` **and test_symbols.py**"

However, the slice design also states under Excluded: "No change to iter_active_instruments — the daemon's 'one final pass' semantics are correct for that context and must not be disturbed" and in Integration Points: "test_symbols.py — no change needed; iter_active_instruments is unchanged."

The task breakdown correctly excludes `test_symbols.py` changes, but the slice design's Technical Scope is self-contradictory. This inconsistency could cause confusion if a reader only references the Technical Scope section. The task file correctly handles this discrepancy, but the source of truth (the slice design) should be reconciled.

---

### [PASS] All success criteria have corresponding tasks

Cross-reference against slice design Success Criteria:

| Success Criterion | Task(s) | Status |
|---|---|---|
| 1. `--universe` queries only active instruments | T2, T5 | ✓ Covered by T5 test `test_universe_default_excludes_delisted` |
| 2. `--universe --include-delisted` queries all instruments | T2, T4, T5 | ✓ Covered by T5 test `test_universe_include_delisted_removes_filter` |
| 3. `--include-delisted` without `--universe` exits 1 | T2, T5 | ✓ Covered by T5 test `test_include_delisted_without_universe_exits_error` |
| 4. `iter_active_instruments` unchanged, daemon tests pass | T6 | ✓ Partial (see CONCERN above) |
| 5. All 1,246+ unit tests pass, zero pyright errors | T7, T8 | ✓ Covered |

---

### [PASS] Task sequencing is correct

- T2 (add parameter) precedes T4 (add CLI option), which precedes T5 (test CLI behaviour) — correct dependency order.
- T5 (tests) immediately follows T4 (implementation) — follows test-with pattern.
- T6 (regression), T7 (static analysis), T8 (full suite) are appropriately placed after implementation and tests.
- T9 (commit) and T10 (verification) are at the end.

No circular dependencies or ordering violations detected.

---

### [PASS] No scope creep detected

All tasks trace to the slice design:
- T2 and T4 implement the core changes described in Technical Scope (new parameter, new CLI option, SQL query replacement).
- T5 adds only the three tests specified in the slice design's Tests section.
- T6 runs regression on the two files specified in the Context section (`data.py` and `test_data_pull.py`).
- T10 verification walkthrough matches the slice design's Verification Walkthrough section.

No task introduces functionality not described in the slice design.

---

### [PASS] All tasks are appropriately sized for a junior AI

Each task has clear, numbered checkboxes with specific file paths, function names, SQL snippets, and pytest invocation commands. A junior AI can determine exactly what to do and how to verify success for each task.

---

### [PASS] Commit checkpoints are distributed throughout

- T1: branch creation (implicit checkpoint)
- T5: local test pass
- T6: regression pass
- T7: static analysis pass
- T8: full suite pass
- T9: commit

Commit is not batched at the end — there are multiple verification steps before commit.
