---
docType: review
layer: project
reviewType: code
slice: time-bars-from-held-bars-and-ticks
targetKind: slice
rulesSource: project
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/309-slice.time-bars-from-held-bars-and-ticks.md
aiModel: claude-haiku-5-5
aiProfile: sdk
status: complete
dateCreated: 20261116
dateUpdated: 20261116
reviewedSha: b0f9155d1dcf04b554c195ba0dfce8a6ac994317
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 31
diffTruncated: false
effort: xhigh
turns: 32
promptTokens: 2936975
cachedTokens: 2506095
cacheWriteTokens: 430858
completionTokens: 90224
durationSeconds: 380.0
costUsd: 0.835341
costBasis: provider
squadronVersion: 0.24.31
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: architectural-decay
    summary: "Time-bar route orchestration duplicates the tick-bar routes"
    location: "src/manta_trading/api_server/routes/futures_time_bars.py:129"
  - id: F002
    severity: concern
    category: error-handling-observability
    summary: "Ledger-mismatch handling breaks the logging rule and diverges from its sibling"
    location: "src/manta_trading/api_server/routes/futures_time_bars.py:111"
  - id: F003
    severity: concern
    category: correctness
    summary: "Silent default in `TimeBarRead.read`"
    location: "src/manta_trading/data/tick/time_bar_reads.py:136"
  - id: F004
    severity: concern
    category: test-debt
    summary: "Bounded bucket arithmetic has no test where it matters"
    location: "src/manta_trading/data/tick/bar_reads.py:70"
  - id: F005
    severity: concern
    category: architectural-decay
    summary: "Dead production modules kept after the parity command was removed"
    location: "src/manta_trading/data/tick/bar_parity_report.py"
  - id: F006
    severity: concern
    category: type-contract-debt
    summary: "Granularity is an untyped string at the API boundary"
    location: "src/manta_trading/api_server/routes/futures_time_bars.py:85"
  - id: F007
    severity: concern
    category: test-debt
    summary: "Load test is not CI-gated"
    location: "test/load/test_309_time_bars_nfr.py"
  - id: F008
    severity: note
    category: dependency-config-debt
    summary: "pydantic is imported directly but not declared"
    location: "pyproject.toml"
  - id: F009
    severity: note
    category: other
    summary: "The bars response changes shape and repeats fields"
    location: "src/manta_trading/api_server/models/futures.py#FuturesBarsResponse"
  - id: F010
    severity: note
    category: performance-resource
    summary: "Ledger totals are fetched again for every window"
    location: "src/manta_trading/data/tick/time_bar_reads.py:161"
  - id: F011
    severity: pass
    category: security-hygiene
    summary: "Time-bar SQL binds every value"
    location: "src/manta_trading/data/tick/time_bar_cut.py#cut_time_bars"
  - id: F012
    severity: pass
    category: correctness
    summary: "Admission runs before any tick is read"
    location: "src/manta_trading/data/tick/time_bar_reads.py#TimeBarRead.admit"
---

# Review: code — slice 309

**Verdict:** CONCERNS
**Model:** claude-haiku-5-5
**Evidence:** 30 successful tool call(s) of 31 made; 221799 of 221799 diff characters reached the model

## Findings

### [CONCERN] Time-bar route orchestration duplicates the tick-bar routes

`_read` (129) and `_time_bars` (148) repeat `futures_tick_bars.py`'s `_read` (85) and `_tick_bars` (135): the same `tick_errors` wrap, `RowCeilingExceeded` → `over_ceiling`, scan-limit → 422, `ReadAbandoned` → bodiless 499, and the executor-built response. The two scan exceptions are also near-copies: `TimeBarScanExceeded` (time_bar_reads.py:42) and `TradeScanExceeded` (tick_bar_reads.py:106) both carry scanned/limit and the same 422 message shape. CLAUDE.md says not to duplicate logic. Pull the shared mapping into one helper, parameterized by remedy and scan exception, and consider a single scan-limit exception type.

### [CONCERN] Ledger-mismatch handling breaks the logging rule and diverges from its sibling

`_ledger_mismatch` logs with `_logger.error` (113), which drops the traceback, then raises a new `HTTPException` from the cause. The sibling `_uncuttable` (futures_tick_bars.py:120) handles the same kind of fault, stored rows disagreeing with the stored record, with `_logger.exception`. The Python rules require `logger.exception` at ERROR level when an exception is re-raised. The CLI branch in tick_get_cmds.py:202–211 is worse: it neither logs nor re-raises. It writes a plain-text line to stderr in text and `--json` modes alike, while `print_error` emits `{"error": ...}` (cli/output.py:33). It also bypasses `run_mapped`'s error path. Use `logger.exception` in the route, and route the CLI case through the same error shape.

### [CONCERN] Silent default in `TimeBarRead.read`

`read` calls `self.sources.get(window.session_date)` and returns 0 when the key is missing (136–138). `source_runs` (time_bar_source.py:108) also reads through `.get` and treats a miss as "not returned". A missing key means `admit` never ran, which is a programming error, not "no bars". Both current callers run `admit` first, so this is latent. If a future caller skips `admit`, the request returns 200 with no bars and no error. `_ledger_count` in the same class asserts the same invariant (162), but `assert` is stripped under `-O`. Raise on an unknown session, and keep `None` only for a known session that is deliberately not read.

### [CONCERN] Bounded bucket arithmetic has no test where it matters

`scan_params` finds the first bucket start at or after `from` and the last start before `until` with floor division. I checked it by hand (from=90 with width 60 gives 120; until=121 keeps the bucket at 120; until=120 excludes it), and it is right. Nothing tests it. `scan_params` has no direct test. The only time-bar tests that pass `from`/`until` use 1 s bars with minute-aligned instants (test_time_bar_cut.py `test_one_second_bars_inside_a_window`, test_futures_time_bars.py `test_one_second_bars_inside_from_until`). At those widths the rounding never fires. Add parametrized unit tests for unaligned `from`/`until` at 5m, 90m and 1d, plus one case where a bar starts before `from` and covers it.

### [CONCERN] Dead production modules kept after the parity command was removed

After `tick_parity` and `parity_plan` were deleted, nothing under `src` imports `bar_parity_report`, and nothing outside `bar_parity.py` calls `classify_minutes`. `read_tick_minute_bars` (bar_parity.py:33) is called only from `test_bar_parity_sql.py`. The slice's task file says to keep them for future work. That is a plan decision, but it leaves production code with no caller. Either delete them (git keeps history) or name the slice that will call them. `NS_PER_MINUTE` in bar_reads.py:38 is also now used only by tests.

### [CONCERN] Granularity is an untyped string at the API boundary

`GranularityParam` is `Annotated[str, Query(...)]`, validated by hand in `bar_size` (101). The previous contract let FastAPI validate against an enum, so the OpenAPI schema listed the accepted sizes. That schema is gone: a client sees a bare string, and the grammar lives only in the description. This is external input, which the Python rules say to validate with Pydantic. Give the parameter a schema-level constraint, such as a `pattern` on `Query`, so the published schema describes the grammar. The 24h ceiling can stay in `parse_bar_size`.

### [CONCERN] Load test is not CI-gated

The Python rules say CI must gate load tests for slices on the network or concurrency path. This file is gated by `MT_RUN_LOAD_TESTS=1`, and its header says the gate is manual until issue #32 and slice 907 land. The gap is repo-wide and tracked, so it does not block this slice, but the slice does not meet the rule. Record it as an accepted exception in the slice, or wire the load tier into CI.

### [NOTE] pydantic is imported directly but not declared

`models/futures.py` now uses `Field(exclude_if=...)` and `serialize_by_alias`, which need pydantic 2.11 or later. `pyproject.toml` declares only `pydantic-settings>=2.9.1`. The lock resolves 2.12.5, so it works today, but a fresh resolve could pick an older pydantic. Declare pydantic with a 2.11 floor.

### [NOTE] The bars response changes shape and repeats fields

`source` (a string) is removed, and `sources` (a list) plus a required `applied` are added. Clients reading `source` break. `granularity`, `start` and `end` now appear both at the top level and inside `applied`. Note the removal in the release notes, and decide whether the top-level copies stay.

### [NOTE] Ledger totals are fetched again for every window

`admit` fetches the ledger for the whole range in `choose_sources`, then again in `trades_in_windows`. `_ledger_count` then runs one more ledger query for each tick-cut session. Each query is small and indexed, but the totals are already in hand. Pass them into `read`.

### [PASS] Time-bar SQL binds every value

The `_CUT` statement composes only identifiers and fixed fragments (`WINDOW_COLUMNS`, `WINDOW_WHERE`, the bucket expression). Every value (anchor, width, open, close, instrument, session date) is a bound parameter from `scan_params`. `_STORED` and `_ROLLED` in bar_reads.py follow the same pattern. No string-built SQL.

### [PASS] Admission runs before any tick is read

`read_series` (futures_read.py:72–73) calls `admit` inside the same snapshot, after the plan and before the first window. An over-limit request therefore reads no tick. `test_an_over_limit_cut_is_refused_before_any_tick_is_read` checks this with a recorder.

### Run Digest

- Response length: 10289 chars
- Response is newline-free: no
- Tool calls made: 31
- Tool calls failed: 1
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: xhigh
- Turns: 32
- Tokens — prompt / cached / cache write / completion / reasoning: 2936975 / 2506095 / 430858 / 90224 / not computed
- Duration: 380.0 s
- Cost: $0.84 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 12
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 12
- Finding-shaped matches — surviving validation: 12
