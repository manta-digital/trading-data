---
docType: review
layer: project
reviewType: tasks
slice: proof-on-existing-data
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20261003
dateUpdated: 20261003
reviewedSha: 21a70e23cbe33e5f8f2cbf1eb67e7bafc0e880a7
revision_number: 3
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 33
turns: 20
promptTokens: 1387363
cachedTokens: 1099776
completionTokens: 74338
reasoningTokens: 63847
durationSeconds: 778.9
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: ci-gating
    summary: "New load test ships with no CI gate and no wiring task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:266-268"
  - id: F002
    severity: concern
    category: task-sizing
    summary: "Task 8.6 is over-scoped for one effort-4 task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:140-153"
  - id: F003
    severity: concern
    category: task-sizing
    summary: "Task 8.3 bundles migration testing with a repo-wide test sweep"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:97-116"
  - id: F004
    severity: concern
    category: task-sizing
    summary: "Task 13.2 bundles an implementation, a test, a destructive run, and verification"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:352-371"
  - id: F005
    severity: concern
    category: test-with-pattern
    summary: "Task 9.4 edits a test file after its section was already committed"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:205-215"
  - id: F006
    severity: note
    category: sequencing
    summary: "Task 8.7's fallback is not reconciled with the 9.1 lock-timeout rule"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:154-162"
  - id: F007
    severity: note
    category: task-sizing
    summary: "Contingent migrations hidden inside task bodies"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:250-256"
  - id: F008
    severity: pass
    category: coverage
    summary: "All nine Success Criteria trace to tasks, and no task is scope creep"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] New load test ships with no CI gate and no wiring task

Task 10.5 creates `test/load/test_226_tick_query_nfr.py`, a new query-latency NFR (Q1–Q4 ≤ 1 s warm). The task states plainly: "It is not wired into CI (CI has no test job; slice 907)." I confirmed the state of the repo — `.github/workflows/ci.yml` is publish-only (`on: push: tags: ["v*"]`), and `scripts/run_tests.py`'s `load` tier is the only path that sets `MT_RUN_LOAD_TESTS=1`. The justification is present and accurate, and slice 907 is named as the owner. But per the review rule, a task breakdown that introduces a load test is expected to include a CI wiring task, or else the NFR has no automatic gate until an unspecified, not-yet-started slice lands. The same holds for the 225 load test re-gate in 9.3. Either add a follow-up task that records a tracking note against slice 907 for this specific test, or explicitly state in the task file that no wiring is possible until 907 and that this is accepted. As written, "CI gating is left implicit."

### [CONCERN] Task 8.6 is over-scoped for one effort-4 task

8.6 bundles four independent deliverables in a single effort-4 task: (1) the `tick_app` compressed-chunk ingest test; (2) conditional extension of `provision_tick_roles.sql` with a minimal grant plus a new privilege test; (3) re-running the provisioning script — which requires a second PM action, and the task itself notes "the PM's one command, as in 13.3" — and confirming the grant on both databases; (4) the coverage-on-a-partial-chunk test. Items (2) and (3) are a distinct workstream: schema change, PM coordination, and cross-database verification. Item (4) is a separate TD 5 row. This should be split into at least two tasks so each has a crisp success criterion (e.g. "the tick_app test passes or records 'no grant needed'" vs "the coverage test passes").

### [CONCERN] Task 8.3 bundles migration testing with a repo-wide test sweep

8.3 combines four things: the `tick_007` migration integration test (empty and populated DBs); a repo-wide search-and-update of every test pinning the tick track's newest migration id or count; flipping the `has_no_compression` assertion to expect compression; and adding the migrations README row. The repo-wide sweep is open-ended (the task's own success criterion is "tests pass; no other integration test newly fails") and would be more reliable as its own task with an inventory step, especially since 9.4 later asks to extend this same test to cover `tick_008`.

### [CONCERN] Task 13.2 bundles an implementation, a test, a destructive run, and verification

13.2 implements a new harness step (`archive-check`, including its own JSON manifest parser because `adopt_files._read_manifest` is private — I confirmed this at `src/manta_trading/data/tick/adopt_files.py` and `src/manta_trading/data/tick/hashing.py:16`), adds a unit test for it, runs `drop-proof` (irreversible), and runs a `pg_database` listing plus the archive verification. The destructive drop should not sit in the same task as newly-written parser code whose test has not yet been committed. Split `archive-check` (implement + test) from the teardown-and-verify run.

### [CONCERN] Task 9.4 edits a test file after its section was already committed

9.4 instructs: "Extend the 8.3 migration test to cover `tick_008`, and the tests that pin the newest tick migration." But 8.3's tests were written and committed at checkpoint 8.8, four sections earlier. If the chunk interval changes, Section 8's committed test file is edited retroactively by a Section 9 task, which breaks the property that a test task immediately follows its implementation task within the same commit. Since this task is conditional, the cleaner form is to fold the `tick_008` migration test into the same conditional task that writes `tick_008` (9.4 already does write the migration), and to accept that the shared "newest migration id" tests may be touched here in a `refactor:`-scoped commit — which is defensible, but the task should say so rather than leaving it as an implied edit to 8.3's deliverable.

### [NOTE] Task 8.7's fallback is not reconciled with the 9.1 lock-timeout rule

If the fallback fires, `TICK_TRADE_COMPRESS_AFTER` moves to a settled age past which supersession and overlap can no longer reach. Task 9.1's rule for `TICK_INGEST_LOCK_TIMEOUT_SECONDS` is "at least 4 x the slowest compressed-chunk supersession delete... Source: the `layouts` report's delete times (6.5), taking the chosen layout's." If the fallback means supersession never touches a compressed chunk in production, that measurement may no longer be the right input. The interaction is not addressed; worth a sentence in 9.1 or 8.7.

### [NOTE] Contingent migrations hidden inside task bodies

10.4 folds a contingent new migration ("the next free `tick_00N`") plus a `final` re-run into its success criterion, and 9.4 folds the conditional `tick_008` into its own. Both are legitimate remedies from the design (Technical Decision 3's "a miss gets an index or read-side aggregate"), and both are conditioned correctly. But as written they are implementation work buried in success criteria, so an implementer could miss that a new migration, its test, and the migration-pin updates are required. If either condition fires, it should be a named task rather than a clause.

### [PASS] All nine Success Criteria trace to tasks, and no task is scope creep

Cross-referenced against the slice design: SC1 (cluster, PM script) → Sections 3–4 in part 1; SC2 (migrated `trading_tick`, 27,691,412 rows, coverage ok) → 10.3, 10.4, 12.3; SC3 (compressed chunks + policy) → 8.2, 10.1, 10.4; SC4 (every TD 3 row measured) → 13.1; SC5 (mapping completeness) → 5.1 in part 1, recorded in 13.1; SC6 (five TD 5 rows) → 8.3 (Migration), 8.5 (Supersession, Overlap), 8.6 (`tick_app`, Coverage); SC7 (bad-header) → Section 1 in part 1; SC8 (`TICK_UNIVERSE`) → 12.1, 12.2; SC9 (proof DB dropped) → 11.1, 13.2. The one deliberate addition — `archive-check` in 13.2 — traces to walkthrough step 8's archive verification and is explicitly reconciled in 14.1 ("Amend the slice design where these tasks differ from it"), so it is a documented design amendment rather than creep. No task lacks a corresponding criterion.

### Run Digest

- Response length: 7848 chars
- Response is newline-free: no
- Tool calls made: 33
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 249927
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1387363 / 1099776 / 74338 / 63847
- Duration: 778.9 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
