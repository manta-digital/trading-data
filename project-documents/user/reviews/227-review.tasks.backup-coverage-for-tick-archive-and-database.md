---
docType: review
layer: project
reviewType: tasks
slice: backup-coverage-for-tick-archive-and-database
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20261004
dateUpdated: 20261004
reviewedSha: 27d395553c7e7b72edb8c7b395eb82f9331b5596
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 40.6
squadronVersion: 0.18.4
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria 1–9 trace to tasks"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:67-440"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "\"Missing required argument exits 2\" has no test"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:407-416"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Task 7.3 is out of sequence with 7.4 and Section 8"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:407-439"
  - id: F004
    severity: concern
    category: requirements-coverage
    summary: "Lifecycle-MISSING fallback is not kept out of the exit code"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:249-262"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "Task 4.1 is too large; three implementation tasks precede their tests"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:226-278"
  - id: F006
    severity: concern
    category: scope
    summary: "Task 2.2 adds an unrequested refactor"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:141-149"
  - id: F007
    severity: note
    category: process
    summary: "Release tag and code review gate are referenced but not tasked"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:427-431"
  - id: F008
    severity: note
    category: verification
    summary: "Live checks run as manta while the walkthrough uses sudo"
    location: "project-documents/user/tasks/227-tasks.backup-coverage-for-tick-archive-and-database.md:279-286"
---

# Review: tasks — slice 227

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria 1–9 trace to tasks

- SC1 (table and hard errors): tasks 1.1–1.5, 4.1, 4.2 and 4.4.
- SC2 (tick archive settings and forced WAL switch): 4.1, then steps 5 and 6 in 6.2 and 6.3.
- SC3 (cron file): 3.2 and 3.3.
- SC4 and SC5 (base backup, metadata dump, offsite check): steps 8, 9 and 11 in 6.3.
- SC6 (arm file, created only after the final check passes): step 8, tested in 6.4.
- SC7 (health `PASS … FLAGS archive=0 stale=0`): step 10.
- SC8 (runbooks 200 and 210): 7.1 and 7.2.
- SC9 (production untouched): the config hash in steps 1 and 12, and the render comparison in 3.3.

The slice states no NFR with a load-test requirement, so no `tests/load/` task or CI gating task is needed.

### [CONCERN] "Missing required argument exits 2" has no test

The slice's Technical Requirements list "the wrappers' new required arguments (missing → exit 2)" as a unit test. Task 2.0 makes `--url-key` and `--remote` optional until the cutover. Task 7.3 makes them required and says a missing one exits 2 with usage. But 7.3 only drops the absent-form tests and adds none.
- Fix: have 7.3 replace the 2.4 absent-form tests with tests that each wrapper exits 2 with usage when `--url-key` is missing (and `--remote` for metadata).

### [CONCERN] Task 7.3 is out of sequence with 7.4 and Section 8

Task 7.3 sits in Section 7, before the full validation in 7.4. But it can only run after the PM's cutover (8.1) and the acceptance read (8.2). 8.2 ends with "Then do 7.3", so the order loops back.
- Result: 7.4 (full unit and integration validation, ruff/mypy/shellcheck, `cf check`) runs before the code change that most alters wrapper behavior. The validated tree is not the final one.
- Fix: move 7.3 to the end of Section 8, as 8.3. Re-run the touched-file validation after it, or place 7.4 after it.

### [CONCERN] Lifecycle-MISSING fallback is not kept out of the exit code

TD10 says a missing B2 lifecycle rule must not affect the cutover's exit code, because it costs storage, not data. Task 4.3 does not say whether `setup-backup.sh` apply returns non-zero when it reports `MISSING lifecycle … (add in the B2 console)`. Task 6.2 step 4 stops the cutover on any failure of `sudo deploy/setup-backup.sh`. Step 13 says "exit 0 only if every step passed" and only asks for the lifecycle line to be included in the report.
- Result: in the likely fallback path, the cutover could stop at step 4 on a non-fatal condition.
- Fix: add to 4.3 that a lifecycle MISSING that cannot be applied is reported but is not a failure exit. Add to 6.3 or 6.4 that step 13 reports the console rule without failing. Add a test for each.

### [CONCERN] Task 4.1 is too large; three implementation tasks precede their tests

Task 4.1 (effort 4) removes `--cluster` and changes the meaning of `--backup-root`. It also does the per-row directories, ACL and settings, the host-once steps, the arm-file reporting and the output prefixing.
- Tasks 4.1–4.3 are three implementation tasks, and the tests come only in 4.4. This weakens the test-with pattern.
- Suggested split: (a) the arguments, table parse and per-row loop for directories and settings, followed by tests; (b) the host-once steps and arm file; (c) 4.2 with its tests; (d) 4.3 with its tests.

The same pattern appears in 6.1–6.3, where eight cutover steps precede 6.4's tests. Consider adding tests after 6.2. Task 6.3, which covers steps 6–13, could also be split.

### [CONCERN] Task 2.2 adds an unrequested refactor

Task 2.2 replaces the `grep | sed | tr` URL read with the shared `env_value` helper. The slice design asks only for `--url-key` and `--remote`. The swap is defensible, since it avoids duplicating the read logic and it touches a production wrapper. It changes production behavior beyond the stated scope.
- Fix: either note it as a deliberate DRY change and require a test that the old and new reads produce identical values for the production key, or drop it.

### [NOTE] Release tag and code review gate are referenced but not tasked

The slice's Verification Walkthrough says the PM runs the cutover "after tagging the release". Task 8.1 says "after the code review". Neither the tag nor the review is a task, and the two documents give different gates. Align the wording in 8.1 with the walkthrough.

### [NOTE] Live checks run as manta while the walkthrough uses sudo

Tasks 4.5 and 8.2 run `setup-backup.sh --check` as manta. The slice's walkthrough runs it with sudo. The tasks justify this by the no-root constraint and expect OK only for items readable without root. Task 8.2 accepts "items that need root to read". The PM would need to run the sudo form for full acceptance of SC9 and SC10. 8.1 or 8.2 could say so explicitly.

### Run Digest

- Response length: 6096 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 40.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
