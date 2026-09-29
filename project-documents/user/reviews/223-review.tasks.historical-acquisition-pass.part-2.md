---
docType: review
layer: project
reviewType: tasks
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 930716e8bd81ff0e94e7e57c0282043d87bef025
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
squadronVersion: 0.15.1
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success-criteria coverage and traceability"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Submit-refusal branches (4xx, 429) have no test"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:826-831"
  - id: F003
    severity: concern
    category: test-coverage
    summary: "\"Download before any submit\" ordering and the ENOSPC path are not tested"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md:227-239"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Walkthrough step 6 has no fallback when the PM has not set the ceilings"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md:359-378"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "20.2 is large and mixes distinct activities"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md:359-378"
  - id: F006
    severity: concern
    category: task-sizing
    summary: "18.1 is the largest single implementation task"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md:276-290"
  - id: F007
    severity: note
    category: consistency
    summary: "18.3 duplicates file creation from Part 1"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md:315"
  - id: F008
    severity: note
    category: process
    summary: "Commit checkpoints are well distributed"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-2.md"
  - id: F009
    severity: note
    category: ci
    summary: "CI gating and load tests are not applicable"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:1217-1238"
---

# Review: tasks — slice 223

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success-criteria coverage and traceability

The criteria Part 2 owns map to tasks:
- FR3 (four definition requests) is covered by 13.2.
- FR4 (ceilings, 30-day cap, space) is covered by 14.2.
- FR5 (unknown submit, deadline sweep, unknown state) is covered by 16.3 and 18.2.
- FR6 (holes, condition reopen, five-attempt exhaust) is covered by 15.2 and 16.3.
- FR7 is covered by 17.2.
- FR8 and FR10 are covered by 18.2 cases 4 and 5.
- FR9 is covered by 11.2.

Every task traces to a criterion or to a named technical decision. Part 1 already supplies the reopen and reset functions that 15.1 depends on, so the section order has no forward dependency. No load test is needed, because the slice restates no NFR.

### [CONCERN] Submit-refusal branches (4xx, 429) have no test

Technical Decision 9 sets three outcomes for a refused submit:
- A 4xx other than 429 exhausts the units, and the phase continues as `partial`.
- A 429 leaves the units `FAILED_RETRYABLE` and aborts the phase as `provider_abort`.
- `ProviderOutcomeUnknownError` stops further submits and ends as `provider_abort`.

Task 18.1 mentions "TD9 outcome rules", but the 18.2 cases only exercise the unknown-outcome path. These branches decide whether money is spent, so add fake-provider cases to 18.2 for the 4xx path, the 429 path, and "stops submitting after an unknown".

### [CONCERN] "Download before any submit" ordering and the ENOSPC path are not tested

FR5 requires that delivered jobs download in deadline order before any submit. 16.3 tests the deadline order inside `advance`. Nothing asserts that reconcile's download precedes the first `submit_batch` in a single pass, for example by checking the fake provider's recorded call order in 18.2.

The slice's failure-modes table says an `OSError` such as ENOSPC ends the run as `storage_abort` with no attempt counted. 16.2 implements this, but 16.3 has no test for it. The same applies to the purchase-phase pre-submit archive free-space refusal: 14.2 tests only the pure `evaluate_space`. Add a case for the OSError path and one for the wiring of the free-space check in 18.2.

### [CONCERN] Walkthrough step 6 has no fallback when the PM has not set the ceilings

The slice says that without the ceilings, step 6 moves to 225. Task 20.2's success line is "every step matches", and steps 7 and 10 depend on step 6's four definition jobs. Add an explicit branch: if the ceilings are unset, run steps 5, 7 (as a no-op check) and 10 on the two adopted jobs only, record step 6 as deferred to 225, and skip the listing-lag and definition-window observations. As written, an implementer either blocks or improvises.

### [CONCERN] 20.2 is large and mixes distinct activities

20.2 bundles:
- the estimate and refusal checks;
- a live purchase with the possible STOP conditions;
- an idempotence re-run;
- the rebuild into a second database;
- teardown with `pg_database` confirmation;
- recording the listing-lag finding and possibly re-setting `TICK_SUBMIT_RESOLVE_AGE`.

Consider splitting it into (a) steps 5 and 6 with the live-purchase findings and (b) steps 7 and 10 with teardown. Then a step 6 failure does not leave teardown ambiguous. Add a commit after (a) so the findings are checkpointed before the databases are dropped.

### [CONCERN] 18.1 is the largest single implementation task

18.1 builds five phases, the purchase flow (calendar, planner, cost, guards, ordered insert-submit-record) and the error mapping, all at effort 4. The 300-line split is conditional, "if over ~300 lines". The purchase phase alone will almost certainly exceed the limit, so make the split unconditional: `purchase_phase.py` as its own task, with the reconcile, availability, await and definitions phases in the other. This also gives the pass tests a natural intermediate commit.

### [NOTE] 18.3 duplicates file creation from Part 1

Part 1's task 8.2 already creates `cli/commands/tick_pass_render.py`. 18.3 should say "extend" the file, and its success criterion should check the file's size after both additions. The "under ~300 lines" check is already there, so this is wording only.

### [NOTE] Commit checkpoints are well distributed

The tasks have commits after sections 11, 13, 14, 15, 16, 17, 18.2, 18.3, 19 and 20, and each test task follows its implementation task. Section 12 has no commit of its own and folds into section 13's commit, which is acceptable. Section 16 packs three tasks at effort 3, 4 and 3 into a single commit. An interim commit after 16.1's repository functions would match the slice's checkpoint-per-step intent.

### [NOTE] CI gating and load tests are not applicable

The slice's Technical Requirements restate no throughput or latency NFR, so no `tests/load/` task is required. The tick unit and integration tiers are already covered by 20.1.

### Run Digest

- Response length: 6033 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
