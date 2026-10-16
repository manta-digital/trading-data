---
docType: review
layer: project
reviewType: tasks
slice: off-tape-prints
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/171-tasks.off-tape-prints.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: c1ed2734ef72f38295ddbd0fa247ca7afcaa7e79
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 8
turns: 9
promptTokens: 369246
cachedTokens: 289646
cacheWriteTokens: 79590
completionTokens: 3883
durationSeconds: 49.4
costUsd: 0.386913
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: concern
    category: coverage
    summary: "Success criterion 1 \"absent from eligible 1m bars\" is only tested for one of the listed values"
    location: "project-documents/user/tasks/171-tasks.off-tape-prints.md:367-370"
  - id: F002
    severity: concern
    category: conventions
    summary: "`OFF_TAPE_SWEEP_LOCK_TIMEOUT` is created in task 6.2 instead of the constants task"
    location: "project-documents/user/tasks/171-tasks.off-tape-prints.md:97-105, 300"
  - id: F003
    severity: note
    category: ci
    summary: "No CI wiring task for the new load case is needed"
    location: ".github/workflows/ci.yml"
  - id: F004
    severity: note
    category: scope
    summary: "Calibration note holds more than calibration"
    location: "project-documents/user/tasks/171-tasks.off-tape-prints.md:230-234, 340-343, 382"
  - id: F005
    severity: pass
    category: coverage
    summary: "Success criteria traceability and failure-table tests"
    location: "project-documents/user/tasks/171-tasks.off-tape-prints.md:122-139, 242-256, 317-333"
  - id: F006
    severity: pass
    category: sequencing
    summary: "Sequencing, sizing and commit checkpoints"
    location: "project-documents/user/tasks/171-tasks.off-tape-prints.md"
---

# Review: tasks — slice 171

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Success criterion 1 "absent from eligible 1m bars" is only tested for one of the listed values

Criterion 1 requires every listed known value to be marked and absent from eligible 1m bars. Task 1.4 asserts the marks for all of them. The only eligible-output assertion is in 7.2, which checks that `prints=eligible` drops the SPY 09-08 770.x highs. The 09-11 high, the AVGO low, the MSFT 08-05 low, and the SPY lows (724.228) are never checked on the eligible path. Task 7.1 checks `ELIGIBLE` against `eligible_bar` in general, so the gap is small. Extend the 7.1 fixture-based test, or the 7.2 test, to assert that each listed value is absent from the eligible output.

### [CONCERN] `OFF_TAPE_SWEEP_LOCK_TIMEOUT` is created in task 6.2 instead of the constants task

The conventions say every comparison value is defined once in `constants.py`. Task 1.2 is the constants task and does not list the lock timeout. Task 6.2 adds it as "(new constant)" without a value or a source for the value. Add it to 1.2. It does not depend on calibration, so it can go there with a stated value or a named config source. This also keeps the constants in one place.

### [NOTE] No CI wiring task for the new load case is needed

The slice restates a read-latency NFR, and 7.4 covers it with a `prints=eligible` case in `test/load/test_187_api_nfr.py`. CI has no test job, and runbook 100 line 369 says load tiers run manually. A CI gating task would therefore have nothing to attach to. Task 7.4 running the tier by hand is the right scope.

### [NOTE] Calibration note holds more than calibration

The calibration note collects the insert failure path (4.2), the sweep chunk measurement (6.5) and the load-test elapsed time (7.4). It is a single place for 9.2 to read from, and it avoids extra files. The one cost is the name: "Insert failure path" under a calibration note is easy to miss. Keep it as is. Only give the note a clearer title if you are touching it anyway.

### [PASS] Success criteria traceability and failure-table tests

- **Criterion 1 marks:** 1.4 items 1–2.
- **Criterion 2 (earnings set):** 1.4 item 3.
- **Criterion 3 (raw unchanged):** 4.4 item 2 for the insert path, and 6.2 checksums plus 6.4 item 1 for the sweep.
- **Criterion 4 (agreement):** 4.4 item 3, with 6.4 item 1 comparing sweep masks to insert-path masks.
- **Criterion 5:** 7.2 for the default and the 422.
- **Criterion 6:** explicitly deferred to Phase 7, in line with the Git Rules that merges and releases are not tasks.

Each failure-table row has a test:
- insert-path failure: 4.4 item 5;
- lock timeout: 6.4 item 3;
- running pass: 6.4 item 4;
- killed mid-chunk: 6.4 item 2;
- checksum mismatch: 6.4 item 5.

The "deep backfill waits" row is accepted in the design without a test.

### [PASS] Sequencing, sizing and commit checkpoints

- Fixtures, constants, detector, tests and calibration come in dependency order. The detector fixtures and the calibration stop condition in 1.4/1.5 are explicit.
- Tests follow their implementations within each section. A commit closes every section (1.6, 2.3, 3.3, 4.5, 5.3, 6.6, 7.5, 8.4, 9.3), so checkpoints are spread across the work.
- Section 5 is placed before the sweep, and it takes its bounds from 1.5. There are no circular dependencies.
- Each task has clear stop conditions for a junior implementer. Examples are 4.2 (failure-path mismatch) and 6.5 (projected duration too long).
- Size fits the slice. It is one task file with nine sections, and I see no test tasks beyond what the criteria need.

### Run Digest

- Response length: 4386 chars
- Response is newline-free: no
- Tool calls made: 8
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 9
- Tokens — prompt / cached / cache write / completion / reasoning: 369246 / 289646 / 79590 / 3883 / not computed
- Duration: 49.4 s
- Cost: $0.39 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
