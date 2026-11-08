---
docType: review
layer: project
reviewType: code
slice: futures-completeness-surfaces
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/305-slice.futures-completeness-surfaces.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: bdd4500c6b9d35e224d12bb050be6902b2c88430
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
turns: 2
promptTokens: 288626
cachedTokens: 16579
cacheWriteTokens: 272043
completionTokens: 2349
durationSeconds: 22.8
costUsd: 1.123071
costBasis: provider
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: architectural-decay
    summary: "`_by_product` and the tier-extraction logic are duplicated"
    location: "src/manta_trading/api_server/models/futures.py:306-316"
  - id: F002
    severity: concern
    category: error-handling-observability
    summary: "Tick-tier extraction raises a bare `ValueError` if the invariant breaks"
    location: "src/manta_trading/api_server/models/futures.py:316"
  - id: F003
    severity: concern
    category: architectural-decay
    summary: "`_family_report` is long and takes many parameters"
    location: "src/manta_trading/data/tick/family_report.py#_family_report"
  - id: F004
    severity: concern
    category: consistency-rot
    summary: "Mutable progress side-channel is threaded through the read path"
    location: "src/manta_trading/data/tick/family_report.py:ReadProgress"
  - id: F005
    severity: concern
    category: consistency-rot
    summary: "Unnecessary `# noqa: E402` and inconsistent import placement in tests"
    location: "test/integration/test_overview_gather.py"
  - id: F006
    severity: note
    category: consistency-rot
    summary: "Function-level imports in the health, accounting and overview modules"
    location: "src/manta_trading/cli/commands/health_tick.py#_futures_rows"
  - id: F007
    severity: note
    category: other
    summary: "Positive observations"
    location: "unverified"
---

# Review: code — slice 305

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] `_by_product` and the tier-extraction logic are duplicated

`_by_product` is defined identically in `api_server/models/futures.py:306` and `cli/commands/accounting_futures.py:55`. Both also repeat the `(tier,) = [x for x in lines if x.family == SchemaFamily.TICK_TIER.value]` extraction at `futures.py:316` and `accounting_futures.py:63`. This breaks the project's DRY rule. Both modules already import `TickAccountingLine` from `data/tick/tick_accounting.py`, so a single `lines_by_product` and `tier_line` there would serve both.

### [CONCERN] Tick-tier extraction raises a bare `ValueError` if the invariant breaks

`(tier,) = [...]` depends on `futures_completeness` always emitting exactly one tick-tier line per product. That holds today because `include_tier=True` is hardcoded in `futures_completeness`. If it breaks, the failure is an unpacking `ValueError` with no context. In the API route that error would escape `from_result`, which sits outside the guarded `read_tick`. An explicit check that names the product would make the failure diagnosable.

### [CONCERN] `_family_report` is long and takes many parameters

It takes eight parameters and runs roughly 70 lines. It mixes progress marking, edge computation, scope reading, ledger totals, building the status, and the statistics branch. This exceeds the project's ~50-line function guideline and the Python rule of 20 lines preferred. Splitting out building `FamilyStatus` from the reads would improve readability. `family_reports` and `family_check` are also at the upper end.

### [CONCERN] Mutable progress side-channel is threaded through the read path

`ReadProgress` is mutated by `_mark` before each step, and callers read it after catching a failure. That is documented and tested, and it works. But the state is written implicitly, so a new read step added without a `_mark` call would silently report the previous step in error text. Each failure site would be more reliable if the step were wrapped in a context manager or the exception were annotated.

### [CONCERN] Unnecessary `# noqa: E402` and inconsistent import placement in tests

The new `manta_trading.data.tick` imports carry `# noqa: E402` even though they sit in the normal import block and are not after executable code. This is noise that hides real E402 violations.

### [NOTE] Function-level imports in the health, accounting and overview modules

`_futures_rows` imports `TickUnavailable` and `unavailable_text` locally. `TickUnavailable` is already importable from `line_state` at module level, since `family_report` imports it there. This looks like a deliberate lazy-import pattern for the CLI, but it is inconsistent with the module's own top-level imports.

### [NOTE] Positive observations

- The JIT-off setting is centralized in `TICK_JIT_OPTION` and `TICK_JIT_STATEMENT`, applied on all three connection paths, with a unit test for each.
- The findings query is now set-based with parameters only. The f-string SQL interpolates only code constants, such as `ledger_units(...)` and `SHAPE_WHERE`, never values.
- The load-test fixture drops only the database it created, on the test admin URL, and never reads the production URL variable. That complies with the Production Database Protection rules.
- Failures are not swallowed in the read path. `family_report` catches nothing, and the callers that catch go through `read_line` at the process boundary.
- Test coverage is broad: unit tests for edge logic, severity rules and models, integration tests for cross-surface agreement, and a load test at manifest scale.

### Run Digest

- Response length: 4428 chars
- Response is newline-free: no
- Tool calls made: 1
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 2
- Tokens — prompt / cached / cache write / completion / reasoning: 288626 / 16579 / 272043 / 2349 / not computed
- Duration: 22.8 s
- Cost: $1.12 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7

## Response (Phase 6, 2026-11-08)

Three fixed, three left as they are.

### Fixed

**F001 (duplicated grouping and tick-tier lookup).** `lines_by_product` and
`tier_line` now live in `data/tick/tick_accounting.py`. The API model and the
CLI accounting section both call them, and the two private copies are gone.

**F002 (bare unpacking `ValueError`).** `tier_line` checks for exactly one
tick-tier line and raises `ValueError` naming the product and the count. A unit
test covers it. Handled together with F001.

**F005 (stray `# noqa: E402`).** Removed. This slice added them, and the
imports are in the normal import block.

### Not changed

**F003 (`_family_report` length).** It is about 65 lines with its signature and
docstring, and each step is one call with a progress mark. Splitting it would
pass the same state through another layer and buy little.
`family_report.py` is already at 300 lines.

**F004 (progress side-channel).** It works and has its own tests
(`test_family_report_progress.py`). Wrapping each step in a context manager
replaces one `_mark` call with one `with` block, so a new step can still leave
it out. Not worth the churn.

**F006 (function-level imports).** A note, and cosmetic. Left as is.
