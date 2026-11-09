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
reviewedSha: 86bd16d51cbdd205f63e73f90f1c8ace5c50b3fa
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 115401
cachedTokens: 11198
cacheWriteTokens: 104199
completionTokens: 5690
durationSeconds: 50.7
costUsd: 0.475593
costBasis: provider
runId: run-20261109-p5-2407c667
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria trace to tasks"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:47-202"
  - id: F002
    severity: concern
    category: coverage
    summary: "Retry-exhausted requests have no consumer, and 4.5 does not wire the final-unit rule"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:163-167"
  - id: F003
    severity: concern
    category: sequencing
    summary: "`--estimate` may fail on an unset ceiling before it can print the command"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:88-92"
  - id: F004
    severity: concern
    category: commit-placement
    summary: "Task 3 commits pure functions that nothing calls yet"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:124-138"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "Task 4.3 is too large for one step"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:153-157"
  - id: F006
    severity: note
    category: verification
    summary: "Lint, format and typecheck are not explicit in tasks 1–4"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:198"
  - id: F007
    severity: note
    category: proportionality
    summary: "Test tasks 1.2, 1.4 and 1.6 are granular but acceptable"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:59-86"
  - id: F008
    severity: note
    category: coverage
    summary: "The D7 follow-up issue is intentionally outside Phase 6"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:193"
---

# Review: tasks — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria trace to tasks

- **Criterion 1 (universe pin):** tasks 5.1 and 5.2.
- **Criterion 2 (cutover stops):** 2.1–2.4 for the gate, the edge and a missing `--gate-issue`. 1.5 and 1.7 cover short ceilings naming the command. A planned request outside the wanted set is 304's existing `_check_scope`.
- **Criterion 3 (`--estimate`):** 1.5 and 1.7.
- **Criteria 4–7 (production outcomes):** the exit rule built in 3.x and 4.x, and the second estimate in 4.5. The purchase itself is correctly left to Phase 7.
- **Technical-requirement unit cases:** each is named in a test task. These include the per-product session check, the 5-step rounding, a missing `--slice`, an absent, null or unparseable edge, and each D6 item 3 unaccounted-for case.
- **Test-with pattern:** each implementation task is followed by its test task. No task spends money or touches production.
- **Scope creep:** none found. Runbook 100, the evidence note skeleton and the line-count check all appear in the design's Included list.

### [CONCERN] Retry-exhausted requests have no consumer, and 4.5 does not wire the final-unit rule

Success criterion 4 and D5 require a retry-exhausted request to be "listed with the provider's error, classified `acquisition failed`". 3.1 builds the function that returns those requests with their error text. It says "4.5 calls it from `_unit_is_final`".

4.5 does neither of these things:
- It does not mention `_unit_is_final`.
- It does not narrate the retry-exhausted requests with their error text. 4.1 labels sessions `acquisition failed`, but the request-level error text from 3.1 is never written to the log.

Without this wiring:
- The exit rule may still fail on units of a retry-exhausted request, because they never count as final.
- The provider error never reaches the evidence note.

Add two explicit items to 4.5:
- Call the 3.1 final-unit check from `_unit_is_final`.
- Narrate each retry-exhausted request with its error.

Add matching assertions to 4.6.

### [CONCERN] `--estimate` may fail on an unset ceiling before it can print the command

The `--estimate` run exists to print the PM's ceiling command, so it normally runs before the ceilings are set.
- 1.5 makes `check_env_text` raise when a ceiling is missing.
- 2.3 runs the env checks inside the preflight, and says the gates also run in `--estimate` mode.
- 1.7 says the estimate is "not refused for short ceilings". It does not say that the preflight env check for an unset ceiling is skipped in this mode.
- The 1.7 test only asserts "does not fail on short ceilings". A reader could take that to mean ceilings that are set but too low.

Say explicitly that an unset ceiling does not stop `--estimate`, and add a test with the ceilings absent from the env text. The full cutover must still stop in that case.

### [CONCERN] Task 3 commits pure functions that nothing calls yet

Task 3 holds two small pure functions, a constant and a final-unit predicate. Its own note says "task 4.5 wires them into the exit rule". The commit message `feat: add failed-request verdicts to the purchase cutover` therefore describes behaviour that does not exist at that point. The tests pass, but no behaviour changes.

Fold task 3 into task 4 as 4.0, or into task 2, and drop the separate marker. The commit would then sit at a point where the behaviour works end to end.

### [CONCERN] Task 4.3 is too large for one step

4.3 bundles four things, and the slice design leaves the real accounting shape for the implementer to find:
- the accounting reader for health, accounting and source-edge lines;
- the cross-tier `traded_vs_cleared` counts and the `over` failure;
- the extension of the baseline file with per-family record totals;
- the delta narration.

Split it into 4.3a (accounting lines and cross-tier counts, with the `over` failure) and 4.3b (scale: baseline totals and deltas). Each gets its own test task. The baseline-format change is the riskiest part, because re-runs read that file, so it should not be buried inside a larger step.

### [NOTE] Lint, format and typecheck are not explicit in tasks 1–4

5.5 says "Each task already ran format, lint, typecheck and its tests". Only the Context Summary ground rules and 2.1's mypy line say so. Commits at tasks 1–4 happen before 5.5. Consider adding one line to each commit marker's task: "ruff format and `ruff check --fix` on the touched files, then `git diff main` for swept lines". This is optional, because the ground rules already cover it.

### [NOTE] Test tasks 1.2, 1.4 and 1.6 are granular but acceptable

Each test task is short and follows its implementation task directly. They could be merged into their implementation tasks, but the current split keeps each step independently checkable. No change needed.

### [NOTE] The D7 follow-up issue is intentionally outside Phase 6

The integration requirement "follow-up invoice issue exists" is a Phase 7 action. The skeleton in 5.4 lists it under Open items, so it is accounted for.

### Run Digest

- Response length: 6171 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 115401 / 11198 / 104199 / 5690 / not computed
- Duration: 50.7 s
- Cost: $0.48 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8

## Resolution

- [pass] Success criteria trace to tasks: no change needed.
- [concern] Retry-exhausted requests have no consumer: task 4.7 now lists them in the narration, passes them to 4.1 for the `acquisition failed` label, and makes `_unit_is_final` accept their units; 4.8 tests it.
- [concern] `--estimate` may fail on an unset ceiling: task 1.7 now skips the env file's ceiling checks in that mode (the usage-plan check still runs) and its test covers unset ceilings.
- [concern] Task 3 commits uncalled functions: removed Task 3's commit marker; its functions are committed with Task 4, which wires them (4.7).
- [concern] Task 4.3 too large: split into 4.3/4.4 (accounting rows, cross-tier, `over`) and 4.5/4.6 (record totals before and after); wiring and its tests are now 4.7/4.8.
- [note] Lint, format and typecheck not explicit: added to the success criteria of the last test task of Tasks 1, 2 and 4 (1.7, 2.4, 4.8).
- [note] Test tasks 1.2, 1.4 and 1.6 are acceptable: no change; the review called them acceptable and test-with requires them.
- [note] The D7 follow-up issue is outside Phase 6: no change; it is a Phase 7 step by design and the slice design says so, so no issue was filed.
