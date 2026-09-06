---
docType: review
layer: project
reviewType: tasks
slice: backup-hardening-and-host-bootstrap
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260905
dateUpdated: 20260905
reviewedSha: 8e3ce104882c10877d896821a4bb8a794530b007
findings:
  - id: F001
    severity: pass
    category: completeness
    summary: "All 13 slice success criteria map to specific tasks"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:566-608"
  - id: F002
    severity: concern
    category: test-with-pattern
    summary: "Section 1 and Section 3 batch multiple implementation tasks before a single test task"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-1.md:98-179"
  - id: F003
    severity: note
    category: sequencing
    summary: "PITR drill deferred to after full production cutover rather than \"before proceeding\" as D11 step 3 states"
    location: "project-documents/user/slices/920-slice.backup-hardening-and-host-bootstrap.md:518-521"
  - id: F004
    severity: note
    category: consistency
    summary: "Task 8.2 bundles four manual PM items though the design's Implementation Notes claim only two"
    location: "project-documents/user/tasks/920-tasks.backup-hardening-and-host-bootstrap-2.md:187-207"
  - id: F005
    severity: note
    category: nfr-coverage
    summary: "Load-test / CI-gating criterion not applicable to this slice"
    location: "unverified"
---

# Review: tasks — slice 920

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] All 13 slice success criteria map to specific tasks

Cross-referencing each of the 13 numbered success criteria against the two task files: SC1→Tasks 3.1/3.6a/8.2/9.4, SC2→3.3/8.3, SC3→8.3, SC4→3.2/1.4/8.3, SC5→9.2, SC6→5.1/3.4/8.4, SC7→9.3, SC8→9.1, SC9→9.4, SC10→3.4/8.2/8.4, SC11→3.5/8.3, SC12→9.5, SC13→7.3/10.1. No criterion is left uncovered, and I found no task that fails to trace back to a design decision or success criterion (no scope creep).

### [CONCERN] Section 1 and Section 3 batch multiple implementation tasks before a single test task

Task 1.8 (`test_backup_health.py`) is the only test task covering Tasks 1.3, 1.4, 1.5, 1.6, and 1.7 — five implementation tasks land before any test runs. Task 3.7 is similarly the sole test task for Tasks 3.1 through 3.6a (six implementation tasks: skeleton, packages/ACL, PostgreSQL settings, cron.d rendering, timeshift merge, restic/crontab steps, rehearsal). This deviates from the test-immediately-follows-implementation pattern applied elsewhere in the same file (e.g. 4.1→4.2, 5.1→5.2, 6.2→6.3 test the preceding task directly). It is defensible here — these are steps of one shell script sharing constants, not independently testable in isolation — but it means a defect introduced in, say, Task 3.3's `ALTER SYSTEM` logic would not surface until Task 3.7, several tasks later, rather than being caught immediately after 3.3.

### [NOTE] PITR drill deferred to after full production cutover rather than "before proceeding" as D11 step 3 states

Design decision D11 states implementation order step 3 as "Atomic + compressed `archive_command`… PITR drill across the mixed archive (walkthrough step 5) **before proceeding**" to step 4 (offsite work). In the task breakdown, all code is built in a worktree per the Context Summary's "leave the host checkout on `main` until cutover in file 2" constraint, so no live mixed-archive drill is possible until after Task 8.2 (production cutover) — the drill runs in Task 9.2, after Sections 3 through 8 (including offsite, restic, and the cutover itself) are already applied to production. This is a reasonable consequence of the worktree-isolation safety strategy the task breakdown adopts (and is disclosed, not hidden), and the destructive reconcile step is still correctly gated by the arm-file mechanism rather than by task ordering alone — but it is a real deviation from D11's literal sequencing intent worth the PM's awareness, since the restore_command/archive_command correctness (the design's top-listed risk) is not proven live until after cutover rather than before it.

### [NOTE] Task 8.2 bundles four manual PM items though the design's Implementation Notes claim only two

The slice's Implementation Notes state: "the only checklist items are the two the script cannot do: the PostgreSQL restart (if any) and removing the user-crontab lines." Task 8.2 correctly includes those two, but also has the PM add `MT_BACKUP_RESTIC_PASSWORD` to `.env` and set the B2 console lifecycle rule (from D4). Both extra items are independently required by other design decisions (D9's password precondition, D4's lifecycle-rule backstop) and are legitimately manual/non-scriptable, so this isn't a task-breakdown defect — but the design's own blanket "only two" claim is stale against its own content and could confuse a PM reading only the Implementation Notes summary.

### [NOTE] Load-test / CI-gating criterion not applicable to this slice

The slice design contains no restated NFR requiring throughput/latency verification under load (its "Risks" and capacity discussion — e.g. `zstd -T2` CPU cost, B2 uplink bandwidth — are informal capacity notes, not formal NFRs). No `tests/load/` task is warranted here, and correspondingly no CI-gating task for a load test is expected or missing.
