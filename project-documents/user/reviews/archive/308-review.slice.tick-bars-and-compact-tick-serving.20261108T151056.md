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
reviewedSha: d076bd745d799b45f03c43f3e8a8f4ec3f8dfe2b
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 13
effort: high
turns: 14
promptTokens: 852296
cachedTokens: 748277
cacheWriteTokens: 103999
completionTokens: 7120
durationSeconds: 69.3
costUsd: 0.562834
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: concern
    category: dependency-direction
    summary: "\"Standard library only\" module cannot hold with the imports it names"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D1 — The rollup module: one trade in, bars out"
  - id: F002
    severity: concern
    category: performance
    summary: "CPU-bound cutting inside async request handling is not addressed"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#Special Considerations"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Cutter `ValueError` has no defined route-level handling"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D1 / D4"
  - id: F004
    severity: concern
    category: hidden-dependency
    summary: "`tick_reads._stream` is reused across modules as a private symbol"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#Dependencies — Interfaces Required"
  - id: F005
    severity: note
    category: alignment
    summary: "\"Partial\" semantics tightened relative to the architecture's wording"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D2 — What a tick bar is"
  - id: F006
    severity: note
    category: alignment
    summary: "Admission design matches the architecture's open question"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D5 — Admission: trades scanned, known before reading"
  - id: F007
    severity: pass
    category: alignment
    summary: "Scope, layering and integration points match the architecture"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md"
---

# Review: slice — slice 308

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [CONCERN] "Standard library only" module cannot hold with the imports it names

D1, Technical Scope and success criterion 9 say `tick_bars.py` imports only the standard library. The stated reason is that trading-engine or 280's live process can run it without the database stack. But D1 needs `UNDEF_INT64` and D2 puts `TICK_BAR_SIDE_BUY/SELL` in `data/tick/constants.py`. That module imports `manta_trading.constants` and `manta_trading.market.schema.seed_cme_calendar` / `seed_cme_metals_calendar`. `UNDEF_INT64` is defined there, and `tick_format` imports it from there. Importing from it pulls those packages in. The "Interfaces Required" list also names `tick_format.UNDEF_INT64`.

Success criterion 9 would be violated, or satisfied only by duplicating the constants. That would break the one-definition rule in CLAUDE.md. Either give the cutter's constants (sentinel, side codes) a dependency-light home that both `tick_bars.py` and `constants.py` import, or drop the standalone-import claim. Add a test that imports `tick_bars` in isolation. The architecture's purity requirement is "one incremental module" that 280 and a per-client engine reuse, so this is the property the design has to deliver.

### [CONCERN] CPU-bound cutting inside async request handling is not addressed

D3 and D5 push up to 2,000,000 trades through a pure-Python cutter inside the route's `read_series` / `on_batch` flow. The Python rules (`python.md`, "Async & Performance") require synchronous work on an async path to finish in under 1 ms or run in an executor, and reviewers must verify this. The design says only "the loop stays simple unless the measurement says otherwise".

It does not say how much synchronous work runs between awaits. Each pushed fetch batch (`TICK_GET_FETCH_ROWS` rows) blocks the event loop for the whole batch. It also does not say whether the cutting runs on the loop or off it. The columnar pivot (D6) over up to 75,000 rows has the same exposure.

State the execution model: per-batch push on the loop with a bounded batch size, or cutting in an executor. Also state the shared-state review for whichever model is chosen, since the cutter is per-window and local. The only verification is the Phase 7 single-request timing, which will not show event-loop starvation under concurrent requests. Add a concurrency check, for example one tick-bar request at the limit with a concurrent `/health` call.

### [CONCERN] Cutter `ValueError` has no defined route-level handling

D4 enumerates 404, 422, 503 and 504 through `tick_errors`. D1 makes out-of-order keys, a backwards session and an undefined price raise `ValueError`, which the design calls a "caller bug". In the history path the caller is the route's own reader. A stored row that violates the contract, such as a ledger/row disagreement or a sentinel price that slipped past the `NOT NULL` column, would surface as an unmapped exception. The statement is also a long-running stream with the scan under way. The "Consumes" section covers ledger disagreement only as "bar ceiling and statement timeout still bound the read".

Specify the outcome: log at ERROR with the window and both keys, then map to a 500 with a stable message. Do not swallow it, and do not return a partial bar list. Add one integration test that injects a violation.

### [CONCERN] `tick_reads._stream` is reused across modules as a private symbol

The new `tick_bar_reads.py` depends on `tick_reads._stream` and `TICK_GET_FETCH_ROWS`. Reaching into an underscore-named function of a sibling module couples the two readers through an undocumented contract. The design does not say whether it is renamed, promoted to a public helper, or left as is. Name the choice. The lightest option is to rename it to a public name in the same commit as step 3 and update the existing caller. That keeps the program-to-contracts rule without a new abstraction.

### [NOTE] "Partial" semantics tightened relative to the architecture's wording

The architecture and slice plan say "the last bar of a session is kept and marked partial". D2 marks it partial only when it holds fewer than N trades. A session whose trade count is a multiple of N has no partial bar. This is the sensible reading, since a full bar is not partial, and success criterion 2 and the walkthrough state it. Record it as a clarification of the ADR wording, not a deviation. `reference.md` should say so.

### [NOTE] Admission design matches the architecture's open question

The slice plan asks how tick bars are admitted under the row ceiling when the bar count is not a function of the window. D5 answers by admitting on ledger-exact trades scanned and bounding output in the collector. The two rejected alternatives are argued concretely. The trade limit is conservative for narrow `from`/`until` requests, because the whole window is counted. This is stated and accepted. The default limit is tied to a Phase 7 measurement with a lowering rule.

### [PASS] Scope, layering and integration points match the architecture

All of these match the architecture and the slice plan entry.
- **Cutting rule:** one incremental module, no SQL second implementation, count restart at session open, and the bar fields all follow the ADR.
- **Compact encoding:** the columnar form carries the stream key on every tick, which 280 reuses.
- **Time bars:** none are derived from ticks, so the "never splice tiers" principle holds.
- **Dependencies:** 306 is a stated prerequisite, and nothing is bought or migrated.
- **Proportionality:** the rename (issue #72), the no-CLI decision (D8) and the row-record decision (D7) each remove scope rather than add it. No unused configuration is introduced beyond the one setting that admission needs.

### Run Digest

- Response length: 7271 chars
- Response is newline-free: no
- Tool calls made: 13
- Tool calls failed: 1
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 14
- Tokens — prompt / cached / cache write / completion / reasoning: 852296 / 748277 / 103999 / 7120 / not computed
- Duration: 69.3 s
- Cost: $0.56 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 7
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 7
- Finding-shaped matches — surviving validation: 7
