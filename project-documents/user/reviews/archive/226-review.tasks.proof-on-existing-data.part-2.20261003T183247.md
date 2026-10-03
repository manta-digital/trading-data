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
reviewedSha: 4d41395774709faed95511005c1110b0fda69751
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 53
turns: 20
promptTokens: 1066817
cachedTokens: 905984
completionTokens: 84966
reasoningTokens: 74183
durationSeconds: 789.5
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: pass
    category: traceability
    summary: "Every Technical Decision 5 row traces to a task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:115-136"
  - id: F002
    severity: pass
    category: sequencing
    summary: "Test-with pattern and commit checkpoints are respected"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:202-208"
  - id: F003
    severity: pass
    category: testing
    summary: "The load tier's production-URL guard is accounted for"
    location: "test/load/test_167_data_status_nfr.py:214"
  - id: F004
    severity: concern
    category: ci-gating
    summary: "A load test is added with no CI-gating task and no statement that CI cannot gate it"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:226-237"
  - id: F005
    severity: concern
    category: sequencing
    summary: "Fixes routed into `tick_007` after it is applied will never take effect"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:220-225"
  - id: F006
    severity: concern
    category: sequencing
    summary: "The chunk-interval change has no migration or rebuild task, and contradicts 10.2"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:156-171"
  - id: F007
    severity: concern
    category: coverage-gap
    summary: "The go/no-go's \"resident memory under ingest\" input has no producing task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:303"
  - id: F008
    severity: concern
    category: test-environment
    summary: "Tier invocations bypass the repository's reviewed test runner"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:52"
  - id: F009
    severity: note
    category: verification-scope
    summary: "14.3 verifies the functional criteria only"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:370-378"
  - id: F010
    severity: note
    category: testing
    summary: "The query-latency load test exercises one range of the two the design measures"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:226-237"
  - id: F011
    severity: note
    category: documentation
    summary: "Task 10.1a breaks the section's numbering"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:202-208"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [PASS] Every Technical Decision 5 row traces to a task

Success Criterion 6 ("the five compressed-chunk tests in Technical Decision 5 pass") is fully covered: supersession and overlap in 8.5, `tick_app` ingest and coverage-on-a-partial-chunk in 8.6, and the Migration row in 8.3. 8.5/8.6 also carry the design's "a failure is never fixed by dropping compression" rule and its escalation to 8.7 → 13.1, so the fallback path is not orphaned.

### [PASS] Test-with pattern and commit checkpoints are respected

10.1a places the guard test immediately after 10.1's implementation; 8.2 → 8.3, 11.1 → 11.2, and 13.2's step-plus-unit-test follow the same shape. Checkpoints are distributed rather than batched: 8.8, 9.2, 10.3, 10.4, 11.2, 12.4, 13.2, 14.2 each end a section with a commit, matching the convention in 223/224/225 task files.

### [PASS] The load tier's production-URL guard is accounted for

10.4's success condition — "the load tier's `test_load_tier_never_references_prod_db_url` still passes" — names a guard that actually exists, and the new test's "never reads the production URL variable" requirement matches how `test/load/test_225_tick_ingest_nfr.py` is written. This part of the escape-proofing is real, not aspirational.

### [CONCERN] A load test is added with no CI-gating task and no statement that CI cannot gate it

10.4 creates `test/load/test_226_tick_query_nfr.py` and says only that its docstring "names the gate (`MT_RUN_LOAD_TESTS=1`)". No task in either part wires that gate anywhere, and neither part mentions CI or slice 907. I verified `.github/workflows/ci.yml` contains only a tag-triggered `publish` job and no test job, so the gate genuinely is unenforced — but 225's equivalent task (`225-tasks.ingest-pass-and-proof-parity-2.md:110-111`) stated this explicitly ("CI runs no test job (slice 907), so this gate is the load tier's gate"). The 226 breakdown leaves the same situation implicit, and 14.3's "Re-run the load tier gate" is a manual action, not a gate. Add a sentence (or a small task) naming the local gate and the CI owner, as 225 did.

### [CONCERN] Fixes routed into `tick_007` after it is applied will never take effect

10.3's rule for a Q1–Q4 miss is "gets an index or read-side aggregate in `tick_007` and is re-measured", but by then `tick_007` has already been applied to `trading_tick_proof` (8.4) and to `trading_tick` (10.2). `apply_migrations` skips any id present in `schema_migrations` (`src/manta_trading/market/schema/runner.py`, the `if migration["id"] in applied: continue` loop), so editing `tick_007`'s SQL afterward changes nothing on either database and the re-measurement would silently measure the old layout. The same escape hatch appears in TD 4. Either add a new migration id for the index/aggregate or state that the change is applied with an explicit DDL run plus a re-measure; "edit tick_007" is not sufficient.

### [CONCERN] The chunk-interval change has no migration or rebuild task, and contradicts 10.2

9.1 says for `TICK_TRADE_CHUNK_INTERVAL`: "If it changes, add the migration and plan the rebuild." There is no task that owns that migration or the resulting rebuild, and Section 10 is written as if it never happens: 10.2's success is "migration through `tick_007`" and the production rebuild/`final` sequence assumes `tick_007` is the newest tick migration. Since the migration is applied to the proof database at 8.4 and to production at 10.2, a chunk-interval change discovered at 9.1 has to invalidate both. Either scope the interval change out of this part explicitly (record-only, defer the migration) or name the task that adds the follow-on migration and re-does the rebuild/`final`.

### [CONCERN] The go/no-go's "resident memory under ingest" input has no producing task

13.1 requires the go/no-go to settle "tick cluster memory (buffer hit ratio and resident memory; confirm or lower `shared_buffers`)", matching the slice design's Technical Decision 1 ("the cluster's resident memory under ingest"). The buffer hit ratio is produced (6.1 records it), but no harness step collects the *cluster's* resident memory: part 1's steps record host CPU, `MemAvailable`/`Committed_AS`/`CommitLimit`, per-device I/O, and per-worker RSS in the `batch` step — none of which is the cluster's RSS or its shared-buffer working set. Either add the measurement to the `queries`/`contention` step (for example per-backend RSS from `pg_stat_activity`/proc during ingest) or drop "resident memory" from 13.1 so the decision rests on what is actually measured.

### [CONCERN] Tier invocations bypass the repository's reviewed test runner

Both parts instruct the implementer to "Export `MT_TIMESCALE_TEST_URL` from `.env` with the quotes stripped" and say nothing about `scripts/run_tests.py`, which the repo documents as "the reviewed entry point for DB-touching test tiers" built after a 2026-08-04 production incident (`TRUNCATE` on six production tables). The runner is what enforces the per-tier allowlist and the production-URL scrub (`test/conftest.py:pytest_configure`), and it is also what supplies `MT_RUN_LOAD_TESTS=1` for the load tier (9.3, 10.4). Hand-rolled exports are exactly the pattern the runner exists to replace, and the design's walkthrough (`set -a; . ./.env; set +a`) is the shell-source form the runner's docstring calls unusable in this project. Point the three tiers at `python scripts/run_tests.py unit|integration|load` and keep the hand-export only for the walkthrough's CLI commands.

### [NOTE] 14.3 verifies the functional criteria only

14.3 walks the Verification Walkthrough "and tick each expected result against Success Criteria 1–9" — the functional list. One integration requirement is not visibly confirmed anywhere: "The Kalshi, minute, daily and health timers fire as scheduled throughout. The harness stops no timer and fires no pass." 7.4 reads the Kalshi timer's next elapse, but nothing checks that the other timers were undisturbed. A single line in 14.3 (or in 7.5's report) would close it.

### [NOTE] The query-latency load test exercises one range of the two the design measures

10.4 ingests "the largest real adopted day" and runs Q1–Q4 against that day's compressed chunk. Technical Decision 3 picks one instrument-session per range (Aug–Sep `trades`, Nov–Dec `tbbo`) and runs the query set "uncompressed and under each layout" in both. The ≤ 1 s bound is per-query, so a single day is defensible, but the task should say which range it covers (the archive's largest day is 2024-09-03 `trades`, per `test/load/test_225_tick_ingest_nfr.py`) so the coverage limit is explicit rather than incidental.

### [NOTE] Task 10.1a breaks the section's numbering

The inserted guard-test task is labelled `10.1a`, which does not exist in the other task files' convention (`8.5`, `13.2`, `14.3` are all sequential). Renumbering to 10.2 and shifting the rest is cosmetic, but leaving a lettered subtask makes cross-reference from 14.3 and any future review noisier than it needs to be.

### Run Digest

- Response length: 8834 chars
- Response is newline-free: no
- Tool calls made: 53
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 291517
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1066817 / 905984 / 84966 / 74183
- Duration: 789.5 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
