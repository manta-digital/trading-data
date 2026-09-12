---
docType: review
layer: project
reviewType: tasks
slice: operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md
aiModel: z-ai/glm-5.3
status: complete
dateCreated: 20260911
dateUpdated: 20260911
reviewedSha: 5e9ae42e147e08821334af17651c9d5840d828bd
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "All nine success criteria are covered by tasks"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md"
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Sequencing and test-with pattern are correct"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md"
  - id: F003
    severity: concern
    category: uncategorized
    summary: "Overview's ten-second latency bound (Decision 12 / SC1) has no load-tier task"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-is-fresh.md"
  - id: F004
    severity: concern
    category: uncategorized
    summary: "CI gating for the load test is left implicit"
    location: "unverified"
  - id: F005
    severity: note
    category: uncategorized
    summary: "Constant rename in Task 4.1 is minor scope beyond the design text"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md"
  - id: F006
    severity: note
    category: uncategorized
    summary: "Task 2.4 and Task 4.3 are at the large end but acceptable"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-ran-what-is-fresh.md"
  - id: F007
    severity: note
    category: uncategorized
    summary: "SC3's \"manual run beside a timer firing\" case is covered at unit level only"
    location: "project-documents/user/tasks/922-tasks.operator-overview-one-screen-that-answers-what-is-running-what-is-ran-what-is-fresh.md"
---

# Review: tasks — slice 922

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3

## Findings

### [PASS] All nine success criteria are covered by tasks

Cross-reference: SC1 → Tasks 4.3–4.5 (gather/build/render + ten-second assertion in integration); SC2 → Tasks 2.1–2.4 (mappings, progress callback, writer) and 2.5/2.7/2.9; SC3 → Tasks 1.6/1.7 (orphan-closing by pid, foreign-host/live-pid untouched) and 2.4; SC4 → Tasks 5.3/5.4 (footer second line, default flip, `--detail`); SC5/SC6 → Tasks 5.1/5.2 (anchor CTE, fresh `migrated_db` apply, D2 assertion unchanged); SC6a → Task 5.5; SC7 → Tasks 2.2, 1.5, 1.7, 3.2, 4.2, 4.5; SC8 → Tasks 3.1/3.2 + 6.1 (accounting drift test deferred there, consistent); SC9 → Tasks 5.6, 6.1, 6.2. No criterion is unowned.

### [PASS] Sequencing and test-with pattern are correct

Section order respects dependencies: recorder (§1) precedes writers (§2); schedules (§3) precede the overview's `schedule_for` use (§4); migration 055 precedes 056 (§5); `data status` default (Task 5.4) reuses the overview's source gather from §4; accounting units land last (§6). Every implementation task is immediately followed by its test task (1.2→1.3, 1.4→1.5, 1.6→1.7, 2.1→2.2, 2.4→2.5, 2.6→2.7, 2.8→2.9, 4.1→4.2, 4.3/4.4→4.5, 5.1→5.2, 5.3/5.4→5.5). Commit checkpoints appear at Tasks 1.7, 2.5, 2.9, 3.2, 4.5, 5.2, 5.5, 5.6, 6.3 — distributed, not batched.

### [CONCERN] Overview's ten-second latency bound (Decision 12 / SC1) has no load-tier task

Decision 12 explicitly restates latency NFRs: the view (167 margin, covered by Task 5.5) and "The overview's own bound is ten seconds end to end." Task 4.5 only asserts `gather` wall time "under the ten-second bound on the test DB" — an integration-tier measurement against `migrated_db`, not production shape. There is no task adding a case in `tests/load/` (or extending `test_167_data_status_nfr.py`) for the overview at production shape. Either add a load-tier case for the overview path, or the task breakdown should explicitly state why the integration-tier bound plus the five-second EODHD timeout suffices (e.g., DB-only reads plus one HTTPS call). As written, SC1's "under ten seconds" is verified only against test-shaped data.

### [CONCERN] CI gating for the load test is left implicit

Task 5.5 extends the existing `test/load/test_167_data_status_nfr.py` with a new case gated on `MT_RUN_LOAD_TESTS=1`. If the existing file already has CI wiring that runs at production shape, the new case inherits it and this is fine — but the task breakdown does not say so, and no task verifies or adds the CI wiring for the new case. Per the review criteria, CI gating must not be implicit. Add one line to Task 5.5 (or a subtask) confirming/adding the CI job that runs the load file, so the new case is actually gated. I could not verify the CI configuration from the provided documents.

### [NOTE] Constant rename in Task 4.1 is minor scope beyond the design text

Task 4.1 renames `HEALTH_EODHD_USER_ENDPOINT` → `EODHD_USER_ENDPOINT`. Design Decision 8 does not require the rename; it only mandates moving the fetch through `eodhd_get`. The rename is defensible (the design context notes the constant is "currently orphaned" and its only consumer becomes the overview) and matches CLAUDE.md's single-definition principle, but it should be acknowledged as a deliberate touch on health-adjacent constants, with any remaining references updated in the same task.

### [NOTE] Task 2.4 and Task 4.3 are at the large end but acceptable

Task 2.4 (effort 3) covers both minute and daily writer sites, including the boundary-extraction refactor in `daily.py` and the new `CycleReport` fields; Task 4.3 (effort 3) covers gather plus the full pure builder. Both have clear success criteria, cite the design's Writers table, and are followed by dedicated test tasks, so a junior executor can complete them; but if execution stalls, the minute/daily split in 2.4 and the gather/build split in 4.3 are natural cut lines.

### [NOTE] SC3's "manual run beside a timer firing" case is covered at unit level only

Task 1.7 verifies with a fake repository that a live-pid row and a foreign-host row are not closed — this satisfies the SC3 unit expectation (SC7's "recorder's orphan-closing rule"). The walkthrough's live two-rows scenario (Verification Walkthrough step 5) is deferred to the host validation in Task 6.3, which is consistent with the design's own placement of that check. No action needed; recorded for traceability.
