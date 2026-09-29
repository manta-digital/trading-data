---
docType: review
layer: project
reviewType: tasks
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260928
dateUpdated: 20260928
reviewedSha: 930716e8bd81ff0e94e7e57c0282043d87bef025
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
squadronVersion: 0.15.1
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "Adopt's calendar access is not tasked"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:343-367"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Adoption refusal paths in the slice are untested or unspecified"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:369-376"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task 7.1 is too large and will exceed the file-size limit"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:343-367"
  - id: F004
    severity: concern
    category: task-sequencing
    summary: "Reset logic is split ambiguously between 5.1 and 8.1"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:293-303, 398-405"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Run-context tests miss some preflight refusals"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:261-272"
  - id: F006
    severity: note
    category: process
    summary: "Walkthrough step 9 has a PM fallback"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md:478-480"
  - id: F007
    severity: pass
    category: uncategorized
    summary: "Success-criteria coverage for Part 1's scope"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md"
  - id: F008
    severity: pass
    category: uncategorized
    summary: "Sequencing, test-with pattern and commit distribution"
    location: "project-documents/user/tasks/223-tasks.historical-acquisition-pass-1.md"
  - id: F009
    severity: pass
    category: uncategorized
    summary: "NFR and load-test requirements do not apply"
    location: "project-documents/user/slices/223-slice.historical-acquisition-pass.md:1217-1246"
---

# Review: tasks — slice 223

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Adopt's calendar access is not tasked

TD6 says `adopt` (like the purchase phase) needs the CME calendar from the production database. It also says that if the calendar is unreachable or the range is unpopulated, the run ends `STORAGE_ABORT` and nothing is written. Task 7.1 calls `session_days(calendar, …)` and 3.1 says `OutOfPopulatedRangeError` "propagates". No Part 1 task does any of these:
- obtain a `TradingCalendar`, or say which database or env var it reads;
- map calendar unreachable or `OutOfPopulatedRangeError` to `EXIT_STORAGE` in the `adopt` verb;
- test that path, so a calendar failure leaves no rows and no exit-0.

The 7.3 integration test uses `session_migrated_db`, so it never exercises the production-database boundary. Add a subtask to 7.1 or 8.2 for the calendar source and the error mapping, plus a test in 8.3.

### [CONCERN] Adoption refusal paths in the slice are untested or unspecified

The slice lists several adopt failure modes that the tests in 7.2 and 7.3 do not cover:
- a job state other than `done` or `expired` is refused (7.1 step 2);
- a write `OSError` such as `ENOSPC` ends as a run-level storage failure naming the path and errno, with no attempt counted (7.1, and the failure-modes table).

The "adoption refuses, exit 1" outcome also has no named exception type or exit-code mapping in 7.1 or 8.2. 8.2 lists exit 1 only as "preflight". Add these cases to 7.2 and name the refusal error.

### [CONCERN] Task 7.1 is too large and will exceed the file-size limit

7.1 is effort 4 and holds seven steps: idempotence check, provider state check, manifest read from a directory or zip, traversal safety, free-space check, hashed copy with `.partial` and rename, transactional unit creation, and verify. Its own success line warns about the ~300-line limit. Split it at a natural seam, and tie 7.2 to the first half:
- **File handling:** `adopt_files.py` covers steps 3–5 (manifest read, safety, free-space, hashed copy). 7.2 tests this half.
- **Orchestration and rows:** `adopt.py` covers steps 1–2 and 6–7 (idempotence, provider state, row and unit writes, verify).

### [CONCERN] Reset logic is split ambiguously between 5.1 and 8.1

5.1 puts "reset exhausted" and "reopen a hole" in `manifest_repo.py`, and 5.2 tests "Reset leaves reopened and non-exhausted units unchanged and returns them as such". 8.1 then defines `reset_units(...)`, which does the same classification, with the location left open ("`manifest_repo.py` callers (a small `reset.py` if needed)"). It is unclear whether the repository functions are per-unit compare-and-set primitives or the classification itself. That risks duplicated logic, which the DRY rule forbids. State that 5.1 holds only the per-unit CAS primitives and that 8.1 owns the classification. Move the "unchanged and listed" test from 5.2 to 8.3, and give 8.1 its own unit test.

### [CONCERN] Run-context tests miss some preflight refusals

FR1 requires each refusal to name its variable or command. The 4.2 tests cover refusals 1–4, migrations pending and the lock. They leave out:
- the unknown-`MT_TICK_*` key when it appears in the `.env` file rather than the process environment (4.1 says both sources are checked; only the misspelt-name case is listed);
- the database-unreachable refusal after `TICK_DB_CONNECT_TIMEOUT_SECONDS`;
- the archive-not-writable case (only "missing" is listed).

Add these to 4.2. The `.env` case matters most, since it is the exact 2026-09-28 failure that motivated the check.

### [NOTE] Walkthrough step 9 has a PM fallback

10.2 stops and hands the PM a command if `sudo` is refused. Project memory records that the sudo grant and automation mandate were due 2026-09-07, and that slices should need zero PM host steps. As a contingency this is acceptable. Just confirm the grant is live before 10.2 so the fallback stays unused.

### [PASS] Success-criteria coverage for Part 1's scope

- **FR1 preflight:** 4.1 and 4.2.
- **FR2 adoption:** 7.1–7.3 and the 3.2 counts of 26 and 52, with a STOP if they differ.
- **FR6 reset part:** 8.1–8.3.
- **Migration and grants:** 2.1–2.3.
- **Settings:** 1.2–1.3.
- **Backup enrolment:** 9.1–9.3.

Part 2 covers the pass contract, universe, planner, guard, availability, delivery, definitions and pass. FR3–FR5, FR7–FR10 and the documents are in its section list. I found no scope creep: `tick_pass_render.py` and the test helpers all trace to FR2, FR6 or the walkthrough.

### [PASS] Sequencing, test-with pattern and commit distribution

There are no circular dependencies. Session days (3) precede adopt (7), the repository (5) precedes verify and adopt, and the run context (4) precedes the verbs (8). Each implementation section is followed immediately by its tests. A commit closes every section from 0 to 9, which matches the PM-confirmed checkpoint-per-section reading. The Section 0 baseline and the Section 10 comparison bracket the work.

### [PASS] NFR and load-test requirements do not apply

The slice restates no NFR. No load test or CI gating task is required.

### Run Digest

- Response length: 6361 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
