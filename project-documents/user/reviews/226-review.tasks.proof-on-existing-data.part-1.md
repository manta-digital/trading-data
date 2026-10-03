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
reviewedSha: 4d41395774709faed95511005c1110b0fda69751
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 52
turns: 20
promptTokens: 1104688
cachedTokens: 932480
completionTokens: 87175
reasoningTokens: 80926
durationSeconds: 535.8
runId: run-20261003-p5-29c1b523
squadronVersion: 0.18.2
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Every success criterion and Technical Decision traces to at least one task"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
  - id: F002
    severity: concern
    category: uncategorized
    summary: "Part 1 gives no signpost to part 2, and forward-references sections it does not contain"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
  - id: F003
    severity: concern
    category: uncategorized
    summary: "The contention loop and Section 8 mutate the same proof database concurrently"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md:419-426"
  - id: F004
    severity: concern
    category: uncategorized
    summary: "No CI-wiring task or explicit disclaimer for the new load test; CI gating is left implicit"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
  - id: F005
    severity: concern
    category: uncategorized
    summary: "Task 1.2's \"existing `DbnFile` tests pass unedited\" contradicts a verified existing assertion"
    location: "test/unit/data/tick/test_dbn_file.py:120"
  - id: F006
    severity: concern
    category: uncategorized
    summary: "The two proof URLs written into `.env` will trip the tick preflight's unknown-key refusal"
    location: "src/manta_trading/data/tick/store_context.py:103-131"
  - id: F007
    severity: note
    category: uncategorized
    summary: "Section 2 has no commit checkpoint; its work lands inside 3.6's provisioning commit"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-1.md"
  - id: F008
    severity: note
    category: uncategorized
    summary: "`archive-check` and the `manifest.json` correction are additions beyond the design, recorded only in the task file"
    location: "project-documents/user/tasks/226-tasks.proof-on-existing-data-2.md"
---

# Review: tasks — slice 226

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [PASS] Every success criterion and Technical Decision traces to at least one task

Criteria 1→3.1–3.7; 2→10.2/10.3; 3→8.2/10.1/10.3; 4→13.1; 5→5.1/13.1; 6→8.5/8.6; 7→1.1–1.4; 8→12.1–12.3; 9→11.1/11.2/13.2. TD 6's every constant appears in 9.1; TD 3's "capacity estimate" and "intra-unit checkpoints" rows and TD 10's realtime/API checks land in 13.1; TD 1's memory confirmation lands in 13.3; the `/data` free-space floor from TD 2's failure table appears in 2.2, 4.1, 5.5 and 10.1. The design's split instruction ("split it here: (a) … (b) …") is honoured exactly by the two files' boundaries. The NFR requirement is met: the slice restates the ≤120 s unit and ≤1 s query bounds, and 9.3 plus 10.4 provide load tests in `test/load/` for them.

### [CONCERN] Part 1 gives no signpost to part 2, and forward-references sections it does not contain

The file's title is `# Tasks: Proof on Existing Data` with no "(part 1 of 2)", and its Context Summary has no "This is part 1 of 2 / Sections 8–14 are in `…-2.md`" line — unlike every other split task file in the repo (`225-…-1.md`, `187-…-1.md`, `169-…-1.md`, `264`, `265`, `267`, `920`, `921` all carry it). It also depends on sections held only in file 2: 2.1 defers the layout to "Section 8", 2.2 requires the dispatcher to know `archive-check` "(added in 13.2)", 3.6 points at "the before/after comparison in 14.2", 6.5 labels its output as "the input to the lock-timeout rule (9.1)", and 7.5 says "Proceed to Section 8 while it runs". A junior AI executing file 1 alone has no instruction that file 2 exists. File 2 carries the reverse pointer; file 1 should carry it too.

### [CONCERN] The contention loop and Section 8 mutate the same proof database concurrently

Task 7.5 starts `contention` in the background and says "Proceed to Section 8 while it runs". The contention loop is defined by 7.4 as "reset, ingest the whole set, repeat" **in `trading_tick_proof`**, spanning two overlapped Kalshi firings plus one solo firing (roughly three hours). Section 8's first mutating task, `226-tasks.proof-on-existing-data-2.md` 8.4, then runs against that same database: it decompresses every compressed chunk and applies `tick_007` to `trading_tick_proof`, and 8.5/8.6 ingest into it. Two writers on one database, with the loop's repeated `TRUNCATE tick_trade, tick_ingest_ledger` reset running underneath a migration that "fails while compressed chunks exist" (design, TD 4, "Applying it to a populated table"). Nothing in either file states a barrier — e.g. "do not begin 8.4 until `contention` has exited" or "run 8.1–8.3 before launching 7.5". Section 9 already waits on the verdict; Section 8 needs the same explicit wait, or its tasks need to be placed before 7.5.

### [CONCERN] No CI-wiring task or explicit disclaimer for the new load test; CI gating is left implicit

Task 10.4 creates `test/load/test_226_tick_query_nfr.py` and names the manual gate (`MT_RUN_LOAD_TESTS=1 uv run pytest …`) in its docstring, but neither task file mentions CI, a workflow, or the load tier's gate anywhere — and `.github/workflows/ci.yml` contains only a `publish` job triggered on `refs/tags/v*`, so no CI job exists to run it. The repo's established form for exactly this situation is a one-line disclaimer in the new test's gating docstring (slice 187, Task 12d: "states CI wiring is slice 907's deliverable, not this slice's (D9)"), and the existing `test/load/test_225_tick_ingest_nfr.py` that 9.3 re-runs already carries "Gate (CI runs no test job; see slice 907 …)" in its module docstring. 10.4 omits the equivalent sentence, so a reader gets a bound that looks gated and is not. One line matching 187's precedent closes it; a pipeline is not required.

### [CONCERN] Task 1.2's "existing `DbnFile` tests pass unedited" contradicts a verified existing assertion

Task 1.1 converts the wrong-`stype_out`/unsupported-schema/mixed-schema/`ts_out`/bad-mapping-date raises to `TickFileDecodeError` "keeping the message text", and 1.2's success criterion claims "existing `DbnFile` tests pass unedited". But `test_dbn_file.py:120` asserts `pytest.raises(ValueError, match="unsupported DBN schema 'ohlcv-1m'")` against `_tick_schema`'s header raise, which 1.1 changes. Whether that assertion still holds depends entirely on whether `TickFileDecodeError` derives from `ValueError`, which I could not verify (its definition in `provider.py` was outside what I read); the message text *is* preserved, so only the class question decides it. Separately, `test/unit/data/tick/test_pass_error_mapping.py` exists to pin how a raw `ValueError`/`TypeError` escapes `run_phase`, and `test_adapter_metadata.py:332` asserts `isinstance(caught.value.__cause__, ValueError)` — both touch the same surface. Either confirm the class hierarchy and say so in 1.1/1.2, or add the test updates to 1.2 rather than asserting tests pass unedited.

### [CONCERN] The two proof URLs written into `.env` will trip the tick preflight's unknown-key refusal

Task 3.3 writes `MT_TICK_DB_URL`, `MT_TICK_MAINTENANCE_URL` "and the two proof URLs" into the checkout's `.env`, and 3.7's success criterion is that "`.env` holds the four tick URLs". `check_env_keys` (called from `open_tick_store`, `store_context.py:233`) refuses every `MT_TICK_*` name in `.env` that is not in `known_tick_env_names()`, with the design noting the refusal exists precisely so "a misspelt ceiling never reads as 'no ceiling'" (TD 2). The harness then drives the shipped CLI (`rebuild` "Drives the shipped CLI", 4.1) from that same checkout, so unless the two proof variable names are added to `known_tick_env_names()` and to `Settings`, Section 4 onward fails at the preflight. I verified the refusal and the call site; I did not read `known_tick_env_names()`'s body, so I cannot rule out that it already enumerates them — this needs confirmation, and no task in either file adds them or a unit test for the new variable names.

### [NOTE] Section 2 has no commit checkpoint; its work lands inside 3.6's provisioning commit

Sections 1, 3, 4, 5, 6 and 7 each end with a commit, but Section 2 (2.1 constants, 2.2 harness skeleton/guard/space check, 2.3 guard tests) has none, so three tasks' worth of `constants.py`, `scripts/proof_226_tick.py` and guard tests are swept into 3.6's `feat: add tick cluster provisioning script` — an unrelated message for that content. The project convention is "Git add and commit from project root at least once per task". A `feat: add proof harness skeleton, constants and the database guard` checkpoint after 2.3 fixes it.

### [NOTE] `archive-check` and the `manifest.json` correction are additions beyond the design, recorded only in the task file

Task 13.2 adds harness step `archive-check`, a twelfth step outside TD 2's eleven-row table, and states that the design's walkthrough command (`sha256sum -c` against each job's `manifest.json`) cannot work because the manifest is JSON with `filename`/`size`/`hash` keys — a correction I confirmed against `adopt_files.py` (`_ManifestEntry`, `HASH_PREFIX = "sha256:"`). Both are well justified and 2.2 already declares the step, but neither is a design change, and the design's walkthrough step 8 still reads `sha256sum -c`. Task 14.1's document list (contract, slice plan Notes, architecture Revision Log, CHANGELOG, README, migrations README) does not include the design's own walkthrough or step table, so the design will remain wrong where the tasks are right. Add the design edit to 13.2 or 14.1, or note explicitly that the design is left stale.

### Run Digest

- Response length: 8868 chars
- Response is newline-free: no
- Tool calls made: 52
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 287981
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / completion / reasoning: 1104688 / 932480 / 87175 / 80926
- Duration: 535.8 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
