---
docType: tasks
slice: first-real-purchase
project: trading-data
lld: user/slices/304-slice.first-real-purchase.md
dependencies:
- 300
- 301
- 302
- 303
projectState: 300-303 are merged; production is at release 0.26.0 with the tick track
  at tick_010_statistics; TICK_UNIVERSE entries for ES and GC have a tbbo tier and
  no history wants; no ceiling-setting script, reconciliation script or 304 cutover
  script exists
dateCreated: 20261107
dateUpdated: 20261107
status: not_started
turns: 28
promptTokens: 2199930
cachedTokens: 2092877
cacheWriteTokens: 106997
completionTokens: 21306
durationSeconds: 179.8
costUsd: 0.850743
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.8507426
    completionTokens: 21306
revision_number: 2
---

## Context Summary

- Working on the `first-real-purchase` slice (304). Design: `user/slices/304-slice.first-real-purchase.md`. Decision references (D1-D8) point into it. Source paths are under `src/manta_trading/`.
- The slice's code is small: one universe edit (D1), one root script that sets the two spend ceilings (D4), one read-only reconciliation script (D5), one cutover script (D3, D6, D7), and a runbook update. The purchase itself (about $6.3, usage-billed) is Phase 7: it runs after merge and release, through the cutover script. **No task here spends money, touches production, or runs the cutover.** Phase 6 ends at a local commit.
- Preconditions for Phase 7, recorded here and not tasks: the ceiling script is run by root with `--per-pass 7 --cap-30d 16`, and production is at the release carrying this slice.
- Excluded: GC `ohlcv-1m` and ES bars outside 2024 (307), any tick tier change, health/accounting/overview verdicts (305), serving (306), ingest-rule changes, the invoice comparison (issue #69), the close-minute bar check (issue #70). Nothing new is added to `deploy/sudoers.d/manta-ops`.
- Ground rules: no silent fallbacks, no magic defaults (one named constant per value); source files stay within about 300 lines; no secrets in code. `ruff format` touched files only, then check `git diff main` for swept lines before each commit. Run mypy with the kalshi src paths and tests in one invocation. Export `MT_TIMESCALE_TEST_URL` from `.env` (quotes stripped) before integration tests. Never touch the production database; tests use temp files, canned data and throwaway databases.
- Known pre-existing failures to ignore: migration 051/052 chain tests, the test cluster's missing TimescaleDB scheduler (`test_policy_advances_head.py`), `test_health_tick` pair needing `MT_BACKUP_ROOTS`, and the listed integration flakes (re-run in isolation before investigating).
- Patterns to follow: `scripts/cutover_303_tick_migrations.py` and `scripts/cutover_common.py` (numbered check-then-act steps, narration to `project-documents/user/notes/`), `deploy/add-tick-env.sh` with `test/unit/deploy/test_add_tick_env.py` (script test through hooks), `deploy/lib/env_value.sh`.
- Next slice: 305 (health, accounting and overview for the new tiers).
- Overall relative effort: 2/5.

## Tasks

### 1. Universe edit (D1)

- [x] **1.1 Add the history wants to `TICK_UNIVERSE`**
  - [x] In `data/tick/universe.py`, give the ES entry `history=(definition 2010-06-06..2026-11-01, statistics 2010-06-06..2026-11-01, ohlcv-1m 2024-01-01..2025-01-01)` and the GC entry the same `definition` and `statistics` wants and no `ohlcv-1m`. Use `HistoryWant` with `TickSchema` members. Every end is the fixed date, never `None`.
  - [x] Add a comment above `TICK_UNIVERSE` that states why the ends are fixed (an open end would buy the newest days on every later pass), citing slice 304 D1. Update the module docstring's "Production configures none" sentence.
  - [x] Do not change `tier`, `start`, `end`, symbols or any other file in `src/`.
  - [x] Success: the module imports (import-time validation accepts the wants).

- [x] **1.2 Tests for 1.1**
  - [x] In `test/unit/data/tick/test_universe.py`, replace `test_production_universe_has_no_history` with a test pinning D1's table exactly: per product, the `(schema, start, end)` of each want, with no `ohlcv-1m` for GC and no open end anywhere.
  - [x] Read `test/unit/data/tick` for any other test that assumes production has no history (grep `history == ()` and `TICK_UNIVERSE`), and update it to the new truth rather than weakening it.
  - [x] Success: `uv run pytest test/unit/data/tick -q` passes.

**Commit**: `feat: add ES and GC history wants for the first real purchase`

### 2. The ceiling script (D4)

- [x] **2.1 Write `deploy/set-tick-spend-ceilings.sh`**
  - [x] Usage `--per-pass USD --cap-30d USD`, run as root. Model it on `deploy/add-tick-env.sh`: `set -euo pipefail`, `die`, source `deploy/lib/env_value.sh` to read old values. The ceiling key names are `MT_TICK_SPEND_CEILING_USD` and `MT_TICK_SPEND_30D_CEILING_USD`; define each once at the top.
  - [x] Check-then-act: refuse, writing nothing, unless both values are non-negative decimals and the target file exists. Both keys must already be present in the file (refuse and name the missing key otherwise; this script replaces, it does not add).
  - [x] Replace the two values in place, so the file's mode and owner are kept (rewrite the file's contents, never `mv` a new file over it). Print the old and new value of each key. Ceilings are not secrets; print no other line of the file.
  - [x] One test hook: the target path is `/etc/manta-trading.env` unless an env var (named in the script header comment) overrides it. No other hooks, no log file, no mode or owner re-assertion.
  - [x] Header comment states what it does and that it is deliberately absent from `deploy/sudoers.d/manta-ops`.
  - [x] Success: `~/.local/bin/shellcheck deploy/set-tick-spend-ceilings.sh` is clean; the file is executable.

- [x] **2.2 Test for 2.1**
  - [x] New `test/unit/deploy/test_set_tick_spend_ceilings.py`, modelled on `test_add_tick_env.py`. One test of the replace path: both values replaced, another key and its position untouched, file mode unchanged, old and new values printed. One test of the refusal path: a bad value (non-numeric or negative) exits non-zero and the file is byte-identical afterwards.
  - [x] Success: `uv run pytest test/unit/deploy -q` passes.

**Commit**: `feat: add script to set the tick spend ceilings`

### 3. The reconciliation script (D5)

Pure logic first, then the I/O around it, so each part tests without a database or a provider.

- [x] **3.1 Write the reconciliation core in `scripts/reconcile_tick_spend.py`**
  - [x] A pure function takes manifest rows (id, job id, schema, product or symbols, start, end, days, estimate, actual) and the provider's jobs (`BatchJob`: job id, state, `cost_usd`), plus `cap_usd`. It returns one line per request (job id, schema, product, days, estimate, actual, delta = actual - estimate), the totals, and a list of named failures.
  - [x] Failures, each naming the offending job or request: a provider job with no manifest row; a delivered request (job state `done`) with no `actual_cost_usd`; total actual above `cap_usd`. A non-zero delta is reported per request and totalled and is never a failure.
  - [x] Use `Decimal` throughout; no float arithmetic on money.
  - [x] Success: module imports; the function has no database or provider import in its body.

- [x] **3.2 Tests for 3.1** (`test/unit/scripts/test_reconcile_tick_spend.py`; import the script the way scripts import `cutover_common`, via a `sys.path` insert of `scripts/`)
  - [x] Canned rows and jobs for: all equal (no failures, zero delta); an unheld provider job (failure naming its id); a delivered request with no actual (failure); a non-zero delta (reported, no failure, totals correct); total over `cap_usd` (failure); exactly at `cap_usd` (no failure).
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **3.3 Add the command-line and I/O layer**
  - [x] Arguments: `--since <UTC instant>` and `--cap-usd <amount>`, both required, no defaults. `--cap-usd` is never read from an environment variable (the dev `.env` holds dev ceilings); argparse refuses a missing one.
  - [x] Manifest read: open the tick database session read-only (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`) and read every `tick_request` with `requested_at >= since`. Reuse `manifest_request_reads.py` where it fits; do not add a write path.
  - [x] Provider read: `batch_jobs_since(since)` on the existing Databento adapter, the free `batch.list_jobs` path. Build the adapter the way the `mt data tick` commands do (read how they construct it; no new client code).
  - [x] A provider read that raises (error or timeout) stops the script with the provider's error and a non-zero exit. It is never treated as an empty job list.
  - [x] Print one line per request, then the totals and each failure. Exit 0 only with no failures, else a non-zero code. The module docstring says the script only reads and calls only free provider methods, and that 307 must not extend it to write.
  - [x] Keep the file within about 300 lines; move the printing into a helper if it grows.
  - [x] Success: `uv run python scripts/reconcile_tick_spend.py --help` lists both required arguments.

- [x] **3.4 Tests for 3.3**
  - [x] With the manifest read and the adapter faked: a job-list read that raises exits non-zero and prints the provider's error (not "0 jobs"); a missing `--cap-usd` is refused; a missing `--since` is refused.
  - [x] A test that the session is set read-only: assert the statement is the first one issued on the connection (fake connection recording statements), and that no statement issued starts with INSERT, UPDATE, DELETE, TRUNCATE, DROP or ALTER.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

**Commit**: `feat: add the tick spend reconciliation script`

### 4. Cutover judgements and evidence helpers (D3, D6, D7)

Mostly pure functions, in one module; the steps that call them are task 5.

- [x] **4.1 Write the judgements and evidence helpers in `scripts/cutover_304_checks.py`**
  - [x] The wanted `(schema, product)` set is built from `TICK_UNIVERSE`'s history wants (not retyped).
  - [x] Preflight check of the env-file text (use `cutover_common.env_value`): `MT_TICK_DATABENTO_PLAN` absent or empty, and both ceilings present. If either ceiling is missing, raise `CutoverError` whose message names the exact `sudo .../deploy/set-tick-spend-ceilings.sh --per-pass 7 --cap-30d 16` command. Return the per-pass ceiling as `Decimal` for the later steps.
  - [x] Estimate judgement over the parsed `pass --estimate-only --json` summary: raise `CutoverError` naming the figure when `planned_usd` exceeds the per-pass ceiling, or `planned_usd + trailing_30d_usd` exceeds `cap_30d_usd`, or `verdict` is not `allowed`. Raise it, naming the request, when any planned request is outside the wanted set (for example a `tbbo` request) or is a `resubmit`. A summary with `verdict` `nothing_to_buy` is a distinct, non-error outcome the caller can tell apart.
  - [x] Classify every unit that is not ingested with its failure reason and D6 class (`awaiting definitions`, `stat_reference`, `session_boundary`, undefined definition window, other). Unclassified is printed as `unclassified`, never hidden.
  - [x] Session-check wrapper: run `scripts/verify_cme_sessions.py --calendar CME_EQUITY` over the 2024 `ohlcv-1m` job directories in `/data/tick-archive`. Derive the directories from the `ohlcv-1m` requests in `debug requests` and the archive layout (read `test/unit/data/tick/test_bar_archive_path.py` and the path function it tests); do not hard-code paths. Exit 1 is a finding to narrate; exit 2 stops the script.
  - [x] Success: module imports; the subprocess call is confined to the session-check wrapper; the file stays within about 300 lines.

- [x] **4.2 Tests for 4.1** (`test/unit/scripts/test_cutover_304_checks.py`)
  - [x] Preflight: plan key set (refuse), a ceiling missing (refuse, message holds the ceiling command), all fine (returns the per-pass `Decimal`).
  - [x] Estimate: over the per-pass ceiling, over the 30-day room, a `tbbo` request, a `resubmit` request (each refuses and names its cause); a clean summary passes; `nothing_to_buy` is reported as such. Use a summary dict shaped like the real output keys (`requests`, `planned_usd`, `trailing_30d_usd`, `per_pass_ceiling_usd`, `cap_30d_usd`, `verdict`), copied from a real `--estimate-only --json` run's shape in `purchase_plan.py`.
  - [x] The classifier on canned unit rows for each D6 class and an unclassified one. The session-check exit codes 0, 1 and 2 map to pass, finding and stop.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

**Commit**: `feat: add the 304 cutover judgements and evidence helpers`

### 5. The cutover steps (D3)

The script is two files: `cutover_304_checks.py` (task 4) and `scripts/cutover_304_first_purchase.py` (the numbered steps, below). The Phase 7 run itself is not a task.

- [x] **5.1 Write the install and purchase steps in `scripts/cutover_304_first_purchase.py`**
  - [x] `main(argv)` takes the release tag, as `cutover_303_tick_migrations.py` does. Narrate through `say` into `NOTES_DIR`. Steps: 1 preflight (`cutover_common.preflight`, then the env-file check of 4.1 on `/etc/manta-trading.env`); 2 hold timers and install the ref, releasing the timers as found in a `finally` on every exit from this step, including a failed install (reuse `hold_timers` / `release_timers` and `install`); no migration, and the timers are not held during the purchase; 3 estimate with `sudo mt-run data tick pass --estimate-only --json`, judged by 4.1 (stop before buying on any refusal; on `nothing_to_buy` skip step 4 and go to evidence, so a re-run after a complete purchase is clean); 4 buy and ingest with `sudo mt-run tick`, looped by the pass-exit mapping, logging each run's output and the last journal lines on a stop.
  - [x] `PASS_ATTEMPTS = 5` is named once at the top of this script. Pass-exit mapping for `sudo mt-run tick`: exit 0 and 3 continue; exit 6 means run again; any other exit stops with that exit code. After `PASS_ATTEMPTS` runs that all ended 6, raise `CutoverError` with the text "jobs still processing after 5 runs; re-run the cutover" (build the number from the constant).
  - [x] Every step is check-then-act and re-runnable. No step waits on a person.
  - [x] Start instant: on the first run write the current UTC instant to `project-documents/user/notes/304-cutover-start.txt`; on a re-run read that file and keep it, so the reconciliation always covers the whole purchase. The evidence step passes it to the reconciliation script.
  - [x] Baselines, taken once before the install step and logged with `log_text`: the exit code of `sudo mt-run data health`, and `sudo mt-run data tick debug definitions --json` (D7's "before" row counts per product). On a re-run after a completed install the baselines are not retaken: they are read back from `304-cutover-baseline.json` (beside `304-cutover-start.txt`), written with them.
  - [x] Success: `uv run python scripts/cutover_304_first_purchase.py` with no argument prints the usage and exits 2; the file is within about 300 lines.

- [x] **5.2 Tests for 5.1**
  - [x] With `run` and the step helpers faked: a failed install still releases the timers; a re-run keeps the first start instant and baselines; an estimate refusal never calls `mt-run tick`; `nothing_to_buy` skips the purchase and continues to evidence; exit 6 twice then 0 runs three times; 0 and 3 continue; the sixth consecutive exit 6 raises; exit 1, 2, 4 or 5 stops with that code; exit 4 stops the script non-zero.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **5.3 Write the evidence collection**
  - [x] Run `scripts/reconcile_tick_spend.py --since <start> --cap-usd <per-pass ceiling from preflight>`; run `mt-run data tick status --json`, `coverage --schema ohlcv-1m` for ES over 2024-01-01 to 2025-01-01, `coverage --schema statistics` for ES and GC over 2010-06-07 to 2026-11-01 (all `--json`), and `debug requests`, `debug units` and `debug definitions` (`--json`). Write the raw JSON into the log with `log_text`.
  - [x] D7 figures: `tick_definition` rows per product after versus the baseline from 5.1; request and unit counts from `debug requests` and `debug units`; pass and ingest durations from the run's `pass_runs` row via `mt-run data overview` (read its options; use `--json` only if it exists). Narrate them as a table for 305.
  - [x] Classify unfinished units and run the session check through `cutover_304_checks.py` (task 4.1).
  - [x] Ledger equals raw: from the `coverage` JSON (read the shape in `data/tick/coverage_rows.py` and the coverage command's output), every session whose units ingested must show ledger equal to raw; any mismatch fails the script naming the session. Sessions that did not ingest are listed with their D6 class, not failed.
  - [x] Success: each command's output is in the narration; a non-zero exit from any of them stops the script with its name.

- [x] **5.4 Tests for 5.3**
  - [x] With `run` faked: a ledger-unequal ingested session fails; a non-ingested session is listed, not failed; a failing evidence command stops the script naming it; the D7 definition delta is computed against the baseline.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **5.5 Write the integration checks and the exit rule**
  - [x] After evidence, run `sudo mt-run data health` again and fail if its exit code differs from the baseline of 5.1.
  - [x] Run `pass --estimate-only --json` again: the verdict must be `nothing_to_buy` and `planned_usd` 0, else fail naming the planned requests. The estimate is free and buys nothing.
  - [x] Exit rule: 0 only when the reconciliation passed, every unit is in a final state (ingested, or failed with a named reason), ledger equals raw in every ingested session, health is unchanged and the second estimate plans nothing. Coverage findings, exit 3 from the pass, and session-placement findings do not fail the script.

- [x] **5.6 Tests for 5.5**
  - [x] With `run` faked: a health exit code that changes fails; a second estimate that plans a request fails; a failed reconciliation fails; a unit with no failure reason and not ingested fails; failed units with reasons plus coverage findings pass.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

**Commit**: `feat: add the 304 first-purchase cutover script`

### 6. Validation pass

- [ ] **6.1 Format, lint, types and tests over everything touched**
  - [ ] `ruff format` on the touched files only, then `git diff main` and check for swept or deleted pre-existing lines; restore any. `ruff check` on touched files.
  - [ ] mypy over the touched files in one invocation with the kalshi src paths and tests.
  - [ ] Unit tier: the whole unit tier with `MT_TIMESCALE_TEST_URL` exported from `.env`. Integration tier separately; any failure outside the pre-existing list is re-run in isolation before it is investigated.
  - [ ] Confirm no file the slice touches exceeds about 300 lines and that the only `src/` change is `universe.py`.
  - [ ] Success: all pass apart from the named pre-existing failures.

**Commit** (only if 6.1 changed files; otherwise make no commit): `style: format and lint touched files for 304`

### 7. Runbook and the evidence note skeleton (D3, D4)

- [ ] **7.1 Update `user/runbooks/100-production-operations.md`**
  - [ ] Add a "Purchase slices" subsection beside "Adding a futures product (slice 232)": the procedure (universe edit released, then the cutover script, then reconciliation and verdicts), where the evidence goes, and that the reconciliation script reads production from the dev checkout read-only and `--cap-usd` is always passed explicitly.
  - [ ] Document `deploy/set-tick-spend-ceilings.sh`: arguments, that it replaces existing keys only, that it is root-run and absent from the sudoers file, and that 307 reuses it with its own figures.
  - [ ] Amend step 8's last sentence ("Afterwards the PM restores the per-pass ceiling") for purchase slices: no restore afterwards, because every want is covered with a fixed end so a pass plans nothing until a reviewed universe change asks for more. Add a revision-log line.
  - [ ] Success: the runbook names each new script by path and the amended rule matches D4's "No restore afterwards".

- [ ] **7.2 Create the evidence note skeleton `user/notes/304-first-purchase-evidence.md`**
  - [ ] Front matter per `file-naming-conventions.md`, status `not_started`. Headings only, each with a one-line statement of what Phase 7 fills in: Reconciliation (per-request estimate, manifest and Databento charge, with total and delta); Verdicts (`status`, each `coverage` result); Findings by D6 class (product, schema, UTC day or session, failure reason, causing record); D7 results (session placement, `tick_definition` growth before and after per product, request and unit counts, pass and ingest durations for 305); Second-run result (nothing to buy, $0); Open items (issue #69 invoice comparison, issue #70).
  - [ ] Do not invent figures; every section is empty until the purchase runs.
  - [ ] Success: the file exists with the headings above and no numbers.

**Commit**: `docs: add the purchase runbook section and the 304 evidence note skeleton`
