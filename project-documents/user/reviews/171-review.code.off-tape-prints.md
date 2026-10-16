---
docType: review
layer: project
reviewType: code
slice: off-tape-prints
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/171-slice.off-tape-prints.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261016
dateUpdated: 20261016
reviewedSha: f45d70382206b95854a54f9df2f8b29f560d91e5
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
turns: 1
promptTokens: 79928
cachedTokens: 3232
cacheWriteTokens: 76694
completionTokens: 4977
durationSeconds: 44.4
costUsd: 0.363187
costBasis: provider
squadronVersion: 0.22.0
findings:
  - id: F001
    severity: concern
    category: design-dry
    summary: "Duplicated streaming-frames logic in measurement script"
    location: "scripts/measure_off_tape_share.py:67-95"
  - id: F002
    severity: concern
    category: security
    summary: "Raw f-string SQL with interpolated values in sweep-measurement script"
    location: "scripts/measure_off_tape_sweep_chunk.py:54-58"
  - id: F003
    severity: concern
    category: configuration
    summary: "Scripts read configuration via os.environ rather than the settings object"
    location: "scripts/cut_off_tape_fixtures.py:95"
  - id: F004
    severity: concern
    category: typing
    summary: "Bare `psycopg.Connection` and loose types will fail strict pyright"
    location: "src/manta_trading/data/quality/off_tape_store.py:55"
  - id: F005
    severity: concern
    category: readability
    summary: "Side-effecting list comprehensions drive the sweep retry"
    location: "src/manta_trading/market/maintenance/off_tape_sweep.py:209-212"
  - id: F006
    severity: concern
    category: naming
    summary: "Misleading \"biggest\" selection in chunk-timing script"
    location: "scripts/measure_off_tape_sweep_chunk.py:92-96"
  - id: F007
    severity: note
    category: structure
    summary: "Circular-import workarounds"
    location: "src/manta_trading/cli/commands/data.py:134-137"
  - id: F008
    severity: note
    category: correctness
    summary: "`SET LOCAL statement_timeout` depends on the caller's transaction state"
    location: "src/manta_trading/cli/commands/health_off_tape.py:49-51"
  - id: F009
    severity: note
    category: structure
    summary: "`eligible_bar` is production code used only by tests"
    location: "src/manta_trading/data/quality/off_tape.py:157-172"
  - id: F010
    severity: pass
    category: testing
    summary: "Exception handling, DB safety and test coverage"
    location: "src/manta_trading/data/quality/off_tape_store.py#mark_inserted"
---

# Review: code — slice 171

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Duplicated streaming-frames logic in measurement script

`_symbols` and `_frames` are copies of `iter_symbol_frames` and `_frames` in `src/manta_trading/data/quality/off_tape_store.py`. Both use the same server-side cursor, the same carry-over of the last symbol's rows, and the same groupby. The only differences are the query and the column list. A fix to the symbol-boundary logic would have to be made twice. Parameterize the store helper (query and columns) and reuse it. The script also redefines `RTH_START` and `RTH_END`, which the detector tests define as well.

### [CONCERN] Raw f-string SQL with interpolated values in sweep-measurement script

`_copy` builds the `COPY (SELECT ...)` statement by interpolating `start.isoformat()` and `end.isoformat()` into the SQL string. The SQL rules say to never f-string SQL. The values come from `datetime.fromisoformat`, so injection risk is low. Still, `COPY (query)` can't take bind parameters, so use `psycopg.sql.Literal` or `cursor.mogrify` to build the query. The same applies to the f-string `CREATE DATABASE "{name}"` and `DROP DATABASE`. Use `psycopg.sql.Identifier` there.

### [CONCERN] Scripts read configuration via os.environ rather than the settings object

The three scripts use `os.environ["MT_TIMESCALE_DB_URL"]` or `MT_TIMESCALE_TEST_URL` directly. The Python rules say "one value, one source": config goes through the pydantic settings object, because `.env` values loaded by settings are not in `os.environ`. A developer with only a `.env` file gets a bare `KeyError`. Either load via the settings object or take the URL as an explicit CLI argument. The CLI argument matches the "destructive/maintenance tooling takes its URL from the caller" principle that `mt data off-tape sweep` already follows.

### [CONCERN] Bare `psycopg.Connection` and loose types will fail strict pyright

The project requires `typeCheckingMode = "strict"`, with zero errors a merge blocker. Many new signatures use unparameterized `psycopg.Connection`. These include `off_tape_store.py`, `off_tape_sweep.py`, `cli/commands/off_tape.py`, and the scripts. Strict mode reports `reportMissingTypeArgument` for these. `health_off_tape.py` correctly uses `psycopg.Connection[Any]`, so the style is inconsistent within the slice. `tuple[object, ...]` rows and `dict[int, tuple]` in `test_eligible_sql.py` have the same problem. Please run pyright and align them.

### [CONCERN] Side-effecting list comprehensions drive the sweep retry

`skipped = [chunk for chunk in swept.pending if not _attempt(...)]` and the `report.unlocked` comprehension run the sweep as a side effect of filtering. `_attempt` mutates `report.done`, so the control flow is hidden in a predicate. Replace both with explicit loops that collect locked chunks. This is the most important part of the module to read correctly.

### [CONCERN] Misleading "biggest" selection in chunk-timing script

`biggest = max(chunks, key=lambda c: (c.end <= args.end and c.start >= args.start, c.end))` picks the latest chunk fully inside the requested range. It does not pick the biggest chunk. The variable name is wrong, and the projection uses that chunk's rows/s. Rename it, or select by actual row count.

### [NOTE] Circular-import workarounds

`health.py` imports `gather_off_tape` inside `gather` because `health_off_tape` imports `HealthCheck` from `health`. `data.py` registers `off_tape_app` with a mid-file import marked `noqa: E402`. Both work. Moving `HealthCheck` into a small shared module would remove the circularity in the health case.

### [NOTE] `SET LOCAL statement_timeout` depends on the caller's transaction state

`SET LOCAL` only takes effect inside a transaction block. On an autocommit connection it does nothing but emit a warning. I did not verify how `mt data health` opens its connection. The unit test mocks the cursor, so it cannot detect this. Confirm that the connection runs in a transaction here.

### [NOTE] `eligible_bar` is production code used only by tests

The docstring says it is a mirror "for tests and reasoning". It is a useful oracle for the SQL-equivalence tests. Consider moving it into the test support package so it isn't shipped in the runtime module.

### [PASS] Exception handling, DB safety and test coverage

- **Exceptions:** `mark_inserted` and `_attempt` log with `logger.exception` and re-raise. `LockNotAvailable` is the only specific exception swallowed, and a comment explains why.
- **Database safety:** the destructive statements in the new tests and scripts target only throwaway databases the process created. No test reads the production URL variable.
- **Tests:** the new tests cover the detector against real production fixtures, SQL/Python parity on every mask, both insert paths, rollback on detector failure, and sweep resume, lock, checksum and deferral behavior. They also cover the migration over compressed chunks and the load-tier latency check for both `prints` modes.

### Run Digest

- Response length: 6071 chars
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
- Tokens — prompt / cached / cache write / completion / reasoning: 79928 / 3232 / 76694 / 4977 / not computed
- Duration: 44.4 s
- Cost: $0.36 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
