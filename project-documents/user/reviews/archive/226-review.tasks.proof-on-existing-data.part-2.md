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
reviewedSha: c2bf7bd4e60cb0af8e7526d4bbd361afad542723
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 40
turns: 20
promptTokens: 875226
cachedTokens: 738816
completionTokens: 104652
reasoningTokens: 93247
durationSeconds: 909.3
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: pass
    category: traceability
    summary: "Every success criterion traces to at least one task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F002
    severity: concern
    category: error-handling
    summary: "`sha256sum -c` is prescribed against a JSON manifest it cannot parse"
    location: "src/manta_trading/data/tick/adopt_files.py:34"
  - id: F003
    severity: concern
    category: testing
    summary: "The restated query-latency NFR has no `test/load/` coverage and no gate"
    location: "test/load"
  - id: F004
    severity: concern
    category: error-handling
    summary: "The compressed-chunk fallback branch has no follow-through task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F005
    severity: concern
    category: sequencing
    summary: "`tick_007` is never applied to `trading_tick_proof`"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F006
    severity: concern
    category: testing
    summary: "`final`'s production write guard has no test"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F007
    severity: note
    category: sequencing
    summary: "The part 1 dispatcher criterion predates two steps it must list"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md:144"
  - id: F008
    severity: note
    category: operational
    summary: "No owner for the provisioning re-run if the go/no-go moves `shared_buffers`"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F009
    severity: note
    category: traceability
    summary: "Task 9.1 names no source report for the lock-timeout rule"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F010
    severity: note
    category: traceability
    summary: "Two standing 220 obligations have design content but no task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F011
    severity: note
    category: scoping
    summary: "Task 11.3 is a no-op"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [PASS] Every success criterion traces to at least one task

SC1 → part 1 §3 (3.1–3.7, 3.7 names the criterion); SC2 → 10.2/10.3; SC3 → 8.2 (policy) + 10.1/10.3 (compression); SC4 → all eleven TD 3 rows have a producing task (`5.1` mapping, `5.3` size, `5.4` jobs, `5.6` workers, `5.7` batch, `6.2` queries, `6.3` layouts, `4.1`/`10.3` ingest checks/rate, `9.1` interval, `13.1` intra-unit checkpoints, capacity); SC5 → 5.1/5.2; SC6 → the five TD 5 rows land in 8.3 (Migration), 8.4 (Supersession, Overlap) and 8.5 (`tick_app`, coverage-on-partial); SC7 → part 1 §1; SC8 → 12.1/12.2; SC9 → 11.1–11.3 + 13.2. No task in either half fails to trace back to a criterion or a stated technical/integration requirement, so there is no scope creep to flag.

### [CONCERN] `sha256sum -c` is prescribed against a JSON manifest it cannot parse

Task 13.2 requires `sha256sum -c` against each job's `manifest.json` and makes "every archive file verifies" the success condition. The archive manifest is not in `sha256sum` checksum format: adoption reads it as JSON (`_Manifest` / `_ManifestEntry` at `adopt_files.py:118-130`) with a `files` list of `{filename, size, hash: "sha256:<lowercase hex>"}` objects, and `MANIFEST_NAME` is deliberately *not* listed among its own entries (`adopt_files.py:34`, and `archive_job_files` hashes the manifest separately). `sha256sum -c` on that file finds no properly formatted checksum lines and cannot confirm a single archive file, so the task's stated success cannot be reached as written. The design's own verification intent ("verify every file against the job's `manifest.json` (size and SHA-256)", 223) is what the task should call for — e.g. re-running the adoption path read-only, or a small verification step in `scripts/proof_226_tick.py` — rather than the coreutils tool.

### [CONCERN] The restated query-latency NFR has no `test/load/` coverage and no gate

The slice design restates two performance bounds: the ingest-rate bound (slowest unit ≤ 120 s, whole set ≤ 2 h) and a new one, "Q1–Q4 execute in ≤ 1 s warm on the chosen layout" (TD 3), which compression newly threatens. `test/load/test_225_tick_ingest_nfr.py` covers the ingest bound on the *uncompressed* path (unit budget 120 s, loop-gap bound), and `test/load/test_224_tick_pass_nfr.py` covers the pass. Neither half of this task breakdown re-runs the existing load test against the compressed layout, and no task adds a load test for the query bound — it is checked only as one-shot harness output in `10.3` and as an integration-test assertion in 8.3/8.4, neither of which is repeatable evidence for an NFR. CI gating is likewise left implicit: `.github/workflows/ci.yml` has only the tag-gated publish job (no test job at all), the load tier is manually gated on `MT_RUN_LOAD_TESTS=1`, and the designated owner of that wiring is slice 907, which is still open. A task stating the gate for these NFR measurements (and naming 907 as the CI owner) is needed so the new ≤ 1 s bound is not an aspiration.

### [CONCERN] The compressed-chunk fallback branch has no follow-through task

Task 8.4 says that if a compressed-chunk path cannot be made to work, "the design then falls back to compressing only past a settled age". The design's mitigation says the same, and it changes `TICK_TRADE_COMPRESS_AFTER` and what `final` compresses. Tasks 10.1 and 10.3, however, are written unconditionally ("compress every eligible chunk", SC3 "every eligible chunk … is compressed"), and 9.1 decides `TICK_TRADE_COMPRESS_AFTER` without any branch for the fallback. If 8.4 trips, the breakdown has no task that lowers the compress-after age, no task that amends `final`'s "every eligible chunk" clause, and no task that records the fallback in the go/no-go beyond the design's prose. This is exactly the case the risk section calls the alternative outcome, so it should be a named task with a named owner rather than implied by "stop and report".

### [CONCERN] `tick_007` is never applied to `trading_tick_proof`

The design states the application order explicitly: "test cluster first, through the integration tier. Then `trading_tick_proof`, then `trading_tick`, with `mt data migrate apply --track tick` and the maintenance URL of each." Task 10.2 applies it to `trading_tick` only; 8.3 applies it on the integration fixture's throwaway databases. No task migrates the proof database. Part 1's `rebuild` (4.1) runs the tick chain while `tick_007` does not yet exist (it is written in §8), so `trading_tick_proof` can never be at the layout the migration lands — which is the state `layouts` (6.3) and the supersession-delete timing for the lock-timeout rule were measured against. Either the stated order should be dropped from the design or a task should carry it; as it stands a documented step has no owner.

### [CONCERN] `final`'s production write guard has no test

Task 10.1 wires a guard that is the inverse of every other harness guard: the compression must only run when the connection's database equals the one named in `MT_TICK_DB_URL` (i.e. `trading_tick`), because this is the harness's only write to production. The Technical Requirements' guard test — implemented in part 1 §2.3 — covers only refusals against `TICK_PROOF_DB_NAME`, so the one destructive harness function that targets the keeper database is the one with no unit test, and §10 has no test task analogous to 11.2's `drop-proof` tests. Unlike the read-only measurement steps, "the harness itself is verified by the walkthrough" does not apply here: a wrong-database compression is a production write.

### [NOTE] The part 1 dispatcher criterion predates two steps it must list

Task 2.2's success is "the dispatcher lists the ten steps from TD 2", but TD 2's table lists eleven (`rebuild`, `workers`, `batch`, `jobs`, `mapping`, `size`, `layouts`, `queries`, `contention`, `final`, `drop-proof`), and `final` (10.1) and `drop-proof` (11.1) are implemented only in part 2's later sections. The count and the ownership of those two steps should be stated once so the part 1 dispatcher check is not unsatisfiable at the time it runs.

### [NOTE] No owner for the provisioning re-run if the go/no-go moves `shared_buffers`

Task 13.1 records "confirm or lower `shared_buffers`" from the buffer-hit-ratio and resident-memory measurements, and TD 1 says that if the memory block changes, the PM re-runs the provisioning script, which restarts only the tick cluster. Tasks 13.2 and 14.2/14.3 do not mention re-running it, and 14.3 (the walkthrough pass) does not re-check the memory settings against the recorded values after such a change. A half-line in 13.1 or a small task naming who re-runs the script and what is re-verified would close it.

### [NOTE] Task 9.1 names no source report for the lock-timeout rule

Every constant in 9.1 names the report it is read from except `TICK_INGEST_LOCK_TIMEOUT_SECONDS`, whose input — the timed supersession delete on a compressed chunk — is produced by part 1's `layouts` step (6.3) as a side measurement. Success for 9.1 requires the "source report" column to be filled for every row, so the source should be named here rather than left to be inferred.

### [NOTE] Two standing 220 obligations have design content but no task

The plan's standing obligations on every 220 slice include a "realtime paths" check naming any decision that rules out path A or path B, and answering "does this belong in the API?" for any new surface. TD 10 answers both (neither path is ruled out; no new surface), and 12.4 covers the third obligation (the Kalshi contract diff), but task 14.1's document list does not carry these two into any committed document — the Revision Log list it names is limited to arbitration, pass form, delivery mode and storage. A line in 14.1 recording both answers (even as "no change") makes the obligation auditable.

### [NOTE] Task 11.3 is a no-op

Task 11.3's only content is "do not run `drop-proof` yet; 13.2 runs it", with the commit folded into 11.1/11.2. It is a reorder note rather than work. As written it is harmless, but it inflates the section; the ordering constraint would read better as a line inside 11.1 and 13.2.

### Run Digest

- Response length: 10105 chars
- Response is newline-free: no
- Tool calls made: 40
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 366009
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 875226 / 738816 / 104652 / 93247
- Duration: 909.3 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 11
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 11
- Finding-shaped matches — surviving validation: 11
