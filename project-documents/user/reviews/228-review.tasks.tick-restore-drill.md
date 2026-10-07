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
reviewedSha: a03695bd125eb193fc009965c3774da034193c96
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 60
turns: 20
promptTokens: 1198349
cachedTokens: 848768
completionTokens: 104154
reasoningTokens: 97625
durationSeconds: 855.0
runId: run-20261007-p5-1e5c3afc
squadronVersion: 0.20.1
findings:
  - id: F001
    severity: pass
    category: requirements-coverage
    summary: "Every slice success criterion maps to a task"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md"
  - id: F002
    severity: pass
    category: delivery-hygiene
    summary: "Commit checkpoints are distributed, not batched"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F003
    severity: concern
    category: sequencing
    summary: "Task 1.1's success criterion depends on a file created in Section 3"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:82"
  - id: F004
    severity: concern
    category: sequencing
    summary: "Task 5.7's parser test depends on a fixture captured in task 6.7"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:275"
  - id: F005
    severity: concern
    category: test-correctness
    summary: "Task 3.5's table-set equality is unachievable by one of the two methods it names"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F006
    severity: note
    category: delivery-hygiene
    summary: "Section 1 has no commit checkpoint"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:62"
  - id: F007
    severity: note
    category: implementation-clarity
    summary: "Task 6.5 says \"run `pg_verifybackup`\" without the versioned path"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F008
    severity: note
    category: verification-scope
    summary: "Task 9.1's mypy scope and diff ref may not apply to this slice"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md:424-441"
  - id: F009
    severity: note
    category: task-sizing
    summary: "Task 3.2 is the largest unit and bundles four concerns"
    location: "project-documents/user/tasks/228-tasks.tick-restore-drill.md"
  - id: F010
    severity: note
    category: nfr-coverage
    summary: "No load test or CI-gating task is required for this slice"
    location: "project-documents/user/slices/228-slice.tick-restore-drill.md"
---

# Review: tasks — slice 228

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [PASS] Every slice success criterion maps to a task

Cross-referenced all of FR1–FR4, the Technical Requirements (integration and unit test lists), and the Integration Requirement against the task file. Each has a home: FR1→6.1/7.4; FR2→6.4 (archive count/bytes), 6.6 (counts + fingerprint), 7.2 (rebuild fingerprint + expected differences), 7.1 (rebuild time), 7.3/7.5 (no leftovers); FR3→7.5; FR4→8.1/8.2/8.3. The unit-test list maps to 3.5, 4.5, 5.3, 5.6, 6.2, 7.3; the integration list to 2.2 and 3.4; "runs only on the host" to 6.7/7.4; "ruff and mypy clean" to 9.1. The three exceptions are recorded as CONCERNs below. No task traces to a criterion the design does not state — 3.3 (`table_md5`), 5.2 (archive-vs-snapshot) and 6.2 (`rebuild_env`) all trace to TD1 steps 5, 1 and 6 respectively.

### [PASS] Commit checkpoints are distributed, not batched

Eight checkpoints (2.3, 3.6, 4.6, 5.8, 6.8, 7.6, 8.4, 9.3) close Sections 2–9, one per coherent unit of work, none deferred to the end. Section 1 is the sole exception and is noted separately.

### [CONCERN] Task 1.1's success criterion depends on a file created in Section 3

Task 1.1's stated success is "a short findings block is added to the top of the `drill_228_bookkeeping.py` docstring (or the 1.1 commit message)". `drill_228_bookkeeping.py` is not created until task 3.1 ("Create `scripts/drill_228_bookkeeping.py`", Section 3), so the primary success path is unattainable when 1.1 runs. Only the parenthetical fallback (put the findings in the commit message) remains, and the task does not designate it as the operative choice. This matters because 1.1's recorded facts — the six tables' columns, keys and id-valued columns — are the input to 3.1's expected-difference constant, so the dependency is real and directional. Either name the 1.1 commit message as the sole destination, or move the docstring clause to 3.1.

### [CONCERN] Task 5.7's parser test depends on a fixture captured in task 6.7

5.7's success says "unit tests cover the log parser on a real recovery log excerpt (captured during Section 7)", while 6.7 says "the log excerpt from recovery is saved as the fixture for 5.7's parser test, which is completed now." The two disagree: the excerpt is captured in 6.7 (Section 6), not Section 7. More importantly, 5.7 sits in Section 5 and its success criterion cannot be met there — it requires a host run (6.7) that happens two sections later, and 6.7 itself has to reach back and finish a Section 5 task. That breaks independent completability for both tasks and leaves 5.7's parser test without a real-format fixture when Section 5's commit checkpoint (5.8) is taken. The fixture capture should be its own explicitly-ordered step, or 5.7's test should be split out to immediately follow 6.7.

### [CONCERN] Task 3.5's table-set equality is unachievable by one of the two methods it names

Task 3.5 requires that "the 3.1 list plus `tick_trade` equals the set of tables the tick migration creates (read from the migration module or the migrated test DB's `public` tables)." The tick track includes the bootstrap migration `001_schema_migrations`, whose SQL creates the `schema_migrations` table — verified at `src/manta_trading/market/schema/migrations/minute.py:880-883`, and that migration is the first entry of `TICK_MIGRATIONS` in `src/manta_trading/market/schema/migrations/tick.py`. A migrated tick database's `public` schema therefore holds eight tables (the six bookkeeping tables, `tick_trade`, and `schema_migrations`), while the design's list is seven. Read via the second method the task offers, the test fails for a reason unrelated to the behaviour it is meant to protect. The design at `project-documents/user/slices/228-slice.tick-restore-drill.md:178` says the list "is checked against the tick migration's tables", which needs the bootstrap excluded explicitly (as the migration module reading would naturally do). The task should name `schema_migrations` as excluded rather than leaving the reader to discover it from a failing equality assertion.

### [NOTE] Section 1 has no commit checkpoint

CLAUDE.md requires "git add and commit from project root at least once per task". Sections 2–9 each end with a checkpoint; Section 1 (task 1.1, a preflight producing recorded findings) has none, so its findings have no designated commit. The parenthetical "or the 1.1 commit message" implies one is intended.

### [NOTE] Task 6.5 says "run `pg_verifybackup`" without the versioned path

On manta9000 there is no `/usr/bin/pg_verifybackup` wrapper: it exists only at `/usr/lib/postgresql/17/bin/pg_verifybackup`. This is recorded twice in the project's own documents — `project-documents/user/runbooks/200-backup-and-restore.md:33` and `project-documents/user/notes/2026-08-16-915-host-survey.md:39` — and `scripts/backup_prod.sh` hard-codes the versioned path for exactly this reason. The design (TD1 step 4) is similarly terse, so this is inherited rather than introduced, but 6.5 is the task that runs on the host (6.7) and a bare invocation will fail there. Naming the path, or reusing `backup_prod.sh`'s constant, would remove a predictable host-run failure.

### [NOTE] Task 9.1's mypy scope and diff ref may not apply to this slice

Two items I could not confirm. (1) 9.1 scopes mypy to "src kalshi paths and tests in one invocation", inherited from the Context Summary, while Sections 2–6 repeatedly require "ruff and mypy clean on touched files" for new `scripts/drill_228_*.py` modules. If "src kalshi paths" is the literal configured target set, mypy is not run over the new code at all in the final gate. I could not locate the mypy configuration with the tools I had, so I am flagging the apparent inconsistency rather than asserting the gate is wrong. (2) 9.1 uses `git diff main`; CLAUDE.md states the integration target is read from `cf config get git.integration_branch` and falls back to `main` only when unset, and warns against inferring it. If an integration branch is configured, `main` is the wrong ref for the unrelated-deletions check. I did not read `.context-forge.toml` to confirm whether the key is set.

### [NOTE] Task 3.2 is the largest unit and bundles four concerns

3.2 (effort 5, the scale's maximum) covers natural-key matching, comparison of every id-valued column through the restored→rebuilt mapping, the allowed-missing rules, and the returned result object, over six tables. Splitting it would fight the cohesion the design intends (TD4 is one comparison defined once), so I would not require a split — but it is the task most likely to need the design open alongside it, and its success criterion ("function takes two connections; no SQL writes") does not state what a correct partial failure looks like.

### [NOTE] No load test or CI-gating task is required for this slice

The slice design restates no non-functional requirement: it carries Functional Requirements, Technical Requirements and Integration Requirements only, and contains no latency or throughput criterion (the 226 proof notes are cited as inputs, not restated as NFRs). No task under `test/load/` is therefore required, and no CI-gating task applies. This is consistent with the repository's CI, which only builds and publishes on `v*` tag pushes (`.github/workflows/ci.yml`) and gates no test tier, so there is no existing CI surface for such a task to wire into.

### Run Digest

- Response length: 8858 chars
- Response is newline-free: no
- Tool calls made: 60
- Tool calls failed: 2
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 358297
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1198349 / 848768 / 104154 / 97625
- Duration: 855.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
