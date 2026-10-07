---
docType: review
layer: project
reviewType: tasks
slice: tick-restore-drill
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/tasks/228-tasks.tick-restore-drill.md
aiModel: deepseek/deepseek-v4.1-flash
status: complete
dateCreated: 20261006
dateUpdated: 20261006
reviewedSha: 459d4a2c98e2184647de364325675dbb2e43bcc9
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 57
turns: 20
promptTokens: 1364270
cachedTokens: 1149696
completionTokens: 110506
reasoningTokens: 103361
durationSeconds: 697.8
runId: run-20261007-p5-1e5c3afc
squadronVersion: 0.20.1
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "6.4 calls `assert_drill_path` on a path that 4.4's contract rejects"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:228-237"
  - id: F002
    severity: concern
    category: consistency
    summary: "4.1's exclusivity claim is contradicted by literals in 5.2, 6.4, 6.5 and 7.3, and its pointer to the confirming grep is stale"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:216"
  - id: F003
    severity: note
    category: design-deviation
    summary: "6.3 takes both tick advisory locks, but the slice design says \"the lock\" (singular)"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:348"
  - id: F004
    severity: note
    category: task-sizing
    summary: "5.7 bundles four unrelated functions and carries its own tests rather than being followed by a test task"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:307"
  - id: F005
    severity: note
    category: task-sizing
    summary: "6.7 mixes a host execution, fixture capture, and possible parser rewrites"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:386"
  - id: F006
    severity: pass
    category: coverage
    summary: "Every Success Criterion maps to at least one task; no task lacks a criterion"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F007
    severity: pass
    category: sequencing
    summary: "Sequencing, test-with pairing, and commit-checkpoint distribution are sound"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F008
    severity: pass
    category: nfr-coverage
    summary: "The absence of a load test and a CI gating task is correctly justified"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
---

# Review: tasks — slice 228

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] 6.4 calls `assert_drill_path` on a path that 4.4's contract rejects

Task 4.4 defines `assert_drill_path(path)` as: "path must be a direct child of `/data/restore-test`, named `228-drill-*`, and contain the marker. Resolve symlinks first. Anything else raises by name." Task 4.5 then pins that contract with tests (unmarked, outside-root, and symlink-escape paths must raise with no `rm` issued).

Task 6.4 (line 362) requires: restic restores into the drill dir, "`assert_drill_path` then `sudo -n chown -R manta:manta` on the restored directory only". The restored archive directory is `<drilldir>/tick-archive` — not a direct child of `/data/restore-test`, and it holds no marker. Implemented to 4.4's text, that call raises on every run, and the step can never pass 6.7. Loosening 4.4 to accept descendants instead breaks 4.5's "outside-root path → no `rm`" case, which is the CLAUDE.md destructive-action guard.

The slice design's intent is unambiguous and different: TD1 step 3 says the chown target "path is checked to sit under the marked drill directory first." That is a *descendant-of-a-marked-drill-dir* predicate, not the *is-the-drill-dir* predicate 4.4 specifies. Either 4.4 needs a second named helper (e.g. `assert_under_drill_dir(path)`) with its own test in 4.5, or 6.4 must assert the parent drill dir and then chown a computed child. As written, the two tasks cannot both be satisfied.

### [CONCERN] 4.1's exclusivity claim is contradicted by literals in 5.2, 6.4, 6.5 and 7.3, and its pointer to the confirming grep is stale

Task 4.1's success criterion states: "no bound or path literal appears anywhere else in the slice's code (grep to confirm at the end of Section 6)". Two verifiable problems:

1. The check is falsified by the file's own siblings. `/data/tick-archive` is stated literally in 5.2 ("`restic ls latest` output for `/data/tick-archive`", line 265) and 6.4 ("restic `restore latest --include /data/tick-archive`", line 362). 6.5 (line 371) carries `/usr/lib/postgresql/17/bin/pg_verifybackup` and says only "define the binary directory once" without assigning it a home. 7.3 (line 420) hard-codes the report directory. Meanwhile 4.1's own constant list covers only the bounds, the drill root, the prefix, the marker and the lock path — it does not name the archive path, the postgres binary directory, the socket mode, the `manta:manta` owner, or the report path. A junior AI cannot tell whether 4.1's list is exhaustive (in which case those literals belong in 4.1) or illustrative (in which case the success criterion is wrong).

2. The pointer is stale. "at the end of Section 6" resolves to 6.1–6.8, none of which runs the grep. The actual check lives in 9.1 (line 481), which also narrows the rule to "no TD6 bound or drill path literal outside `drill_228_lifecycle.py`" — a materially different (and achievable) criterion from 4.1's blanket "no bound or path literal."

4.1 and 9.1 should state one rule, and every literal that rule covers should either live in `drill_228_lifecycle.py` or be listed in 4.1.

### [NOTE] 6.3 takes both tick advisory locks, but the slice design says "the lock" (singular)

Task 6.3 has the drill take `TICK_ACQUISITION_LOCK_KEY` *and* `TICK_INGEST_LOCK_KEY`, and 1.1 (line 66) exists to confirm they are distinct and that ingest may run alongside acquisition. The slice design's TD1 step 1 and TD6 both refer to "the tick advisory lock" in the singular. The task's reasoning is sound — the design's Ordering guarantee ("no tick run can change production during the comparison") is only true if both locks are held, since ingest does not contend with acquisition — but this is a substantive requirement the tasks add that the design does not state, and the task file does not mark it as a deviation. Compare 7.4, which explicitly requires PM approval before widening TD4's allowed set. Either cite the design text that supports taking both, or route it through the same PM-approval path as 7.4.

### [NOTE] 5.7 bundles four unrelated functions and carries its own tests rather than being followed by a test task

Task 5.7 (effort 4) implements scratch-server start/wait/stop, the log parser `last_restored_segment`, the comparison `check_reached`, and `create_drill_database()` — three unrelated concerns in one task — and its success criterion asserts unit tests without naming a file, unlike 5.6 which names `test_drill_228_host.py`. Splitting into (a) `start_scratch` with tests, (b) the log parse plus `check_reached` with tests, and (c) `create_drill_database` would keep the test-with pattern intact and each task's success criterion checkable. 6.7 is the only task that names the real-log and real-`restic ls --json` fixtures the design's CLAUDE.md real-format rule demands, so the test file for 5.7 must survive until 6.7 regardless.

### [NOTE] 6.7 mixes a host execution, fixture capture, and possible parser rewrites

Task 6.7 asks for a host run through step 5, saving real-log and `restic ls --json` fixtures, adding tests that 5.7's and 5.2's parsers read them, and fixing either parser "if it fails" — plus a CLAUDE.md error-evidence protocol. Fixing 5.2 or 5.7 invalidates the success criteria of tasks that are already committed at 5.8, so the outcome of 6.7 is either "nothing to fix" or "two earlier tasks were wrong." Splitting the fixture capture and parser verification into its own task would make 6.7's success criterion ("steps 0-5 pass on the host; no leftovers") cleanly falsifiable.

### [PASS] Every Success Criterion maps to at least one task; no task lacks a criterion

Cross-referencing the slice's Functional Requirements (1–4), Technical Requirements, and Integration Requirement against the task file:

- FR1 (exits 0 on manta9000) → 6.7, 7.4. FR2 (report contents: archive count/bytes, table counts, fingerprints, allowed differences only, rebuild time, no leftovers) → 6.4, 6.6, 7.2, 7.3, 7.4. FR3 (second run exits 0) → 7.5. FR4 (runbook 200: primary procedure, fallback procedure, drill command, record row) → 8.1, 8.2, 8.3.
- Every Technical Requirement unit test has a home: leftover handling and `flock` → 4.7; `postgresql.auto.conf` guard → 5.4/5.6; archive-vs-snapshot with stubbed `restic ls` → 5.2/5.3; cleanup path check with stubbed sudo → 4.4/4.5; calendar URL override vs tick URLs → 6.2; bookkeeping table list equals the tick migration's tables → 3.8; report exit status → 6.2. Every integration case is present: the four fingerprint cases in 2.2, the four bookkeeping cases in 3.3, and "a missing row with archived files fails" in 3.5.
- The Integration Requirement is handled correctly: 8.3 states the wording change is the slice's requirement while running it on 2026-11-17 is the PM's future quarterly drill, so it is deliberately not a task. That is the right call and is explicit.

I verified the tasks' factual anchors against the repository: `migrated_tick_db`/`second_migrated_tick_db` exist in `test/conftest.py`; `TICK_TRADE_COLUMNS` in `src/manta_trading/data/tick/storage_columns.py` does include the six BBO columns, and `sequence_ordinal` plus `unit_id` are in `TICK_TRADE_DERIVED_COLUMNS`, so 2.1's "every `tick_trade` column except `unit_id`" is exactly right; `TICK_LOCK_KEYS` exists in `scripts/cutover_227_host.py`; `provision_tick_roles.sql` accepts `-v tick_db=`; `/usr/lib/postgresql/17/bin/pg_verifybackup` appears in `scripts/backup_prod.sh`; `deploy/backup-clusters.conf`'s tick row carries no archive path; runbook 200 has the "### The tick cluster (slice 227)" heading, the "The full archive-and-database drill is slice 227's" line that 8.2 replaces, and the "Repeat expectation ... Next due: 2026-11-17" paragraph 8.3 edits. Task 3.8's table-count arithmetic also checks out: the tick migration creates exactly seven `public` tables (six bookkeeping plus `tick_trade`) plus `schema_migrations`.

### [PASS] Sequencing, test-with pairing, and commit-checkpoint distribution are sound

Dependencies are respected and acyclic: sections run 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9; 4.6 builds only on 4.4's functions; 5.4/5.5 feed 6.5; 5.7's `create_drill_database` feeds 7.1; 6.1–6.6 feed 6.7; 7.4 feeds 7.5; and 8.3's drill-record row correctly depends on 7.4/7.5 having produced a date, duration and outcome.

Implementation is followed immediately by its test in almost every case (2.1→2.2, 3.2→3.3, 3.4→3.5, 3.6→3.7, 4.2→4.3, 4.4→4.5, 4.6→4.7, 5.4+5.5→5.6, 6.1→6.2). Commit checkpoints appear at 1.2, 2.3, 3.9, 4.8, 5.8, 6.8, 7.6, 8.4 and 9.3 — one per section, spread throughout rather than batched at the end, each carrying a semantic prefix consistent with CLAUDE.md. Task kinds that are pure constants (3.1, 4.1) have no test task, which is appropriate at effort 1–3.

### [PASS] The absence of a load test and a CI gating task is correctly justified

The slice design restates no NFR — a search for "NFR", "performance", "load test", "latency" and "throughput" in `228-slice.tick-restore-drill.md` returns nothing — so the rule requiring a `tests/load/` task for a restated NFR does not apply, and the corresponding CI-gating rule is not triggered. The task file states the position explicitly in its Context Summary ("The drill is a manual quarterly script: no load test and no CI gating task is needed. Its checks are the unit and integration tests below plus the recorded host run"), and the design agrees, resting verification on the recorded host run plus unit and integration tiers. The only timing the drill records — the rebuild time — is explicitly "recorded, not gated" in both 7.1 and the design, which is a deliberate non-gate rather than an omitted one.

### Run Digest

- Response length: 10896 chars
- Response is newline-free: no
- Tool calls made: 57
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 369613
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1364270 / 1149696 / 110506 / 103361
- Duration: 697.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
