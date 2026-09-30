---
docType: review
layer: project
reviewType: code
slice: historical-acquisition-pass
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/224-slice.historical-acquisition-pass.md
aiModel: claude-sonnet-5-5
status: complete
dateCreated: 20260930
dateUpdated: 20260930
reviewedSha: a99dcc57a5ee1a59ee014026b8880b07e73054e4
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: true
squadronVersion: 0.16.0
findings:
  - id: F001
    severity: concern
    category: design
    summary: "Phase summaries are an untyped dict contract duplicated as string literals across modules"
    location: "src/manta_trading/cli/commands/tick_pass_render.py:145-260"
  - id: F002
    severity: concern
    category: error-handling
    summary: "A malformed definition record raises `TypeError` out of the pass instead of failing the unit"
    location: "src/manta_trading/data/tick/definitions.py:_value"
  - id: F003
    severity: concern
    category: error-handling
    summary: "`product_of_shape` `ValueError` escapes unmapped from planning"
    location: "src/manta_trading/data/tick/purchase_plan.py:_owned, _resubmits"
  - id: F004
    severity: concern
    category: correctness
    summary: "Import-time and runtime `assert` used for invariants"
    location: "src/manta_trading/cli/commands/tick_exit.py:34"
  - id: F005
    severity: concern
    category: structure
    summary: "Function and file size exceed the project guideline"
    location: "src/manta_trading/data/tick/spend_guard.py:evaluate_spend"
  - id: F006
    severity: concern
    category: design
    summary: "`pass_contract.py` copies the Kalshi contract"
    location: "src/manta_trading/data/tick/pass_contract.py"
  - id: F007
    severity: concern
    category: testing
    summary: "Test code weakens strict typing and hygiene"
    location: "test/integration/data/test_tick_purchase_phase.py:_units"
  - id: F008
    severity: note
    category: testing
    summary: "Load-test tier not visible for the network and concurrency paths"
    location: "unverified"
  - id: F009
    severity: note
    category: design
    summary: "Duplicate money formatting"
    location: "src/manta_trading/cli/commands/tick_pass_render.py:_money"
  - id: F010
    severity: pass
    category: correctness
    summary: "Spend guards, submit path and blocking-I/O discipline"
    location: "src/manta_trading/data/tick/purchase_phase.py"
---

# Review: code — slice 224

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5
**Diff:** truncated: 262144 of 292325 characters reached the model

## Findings

### [CONCERN] Phase summaries are an untyped dict contract duplicated as string literals across modules

The phases build `summary: dict[str, Any]` in `acquisition_pass.py`, `purchase_phase.py`/`purchase_plan.plan_summary`, `definitions.py` and `AdvanceTally.to_dict`. The renderer then reads those keys back by literal (`"wanted_days"`, `"unheld_jobs"`, `"cap_30d_usd"`, `"skipped"`, `"waited_seconds"`, `"unknown_submits"` and so on). Producer and consumer share no definition, so renaming a key breaks the report or the JSON without any failing check. Only `_DELIVERY_LABELS` has a parity test. This conflicts with the "define a value once" rule. Use typed summary dataclasses or shared key constants.

### [CONCERN] A malformed definition record raises `TypeError` out of the pass instead of failing the unit

`_value` raises `TypeError` for an unexpected field type. `project_definitions` only catches `DefinitionRejected`, and `run_phase` only maps `ProviderError` and storage failures. A bad record therefore crashes the whole run with a traceback and leaves the unit at *verified*, so every later run hits it again. Convert this to `DefinitionRejected`, so the unit is exhausted with a reason as the other rejects are.

### [CONCERN] `product_of_shape` `ValueError` escapes unmapped from planning

`adopt.product_of` wraps the `ValueError` as `TickAdoptionRefused`. `_owned` and `_resubmits` call `product_of_shape` directly. A manifest row with an unusual `stype_in` or symbols shape will crash the purchase phase with a raw traceback. That skips the report and the exit-code mapping. Wrap it in a domain error that `run_phase` or `exit_code_for` maps.

### [CONCERN] Import-time and runtime `assert` used for invariants

The exhaustiveness guard on `EXIT_BY_OUTCOME`, the `_PHASE_LINES` guard in `tick_pass_render.py`, and the runtime asserts in `definitions.project_unit`, `in_flight._poll`, `deliver_job` and `purchase_phase` all vanish under `python -O`. The exit-code guard is the most important one, because it is what stops a new outcome from silently exiting 0. Raise explicit errors, or rely on the unit test that already checks this.

### [CONCERN] Function and file size exceed the project guideline

`evaluate_spend` is about 65 lines and mixes verdict computation with reason building. `manifest_reads.py` now runs well past ~300 lines (about 340 by the hunks shown). `build_plan` and `AvailabilityPhase.run` are near the limit. Extract reason building, and move the pass-specific reads out of `manifest_reads.py`, as was already done for `manifest_pass.py`.

### [CONCERN] `pass_contract.py` copies the Kalshi contract

This is a deliberate DRY exception (TD1), guarded by a parity test. It is still duplicated logic that will drift, and the copy already diverges by design. Consider moving the shared contract to a neutral module if a third consumer appears.

### [CONCERN] Test code weakens strict typing and hygiene

`_units` has function-local imports and an unused import kept alive with `# noqa: F401`. `test_tick_pass.py` uses `# type: ignore` on `Sleeper(run.clock)` and on the `fetchone()` unpack. `test_a_bad_date_is_a_usage_error` asserts `EXIT_PROVIDER` only because click's usage code happens to equal 2. The rules include tests in strict pyright, so type the fixtures properly. Assert click's code directly, not through an unrelated constant.

### [NOTE] Load-test tier not visible for the network and concurrency paths

The pass adds polling, `to_thread` I/O and executor use. The rules ask for at least one `tests/load/` test on such paths, and none appears in the reviewed part of the diff. Please confirm one exists or add one.

### [NOTE] Duplicate money formatting

`_money` here and `spend_guard._usd` both format dollars to four decimals, and `tick_render.usd` covers the same ground. Consider sharing one helper.

### [PASS] Spend guards, submit path and blocking-I/O discipline

Both spend ceilings must be set. The attempt is stamped before the paid call, and there is no re-submit without a job-list search. `fetch_range` is never called. Blocking calls (provider, calendar, hashing, decode) run through `asyncio.to_thread`. SQL is parameterised, and the only interpolated names come from module constants. `run_phase` is a documented boundary that logs with `logger.exception`.

### Run Digest

- Response length: 5378 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
