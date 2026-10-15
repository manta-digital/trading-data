---
docType: review
layer: project
reviewType: tasks
slice: tick-pass-production-wiring
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 2f87ecf59a737c86dad402ac7fb02ff77e8ee6a8
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
durationSeconds: 42.9
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: concern
    category: scope
    summary: "Ingest callback (Task 3.3) contradicts the slice design and changes an excluded area"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:201-216"
  - id: F002
    severity: concern
    category: clarity
    summary: "Cutover connection-budget inputs are not located (Task 7.6)"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:167-173"
  - id: F003
    severity: concern
    category: proportionality
    summary: "Three separate test tasks for the cutover script is more than the criteria need"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:133-196"
  - id: F004
    severity: concern
    category: test-coverage
    summary: "No test for the informational severity in `render` and `--json` (Tasks 4.3, 4.5)"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:336-361"
  - id: F005
    severity: concern
    category: process
    summary: "Closeout commit mixes unrelated content (Tasks 9.2, 10.1, 10.4)"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:262-264, 278-280"
  - id: F006
    severity: note
    category: completeness
    summary: "Execution plan after a failed cutover is missing"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:284-296"
  - id: F007
    severity: note
    category: coverage
    summary: "No load-test or CI-wiring task, and none is needed"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md:290-310"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success criteria traceability"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md"
  - id: F009
    severity: pass
    category: sequencing
    summary: "Sequencing and checkpoints"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-1.md:133-134"
---

# Review: tasks — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Ingest callback (Task 3.3) contradicts the slice design and changes an excluded area

The slice design says the ingest pass already "accepts" a per-batch callback handed over by 225. The task file's own Context Summary says the code has none: `IngestInputs` has no such field and `_copy` reports nothing. Task 3.3 therefore adds a field to `IngestInputs` and a hook in `ingest_worker.py`. The design's Excluded list bars changes to "the tick pass logic". The task file justifies this as an additive hook and gives a stop-and-raise condition. The slice design is still wrong, though, and the Project Manager has not signed off on the scope change. Amend the slice design (Data Flow step 2 and Excluded) or get the Project Manager's acknowledgement before Task 3 starts. Also settle the process-versus-thread question before implementation. The task defers it to a "stop and raise" clause, so Tasks 3.5–3.8 could stall mid-stream.

### [CONCERN] Cutover connection-budget inputs are not located (Task 7.6)

Step 6 uses "231's recorded extra" and "231 API budget" for the primary cluster, but the task does not say where these live. The pool constants are findable in `src/manta_trading/api_server/tick_access.py` and `src/manta_trading/data/tick/constants.py`. The primary cluster's recorded extra appears only in 231's documents. A junior implementer could invent a number. Name the source file or constant for each term, and say to stop if the primary figure is not recorded as a number.

### [CONCERN] Three separate test tasks for the cutover script is more than the criteria need

Tasks 7.3, 7.5 and 7.7 each add unit tests and a commit around the scaffolding, steps 1–5 and steps 6–9. Together with 7.1 that makes four commits for one script. The slice requires the cutover to be verified (Integration Requirements), but the real cutover run in Task 10.2 already exercises it. Merge 7.3 and 7.5 into one test task, or fold the scaffolding and steps 1–5 into one implement-then-test pair. Keep the tests for the pure logic: the G-suffix parse, the connection arithmetic and the purchase-guard refusal. Cut the step-order and scaffolding tests that restate the `STEPS` list.

### [CONCERN] No test for the informational severity in `render` and `--json` (Tasks 4.3, 4.5)

Task 4.3 changes `HealthCheck`, `render`, `verdict_line` and the `--json` output so informational checks print but do not affect the verdict. Task 4.5's tests check the exit code and the printed rows. They do not assert the `--json` shape or `verdict_line` output for an informational check, so a regression in the shared health code could pass unnoticed. Add one assertion for each of these two paths to 4.5.

### [CONCERN] Closeout commit mixes unrelated content (Tasks 9.2, 10.1, 10.4)

The known-failures list from Task 9.2 and the Project Manager's migration designation from Task 10.1 are both to be quoted in the body of the Task 10.4 `docs:` commit. That commit sits at the end of the slice, far from the work it describes, and mixes test-run results with a report commit. Record the designation in the cutover report, which is already committed. Put the known-failure list in the Task 9.3 commit if there is one, or in the report.

### [NOTE] Execution plan after a failed cutover is missing

Task 10.2 covers the missing-keys stop and a re-run, but not a failed step such as a failed memory check or a connection budget that does not fit. The script is check-then-act and re-runnable, so a general instruction would be enough: diagnose from the report, fix on the branch, and re-run from Task 9.

### [NOTE] No load-test or CI-wiring task, and none is needed

The slice states no NFR of its own. It reuses `test_922_overview_nfr.py` only as a seeded-row test, and Task 2.2 runs it. Because no load test is required, no CI gating task is needed.

### [PASS] Success criteria traceability

- **Functional 1–7:** covered by Tasks 3.7–3.10 and 5.1–5.2, 1.3–1.4 and 2.3, 4.1–4.5, and 6.1–6.2. Functional 1 and 2 are also checked live in 10.3.
- **Technical 1–8:** covered by Tasks 2.2, 1.4, 5.3, 3.2/3.6/3.10/4.5, 2.3, 9, 8.1 and 8.2/8.3.
- **Integration 1–2:** covered by Tasks 10.2 and 10.3.
- **Design-level obligations:** the unit-double-start check (TD2) is covered by 10.3. The architecture Revision Log is covered by 8.4. The `cutover_common.migrate` generalisation is covered by 7.1.
- **Scope creep:** I found none. Every task traces to a criterion or design section.

### [PASS] Sequencing and checkpoints

- **Order:** dependencies run in a valid order, with no cycles.
  - `PassKind.TICK` and migration 060 are committed together, because seeding tests would otherwise break. The task file states this.
  - The heartbeat callback comes before the reporter, then the verb.
  - `units_near_deadline` comes before the health gatherer.
  - The unit file comes before the deploy tests, and the migrate generalisation before the cutover steps.
- **Test-with pattern:** each implementation task is followed by its tests. The tests for 1.1–1.3 sit in 1.4, the same pre-commit block.
- **Commits:** checkpoints fall at 2.4, 3.2, 3.4, 3.6, 3.11, 4.6, 5.4, 6.3, 7.1, 7.3, 7.5, 7.7, 8.5 and 10.4. They are spread through the work rather than batched at the end.
- **Git rules:** no task creates a branch or performs a merge. The push in 10.1 is gated on Project Manager confirmation.

### Run Digest

- Response length: 6769 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 42.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 9
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 9
- Finding-shaped matches — surviving validation: 9
