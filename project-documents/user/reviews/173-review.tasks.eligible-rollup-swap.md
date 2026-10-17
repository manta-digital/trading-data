---
docType: review
layer: project
reviewType: tasks
slice: eligible-rollup-swap
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/173-tasks.eligible-rollup-swap.md
aiModel: xiaomi/mimo-v2.6-pro
aiProfile: openrouter
status: complete
dateCreated: 20261017
dateUpdated: 20261017
reviewedSha: 1fba7c813d0c3ed325a6ef93065337d67c4f3ce7
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 12
turns: 6
promptTokens: 112895
cachedTokens: 95872
completionTokens: 8884
reasoningTokens: 7009
durationSeconds: 209.0
costUsd: 0.015518
costBasis: alias_pricing
runId: run-20261017-p5-05794663
squadronVersion: 0.23.0
findings:
  - id: F001
    severity: pass
    category: traceability
    summary: "Every success criterion traces to a task"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Constant-removal file set is miscounted and one test file is omitted"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
  - id: F003
    severity: note
    category: test-coverage
    summary: "Cutover test placement conflicts with the 172 pattern it cites"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
  - id: F004
    severity: note
    category: process
    summary: "Load-tier CI gating is explicitly deferred, not implicit"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
  - id: F005
    severity: note
    category: test-coverage
    summary: "Reader-gap test covers the relation layer, not the endpoints named in the criterion"
    location: "project-documents/user/tasks/173-tasks.eligible-rollup-swap.md"
---

# Review: tasks — slice 173

**Verdict:** CONCERNS
**Model:** xiaomi/mimo-v2.6-pro

## Findings

### [PASS] Every success criterion traces to a task

Cross-referencing the slice design's criteria against the tasks: Functional 1 (SPY/AVGO/MSFT off-tape values absent from 5m and 1h) → task 3.4's per-granularity assertions; Functional 2 (no read gap) → task 2.4 item 4 (reader thread across 064, transactional path) and item 5; Functional 3 (post-cutover verify/coverage/value-parity/no leftovers/API default) → task 5.1's post-swap checks plus 3.1–3.3, with the production legs named in 8.3 for Phase 7; Functional 4 (kill-and-rerun, timers held) → 5.1's swap-state detection and 5.2's kill-and-rerun and per-failure-table-row cases; Functional 5 (NFR pass numbers) → 8.2 with the design's NFR table and Section 6's 5m load case. Technical Requirements (probe first → 1.1; migration integration tests → 2.4; NULL-bucket read behavior → 3.2/3.4; health failure states → 4.1/4.3; quality bar → 7.2/7.3/8.1) and Integration Requirements (counts unchanged → 2.4 item 5; mt-ui charts → 8.3's named Phase 7 check) are all covered. No scope creep: every section, including the Section 1 probe and Section 6 load case, traces to Technical Scope items or Technical Requirements.

### [CONCERN] Constant-removal file set is miscounted and one test file is omitted

Task 3.1 says to update "all four users" of `PRINTS_ELIGIBLE_1M_ONLY_MESSAGE` found by `grep -rn "PRINTS_ELIGIBLE_1M_ONLY_MESSAGE" src test`, but that grep finds six importing files, not four: `src/manta_trading/api_server/routes/bars.py:38`, `src/manta_trading/cli/commands/data.py:23`, `src/manta_trading/market/timescale_minute_db.py:20`, `test/integration/api_server/test_bars_prints.py:13`, `test/unit/api_server/test_bars.py:26`, and `test/unit/cli/commands/test_data_get.py:12` (plus the definition at `src/manta_trading/constants.py:1484`). Task 3.4's named update list names only `test_bars_prints.py` and `test_data_get.py`, omitting `test/unit/api_server/test_bars.py` (which imports the constant at line 26 and asserts on it at line 672). The trailing safety clause in 3.4 ("no test may still import the removed constant") and the full unit tier in 8.1 would catch the omission, but a junior implementer who trusts the stated count or the named list over re-running the grep will leave a broken unit test. Fix the count and add `test/unit/api_server/test_bars.py` to the 3.4 list.

### [NOTE] Cutover test placement conflicts with the 172 pattern it cites

Task 5.2 directs unit tests to `test/unit/scripts/` while also saying to "follow how the 172 script's tests stub the host commands" — but 172's cutover tests live in `test/unit/test_cutover_172.py`, and the repo convention is `test/unit/test_cutover_*.py` (234, 267, 921 all follow it). `test/unit/scripts/` contains only an `__init__.py`. Either location works, but the two instructions point at different places; naming one explicitly (e.g. `test/unit/test_cutover_173.py`) would remove the ambiguity.

### [NOTE] Load-tier CI gating is explicitly deferred, not implicit

The slice restates NFRs and the breakdown adds the 5m load case (6.1) and NFR re-runs (8.2) in `test/load/`. There is no CI-wiring task, but task 8.2 states the disposition explicitly: the load tier is a manual gate and CI wiring belongs to slice 907 per slice 187 D9. That satisfies "CI gating is not left implicit" — the ownership is named and deferred by design, so no gap here. If the PM wants this slice to close the loop itself, a small wiring task would be needed, but the current framing is internally consistent.

### [NOTE] Reader-gap test covers the relation layer, not the endpoints named in the criterion

Functional criterion 2 names `mt data status` and `/api/v1/status` serving without a gap; task 2.4 item 4 tests a thread reading `data_status` and the 4h rollup directly. Since both endpoints read those relations and the transactional rename is atomic across all five names, this is adequate coverage, and 2.4 item 5 separately verifies the endpoint-visible counts — but the equivalence is implicit in the task text. A one-line note in 2.4 that the relation-level reader stands in for both endpoints would make the traceability explicit.

### Run Digest

- Response length: 5167 chars
- Response is newline-free: no
- Tool calls made: 12
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 131072 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 25697
- Effort: backend default
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 112895 / 95872 / not computed / 8884 / 7009
- Duration: 209.0 s
- Cost: $0.02 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
