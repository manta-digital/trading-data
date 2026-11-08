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
reviewedSha: 8f5c85fb23c56f029da78a0e97676a89c3f39846
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
turns: 3
promptTokens: 120701
cachedTokens: 11198
cacheWriteTokens: 109499
completionTokens: 6660
durationSeconds: 56.3
costUsd: 0.506495
costBasis: provider
runId: run-20261108-p5-33ccd68a
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "All ten success criteria and the technical requirements are covered"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:60-364"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and commit markers are sound"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:95-364"
  - id: F003
    severity: concern
    category: testing
    summary: "Commit at task 4 follows an API caller change that task 4 never tests"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:171"
  - id: F004
    severity: concern
    category: duplication
    summary: "The progress-plus-unavailable join is specified three times with no shared helper"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:179"
  - id: F005
    severity: concern
    category: specification
    summary: "The saved measurement output has no named location, but 10.1 depends on it"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:318"
  - id: F006
    severity: note
    category: scope
    summary: "The seed lives in one file and 9.2 does not point to the fixtures it samples"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:285-295"
  - id: F007
    severity: note
    category: ci-gating
    summary: "The load test is deliberately not wired into CI"
    location: "project-documents/user/tasks/305-tasks.futures-completeness-surfaces.md:301"
---

# Review: tasks — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] All ten success criteria and the technical requirements are covered

- Criterion 1 (lag pin test, `COVERAGE_SOURCE_TABLE` unchanged): 1.2 and 1.3.
- Criterion 2 (pending statistics sessions, production-shaped want ends, regression on tick and bar judged sets): 1.4 and 1.5.
- Criterion 3 (four-surface agreement): 8.1.
- Criterion 4 (health severity table, timeout row): 5.2 and 5.4.
- Criterion 5 (`load-complete only` label): 3.5, 4.3, 5.4 and 7.3.
- Criterion 6 (`traded_vs_cleared` counts match `tick coverage`): 3.3.
- Criterion 7 (inactive check listed): 3.5 and 4.3.
- Criterion 8 (accounting section and JSON): 4.2 and 4.3.
- Criterion 9 (overview lines and outage): 6.3.
- Criterion 10 (API `families`, no cross-tier fields): 7.1 to 7.3.
- Measurement, D10 fix and note: 9 and 10.
- Docs: 7.4.
- Changelog and line-count checks: 11.

### [PASS] Sequencing and commit markers are sound

- Tests follow their implementation tasks (1.3/1.5, 2.3/2.5, 3.3/3.5, 5.2/5.4, 9.4 after the seed).
- No circular dependencies. The agreement test (8) comes after all four surfaces exist. The measurement (9) comes after the surfaces it times.
- `**Commit**:` markers are plain lines at working, tested points, one per numbered group, with a conditional one in task 10.

### [CONCERN] Commit at task 4 follows an API caller change that task 4 never tests

4.1 rewrites `accounting_lines` into a pure converter and switches `api_server/tick_access.read_tick` to `futures_completeness(cross_tier=False)`. That caller now receives one line per family, but its top-level `TickAccountingRecord` fields must still come from the tick-tier line only. Neither step says how that line is selected.

4.3 tests only the accounting command. The API tests (`test_futures_models.py`, the API integration test, `test_231_futures_api_nfr.py`) are not re-run until 7.3 and 11.2. The commit after task 4 could therefore land with a broken `/api/v1/status`.

Fix:
- Add to 4.1 that `read_tick` takes the tick-tier line for the existing top-level fields.
- Add to 4.3 that the existing API tick-block tests pass unchanged.

### [CONCERN] The progress-plus-unavailable join is specified three times with no shared helper

4.2, 5.3 (line 210) and 7.2 (line 255) each say to join `ReadProgress` with the unavailable text. The project rules say not to duplicate logic. As written, three callers will each build the string `GC statistics: traded_vs_cleared: <error>` on their own, and the formats can drift. Only 2.5 tests the `progress` contents, and no task tests the formatted detail in one place.

Fix: add a small formatter beside `ReadProgress` in 2.2, with a unit test in 2.5. Have 4.2, 5.3 and 7.2 call it.

### [CONCERN] The saved measurement output has no named location, but 10.1 depends on it

9.6 says "its figures are saved". 10.1 then reads "the 9.5 output saved by 9.6" to find each missed bound's statement and plan. No path is given. The throwaway database is dropped, so the output file is the only record of the `EXPLAIN (ANALYZE, BUFFERS)` plans. A junior agent running in the background could lose it or save it somewhere untracked.

Fix: name the output file in 9.6, for example a path under a gitignored scratch directory, and cite it in 10.1 and 10.4.

### [NOTE] The seed lives in one file and 9.2 does not point to the fixtures it samples

9.1 to 9.3 put database creation, unit seeding, ledger seeding, `tick_definition`, day conditions, edge and `tick_statistics` bulk load with compression into `manifest_scale_seed.py`. That is likely to pass the ~300-line guideline. 9.2 says "real proportions … from the 2024 fixtures" without naming them. A short line allowing a split (for example, a ledger-seed module) and naming the fixture files would help.

### [NOTE] The load test is deliberately not wired into CI

The slice restates NFR-style bounds (D9) and 9.4 adds the load test. 9.4 states explicitly that the load tier runs only under `MT_RUN_LOAD_TESTS=1` and that `.github/workflows` has no load step, so no CI wiring is added. This matches D9 ("like the rest of the load tier"), and the gate is the one-time pre-307 measurement plus D10. The decision is explicit, not implicit, so I am not raising it as a gap.

### Run Digest

- Response length: 5151 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 120701 / 11198 / 109499 / 6660 / not computed
- Duration: 56.3 s
- Cost: $0.51 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7

## Resolution

- [pass] Success criteria and technical requirements covered: no change needed.
- [pass] Sequencing and commit markers sound: no change needed.
- [concern] Task 4 API caller change untested: added a 4.3 item that the existing API status tests pass unchanged after the `read_tick` caller change.
- [concern] Progress-plus-unavailable join specified three times: task 2.4 now defines one helper, `unavailable_with_progress`; 4.2, 5.3 and 7.2 call it, and 2.5 tests it.
- [concern] Measurement output has no location: 9.6 writes to `/tmp/305-measurement.log` and 10.1 reads from that path.
- [note] 9.2 does not point to fixtures: it now names `test/fixtures/databento/real/` and `test_tick_statistics_real.py`. The seed stays in one file, so the split was not applied.
- [note] Load test not wired into CI: no change; this is deliberate and task 9.4 already says so.
