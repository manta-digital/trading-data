---
docType: review
layer: project
reviewType: tasks
slice: futures-history-acquisition
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/307-tasks.futures-history-acquisition.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261109
dateUpdated: 20261109
reviewedSha: fab137264f01cdf79e065397aa91bd25db65e25a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 115399
cachedTokens: 8831
cacheWriteTokens: 106564
completionTokens: 5082
durationSeconds: 42.6
costUsd: 0.478738
costBasis: provider
runId: run-20261109-p5-2407c667
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 3 tests need exit-rule wiring that is scheduled in Task 4.5"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:126-137"
  - id: F002
    severity: concern
    category: contract-gap
    summary: "`--estimate` has no start instant, but `judge_estimate` now requires one"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:73-75, 121-124, 133"
  - id: F003
    severity: concern
    category: test-coverage
    summary: "D6 item 6 (scale baseline extension) has no test and an open-ended implementation choice"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:155, 158-160"
  - id: F004
    severity: note
    category: scope
    summary: "Task 5.1 forbids other `src/` edits but also asks for docstring updates"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:177-178"
  - id: F005
    severity: note
    category: proportionality
    summary: "Granularity: separate test sub-tasks and a read-only task could be merged"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:51-52, 78-88, 158-170"
  - id: F006
    severity: pass
    category: coverage
    summary: "Success criteria coverage and commit placement"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:46-201"
---

# Review: tasks — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 3 tests need exit-rule wiring that is scheduled in Task 4.5

Task 3.2 says a failed-retryable request "is a failure in the exit rule (non-zero) with its id named". It also says a retry-exhausted request does not fail the exit rule, and that `_unit_is_final` in the evidence module accepts it. Task 3.3 tests those behaviours and ends at the commit `feat: handle failed purchase requests in the cutover verdicts`. The wiring into `final_failures` and the evidence module is only done in Task 4.5 (line 164), and tested in 4.6. A junior following the order has no way to make the 3.3 exit-rule tests pass at that commit, or must pull 4.5 work forward. Either move the `final_failures` / `_unit_is_final` changes into Task 3, or reword 3.2/3.3 so they cover only the classification function. Leave the exit-rule assertions to 4.5/4.6.

### [CONCERN] `--estimate` has no start instant, but `judge_estimate` now requires one

Task 1.5 says `--estimate` writes no start file or baseline. Task 3.1 changes `judge_estimate` to take the cutover's start instant. Task 3.3 says a resubmit with no start instant given "is a programming error (raises)". In `--estimate` mode, which still judges scope, the tasks never say where the start instant comes from. This matters most on the re-run after a failed-retryable request, where the estimate contains a resubmit. The first-run note in 3.1 also contradicts itself. It says the start instant "is recorded after the estimate is judged" and then "written ... before the estimate in `_steps`". Specify the rule. For example, `--estimate` reads the existing `{slice}-cutover-start.txt` if present and otherwise passes an explicit "no start" value that makes any resubmit refuse. Then add a test for it. State the first-run order once.

### [CONCERN] D6 item 6 (scale baseline extension) has no test and an open-ended implementation choice

Task 4.3 extends the baseline with `tick_ohlcv_1m` row counts and narrates deltas. It leaves it to the implementer to decide whether an `mt` surface reports those counts, with a fallback if none does. Task 4.4 tests only the accounting lines, the `over` counts and the missing-key stop. Nothing tests the baseline extension, the delta, or the fallback narration. This is a required evidence item (D6 item 6) and it changes the baseline file format that re-runs read back. Add one test for it. Resolve the "which surface" question in 1.1's read step so 4.3 does not branch.

### [NOTE] Task 5.1 forbids other `src/` edits but also asks for docstring updates

The task says to change no other file under `src/`, then says to update "any docstring that says ES bars are 2024 only". Such a docstring may sit outside `universe.py`. Clarify that docstring-only edits elsewhere are permitted.

### [NOTE] Granularity: separate test sub-tasks and a read-only task could be merged

- Tasks 1.6 and 1.7 are both test work for one commit, and 1.2–1.5 are untested until 1.7. 304's tests also fail between 1.2 and 1.6 because signatures change. Consider folding each test step into its implementation step, or at least stating that 1.6 immediately follows 1.2/1.3.
- Tasks 4.4 and 4.6 could be merged with 4.2 into a single "tests for Task 4" item.
- Task 1.1 is a read-only step with no artifact. It is justified here by the field-name dependency, but it could be merged into 1.2.

None of this affects correctness.

### [PASS] Success criteria coverage and commit placement

Each criterion traces to a task:
- Criterion 1 (universe and pin): 5.1 and 5.2.
- Criterion 2 (stops before spending: gate, edge, `--gate-issue`, ceilings, scope): 1.4 and 2.1–2.4. The outside-the-wants refusal is carried over from 304.
- Criterion 3 (`--estimate`): 1.4, 1.5 and 1.7.
- Criteria 4–7: 3.x and 4.x logic, with the purchase itself left to Phase 7.

The Technical Requirements' enumerated unit tests are all present. They cover the missing `--slice`, the gate and `gh` error cases, each edge case, ceiling rounding, once-per-product calendars, and each unaccounted-for D6 case. The runbook update (5.3) and the evidence note skeleton (5.4) match the slice. No task spends money, merges or pushes, and Phase 7 preconditions are recorded as context rather than as human-wait tasks. No NFR is restated in the slice, so no load test or CI wiring task is required.

### Run Digest

- Response length: 5455 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 115399 / 8831 / 106564 / 5082 / not computed
- Duration: 42.6 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
