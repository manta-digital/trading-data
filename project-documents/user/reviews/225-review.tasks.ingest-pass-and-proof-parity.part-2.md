---
docType: review
layer: project
reviewType: tasks
slice: ingest-pass-and-proof-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: de4137d1d39045b3d2cc691609c0df027e51361b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 4
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: nfr-coverage
    summary: "Throughput targets have no `tests/load/` task and no CI gate"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:239-242"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "FR7 is only partly tested at the pass level"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:54-69"
  - id: F003
    severity: concern
    category: task-sequencing
    summary: "Two implementation tasks in a row (6.3, 6.4) with no test until 6.5"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:112-148"
  - id: F004
    severity: concern
    category: task-clarity
    summary: "Standing-check outputs 7.1 and 7.2 have no named destination"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:172-186"
  - id: F005
    severity: note
    category: task-clarity
    summary: "Throughput pass/fail thresholds in 8.3 are not quantified"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:236-238"
  - id: F006
    severity: note
    category: test-coverage
    summary: "\"Real adopted day file end to end\" is not clearly met by an automated test"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:54-66"
  - id: F007
    severity: note
    category: task-scope
    summary: "Building the `TickPass[TickStore]` instance is not assigned to a task"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:37-52"
  - id: F008
    severity: note
    category: test-coverage
    summary: "5.3 CLI tests omit the \"unselectable `--unit-id` reports its reason\" case"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:79-81"
  - id: F009
    severity: pass
    category: coverage
    summary: "Success criteria coverage across the two parts"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md"
  - id: F010
    severity: pass
    category: sequencing
    summary: "Sequencing and commit distribution"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md"
---

# Review: tasks — slice 225

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Throughput targets have no `tests/load/` task and no CI gate

The slice restates two architecture targets as pass/fail (TD2 and walkthrough step 4). A day of sessions must ingest far faster than a day of market time, and the full run must fit within an operator's working session. Task 8.3 checks these once, by hand, and records the numbers. No task adds a load test under `tests/load/`, and no task wires CI to gate on one. If this is deliberate, say so: for example, that 226 owns the load harness because it owns measurement. Otherwise add a small load test over the real fixture days with a threshold, plus a CI job that runs it.

### [CONCERN] FR7 is only partly tested at the pass level

FR7 and TD8 give several pass-level outcomes. Task 5.2 covers a dropped database (case 2), a raising worker with the other unit settling (case 3), and a failed check (case 4). Four outcomes have no test at this level:
- A row-lock timeout ends the run as `storage_abort`. Task 4.3 case 5 only checks that the worker raises `OperationalError`.
- A unit reopened mid-run is reported as skipped "changed during ingest", with outcome `ok` and no failure recorded. Task 4.3 case 4 is worker-level only, and the pass-level `skipped.changed_during_ingest` tally in 5.1 is never asserted.
- An unreachable production calendar (`TickCalendarError`) gives `storage_abort`.
- A plan-time failure, such as an unsupported shape, gives `partial`.

Add these to 5.2, or note where they are covered.

### [CONCERN] Two implementation tasks in a row (6.3, 6.4) with no test until 6.5

The test-with pattern is followed for 6.1→6.2 and 5.1→5.2. Tasks 6.3 (`status_reads.py`) and 6.4 (`build_status` and `build_coverage`) are both implementation-only, with success criteria of "imports". The first test that touches them is 6.5, and the commit comes only after that. The SQL for the raw-count join against session arrays is the riskiest part of the slice. Either add a check that runs the read against `migrated_tick_db` at 6.3, or merge 6.3 into 6.5's test scope explicitly. Keep 6.4's `to_dict()` round-trip in 6.5.

### [CONCERN] Standing-check outputs 7.1 and 7.2 have no named destination

Tasks 7.1 and 7.2 succeed when "a note" is written. They don't say where the note lives (this task file, the LLD, or a review file) or who reads it. 7.1 also says to name undeclared differences "for the PM", but it doesn't say whether that blocks 7.3. A junior AI could satisfy these without leaving anything reviewable. Name the file and section, and state what happens if an undeclared divergence is found.

### [NOTE] Throughput pass/fail thresholds in 8.3 are not quantified

Task 8.3 says a miss fails the walkthrough. Its two targets are "far below 24 h" and "inside an operator's working session". The executor has no number to compare against. Fix a concrete ceiling, such as a per-unit maximum and a whole-run maximum in minutes, before the run. Otherwise the recorded numbers can't be judged.

### [NOTE] "Real adopted day file end to end" is not clearly met by an automated test

The slice's Technical Requirements ask for at least one integration test on a real adopted day file. Task 0.2 builds few-thousand-record slices, and 5.2 case 1 says "two real days" without saying full files or fixtures. Full-day coverage currently comes only from the manual walkthrough in 8.3. State in 5.2 whether case 1 uses fixture slices or archive files, or record that the walkthrough satisfies this requirement.

### [NOTE] Building the `TickPass[TickStore]` instance is not assigned to a task

Task 5.1 creates `IngestPhase` and `run_ingest`. The design says ingest is `TickPass[TickStore]` with one phase, run through `run_phase`. No task builds that pass object and wires it to `open_tick_store(lock_key=TICK_INGEST_LOCK_KEY)`. It is probably implicit in 5.3, but stating it in 5.1 or 5.3 avoids a gap.

### [NOTE] 5.3 CLI tests omit the "unselectable `--unit-id` reports its reason" case

TD4 says a named unit that isn't selectable is reported with its reason. Task 3.2 tests the selection logic, but the 5.3 CLI tests only cover repeated `--unit-id`. Add one CLI assertion that the reason appears in the report and the `--json` output.

### [PASS] Success criteria coverage across the two parts

Every functional requirement maps to at least one task:
- FR1 and FR2: 5.2 case 1 and 8.3.
- FR3–FR6 and FR8: part 1 (4.3, 3.2, 2.7).
- FR7: 5.2 (see the concern above for the gaps).
- FR9: 6.2 and 6.5.
- FR10: 6.5 and 8.4.
- FR11: 5.3 and 6.6.

The documentation, contract-row (I10–I13), Revision Log and supersedes-list items are in 7.3 and 7.4. The walkthrough steps 1–9 map to 8.1–8.5. No scope creep found.

### [PASS] Sequencing and commit distribution

Dependencies run one way: 5 needs part 1's worker, 6 needs 1.1's connection helper, 7 needs the built code, and 8 needs everything else. There are no cycles. Commits fall at 5.2, 5.3, 6.2, 6.5, 6.6, 7.4 and 8.5, with semantic prefixes, so they aren't batched at the end. File-size splits are called out in 5.3, 6.4 and 6.6.

### Run Digest

- Response length: 6555 chars
- Response is newline-free: no
- Tool calls made: 4
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
