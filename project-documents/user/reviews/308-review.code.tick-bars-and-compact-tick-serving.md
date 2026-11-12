---
docType: review
layer: project
reviewType: code
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: [python.md, sql.md, testing.md]
project: trading-data
verdict: CONCERNS
verdictSource: derived
sourceDocument: project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md
aiModel: deepseek/deepseek-v4.1-flash
aiProfile: openrouter
status: complete
dateCreated: 20261112
dateUpdated: 20261112
reviewedSha: 013a6674a37625a84eb14c1c84f069fcd1f58d74
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 36
diffTruncated: false
turns: 20
promptTokens: 2347621
cachedTokens: 1952000
completionTokens: 96955
reasoningTokens: 92052
durationSeconds: 629.0
costUsd: 0.113533
costBasis: alias_pricing
runId: run-20261112-p6-3080c8dd
squadronVersion: 0.24.26
categoriesNormalized: true
findings:
  - id: F001
    severity: concern
    category: documentation-drift
    summary: "The remedy vocabulary grew a fourth entry; the reference still enumerates three"
    location: "docs/api/reference.md"
  - id: F002
    severity: concern
    category: consistency-rot
    summary: "The D9 latency/starvation measurement is implemented twice, with divergent bounds"
    location: "scripts/measure_tick_bars.py#judge"
  - id: F003
    severity: concern
    category: type-contract-debt
    summary: "`read_tick_bars` requires an unclipped window but never checks it, and a clipped window silently renumbers every bar"
    location: "src/manta_trading/data/tick/tick_bar_reads.py#read_tick_bars"
  - id: F004
    severity: concern
    category: other
    summary: "`scripts/measure_tick_bars.py` accepts numeric arguments that produce a traceback instead of a usage error"
    location: "scripts/measure_tick_bars.py#_args"
  - id: F005
    severity: concern
    category: consistency-rot
    summary: "The script test mutates `sys.path` where the established pattern loads the script by path"
    location: "test/unit/test_measure_tick_bars.py#measure"
  - id: F006
    severity: note
    category: other
    summary: "`judge` computes an overlap signal it never judges on"
    location: "scripts/measure_tick_bars.py#measure"
  - id: F007
    severity: note
    category: documentation-drift
    summary: "The reference promises a server log line for the `499` that the route deliberately does not write"
    location: "docs/api/reference.md"
  - id: F008
    severity: pass
    category: other
    summary: "The cutter is tested against an independent oracle on real data, and admission is tested before the read"
    location: "test/unit/data/tick/test_tick_bars.py"
---

# Review: code — slice 308

**Verdict:** CONCERNS
**Model:** deepseek/deepseek-v4.1-flash
**Evidence:** 36 successful tool call(s) of 36 made; 162981 of 162981 diff characters reached the model

## Findings

### [CONCERN] The remedy vocabulary grew a fourth entry; the reference still enumerates three

`admission.py` now defines `NARROW_TICK_BARS = "raise ticks_per_bar, or narrow start/end or from/until"` (`src/manta_trading/api_server/admission.py:39`, exported in `__all__`) alongside the three existing remedies. But `docs/api/reference.md` §2.7 still introduces the set as "one of three fixed remedies, defined once in `admission.py`" and lists only `narrow the filter`, `narrow start/end` and `narrow start/end or from/until`. A client that discovers the tick-bar `422` (whose text ends in the new remedy) is told by §2.7 that this string is not part of the vocabulary. §6.1 and §6.9 do quote the new remedy inline, so the omission is confined to the summary that claims to be the single enumeration. Add the fourth bullet and change "three" to "four".

### [CONCERN] The D9 latency/starvation measurement is implemented twice, with divergent bounds

`scripts/measure_tick_bars.py` contains a full idle-probe → concurrent-request → concurrent-probe harness: `measure()` (seven idle probes, `PROBE_DELAY_S = 0.2`, up to `PROBES = 7` busy probes), `judge()` (`BOUND_S = 10.0`, `STARVATION_MARGIN_S = 0.25`) and `Timing`. `test/load/test_308_tick_bars_nfr.py` re-implements the same loop and the same judgement independently, with its own `PROBES`, its own `0.2` sleep literal, its own `STARVATION_MARGIN_S = 0.25` and a different request bound (`TICK_BARS_BOUND_S = 5.0`). The script's own docstring points at the load test as the source of the 0.25 s evidence, so the two are meant to be the same check. Two copies of a threshold that must agree is precisely the failure mode the project's "define comparison values once" rule targets: lowering one margin leaves the other silently asserting the old one. Either have the load test import `measure`/`judge` from the script, or move the harness into one test-support module both consume. The two different *request* bounds are defensible (production limit vs. the proxy session) and should be named as such where they are defined.

### [CONCERN] `read_tick_bars` requires an unclipped window but never checks it, and a clipped window silently renumbers every bar

The reader's contract is stated only in prose ("`window` must be unclipped; one that misses the bounds is not read"). Its `_WindowCut` derives the bar boundary from `self.position % n`, and `TickBarCutter` starts `index` at 0 for the first trade it sees, so the whole `index`/`partial`/open-time guarantee in `docs/api/reference.md` §6.1 holds only if the window begins at the session open. Today that holds because the tick-bar route calls `read_series(conn, plan_for, read, max_rows, admit=admit)` with no `bounds` (`routes/futures_tick_bars.py:_read`), so `read_series`'s `clip(plan.segments, bounds)` passes every window through untouched. Nothing enforces it: `read_series` takes `bounds` and `clip`s with it, and `read_tick_bars` only tests *overlap* via `overlapping([window], bounds)`, so a future caller that does pass `bounds` would get windows pre-clipped at `from`, the cutter would number bars from that instant, and the response would be wrong while every total still balanced. The `row_cells`-style "one place states the rule" idiom used elsewhere would be a cheap guard here — reject (or assert on) a window whose `open_ns` is not the session open, rather than relying on the caller's restraint. A docstring is not a contract the code enforces.

### [CONCERN] `scripts/measure_tick_bars.py` accepts numeric arguments that produce a traceback instead of a usage error

`_args` declares `--ticks-per-bar` and `--limit` as bare `type=int` with no lower bound. `--limit 0` makes `pick_range`'s band `low = 0.0` return the first row immediately, and `report()` then evaluates `ledger_sum / args.limit` — a `ZeroDivisionError` raised *after* the ledger read and the production request, from inside `asyncio.run(_run(args))`, so the operator gets a traceback and no note rather than the "no run lands in the band" message `pick_range` was written to give. `--ticks-per-bar 0` fails similarly in `check_totals`'s `math.ceil(row.records / ticks_per_bar)` (or is bounced by the server as a `422`, surfaced as `SystemExit` from `measure`). This script is meant to be run once, by hand, against production (issue #75), which is exactly when a crash after the expensive work is most costly. Validate both up front (`type=int` plus an explicit `>= 1` check, matching `TicksPerBarParam`'s `ge=1` and `Settings.api_max_tick_bar_trades`' `gt=0`), so a typo costs a usage message, not a rerun.

### [CONCERN] The script test mutates `sys.path` where the established pattern loads the script by path

The `measure` fixture does `sys.path.insert(0, str(SCRIPTS))` at module scope and then `importlib.import_module("measure_tick_bars")`. `test/unit/api_server/test_openapi_artifact.py` loads the sibling script the other way — `importlib.util.spec_from_file_location` — with a comment that says exactly why: "`scripts/` is not an installed package, and making it one to satisfy a test would change what this project ships." The module-scoped fixture leaves `scripts/` on `sys.path` for the rest of the session, so a later `import x` in an unrelated test can now resolve against `scripts/`. Follow the existing pattern, or better, extract the two tested functions (`pick_range`, `check_totals`, `judge`) into an importable module and let the script be a thin `main`.

### [NOTE] `judge` computes an overlap signal it never judges on

`Timing.overlapped` is set in `measure()` as `not request.done()` *after* the probe loop and is only echoed into the note by `report()`. `judge()` never reads it, and its two real conditions partially contradict it: it fails a run with no busy probes at all ("the request finished before any concurrent probe") even when `overlapped` is true, and passes a run whose request finished a millisecond before the last probe. Either make `overlapped` a judged condition (a request that never overlapped proves nothing about starvation) or drop the field, so the note cannot print a "still running" claim beside a `FAIL`. Separately, `report()`'s `(request still running after the probes: …)` reads as a verdict on a check that does not exist.

### [NOTE] The reference promises a server log line for the `499` that the route deliberately does not write

§6.9 ends with "A client that disconnects mid-read gets nothing (the server logs status `499`)." The route does the opposite on purpose: `_tick_bars` catches `ReadAbandoned` and returns `Response(status_code=CLIENT_CLOSED_REQUEST)` with a comment stating this is "not an error to log (D10)", and no `_logger` call exists on that path (the only logging in the module is `_uncuttable`'s). Whatever the ASGI access log records, the application writes no line tying the `499` to the abandoned session. Reword to "the response carries status `499`; it is not logged as an error" so the sentence matches the code and so an operator does not go looking for an ERROR record that will not be there.

### [PASS] The cutter is tested against an independent oracle on real data, and admission is tested before the read

The testing on this slice follows the project's test-with standard rather than test-after, and it tests behaviour rather than implementation: `test/unit/data/tick/tick_bar_oracle.py` re-derives the rule "the obvious way" (sort by key, group by session, slice by N, summarise) from the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, asserting the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) so a changed fixture fails loudly; `test_tick_bars_isolation.py` runs a fresh `-I` interpreter and asserts neither `psycopg` nor `tick.constants` loads, which is the criterion-8 boundary made mechanical; and `test_over_the_trade_limit_is_422_before_any_tick_is_read` spies on `read_tick_bars` and asserts `reads == []` on the refusal and `len(reads) == 1` once the limit is raised, which is the admission-before-reading guarantee asserted where it can actually break. The reader is additionally checked for early stop by counting `fetchmany` calls and for connection health via `_all_available`, so the abandoned-read path is shown not to leak pool connections.

### Run Digest

- Response length: 9426 chars
- Response is newline-free: no
- Tool calls made: 36
- Tool calls failed: 0
- Stop reason: stop
- Output budget: 384000 tokens
- System prompt: custom
- Settings sources: n/a (non-SDK)
- Reasoning characters: 343318
- Effort: backend default
- Turns: 20
- Tokens — prompt / cached / cache write / completion / reasoning: 2347621 / 1952000 / not computed / 96955 / 92052
- Duration: 629.0 s
- Cost: $0.11 (alias_pricing)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 8
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 8
- Finding-shaped matches — surviving validation: 8

### Raw Response

## Summary

The slice is well-built: the cutter is a single standard-library implementation pinned by an isolation test and an independent oracle, admission happens on the ledger before any tick is read, and the CPU-heavy pivots are pushed off the event loop. I found no correctness defect in the request path. The concerns are documentation drift in the remedy vocabulary, a measurement harness implemented twice with divergent constants, one unenforced reader contract that would silently corrupt bar indices if it were ever violated, and two script/test hygiene issues.

## Findings

### [CONCERN] The remedy vocabulary grew a fourth entry; the reference still enumerates three
category: documentation-drift
location: docs/api/reference.md
`admission.py` now defines `NARROW_TICK_BARS = "raise ticks_per_bar, or narrow start/end or from/until"` (`src/manta_trading/api_server/admission.py:39`, exported in `__all__`) alongside the three existing remedies. But `docs/api/reference.md` §2.7 still introduces the set as "one of three fixed remedies, defined once in `admission.py`" and lists only `narrow the filter`, `narrow start/end` and `narrow start/end or from/until`. A client that discovers the tick-bar `422` (whose text ends in the new remedy) is told by §2.7 that this string is not part of the vocabulary. §6.1 and §6.9 do quote the new remedy inline, so the omission is confined to the summary that claims to be the single enumeration. Add the fourth bullet and change "three" to "four".

### [CONCERN] The D9 latency/starvation measurement is implemented twice, with divergent bounds
category: consistency-rot
location: scripts/measure_tick_bars.py#judge
`scripts/measure_tick_bars.py` contains a full idle-probe → concurrent-request → concurrent-probe harness: `measure()` (seven idle probes, `PROBE_DELAY_S = 0.2`, up to `PROBES = 7` busy probes), `judge()` (`BOUND_S = 10.0`, `STARVATION_MARGIN_S = 0.25`) and `Timing`. `test/load/test_308_tick_bars_nfr.py` re-implements the same loop and the same judgement independently, with its own `PROBES`, its own `0.2` sleep literal, its own `STARVATION_MARGIN_S = 0.25` and a different request bound (`TICK_BARS_BOUND_S = 5.0`). The script's own docstring points at the load test as the source of the 0.25 s evidence, so the two are meant to be the same check. Two copies of a threshold that must agree is precisely the failure mode the project's "define comparison values once" rule targets: lowering one margin leaves the other silently asserting the old one. Either have the load test import `measure`/`judge` from the script, or move the harness into one test-support module both consume. The two different *request* bounds are defensible (production limit vs. the proxy session) and should be named as such where they are defined.

### [CONCERN] `read_tick_bars` requires an unclipped window but never checks it, and a clipped window silently renumbers every bar
category: type-contract-debt
location: src/manta_trading/data/tick/tick_bar_reads.py#read_tick_bars
The reader's contract is stated only in prose ("`window` must be unclipped; one that misses the bounds is not read"). Its `_WindowCut` derives the bar boundary from `self.position % n`, and `TickBarCutter` starts `index` at 0 for the first trade it sees, so the whole `index`/`partial`/open-time guarantee in `docs/api/reference.md` §6.1 holds only if the window begins at the session open. Today that holds because the tick-bar route calls `read_series(conn, plan_for, read, max_rows, admit=admit)` with no `bounds` (`routes/futures_tick_bars.py:_read`), so `read_series`'s `clip(plan.segments, bounds)` passes every window through untouched. Nothing enforces it: `read_series` takes `bounds` and `clip`s with it, and `read_tick_bars` only tests *overlap* via `overlapping([window], bounds)`, so a future caller that does pass `bounds` would get windows pre-clipped at `from`, the cutter would number bars from that instant, and the response would be wrong while every total still balanced. The `row_cells`-style "one place states the rule" idiom used elsewhere would be a cheap guard here — reject (or assert on) a window whose `open_ns` is not the session open, rather than relying on the caller's restraint. A docstring is not a contract the code enforces.

### [CONCERN] `scripts/measure_tick_bars.py` accepts numeric arguments that produce a traceback instead of a usage error
category: other
location: scripts/measure_tick_bars.py#_args
`_args` declares `--ticks-per-bar` and `--limit` as bare `type=int` with no lower bound. `--limit 0` makes `pick_range`'s band `low = 0.0` return the first row immediately, and `report()` then evaluates `ledger_sum / args.limit` — a `ZeroDivisionError` raised *after* the ledger read and the production request, from inside `asyncio.run(_run(args))`, so the operator gets a traceback and no note rather than the "no run lands in the band" message `pick_range` was written to give. `--ticks-per-bar 0` fails similarly in `check_totals`'s `math.ceil(row.records / ticks_per_bar)` (or is bounced by the server as a `422`, surfaced as `SystemExit` from `measure`). This script is meant to be run once, by hand, against production (issue #75), which is exactly when a crash after the expensive work is most costly. Validate both up front (`type=int` plus an explicit `>= 1` check, matching `TicksPerBarParam`'s `ge=1` and `Settings.api_max_tick_bar_trades`' `gt=0`), so a typo costs a usage message, not a rerun.

### [CONCERN] The script test mutates `sys.path` where the established pattern loads the script by path
category: consistency-rot
location: test/unit/test_measure_tick_bars.py#measure
The `measure` fixture does `sys.path.insert(0, str(SCRIPTS))` at module scope and then `importlib.import_module("measure_tick_bars")`. `test/unit/api_server/test_openapi_artifact.py` loads the sibling script the other way — `importlib.util.spec_from_file_location` — with a comment that says exactly why: "`scripts/` is not an installed package, and making it one to satisfy a test would change what this project ships." The module-scoped fixture leaves `scripts/` on `sys.path` for the rest of the session, so a later `import x` in an unrelated test can now resolve against `scripts/`. Follow the existing pattern, or better, extract the two tested functions (`pick_range`, `check_totals`, `judge`) into an importable module and let the script be a thin `main`.

### [NOTE] `judge` computes an overlap signal it never judges on
category: other
location: scripts/measure_tick_bars.py#measure
`Timing.overlapped` is set in `measure()` as `not request.done()` *after* the probe loop and is only echoed into the note by `report()`. `judge()` never reads it, and its two real conditions partially contradict it: it fails a run with no busy probes at all ("the request finished before any concurrent probe") even when `overlapped` is true, and passes a run whose request finished a millisecond before the last probe. Either make `overlapped` a judged condition (a request that never overlapped proves nothing about starvation) or drop the field, so the note cannot print a "still running" claim beside a `FAIL`. Separately, `report()`'s `(request still running after the probes: …)` reads as a verdict on a check that does not exist.

### [NOTE] The reference promises a server log line for the `499` that the route deliberately does not write
category: documentation-drift
location: docs/api/reference.md
§6.9 ends with "A client that disconnects mid-read gets nothing (the server logs status `499`)." The route does the opposite on purpose: `_tick_bars` catches `ReadAbandoned` and returns `Response(status_code=CLIENT_CLOSED_REQUEST)` with a comment stating this is "not an error to log (D10)", and no `_logger` call exists on that path (the only logging in the module is `_uncuttable`'s). Whatever the ASGI access log records, the application writes no line tying the `499` to the abandoned session. Reword to "the response carries status `499`; it is not logged as an error" so the sentence matches the code and so an operator does not go looking for an ERROR record that will not be there.

### [PASS] The cutter is tested against an independent oracle on real data, and admission is tested before the read
category: other
location: test/unit/data/tick/test_tick_bars.py
The testing on this slice follows the project's test-with standard rather than test-after, and it tests behaviour rather than implementation: `test/unit/data/tick/tick_bar_oracle.py` re-derives the rule "the obvious way" (sort by key, group by session, slice by N, summarise) from the committed 2024-12-03 `tbbo` cut and its derived `trades` twin, asserting the fixture's premise (`SESSION_COUNTS`, `BEFORE_CLOSE`/`AFTER_OPEN`) so a changed fixture fails loudly; `test_tick_bars_isolation.py` runs a fresh `-I` interpreter and asserts neither `psycopg` nor `tick.constants` loads, which is the criterion-8 boundary made mechanical; and `test_over_the_trade_limit_is_422_before_any_tick_is_read` spies on `read_tick_bars` and asserts `reads == []` on the refusal and `len(reads) == 1` once the limit is raised, which is the admission-before-reading guarantee asserted where it can actually break. The reader is additionally checked for early stop by counting `fetchmany` calls and for connection health via `_all_available`, so the abandoned-read path is shown not to leak pool connections.
