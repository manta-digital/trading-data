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
reviewedSha: 21a70e23cbe33e5f8f2cbf1eb67e7bafc0e880a7
revision_number: 3
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 28
turns: 14
promptTokens: 597672
cachedTokens: 563200
completionTokens: 37984
reasoningTokens: 34915
durationSeconds: 162.8
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: concern
    category: sequencing
    summary: "Section 6 queries/layouts have no populated proof database after task 5.6"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
  - id: F002
    severity: note
    category: test-coverage
    summary: "CI gating of the two load tests is deferred, not wired"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F003
    severity: note
    category: sequencing
    summary: "Second PM provisioning run in task 8.6 deviates from the design's \"one PM action\""
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F004
    severity: note
    category: documentation
    summary: "Task 8.4 references the load test as a consumer of the proof database"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F005
    severity: pass
    category: traceability
    summary: "All slice success criteria traced to tasks"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Section 6 queries/layouts have no populated proof database after task 5.6

Task 6.1 (`queries` step) picks the busiest instrument-session "from the ledger," and 6.2/6.3/6.4 compress and measure "every chunk" of the proof database. Both require the full 27.7 M-row dataset loaded in `trading_tick_proof`. But task 5.6 (`batch`) ends with "The proof database is reset afterwards," and the reset is `TRUNCATE tick_trade, tick_ingest_ledger` (defined in 2.2) — which empties both the rows and the ledger. No task between 5.6 and 6.2 re-ingests the full set. The design's own failure-mode table is explicit that the reset happens "before each ingest, so a re-run starts clean" (Technical Decision 2), i.e. it is a pre-ingest reset, not a post-ingest one — so 5.6's "reset afterwards" is inconsistent with the design and leaves Section 6 with nothing to measure. Either 5.6 must not reset at the end, or a re-ingest task must be added before 6.1.

### [NOTE] CI gating of the two load tests is deferred, not wired

Tasks 9.3 and 10.5 both add/run load tests (`test_225_tick_ingest_nfr.py`, new `test_226_tick_query_nfr.py`) but no CI-wiring task gates on them; both files state the deferral is deliberate and owned by slice 907 ("CI runs no test job"). This is explicit rather than implicit, and slice 907 exists as the designated owner, so it is not a blocking gap — but the NFRs ship ungated until 907 lands, which is worth surfacing.

### [NOTE] Second PM provisioning run in task 8.6 deviates from the design's "one PM action"

Task 8.6 conditionally re-runs `scripts/provision_tick_cluster.sh` (the PM's one command) if the `tick_app` compressed-chunk test needs a new grant, and 13.3 conditionally re-runs it again for a memory-setting change. The design (TD 1, Prerequisites) frames provisioning as a single PM action, though it also notes the script is safe to re-run and reconciles settings. This is coordinated and probably intended, but it means the PM may be asked for up to three `sudo` runs during the slice; worth confirming the PM expectation.

### [NOTE] Task 8.4 references the load test as a consumer of the proof database

Task 8.4's success note says the proof database's chosen layout is what "final's comparison and the load test (10.5) use." Task 10.5 describes a self-contained fixture database migrated through the newest tick migration, not the proof database, so the reference to 10.5 consuming the proof database is inaccurate. Harmless but could mislead.

### [PASS] All slice success criteria traced to tasks

Functional criteria 1–9 and the Technical/Integration Requirements each map to concrete tasks: 1→Section 3+3.7, 2→10.3/10.4, 3→8.2/10.1/10.4, 4→Sections 4–6 + 13.1, 5→5.1, 6→8.3/8.5/8.6, 7→1.1–1.4, 8→12.1/12.2, 9→11.1/11.2/13.2; guard test→2.3, `.env` writer test→3.5, checksum before/after→3.6/14.2, timer check→14.3, docs→8.3/14.1/14.2. TD 3's measured table also maps row-for-row (ingest rate, mapping, checks, bytes/record, chunk interval, query latency, batch budget, worker count, intra-unit checkpoints, job timing, capacity). No success criterion is left without a task, and the only tasks without a direct criterion (7.x contention, 12.4 Kalshi diff, 13.2 archive-check) trace to Technical Decisions 7/10 and the walkthrough.

### Run Digest

- Response length: 4272 chars
- Response is newline-free: no
- Tool calls made: 28
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 126808
- Effort: backend default
- Turns: 14
- Tokens — prompt / cached / completion / reasoning: 597672 / 563200 / 37984 / 34915
- Duration: 162.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
