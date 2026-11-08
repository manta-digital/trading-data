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
reviewedSha: 7832b89c2d25933dcee4b10df879dbdfd8e1a6bb
revision_number: 2
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 6
effort: high
turns: 7
promptTokens: 510105
cachedTokens: 401350
cacheWriteTokens: 108743
completionTokens: 8131
durationSeconds: 77.7
costUsd: 0.557211
costBasis: provider
runId: run-20261108-slices-plan-d03f6d49
squadronVersion: 0.24.24
findings:
  - id: F001
    severity: pass
    category: architectural-alignment
    summary: "Rollup design matches the architecture's tick-bar decision"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D1 — The rollup module: one trade in, bars out"
  - id: F002
    severity: concern
    category: architectural-alignment
    summary: "Partial-bar rule departs from the architecture's wording without amending the architecture"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:184"
  - id: F003
    severity: concern
    category: scope
    summary: "D9 rename is scope the slice plan does not assign, and it carries a breaking OpenAPI change"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:278-281"
  - id: F004
    severity: note
    category: performance
    summary: "Default trade limit ships before the 10 s bound has been measured"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md:221"
  - id: F005
    severity: pass
    category: error-handling
    summary: "Failure modes for the new read path are enumerated with handling"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#D11 — Abandoned, concurrent and early-stopped reads"
  - id: F006
    severity: pass
    category: integration
    summary: "Integration points and proportionality are otherwise acceptable"
    location: "project-documents/user/slices/308-slice.tick-bars-and-compact-tick-serving.md#Integration Points"
---

# Review: slice — slice 308

**Verdict:** CONCERNS
**Model:** claude-sonnet-5-5

## Findings

### [PASS] Rollup design matches the architecture's tick-bar decision

The design implements the architecture's "Tick bars and raw ticks" decision as written:
- **Cutter:** one incremental module takes one trade in and returns closed bars plus the current partial bar.
- **History:** it runs the module in a loop, with no SQL second implementation (D1 "Rejected").
- **Count and tick:** one tick is one record, and the count restarts at each session open.
- **Compact ticks:** the encoding has integer nanosecond deltas, integer prices, and symbol and session once per block, with the stream key on every tick (D6).

The module is standard-library only, with the `UNDEF_INT64` move and an isolation test. Dependencies point from the routes to the reader to the module, never the reverse. The architecture's point 9 (server-side cutting doesn't scale past a handful of clients) is explicitly accepted in D11 and in "Scaling".

### [CONCERN] Partial-bar rule departs from the architecture's wording without amending the architecture

The architecture and the slice plan entry both say "the last bar of a session is kept and marked partial". D2 changes this. A session's last bar is partial only if it holds fewer than N trades, and a full last bar is "kept and is not partial". D2 calls this a clarification. It changes the meaning of a field that trading-ui (issue #62), trading-engine and 280's live cutting all read.

D1 also describes `close_session()` as returning "the open bar, final, partial=True", and `current()` as returning a bar with `partial=True`. D2 gives a conditional rule. The two descriptions agree only if `close_session()` can never see a full bar, which D1 doesn't state.

Fix both points:
- Record the rule in the architecture's "Tick bars and raw ticks" paragraph (and the 308 slice plan entry), so 280's slice design starts from one definition.
- State in D1 that `close_session()` returns None when the last bar closed at the Nth trade.

### [CONCERN] D9 rename is scope the slice plan does not assign, and it carries a breaking OpenAPI change

The design says the slice plan entry does not name the `TICK_BAR_GRANULARITIES` → `FUTURES_BAR_GRANULARITIES` rename, and that it is "the first item cut". It also changes a published OpenAPI component name. That is unrelated to the rollup, the route, the columnar encoding or admission, and it adds a breaking change to a Medium-risk slice.

Cut D9 from this slice and ship it now as its own `refactor:` commit under #72. That is the path the design already names as the fallback. The slice then needs no CHANGELOG line for the component, no grep-empty criterion (criterion 8) and no step 1. The remaining slice work doesn't depend on the new names.

### [NOTE] Default trade limit ships before the 10 s bound has been measured

The architecture states no numeric NFR for this path. It does require gzip at level 1, encoded off the event loop, and the slice restates both. The 10 s bound is borrowed from 306 D10 and is restated as a target in D10. The 2,000,000-trade default is chosen at design time, and the first measurement is a Phase 7 production run after release. The design handles a miss explicitly: lower the default and record the measurement. A later tightening could still break clients that already rely on the limit.

If the Phase 6 oracle tests add a timing run of the pure cutter over the real fixture's trades, replayed to a few hundred thousand pushes, the Python half of the budget is known before release. This is optional. The existing plan is adequate.

### [PASS] Failure modes for the new read path are enumerated with handling

Each failure on the new I/O path has a stated strategy:
- **Client disconnect:** `stop` is checked before each fetch, so no further batch is pushed.
- **Early stop, cutter error, cancellation:** cursor and transaction context managers close the cursor and return the connection.
- **Pool exhaustion:** answers 503 after the checkout timeout, never a hang.
- **Statement timeout:** answers 504 through `tick_errors`.
- **Undefined price or out-of-order input:** answers a logged 500 (D1).
- **Ledger and rows disagreeing:** the bar ceiling and the statement timeout still bound the read.

Integration tests cover early stop and disconnect. The admission design is also sound: it counts work, which is known from the ledger before any read, and bounds the output while cutting (D5 "Rejected" explains why neither a row-ceiling count nor a `Σ ceil(n/N)` pre-count would work).

### [PASS] Integration points and proportionality are otherwise acceptable

The design gives 280 what its live messages need: the cutter's `push`/`current`/`close_session`, the columnar block and the stream key. It consumes 306's family-parameterised plan and `verdicts_for` and names 306's merge as a precondition. Prerequisite changes are small and explicit: the private `_stream` becomes the public `stream_rows`, and `read_series` gains an optional `admit` hook that `/ticks` and `/bars` leave unset. Every item in the plan entry has a home: the module, the route with free N, the compact encoding and stream key, admission, and the docs. Nothing in the entry is dropped. The exclusions (no CLI form, no time bars from ticks, no new tables) match the architecture's principles.

### Run Digest

- Response length: 6268 chars
- Response is newline-free: no
- Tool calls made: 6
- Tool calls failed: 0
- Stop reason: end_turn
- Output budget: backend default
- System prompt: preset+append
- Settings sources: project
- Reasoning characters: 0
- Effort: high
- Turns: 7
- Tokens — prompt / cached / cache write / completion / reasoning: 510105 / 401350 / 108743 / 8131 / not computed
- Duration: 77.7 s
- Cost: $0.56 (provider)
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 6
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 6
- Finding-shaped matches — surviving validation: 6

## Resolution

- F001 [pass] Rollup design matches the architecture: no change.
- F002 [concern] Partial-bar rule: applied. The architecture's "Tick bars and raw ticks" paragraph and the 308 slice plan entry now say the last bar is partial only when it holds fewer than N trades. D2 points to that amendment instead of calling it a clarification. D1 now says `current()` and `close_session()` return `None` when no bar is open, which is the case right after a bar closes at its Nth trade.
- F003 [concern] D9 rename: applied. D9 is cut from the slice, along with its Scope line, component entries, criterion 8, step 1, CHANGELOG item and OpenAPI mentions. An Excluded line says the rename ships on its own as a `refactor:` commit under the existing issue #72. Later decisions, criteria and steps are renumbered (D10→D9, D11→D10).
- F004 [note] Trade limit not measured before release: rejected. The review calls it optional and the existing plan adequate. D5 already lowers the default on a miss, with the measurement recorded.
- F005 [pass] Failure modes enumerated: no change (now D10 after renumbering).
- F006 [pass] Integration points and proportionality: no change.
