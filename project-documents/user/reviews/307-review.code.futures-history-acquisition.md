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
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/307-slice.futures-history-acquisition.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261111
dateUpdated: 20261111
reviewedSha: 697ad7ceb93ddbb73c05269c3f59af8c9bc1af81
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 34
diffTruncated: false
turns: 20
promptTokens: 2143029
cachedTokens: 1829248
completionTokens: 85705
reasoningTokens: 76029
durationSeconds: 437.4
costUsd: 0.097085
costBasis: alias_pricing
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "The derived 30-day ceiling ignores unheld provider jobs, which the guard counts — so the printed remedy can never clear the guard"
    location: "scripts/cutover_304_checks.py#ceiling_command"
  - id: F002
    severity: note
    category: documentation-drift
    summary: "The module docstring's Steps list omits the two refusals the gate now adds to step 1"
    location: "scripts/cutover_304_first_purchase.py"
  - id: F003
    severity: note
    category: architectural-decay
    summary: "The same \"every history want has an end\" invariant is re-implemented in three modules"
    location: "scripts/cutover_304_evidence.py#coverage_reads"
  - id: F004
    severity: note
    category: architectural-decay
    summary: "`cutover_304_checks.py` now runs to about 330 lines, past the ~300-line guidance the slice restates"
    location: "scripts/cutover_304_checks.py"
  - id: F005
    severity: note
    category: test-debt
    summary: "No test drives `_steps` down the buy branch, so the wiring from a buyable estimate to `mt-run tick` is untested"
    location: "test/unit/scripts/test_cutover_304_first_purchase.py"
  - id: F006
    severity: pass
    category: correctness
    summary: "The estimate handling now matches the real payload, and the 307 entry points write no state before the gates pass"
    location: "scripts/cutover_304_first_purchase.py#_estimate_only"
---

# Review: code — slice 307

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash

## Findings

### [CONCERN] The derived 30-day ceiling ignores unheld provider jobs, which the guard counts — so the printed remedy can never clear the guard

`ceiling_values`/`ceiling_command` derive `--per-pass` from `planned` and `--cap-30d` from `planned + trailing`, and both refusal messages in `judge_estimate` tell the operator "the PM runs: {command}". But the guard's own 30-day sum is `trailing_total + unheld_total + planned_new` (`evaluate_spend` in `src/manta_trading/data/tick/spend_guard.py`: `over_30d = _over(trailing_total + unheld_total + planned_new, cap_30d)`), and `plan_summary` (`src/manta_trading/data/tick/purchase_plan.py`) carries `unheld_jobs` alongside `planned_usd`/`trailing_30d_usd` precisely because provider jobs no manifest row holds count toward the cap. `_fits_from`/`_breach_reasons` therefore name unheld jobs in `reasons`, while the remedy command leaves them out of the cap. When `unheld_jobs` is non-empty the PM can run the printed command and the full cutover stops again on the same 30-day check with the same command printed — a remedy that does not converge. The per-pass value is consistent (the guard compares the same marginal total to it); only the cap is short. Reading `planned_usd + trailing_30d_usd + unheld_usd` (the estimate already carries each job's `cost_usd`) would make the printed command the one that actually clears the guard.

### [NOTE] The module docstring's Steps list omits the two refusals the gate now adds to step 1

The header was updated to say "the `--estimate` run prints the command that sets them" and step 1 now reads "ref exists and is on origin; env file has no plan and (not with `--estimate`) both ceilings", but the two refusals `run_gates` performs before the install — issue #69 must be `CLOSED` (D2) and the dataset edge must reach the latest want end (D1) — are not named anywhere in the Steps list, and `--gate-issue` is not mentioned in the usage block either (it reads `--slice <n> --gate-issue <n> [--estimate] <release-tag>` only in the invocation line, not in the steps). A reader following the steps would not know the cutover can stop on an open issue or a short edge before anything is installed. The tests assert that ordering (`test_an_open_gate_issue_stops_before_the_install_and_writes_nothing`), so this is a documentation gap, not a behavioural one.

### [NOTE] The same "every history want has an end" invariant is re-implemented in three modules

`coverage_reads` (`scripts/cutover_304_evidence.py`) and `latest_want_end` (`scripts/purchase_verdicts.py#latest_want_end`) each walk `TICK_UNIVERSE[*].history`, each raise `CutoverError` on a `None` end with nearly the same message, and `latest_want_end` adds a third variant of the "no wants" case. The invariant is one thing — `HistoryWant.end` is fixed for every configured want (`_HISTORY_WANTS` in `src/manta_trading/data/tick/universe.py`) — and the project rule is that a value used in conditionals is defined once. Both call sites are correct today; the cost is that a future change to the invariant has to be made in two places, and the two messages can drift. Having the checks module expose one `history_wants()` (or one `require_fixed_ends()`) that both readers consume would collapse it.

### [NOTE] `cutover_304_checks.py` now runs to about 330 lines, past the ~300-line guidance the slice restates

The diff's final hunk header is `@@ -177,20 +288,42 @@`, i.e. the new `bar_job_dirs`/`run_session_check` block occupies lines 288–329, so the file ends near line 330 — about 10% past both the project's "~300 lines where practical" and this slice's Technical Requirement ("Each script stays within about 300 lines", which is why `purchase_verdicts.py` and `purchase_accounting.py` exist at all). The added material is cohesive (ceiling derivation, the resubmit machinery, the universe-derived product/calendar lookups), so this is a size note rather than a design complaint; splitting the resubmit helpers out alongside the new modules would restore the stated bound.

### [NOTE] No test drives `_steps` down the buy branch, so the wiring from a buyable estimate to `mt-run tick` is untested

Every `_drive`/`_steps` invocation in this file passes a `nothing_to_buy` summary (`NOTHING`) or a `refused` one; the `--estimate` tests use a `refused` summary too. `run_passes` is tested directly, and `judge_estimate` returning `Estimate.BUY` for verdict `estimate_only` is tested in `test_cutover_304_checks.py`, but nothing exercises the `else` branch of `_steps` — `say("4/6 buy and ingest")` → `run_passes(_tick_once)` — through `_steps`. That branch is the one that spends money, and it is also where a wrong argument or a missing second-estimate call would hide. A summary with `verdict: "estimate_only"`, adequate ceilings and one planned request, asserting the call order contains `install`, `tick`, `collect`, would close it.

### [PASS] The estimate handling now matches the real payload, and the 307 entry points write no state before the gates pass

Verified against the producers, not the diff text: `purchase_summary` reads one `phases` entry named `purchase` with `outcome == "ok"`, which is the shape `PassResult.to_dict` emits (`src/manta_trading/data/tick/pass_contract.py`) and what the captured `test/unit/scripts/fixtures/pass_estimate_only.json` holds; a `skipped` or absent phase stops with a named `CutoverError` rather than a `KeyError` from the top-level handler. `judge_estimate` now accepts `estimate_only` (the only verdict `evaluate_spend` can report under `--estimate-only`) and still refuses anything else; the ceiling checks run only when `check_ceilings=True`, so `--estimate` prints the command for a short or unset ceiling instead of stopping. `_estimate_only` calls `preflight`, `check_plan_unset`, `check_checkout_database`, `run_gates`, then `fetch_estimate` and returns — no install, no pass, no start/baseline file, which `test_estimate_prints_the_command_and_buys_nothing` asserts by inspecting the notes directory. `calendar_for` delegates to `calendar_for_product` (whose `KeyError` message is re-raised as `CutoverError`, so an unmapped product has no default calendar), and `product_of_symbols` consumes the comma-joined rendering that `tick_debug_cmds._cell` actually produces, pinned by a test that runs a real `_cell` over the row.

### Run Digest

- Response length: 7685 chars
- Response is newline-free: no
- Tool calls made: 34
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 304082
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 2143029 / 1829248 / not computed / 85705 / 76029
- Duration: 437.4 s
- Cost: $0.10 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
