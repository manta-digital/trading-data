---
docType: review
layer: project
reviewType: slice
slice: tick-bars-and-compact-tick-serving
conventionsSource: AGENTS.md
projectRules: []
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md
aiModel: claude-sonnet-5-5
aiProfile: sdk
status: complete
dateCreated: 20261108
dateUpdated: 20261108
reviewedSha: d6393561654627d8d400d8a2e7b82d676c644658
revision_number: 1
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 5
effort: high
turns: 6
promptTokens: 406766
cachedTokens: 300503
cacheWriteTokens: 106253
completionTokens: 7663
durationSeconds: 71.7
costUsd: 0.532484
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Rollup rule matches the architecture and the ADR"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D1 — The rollup module: one trade in, bars out"
  - id: F002
    severity: pass
    category: scope-and-boundaries
    summary: "Admission design answers the slice plan's open question"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D5 — Admission: trades scanned, known before reading"
  - id: F003
    severity: pass
    category: architectural-alignment
    summary: "Columnar encoding matches the architecture, with justified choices"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D6 — Columnar raw ticks"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Concurrency and abandoned-request failure modes of the new scan are not enumerated"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#Special Considerations"
  - id: F005
    severity: concern
    category: verification
    summary: "The measurement that validates the default trade limit is not at the limit, and the documents disagree"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:293"
  - id: F006
    severity: note
    category: scope
    summary: "The rename of the time-bar granularity names (D9) goes beyond the slice plan entry"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:278-281"
  - id: F007
    severity: pass
    category: integration
    summary: "Integration points and dependencies are consistent with the parent plan"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#Dependencies"
---

# Review: slice — slice 308

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Rollup rule matches the architecture and the ADR

The design takes each rule from the architecture's "Tick bars and raw ticks" paragraph:
- one incremental module, one trade in and bars out;
- one tick is one trade record;
- the count restarts at each session open;
- history runs the module in a loop, with no SQL second implementation (rejected explicitly in D1);
- a bar carries OHLC, volume, trade count, buy/sell volume by aggressor side, bid/ask at close, and its identity (session date, index).

D2 refines the "last bar is kept and marked partial" rule: a full last bar is kept and is not partial. The design flags this as a clarification and says `reference.md` will state it. The `tick_bars.py` isolation test (standard library plus `dbn_values.py` only) keeps the dependency direction right for 280 and trading-engine to reuse the module.

### [PASS] Admission design answers the slice plan's open question

The slice plan asks how tick bars are admitted under the row ceiling when the bar count is not a function of the window. D5 splits the problem:
- Scan work is known exactly from the ingest ledger before any read, and is checked against the new `MT_API_MAX_TICK_BAR_TRADES` setting.
- Output size is bounded by the existing bar ceiling while cutting.

It also rejects two alternatives, with reasons: counting scanned trades against the 75,000-row ceiling, and a pre-count of bars as `Σ ceil(nᵢ/N)`. The `admit` hook is opt-in, so `/ticks` and `/bars` are unaffected. Ledger-versus-rows disagreement is handled by the bar ceiling and statement timeout in Integration Points.

### [PASS] Columnar encoding matches the architecture, with justified choices

The encoding matches the architecture: integer nanosecond deltas, integer prices, symbol and session once per block, and the stream key on every tick. The design handles precision risks explicitly:
- `ts_base` is a decimal string, because epoch nanoseconds exceed 2⁵³.
- Prices use a stated `price_scale`.
- `price_scale` and the delta ranges are safe for ES and GC.

Replacing the old shape rather than versioning it is consistent with the architecture's Current State, which says no consumer calls the futures routes yet. The CHANGELOG lists it as a breaking change.

### [CONCERN] Concurrency and abandoned-request failure modes of the new scan are not enumerated

The tick-bar route is a new long-running I/O and CPU path. A request can scan up to 2,000,000 trades, holds a REPEATABLE READ snapshot connection while it does so, and pushes batches through `run_in_executor`.

The design covers:
- the statement timeout (504);
- tick database unavailable (503);
- a cutter input error (500);
- the loop-starvation check in D10.

It does not say what happens in three cases:
1. **The client disconnects mid-request.** An executor call cannot be cancelled. The window loop may keep scanning and cutting to the limit, holding the snapshot connection, with nothing stating that the loop stops or that the cursor and connection are released.
2. **Several limit-sized requests arrive together.** Architecture point 9 accepts that cutting does not scale. The design states no cap, pool-exhaustion mapping (503 versus a hang), or executor bound for the case where a few concurrent requests each hold a connection and a worker thread for seconds.
3. **A named cursor is stopped early.** D3 stops a window's scan at the first trade that opens a bar at or after `until`. Cursor close and snapshot release on that path, and on error or cancellation, are implicit.

Add one short decision covering these three cases. For example: check for disconnect between batches and stop, rely on the existing pool's acquire timeout mapped to 503, and use `finally`-style cursor release. Add a matching integration test.

### [CONCERN] The measurement that validates the default trade limit is not at the limit, and the documents disagree

D10 and D5 say the Phase 7 check runs "one ES request at the trade limit (about five 2024 sessions)". D5 also says the 2,000,000 default is lowered if that measurement misses the bound. The walkthrough step (lines 390–395) requests `start=2024-12-02&end=2024-12-06`, which is four sessions and "just under the limit". At the stated median of about 393k trades per session, that is about 1.6M trades.

So the check as written does not exercise the limit the default is justified by. A pass would leave the 10 s bound unverified between about 1.6M and 2M trades. Success criterion 13 then records a measurement that does not match D10.

Make D10 and the walkthrough agree. Either choose a range whose ledger sum is at or just under the configured limit, or state that the limit is validated by extrapolation. Preferably pick the range from the ledger totals, so the request really is at the limit.

### [NOTE] The rename of the time-bar granularity names (D9) goes beyond the slice plan entry

The slice plan entry for 308 does not mention renaming `TICK_BAR_GRANULARITIES` / `TickBarGranularity` (issue #72). The rename is mechanical, it is step 1 of the development approach, and the design's rationale (after this slice, "tick bar" means only an N-trade bar) is reasonable. It does change a published OpenAPI component name.

If the slice runs long, this is the first scope item to cut. It can ship as its own `refactor:` or `fix:` commit under the project's small-change path with no loss to the slice's deliverables.

### [PASS] Integration points and dependencies are consistent with the parent plan

- The 306 prerequisite (family-parameterised plans, `verdicts_for`, provider-only bars) matches the slice plan's `Dependencies: [306]`.
- The `source` field is correctly omitted from tick bars, and the reason is given. A tick bar has one possible source, and the architecture's "never splice tiers silently" principle concerns time bars.
- The slice does not add a table or migration.
- It provides 280 with the cutter, the block shape and the stream key.
- It consumes the 302 ledger and `tick_trade`.

The renaming of the private `_stream` to a public `stream_rows`, named in Interfaces Required, removes what would otherwise be a hidden dependency on a private symbol.

### Run Digest

- Response length: 7512 chars
- Response is newline-free: no
- Tool calls made: 5
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 6
- Tokens — prompt / cached / cache write / completion / reasoning: 406766 / 300503 / 106253 / 7663 / not computed
- Duration: 71.7 s
- Cost: $0.53 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
