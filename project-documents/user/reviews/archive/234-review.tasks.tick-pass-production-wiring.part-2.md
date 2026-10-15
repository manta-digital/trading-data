---
docType: review
layer: project
reviewType: tasks
slice: tick-pass-production-wiring
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: e0da9e17ed546c8f40e100c41d928bf79e3b4797
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
durationSeconds: 28.4
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage across parts 1 and 2"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and test-with pattern"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:34-321"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task 7.2 bundles scaffolding and five cutover steps"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:121-149"
  - id: F004
    severity: concern
    category: task-sizing
    summary: "Task 7.1 touches unknown callers with weak scoping"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:109-119"
  - id: F005
    severity: concern
    category: process
    summary: "Task 9.2 ties the record of known failures to a commit that may not exist"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:256-259"
  - id: F006
    severity: note
    category: scope
    summary: "Proportionality and scope"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:57-66"
  - id: F007
    severity: note
    category: safety
    summary: "Destructive-DB gating is handled"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:130-133"
  - id: F008
    severity: note
    category: coverage
    summary: "Verification of TD2 double start is placed in execution, not as an automated test"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:301-304"
---

# Review: tasks — slice 234

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage across parts 1 and 2

Part 2 covers every slice criterion that is not in part 1.
- Functional 7 (`add-tick-env.sh`) is covered by Tasks 6.1–6.2.
- Technical 3 (deploy tests) is covered by 5.3.
- Technical 6 (static checks and test tiers) is covered by 9.1–9.2.
- Technical 7 (runbook 100) is covered by 8.1.
- Technical 8 (the 220 obligations) is covered by 8.2–8.3.
- Integration 1 and 2 (cutover report, `/api/v1/futures/*`) are covered by 10.2–10.3.
- The superseded architecture statements are covered by 8.4.
- The cutover's nine steps in TD4 map one-to-one onto 7.2 and 7.4.

I checked part 1 only by grep. It carries `openapi.json` regeneration, the heartbeat, the abandoned-row test, `units_near_deadline` and the manual-iff-no-timer test, so I found no gap between the two parts.

### [PASS] Sequencing and test-with pattern

Dependencies run in order: unit and kinds (5), then env script (6), then cutover script (7), then docs (8), then validation (9), then execution (10). Each implementation task is followed by its test task. The commit checkpoints are spread across the file: 5.4, 6.3, 7.1, 7.3, 7.5, 8.5, 9.3 and 10.4. No dependency is circular. The LLD's step 7 → step 8 adjacency is kept by 7.4 and tested in 7.5. The merge is correctly left to Phase 7 (10.4).

### [CONCERN] Task 7.2 bundles scaffolding and five cutover steps

Task 7.2 covers the module scaffolding and report writer, timer hold and release, the `--pm-designation` gate, and cutover steps 1–5. Those steps include a chgrp/chmod pass, a migration and an install. Its only success criterion is "the module imports; a dry listing shows steps 1–5", so a junior AI gets no feedback until 7.3. Split off the scaffolding (STEPS runner, report, hold/release in `finally`, `--pm-designation`) as its own sub-task with a small test, then add the steps. Alternatively, move the 7.3 scaffolding tests ahead of the step implementations.

### [CONCERN] Task 7.1 touches unknown callers with weak scoping

The task says "others found by grep" and "check how `verify_migrate_target` and `_migrate_status` pick the track", so its size is unknown. The existing `scripts/cutover_*` files include 265, 267, 268, 921 and 922. The success criterion names only the 267, 921 and 227 tests, and not the 265, 268 and 922 callers. Add a requirement that every `cutover_*.py` caller is updated, and that each caller's test (if one exists) runs. If a caller has no test, state that.

### [CONCERN] Task 9.2 ties the record of known failures to a commit that may not exist

The known pre-existing failures are to be recorded "in the body of the Task 9.3 commit if one is made, otherwise in the Task 10.4 commit". That ties a validation record to a commit that is conditional and unrelated to it, and 10.4 is a docs commit. Record the list in the cutover or closeout notes instead, or in the report. This is minor.

### [NOTE] Proportionality and scope

The tests are scoped to the slice criteria. There are no unrelated test tasks and no load test, which fits because the slice restates no NFR. The 6.2 testing of the root-only chown/chmod through an injectable prefix is justified by Functional 7's `0640 root:manta-trading` requirement. The split into two task files is reasonable for a slice of this size. Every task traces to a slice criterion or to the standing 220 obligations, so I found no scope creep.

### [NOTE] Destructive-DB gating is handled

Migration 060 drops and re-adds a constraint on production. The task makes that step refuse to run without `--pm-designation` (7.2), and 10.1 requires the Project Manager's explicit designation. This complies with CLAUDE.md. The push to origin also waits on Project Manager confirmation, and the issue #33/#34 posts wait on confirmation too.

### [NOTE] Verification of TD2 double start is placed in execution, not as an automated test

The slice asks for "a deploy-tier or cutover check" that starts the unit twice and asserts one `TICK` row. Task 10.3 does this manually during execution. That satisfies the slice wording. Any future regression would not be caught automatically, but adding a test is not required by the slice.

### Run Digest

- Response length: 5065 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 28.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
