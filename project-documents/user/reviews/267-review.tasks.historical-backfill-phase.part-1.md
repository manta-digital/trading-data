---
docType: review
layer: project
reviewType: tasks
slice: historical-backfill-phase
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/267-tasks.historical-backfill-phase-1.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260901
dateUpdated: 20260901
reviewedSha: 00c1f0a397f2a6f08bb41c3b1e6629fed2e1542d
findings:
  - id: F001
    severity: concern
    category: completeness
    summary: "Task 2.1's own success grep cannot pass — stale \"slice 266\" references left unscheduled"
    location: "src/manta_trading/cli/commands/kalshi_render.py:318"
  - id: F002
    severity: concern
    category: sequencing
    summary: "Section 3 test task is sequenced before the fixtures it depends on"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-1.md:237-249"
  - id: F003
    severity: note
    category: consistency
    summary: "Task 1.2 mixes a [PM] sub-step and an [agent] sub-step in one task, unlike sibling slice 265's convention"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-1.md:114-126"
  - id: F004
    severity: pass
    category: nfr-coverage
    summary: "No load-test task is missing, and none needs CI gating"
    location: "unverified"
  - id: F005
    severity: pass
    category: coverage
    summary: "All ten slice Success Criteria trace to tasks, and commit/effort discipline is sound within part 1"
    location: "project-documents/user/tasks/267-tasks.historical-backfill-phase-1.md"
---

# Review: tasks — slice 267

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Task 2.1's own success grep cannot pass — stale "slice 266" references left unscheduled

Task 2.1 fixes only two files' stale references to the retired slice 266 (`constants.py`'s two policy comments and `trade_types.py`'s `TradesBehindCutoffError` message), then states as its acceptance test: "`grep -rn "266" src/` finds nothing that describes future work." Running that grep over the actual tree turns up four more sites that describe 266 as pending/future work and are never assigned to any task in either part 1 or part 2:
- `src/manta_trading/data/kalshi/candle_sync.py:111` — "the cutoff line is the signal that 266 (historical backfill) has become urgent"
- `src/manta_trading/data/kalshi/candle_repository.py:194` — "no longer served live; slice 266's input"
- `src/manta_trading/data/kalshi/candle_selection.py:26` — "`BEHIND_CUTOFF` is slice 266's input"
- `src/manta_trading/cli/commands/kalshi_render.py:318` — the **user-facing** `status` CLI line: `"before coverage ... (tape predates the collector; slice 266)"`

The last one is the most significant: the slice design's *Value* section explicitly frames this exact caveat as what the slice replaces ("A visible floor replaces a permanent caveat"), yet no task in Section 7 (which does touch `kalshi_render.py` to add the *new* historical status line, Task 7.4) or elsewhere edits this pre-existing `before coverage` line. After 267 ships, operators would still be told to look for "slice 266," which no longer exists. (`client.py`'s reference is fixed by Task 3.1, and `trade_status.py`'s docstring by Task 7.3 — those are fine; only these four are unaddressed.)

failure_scenario: Slice 267 ships; an operator runs `mt data kalshi status` and sees "before coverage 12,000 closed markets (tape predates the collector; slice 266)" — a dangling reference to a retired slice, and the exact caveat the slice's Value section promised to remove is still printed verbatim.

### [CONCERN] Section 3 test task is sequenced before the fixtures it depends on

Task 3.2 ("Client endpoint unit tests") is placed before Task 3.3 ("Recorder and fixtures"), but Task 3.2's own success bullet says the cases run "against the fixtures Task 3.3 records" and hedges with "write them against hand-rolled bodies first if the recorder runs later, then switch to the fixture names." This is a self-acknowledged ordering problem: a junior agent executing in document order does throwaway work in 3.2 (hand-rolled response bodies) and then has to redo it once 3.3's real fixtures exist. Swapping the two tasks (methods → fixtures → tests-against-fixtures), or merging them, would remove the rework and the conditional language.

failure_scenario: An agent completes Task 3.2 using hand-rolled JSON bodies, checks it off, then Task 3.3 records real fixtures with a subtly different shape (e.g. field ordering or an extra optional key) — the "switch to fixture names" step is silently skipped because the task is already marked done, leaving tests that assert against synthetic data instead of the recorded real-world response.

### [NOTE] Task 1.2 mixes a [PM] sub-step and an [agent] sub-step in one task, unlike sibling slice 265's convention

265's task files (`265-tasks.public-trades-collection-2.md:348,360,383`) always give `[PM]` and `[agent]` work separate task numbers (Task 9.1 `[PM]`, Task 9.2 `[PM]`, Task 9.3 `[agent]`), so each task has a single owning actor. Task 1.2 here bundles a `[PM]` host command and an `[agent]` write-up under one task number. Given it's a single short synchronous handoff (paste output, then record it), this is low-risk, but it's a pattern deviation from the established convention that could confuse ownership tracking (e.g. the `task-checker` agent checking off a task that is only half-done from the agent's side).

### [PASS] No load-test task is missing, and none needs CI gating

The slice's only threshold-shaped criterion — Criterion 6, "total pass duration ... stays under 45 minutes" — is enforced structurally by a computed request cap (`cap = requests_per_minute × HISTORICAL_PHASE_MINUTES`), not by a runtime load characteristic. It is verified by unit test (Task 2.2's arithmetic assertions, Task 7.1's computed-cap test in part 2), by rehearsal (Task 9.2), and by the production cutover script's report check (Task 11.1). This mirrors the precedent already recorded for sibling slice 264 (`264-tasks.candlestick-collection-1.md:579-582`, review finding F008): "no load test is required... the design states no NFR, and its workload numbers are measurements and derived estimates, not thresholds." It also matches repo-wide practice — `test/load` is a manually-invoked pytest tier (`scripts/run_tests.py`) that is not wired into `.github/workflows/ci.yml` for any existing slice, so adding a new CI-gating task here would be inventing a bar the codebase doesn't otherwise hold itself to.

### [PASS] All ten slice Success Criteria trace to tasks, and commit/effort discipline is sound within part 1

Cross-referencing the design's Success Criteria 1–10 against both task files: all ten land in part 2 (Sections 6–11), consistent with part 1's explicit framing as prerequisite work only ("Sections 1–5 do the endpoint-cost discovery, constants and migration, the client methods and fixtures, the repository seams, and the direction-parameterised window loop"). No criterion is dropped. Within part 1: every task sits at or under the stated effort ceiling ("Everything else is ≤ 3"), commit checkpoints close each of the five sections (Tasks 1.2, 2.5, 3.4, 4.4, 5.3) rather than batching at the end, and four of five sections follow the test-immediately-after-implementation pattern (2.1→2.2, 4.1→4.2, 5.1→5.2, and 4.3 which bundles its own integration test).
