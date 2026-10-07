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
reviewedSha: e572101d81dbaceb70f32ae042c324eaaf5d80fc
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 45
turns: 20
promptTokens: 1100649
cachedTokens: 902016
completionTokens: 88674
reasoningTokens: 82626
durationSeconds: 603.4
runId: run-20261007-p5-1e5c3afc
squadronVersion: 0.20.1
findings:
  - id: F001
    severity: concern
    category: test-spec-correctness
    summary: "Task 3.5's table-list equality test cannot pass as specified"
    location: "src/manta_trading/market/schema/migrations/minute.py:883"
  - id: F002
    severity: concern
    category: test-coverage-gap
    summary: "The allowed-difference column set is never pinned against the migration"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F003
    severity: concern
    category: missing-decision
    summary: "The live archive path has no defined source"
    location: "deploy/backup-clusters.conf"
  - id: F004
    severity: concern
    category: production-safety
    summary: "Which advisory lock the drill holds is ambiguous"
    location: "src/manta_trading/data/tick/constants.py:172"
  - id: F005
    severity: concern
    category: sequencing
    summary: "Task 5.3 depends on a host operation that no step sanctions"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F006
    severity: note
    category: scope
    summary: "`--through-step 5` is interim CLI scaffolding that touches FR1's meaning"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F007
    severity: note
    category: task-ordering
    summary: "Test tasks are batched at the end of sections 3 and 4"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F008
    severity: note
    category: dry
    summary: "Task 2.1 names the BBO columns separately although they are already in `TICK_TRADE_COLUMNS`"
    location: "src/manta_trading/data/tick/storage_columns.py:41"
  - id: F009
    severity: note
    category: traceability
    summary: "The Integration Requirement has no task, by construction"
    location: "project-documents/user/runbooks/200-backup-and-restore.md:646"
  - id: F010
    severity: pass
    category: coverage
    summary: "Required criteria are fully mapped, and the load-test/CI rule is satisfied by an explicit decision"
    location: "project-documents/user/tasks/228-tick-restore-drill.md"
---

# Review: tasks — slice 228

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] Task 3.5's table-list equality test cannot pass as specified

Task 3.5 asserts that "the 3.1 list plus `tick_trade` equals the set of `public` tables in the migrated test DB". The `migrated_tick_db` fixture applies `TRACKS["tick"]`, whose first entry is the reused bootstrap migration `001_schema_migrations` (`src/manta_trading/market/schema/migrations/tick.py:70` takes `next(m for m in MINUTE_MIGRATIONS if m["id"] == BOOTSTRAP_MIGRATION_ID)`), and that migration creates `schema_migrations` in `public` (`src/manta_trading/market/schema/migrations/minute.py:883`). The migrated tick DB therefore holds eight `public` tables: `schema_migrations` plus the seven tick tables. The 3.1 list plus `tick_trade` is seven. The test's stated Success ("passes now") is unreachable. The same boundary is already handled deliberately elsewhere — `provision_tick_roles.sql` calls its write surface "the seven tick tables" and treats `schema_migrations` separately, and `test/integration/data/test_tick_role_privileges.py` subtracts `{LEDGER}` from the `pg_tables` set. Task 3.5 must exclude `schema_migrations` explicitly (and the exclusion should be stated, not silent, or the failure will be diagnosed as a live-rebuild bug at the worst moment). Note the same "every public table" phrasing appears in 6.3 and 6.6, where the extra table is harmless but should still be acknowledged so the row-count check is not mistaken for a seventh tick table.

### [CONCERN] The allowed-difference column set is never pinned against the migration

TD4 ends with: "The set is one constant of `(table, column)` pairs. The exact column names are taken from the tick migrations during implementation and pinned by a test." Task 3.1 builds the constant from the 1.1 findings, and 3.1's own Success criterion only requires that no literal appears *outside* the constant — it says nothing about the literals being real columns. The only migration-vs-code test in the breakdown is 3.5, which covers the *table* list, not the *column* names. A misspelled pair (e.g. `estimated_cost` for `estimated_cost_usd`, or `state_changed_at` vs the migration's actual `state_changed_at`/`fetch_status` pair) silently drops a column from the allowed set, so the drill fails loudly later on the host with a difference the design already declared expected. A pinning test asserting every `(table, column)` in the constant exists in the migrated tick DB's `information_schema.columns` is the design's requirement and has no task. I verified the real column names in `src/manta_trading/market/schema/migrations/tick.py:94-170`; the design's prose description ("estimate fields") is not a column list, so the constant genuinely depends on a test rather than on transcription.

### [CONCERN] The live archive path has no defined source

Steps 1, 3 and 7 all compare against "the live archive" (`/data/tick-archive`): task 5.2 compares the live archive with `restic ls latest` for `/data/tick-archive`, task 6.3 runs that check inside step 1, and task 6.4's step 3 compares "file count and bytes with the live archive". Task 6.1 says to read paths from `load_clusters()` and asserts "No tick path literal", but the cluster table does not carry the archive path — its columns are `cluster, url_key, backup_root, remote_subpath, replication_host, metadata_cron, weekly_base_cron` (`deploy/backup-clusters.conf`). The archive root is `MT_TICK_ARCHIVE_DIR` (`.env`, slice 223; constant at `src/manta_trading/data/tick/constants.py:184`), and runbook 200 notes it is stated in two places that cannot share a constant. Task 6.1 must name the archive's source explicitly (and the same for the restore target path in step 3), or the implementer will either hard-code `/data/tick-archive` — violating 6.1's own success criterion and 9.1's grep check — or invent a third source of truth.

### [CONCERN] Which advisory lock the drill holds is ambiguous

TD1 step 1 says the drill takes "the tick advisory lock" (singular) so "no tick run can change production during the comparison". Task 6.3 reproduces the singular phrasing but then points at "keys in `data/tick/constants.py`" (plural). Two tick locks exist: `TICK_ACQUISITION_LOCK_KEY = 220_000_001` (constants.py:172) and `TICK_INGEST_LOCK_KEY = 220_000_002` (constants.py:240), and 227's helper treats them as a pair (`TICK_LOCK_KEYS = (TICK_ACQUISITION_LOCK_KEY, TICK_INGEST_LOCK_KEY)` in `scripts/cutover_227_host.py`). If the drill takes only the acquisition key, an ingest running concurrently writes to `trading_tick` mid-comparison, which is exactly the condition step 5's lock-verification exists to detect; if it takes both, step 5's `pg_locks` check must look for both. The task should state which key(s) are held and which the check verifies, since the release in 6.6 and the step-5 assertion both depend on it.

### [CONCERN] Task 5.3 depends on a host operation that no step sanctions

Task 5.3 requires a fixture recorded from the real host: a few lines of `deploy/lib/restic_repo.sh --env-file .env --prefix system run -- ls latest --json /data/tick-archive`, into `test/fixtures`, per CLAUDE.md's parser-fixture rule. Sections 1–5 otherwise run in the dev checkout; every other host-touching task is gated to the recorded run (6.7, 7.4, 7.5). This one has no owner, no step, and no host-access prerequisite — and it also presumes the installed restic supports `ls --json`, while the same task hedges the other way ("JSON output if available, else tolerate whitespace variation"). Either 5.3 should capture the fixture in the non-JSON form it will definitely parse, or the capture should be moved into the host run and the parser written against what is actually emitted. If `--json` is not available, the fixture the task names cannot be captured at all.

### [NOTE] `--through-step 5` is interim CLI scaffolding that touches FR1's meaning

Task 6.7 allows an intermediate `--through-step 5` style option to run the drill before steps 6–9 exist. The task acknowledges it must be removed or documented, which is adequate, but note that TD1 step 9 ties exit 0 to *every* check passing (archive, database, fallback) and FR1 repeats it. A retained partial-run mode makes "exits 0" true of a run that never exercised the fallback, which is the one claim this slice exists to make. Removal is the cleaner resolution; if kept, `--help` and the runbook drill section (8.3) must both state what exit 0 means under it.

### [NOTE] Test tasks are batched at the end of sections 3 and 4

The test-with pattern holds in sections 2, 5, 6 and 7, but section 3 implements 3.1, 3.2a–3.2d and 3.3 before any test at 3.4, and section 4 implements 4.1–4.4 before 4.5. Each section is closed by a commit checkpoint, so the exposure is bounded and the section sizes are reasonable; this is a deviation from the pattern rather than a defect. Splitting 3.4's cases across 3.2a–3.2d (each of which has a crisp Success line already) would tighten it if the implementer finds the batched test file unwieldy.

### [NOTE] Task 2.1 names the BBO columns separately although they are already in `TICK_TRADE_COLUMNS`

Task 2.1 builds the hashed list from "`TICK_TRADE_COLUMNS`, the BBO columns, `sequence_ordinal`". The six BBO columns are entries *inside* `TICK_TRADE_COLUMNS` (`src/manta_trading/data/tick/storage_columns.py:41-46`), and `sequence_ordinal` is in `TICK_TRADE_DERIVED_COLUMNS`. Naming the BBO columns a second time invites a duplicate list in the module, which is what TD3's "not typed out, so a new column is hashed automatically" is guarding against. Referring to `TICK_TRADE_COLUMNS` plus `TICK_TRADE_DERIVED_COLUMNS` (minus `unit_id`) would keep the single source. 2.2's unit test ("a column added to `TICK_TRADE_COLUMNS` appears in the generated SQL") already guards the right invariant.

### [NOTE] The Integration Requirement has no task, by construction

The slice's only Integration Requirement — "the next quarterly drill (due 2026-11-17) runs production's Step 6 plus this script" — has no corresponding task. That is consistent with the slice's Excluded section (automating production's drill is out of scope) and with task 8.3, which records the drill alongside production's Step 6 and its due date in runbook 200 (which does carry "Next due: 2026-11-17"). Recording it here so the mapping is explicit rather than assumed: this criterion is satisfied by documentation plus a future manual action, not by anything verifiable at the end of Phase 6.

### [PASS] Required criteria are fully mapped, and the load-test/CI rule is satisfied by an explicit decision

Cross-referencing the slice's Success Criteria against the tasks: FR1 → 7.4; FR2's five items → 6.4 (archive counts/bytes), 6.6 (counts + fingerprint vs production), 7.2 (rebuild fingerprint + bookkeeping differences), 7.1 (rebuild time recorded, not gated), 7.3/7.5 (no leftovers); FR3 → 7.5; FR4 → 8.1/8.2/8.3. The Technical Requirements map one-for-one: fingerprint integration cases → 2.2 (+ the automatic-hashing unit test); all six bookkeeping integration cases → 3.4; leftover handling, `flock`, `auto.conf` guard, snapshot precondition, cleanup path check → 4.5, 5.3, 5.6; calendar-URL override → 6.2; table list vs migration → 3.5; report exit status → 6.2/7.3; ruff/mypy and the host-only full run → 9.1, 7.4. No task lacks a criterion behind it (1.1 traces to TD1's "implementation step 2 confirms" clause, 3.3 to TD1 step 5, 8.1 to Technical Scope item 4), commit checkpoints are spread across all nine sections, and dependency order is respected with no cycles (fingerprint → bookkeeping → lifecycle → host primitives → steps 0–5 → steps 6–9 → runbook → validation; step 3's archive restore precedes the rebuild that adopts from it; step 2's target segment precedes step 4's `check_reached`). On the load/CI rule: the parent slice restates no NFR (no NFR or performance-target text in `project-documents/user/architecture/220-slices.data-acquisition-futures-tick-primary-focus.md` or the 228 design), so no `tests/load/` task and no CI-gating task are required, and the breakdown states that decision explicitly in its Scope bullet rather than leaving it implicit.

### Run Digest

- Response length: 11426 chars
- Response is newline-free: no
- Tool calls made: 45
- Tool calls failed: 1
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 308228
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1100649 / 902016 / 88674 / 82626
- Duration: 603.4 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
