---
docType: review
layer: project
reviewType: code
slice: futures-history-acquisition
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: FAIL
verdictSource: stated
sourceDocument: project-documents/user/slices/307-slice.futures-history-acquisition.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261111
dateUpdated: 20261111
reviewedSha: b5c3d781963789cef917771538a0b51a10bab29b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 41
diffTruncated: false
turns: 20
promptTokens: 2104529
cachedTokens: 1763712
completionTokens: 72988
reasoningTokens: 67270
durationSeconds: 328.4
costUsd: 0.093569
costBasis: alias_pricing
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: fail
    category: correctness
    summary: "The estimate summary is read at the top level, but the command nests it under `phases[].summary`"
    location: "scripts/cutover_304_first_purchase.py#fetch_estimate"
  - id: F002
    severity: fail
    category: correctness
    summary: "`judge_estimate` requires verdict `allowed`, but an `--estimate-only` run reports `estimate_only`"
    location: "scripts/cutover_304_checks.py:242"
  - id: F003
    severity: concern
    category: correctness
    summary: "`over_sessions` drops the family, so the failure message attributes one family's over sessions to another"
    location: "scripts/purchase_accounting.py#over_sessions"
  - id: F004
    severity: note
    category: error-handling-observability
    summary: "A naive `requested_at` raises `TypeError` where the sibling check raises `CutoverError`"
    location: "scripts/cutover_304_checks.py#resubmit_origins"
  - id: F005
    severity: note
    category: architectural-decay
    summary: "`EXIT_USAGE` is now dead"
    location: "scripts/cutover_304_first_purchase.py:79"
  - id: F006
    severity: note
    category: documentation-drift
    summary: "The module docstring still says the preflight names the ceiling command"
    location: "scripts/cutover_304_first_purchase.py"
  - id: F007
    severity: pass
    category: test-debt
    summary: "Universe-driven reads, per-product calendars and the gated preflight are correct and tested"
    location: "scripts/cutover_304_evidence.py#coverage_reads"
---

# Review: code — slice 307

**Verdict:** FAIL
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [FAIL] The estimate summary is read at the top level, but the command nests it under `phases[].summary`

`fetch_estimate()` runs `sudo mt-run data tick pass --estimate-only --json` (`ESTIMATE_ARGS`) and returns `json.loads(result.stdout)` unchanged, and `judge()` hands that straight to `judge_estimate`. That verb renders through `print_pass` (`src/manta_trading/cli/commands/tick_pass_render.py`), which in `--json` mode prints `pass_to_dict(result, exit_code)` = `{**result.to_dict(), "exit_code": …}`; `PassResult.to_dict` (`src/manta_trading/data/tick/pass_contract.py`) emits `run_id`, `started_at`, `phases` (each `PhaseReport` as `{name, outcome, duration_ms, summary}`), `outcome`, `duration_ms`. The purchase figure therefore lives at `phases[i]["summary"]` — the `plan_summary` dict from `src/manta_trading/data/tick/purchase_plan.py:277-304`.

Every consumer reads it flat: `_check_scope` does `for key in summary["wanted_days"]` (`scripts/cutover_304_checks.py:196`), and `_estimate_only` does `Decimal(summary["planned_usd"])` (`scripts/cutover_304_first_purchase.py`). On a real payload the first access raises `KeyError`, which `main`'s `except (KeyError, ValueError)` reports as `FAILED: unexpected payload` and exit 1 — so neither `--estimate` (which never prints the PM's ceiling command) nor step 3 of the purchase can run. I found no unwrapping anywhere in `cutover_304_first_purchase.py`; the earlier cutover for the same command, `scripts/cutover_234_tick_wiring.py:178` → `purchase_wants` (`scripts/cutover_234_checks.py`), does unwrap it: `next((p for p in estimate["phases"] if p["name"] == "purchase"), None)`.

The tests cannot catch this because every fixture is a hand-built flat dict (`_summary()` in `test/unit/scripts/test_cutover_304_checks.py`, `NOTHING`/`summary` in `test_cutover_304_first_purchase.py`). That is exactly the fixture-must-be-the-production-format rule in the project guidelines: no test in this diff passes a real rendering through the code that parses it. Unwrap the purchase phase (and fail with a named `CutoverError` when the phase is absent or `outcome != "ok"`), and shape at least one fixture from the real payload.

### [FAIL] `judge_estimate` requires verdict `allowed`, but an `--estimate-only` run reports `estimate_only`

`VERDICT_ALLOWED = "allowed"`, and `judge_estimate` refuses unless `summary["verdict"] == VERDICT_ALLOWED`. The purchase step judges `fetch_estimate()`, and `ESTIMATE_ARGS` contains `--estimate-only`; under that flag `evaluate_spend` sets `status = VerdictStatus.ESTIMATE_ONLY` (`src/manta_trading/data/tick/spend_guard.py`), and `plan_summary` writes `"verdict": spend.status.value if spend else "nothing_to_buy"`. The repository's own test pins this: `test/unit/data/tick/test_tick_purchase_phase.py::test_estimate_only_reports_the_plan_and_submits_nothing` asserts `report.summary["verdict"] == "estimate_only"`.

So with any planned request the verdict is `estimate_only`, never `allowed`, and step 3 raises `estimate: verdict estimate_only: [...]` — the buy and ingest step (`sudo mt-run tick`) is unreachable. Note the asymmetry the same commit introduces: `check_ceilings=False` returns `Estimate.BUY` *before* the verdict check, so `--estimate` sidesteps this while the real run still trips over it. The verdict rule needs to accept the estimate-only outcome (the ceilings and the `nothing_to_buy` branch already carry the decision), and `VERDICT_ALLOWED` should either become the value the estimate actually reports or the check should be dropped as unrepresentable. Add a test whose verdict is `estimate_only`.

### [CONCERN] `over_sessions` drops the family, so the failure message attributes one family's over sessions to another

`over_sessions` filters the statistics `coverage` document's `volume_checks` on `v["verdict"] == VolumeClass.OVER.value` only and names the session without its family, although the field is present (`volume_verdict_dict` in `src/manta_trading/data/tick/tick_coverage.py` emits `family` per verdict). That document's checks are produced by `_volume_checks` in `src/manta_trading/data/tick/tick_coverage_statistics.py`, which runs every wanted volume family — the tick tier *and* the bars — so the list mixes families. `_family_evidence` collects those names into one `named[product]` list and `over_failures` prints that same list under *each* row whose `cross_tier` over count is non-zero, keyed by `row.label`. An ES tbbo over-session is therefore reported as an ES `ohlcv-1m` over-session (and the identical list is repeated for both rows). Since an `over` is the defect this evidence exists to name, filter by `v["family"]` when collecting and pass the counts per family.

### [NOTE] A naive `requested_at` raises `TypeError` where the sibling check raises `CutoverError`

`resubmit_origins` parses `requested_at` with `datetime.fromisoformat` and compares the result to `since` inside `min`/`_check_resubmit`, both timezone-aware. A value that parsed but carried no offset would fail with a raw `TypeError` from the comparison rather than the deliberate stop this module uses everywhere else; `check_dataset_edge`'s `_edge_of` (`scripts/purchase_verdicts.py`) guards exactly this case with "not a UTC instant". The current CLI renders `TIMESTAMPTZ` through `_cell` with its offset, so the risk is low — the `TypeError` would also surface as the top-level handler's `FAILED: unexpected payload`, not a traceback. Adding the same two-line guard would make the two readers consistent.

### [NOTE] `EXIT_USAGE` is now dead

`EXIT_USAGE = 2` is no longer referenced after `main` switched to returning argparse's own exit code (`except SystemExit as exc: return int(exc.code or 0)`); the only other definition in the tree, `scripts/wal_segment_name.py`, uses its own. The module docstring already spells the `2` through argparse, and the test `test_no_argument_prints_usage_and_exits_two` pins the literal. Delete the constant.

### [NOTE] The module docstring still says the preflight names the ceiling command

The header says to run "with the PM's ceiling script already run (the preflight names its command if not)", but `_ceiling` now raises with `NO_CEILING_HINT` — "the PM sets the ceilings with the command the `--estimate` run prints" — so the preflight no longer names a command at all. Step 1's description ("env file has no plan and both ceilings") is likewise conditional on `--estimate` now skipping the ceiling checks. Point both at the `--estimate` run.

### [PASS] Universe-driven reads, per-product calendars and the gated preflight are correct and tested

Verified against the modules they call: `coverage_reads` reproduces the three hard-coded reads from `TICK_UNIVERSE` and treats a `None` end as a stop rather than a default; `calendar_for` delegates to `calendar_for_product`, whose `KeyError` carries the known-products message that is re-raised as `CutoverError` (`src/manta_trading/data/tick/constants.py`), so an unmapped product has no calendar; `product_of_symbols` parses the comma-joined string that `tick_debug_cmds._cell` actually renders, and `test_bar_job_dirs_read_the_rows_the_real_cli_renders` feeds a real `_cell` rendering through it; `run_session_check` passes the calendar it is given and keeps exit 1 vs exit 2 distinct; the gate and edge refusals run before the install and before any state is written, which the tests assert on the notes directory. Test coverage for the new pure functions is thorough, including the open-ended-want stop, the resubmit-origin tie-break, the ledger baseline that "predates 307", and the record-total delta.

### Run Digest

- Response length: 8973 chars
- Response is newline-free: no
- Tool calls made: 41
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 253956
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 2104529 / 1763712 / not computed / 72988 / 67270
- Duration: 328.4 s
- Cost: $0.09 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
