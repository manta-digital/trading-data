---
docType: review
layer: project
reviewType: tasks
slice: time-bars-from-held-bars-and-ticks
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: FAIL
verdictSource: derived
sourceDocument: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261115
dateUpdated: 20261115
reviewedSha: 78a6e5234d2298765ec9ee2b6a1fb5feca2ed566
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 14
effort: xhigh
turns: 15
promptTokens: 578086
cachedTokens: 405329
cacheWriteTokens: 172747
completionTokens: 73941
durationSeconds: 321.5
costUsd: 0.211624
costBasis: provider
runId: run-20261115-p5-353fa79c
squadronVersion: 0.24.28
findings:
  - id: F001
    severity: fail
    category: test-design
    summary: "Route tests expect `ticks` for sizes the fixture serves from bars"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:309"
  - id: F002
    severity: concern
    category: spec-conflict
    summary: "Measured run is the first run in the band, but criterion 5 asks for the widest"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:143"
  - id: F003
    severity: concern
    category: ci-gating
    summary: "No CI job gates the load test"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:368"
  - id: F004
    severity: concern
    category: scope
    summary: "`mt data tick get` skips the trade limit, and the design does not allow that"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:338"
  - id: F005
    severity: concern
    category: coverage
    summary: "The default range is held ticks, so sessions held only in bars are never planned"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:286"
  - id: F006
    severity: concern
    category: sizing
    summary: "Section 6 is one commit covering about 30 items"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:330"
  - id: F007
    severity: note
    category: sizing
    summary: "Section 9.1 packs about eight replacements into one checkbox"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:438"
  - id: F008
    severity: note
    category: coverage
    summary: "\"A bar never crosses a session\" has no test"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:119"
  - id: F009
    severity: note
    category: coverage
    summary: "Criterion 2 says \"summed by hand\"; the task uses a Python oracle"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:116"
  - id: F010
    severity: note
    category: commit-cadence
    summary: "Two commit subjects exceed the 72-character guideline"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:465"
  - id: F011
    severity: note
    category: commit-cadence
    summary: "Section 11 has no commit marker"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:480"
  - id: F012
    severity: note
    category: scope
    summary: "Task 10.1 adds the 300 architecture document, which the design does not list"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:470"
  - id: F013
    severity: pass
    category: coverage
    summary: "Every success criterion maps to a task or a handoff step"
    location: "project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:132"
  - id: F014
    severity: pass
    category: sequencing
    summary: "Sequencing holds and the referenced code exists"
    location: "project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:63"
---

# Review: tasks — slice 309

**Verdict:** FAIL
**Model:** claude-haiku-5-5

## Findings

### [FAIL] Route tests expect `ticks` for sizes the fixture serves from bars

Task 4.3 (line 195) defines the fixture as "the real tick day complete in both families", and says `5m` chooses `ohlcv-1m`. Under the rule in 4.1 (line 173), any size with a provider width that divides it, where the bar verdict is complete, chooses the provider. `1m` (60 s), `1h` and `1d` all qualify, so on this fixture they choose `ohlcv-1m`. Task 6.4 still says `1h`, `1m`, `15s` and `1d` return `sources` naming `ticks`. Only `15s` is right. An implementer will either break the 4.1 rule to satisfy 6.4 or write a failing test.

Also, no route-level test asserts `sources` naming `ohlcv-1m`. SC3's response-level clause is covered only at the choice level (4.3) and the reader level (5.4). Fix: assert `ohlcv-1m` for `1h`, `1m` and `1d`, and `ticks` for `15s`, on the fixture. Add a route assertion for the provider source.

### [CONCERN] Measured run is the first run in the band, but criterion 5 asks for the widest

Design criterion 5 (slice design line 138) requires the note to record "the widest range the default admits". Task 3.1(b) measures "the run `pick_range` returns": the first run of consecutive sessions whose ledger counts sum to 95–100% of the limit. These are not the same run. An earlier review round raised this as FAIL. The revision changed the task to match `pick_range` but left the criterion as "widest". Either name the largest-trade admitted run and how to select it, or amend criterion 5 and say why the first in-band run bounds the 10 s budget.

### [CONCERN] No CI job gates the load test

Task 7.3 adds `test/load/test_309_time_bars_nfr.py` with a bound, then defers the gate to issue #32. I checked `.github/workflows/ci.yml`: it has no pytest, unit, integration or load step. The project rule is that a load test has a CI wiring task. Add one that runs the file in CI, or name the specific blocker (for example, CI cannot reach the test cluster). Otherwise the 308 and 309 bounds are checked only by hand in 11.1.

### [CONCERN] `mt data tick get` skips the trade limit, and the design does not allow that

Task 7.1 passes `max_trades=None` for the CLI, so `mt data tick get <size>` reads with no admission. The design says the CLI "follows the same rule" (slice design line 62), and its cost section refuses tick-cut reads over the limit before any tick is read. The design names no operator exemption. A month of `1s` bars on production is the cost case the limit exists for. Either apply the same admission to the CLI with a refusal message, or record the exemption in the design before building it.

### [CONCERN] The default range is held ticks, so sessions held only in bars are never planned

Task 6.2 plans both bars routes on `SchemaFamily.TICK_TIER`, with "their default range is the held ticks". A session held only in provider bars is outside that range, so a default request skips it. The design says provider bars are used where held (rule 2), and that the same routes will serve bars back to 2010 with no change for the consumer. That second claim fails for a default request if bars are bought without ticks. No test in 5.4 or 6.4 covers a bars-only session through a route with the default range. Decide whether the default range is the union of tick and bar sessions and state it in 6.2, or record that a later slice owns it. This does not affect production today, since no bars are held.

### [CONCERN] Section 6 is one commit covering about 30 items

Section 6 covers the response model, the routes, the model tests, the route success and refusal tests, the error mapping and the existing-test updates. Its single marker spans about 30 checkboxes and bullets across roughly ten files. The tree cannot be green after 6.1 alone, but it can be green at two points: 6.1, 6.2, 6.3 and 6.6 together (existing tests move to the new shape), then 6.4 and 6.5 as additive route tests. Put a marker at the first point so a route-test bisect does not undo the response change.

### [NOTE] Section 9.1 packs about eight replacements into one checkbox

The earlier review flagged this as too large. It still is. Splitting it into routes and grammar, sources and applied examples, and config, cost and removals would make each item checkable.

### [NOTE] "A bar never crosses a session" has no test

The design states this rule (slice design line 58). 2.2 checks anchors and totals but not boundaries. Add one assertion for a size that does not divide the session evenly (for example `90m` or `4h`): the last bucket ends at or before the session close, and no bucket includes trades from another session.

### [NOTE] Criterion 2 says "summed by hand"; the task uses a Python oracle

2.2 reuses `tick_minutes` (`test/tick_support/parity_seed.py:54`), an independent Python bucketing of the tick rows, not a manual sum. That is independent of the SQL, so it meets the intent. Say so in 2.2 so an implementer does not read "by hand" as a manual check.

### [NOTE] Two commit subjects exceed the 72-character guideline

The docs subject on line 465 is about 95 characters. The feat subject on line 330 is about 78. Shorten both, for example `docs: document time bars from held bars or ticks; regenerate OpenAPI`.

### [NOTE] Section 11 has no commit marker

This file uses markers, so the implementation rule allows commits only at them. If validation in 11.1 finds a failure that needs a fix, there is no permitted commit point. Add a marker for a `fix:` commit made during 11.1, or say where such fixes go.

### [NOTE] Task 10.1 adds the 300 architecture document, which the design does not list

The design's cleanup list (slice design line 128) names the slice and task files for 302–305 and 308. Task 10.1 also covers the 300 architecture, conditionally, where it contradicts the ticks-primary rule. The condition limits the risk, but the architecture is the parent document. Either drop it from 10.1 or have the design list it.

### [PASS] Every success criterion maps to a task or a handoff step

SC2 is covered by 2.2. SC3 by 4.3 and 5.4. SC4 by 4.3 and 6.4. SC5 by 3.1, 5.2, 5.4 and 6.5. SC6 by 6.6 and 11.1. SC7 at test scale by 8.3. SC1, SC8 and SC7's production half sit in the Phase 7 handoff, which is an accepted location. The only route-level gap is the one in the first finding.

### [PASS] Sequencing holds and the referenced code exists

The order is BarSize (1), then the cut (2), the measurement (3, which uses 2.1), source choice (4), reader (5), routes (6), removals (8) and docs (9). The parity deletions in 8.2 come after every reader. I checked these references and they exist: `_TICK_MINUTES` (`bar_parity.py:37`), `_DAY_BUCKET` (`bar_reads.py:44`), `tick_bar_reads._entry` (line 246), `pick_range` (`scripts/measure_tick_bars.py:97`), `tick_minutes` (`parity_seed.py:54`), the `TickBarGranularity` users (`futures_series.py` and `futures_params.py`), and the line references in `test_tick_access.py` and `test_futures_models.py`. The load tier lives under `test/load/`, which matches the task. The earlier round's ledger-guard, BLOCKED-scope and `BAR_SOURCE_NAME` items are addressed in the current text.

### Run Digest

- Response length: 10202 chars
- Response is newline-free: no
- Tool calls made: 14
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 15
- Tokens — prompt / cached / cache write / completion / reasoning: 578086 / 405329 / 172747 / 73941 / not computed
- Duration: 321.5 s
- Cost: $0.21 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 14
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 14
- Finding-shaped matches — surviving validation: 14

### Raw Response

Both documents are read. Next I'll check the code references the task file depends on, since a plan that cites a missing file or symbol gives a junior AI a dead end.
The code references mostly check out (`tick_minutes` signature, `pick_range`, the `test_futures_models.py:201` and `test_tick_access.py:18,21` line numbers, `test/load/` layout, the 308 note, `ci.yml`). The grep output was capped, so I need targeted checks on `_TICK_MINUTES`, `tick_bar_reads._entry`, the CI workflow, and the test files the task says must pass unchanged.
Two things came out of that batch that change the picture:

- The CI workflow has no pytest/unit/integration/load reference, so "CI has no test job" is accurate and the load test is not gated anywhere.
- A review of an earlier revision exists in `project-documents/user/reviews/`. Its open items are the widest-vs-earliest measurement issue, the CI gate and the 9.1 size. I'll check whether the current text resolves them rather than re-raising them blindly.

Next I'll check the remaining symbols the task relies on, and the test files I could not find.
## Summary

**Verdict: FAIL.** Success-criteria coverage is nearly complete and the sequencing holds, but the route expectations in 6.4 contradict the fixture defined in 4.3 and the source rule in 4.1. That has to be fixed before the section 6 commit is built. Five concerns follow. I reviewed the working-tree copy; git status at session start showed the task file modified and uncommitted.

## Findings

### [FAIL] Route tests expect `ticks` for sizes the fixture serves from bars
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:309
category: test-design

Task 4.3 (line 195) defines the fixture as "the real tick day complete in both families", and says `5m` chooses `ohlcv-1m`. Under the rule in 4.1 (line 173), any size with a provider width that divides it, where the bar verdict is complete, chooses the provider. `1m` (60 s), `1h` and `1d` all qualify, so on this fixture they choose `ohlcv-1m`. Task 6.4 still says `1h`, `1m`, `15s` and `1d` return `sources` naming `ticks`. Only `15s` is right. An implementer will either break the 4.1 rule to satisfy 6.4 or write a failing test.

Also, no route-level test asserts `sources` naming `ohlcv-1m`. SC3's response-level clause is covered only at the choice level (4.3) and the reader level (5.4). Fix: assert `ohlcv-1m` for `1h`, `1m` and `1d`, and `ticks` for `15s`, on the fixture. Add a route assertion for the provider source.

### [CONCERN] Measured run is the first run in the band, but criterion 5 asks for the widest
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:143
category: spec-conflict

Design criterion 5 (slice design line 138) requires the note to record "the widest range the default admits". Task 3.1(b) measures "the run `pick_range` returns": the first run of consecutive sessions whose ledger counts sum to 95–100% of the limit. These are not the same run. An earlier review round raised this as FAIL. The revision changed the task to match `pick_range` but left the criterion as "widest". Either name the largest-trade admitted run and how to select it, or amend criterion 5 and say why the first in-band run bounds the 10 s budget.

### [CONCERN] No CI job gates the load test
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:368
category: ci-gating

Task 7.3 adds `test/load/test_309_time_bars_nfr.py` with a bound, then defers the gate to issue #32. I checked `.github/workflows/ci.yml`: it has no pytest, unit, integration or load step. The project rule is that a load test has a CI wiring task. Add one that runs the file in CI, or name the specific blocker (for example, CI cannot reach the test cluster). Otherwise the 308 and 309 bounds are checked only by hand in 11.1.

### [CONCERN] `mt data tick get` skips the trade limit, and the design does not allow that
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:338
category: scope

Task 7.1 passes `max_trades=None` for the CLI, so `mt data tick get <size>` reads with no admission. The design says the CLI "follows the same rule" (slice design line 62), and its cost section refuses tick-cut reads over the limit before any tick is read. The design names no operator exemption. A month of `1s` bars on production is the cost case the limit exists for. Either apply the same admission to the CLI with a refusal message, or record the exemption in the design before building it.

### [CONCERN] The default range is held ticks, so sessions held only in bars are never planned
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:286
category: coverage

Task 6.2 plans both bars routes on `SchemaFamily.TICK_TIER`, with "their default range is the held ticks". A session held only in provider bars is outside that range, so a default request skips it. The design says provider bars are used where held (rule 2), and that the same routes will serve bars back to 2010 with no change for the consumer. That second claim fails for a default request if bars are bought without ticks. No test in 5.4 or 6.4 covers a bars-only session through a route with the default range. Decide whether the default range is the union of tick and bar sessions and state it in 6.2, or record that a later slice owns it. This does not affect production today, since no bars are held.

### [CONCERN] Section 6 is one commit covering about 30 items
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:330
category: sizing

Section 6 covers the response model, the routes, the model tests, the route success and refusal tests, the error mapping and the existing-test updates. Its single marker spans about 30 checkboxes and bullets across roughly ten files. The tree cannot be green after 6.1 alone, but it can be green at two points: 6.1, 6.2, 6.3 and 6.6 together (existing tests move to the new shape), then 6.4 and 6.5 as additive route tests. Put a marker at the first point so a route-test bisect does not undo the response change.

### [NOTE] Section 9.1 packs about eight replacements into one checkbox
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:438
category: sizing

The earlier review flagged this as too large. It still is. Splitting it into routes and grammar, sources and applied examples, and config, cost and removals would make each item checkable.

### [NOTE] "A bar never crosses a session" has no test
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:119
category: coverage

The design states this rule (slice design line 58). 2.2 checks anchors and totals but not boundaries. Add one assertion for a size that does not divide the session evenly (for example `90m` or `4h`): the last bucket ends at or before the session close, and no bucket includes trades from another session.

### [NOTE] Criterion 2 says "summed by hand"; the task uses a Python oracle
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:116
category: coverage

2.2 reuses `tick_minutes` (`test/tick_support/parity_seed.py:54`), an independent Python bucketing of the tick rows, not a manual sum. That is independent of the SQL, so it meets the intent. Say so in 2.2 so an implementer does not read "by hand" as a manual check.

### [NOTE] Two commit subjects exceed the 72-character guideline
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:465
category: commit-cadence

The docs subject on line 465 is about 95 characters. The feat subject on line 330 is about 78. Shorten both, for example `docs: document time bars from held bars or ticks; regenerate OpenAPI`.

### [NOTE] Section 11 has no commit marker
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:480
category: commit-cadence

This file uses markers, so the implementation rule allows commits only at them. If validation in 11.1 finds a failure that needs a fix, there is no permitted commit point. Add a marker for a `fix:` commit made during 11.1, or say where such fixes go.

### [NOTE] Task 10.1 adds the 300 architecture document, which the design does not list
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:470
category: scope

The design's cleanup list (slice design line 128) names the slice and task files for 302–305 and 308. Task 10.1 also covers the 300 architecture, conditionally, where it contradicts the ticks-primary rule. The condition limits the risk, but the architecture is the parent document. Either drop it from 10.1 or have the design list it.

### [PASS] Every success criterion maps to a task or a handoff step
location: project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md:132
category: coverage

SC2 is covered by 2.2. SC3 by 4.3 and 5.4. SC4 by 4.3 and 6.4. SC5 by 3.1, 5.2, 5.4 and 6.5. SC6 by 6.6 and 11.1. SC7 at test scale by 8.3. SC1, SC8 and SC7's production half sit in the Phase 7 handoff, which is an accepted location. The only route-level gap is the one in the first finding.

### [PASS] Sequencing holds and the referenced code exists
location: project-documents/user/tasks/309-tasks.time-bars-from-held-bars-and-ticks.md:63
category: sequencing

The order is BarSize (1), then the cut (2), the measurement (3, which uses 2.1), source choice (4), reader (5), routes (6), removals (8) and docs (9). The parity deletions in 8.2 come after every reader. I checked these references and they exist: `_TICK_MINUTES` (`bar_parity.py:37`), `_DAY_BUCKET` (`bar_reads.py:44`), `tick_bar_reads._entry` (line 246), `pick_range` (`scripts/measure_tick_bars.py:97`), `tick_minutes` (`parity_seed.py:54`), the `TickBarGranularity` users (`futures_series.py` and `futures_params.py`), and the line references in `test_tick_access.py` and `test_futures_models.py`. The load tier lives under `test/load/`, which matches the task. The earlier round's ledger-guard, BLOCKED-scope and `BAR_SOURCE_NAME` items are addressed in the current text.
