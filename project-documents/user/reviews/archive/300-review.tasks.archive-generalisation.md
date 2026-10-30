---
docType: review
layer: project
reviewType: tasks
slice: archive-generalisation
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/300-tasks.archive-generalisation.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261030
dateUpdated: 20261030
reviewedSha: 9159c844cab83042c114b093bee11a9273ec2477
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 103468
cachedTokens: 8820
cacheWriteTokens: 94644
completionTokens: 5193
durationSeconds: 45.4
costUsd: 0.432145
costBasis: provider
runId: run-20261030-p5-dbc05e02
squadronVersion: 0.24.9
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and test-with pattern"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md"
  - id: F003
    severity: concern
    category: spec-consistency
    summary: "`is_plan_included` empty/inverted-range behaviour is tested but never specified"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:146-151"
  - id: F004
    severity: concern
    category: commit-checkpoints
    summary: "No commit checkpoint for fixes and conversions found in group 8, and a baseline recorded in a later commit message"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:233-262"
  - id: F005
    severity: concern
    category: proportionality
    summary: "Duplicated long-running validation and over-granular bookkeeping tasks"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:59-66"
  - id: F006
    severity: note
    category: sequencing
    summary: "Interim value for `PlannedCost.included` between 6.1 and 6.4"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:183-185"
  - id: F007
    severity: note
    category: risk
    summary: "Open-ended import-cycle branch in 4.1"
    location: "project-documents/user/tasks/300-tasks.archive-generalisation.md:139"
---

# Review: tasks — slice 300

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage

- Criterion 1 (parity) maps to 8.4 and 8.5.
- Criterion 2 maps to 2.6.
- Criterion 3 maps to 3.4 and 3.5.
- Criterion 4 maps to 5.3 and 5.4.
- Criteria 5 and 6 map to 6.2 and 6.5. Unheld jobs are tested through `purchase_plan`, not only `evaluate_spend`.
- Criterion 7 maps to 7.1 and 7.2.
- Criterion 8 maps to 4.2 and 4.4, plus the 8.6 walkthrough.

The unit tests the design lists are all present: family totality and disjointness, inclusion-table totality, `is_plan_included` cases, `evaluate_spend`, and registry keys equal to `STORED_TIERS`. So are the integration tests: `tick_008` on empty and populated databases, family supersession, and both writers. The slice states no NFR, so no load-test or CI-wiring task is owed. Running the existing load-tier tick tests covers "unchanged behaviour". The design's end-of-slice grep is 8.1, and its mitigation unit test (a test-only extra family) is in 2.3.

### [PASS] Sequencing and test-with pattern

- The groups follow the design's suggested order, and there are no circular dependencies.
- `tier_rank_sql` is removed in 2.5, after both consumers switch in 2.4.
- The migration (5.1) comes before the trailing read that filters on the new column (6.3).
- The single-call-site rule is stated honestly: 5.3 computes the value at submit as an interim step, and 6.4 moves it onto the plan item.
- Each implementation task or small group is followed by its tests: 2.1–2.2 by 2.3, 3.x by 3.5, 4.3 by 4.4, 5.1 by 5.2, 5.3 by 5.4, and 6.4 by 6.5.

### [CONCERN] `is_plan_included` empty/inverted-range behaviour is tested but never specified

Task 4.4 requires that "an empty or inverted range is refused explicitly rather than reported as included". Task 4.3 defines the function without any such rule, and D7 in the slice design doesn't mention it. An implementer following 4.3 would write a function that fails the 4.4 test, or would have to invent the error type.

The refusal is consistent with the no-silent-fallback rule. Add it to 4.3 (raise `ValueError` when `end_exclusive <= start`) and keep the test. Alternatively, drop the case from 4.4. Either way, 4.3's "under 60 lines" success line should be checked against the final shape.

### [CONCERN] No commit checkpoint for fixes and conversions found in group 8, and a baseline recorded in a later commit message

Groups 2–7 each end in a commit, which is good. Group 8 has no commit until 8.7, and that commit is `docs:`.
- 8.1 says any missed tier-name use is converted "with a test". 8.2 can also force fixes. Both would produce code and test changes that are either uncommitted until the docs commit or mislabeled as `docs:`.
- Add a conditional commit step to 8.1/8.2 for those changes, for example `refactor:` or `fix:`.
- 1.1 asks for the baseline pass/fail counts to appear in "the first commit message body of group 2". That ties a record to a commit made much later. Record the counts in the task file next to the parity heading instead, or in 1.2's note, so the result survives if group 2's commit is squashed or reworded.

### [CONCERN] Duplicated long-running validation and over-granular bookkeeping tasks

- 1.1 runs the load-tier tick tests (about 77 minutes) as a baseline, and 8.2 runs them again. The baseline is only needed to explain failures. A cheaper plan is to run the load tier once in 8.2. If anything fails, rerun just that test in the base worktree already built for 8.4 to see whether the failure is pre-existing. That saves a 77-minute run and removes the dependency on a remembered list of "known pre-existing failures".
- 1.2 (record the base hash) is one command and can merge into 1.1.
- 8.3 (file-size check) can fold into 8.2, since it is a `wc -l` pass with its output recorded in 8.7's commit body.

None of this blocks the plan. The cuts are one baseline load run and two micro-tasks.

### [NOTE] Interim value for `PlannedCost.included` between 6.1 and 6.4

6.1 makes `included` a required field and says to "update every constructor call". `purchase_plan` can only supply a real value in 6.4. The implementer must pass `False` at those sites for the interim, which equals today's behaviour. State that in 6.1 so it isn't read as a hidden default. 6.4's success line ("no second call site computes inclusion") then covers the removal.

### [NOTE] Open-ended import-cycle branch in 4.1

4.1 says to stop and report if `config` importing `TickSchema` closes an import cycle. The stop condition is reasonable, and the design places the table in `config/`. The task should name the likely resolution so a junior implementer isn't blocked, for example importing under `TYPE_CHECKING` or placing the enum in a leaf module. This is only advice, because the design doesn't say which.

### Run Digest

- Response length: 5840 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 103468 / 8820 / 94644 / 5193 / not computed
- Duration: 45.4 s
- Cost: $0.43 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
