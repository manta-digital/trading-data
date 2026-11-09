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
reviewedSha: ed1b16069fe016751d4899ec61021eb1ba63ca68
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 115431
cachedTokens: 11198
cacheWriteTokens: 104229
completionTokens: 5063
durationSeconds: 43.4
costUsd: 0.469445
costBasis: provider
runId: run-20261109-p5-2407c667
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 2 breaks tests that Task 1 just migrated, and nothing updates them"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:106-113"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Task 3 commit is labelled as handling failed requests, but only the `since` change is wired"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:124-135"
  - id: F003
    severity: concern
    category: sequencing
    summary: "1.4 depends on `since` from 3.1, which comes two tasks later"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:73"
  - id: F004
    severity: concern
    category: task-clarity
    summary: "4.1 leaves two label rules without a data source"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:140-142"
  - id: F005
    severity: concern
    category: sequencing
    summary: "Test-with pattern is loose in Task 1"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:51-86"
  - id: F006
    severity: note
    category: proportionality
    summary: "5.5 final verification repeats per-task checks"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:194-197"
  - id: F007
    severity: pass
    category: coverage
    summary: "Success criteria traceability"
    location: "project-documents/user/tasks/307-tasks.futures-history-acquisition.md:49-199"
---

# Review: tasks — slice 307

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 2 breaks tests that Task 1 just migrated, and nothing updates them

- 1.5 and 1.6 make every `main` call in the tests pass `--slice 304`. 2.3 then makes `--gate-issue` required with no skip, and runs `check_gate_issue` and `check_dataset_edge` inside `_steps` step 1.
- 2.3 and the slice design say 304's tests call the preflight function directly. 1.5 says they call `main`.
- Every `main`-based test from 1.5 and 1.6, including the `--estimate` test, will fail after 2.3. They will need `--gate-issue` plus fakes for `gh` and `status`.
- 2.4 only adds new tests and does not mention updating the existing ones. Task 2's success line (`uv run pytest test/unit/scripts -q` passes) cannot hold at the commit.
- Fix: add an explicit step to 2.3 or 2.4 to update the existing `main`-based tests with the gate argument and fakes. Say which 304 tests call `main` and which call `preflight` directly, so the "no gate issue" claim is accurate.

### [CONCERN] Task 3 commit is labelled as handling failed requests, but only the `since` change is wired

- 3.2 builds the retry-exhausted classifier, the failed-retryable failure strings and the `_unit_is_final` helper. Their success line says "see 3.3", and wiring is deferred to 4.5.
- The commit message is `feat: handle failed purchase requests in the cutover verdicts`. At that commit the cutover behaves as before, except for the `since` change.
- Either move the 3.2 wiring (`final_failures`, `_unit_is_final`) into Task 3, or merge 3.2 and 3.3 into Task 4 and keep only 3.1 plus its test with the Task 2 commit. This also trims one commit.

### [CONCERN] 1.4 depends on `since` from 3.1, which comes two tasks later

- 1.4 says "no start instant (`since=None`, see 3.1)", but `judge_estimate` only gains the `since` parameter in 3.1.
- Today's behaviour (every resubmit refuses) already equals the `since=None` behaviour. The reference is therefore harmless in effect but confusing, and a junior may try to add the parameter early.
- Rewrite 1.4 to say it relies on 304's existing judgement, with `since` added in 3.1.

### [CONCERN] 4.1 leaves two label rules without a data source

- `publication after want end` needs "a statistics session whose records fall in the UTC day at or after `latest_want_end()`". The task does not say which `coverage` field carries the records' UTC day, or how to derive it.
- `acquisition failed` needs a session mapped to a retry-exhausted request. `coverage` sessions and `debug requests` rows are different documents, and the link between them is not stated.
- 1.1's read-first list does not name either field, so the junior may invent one. 4.2's "real-shape fixture" cannot be built without them.
- Add both to 1.1's list of fields to learn. If the session-to-request join is not available, mark it `BLOCKED:` there instead of leaving it to 4.1.

### [CONCERN] Test-with pattern is loose in Task 1

- 1.1 to 1.4 are four implementation sub-tasks that run before any test. 1.5 and 1.6 then come after all four.
- 1.5 is forced, because the interface change breaks 304's tests. The new-behaviour tests for 1.1 to 1.4 could still sit directly after each piece, or at least be split by feature.
- This is acceptable for a 2/5 slice, but 1.6 is a broad, loosely bounded test task. Consider splitting it into per-feature test items under each implementation task.

### [NOTE] 5.5 final verification repeats per-task checks

- Every task already ends with a test pass. 5.5's `git diff main` and `wc -l` checks are useful, but 4.5 and 4.6 already check line counts.
- The slice also needs a lint, typecheck and integration run before the final commit. Keep 5.5 because of those, and drop the duplicated unit-test run if trimming.

### [PASS] Success criteria traceability

- **Criterion 1** (universe and pin) is covered by 5.1 and 5.2.
- **Criterion 2** (all refusal cases) is covered by 2.1 to 2.4, 1.3 and 3.1.
- **Criterion 3** (`--estimate`) is covered by 1.3, 1.4 and 1.6.
- **Criterion 4** (failed requests) is covered by 3.1 to 3.3 and 4.5.
- **Criterion 5** (reconciliation) is existing 304 behaviour, run in Phase 7.
- **Criterion 6** (D6 items) is covered by 4.1 to 4.6 and the 5.4 note skeleton.
- **Criterion 7** (second estimate) is covered by 4.5.
- The Phase 7 items (purchase, invoice follow-up issue, health timeout check) are correctly left out of the tasks, and no task spends money or merges.
- Every task traces to a criterion or technical requirement, with no scope creep.
- The commit markers sit at five tested points, apart from the Task 3 issue above.

### Run Digest

- Response length: 5586 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 115431 / 11198 / 104229 / 5063 / not computed
- Duration: 43.4 s
- Cost: $0.47 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
