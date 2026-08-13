---
docType: review
layer: project
reviewType: tasks
slice: coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260813
dateUpdated: 20260813
reviewedSha: 55e661cde8c0e626d6936db21f494b924af4def0
findings:
  - id: F001
    severity: concern
    category: scope-coverage
    summary: "F.1 contradicts the slice design's \"Consumers requiring change\" claim for `restore_metadata.py`"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F002
    severity: note
    category: scope-coverage
    summary: "Criterion 3's \"count tripwire\" aspect is not explicitly addressed"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F003
    severity: pass
    category: traceability
    summary: "All 19 success criteria trace to at least one task across Parts 1 and 2"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F004
    severity: pass
    category: test-coverage
    summary: "Test-with pattern respected; tests immediately follow their implementation tasks"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F005
    severity: pass
    category: load-testing
    summary: "Load tests added for the three NFRs the slice restates; CI wiring gap documented, not implicit"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F006
    severity: pass
    category: process
    summary: "Commit checkpoints distributed across task groups, not batched at the end"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F007
    severity: pass
    category: sequencing
    summary: "Sequencing is logical; A → B → C → D → E → F; B feeds C; C.9 precedes D; E and F depend only on prior"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
  - id: F008
    severity: pass
    category: task-sizing
    summary: "Tasks are well-scoped; sub-tasks are independently completable with clear success criteria"
    location: "project-documents/user/tasks/169-tasks.coverage-cagg-refresh-repair-the-current-bucket-is-never-re-materialized-1.md"
---

# Review: tasks — slice 169

**Verdict:** CONCERNS
**Model:** minimax/minimax-m3

## Findings

### [CONCERN] F.1 contradicts the slice design's "Consumers requiring change" claim for `restore_metadata.py`

The slice design's Migration Plan / Consumers section states that `restore_metadata.py` "lists both coverage views among recreatable objects (lines 190–191) and references 046 as their creating migration. **That reference must move to 051**, or the restore tool recreates them at the old width." Task F.1 instead verifies that no change is needed, on the grounds that the ledger check at `:166` consults `MINUTE_MIGRATIONS` dynamically. The author has documented the verification (with line numbers), but the two documents disagree on a load-bearing consumer and the disagreement is not flagged. If the dynamic-lookup claim is correct, the slice design should be amended; if incorrect, F.1 needs a "change" branch, not just a verify-only branch. Either way, the discrepancy should be reconciled in writing — the current state leaves a future reader trying to determine which document is authoritative on this consumer.

### [NOTE] Criterion 3's "count tripwire" aspect is not explicitly addressed

Criterion 3 says: "Migrations 051/052 apply cleanly on a cold-start database, and the migration chain ends at **052 with the count tripwire updated**." Task D.3 covers the cold-start apply and the "ends at 052" assertion, but the "count tripwire updated" clause is not represented as a separate check. If the project has a count assertion elsewhere (e.g. a `MAX_MIGRATION` constant, ledger length check, or test that pins the migration count), an explicit verify/update step would close the criterion literally. If the tripwire is implicit in the existing framework, a one-line note in D.3 confirming this would suffice.

### [PASS] All 19 success criteria trace to at least one task across Parts 1 and 2

Every criterion is covered: criteria 1, 2, 4, 7, 13, 14, 15, 16, 18 (scaffold), 19 are addressed in Part 1; criteria 3, 5, 6, 8, 9, 10, 11, 12, 17, 18 (prod), 19 (prod) are appropriately split with the prod step in Part 2. Criterion 12 is correctly framed as a prediction in B.4 and explicitly handed off to Part 2 G.9a rather than marked satisfied prematurely. Criterion 18's "policy advances the head" mechanism is built as a reusable check in C.6a (with a regression case that would catch the original defect's signature), ready for Part 2 G.13 to exercise against prod.

### [PASS] Test-with pattern respected; tests immediately follow their implementation tasks

C.3 follows C.2; C.6 follows C.5; C.6a carries its own integration + regression tests; C.8 follows C.1/C.7; C.10 follows C.9; D.3, D.3a, D.4, D.5, D.5a all follow D.1/D.2. The C.9 fix to `_data_status_doc_comment()` is correctly sequenced **before** D.1/D.2's migrations, so 051/052 can call the corrected function directly with no placeholder/supersede step. F.1 also adds a previously-missing test for `restore_metadata.py` — good test-with discipline, even though that file had no coverage before this slice.

### [PASS] Load tests added for the three NFRs the slice restates; CI wiring gap documented, not implicit

Task B.8 adds `test/load/test_169_coverage_freshness_probe_nfr.py` (criterion 17), extends the 167 precedent `test_167_data_status_nfr.py` (criterion 12), and adds a policy-run-cost assertion (criterion 19). B.8 explicitly cites the 167 precedent's documented CI gap, references slice 907 (CI Pipeline and Load-Test Gating) as the out-of-band owner, and D.7 confirms no integration-test CI gap is introduced relative to the project's current (no test-CI) baseline. CI gating is documented, not left implicit.

### [PASS] Commit checkpoints distributed across task groups, not batched at the end

Five commits, one per task group: after B, C, D, E, F. Each commit message is descriptive and scope-bounded (e.g. `feat: re-derive coverage cagg constants at the measured width`, `test: add load tests for coverage-cagg NFRs at the new width`).

### [PASS] Sequencing is logical; A → B → C → D → E → F; B feeds C; C.9 precedes D; E and F depend only on prior

A.1 verifies the 140-arch amendment state before B measures; B.7 selects the width that C.1/C.2/C.4/C.7 render from; C.9 (doc comment fix) is explicitly called out as a prerequisite for D.1/D.2 so 051 can call the corrected function directly. The F.1 verification is correctly placed last — it depends on the migration state being settled.

### [PASS] Tasks are well-scoped; sub-tasks are independently completable with clear success criteria

While Task B has 8 sub-tasks (18 effort) and Task C has 11 sub-tasks (22 effort), each sub-task carries an explicit `Success:` clause and a discrete deliverable. The largest unit, D.1 (effort 4), is a complex migration with non-trivial ordering requirements that justify its size. D.1's success criteria explicitly enumerate the mandatory ①②③ ordering, the `CASCADE` prohibition, the idempotency requirement, and the description-text verification — all the things a junior AI would otherwise need to derive from the slice design.
