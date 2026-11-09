---
docType: tasks
slice: futures-history-acquisition
project: trading-data
lld: user/slices/307-slice.futures-history-acquisition.md
dependencies:
- 304
- 305
projectState: 304's cutover (cutover_304_first_purchase.py, cutover_304_checks.py,
  cutover_304_evidence.py) is merged with 304's values in it (PASS_ATTEMPTS, START_FILE
  and BASELINE_FILE under 304-, SESSION_CALENDAR CME_EQUITY, ES bars 2024 and statistics
  ranges hard-coded in the evidence module, CEILING_COMMAND fixed at 7 and 16); TICK_UNIVERSE
  ends at 2026-11-01 with ES ohlcv-1m for 2024 only and no GC bars; 305's family reports,
  health and accounting rows and traded_vs_cleared counts exist
dateCreated: 20261109
dateUpdated: 20261109
status: not_started
turns: 24
promptTokens: 1766470
cachedTokens: 1653536
cacheWriteTokens: 112888
completionTokens: 24974
durationSeconds: 195.5
costUsd: 0.867035
usageRounds: 3
pricedRounds: 3
usageByModel:
  claude-sonnet-5-5:
    rounds: 3
    costUsd: 0.867035
    completionTokens: 24974
revision_number: 2
---

## Context Summary

- Working on the `futures-history-acquisition` slice (307). Design: `user/slices/307-slice.futures-history-acquisition.md`. Decision references (D1-D7) point into it. Source paths are under `src/manta_trading/`; script paths are under `scripts/`.
- The slice's code is small: one universe edit (D1), and the parameterisation of 304's three cutover scripts so they run 307's purchase (D3), with two new checks (the issue #69 gate, D2, and the dataset-edge check, D1), D5's failed-request handling and D6's evidence items in one new module, `scripts/purchase_verdicts.py`. The purchase itself (about $89, usage-billed) is Phase 7: it runs after merge and release, once issue #69 is closed, through the cutover script. **No task here spends money, touches production, runs the cutover, or runs a production `mt-run`.** Phase 6 ends at a local commit.
- Preconditions for Phase 7, recorded here and not tasks: issue #69 is closed; production is at the release carrying this slice; the PM runs the one ceiling command the `--estimate` run prints (D4). The cutover itself checks #69 and the dataset edge.
- Excluded: `ohlcv-1s`, any tick tier or `tbbo` range change, any ingest, statistics or calendar rule fix, the close-minute check of issue #70, serving (306), tick bars (308), new operator commands.
- Ground rules: no silent fallbacks, no magic defaults (one named constant per value, defined once); each script stays within about 300 lines; no secrets in code; every `try/except` re-raises or is a commented, specific swallow. A `gh` failure, an unreadable edge or a changed payload is a stop, never read as "closed" or "reached". `ruff format` touched files only, and `ruff check --fix` on named files only, then check `git diff main` for swept lines before each commit. Run mypy with the kalshi src paths and tests in one invocation. Export `MT_TIMESCALE_TEST_URL` from `.env` (quotes stripped) before integration tests. Never touch the production database; tests use canned data and fakes.
- Known pre-existing failures to ignore: migration 051/052 chain tests, the test cluster's missing TimescaleDB scheduler (`test_policy_advances_head.py`), the `test_health_tick` pair needing `MT_BACKUP_ROOTS`, and the listed integration flakes (re-run in isolation before investigating).
- Patterns to follow: 304's scripts and their tests (`test/unit/scripts/test_cutover_304_checks.py`, `test_cutover_304_first_purchase.py`), `scripts/cutover_common.py` (`out`, `run`, `say`, `log_text`, `CutoverError`).
- Next slice: 308 (tick bars). 306 (serving) is independent.
- Overall relative effort: 2/5.

## Tasks

### 1. Parameterise the cutover: `--slice`, derived values, `--estimate` (D3, D4)

- [x] **1.1 Replace 304's constants with derived values**
  - [x] First, read (edit nothing yet) the three 304 cutover scripts, their two test files, `FUTURES_PRODUCT_CALENDAR` in `data/tick/constants.py`, `src/manta_trading/data/tick/family_report.py`, and the `coverage --json`, `status --json` and `accounting --json` producers (`tick_status_build.py`, `cli/commands/tick_status_render.py`, and the accounting renderer). Learn the real field names of the dataset edge, the sessions (`status`, `check`), the per-family record total, and the request rows (product, schema, start, end, request time, state, error). Find also how the planner maps a statistics session to the UTC day its records fall in (used by 4.1); name the function.
  - [x] In `cutover_304_evidence.py`, delete `BARS_START`, `BARS_END`, `STATISTICS_START`, `STATISTICS_END` and the hard-coded coverage list in `collect`. Derive the coverage reads from `TICK_UNIVERSE`: for every entry and every history want except `definition`, one read with that product, the want's schema, and the want's own start and end.
  - [x] In `cutover_304_checks.py`, remove `SESSION_CALENDAR`. `run_session_check` takes the calendar id as an argument. The caller gets it from `FUTURES_PRODUCT_CALENDAR[product]`; a product missing from the map raises `CutoverError`, never a default calendar.
  - [x] `bar_job_dirs` groups the `ohlcv-1m` job directories by product (return a mapping product to directories). `_session_placement` runs the check once per product with that product's calendar and narrates each outcome by product.
  - [x] `PRODUCTS` in the evidence module comes from `TICK_UNIVERSE`, not a literal.
  - [x] Success: no `2024`, `2026` or `CME_EQUITY` literal remains in the three scripts (grep).
  Note: `debug requests` rows carry no request state or error text. The retry-exhausted state and its error are on the units (`fetch_status` RETRY_EXHAUSTED, `failure_reason`). Dataset edge: `status --json` `.products[].edge_available_end` (ISO or null). Accounting lines: `.tick.products[].families[]` with `records`, `caught_up`, `source_edge`, `buckets`, `cross_tier`. The statistics session is the UTC date of `ts_ref`: `utc_date_of_ns` and `assign_session` in `data/tick/statistics_session.py`.

- [x] **1.2 Tests for 1.1**
  - [x] Update 304's two test files for the new signatures (`bar_job_dirs`, `run_session_check`, coverage reads). Do not weaken an assertion: change what is called, not what is asserted.
  - [x] Coverage reads derive from the universe: monkeypatch `TICK_UNIVERSE` with a one-product entry and assert exactly its non-definition wants are read, with their own ranges.
  - [x] The session check runs once per product with that product's calendar (ES with the equity calendar id, GC with the metals id); an unmapped product raises.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **1.3 Add `--slice` and rename the state and log files**
  - [x] Replace the positional-only `argv` handling in `cutover_304_first_purchase.py` with `argparse`: positional `ref`, required `--slice` (a non-empty string; a missing one is refused with usage and exit 2), and `--estimate` (flag, used by 1.7).
  - [x] `START_FILE` and `BASELINE_FILE` become functions of the slice (`{slice}-cutover-start.txt`, `{slice}-cutover-baseline.json` under `NOTES_DIR`); the narration log is `cutover-{slice}-{ref}-{stamp}.log`. The script writes nothing else under the notes directory. `record_start_and_baselines` takes the slice.
  - [x] Update the module docstring: usage with the new arguments, and that the file is shared by purchase slices.
  - [x] Success: `--slice 304` reproduces 304's file names exactly.

- [x] **1.4 Tests for 1.3**
  - [x] Update 304's `main` tests to pass `--slice 304`.
  - [x] `--slice` missing: refused, exit 2, nothing run. `--slice 307` writes `307-cutover-start.txt`, `307-cutover-baseline.json` and `cutover-307-<ref>-<stamp>.log`, and reads back its own start instant on a re-run (use a temp `NOTES_DIR`).
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **1.5 Ceiling values from the estimate (D4) and resubmits since the start (D5)**
  - [x] In `cutover_304_checks.py` add `CEILING_STEP_USD = 5`, defined once, and a pure function that returns the per-pass value (planned figure rounded up to the next multiple of the step) and the 30-day value (trailing plus planned, rounded up likewise), as `Decimal`s. A planned figure that is already a multiple of the step is not raised further.
  - [x] Replace the fixed `CEILING_PER_PASS_USD`, `CEILING_CAP_30D_USD` and `CEILING_COMMAND` with a function that builds the PM's command from those two values and `CEILING_SCRIPT`. `_ceiling` and `check_env_text` stop naming a fixed command: a missing ceiling says the PM sets the ceilings with the command the `--estimate` run prints. When `judge_estimate` refuses because planned exceeds the per-pass ceiling or planned plus trailing exceeds the 30-day ceiling, the message includes the command built from that estimate's figures.
  - [x] `judge_estimate` takes an optional `since` (the cutover's start instant, or `None`). In `_check_scope` a request with `resubmit` true is accepted only when `since` is given and its original request was made at or after it; any other resubmit, and every resubmit when `since` is `None` (the `--estimate` mode records no start), refuses naming the request. Find which field carries when the original was made in 1.1's reading; if the estimate payload has none, take it from the matching `debug requests` row by request id. If neither carries it, write `BLOCKED:` under this task.
  - [x] The full cutover passes the start instant from `record_start_and_baselines`, which already runs before the estimate in `_steps`; keep that order.
  - [x] Success: for planned 89 and trailing 0 the command contains `--per-pass 90 --cap-30d 90`; for planned 90 and trailing 0 the same; for planned 6.3 and trailing 4 it contains `--per-pass 10 --cap-30d 15`.
  Note: the estimate payload and `debug requests` rows carry no request id, so a resubmit is matched to its original by (schema, start, end) against jobless `debug requests` rows (`requested_at` is kept on resubmit); the earliest match wins on a shared range.

- [x] **1.6 Tests for 1.5**
  - [x] The three ceiling cases above, and the stop message of an over-ceiling estimate carries the command.
  - [x] A resubmit of a request made after `since` is accepted; one made before it refuses; with `since=None` every resubmit refuses, including one made just now.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes.

- [x] **1.7 The `--estimate` mode**
  - [x] With `--estimate`, `_steps` runs preflight and the free estimate only (no install, no start instant or baseline written, no pass, no evidence), then prints the planned figure, the request count, the verdict and the PM's ceiling command from 1.5, and returns 0. It never calls `mt-run tick`.
  - [x] The estimate is judged for scope with `since=None`. In this mode the preflight skips the env file's ceiling checks (`check_env_text`'s unset-ceiling raise; the usage-plan check still runs), and the estimate is not refused for short ceilings: the whole purpose is to print the command, so the ceiling-short stop becomes the printed command, not an error. The full cutover still stops on short ceilings (D4).
  - [x] Test: prints the command, buys nothing (a fake `mt-run tick` that fails the test if called), writes no start or baseline file, and does not fail on short or unset ceilings.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes; `ruff format` and `ruff check` on the touched files by name and `mypy` (kalshi src paths and tests in one invocation) are clean.

**Commit**: `refactor: parameterise the purchase cutover by slice and derive its wants`

### 2. The issue gate, the dataset-edge check and failed requests (D1, D2, D5)

- [x] **2.1 Create `scripts/purchase_verdicts.py` with the preflight gates**
  - [x] New module with a docstring stating it holds the cutover's own judgements (D5, D6) so each cutover script stays near 300 lines. It imports from `cutover_common` and `cutover_304_checks` and from nothing that imports it (no cycle).
  - [x] `check_gate_issue(number, view)`: `view` is an injected callable that runs `gh issue view <number> --json state` through `cutover_common.out` and returns its text. Parse the JSON; the state must be exactly `CLOSED` (the string held in one constant) or raise `CutoverError` saying the invoice comparison is not done and naming the issue. A `gh` failure (the call raising), non-JSON output or a missing `state` key raises `CutoverError` naming the cause. Never return success on any of those.
  - [x] `latest_want_end()`: the greatest history-want end across `TICK_UNIVERSE`, as a UTC instant at 00:00 of that date. Read from the universe, never passed in.
  - [x] `check_dataset_edge(status_result, minimum)`: `status_result` is the completed `status --json` process. Raise `CutoverError` when it exited with a code other than 0 or 3 (naming the exit code and its error text; 3 is a verdict, not a failure), when its output is not JSON, when any product's edge field is absent or null, when it does not parse as a UTC instant (quote the text), or when the instant is before `minimum` (the stop names the edge and the minimum). The edge needs only to reach `minimum`; equal passes.
  - [x] Both functions are pure apart from the injected callable; each failure message is distinct so the test can tell them apart.
  - [x] Success: the module imports; `uv run mypy` on it is clean.

  Note: `status` exit 3 is a verdict (a roll line), not a failure, so `check_dataset_edge` accepts exits 0 and 3 and stops on any other code, as the evidence step's status read does. The edge is read from every product in `status --json` (`.products[].edge_available_end`); all must reach the minimum.

- [x] **2.2 Tests for 2.1**
  - [x] New `test/unit/scripts/test_purchase_verdicts.py`. Gate: `CLOSED` passes; `OPEN` stops; a raising `view` stops (never "closed"); non-JSON output stops; JSON without `state` stops.
  - [x] Edge: an edge equal to the minimum passes, one second earlier stops; a `status` exit other than 0 or 3 stops naming the code; absent edge, null edge and an unparseable edge (`"yesterday"`) each stop with their own message; `latest_want_end()` equals the universe's latest end at midnight UTC (monkeypatch the universe to a different end and assert it follows).
  - [x] Success: `uv run pytest test/unit/scripts/test_purchase_verdicts.py -q` passes.

- [x] **2.3 Wire the gates into the preflight**
  - [x] Add required `--gate-issue` (an integer; missing is refused with usage and exit 2) to the argparse of 1.3. There is no flag that skips it.
  - [x] The preflight step (`_steps` step 1) runs, in order after `preflight(ref)` and the env checks: `check_gate_issue`, then `check_dataset_edge` with `status` read through `evidence.mt_run(["data","tick","status","--json"])` and `latest_want_end()`. Both run before the install and before any start instant is written, so a refusal leaves no state and spends nothing. In `--estimate` mode they still run.
  - [x] 304's tests call the preflight function directly with no gate issue; keep that possible by making the gate and edge checks separate calls made from `_steps`, not hidden inside `cutover_common.preflight`.
  - [x] Success: see 2.4.

- [x] **2.4 Tests for 2.3**
  - [x] First update 304's and 1.4's `main` tests, which now need `--gate-issue` and fakes for the gate and edge calls (the fakes return a closed issue and a sufficient edge), so they keep their meaning.
  - [x] Through `main` with fakes: `--gate-issue` not given is refused (exit 2); an open issue stops before the install is called; a short edge stops before the install is called; neither writes a start or baseline file.
  - [x] Success: `uv run pytest test/unit/scripts -q` passes; format, lint and typecheck of the touched files are clean (as in 1.7).

**Commit**: `feat: gate the purchase cutover on the invoice issue and the dataset edge`

### 3. Failed-request verdicts (D5)

These are pure functions; task 4.7 wires them in, and they are committed with Task 4, so no commit marker holds uncalled code.

- [x] **3.1 Classify failed requests**
  - [x] In `purchase_verdicts.py` add `ACQUISITION_FAILED = "acquisition failed"` (the D6 label, defined once). Add a function that takes the `debug requests` rows and returns the retry-exhausted ones with their provider error text; the request state and error field names come from the manifest (use the enum, not a string literal).
  - [x] Add a pure function over the same rows that returns one failure string, naming the request id, for each request still failed-retryable. Retry-exhausted requests produce no failure string.
  - [x] A unit of a retry-exhausted request is final (its days are held). Put that check beside these functions; 4.7 calls it from `_unit_is_final`.
  - [x] Success: see 3.2.
  Note: `debug requests` rows carry no state or error. `retry_exhausted_requests` takes the request rows and the unit rows; the state and the provider's error text come from the units' `fetch_status` and `failure_reason`.

- [x] **3.2 Tests for 3.1**
  - [x] A failed-retryable request yields a failure string naming it; a retry-exhausted request yields none and is returned with its error and the `acquisition failed` label; its units count as final.
  - [x] Success: `uv run pytest test/unit/scripts/test_purchase_verdicts.py -q` passes.

### 4. The D6 evidence items

- [x] **4.1 The not-caught-up session list (item 3)**
  - [x] In `purchase_verdicts.py` add `PUBLICATION_AFTER_WANT_END = "publication after want end"` (defined once beside `ACQUISITION_FAILED`). Add a function that, for every family whose caught up is false, takes the family's `coverage` document and returns one line per judged session that is not `complete` and not a provider hole: product, schema, session date, and the bucket `coverage` emits (`TickSessionStatus` value, copied verbatim, never re-spelled). For a failed unit it appends the manifest `failure_reason` verbatim (from the `debug units` rows), grouped by `classify_unit`'s existing class.
  - [x] The two labels of the script's own: a `statistics` session whose records fall in a UTC day at or after `latest_want_end()` is `publication after want end` (use the planner's statistics-day mapping found in 1.1, never a second derivation; if there is none, write `BLOCKED:` under this task); a session of the same product and schema inside the start and end of a retry-exhausted request (3.1) is `acquisition failed`. No other label is invented.
  Note: the PM chose option (b). A statistics session is published during the next session, so the last `STATISTICS_PUBLICATION_SESSIONS` judged sessions of the family's coverage are labelled `publication after want end`; any other incomplete statistics session in an unlisted bucket fails the exit rule.
  - [x] The exit rule failures of D6 item 3: bucket `failed` or `retry_exhausted` with no failure reason and not labelled `acquisition failed`; bucket `in_flight` or `awaiting_ingest`; bucket `missing`, `pending` or `edge_unknown` that is not `publication after want end`. Return these as a list of failure strings naming product, schema, date and bucket.
  - [x] Success: see 4.2.

- [x] **4.2 Tests for 4.1**
  - [x] Real-shape fixture: build the coverage and `debug units` documents from the producers' actual field names (1.1), not hand-simplified ones.
  - [x] A `complete` session and a provider hole are not listed; a family that is caught up lists nothing. A failed unit line carries its reason verbatim. A statistics `missing` session in the last UTC day at or after the want end is `publication after want end` and does not fail; the same bucket one session earlier fails. Each unaccounted-for case above fails the rule on its own: failed with no reason, `in_flight`, `awaiting_ingest`, `pending` not at the end, `edge_unknown` not at the end.
  - [x] Success: `uv run pytest test/unit/scripts/test_purchase_verdicts.py -q` passes.

- [x] **4.3 Health and accounting rows and cross-tier counts (items 2, 4)**
  - [x] Add a function that reads `mt data accounting --json` (read the producer in `src/manta_trading/` and the 305 tests for the real shape first; the walkthrough in the slice design gives `.tick.products[].families[]` with `schema`, `caught_up`, `sessions_complete`, `sessions_in_scope`) and returns, per product and family, the narration lines: sessions complete over judged, source edge, caught up. It also returns the `traded_vs_cleared` counts per product and volume family (`agree`, `under`, `over`, `no_trades`) and the list of `over` sessions by name. Use the field names the producer emits.
  - [x] Any `over` session is a defect by 305 D5 and fails the exit rule, named in the failure string.
  - [x] Success: see 4.4.
  Note: the accounting reader is in `scripts/purchase_accounting.py` (not `purchase_verdicts.py`), to keep each script near 300 lines. Accounting gives `traded_vs_cleared` counts only; the `over` sessions are named from the statistics `coverage` document's `volume_checks`.

- [x] **4.4 Tests for 4.3**
  - [x] A canned accounting document yields one line per product and family with the three values; `over` counts of zero pass and a non-zero count fails naming the session; a missing family key stops with a `CutoverError`, not a default.
  - [x] Success: `uv run pytest test/unit/scripts/test_purchase_verdicts.py -q` passes.

- [x] **4.5 Scale: record totals before and after (item 6)**
  - [x] The baseline gains the per-family record totals read from the same accounting document (the field found in 1.1), and the after-totals are narrated with the delta per product and family. Request and unit counts and the `tick_definition` delta are already narrated by 304's `collect`. The script never queries the database itself.
  - [x] Success: see 4.6.

- [x] **4.6 Tests for 4.5**
  - [x] A canned before and after accounting document give the delta per product and family; a missing record-total field stops with a `CutoverError`, not a default.
  - [x] Success: `uv run pytest test/unit/scripts/test_purchase_verdicts.py -q` passes.

- [x] **4.7 Wire the evidence items into the collection and the exit rule**
  - [x] In `cutover_304_evidence.collect`, after the coverage reads, call 4.1 and 4.3 and narrate their lines with `say`/`log_text`; append their failures to `evidence.failures`. Read the `debug requests` rows already fetched there, list the retry-exhausted requests with their errors (3.1) in the narration and pass them to 4.1's function for the `acquisition failed` label. `_unit_is_final` also accepts a unit of a retry-exhausted request (3.1). `Evidence` gains the lists it needs and nothing else.
  - [x] `final_failures` now also fails on a failed-retryable request (3.1), on 4.1's failures, and on an `over` session. Health's exit code must still equal the baseline's (D6). A second estimate must be `nothing_to_buy` at 0, as 304 has it; pass it the start instant so 1.5's rule applies.
  - [x] Check each of the four cutover scripts (the three 304 files and `purchase_verdicts.py`) with `wc -l`; if one exceeds about 300 lines, move the nearest cohesive function into `purchase_verdicts.py` or the module it belongs to and re-run the tests. Do not split mid-function.
  - [x] Success: see 4.8.
  Note: the second estimate is not routed through `judge_estimate`; the exit rule already requires `nothing_to_buy` at 0 USD, so it needs no start instant. The new failures are appended to `evidence.failures` in `collect`, which `final_failures` already returns.

- [x] **4.8 Tests for 4.7**
  - [x] With `mt_run` faked and canned documents: an unaccounted-for session fails the exit rule; a named `publication after want end` session does not; a failed-retryable request fails; a retry-exhausted request is listed and its units are final, and the exit rule holds; an `over` session fails; a clean run returns no failures. 304's existing evidence tests still pass (a family that is caught up lists nothing).
  - [x] Success: `uv run pytest test/unit/scripts -q` passes; each script is within about 300 lines; format, lint and typecheck of the touched files are clean (as in 1.7).

**Commit**: `feat: add the whole-range evidence items to the purchase cutover`

### 5. The universe edit, runbook and evidence note skeleton (D1, D7)

- [x] **5.1 Edit `TICK_UNIVERSE`**
  - [x] In `data/tick/universe.py` change `_HISTORY_END` to `date(2026, 12, 1)` and add `HistoryWant(TickSchema.OHLCV_1M, _HISTORY_START, _HISTORY_END)` to the shared tuple (rename it from `_DEFINITION_AND_STATISTICS` to say it holds all three schemas), so ES and GC both use it and the four ranges are defined once. Remove ES's separate 2024 `ohlcv-1m` want. Do not change `tier`, `start`, `end` or symbols, and edit no file under `src/` other than `universe.py`.
  - [x] Update the comment above `TICK_UNIVERSE` to cite 307 D1: the end is fixed because an open end would buy the newest days on every later pass, and the whole month is chosen because it is past by the time the purchase can run. Update any docstring that says ES bars are 2024 only.
  - [x] Success: the module imports (import-time validation accepts the wants).

- [x] **5.2 Tests for 5.1**
  - [x] Update the universe pin in `test/unit/data/tick/test_universe.py` to D1's table exactly: for ES and GC, `(schema, start, end)` of `definition`, `statistics` and `ohlcv-1m` are each 2010-06-08 to 2026-12-01, with no open end.
  - [x] grep `test/` for tests that assume ES bars are 2024 only, GC has no bars, or the end is 2026-11-01 (`2026, 11, 1`, `2024, 1, 1`), and update them to the new truth. Do not weaken an assertion.
  Note: the grep finds three files with the old end `2026, 11, 1`; none takes a history range from `TICK_UNIVERSE`. `test/unit/data/tick/test_planner.py` (a `wanted_spans` call with its own edges) and `test/unit/data/tick/test_family_edges.py` (`source_edge` with its own dates) pass their own literals into pure functions and pass unchanged. `test/load/manifest_scale_seed.py` (`WANT_END`, `UNIT_END`, `DATASET_END`) takes only each entry's tier window and symbols from `TICK_UNIVERSE` and replaces `history` with wants of its own at the shape 307 will give production; 305's NFR bounds were measured at that data size, so it is deliberately left unchanged. No test assumed the old ranges.
  - [x] Success: `uv run pytest test/unit/data/tick test/unit/scripts -q` passes.

- [x] **5.3 Runbook 100's "Purchase slices" section**
  - [x] In `project-documents/user/runbooks/100-production-operations.md`, section "Purchase slices", replace the cutover command with the new arguments: `--slice`, `--gate-issue`, `--estimate`. State that the gate refuses unless the issue is closed, that the dataset edge must reach the universe's latest want end, that the ceiling values come from the `--estimate` run, rounded up to the next 5, and that the PM runs only that one root command. Name the state files and log (`{slice}-cutover-start.txt`, `{slice}-cutover-baseline.json`, `cutover-{slice}-<tag>-<stamp>.log`). Keep the section's other steps.
  - [x] Replace the text that says 304's script name is the only one, and the line "Slice 307 reuses it with its own figures", so each is true after this slice. Keep the file's existing style.
  - [x] Success: the section's example command is the one in the slice design's Verification Walkthrough step 3 and its arguments are accurate against 1.3 and 2.3 (`--help` of the script).

- [x] **5.4 Create the evidence note skeleton `user/notes/307-history-acquisition-evidence.md`**
  - [x] Front matter per `file-naming-conventions.md`, status `not_started`. Headings only, each with a one-line statement of what Phase 7 fills in, mirroring D6's six items: Reconciliation; Per product and family (health row, accounting line); Not-caught-up sessions (product, schema, session date, `coverage` bucket, reason, verbatim); Cross-tier (`traded_vs_cleared` counts, `over` sessions); Session placement (per product); Scale (request and unit counts, durations, `tick_ohlcv_1m` and `tick_definition` rows before and after); Second-run result; Open items (the December invoice follow-up issue of D7, issue #70).
  - [x] Do not invent figures; every section is empty until the purchase runs.
  - [x] Success: the file exists with the headings above and no numbers.

- [x] **5.5 Final verification**
  - [x] Each task already ran format, lint, typecheck and its tests. Here run only what no task covered: `uv run mypy` with the kalshi src paths and tests in one invocation, and the integration tick tier (re-run any known flake in isolation first).
  - [x] `git diff main` shows no swept lines; `wc -l` of each of the four cutover scripts is within about 300.
  - [x] Success: all pass; no secret appears in the diff.

**Commit**: `feat: add ES and GC futures history wants through 2026-12-01 and the purchase runbook update`
