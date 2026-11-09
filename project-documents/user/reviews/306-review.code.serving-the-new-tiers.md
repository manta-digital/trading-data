---
docType: review
layer: project
reviewType: code
slice: serving-the-new-tiers
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: PASS
verdictSource: stated
sourceDocument: project-documents/user/slices/306-slice.serving-the-new-tiers.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261109
dateUpdated: 20261109
reviewedSha: d1edf02d1e12e332bb8bdffdbd41060239ac9581
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 1
diffTruncated: false
turns: 2
promptTokens: 241308
cachedTokens: 16579
cacheWriteTokens: 224725
completionTokens: 2634
durationSeconds: 25.3
costUsd: 0.934285
costBasis: provider
squadronVersion: 0.24.24
categoriesNormalized: true
findings:
  - id: F001
    severity: pass
    category: architectural-decay
    summary: "Plan, verdict, and reader selection is centralised"
    location: "src/manta_trading/data/tick/tick_status_scope.py:49-59"
  - id: F002
    severity: pass
    category: correctness
    summary: "Parity input pairing is safe"
    location: "src/manta_trading/data/tick/parity_plan.py:60-75"
  - id: F003
    severity: pass
    category: test-debt
    summary: "Provider bar SQL and tests are sound"
    location: "src/manta_trading/data/tick/bar_reads.py:34-60"
  - id: F004
    severity: note
    category: consistency-rot
    summary: "Parity command imports private-turned-public helpers from the `get` command module"
    location: "src/manta_trading/cli/commands/tick_parity_cmds.py:23-29"
  - id: F005
    severity: note
    category: type-contract-debt
    summary: "Loose types at a few helper boundaries"
    location: "src/manta_trading/cli/commands/tick_get_writers.py:88"
  - id: F006
    severity: note
    category: consistency-rot
    summary: "Unrelated reformatting in a test file"
    location: "test/unit/api_server/test_openapi_artifact.py:33-63"
---

# Review: code — slice 306

**Verdict:** PASS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Plan, verdict, and reader selection is centralised

`verdicts_for(family)` is now the single place a family is paired with a verdict reader. `get_plan._incomplete` and `parity_plan` both use it, and it raises `ValueError` for families that have no session reader. This is explicit failure rather than a silent fallback, as CLAUDE.md requires. `BAR_SOURCE` is keyed by `TICK_BAR_GRANULARITIES`, so a new served bar size cannot be added without naming its source. `test_bar_source.py` and `test_verdicts_for.py` cover both.

### [PASS] Parity input pairing is safe

The `bar[day]` lookup cannot raise `KeyError`. `session_verdicts` returns one verdict per input session, and `parity_inputs` filters its sessions to the dates in `plan.segments`. Both verdict sets are read on the same connection, inside the `REPEATABLE READ READ ONLY` snapshot opened in `tick_parity_cmds._run`. A session is compared only when both tiers are `complete`. Every other planned session is reported as skipped with both statuses, so a partial load is not reported as a disagreement.

### [PASS] Provider bar SQL and tests are sound

The bucket formula is defined once as `SESSION_BUCKET` and reused by `bar_parity`, so tick-derived and provider minutes share the same key. All values are bound as parameters; only identifiers are composed with `sql.Identifier` and `sql.SQL`. The tests compute rollups independently in Python rather than reusing the SQL. They also cover DST changes, an early close, the daily break, and real DBN sample bars. The old read-time tick-bar tests were moved to `test_bar_parity_sql.py` rather than dropped.

### [NOTE] Parity command imports private-turned-public helpers from the `get` command module

`tick_parity_cmds` and `tick_parity_render` now import `CONTRACT_OPTION`, `plan_for_target`, `target_rule`, `head_lines`, `iso_day`, and `target_dict`. These were private names that were made public by dropping the underscore. This works, but it couples one CLI command module to another. If more commands reuse them, move the shared options and plan helpers into a neutral module.

### [NOTE] Loose types at a few helper boundaries

`iso_day(day: Any)` is now shared across modules. Annotating it as `date | None` would satisfy strict pyright. The same applies to `Ohlcv = tuple[int, ...]` in `bar_parity.py`, which could be a fixed-length 5-tuple, and to `read: Any` in `test/tick_support/reads.py`. None of these affect behavior.

### [NOTE] Unrelated reformatting in a test file

The `Path(...)` expression and the assertion message string were reformatted, which is unrelated to this slice. It is harmless and consistent with `ruff format`, but it would be cleaner as a separate `style:` commit.

### Run Digest

- Response length: 3473 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 241308 / 16579 / 224725 / 2634 / not computed
- Duration: 25.3 s
- Cost: $0.93 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6
