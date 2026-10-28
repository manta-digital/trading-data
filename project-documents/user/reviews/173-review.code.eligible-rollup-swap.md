---
docType: review
layer: project
reviewType: code
slice: eligible-rollup-swap
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/173-slice.eligible-rollup-swap.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261027
dateUpdated: 20261027
reviewedSha: 1eeb5069c3301cd4c9b4c49e437972694800d022
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
turns: 1
promptTokens: 80130
cachedTokens: 3232
cacheWriteTokens: 76896
completionTokens: 1550
durationSeconds: 15.0
costUsd: 0.329638
costBasis: provider
squadronVersion: 0.24.5
findings:
  - id: F001
    severity: concern
    category: sql-safety
    summary: "Raw SQL migration step: LOCK TABLE on a view and unverified DDL ordering"
    location: "src/manta_trading/market/schema/migrations/minute.py#_swap_eligible_rollups"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Swallowed exception in test reader thread"
    location: "test/integration/test_migration_064_eligible_rollup_swap.py:~230"
  - id: F003
    severity: concern
    category: design
    summary: "Scripts import with sys.path mutation and private names"
    location: "scripts/cutover_173_eligible_rollup_swap.py:42-60"
  - id: F004
    severity: concern
    category: security
    summary: "Health check SQL built with f-strings and interpolated timeout"
    location: "src/manta_trading/cli/commands/health_value_parity.py:fetch_value_parity"
  - id: F005
    severity: concern
    category: correctness
    summary: "Magic fallback of silent filter in bars read"
    location: "src/manta_trading/market/timescale_minute_db.py:~337"
  - id: F006
    severity: concern
    category: typing
    summary: "Mutable-default-style type ignores and weak typing in tests"
    location: "test/integration/test_health_value_parity.py:_sample"
  - id: F007
    severity: note
    category: conventions
    summary: "Unrelated formatting churn mixed into the diff"
    location: "test/load/test_187_api_nfr.py"
  - id: F008
    severity: pass
    category: design
    summary: "Swap design is idempotent, guarded, and well tested"
    location: "src/manta_trading/market/schema/eligible_swap_sql.py"
---

# Review: code — slice 173

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Raw SQL migration step: LOCK TABLE on a view and unverified DDL ordering

The rename transaction runs `LOCK TABLE data_status IN ACCESS EXCLUSIVE MODE` on a view, then renames and re-issues the view. This was reportedly probed on TimescaleDB 2.29.1, and an integration test covers a concurrent reader. The risk is that the test reader runs only a few queries against a small dataset. The deadlock-avoidance comment relies on lock order that is not asserted anywhere. This is not blocking, but the claim is only weakly tested.

### [CONCERN] Swallowed exception in test reader thread

The `except psycopg.Error` in `read()` collects errors into a list and asserts on them later, with an inline comment. That meets the rule (b). However, the thread itself can die from an exception outside the `try`, for example at connect. `reads > 0` is then the only guard. This is minor.

### [CONCERN] Scripts import with sys.path mutation and private names

The script imports the private `_connect` and `_mt` from `cutover_172_eligible_rollups`, and inserts into `sys.path` before the imports (requiring `noqa: E402`). That couples 173 to 172's internals. Consider making these helpers public in a shared module (`cutover_common`).

### [CONCERN] Health check SQL built with f-strings and interpolated timeout

`SET LOCAL statement_timeout = '{HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT}'` and the SQL assembly use f-strings. The inputs are constants and identifiers, not user data, so there is no injection risk today. It does not follow the "never f-string SQL" rule literally, though. Using `psycopg.sql` composition, or `set_config` with a parameter, would conform. Also, `SET LOCAL` outside an explicit transaction block has no effect on an autocommit connection. Confirm that the connection passed to `gather` is in a transaction. Otherwise the timeout silently doesn't apply.

### [CONCERN] Magic fallback of silent filter in bars read

The aggregated read now adds `AND open IS NOT NULL`. Dropping all-marked buckets is intentional and tested. It does mean a default-API response changes silently. Only `open` is checked, so this relies on the invariant that all four prices are NULL together. That invariant is documented in the rollup definition but not asserted here.

### [CONCERN] Mutable-default-style type ignores and weak typing in tests

`_sample` has `# type: ignore[no-untyped-def]` and no return annotation. The Python rules require strict pyright with zero errors and type hints on all signatures, including tests. Annotate it as `-> ValueParitySample`. Similar `# type: ignore[union-attr]` regex uses in `test_eligible_swap_sql.py` could be replaced with a helper that asserts the match.

### [NOTE] Unrelated formatting churn mixed into the diff

Several hunks only reformat existing code (`test_data_get.py`, `test_bars.py`, `test_187_api_nfr.py`, `test_timescale_minute_db.py`). They are harmless but add noise. Ideally these would go in a separate `style:` commit.

### [PASS] Swap design is idempotent, guarded, and well tested

Every rename and drop is guarded by `to_regclass`, so a rerun finishes a partial apply or does nothing. The names are defined once in `SWAP_NAMES`. Unit and integration tests cover cold start, populated, rerun-after-failure, rerun-on-finished and concurrent reader cases. The cutover script's control flow has one test per failure row. Timers stay held on an unreadable state, which is the safe side.

### Run Digest

- Response length: 4175 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: 1
- Tokens — prompt / cached / cache write / completion / reasoning: 80130 / 3232 / 76896 / 1550 / not computed
- Duration: 15.0 s
- Cost: $0.33 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
