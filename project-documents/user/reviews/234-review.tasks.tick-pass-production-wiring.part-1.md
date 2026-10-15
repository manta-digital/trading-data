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
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: e0da9e17ed546c8f40e100c41d928bf79e3b4797
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
durationSeconds: 53.5
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "Task 3.3 changes code the LLD excludes, and a PM gate blocks Tasks 3.5–3.10 behind it"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:40-48"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 3.7 is too large for one junior-completable task"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:247-270"
  - id: F003
    severity: concern
    category: git-process
    summary: "`git diff main` ignores the configured integration branch"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:247"
  - id: F004
    severity: note
    category: coverage
    summary: "Two success criteria have no explicit verification step"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:293-308"
  - id: F005
    severity: note
    category: scope
    summary: "`--pm-designation` is an addition to the cutover script that the LLD does not mention"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:130-133"
  - id: F006
    severity: note
    category: proportionality
    summary: "Proportionality is acceptable but on the heavy side"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md"
  - id: F007
    severity: note
    category: commit-checkpoints
    summary: "Tasks 1 and 2 share one commit at 2.4"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:133-134"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success-criteria coverage and sequencing"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md"
---

# Review: tasks — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 3.3 changes code the LLD excludes, and a PM gate blocks Tasks 3.5–3.10 behind it

The LLD's Excluded list bars changes to "the tick pass logic". It also says the ingest pass already "accepts" a per-batch callback handed over by 225. The task file found that this is false in the code (`IngestInputs` has no such field and `_copy` reports nothing). Task 3.3 therefore adds the hook to the ingest pass and worker.

The task file handles this honestly by flagging it for PM confirmation before 3.3 starts. Three things are still unresolved:
- Tasks 3.5 to 3.10 and the `pass_runs` heartbeat criterion (Technical Requirement 4) all depend on 3.3. If the PM declines, or the workers turn out to be processes, no fallback is written.
- The LLD still carries the incorrect claim, and no task corrects it.
- The confirmation is buried in the Context Summary rather than being an explicit first step.

Recommendation: make the PM confirmation an explicit pre-implementation checkbox, ideally before Task 1. Add a sub-item to 3.3 or 8.4 that corrects the LLD's Data Flow step 2 and Excluded wording once the reading is confirmed. State what happens to 3.5 to 3.10 if the PM declines.

### [CONCERN] Task 3.7 is too large for one junior-completable task

Task 3.7 bundles several separable behaviours:
- verb registration;
- the recorder open/progress/close flow;
- ingest always following acquisition;
- the preflight and lock-held refusal path, where the other pass is still attempted and the exit is 1 with `FAILED`;
- exception handling that re-raises after closing the row;
- best-effort recorder behaviour;
- summary rendering.

Task 3.8 then tests all of them in one pass. The effort-4 rating reflects this. A junior would be better served by two steps, each tested straight after it:
- **Happy path:** open, run both passes, fold, close, and the exit code.
- **Failure semantics:** the refusal paths, unexpected exceptions and the down-primary case.

The two steps can still share one commit at 3.10.

### [CONCERN] `git diff main` ignores the configured integration branch

Task 9.1 says `git diff main` to check for swept formatting changes. CLAUDE.md says the target is whatever `cf config get git.integration_branch` returns, and `main` only when that is empty. Branches fork from the target, so diffing against `main` could show unrelated changes, or hide them, when an integration branch is set. The Context Summary in part 1 also says the branch is "created from the target". Replace `main` with the target, read from config.

### [NOTE] Two success criteria have no explicit verification step

Functional Requirement 1 includes "`mt-run follow tick` tails it". Task 5.2 adds `tick` to `KINDS`, but neither 5.3 nor 10.3 checks `follow tick`. Functional Requirement 2 says phases appear in `mt data overview` while the run is in progress. Task 3.8 uses a fake recorder, and 10.3 checks the overview only after the firing. Adding `mt-run follow tick` to 10.3, and an overview read during the supervised firing, would close both.

### [NOTE] `--pm-designation` is an addition to the cutover script that the LLD does not mention

The required argument is not in LLD TD4. It is justified by the CLAUDE.md rule on destructive statements against production, since migration 060 drops and re-adds a constraint. Treat it as a deliberate addition and fold it into the LLD's TD4 step list during 10.4, so the design and the script do not drift apart.

### [NOTE] Proportionality is acceptable but on the heavy side

The slice is rated effort 3/5 and the breakdown has 10 tasks, about 45 sub-items, and two task files. Most of that weight comes from items the LLD requires: the heartbeat, the orphan-row test, the migration test, the cutover tests, and the three standing 220 obligations. Trim where possible:
- The "ruff and mypy clean on touched files" success lines repeat in nearly every task and are also covered by Task 9.1.
- Task 1.3's grep-and-handle bullets over-specify how to find callers.

Nothing here needs to change before work starts.

### [NOTE] Tasks 1 and 2 share one commit at 2.4

The note explains why: `PassKind.TICK` without migration 060 breaks the every-kind seeding tests. A dozen sub-items land in one checkpoint, which is acceptable. After that, commits come roughly every task or two (3.2, 3.4, 3.6, 3.10, 4.6, 5.4, 6.3, 7.1, 7.3, 7.5, 8.5, and so on).

### [PASS] Success-criteria coverage and sequencing

- **Functional criteria 1–7:**
  - Tasks 3.7 and 5.2 cover `mt-run tick` and the `pass_runs` row.
  - Task 3.8 covers ingest after `REFUSED`, `PROVIDER_ABORT` and `IN_FLIGHT`.
  - Tasks 1.3 and 1.4 cover the overview and API.
  - Tasks 4.3 to 4.5 cover both health rows.
  - Task 6 covers `add-tick-env.sh`.
- **Technical requirements:**
  - Task 2.2 covers migration 060.
  - Task 1.4 covers the manual-iff-no-timer test.
  - Task 5.3 covers the deploy tests.
  - Task 3.2 covers the fold and map exhaustiveness.
  - Task 3.6 covers the heartbeat.
  - Task 3.9 covers the orphaned row.
  - Task 2.3 covers `openapi.json`.
  - Tasks 9.1 and 9.2 cover static checks and the test tiers.
  - Tasks 8.1 to 8.4 cover the runbook, the standing obligations and the architecture revisions.
- **Integration requirements:** Tasks 10.2 and 10.3 cover the cutover report and the `/api/v1/futures/*` check.
- **Dependencies:** all run forward and none are circular. The `PassKind.TICK` and migration 060 ordering, the deferred tick-service half of the manual-iff-no-timer test (1.4 then 5.3), and `cutover_common.migrate(track)` (7.1) before the cutover steps (7.2) are all handled. No task merges a branch, which matches the Phase 7 rule. No NFR is restated in the slice, so no load-test or CI-gating task is needed.

### Run Digest

- Response length: 7005 chars
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
- Duration: 53.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
