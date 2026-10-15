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
sourceDocument: project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261014
dateUpdated: 20261014
reviewedSha: 2f87ecf59a737c86dad402ac7fb02ff77e8ee6a8
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 11
durationSeconds: 46.3
runId: run-20261015-p5-7ef29d51
squadronVersion: 0.21.9
findings:
  - id: F001
    severity: pass
    category: coverage
    summary: "Success criteria coverage for part 2"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Sequencing and checkpoints"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Task 9.2 depends on a later task's commit body"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:262-263"
  - id: F004
    severity: concern
    category: proportionality
    summary: "Cutover and env-script tests are heavier than a run-once host script needs"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:90-101, 133-137, 188-196"
  - id: F005
    severity: concern
    category: clarity
    summary: "Step 6 has no pointer to 231's recorded connection budget"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:167-173"
  - id: F006
    severity: note
    category: clarity
    summary: "Task 10.3 states the estimate-only guard twice, with different scope"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:299-300, 309-311"
  - id: F007
    severity: note
    category: clarity
    summary: "Loose references that rely on the implementer's judgement"
    location: "project-documents/user/tasks/234-tasks.tick-pass-production-wiring-2.md:217-218, 249-250"
  - id: F008
    severity: note
    category: coverage
    summary: "No load-test or CI-gate task is needed"
    location: "project-documents/user/slices/234-slice.tick-pass-production-wiring.md"
---

# Review: tasks — slice 234

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Success criteria coverage for part 2

Every criterion in this part's scope traces to a task:
- Functional 7 (`add-tick-env.sh`) → 6.1–6.2.
- Technical 2–3 (deploy tests and manual-iff-no-timer) → 5.3.
- Technical 6 (static checks, test tiers) → 9.
- Technical 7 (runbook 100) → 8.1.
- Technical 8 (Kalshi diff, slice-mapping rows) → 8.2–8.3.
- Integration 1–2 (cutover report, `/api/v1/futures/*`) → 10.2–10.3.
- Architecture supersession and the realtime/API answers → 8.4.
- TD2 double-start verification → 10.3.

Parts 1 and 2 together cover every criterion. Nothing in this part falls outside the slice design. The `cutover_common.migrate` generalisation (7.1) is justified by Interfaces Required and Development Approach step 6. I confirmed that `cutover_267_historical.py:393` is the only caller. The 921 and 268 scripts don't call it, and 265 and 922 define their own.

### [PASS] Sequencing and checkpoints

There are no circular dependencies. Task 5.1 provides the unit file that Task 1.4's manual-iff-no-timer test needs, and 5.3 picks it up. 7.1 comes before the migrate step in 7.4. Commits are spread across the part, one per section plus 7.1, 7.3, 7.5 and 7.7. Each test task follows its implementation task. Merging is correctly left to Phase 7. The push to origin, the migration designation and the issue closures all wait for Project Manager confirmation.

### [CONCERN] Task 9.2 depends on a later task's commit body

The success line for 9.2 says the known failures seen "are listed in the body of the Task 10.4 commit". Task 9 therefore can't finish until Task 10 commits. 10.4 is a `docs:` commit about the cutover report, which is the wrong place for test-tier results. Record the failures in the Task 9.3 commit if there is one. Otherwise write them to a short note, or into the cutover report, and drop the cross-task dependency.

### [CONCERN] Cutover and env-script tests are heavier than a run-once host script needs

Task 7 has three test tasks (7.3, 7.5, 7.7) that grow `test_cutover_234.py`. Task 7.7 alone lists eight assertions. Most of them re-test `cutover_common` behaviour (stop at first failure, release in `finally`) or restate step definitions.

Task 6.2 requires an "injectable command prefix" so that chown/chmod can be asserted. That dictates a design choice in a one-time root script that the slice doesn't require.

Keep the assertions that guard real risk:
- The purchase guard refuses when anything is wanted.
- Step 7 sits immediately before step 8.
- `MemoryMax` parsing is 1024-based and fails on a mismatch.
- The connection check fails at its boundary.
- The report carries no secret values.

Merge the scaffolding and step tests into one or two tasks. In 6.2, allow the chown/chmod assertion to be done by inspecting the script when root can't be exercised.

### [CONCERN] Step 6 has no pointer to 231's recorded connection budget

Task 7.6 says "231's recorded extra + 1" for the primary cluster and "231 API budget" for the tick cluster. It doesn't say where either value is recorded. The slice names the pool constants (`TICK_API_POOL_MAX`) but not the location of the primary extra. A junior implementer could invent a literal, which the "no new worker arithmetic" rule forbids. Name the 231 file or constant to read. If the primary extra exists only in prose, say so and have the implementer stop and ask.

### [NOTE] Task 10.3 states the estimate-only guard twice, with different scope

The first bullet requires a guard run "immediately before each manual `sudo mt-run tick`". The TD2 bullet says "one guard run covers both starts". Both are workable. State the rule once: one guard run before each distinct start, with the concurrent pair counting as one.

### [NOTE] Loose references that rely on the implementer's judgement

Task 8.2 leaves the location of the contract diff open: "the slice design's section or a short note". Task 9.1 points at "the project memory" for the mypy invocation. Both are acceptable, but pick the 8.2 location now so the artifact has one home. Give the mypy command inline in 9.1.

### [NOTE] No load-test or CI-gate task is needed

The slice restates no NFR. `test_922_overview_nfr.py` is existing coverage that only needs the 060 migration. Adding a `tests/load/` task or a CI wiring task would be scope creep.

### Run Digest

- Response length: 5558 chars
- Response is newline-free: no
- Tool calls made: 11
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 46.3 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
