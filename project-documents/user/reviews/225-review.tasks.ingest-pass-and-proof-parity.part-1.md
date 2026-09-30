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
sourceDocument: project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: de4137d1d39045b3d2cc691609c0df027e51361b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: nfr-coverage
    summary: "Throughput NFR has no load test and no CI gate"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-2.md:231-242"
  - id: F002
    severity: concern
    category: commit-checkpoints
    summary: "Section 2 batches nine tasks into one commit"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:207-299"
  - id: F003
    severity: concern
    category: sequencing
    summary: "4.1 depends on 4.2, which comes after it"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:367-391"
  - id: F004
    severity: concern
    category: testability
    summary: "Worker tests need seams that the tasks do not provide"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:404-409"
  - id: F005
    severity: concern
    category: test-coverage
    summary: "Some TD8 failure rows have no test in part 1"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:347-355"
  - id: F006
    severity: note
    category: task-sizing
    summary: "2.8 combines ledger accumulation and row building"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:279-290"
  - id: F007
    severity: pass
    category: coverage
    summary: "Success-criteria traceability for part 1"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:79-415"
  - id: F008
    severity: pass
    category: sequencing
    summary: "Refactor sequencing and commit rhythm in Sections 0, 1 and 3"
    location: "project-documents/user/tasks/225-tasks.ingest-pass-and-proof-parity-1.md:132-203"
  - id: F009
    severity: pass
    category: verification
    summary: "Grant assumption is correct"
    location: "scripts/provision_tick_roles.sql:146"
---

# Review: tasks — slice 225

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Throughput NFR has no load test and no CI gate

The slice restates two throughput targets. The first is that a day's sessions ingest far faster than a day of market time. The second is that a month of sessions ingests within an operator's working session (TD2, Walkthrough step 4). Neither task file has a task for a load test in `tests/load/`. Neither has a CI wiring task to gate on one. The only check is the manual walkthrough (8.3), which is not repeatable and is not run in CI. The LLD deliberately leaves measurement to 226. Even so, the review rule requires the NFR to have a load test and a CI gate, or an explicit decision to defer both. Add a small `tests/load/` task, for example ingest of a real-day fixture with a generous per-unit ceiling, plus a CI wiring task. Alternatively, record in the task file that both are deferred to 226, so the gap is explicit.

### [CONCERN] Section 2 batches nine tasks into one commit

Tasks 2.1–2.9 cover checks, resolution, sessions, ordinals and ledger, and they share a single commit at 2.9. The project rule is at least one commit per task. Sections 1 and 3 commit after each implementation-and-test pair, so Section 2 breaks the pattern. Add commits after 2.3, 2.5, 2.7 and 2.9. Use `feat(tick): add contract resolution` and similar messages.

### [CONCERN] 4.1 depends on 4.2, which comes after it

Step 1 of the transaction in 4.1 is "supersession (4.2)", and 4.1's success criterion is "imports". So 4.1 cannot import until 4.2 exists. Either move 4.2 ahead of 4.1, or fold it in as a subitem. Also, 4.1 and 4.2 have no tests of their own until 4.3. 4.3 is effort 4 with six integration cases. Split it so the supersession and overlap cases, which cover FR6, follow 4.2 directly.

### [CONCERN] Worker tests need seams that the tasks do not provide

Two cases in 4.3 cannot be built against the worker as specified:
- **Case 4** changes a unit's row from another connection before `mark_ingested`. The worker is a plain function holding one transaction, with no point between COPY and the transition where a test can act.
- **Case 5** sets the lock timeout short "in the test". 4.1 takes the timeout from a constant in 0.3.

4.1 should specify how these are injected, for example a `lock_timeout` parameter or a settings object passed to the worker. It should also name a test hook or a patchable seam around the transition step. Otherwise the implementer will invent a hack.

### [CONCERN] Some TD8 failure rows have no test in part 1

- **`decode` check:** 4.1 catches decode and file errors and maps them to `decode`, but no task tests a missing or corrupt archive file.
- **`TickCalendarError`:** 3.3 says an unreachable calendar raises it and becomes `storage_abort`, but 3.4 does not test it.
- **Superseded-unit list:** 3.3 builds the superseded lower-tier units with their ledger min/max times, but 3.4 does not check that list.

Part 2 (5.2) does not cover these three either. Add a case for each.

### [NOTE] 2.8 combines ledger accumulation and row building

The task holds ledger accumulation, the instrument-set rule, `calendar_id` and `session_date`, and COPY row building. Row building is the piece 226 may replace. It already carries a "split if over ~300 lines" hint. Splitting row building into its own task would follow the seam the LLD names.

### [PASS] Success-criteria traceability for part 1

Every part 1 task cites a TD or FR. I found no scope creep.
- **FR3–FR6 and FR8:** covered by 2.7, 2.9, 3.2 and 4.3.
- **TD1–TD8:** covered by Sections 0–4.
- **FR1, FR2, FR7, FR9, FR10 and FR11:** covered in part 2.

Part 2 also carries the standing tasks (7.1–7.3) and the walkthrough.

### [PASS] Refactor sequencing and commit rhythm in Sections 0, 1 and 3

The refactors are ordered so that every 224 test must pass unchanged before the new code that uses them: the store context, then the generic contract with the parity test, then the transition builders. Each pair commits after its tests. Section 3 also has a commit after each implementation-and-test pair.

### [PASS] Grant assumption is correct

The task file states that `tick_app` already has SELECT, INSERT, UPDATE and DELETE on all seven tick tables, so no grant task is needed. Line 146 of the provisioning script confirms the grant statement.

### Run Digest

- Response length: 5881 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
