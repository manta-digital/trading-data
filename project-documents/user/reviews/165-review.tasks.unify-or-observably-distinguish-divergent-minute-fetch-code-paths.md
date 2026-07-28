---
docType: review
layer: project
reviewType: tasks
slice: unify-or-observably-distinguish-divergent-minute-fetch-code-paths
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260727
dateUpdated: 20260727
findings:
  - id: F001
    severity: fail
    category: completeness
    summary: "`run_daily_refetch`'s call to `_do_daily_symbol` never receives the new required `via` argument"
    location: src/manta_trading/data/acquisition/daemon/daily.py:426
  - id: F002
    severity: concern
    category: accuracy
    summary: "Task 1.3 cites a test class that doesn't exist"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md:81
  - id: F003
    severity: pass
    category: coverage
    summary: "Functional and technical success criteria otherwise map cleanly to tasks"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
  - id: F004
    severity: pass
    category: sequencing
    summary: "Sequencing and commit distribution are sound"
    location: project-documents/user/tasks/165-tasks.unify-or-observably-distinguish-divergent-minute-fetch-code-paths.md
---

# Review: tasks — slice 165

**Verdict:** FAIL
**Model:** claude-sonnet-5

## Findings

### [FAIL] `run_daily_refetch`'s call to `_do_daily_symbol` never receives the new required `via` argument

Task 1.2 makes `via: str` a required keyword-only parameter on `_do_daily_symbol` and `_process_daily_symbol`, and wires `run_daily_cycle` → `_process_daily_symbol` with `via="cycle"`. But `run_daily_refetch` calls `_do_daily_symbol` directly at `daily.py:426` (mirroring how `run_minute_refetch` calls `_do_minute_symbol` directly, which Phase 2 task 2.1 explicitly fixes with `via="refetch"`). No task in the breakdown (Phase 1, 2, 3, or 4) instructs updating this `run_daily_refetch` call site. Since `via` has no default, following the task list literally leaves `_do_daily_symbol(symbol, pool=pool, http=http, settings=settings, force_reset_terminal=True, window=window)` missing a required argument — `mt data pull` (daily), which calls `run_daily_refetch` from `cli/commands/data.py:2635`, would raise `TypeError: _do_daily_symbol() missing 1 required keyword-only argument: 'via'` at runtime for every invocation.

This would not be caught by the test suite as scoped: `TestRunDailyRefetch` in `test_daily.py` (confirmed via `patch(...daily._do_daily_symbol...)` at line 293) mocks `_do_daily_symbol`, so a missing kwarg on the real call site is invisible to it, and task 1.3's daily test only asserts `_do_daily_symbol` itself accepts/forwards `via` — it doesn't assert `run_daily_refetch` passes it. Neither 2.3 nor 4.3 (integration/E2E verification) exercises the daily-refetch path. This also leaves the slice's own functional requirement ("Every … log line from … `_do_daily_symbol` … includes a `via=refetch` … field") unmet for the refetch caller specifically.

Fix: add a bullet to task 1.2 (or a new task) instructing `run_daily_refetch` to pass `via="refetch"` in its `_do_daily_symbol` call, parallel to what 2.1 does for the minute side.

### [CONCERN] Task 1.3 cites a test class that doesn't exist

Task 1.3 tells the implementer to see "the `TestForceResetTerminalAndWindow` class at line 262 for the mocking pattern." No class of that name exists in `test/unit/data/acquisition/daemon/test_minute.py`. Line 262 actually falls inside `TestDoMinuteSymbolExtensions` (which starts at line 261). A junior AI following this reference literally will search for a nonexistent symbol before falling back to context. Given CLAUDE.md's "do not guess or assume" instruction and the reviewer criterion that tasks be precisely completable, this citation should be corrected to the real class name (`TestDoMinuteSymbolExtensions`).

### [PASS] Functional and technical success criteria otherwise map cleanly to tasks

Coverage-aware seeding for `run_minute_refetch` (2.1/2.2/2.3), `force_reset_terminal` unchanged (2.2's retained `test_force_reset_terminal_always_true`), minute `via` markers (1.1/1.3/4.3), slice-162 walkthrough correction (3.1), reference-doc supersession (3.2), ruff/mypy cleanliness (2.1, 4.2), and full unit-test pass (4.1) each trace to a specific task with a concrete success check. No scope creep observed — 3.3 and 4.4 are standard close-out housekeeping consistent with project conventions, not unrelated work. No NFR is restated in this slice's Success Criteria that would require a `tests/load/` task; none is needed here.

### [PASS] Sequencing and commit distribution are sound

Phase 1 (via threading, a prerequisite since `run_minute_refetch`/`run_minute_cycle` calls will need to supply the new parameter) correctly precedes Phase 2 (unify + pass `via="refetch"`). Test tasks (1.3, 2.2) immediately follow their implementation tasks rather than being batched at the end, and four commits are distributed one per phase rather than collapsed into a single final commit. No circular dependencies.
