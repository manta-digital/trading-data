---
docType: review
layer: project
reviewType: tasks
slice: tick-pass-production-wiring
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 6cc42bcd731b61d42aaa1332807792d4119e0645
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 52.0
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success-criteria coverage across both parts"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 7 is oversized, and its tests and commit are batched at the end"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:107-178"
  - id: F003
    severity: concern
    category: safety
    summary: "The production migration needs a recorded Project Manager designation"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:135-137, 263-275"
  - id: F004
    severity: concern
    category: safety
    summary: "Task 10.3 starts real tick passes with no purchase guard"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:277-285"
  - id: F005
    severity: concern
    category: sequencing
    summary: "The release-ref task never says who pushes"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:256-261"
  - id: F006
    severity: concern
    category: hallucination-trap
    summary: "A hardcoded byte value sits next to a \"parse from the unit\" instruction"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:157-160"
  - id: F007
    severity: concern
    category: task-clarity
    summary: "Task 9.3 contradicts itself about recording known failures"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:246-250"
  - id: F008
    severity: note
    category: consistency
    summary: "A forward reference between parts uses inconsistent task numbers"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:63-64"
  - id: F009
    severity: note
    category: scope
    summary: "Scope and outward-facing actions to confirm"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:223-226, 291-296"
  - id: F010
    severity: note
    category: proportionality
    summary: "Proportionality is acceptable"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md"
---

# Review: tasks — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success-criteria coverage across both parts

Each LLD criterion that falls in part 2 maps to a task:
- **Functional 7 (`add-tick-env.sh`):** Tasks 6.1–6.2.
- **Technical 3 (deploy tests):** Task 5.3.
- **Technical 6 (static checks and test tiers):** Task 9.
- **Technical 7 (runbook 100):** Task 8.1.
- **Technical 8 (Kalshi contract diff and slice-mapping rows):** Tasks 8.2–8.3.
- **Integration 1 (cutover report):** Tasks 7 and 10.2.
- **Integration 2 (`mt-serve` serving `/api/v1/futures/*`):** Task 10.3.
- **Architecture supersession and the realtime-paths and API answers:** Task 8.4.

Criteria in part 1 (Functional 1–6, Technical 1, 2, 4 and 5) are covered there, including the openapi regeneration in Task 2.3. The slice states no NFR, so no load test or CI-gating task is required. No task fails to trace to a criterion.

### [CONCERN] Task 7 is oversized, and its tests and commit are batched at the end

Task 7 (effort 4) holds four pieces of work with one commit and no tests until 7.4:
- a refactor of shared `cutover_common.migrate`, which has existing callers;
- nine cutover steps, split across 7.2 and 7.3;
- one test task covering every step;
- a single commit, 7.5.

This breaks the test-with pattern, since 7.1–7.3 have no tests directly after them. It also puts a behaviour-preserving refactor (7.1) and new feature code into one `feat:` commit. Suggested fix:
- Make 7.1 its own `refactor:` commit with the `migrate` track test.
- Split 7.4 so steps 1–5 are tested right after 7.2 and steps 6–9 right after 7.3, each with a commit.
- Alternatively, split Task 7 into 7a (generalise `migrate`) and 7b (the script).

### [CONCERN] The production migration needs a recorded Project Manager designation

Step 3 runs migration 060 against the production minute database. It is an `ALTER … DROP/ADD CONSTRAINT`. CLAUDE.md allows destructive or ALTER statements only on a database the process created or one the Project Manager explicitly designated. The task says the single Project Manager act is the `add-tick-env.sh` run, and "Every other step needs no Project Manager action." That claim conflicts with the rule for the migration (and the `mt-serve` restart). Add an explicit precondition to 10.2: the Project Manager confirms the cutover may migrate production. Alternatively, cite where the existing 227 and 921 cutover pattern records that standing authorization.

### [CONCERN] Task 10.3 starts real tick passes with no purchase guard

The estimate-only guard protects only the cutover's step 8 firing. Task 10.3 then runs `sudo mt-run tick` and a concurrent double start, which are full-universe passes with production spend ceilings set. The slice's own risk section says availability can move between check and firing, so a later run can buy. Nothing in 10.3 re-runs the estimate-only check first or says what to do if a run purchased. Add a pre-run estimate-only step, plus an instruction to record each run's purchase count and stop if it is non-zero. The double-start check does not need two real passes. It could use the already-active unit state or a run that finds nothing wanted.

### [CONCERN] The release-ref task never says who pushes

Task 10.1 says "Confirm the slice commits are pushed to origin", but no task performs the push. Step 1 of the cutover requires the ref on origin, and a push is outward-facing. State it explicitly. Either "push the slice branch (with Project Manager approval)" or "stop and ask the Project Manager to push if it is not on origin".

### [CONCERN] A hardcoded byte value sits next to a "parse from the unit" instruction

Step 8 says to compare `memory.max` with "the value parsed from the installed `manta-acquisition.slice` `MemoryMax` (14G = 15032385536 bytes)". This is the trap in CLAUDE.md: if the parse returns nothing, the nearby literal becomes the expected value and the check passes against a constant. Remove the byte figure from the instruction. Make the step fail if the parse yields nothing, and keep 14G only as a test-fixture value in 7.4.

### [CONCERN] Task 9.3 contradicts itself about recording known failures

Task 9.2 requires the known failures seen to be "recorded in the commit message body". Task 9.3 commits "only if fixes were needed", otherwise noting the result "in the next commit body". If no fixes are needed, there is no commit to carry the record. Task 9 is also mostly a re-run of the per-task checks, so consider folding it into Task 10. Alternatively, record the result in the cutover report or closeout commit and drop the conditional commit.

### [NOTE] A forward reference between parts uses inconsistent task numbers

Part 1 Task 1.4 says "Task 5.2 confirms tick is included" in the manual-iff-no-timer test. In part 2 that confirmation sits in Task 5.3. Align the reference so the check is not missed.

### [NOTE] Scope and outward-facing actions to confirm

- **CHANGELOG (8.5):** the CHANGELOG entry is not in the LLD scope. It is harmless and bounded by "do not bump a version", but it is an addition.
- **GitHub issues (10.4):** closing issue #33 and commenting on #34 are outward-facing. They are instructed explicitly, but they should wait for the cutover report to show `memory.max` in force and `oom_kill` 0. The task already ties them to the report.

### [NOTE] Proportionality is acceptable

Ten tasks across two files for a slice with a nine-step production cutover is reasonable. Tasks 5.1–5.2 (the unit file and the `UNITS` and `KINDS` registration), 6 and 8 are well sized. The tests in 5.3, 6.2 and 7.4 each trace to a design requirement. The cutover tests in 7.4 are numerous, but each lists a behaviour from TD4.

### Run Digest

- Response length: 7239 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 52.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
