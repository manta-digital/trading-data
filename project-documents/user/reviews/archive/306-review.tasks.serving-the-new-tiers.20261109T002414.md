---
docType: review
layer: project
reviewType: tasks
slice: serving-the-new-tiers
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/306-tasks.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 67f8a2ad3bec64864ff0fa44b973856d881f1a45
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 3
turns: 4
promptTokens: 220511
cachedTokens: 108278
cacheWriteTokens: 112227
completionTokens: 4475
durationSeconds: 39.6
costUsd: 0.505269
costBasis: provider
runId: run-20261109-p5-f5b47c48
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Task 3 commit is not a working point: CLI-equality tests need task 4"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:124"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Existing load test seeds ticks only and breaks when bars switch, with no task to fix it"
    location: "test/load/test_231_futures_api_nfr.py:216"
  - id: F003
    severity: concern
    category: nfr-coverage
    summary: "D10 states a 10 s bound but no `tests/load` task or CI gate covers it"
    location: "project-documents/user/slices/306-slice.serving-the-new-tiers.md:247-251"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Python bucket twin in 2.2 is test-only duplication"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:88-90"
  - id: F005
    severity: concern
    category: task-sizing
    summary: "5.6 is oversized and has a conditional fixture branch"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:185-191"
  - id: F006
    severity: concern
    category: proportionality
    summary: "6.4 duplicates 7.5"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:218-252"
  - id: F007
    severity: note
    category: consistency
    summary: "Design/task inconsistencies are resolved in the task file but the design is unchanged"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md:44-46"
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Success criteria coverage and task order"
    location: "project-documents/user/tasks/306-tasks.serving-the-new-tiers.md"
---

# Review: tasks — slice 306

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Task 3 commit is not a working point: CLI-equality tests need task 4

Task 3.3 says the API tests `test_contract_bars_equal_the_cli` and `test_product_bars_across_the_roll_equal_the_cli` "stay", and its success line requires `test_futures_series.py` to pass. The CLI (`tick_get_cmds.py`) only switches to `read_provider_bars` in task 4.1. At the task 3 `**Commit**:` the API serves provider bars while `get` still builds bars from ticks, so those equality tests cannot pass. Either move the CLI-equality tests and their success check into 4.3, or drop the task 3 commit marker and commit once after task 4. The first option is cleaner.

### [CONCERN] Existing load test seeds ticks only and breaks when bars switch, with no task to fix it

`test_a_product_1m_bars_read_across_the_roll` reads 54 sessions of product `1m` bars from seeded `tick_trade` data (lines 10–12, 84, 216–236). After 3.2 it reads `tick_ohlcv_1m`, so it returns no bars or fails its row-count and bound assertions. Task 1.3 only adds a `TICK_TIER` argument to this file. Nothing reseeds it with provider bars, and the 6.4 and 7.5 test runs don't include `test/load`. Add a subtask in 3.3 to reseed it with `tick_ohlcv_1m` bars (about 1,380 per session) and keep its bound. It is the only automated check on the D10 admission bound (75,000 rows) before Phase 7.

### [CONCERN] D10 states a 10 s bound but no `tests/load` task or CI gate covers it

D10 sets a quantified bound: a long coarse read completes in under 10 s. The slice deliberately measures it once in production in Phase 7, so this is not a demand for new generated-data load tests. The slice does restate the bound, though, and the task file has no load task and no CI wiring for it. The reseeded `test_231` read above would give a regression guard. If a CI job already gates `test/load`, name it in that task. If none does, say explicitly that D10 is Phase 7 only.

### [CONCERN] Python bucket twin in 2.2 is test-only duplication

2.2 builds a pure Python reimplementation of the SQL bucket formula "used only by tests" and unit-tests it. That tests the twin and not the shipped SQL. The behaviour that matters is already checked against real SQL in 2.4 (b) and (c), and by the DST test (4h starts across the DST change). Cut the twin and its unit tests. Keep 2.2 to the SQL fragment and the helper reuse, with 2.4 as the proof. The 5.6 requirement for an "independent Python bucketing" is a different check and can stay.

### [CONCERN] 5.6 is oversized and has a conditional fixture branch

5.6 packs five test groups into one subtask: per-minute class parity, `at_close`, the skipped-session rule, four exit-code paths, and a possible hand-built bar day for `tick_only`. A junior AI would struggle to complete it as one unit. Split it into (a) class parity and `at_close`, and (b) skip rule and exit codes. Make the "if the fixture yields no `tick_only` minute" fallback an explicit step with a clear test, not an optional aside.

### [CONCERN] 6.4 duplicates 7.5

6.4 runs the touched-area tests, ruff and mypy. 7.5 runs the same set again, and nothing but docs changes in between. Keep the task 6 run, since it gates the commit that deletes code. Reduce 7.5 to the success-criteria 1–13 mapping and the file-size check, plus a docs-only check. Alternatively fold 6.4 into 6.3's success line.

### [NOTE] Design/task inconsistencies are resolved in the task file but the design is unchanged

Success criterion 10 and the Migration Plan say a grep for `TICK_BAR_GRANULARITIES` is empty, but D4 keeps that name. The design also writes exit codes 2 and 3 where the code constants are `EXIT_PARTIAL = 3` and `EXIT_PROVIDER = 2`. The task file states which side controls, and 6.3 applies the D4 reading. The slice design still carries both errors, and the Verification Walkthrough repeats "exit 2". Task 7.5 maps criteria against the design, so criterion 10 will appear to fail literally. Correct the design text too, or have 7.5 record the deviation.

### [PASS] Success criteria coverage and task order

Every criterion 1–13 maps to a task:
- Criterion 1: 3.3(a)
- Criterion 2: 2.4
- Criterion 3: 2.4 DST and early-close tests
- Criterion 4: 3.1
- Criterion 5: 4.3(a)
- Criterion 6: 7.4 and D6
- Criterion 7: 5.1–5.6
- Criteria 8 and 9: 5.7
- Criterion 10: 6.1–6.3
- Criterion 11: 6.4 and 7.5
- Criteria 12 and 13: 7.1–7.4

The order is acyclic: plan family first, then reader, then API, `get`, parity, removal, and docs. Tests follow their implementation tasks, and the commit markers sit at the end of each group. I found no scope creep. The statistics routes and the purchase stay excluded, and the Phase 7 measurements are correctly left out of the tasks.

### Run Digest

- Response length: 5510 chars
- Response is newline-free: no
- Tool calls made: 3
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 4
- Tokens — prompt / cached / cache write / completion / reasoning: 220511 / 108278 / 112227 / 4475 / not computed
- Duration: 39.6 s
- Cost: $0.51 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
