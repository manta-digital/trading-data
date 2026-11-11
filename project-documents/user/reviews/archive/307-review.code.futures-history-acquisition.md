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
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/307-slice.futures-history-acquisition.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261111
dateUpdated: 20261111
reviewedSha: b5c3d781963789cef917771538a0b51a10bab29b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 2
diffTruncated: false
turns: 3
promptTokens: 200089
cachedTokens: 16579
cacheWriteTokens: 183506
completionTokens: 2564
durationSeconds: 23.5
costUsd: 0.766588
costBasis: provider
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: note
    category: correctness
    summary: "Naive/aware datetime comparison could raise an uncaught TypeError"
    location: "scripts/cutover_304_checks.py#_check_resubmit"
  - id: F002
    severity: note
    category: consistency-rot
    summary: "Scripts named for slice 304 now serve every purchase slice"
    location: "scripts/cutover_304_first_purchase.py:1"
  - id: F003
    severity: note
    category: test-debt
    summary: "Tests reach into private helpers of the CLI"
    location: "test/unit/scripts/test_cutover_304_checks.py:300"
  - id: F004
    severity: note
    category: error-handling-observability
    summary: "`main` converts `SystemExit` from argparse into a return code"
    location: "scripts/cutover_304_first_purchase.py#main"
  - id: F005
    severity: pass
    category: other
    summary: "Explicit failure on missing or unknown data"
    location: "scripts/purchase_verdicts.py"
---

# Review: code — slice 307

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [NOTE] Naive/aware datetime comparison could raise an uncaught TypeError

`resubmit_origins` parses `requested_at` with `datetime.fromisoformat`, and `_check_resubmit` compares the result to `since`, which is timezone-aware. If a row ever rendered a naive timestamp, `made < since` would raise a raw `TypeError` instead of a `CutoverError`. `check_dataset_edge` already rejects naive instants explicitly, so adding the same guard in `resubmit_origins` would make the behavior consistent. The current CLI very likely emits aware ISO strings, so this is low risk.

### [NOTE] Scripts named for slice 304 now serve every purchase slice

The module docstring says the cutover is shared by every purchase slice, and it now takes `--slice`. The `cutover_304_*` filenames and the `prog=` string still say 304. A later rename to a neutral name such as `purchase_cutover_*` would stop the naming drifting from what the code does. The tests and the new `purchase_*` modules already import them by name, so the rename is not urgent.

### [NOTE] Tests reach into private helpers of the CLI

Several tests call `tick_debug_cmds._cell` and `accounting_futures._product_payload` directly. This is deliberate, because it ties the fixtures to the real output format, as the parsing rules require. It also makes the tests brittle if those private functions are renamed. Exposing a public render function would avoid that.

### [NOTE] `main` converts `SystemExit` from argparse into a return code

Catching `SystemExit` around `parse_args` is justified by the inline comment: argparse has already printed usage, and `--help` exits 0. The handler is narrow and sits at a process boundary, which is acceptable. `ArgumentParser(exit_on_error=False)` would be a cleaner alternative, but it doesn't cover every error path.

### [PASS] Explicit failure on missing or unknown data

Gate-issue, dataset-edge, bucket-classification and accounting parsing all raise `CutoverError` with a distinct message for each cause. There are no default values. The unknown-bucket and no-baseline-totals cases each have a test.

### Run Digest

- Response length: 2977 chars
- Response is newline-free: no
- Tool calls made: 2
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 3
- Tokens — prompt / cached / cache write / completion / reasoning: 200089 / 16579 / 183506 / 2564 / not computed
- Duration: 23.5 s
- Cost: $0.77 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 5
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 5
- Finding-shaped matches — surviving validation: 5
