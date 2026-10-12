---
docType: review
layer: project
reviewType: code
slice: operator-surface-full-parity
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/230-slice.operator-surface-full-parity.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261012
dateUpdated: 20261012
reviewedSha: 86d99f33c6a84c9d522c5b5df3ec694d3028e073
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 0
diffTruncated: false
durationSeconds: 48.0
squadronVersion: 0.21.5
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Cheap string-splicing used to build the streaming JSON object"
    location: "src/manta_trading/cli/commands/tick_get_writers.py:JsonWriter"
  - id: F002
    severity: concern
    category: error-handling
    summary: "Partial-read handler swallows the error without logging"
    location: "src/manta_trading/cli/commands/tick_get_cmds.py:_stream"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Connection-level and calendar failures are all labelled \"unreachable\""
    location: "src/manta_trading/cli/tick_composed.py:_READ_FAILURES"
  - id: F004
    severity: concern
    category: design
    summary: "Duplicated helper and an unrelated helper-shadowing name"
    location: "src/manta_trading/cli/tick_composed.py:48"
  - id: F005
    severity: concern
    category: conventions
    summary: "Magic default width and an `assert` used as control flow in writers"
    location: "src/manta_trading/cli/commands/tick_get_writers.py:_WIDTHS"
  - id: F006
    severity: note
    category: style
    summary: "Unrelated reformatting mixed into the slice"
    location: "src/manta_trading/cli/rendering/overview.py:177"
  - id: F007
    severity: note
    category: naming
    summary: "Two different `Row` types and repeated deferred imports"
    location: "src/manta_trading/data/tick/debug_reads.py:23"
  - id: F008
    severity: pass
    category: testing
    summary: "Tests, safety and structure"
    location: "test/integration/data/test_tick_get_cli.py"
---

# Review: code — slice 230

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] Cheap string-splicing used to build the streaming JSON object

`JsonWriter.begin` writes `json.dumps(head)[:-1] + ', "rows": ['`, and `_close` writes `json.dumps(tail)[1:]`. This only works while both dicts are non-empty, and it depends on `json.dumps` formatting. CLAUDE.md says "never use cheap hacks". Write the key prefix and suffix explicitly, for example by dumping each key/value pair separately, or by writing literal `{`, `}` and `,` around per-key `json.dumps` calls. The partial-output test covers only the happy path of this splice.

### [CONCERN] Partial-read handler swallows the error without logging

`except psycopg.OperationalError` returns `EXIT_STORAGE` after `writer.partial(...)`. The inline comment says only "QueryCanceled included". Nothing is logged with `logger.exception`, and no comment says why swallowing is correct. The error text is reported through the writer, but the project rule requires either a re-raise after logging or a justification comment. At minimum, log at ERROR level and say why this is the output boundary.

### [CONCERN] Connection-level and calendar failures are all labelled "unreachable"

`_READ_FAILURES` maps `TickPreflightError` and `TickCalendarError` (a range or calendar refusal) to `TickLineState.UNREACHABLE`. A refusal is not an outage, so the operator sees "unreachable: ..." for what may be a configuration or range problem. Other `psycopg.Error` subclasses raised during the read, such as a missing table, are not caught. They would crash `accounting` and `overview` after the pass row is closed, which breaks the stated "an outage never fails the command" guarantee (FR14). Add a distinct state or broaden the catch deliberately. Also check that `calendar_url(settings)` in `accounting._tick_lines` cannot raise outside `read_line`, because it runs before the guarded call.

### [CONCERN] Duplicated helper and an unrelated helper-shadowing name

`_first_line` is implemented twice: in `tick_composed.py` (with password scrubbing) and in `tick_get_cmds.py` (without it). `tick_get_cmds._first_line` does the same work minus the scrub, which breaks DRY. `tick_composed.open` also shadows the builtin `open`. Callers must write `tick_composed.open` and not import it directly, and the name will confuse readers and linters. Rename it, for example to `connect_bounded`. `_password` additionally re-resolves the URL that `open` already resolved.

### [CONCERN] Magic default width and an `assert` used as control flow in writers

`_WIDTHS.get(n, 8)` hard-codes a default width in the lookup, and `size` and `bid_sz` fall into it silently. Define the default once as a named constant. `TextWriter._lists` uses `assert self._plan is not None`, which is stripped under `-O`. Have `finish` and `partial` fail explicitly or take the plan as an argument. `finish(exit_code)` also ignores its argument in `TextWriter` and `CsvWriter`.

### [NOTE] Unrelated reformatting mixed into the slice

`render_source_lines`, `render_status_sources` and `render_universe` are reflowed, and `test_operations_serving.py` has a similar reformat. They change no logic but add diff noise. A separate `style:` commit would keep the history clean.

### [NOTE] Two different `Row` types and repeated deferred imports

`debug_reads.Row` is `dict[str, Any]` and `tick_reads.Row` is `tuple[Any, ...]`. Distinct names would avoid mix-ups. Function-local imports are repeated across `accounting._tick_lines`, `overview.read_tick_source` and the CLI commands. That is acceptable for start-up cost, but it should be applied consistently.

### [PASS] Tests, safety and structure

Tests are added alongside the implementation: parametrized refusals, partial-output tests in all three formats, a real broken-pipe subprocess test, and degraded-state tests that check exit codes. Database safety is respected. Fixtures use throwaway databases, `cli_settings` builds `Settings` with `_env_file=None`, the subprocess receives an explicit variable list, and the terminate-backend query is scoped to the scratch database. Prices and times are formatted exactly, with no floats. Reads are provider-free and use `REPEATABLE READ READ ONLY`. `status` and `accounting` share one bucket function, `product_scope` and `bucket_counts`, so they cannot disagree. The new constants are centralized.

### Run Digest

- Response length: 5001 chars
- Response is newline-free: no
- Tool calls made: 0
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: backend default
- Turns: not computed
- Tokens — prompt / cached / completion / reasoning: not computed / not computed / not computed / not computed
- Duration: 48.0 s
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8
