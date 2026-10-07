---
docType: review
layer: project
reviewType: tasks
slice: tick-restore-drill
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/228-tasks.tick-restore-drill.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20261006
dateUpdated: 20261006
reviewedSha: 81eaeeae46681216d794847d7d515745344b8199
revision_number: 3
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 56
turns: 20
promptTokens: 1338865
cachedTokens: 1048064
completionTokens: 134449
reasoningTokens: 128297
durationSeconds: 1304.2
runId: run-20261007-p5-1e5c3afc
squadronVersion: 0.20.1
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Archive count/bytes comparison does not account for the `.partial` exclusion"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F002
    severity: concern
    category: test-sequencing
    summary: "Test tasks 5.3 and 5.6 bundle two implementations, so 5.1's and 5.4's tests do not immediately follow them"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:287"
  - id: F003
    severity: concern
    category: task-scoping
    summary: "Task 6.2 implements `rebuild_env()` inside a task scoped as unit tests"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:355"
  - id: F004
    severity: concern
    category: process-consistency
    summary: "Task 9.2 edits a Technical Decision's wording while 7.4 says design changes require the Project Manager"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:524"
  - id: F005
    severity: concern
    category: conventions
    summary: "Commit checkpoint 5.11 is the only one without a checkbox"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:343"
  - id: F006
    severity: pass
    category: verification
    summary: "No NFR, load test, or CI-gating task is missing"
    location: ".github/workflows/ci.yml"
  - id: F007
    severity: pass
    category: sequencing
    summary: "Sequencing and dependency graph are sound"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
---

# Review: tasks — slice 228

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Archive count/bytes comparison does not account for the `.partial` exclusion

Tasks 5.2 and 6.4 compare the live archive against the restic listing by raw "file count, total bytes" (6.4: "compare file count and bytes with the live archive"). The system backup deliberately excludes unfinished copies: `project-documents/user/runbooks/200-backup-and-restore.md:588` states `deploy/restic-excludes.txt` excludes `/data/tick-archive/**/*.partial` ("an unfinished download or adoption copy is not the record"), and the slice-223 verifier encodes that asymmetry explicitly (`scripts/verify_tick_archive_backup.sh:14`, "file count == the archive's own file count"). A `.partial` file present in the live archive at drill time therefore makes the restored copy smaller than the live archive by construction, and both 5.2 and 6.4 fail on a healthy backup. The success criteria should be "the live archive minus `**/*.partial`", stated once. This gap is inherited from the slice design (TD1 steps 1 and 3), so fixing it in either place should be mirrored.

### [CONCERN] Test tasks 5.3 and 5.6 bundle two implementations, so 5.1's and 5.4's tests do not immediately follow them

The test-with pattern holds for most of the file (2.1→2.2, 3.2→3.3, 3.4→3.5, 3.6→3.7, 4.2→4.3, 4.4→4.5, 4.6→4.7, 5.7→5.8, 5.9→5.10), but two test tasks each cover two separate implementations. Task 5.3 is titled "Unit test for 5.2 and the sudo wrapper" and carries the only test for 5.1's `sudo -n`-expiry error, and task 5.6 covers both 5.4's `empty_auto_conf` guard and 5.5's config writer. So 5.1's success criterion ("a stubbed `sudo -n` returning exit 1 yields the named error") and 5.4's ("a file containing `archive_command` is truncated and passes; a stub that leaves a line behind raises") are only exercised two tasks later. Splitting the wrapper/guard cases into their own test tasks keeps each implementation task immediately provable.

### [CONCERN] Task 6.2 implements `rebuild_env()` inside a task scoped as unit tests

Task 6.2 is titled "Unit tests: report and exit status; URL environments", but its second bullet is a new implementation: "Add `rebuild_env()`: returns the subprocess environment for step 6". A later task (7.1) depends on that function existing, yet it is introduced as a sub-item of a test task, which muddles "is this task about writing tests or code?" and makes the success criterion ("tests pass") not obviously the completion signal for the function. Either move `rebuild_env()` into 6.1's skeleton task or retitle 6.2 to name both deliverables.

### [CONCERN] Task 9.2 edits a Technical Decision's wording while 7.4 says design changes require the Project Manager

Task 7.4 correctly states the rule: "Widening TD4's set is a design change: ask the Project Manager." Task 9.2 then instructs the implementer to "Edit the design's wording to 'both tick advisory locks'" — a change to TD1's text, not merely its status. Both are design edits, and the two tasks treat the same class of action inconsistently. The substantive fix (design says one lock, code holds two) is worth making, but 9.2 should route it through the same confirmation gate 7.4 names.

### [CONCERN] Commit checkpoint 5.11 is the only one without a checkbox

Section 5 renders its checkpoint as `- **5.11 Commit checkpoint** - ...`, whereas the other eight checkpoints use `- [ ] **X.Y Commit checkpoint**`. The project convention is explicit: "Use checklist format for all task files. Each item and subitem should have a `[ ]` 'checkbox'." A checkbox-less item is invisible to the `task-checker` delegation the convention relies on.

### [PASS] No NFR, load test, or CI-gating task is missing

The slice design restates no non-functional requirement (no latency, throughput, or scale target appears in its Requirements sections; the only cost figures are informational measurements). The task file states this and justifies the omission at line 55 — a manual quarterly script has no load-bearing NFR to gate. Independently verified that the repository's single workflow, `.github/workflows/ci.yml`, only publishes to PyPI on `v*` tags and gates no test tier, so there is no existing CI gate this slice's tests should have been wired into. No task is owed here.

### [PASS] Sequencing and dependency graph are sound

Section order follows the design's Implementation Notes (fingerprint/bookkeeping → lifecycle → host primitives → steps 0–5 → steps 6–9 → runbook → validation). Dependencies are respected: `compare_bookkeeping` (3.4) builds on 3.2, the drill skeleton (6.1) precedes the step functions, 6.8's real-format fixtures depend on 6.7's host run, and host-only verification (7.4, 7.5) precedes the runbook record row (8.3). No circular dependencies, and the single cross-slice prerequisite (227) is declared in the frontmatter and satisfied per the project state block.

### Run Digest

- Response length: 6635 chars
- Response is newline-free: no
- Tool calls made: 56
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 467305
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1338865 / 1048064 / 134449 / 128297
- Duration: 1304.2 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
