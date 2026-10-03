---
docType: review
layer: project
reviewType: tasks
slice: proof-on-existing-data
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20261003
dateUpdated: 20261003
reviewedSha: c2bf7bd4e60cb0af8e7526d4bbd361afad542723
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 49
turns: 20
promptTokens: 1048437
cachedTokens: 900096
completionTokens: 124475
reasoningTokens: 118792
durationSeconds: 717.0
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: testing
    summary: "Constants that govern the restated ingest-throughput NFR are re-set with no load-tier task to re-gate them"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:133-156"
  - id: F002
    severity: concern
    category: testing
    summary: "Task 8.3 instructs updating a tick migration-chain test whose existence I could not confirm"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:98"
  - id: F003
    severity: concern
    category: measurement-coverage
    summary: "The spreads decision has no task that produces its \"measured spread share\""
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md:274-281"
  - id: F004
    severity: concern
    category: task-sizing
    summary: "Task 6.3 (`layouts`, effort 5) is too large for one junior-AI task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md:334"
  - id: F005
    severity: note
    category: documentation
    summary: "`tick_007` is never applied to `trading_tick_proof`, though the design's Implementation Details say to"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md:71-131"
  - id: F006
    severity: note
    category: documentation
    summary: "\"Ten steps from TD 2\" undercounts the design's step table"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md:144"
  - id: F007
    severity: note
    category: task-sizing
    summary: "Run-and-verify tasks are slightly granular"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
  - id: F008
    severity: pass
    category: coverage
    summary: "Success criteria are fully cross-referenced and commit checkpoints are well distributed"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Constants that govern the restated ingest-throughput NFR are re-set with no load-tier task to re-gate them

The slice design restates the architecture's throughput NFR in TD3 ("Slowest unit ≤ 120 s ... the whole proof set ≤ 2 h"), and `test/load/test_225_tick_ingest_nfr.py:65` already gates exactly that bound. Section 9 then changes `TICK_INGEST_WORKERS`, `TICK_DECODE_BATCH_BYTES`, `TICK_INGEST_LOCK_TIMEOUT_SECONDS` and `TICK_TRADE_CHUNK_INTERVAL` — all on the ingest path that load test covers — yet no task in either file updates or re-runs `test/load/`. The project has an established pattern for this (slice 169 added a `test/load/` task when it touched an NFR; `test/load/test_225_tick_ingest_nfr.py:20` documents the tier's gate). Task 14.3's "Full validation" mentions only the unit and integration tiers. Add a task that re-runs the ingest load test against the new constants (and, if it exists, wire the gate per the standing 907/slice-169 out-of-band convention — but state that precedent explicitly rather than leaving CI gating implicit).

### [CONCERN] Task 8.3 instructs updating a tick migration-chain test whose existence I could not confirm

Task 8.3 says "Update the migration-chain test that pins the last migration id." I searched the test tree: `test/unit/market/schema/test_tick_migrations.py:68` asserts only that tick ids are prefixed, unique and ascending — it does not pin the last id — and the only last-id pin I found is the minute track's `test/integration/test_migration_051_052.py:185` (`assert applied[-1] == _MIGRATION_052_ID`). I could not locate a tick-track test pinning the last tick migration id. A junior AI following 8.3 (and 14.3's "with the chain test updated for `tick_007`") may hunt for a test that does not exist. Either name the file explicitly or replace this line with "add a tick migration-chain test if none pins the last id." Note the slice design carries the same reference, so the ambiguity originates upstream.

### [CONCERN] The spreads decision has no task that produces its "measured spread share"

Task 13.1 requires the spreads recommendation "from the measured spread share in both tiers," and the design's TD9 says the same. The nearest producing task is 5.3 (`size`), which records bytes-per-record and "rows per instrument per chunk" for skew evidence — not a spread-vs-outright classification. The design cites 224's 2.35 % figure for trades only, so the tbbo share is unmeasured, and no task computes either from the ledger. Add a step (or extend 5.3) that derives spread share per tier from the ledger and has it recorded as a report, otherwise task 13.1 asks for a number no earlier task supplies.

### [CONCERN] Task 6.3 (`layouts`, effort 5) is too large for one junior-AI task

Task 6.3 bundles: decompressing all chunks; applying layout A, compressing every chunk, measuring per-tier compressed bytes/row, and running the query set three times; repeating all of that for layout B; timing the supersession delete on a compressed chunk under each layout; verifying TimescaleDB 2.29's unique-key/segmentby-orderby rule; and applying the TD4 decision rule. It is the only effort-5 item in either file, and each of those is independently failable and independently reportable. Split it into at least (a) layout A measure, (b) layout B measure, (c) decision + unique-key verification, so a failure in one half doesn't cost a re-run of the whole thing.

### [NOTE] `tick_007` is never applied to `trading_tick_proof`, though the design's Implementation Details say to

The design says "Applying it: test cluster first ... Then `trading_tick_proof`, then `trading_tick`," but Section 8's success criteria cover only the test cluster and (in Section 10) `trading_tick`. This is probably harmless — the `layouts` step sets the columnstore settings directly rather than through the migration (TD4), and the design's own walkthrough (step 6) never applies `tick_007` to the proof database — but the discrepancy should be reconciled so the junior AI knows the proof DB is intentionally excluded.

### [NOTE] "Ten steps from TD 2" undercounts the design's step table

Task 2.2's success line says "the dispatcher lists the ten steps from TD 2." TD 2's table (slice design lines 298–308) lists eleven rows — `rebuild`, `workers`, `batch`, `jobs`, `mapping`, `size`, `layouts`, `queries`, `contention`, `final`, `drop-proof`. State the count exactly (or name the steps) so the dispatcher's expected surface is unambiguous.

### [NOTE] Run-and-verify tasks are slightly granular

Tasks 5.2 ("Run `mapping`", effort 1), 5.5 ("Run `size` and `jobs`"), 6.4 ("Run `queries` and `layouts`") and 10.3 ("Run `final`") are all "execute the step just implemented and read the report." They are low-risk but could fold into the preceding implementation task without losing any success criterion, which would shorten the checklist.

### [PASS] Success criteria are fully cross-referenced and commit checkpoints are well distributed

All nine functional success criteria trace to tasks (SC1→3.7, SC2→4.2/10.2/10.3/12.3, SC3→10.1/10.3, SC4→Sections 5–7 + 13.1, SC5→5.1/5.2, SC6→8.4/8.5, SC7→Section 1, SC8→12.1/12.2, SC9→13.2), as do the technical requirements (guard unit test→2.3, `.env`/secret tests→3.5, docs→8.3/14.1/14.2) and integration requirements (11.3's deferred drop, 14.2's `17/main` checksum comparison). Sequenced work follows the design's Development Approach order, no circular dependency exists (11.3's forward reference to 13.2 is a reorder, not a cycle), commits land at roughly every 1–2 sections (1.5, 3.6, 4.2, 5.5, 5.8, 6.4, 7.5, 8.6, 9.2, 10.3, 11.3, 12.4, 13.2, 14.2) rather than batched at the end, and no task traces to nothing in the design.

### Run Digest

- Response length: 7277 chars
- Response is newline-free: no
- Tool calls made: 49
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 435879
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1048437 / 900096 / 124475 / 118792
- Duration: 717.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
