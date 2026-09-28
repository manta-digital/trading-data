---
docType: review
layer: project
reviewType: tasks
slice: cme-session-model-and-data-correctness-amendment
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 642358be6c937a306b5eba95c84dcf066841bb20
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 7
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "All eight Functional Requirements trace to a task"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md"
  - id: F002
    severity: pass
    category: coverage
    summary: "Technical and Integration Requirements are covered, including the contract amendment"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md:167-204"
  - id: F003
    severity: pass
    category: scope
    summary: "No scope creep; exclusions are respected"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:9-14"
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing is dependency-correct with no circularity"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md"
  - id: F005
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, not batched"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md:79,110,157,203,226"
  - id: F006
    severity: concern
    category: test-specification
    summary: "Naive-datetime error type is under-specified for `session_containing`, weakening the test that should catch a divergence from FR5"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-2.md:44-45,76"
  - id: F007
    severity: note
    category: test-coverage
    summary: "No task exercises the `late_open` exception path"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:116-131,305-317"
  - id: F008
    severity: note
    category: task-scoping
    summary: "Task 4.1 (compile the CME exception table) is a research task with a soft success criterion"
    location: "project-documents/user/tasks/221-tasks.cme-session-model-and-data-correctness-amendment-1.md:259-275"
  - id: F009
    severity: note
    category: nfr-coverage
    summary: "No load-test task is present, and none appears required"
    location: "unverified"
---

# Review: tasks — slice 221

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] All eight Functional Requirements trace to a task

FR1 (calendar rows + bound) → Part 1 §3.1/§5.1 + integration checks in §5.2. FR2 (session coverage) → §5.2, §1.2, §4.4. FR3 (specific 2024 dates) → §5.2 and Part 2 §6.4. FR4 (NYSE/NASDAQ byte-identical) → §0.1 fixture, §1.2 identity test, §5.2 `test_nyse_sessions_unchanged`. FR5 (lookup semantics) → Part 2 §6.1/§6.4. FR6 (clamp + reporting + `--strict` exit 4) → §3.2–§3.5, §5.2. FR7 (`calendars list` fixed) → Part 2 §7.1. FR8 (verification script over both jobs) → Part 2 §8.1/§8.2. No gaps found.

### [PASS] Technical and Integration Requirements are covered, including the contract amendment

Every unit/integration test bullet in the slice design's Technical Requirements maps to a task (session_interval cases, SessionIndex cases, seed-table invariants, extension clamp, `calendars list` column check, NYSE regression, real-data boundary fixture, migration 057/058 integration test, ruff/mypy). Section 9 (9.1–9.4) covers all D10 contract items: vocabulary, I11–I14, the I7 exception, the sequence-gap move, the renumbering, and the mapping-table/CLI-line updates.

### [PASS] No scope creep; exclusions are respected

No task touches `CME_METALS`/GC, fixes NYSE/NASDAQ holiday data, modifies migration 026's callable, or adds an API surface — all explicitly excluded in the slice design. Every task's rationale cites a specific LLD decision (D2–D10) or success criterion rather than introducing unrelated work. Section 5.2's "NYSE unchanged" test and §0.1/§1.2 actively guard the exclusion rather than violate it.

### [PASS] Sequencing is dependency-correct with no circularity

Baseline fixture (§0) precedes any `src/` change; `session_interval` (§1) and `SessionIndex` (§2) precede the extension routine (§3) that composes them; the seed table (§4) precedes migration 058 (§5), which precedes the lookup tests that require it (Part 2 §6.4); CLI (§7) correctly follows the lookup it wraps (§6); the verification script (§8) correctly follows both the index and migration 058. Final validation (§10) is last.

### [PASS] Commit checkpoints are distributed, not batched

Commits land at the end of nearly every section (0–2, 3, 4, 5, 6, 7, 8, 9, and a final commit in 10.2) — roughly one checkpoint per section across ten sections, consistent with the project's "checkpoint per section" convention.

### [CONCERN] Naive-datetime error type is under-specified for `session_containing`, weakening the test that should catch a divergence from FR5

FR5 in the slice design states `session_containing` "raises `ValueError` for a naive datetime," and D7 states this generally. Task 6.1 makes this explicit for `sessions_between` ("naive inputs raise `ValueError`", line 43) but for `session_containing` only says "the same range check, then `SessionIndex(...).locate(ts)`" (lines 44-45) without restating the exception type. The corresponding test task, 6.4 line 76, says only "a naive datetime (raises)" — it doesn't pin the exception class. If a junior implementer's "range check" for `session_containing` compares a naive `ts` against tz-aware bounds before delegating to `locate()`, Python raises `TypeError` (offset-naive vs. offset-aware comparison), not `ValueError`, and a loosely-written test (`pytest.raises(Exception)` or similar) would pass while silently missing the literal FR5 requirement. Tighten task 6.1 to state explicitly that `session_containing` validates `ts.tzinfo` and raises `ValueError` before any comparison, and tighten 6.4's bullet to "raises `ValueError`" rather than the generic "(raises)".

### [NOTE] No task exercises the `late_open` exception path

FR2 says sessions open 17:00 CT "except where a `late_open` exception says otherwise," and D5 allows `late_open` rows "if one ever occurs." Task 1.2 exercises `closed` and `early_close` population cases but not `late_open`, and 4.4's seed-table tests check `early_close`/`closed` invariants but assert nothing about a `late_open` row. If the compiled CME table (§4.1) never actually emits a `late_open` exception, this code path ships untested. Low severity since the design itself treats `late_open` as conditional/rare, but worth a synthetic unit case in 1.2 for defense-in-depth.

### [NOTE] Task 4.1 (compile the CME exception table) is a research task with a soft success criterion

Effort 4, and its "Success" line ("a working list in the scratchpad with a source for every row") is not mechanically verifiable the way the rest of the breakdown's success criteria are (grep, test pass, exit code). This is inherent to the domain — external CME sourcing can't be reduced to a code check — and the task correctly builds in an escalation path ("A year that cannot be sourced from CME is reported to the PM"), so this is informational rather than blocking.

### [NOTE] No load-test task is present, and none appears required

The slice design states no latency/throughput/concurrency NFR for the session lookup or verification script (its only quantitative claims — "~250 session rows per seeded year," "no special cutover" — are storage-volume, not performance, statements). No `tests/load/` task or CI-gating task exists in the breakdown, which is consistent with there being no NFR to restate.

### Run Digest

- Response length: 6748 chars
- Response is newline-free: no
- Tool calls made: 7
- Tool calls failed: 0
- Stop reason: end_turn
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
