---
docType: review
layer: project
reviewType: tasks
slice: futures-completeness-surfaces
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: 4d3f18630faf9ebdfd6adcd76c363cee7586e18f
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 119714
cachedTokens: 8831
cacheWriteTokens: 110879
completionTokens: 7727
durationSeconds: 61.8
costUsd: 0.522449
costBasis: provider
runId: run-20261108-p5-33ccd68a
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage and test-with sequencing"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:59-278"
  - id: F002
    severity: concern
    category: ci-gating
    summary: "No CI wiring task for the manifest-scale load test"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:282-312"
  - id: F003
    severity: concern
    category: sequencing
    summary: "`CrossTierResult` is used in task 2 but defined in task 3, with a likely import cycle"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:101"
  - id: F004
    severity: concern
    category: interface
    summary: "Per-step progress cannot be recorded with the specified `family_reports` signature"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:104-118"
  - id: F005
    severity: concern
    category: interface
    summary: "`load_complete_only` has no source in the report that three surfaces read it from"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:99-102, 166, 193-194, 245"
  - id: F006
    severity: concern
    category: sequencing
    summary: "Task 1 never wires `source_edge` into the existing status path, yet 1.5 tests the behaviour change"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:81-91"
  - id: F007
    severity: concern
    category: interface
    summary: "`accounting_lines` and `futures_completeness` leave open which one reads the database"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:166, 174, 250"
  - id: F008
    severity: concern
    category: test-coverage
    summary: "Criteria 5 and 7 are not asserted on every surface the criteria name"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:178-183, 253-256, 350"
  - id: F009
    severity: concern
    category: test-design
    summary: "The four-surface agreement test cannot compare health buckets as written"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:268-272"
  - id: F010
    severity: concern
    category: sequencing
    summary: "Task 10.1 depends on a \"keep the database\" fixture option that 9.1 never creates"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:285-290, 318-321"
  - id: F011
    severity: concern
    category: task-sizing
    summary: "Task 9.1 is oversized for one junior task"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:284-291"
  - id: F012
    severity: concern
    category: commit-markers
    summary: "Task 10 ends with two commits but only one marker"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:336"
  - id: F013
    severity: concern
    category: proportionality
    summary: "Duplicated test and validation work"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:197-215, 273-276, 347-351"
---

# Review: tasks — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage and test-with sequencing

Each criterion has an implementing task followed by its test task.
- Criterion 1: 1.1–1.3
- Criterion 2: 1.4–1.5
- Criterion 3: 8.1
- Criterion 4: 5.1–5.4
- Criterion 6: 3.2–3.3
- Criterion 7: 3.4–3.5
- Criterion 8: 4.x
- Criterion 9: 6.x
- Criterion 10: 7.x

The technical requirement (the D9 bounds and the D10 fix path) is covered by tasks 9–10. Commit markers sit after each working, tested group and are not in the middle of one. I found no scope creep.

### [CONCERN] No CI wiring task for the manifest-scale load test

Task 9 adds a load test in `test/load/` with fixed bounds (D9). Those bounds are the slice's NFR: ≤5 s, <0.25 s, ≤30 s, ≤10 s and ≤2 s. No task wires the test into CI or any scheduled gate. It runs only under `MT_RUN_LOAD_TESTS=1`, and no task makes anything run it again. A regression past a bound would go unnoticed after 305. Add a task that either wires `test_305_manifest_scale_nfr.py` into the load-tier CI job or records why the existing load tier already gates on it.

### [CONCERN] `CrossTierResult` is used in task 2 but defined in task 3, with a likely import cycle

Task 2.1 adds `cross_tier: tuple[CrossTierResult, ...]` to `FamilyStatus` and says "the type is added in task 3". The task 2 commit therefore cannot pass mypy. Task 3.1 then has `family_report.py` import `CrossTierResult` from `cross_tier_checks.py`, while `traded_vs_cleared` takes the reports (`FamilyStatus`), which are defined in `family_report.py`. That is a circular import. Move `CrossTierResult` into task 2 or a neutral module, or have 3.1 say `cross_tier_checks.py` imports `FamilyStatus` under `TYPE_CHECKING` only.

### [CONCERN] Per-step progress cannot be recorded with the specified `family_reports` signature

Task 2.4 has `futures_completeness` write product, family and step (`scope`, `content`, `traded_vs_cleared`, `records`) to `ReadProgress` "before each step". Those steps run inside `family_reports` (2.2), whose signature takes no progress recorder or callback. The criterion 4 timeout row must name product, family and step, for example `GC statistics: traded_vs_cleared`. As written, an implementer can only record up to family granularity. Add a progress parameter to `family_reports` in 2.2.

### [CONCERN] `load_complete_only` has no source in the report that three surfaces read it from

`TickAccountingLine` (4.1) and `FamilyAccountingRecord` (7.1) both carry `load_complete_only`, and health's `family_check(product, report)` (5.1) prints it. `FamilyStatus` (2.1) gains only `source_edge`, `records` and `cross_tier`. The value depends on whether the entry also wants statistics, and a single-family report cannot know that. Add the field to `FamilyStatus` in 2.1, derived in `family_reports`. Otherwise each surface re-derives it, which breaks the one-report rule (D3) and the define-once rule.

### [CONCERN] Task 1 never wires `source_edge` into the existing status path, yet 1.5 tests the behaviour change

Task 1.4 changes `judged` to take the edge, and 1.5 tests that an open-window statistics session is `pending` and leaves `caught_up` true. Nothing in task 1 says who calls `source_edge` or where it gets the dataset edge in `_families` and `family_scope`. Task 2.2 first describes "`family_scope`, then `source_edge`, then `judged`". It also does not say which function builds the `pending` bucket for sessions after the edge. Add the call-site change to 1.4 so the task 1 commit is a working state.

### [CONCERN] `accounting_lines` and `futures_completeness` leave open which one reads the database

Task 4.1 builds lines "from `family_reports`" and gives `accounting_lines` a `cross_tier` parameter, which implies it calls `family_reports` itself. Task 4.2 and 7.2 call `futures_completeness` once inside `read_line` or `read_tick`. The design requires one call that reads every product, so that a failure fails the whole read. State whether `accounting_lines` takes the reports from `futures_completeness` as input. If it calls `family_reports` itself, `ReadProgress` and the single-read guarantee are lost.

### [CONCERN] Criteria 5 and 7 are not asserted on every surface the criteria name

- **Criterion 5:** it covers health, accounting and the API. The `load-complete only` label is tested for status (3.5) and health (5.4) only. Tests 4.3 and 7.3 never assert it, even though 7.1 adds the `load_complete_only` field.
- **Criterion 7:** it requires the inactive `ohlcv_1s_minute_sum` listing in accounting. Test 4.3 does not assert it, and only 3.5 covers it for status.

Task 11.2 says the final check confirms these are "covered", so as written that check would fail. Add the assertions to 4.3 and 7.3.

### [CONCERN] The four-surface agreement test cannot compare health buckets as written

Test 8.1 asserts identical buckets, `caught_up` and `source_edge` across status, health, accounting and the API. A health row (5.1) is a `HealthCheck` with state and a one-line detail string, so it has no structured buckets to compare. The only route is parsing the detail text, which is brittle. Specify the comparison instead. For example, build the health rows and assert on the structured report they were built from. Or restrict the health comparison to state and the counts in the detail, using the production-format string from D5.

### [CONCERN] Task 10.1 depends on a "keep the database" fixture option that 9.1 never creates

Task 10.1 says to keep the seeded database for the session "with the existing fixture options". Task 9.1 specifies a fixture whose finalizer always drops the database, and defines no such option. Add the option to 9.1. Make sure it still drops only the database the fixture created. Alternatively, have 10.1 say how to run `EXPLAIN` inside the live session.

### [CONCERN] Task 9.1 is oversized for one junior task

Task 9.1 creates the throwaway database, seeds about 30,000 manifest units, seeds ledgers at real proportions, loads 19.4 M statistics rows with a real stat-type mix, sets day conditions and the dataset edge, compresses chunks, and writes the safe finalizer. Split it into at least two tasks: (a) database fixture, manifest and ledgers, and (b) the `tick_statistics` bulk load with compression. Give each its own check. Task 9.3's "timing each statement through the connection's logging hook" is also underspecified for a junior. Name the hook.

### [CONCERN] Task 10 ends with two commits but only one marker

The `**Commit**:` line names the docs commit and puts the earlier `fix:` commit in a parenthetical. The fix commit therefore has no marker of its own, and it is conditional. Put a conditional `**Commit**:` marker after 10.3, and keep the docs marker after 10.4.

### [CONCERN] Duplicated test and validation work

- **Health:** task 5.2 unit-tests every severity row, and 5.4 re-tests the same cases through the database, including the INFO ones. Criterion 4 needs integration coverage only for the exit codes (FAIL gives 1, INFO gives 0) and for the timeout row. Drop the repeated INFO and OK integration cases, or reduce them to a single parametrised case.
- **Validation:** 8.2 and 11.2 run nearly the same validation. Keep the full pass in 11.2 and cut 8.2 to a short check that the new modules fit the line budget.
- **Seed shape:** the percentage-tolerance assertions in 9.2 go beyond what the criteria need. One non-zero and compressed assertion would do.

### Run Digest

- Response length: 9188 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 119714 / 8831 / 110879 / 7727 / not computed
- Duration: 61.8 s
- Cost: $0.52 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 13
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 13
- Finding-shaped matches — surviving validation: 13
