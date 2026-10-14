---
docType: review
layer: project
reviewType: tasks
slice: universe-expansion-gc
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/232-tasks.universe-expansion-gc.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: adcd1d75d212c9b106955d6a9bc21f922b405130
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
durationSeconds: 33.6
runId: run-20261014-p5-ea0063b3
squadronVersion: 0.21.7
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage is complete"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "PM-gated spend and merge handling are sequenced correctly"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:279-337"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Migration-chain tail test is fixed two tasks after the change that breaks it"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:160-190"
  - id: F004
    severity: concern
    category: process
    summary: "Documentation tasks 10.1–10.5 have no intermediate commit checkpoint"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:382-428"
  - id: F005
    severity: concern
    category: proportionality
    summary: "A few tasks are heavier than the slice needs"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:332-335"
  - id: F006
    severity: note
    category: risk
    summary: "Scratch sourcing list in 2.1 has no durable home"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:114-116"
  - id: F007
    severity: note
    category: process
    summary: "Production migration apply is an outward-facing step without an explicit PM go-ahead"
    location: "project-documents/user/tasks/232-tasks.universe-expansion-gc.md:281-289"
---

# Review: tasks — slice 232

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage is complete

Every Functional Requirement in the slice design maps to a task:
- FR1 maps to 3.2 (fresh database) and 7.1 (production).
- FR2 maps to 2.2 and 2.4.
- FR3 maps to 7.1.
- FR4 maps to 4.1–4.3.
- FR5 maps to 7.2, 8.1, 8.2 and 9.1.
- FR6 maps to 9.2.
- FR7 maps to 9.1 and 9.3.
- FR8 maps to 9.4.
- FR9 maps to 7.2 and 10.3.

The Technical Requirements are covered as well:
- Shared builders and an untouched 058 are covered by 1.1 and 1.2.
- ruff, mypy and the test tiers are in 6.1 and 10.5.
- The Kalshi parity test is in 0.1, 6.1 and 10.5.
- The Revision Log entry, the contract rows and the runbook are in 10.4, 10.1 and 10.2.

The ingest throughput bounds are checked in 8.2. The design states that no new load test is needed, so the absence of a load test and a CI-gating task is correct. I found no scope creep: 4.5 and 5.x trace to TD10 and TD8.

### [PASS] PM-gated spend and merge handling are sequenced correctly

Section 7 stops at the free estimate, and Section 8 has an explicit precondition that the PM has set both ceilings. No task edits a ceiling. The last task ends at a commit on the slice branch, and no merge is written as a task. Section 6 gates the work before anything touches production, and the recorder and test parametrization (Section 5) precede the GC fixtures (9.4).

### [CONCERN] Migration-chain tail test is fixed two tasks after the change that breaks it

Task 3.1 appends 059 to the migration list, which makes `test_chain_ends_at_058` fail. Task 3.2 (integration test) comes next, and the unit test is only repaired in 3.3. The test-with pattern wants the fix to follow its cause immediately. Move 3.3 up to directly after 3.1, or fold it into 3.1 as a sub-item, so no task finishes with a red test.

### [CONCERN] Documentation tasks 10.1–10.5 have no intermediate commit checkpoint

Five documentation and gate tasks end in a single commit at 10.6. Every earlier section has its own checkpoint, and the project rule is at least one commit per task. Add a commit checkpoint after 10.4 (the docs edits), for example `docs: record GC contract rows, runbook and plan updates`. 10.6 then commits only the LLD finalization.

### [CONCERN] A few tasks are heavier than the slice needs

- 8.3 is a message to the PM with no work in it. Merge it into 8.2 or 8.4 as a sub-item.
- 4.1 and 4.2 are each effort 1 and edit the same two constants; they could be one task.
- 10.5 re-runs the full gate that 6.1 already ran. After 6.1 the only code change is the GC fixture and test edit in 9.4, so 10.5 can be reduced to the diff-scope check plus a targeted re-run of the equivalence test, parity test and lint.

None of these affect correctness. They are trims to keep a 3/5 slice from carrying roughly 35 tasks.

### [NOTE] Scratch sourcing list in 2.1 has no durable home

Task 2.1 produces a "scratch list" that 2.2 consumes. Eight years of sourced rows would be costly to lose if the session is interrupted. Consider having 2.1 write the list to a file named in the task (for example in the Implementation Findings or an uncommitted scratch path) so 2.2 can resume from it.

### [NOTE] Production migration apply is an outward-facing step without an explicit PM go-ahead

Task 7.1 applies 059 to production. The migration is additive and idempotent, and the design includes the step. A one-line "confirm with the PM before apply" would still match the spend-gating style used elsewhere in the file. This is optional.

### Run Digest

- Response length: 4223 chars
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
- Duration: 33.6 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
