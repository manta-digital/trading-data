---
docType: review
layer: project
reviewType: tasks
slice: unified-schema-migration-tracking-across-both-databases
project: squadron
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/131-tasks.unified-schema-migration-tracking-across-both-databases.md
aiModel: z-ai/glm-5.1
status: complete
dateCreated: 20260422
dateUpdated: 20260422
findings:
  - id: F001
    severity: concern
    category: cli
    summary: "Missing `--json` flag for `mt data migrate` (not status)"
    location: Task 5
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Missing integration test for full minute track against real DB"
    location: Task 3
  - id: F003
    severity: concern
    category: version-control
    summary: "Commit checkpoints batched at end rather than distributed"
    location: Task 10.6
  - id: F004
    severity: note
    category: scoping
    summary: "Task 4.3 lacks concrete success criteria for pool adaptation"
    location: Task 4.3
  - id: F005
    severity: note
    category: test-coverage
    summary: "SC5 before/after CLI flow not explicitly tested end-to-end"
    location: Task 6.5
  - id: F006
    severity: pass
    category: sequencing
    summary: "Task sequencing respects all dependencies"
  - id: F007
    severity: pass
    category: test-coverage
    summary: "Test-with pattern consistently applied"
  - id: F008
    severity: pass
    category: scoping
    summary: "No scope creep identified"
---

# Review: tasks — slice 150

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.1

## Findings

### [CONCERN] Missing `--json` flag for `mt data migrate` (not status)

The slice design CLI spec explicitly defines `mt data migrate [--db minute|daily|all] [--json]`, but Task 5.1 only adds the `--db` option. No subtask explicitly adds the `--json` flag to `mt data migrate`. Task 5.4 defines the JSON output shape but assumes the flag exists. Additionally, unlike `mt data migrate status` (which has test 6.5a for JSON shape), there is no test subtask verifying JSON output for `mt data migrate` itself. A junior AI following these tasks literally would likely omit the `--json` flag and its test. **Add a subtask to 5.1 (or a new 5.x) for adding `--json` to `mt data migrate`, and add a test subtask like `test_migrate_json_output` to 5.5.**

### [CONCERN] Missing integration test for full minute track against real DB

Success criterion 10 explicitly requires: *"Integration test that runs the full minute track against a real test DB and asserts `schema_migrations` rows match `TRACKS["minute"]`."* Task 3.3 only confirms existing tests still pass (3.3a) and adds a `list_migration_state` test (3.3b). No subtask creates the specified integration test that validates the complete minute-track migration result against `TRACKS["minute"]`. **Add a subtask like 3.3c: integration test that runs the full minute track and asserts `SELECT migration_id FROM schema_migrations` matches all IDs in `TRACKS["minute"]`.**

### [CONCERN] Commit checkpoints batched at end rather than distributed

Only one commit is specified (Task 10.6), placed after all 10 tasks are complete. This means a failure at Task 8 or 9 could lose all work from Tasks 1–7 with no recovery point. Best practice for a slice of this size is to commit after each logical unit: after the module restructure (Tasks 1–2), after DB class updates (Tasks 3–4), after CLI changes (Tasks 5–7), and after cleanup (Tasks 8–9). **Distribute commit checkpoints after each task cluster.**

### [NOTE] Task 4.3 lacks concrete success criteria for pool adaptation

Task 4.3 says "adapt accordingly" and "document the adaptation inline if needed" but provides no measurable completion condition. A junior AI cannot determine when this subtask is done. If MarketDB uses the same psycopg3 pool pattern, this is trivial; if not, the adaptation could be substantial. **Consider splitting into: 4.3a (inspect MarketDB pool pattern and document finding), 4.3b (implement adaptation if needed, with explicit success criterion).**

### [NOTE] SC5 before/after CLI flow not explicitly tested end-to-end

Success criterion 5 describes a specific flow: *"Running `mt data migrate status` against a fresh daily DB (before first migrate) prints all daily-track migrations as pending; running it after `mt data migrate --db daily` prints them all as applied with timestamps."* The individual CLI tests in 6.5 cover JSON shape, missing URL, and pending display separately, but no test exercises the full before-migrate → migrate → after-migrate lifecycle through the CLI. Task 10.2 partially covers this in manual validation, but there's no automated test for this flow.

### [PASS] Task sequencing respects all dependencies

Task order correctly follows the dependency chain: module restructure (1) → runner extraction (2) → DB class updates (3, 4) → CLI changes (5, 6, 7) → cleanup (8, 9) → validation (10). No circular dependencies. Tasks 3 and 4 could theoretically run in parallel since they both depend only on Tasks 1–2.

### [PASS] Test-with pattern consistently applied

Every implementation task has its test subtasks immediately following: Task 2 → 2.4, Task 3 → 3.3, Task 4 → 4.4, Task 5 → 5.5, Task 6 → 6.5, Task 7 → 7.3. This is well done.

### [PASS] No scope creep identified

All tasks trace back to elements in the slice design. Task 7 (repurpose `mt data daily migrate`) is explicitly within scope per D5 ("Decision: repurpose, rename verify"). Task 10 (final validation) is a standard capstone task. No tasks introduce work outside the slice's stated scope.
