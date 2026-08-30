---
docType: review
layer: project
reviewType: tasks
slice: public-trades-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/265-tasks.public-trades-collection-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260829
dateUpdated: 20260829
reviewedSha: 1136f9ee6caa2c66c21f347444769c86fa2b9eee
findings:
  - id: F001
    severity: concern
    category: correctness
    summary: "Task 5.1 requires `_iso` reuse from a module it also forbids coupling to"
    location: "src/manta_trading/data/kalshi/status.py:81"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "Criterion 4's mid-window abort has no integration-tier proof, though the design assigns it there"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:276-279"
  - id: F003
    severity: concern
    category: correctness
    summary: "Task 9.3's watermark-delta bullet names a source that no longer exists when the task runs"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:364-366"
  - id: F004
    severity: note
    category: documentation
    summary: "Runbook rename targets verified; Task 8.1's mechanical success check is sound"
    location: "project-documents/user/runbooks/100-production-operations.md:131"
  - id: F005
    severity: note
    category: test-coverage
    summary: "No NFR is restated, so no load test or CI gate is owed"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:373-387"
  - id: F006
    severity: pass
    category: traceability
    summary: "Success-criteria coverage is complete and cross-referenced by number"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:44-48"
  - id: F007
    severity: pass
    category: sequencing
    summary: "Criterion 13's multi-day clause is carried as a handoff, not a wait-blocked task"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:390-399"
  - id: F008
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, and gate shape is justified where it differs"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:134-136"
  - id: F009
    severity: pass
    category: scoping
    summary: "Task sizing is uniform and dependencies flow forward"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:50-399"
---

# Review: tasks — slice 265

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] Task 5.1 requires `_iso` reuse from a module it also forbids coupling to

Task 5.1 (lines 50–77) mandates a new `data/kalshi/trade_status.py`, explicitly bans any re-export path through `status.py` ("a module whose only job is to forward a name is the complexity CLAUDE.md tells us to resist"), and then says `TradeStatus.to_dict()` puts "timestamps through the existing `_iso` helper." `_iso` is defined at `status.py:81` as a module-private function. The task leaves a junior implementer three unmarked options: import `status._iso` across modules (a private import, and it reintroduces exactly the coupling the task just forbade), copy it (DRY violation, CLAUDE.md), or promote it to a shared module (unstated, and it touches `CandleStatus`/`CatalogStatus` call sites at `status.py:62,199,212`). Failure scenario: the implementer copies `_iso` into `trade_status.py`; a later change to timestamp rendering updates only one copy and the JSON payloads of the candle and trade blocks diverge silently. Name the resolution in the task — the cheapest is a one-line bullet saying `_iso` moves to an existing shared helper module and the three current call sites follow.

### [CONCERN] Criterion 4's mid-window abort has no integration-tier proof, though the design assigns it there

Task 7.6 records the abort-inside-a-window rehearsal (walkthrough step 7) as deliberately not performed, citing "the integration test's job (part 1, Task 4.3b case 6)." Task 4.3b is a *unit* test file (`test/unit/data/kalshi/test_trade_sync.py`, part 1 line 575), running against `FakeTradeRepository` — an in-memory fake with no transactions. No task in either part injects a `ProviderError` mid-window against a real database: part 1's Task 3.3 covers `write_page` cases only, and Task 6.1 (line 143) runs a clean three-phase pass. Failure scenario: `TradeSync` advances the watermark inside the same transaction scope as the page writes (or `advance_watermark` autocommits on a connection where earlier page writes were rolled back); the fake repository records the call order correctly and case 6 passes, while against `kalshi_db` the watermark would be left past an incompletely walked window — the exact loss Criterion 4 and Decision 7 exist to prevent. Task 6.1 already builds the fake trade source into the integration pass test; adding "a `ProviderError` after page 2 of the first window leaves `sync_state['trades'].watermark_ts` unchanged and the committed rows of pages 1–2 present" is one bullet there.

### [CONCERN] Task 9.3's watermark-delta bullet names a source that no longer exists when the task runs

Task 9.3 opens with "Everything here is read **from the firing that just completed** — no bullet waits on a later one," then instructs: "`watermark_ts` advanced by the expected number of windows … read **before and after** from `sync_state['trades']`." After the firing completes, the "before" value is gone from `sync_state` — the row has already been overwritten. Failure scenario: the PM runs Task 9.3 after Task 9.2's firing, cannot obtain the "before" watermark, and either skips the number (losing the cap's per-pass-advance measurement, which is unrepeatable — the first firing happens once) or waits for a second firing (violating part 1's hard no-wait rule and the PM's standing veto). The fix is the same one the reviewer's F005 applied to the `capped` figure one bullet below: name the retrievable source. On the first run the "before" watermark equals `coverage_from_ts` (still persisted, never moved) and is also printed on the `kalshi trades phase started … watermark=` journal line Task 9.2 already captures.

### [NOTE] Runbook rename targets verified; Task 8.1's mechanical success check is sound

Task 8.1 (line 303) claims two existing `MT_KALSHI_CANDLE_*` references at runbook lines 131 and 415; `grep` confirms exactly those two hits and no others. The success criterion — every remaining `MT_KALSHI_CANDLE_` hit must sit inside the one sentence describing the guard's failure message — is checkable by a junior without judgment, which is what a documentation task's success criterion should look like.

### [NOTE] No NFR is restated, so no load test or CI gate is owed

Neither the slice nor its parent (`260-slices.kalshi-event-contract-data.md`) contains an NFR section, and no success criterion is phrased as a numeric non-functional bound to gate on; the repo's `test/load/*_nfr.py` files all belong to slices that did restate one. The prior review round's F007 ("no NFR to gate on") is correct and no load-test or CI-wiring task is missing. Worth stating explicitly: the design's throughput expectations (~420 requests/hour steady state, per-window wall time, ~15-minute drain pass) are proven only by the rehearsal's recorded numbers (Tasks 7.3–7.5) and one production firing (Task 9.3) — there is no automated regression gate, and none is required here, but a future performance regression in `write_page` would surface only through operator observation.

### [PASS] Success-criteria coverage is complete and cross-referenced by number

Every criterion has an owner: 1 → part 1 Task 4.4 + Tasks 6.1, 7.3; 2 → part 1 Tasks 3.2/3.3 + 7.3; 3 → 6.1, 7.4; 4 → part 1 Task 4.3b + 7.4; 5 → part 1 Section 1 + 7.2; 6 → part 1 Task 4.3b + 9.2; 7 → part 1 Task 4.3b + 7.3; 8 → part 1 Task 4.3b case 7 + Tasks 5.1/5.3 (lag) + 9.3; 9 → part 1 Task 3.3 case 2 + 7.3; 10 → 6.2, 7.2; 11 → Section 5; 12 → part 1 Task 2.3 + 7.5; 13 → Section 9. Section 5's header disclaiming Criterion 10 ("belongs to Tasks 6.2 and 7.2, not here") is the kind of explicit non-ownership that prevents a criterion from falling between two sections. No task in part 2 lacks a criterion or design-section citation, so there is no scope creep.

### [PASS] Criterion 13's multi-day clause is carried as a handoff, not a wait-blocked task

The `tape through` advancing over ~10 days is explicitly excluded from the checklist and routed to the completion record plus a 266 prerequisite, with each underlying mechanism attributed to a proof that completes now (the cap to part 1 Task 4.3b case 7 and Task 9.3, the window loop to the rehearsal, the lag figures to Task 5.3). This is the correct handling of an unobservable-today criterion and matches the standing rule that no task may wait on wall-clock time.

### [PASS] Commit checkpoints are distributed, and gate shape is justified where it differs

Part 2 carries four checkpoint commits — 5.5 (`feat: add the trades block…`), 6.3 (`test: add end-to-end coverage…`), 7.6 (`docs: record the 265 rehearsal…`), 8.2 (`docs: document the trades phase…`) — one per section that produces artifacts, none batched at the end. Section 8's explicit note that it runs no ruff/mypy/pyright gates *because it edits markdown only* pre-empts the obvious "why is this section shaped differently" question rather than leaving the implementer to guess. Section 9 correctly carries no commit: it is host operations.

### [PASS] Task sizing is uniform and dependencies flow forward

Every part 2 task is effort ≤ 3, inside the ceiling part 1's Context Summary sets (part 1 keeps four above it with stated justification). Sequencing has no cycles: Section 5 depends on part 1's repository and rule; 5.3's integration tests are placed before 5.4's rendering, so the riskiest logic — the three coverage-boundary counts in 5.2 — is verified before anything renders it; Section 6 depends on Section 5's wiring; Section 7 exercises the CLI built in 5–6; Section 8 updates the runbook that Section 9's PM steps follow. Tasks 7.3–7.5 now compare like against like (first-pass insert path vs. uncompressed re-walk vs. compressed re-walk), so Criterion 12's timing measurement attributes to compression rather than to write-path differences.
