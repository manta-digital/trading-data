---
docType: review
layer: project
reviewType: tasks
slice: public-trades-collection
project: trading-data
verdict: FAIL
sourceDocument: project-documents/user/tasks/265-tasks.public-trades-collection-2.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260829
dateUpdated: 20260829
reviewedSha: d1208310e79c72ee6a42d7ed5c5861387fb5da4a
findings:
  - id: F001
    severity: fail
    category: uncategorized
    summary: "Task 9.3 is wait-blocked across days, contradicting part 1's own hard rule"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:301-315"
  - id: F002
    severity: concern
    category: uncategorized
    summary: "Task 9.1 makes release tagging a task, which part 1 says it is not"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:278-281"
  - id: F003
    severity: concern
    category: uncategorized
    summary: "The design's late-arriving-trades check has no task and no recorded omission"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:479"
  - id: F004
    severity: concern
    category: uncategorized
    summary: "Section 5's header cites Success Criterion 10, which belongs to a different section"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:41-44"
  - id: F005
    severity: concern
    category: uncategorized
    summary: "Task 5.2's success criterion is self-cancelling, and its assertion has no owning test bullet"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:77-79"
  - id: F006
    severity: note
    category: uncategorized
    summary: "Task 7.3 is titled \"the cap\" but no bullet observes capping"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:179-199"
  - id: F007
    severity: note
    category: uncategorized
    summary: "Task 8.3 is a commit-only task and, unlike its peers, carries no gates"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:269-270"
  - id: F008
    severity: note
    category: uncategorized
    summary: "Task 7.6 is bookkeeping-only and belongs inside Task 7.7"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:225-229"
  - id: F009
    severity: note
    category: uncategorized
    summary: "Task 5.2 at effort 4 exceeds the effort ceiling established for this slice family"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:58-79"
  - id: F010
    severity: note
    category: uncategorized
    summary: "Task 5.1 deviates from the design's component structure; the deviation is justified but the re-export is not"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:46-56"
  - id: F011
    severity: pass
    category: uncategorized
    summary: "Every success criterion traces to at least one task, across both files"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:374-386"
  - id: F012
    severity: pass
    category: uncategorized
    summary: "No scope creep; every task traces back to the design"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:41-315"
  - id: F013
    severity: pass
    category: uncategorized
    summary: "Commit checkpoints are distributed, and tests follow their implementation"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-2.md:114-116"
  - id: F014
    severity: pass
    category: uncategorized
    summary: "No load test is required, consistent with the precedent set for this slice family"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:88-96"
---

# Review: tasks — slice 265

**Verdict:** FAIL
**Model:** claude-opus-5

## Findings

### [FAIL] Task 9.3 is wait-blocked across days, contradicting part 1's own hard rule

Task 9.3 "Watch the drain" spans days by construction: "Over the following days…", "when `behind` clears (~10 days)", and its own last bullet admits "This task spans days by its nature". Part 1's Context Summary states as a hard rule for this slice: "No task waits on a wall-clock event" (`265-tasks.public-trades-collection-1.md:100`). The PM has separately vetoed wait-blocked tasks outright — no task may wait on tonight/tomorrow/next week; restructure to something measurable now.

The self-exemption ("not a blocking step for the slice's merge") does not resolve it, because Success Criterion 13's second half — `tape through` advancing ~7 hours per firing until `behind` clears, then staying within two hours of now — has no other owning task. As written, Criterion 13 cannot be marked done for ~10 days after merge, and there is no artifact that records it when it is.

Restructure into what is measurable at the time of the firing: after the first supervised firing (Task 9.2), assert the single-firing deltas — `watermark_ts` advanced by ~7 windows, `requests` at the cap, `capped: true`, `HTTP 429` count zero, `before coverage` recorded as a baseline number. Then make the ~10-day steady-state observation an explicit follow-up note in the slice completion record (or a 266 prerequisite), not a checklist item in this file.

### [CONCERN] Task 9.1 makes release tagging a task, which part 1 says it is not

Task 9.1's first bullet begins "Tag → `install-production.sh --ref` → …". Part 1's Context Summary states "Merge and release tagging follow runbook 100's update procedure after PM approval and **are not tasks here**" (`265-tasks.public-trades-collection-1.md:96-97`), and git branch/merge/tag steps are not permitted as task items. A junior implementer reading part 2 in isolation will treat tagging as an actionable step it is told elsewhere it is not.

Fix by making 9.1 start at the installed ref: "Following runbook 100's update procedure (tag and install are the PM's release steps, not tasks), verify `mt data migrate status --track kalshi` reports 1 pending → apply with the maintenance credential → 0 pending."

### [CONCERN] The design's late-arriving-trades check has no task and no recorded omission

The slice's Risk Assessment names a concrete rehearsal check for its third risk: "the rehearsal re-walks a settled hour a day later and diffs the count as the check." Section 7 (Tasks 7.1–7.7) contains no such step, and — unlike the abort case, which Task 7.6 handles by deliberately recording that the manual analogue was not re-run and why — nothing in the file records that this check was dropped. It also cannot be added as written without becoming a second wait-blocked task.

Either add a bullet to Task 7.4 or 7.5 that re-walks a window already walked earlier in the same rehearsal and diffs the row count (measurable within the session, weaker but real), or add a bullet to Task 7.7's rehearsal note stating the day-later diff was not performed, why, and where the residual risk is carried (the PM's drain watch). Silently omitting a design-named check is the gap; the pattern for handling it correctly already exists at Task 7.6.

### [CONCERN] Section 5's header cites Success Criterion 10, which belongs to a different section

Section 5's header reads "Design *CLI and rendering*, *Technical Decision 10*, Success Criteria 10 and 11." Criterion 10 is the ledger preflight naming `kalshi_006_trades` (slice design line 383) and has nothing to do with the status block; it is correctly owned by Task 6.2 and Task 7.2. Decision 10 ("`status` reads the database only") is cited correctly, so this looks like the "10" being duplicated from the decision number into the criteria list. Section 5 covers Criterion 11 only (plus part of Criterion 8's `status`-shows-the-lag clause). Leaving it wrong means an implementer checking off Section 5 believes preflight coverage is done when it is not.

### [CONCERN] Task 5.2's success criterion is self-cancelling, and its assertion has no owning test bullet

The bullet reads: "Success: the reader issues no query against `kalshi.trades` (assert in the integration test by inspecting the statements or by dropping read access is not needed — assert on the rendered SQL text)." The parenthetical proposes two approaches, negates part of one mid-sentence, and lands on a third. A junior implementer cannot determine what to write.

Compounding it, Task 5.4's integration bullets (lines 97-112) enumerate the `None` case, every field value, the four counts, and rule respect — but never the "no query against `kalshi.trades`" assertion. So the check Decision 10 exists to enforce is stated as a success condition of 5.2 and then owned by no test bullet anywhere. Resolve to one sentence — e.g. "Success: a unit test asserts the rendered SQL text contains no reference to `kalshi.trades`" — and add the matching bullet to whichever task owns that test.

### [NOTE] Task 7.3 is titled "the cap" but no bullet observes capping

The task title reads "First pass — three phases, the floor, the cap", and it mirrors the design's walkthrough step 4, which cites Criteria 1, 2, 6, 8, and 9. But the task seeds the watermark at `now − 3 hours`, so the drain is ~3 windows ≈ 900 requests — well under `TRADE_REQUESTS_PER_PASS = 3,000`. Capping cannot occur, and correspondingly none of the seven bullets mentions `capped`. Criterion 8 is therefore proven only by part 1's unit test (Task 4.3, case 7) and by the PM's Task 9.2/9.3 observation.

That is defensible — a rehearsal that actually hit the cap would take ~10 minutes of live tape — but the title should say so, the way Task 7.3 already handles the first-run floor ("record explicitly that this substitutes for the design's cutoff start"). Suggest either dropping "the cap" from the title, or adding a bullet: "confirm the summary reports `capped: false` and record that the cap itself is proven by Task 4.3 case 7 and observed on the host in Task 9.2."

### [NOTE] Task 8.3 is a commit-only task and, unlike its peers, carries no gates

Tasks 5.5, 6.3, and 7.7 each pair a checkpoint commit with gates or a completion action. Task 8.3's entire content is one `git commit` line. It should be a bullet at the end of Task 8.2, not a task. Separately, since Section 8 edits markdown only, its omission of gates is correct — but stating that explicitly ("no ruff/mypy gates: documentation only") would keep the section's shape legible against 5.5 and 6.3.

### [NOTE] Task 7.6 is bookkeeping-only and belongs inside Task 7.7

Task 7.6's whole body is "confirm that test covers it and record in the rehearsal note that the manual analogue was not re-run by hand and why." There is nothing to run and nothing to produce except one paragraph in a note that Task 7.7 writes anyway. Merging it into 7.7 as a bullet costs nothing and removes a task whose completion is indistinguishable from reading part 1. (The *content* is right and should be kept — see the late-arriving-trades finding above, where this exact pattern is what is missing.)

### [NOTE] Task 5.2 at effort 4 exceeds the effort ceiling established for this slice family

Slice 264's task review resolved its own sizing finding by splitting until "No task in either file now exceeds effort 3" (`264-tasks.candlestick-collection-1.md:571-573`). Task 5.2 is effort 4 and does carry two separable halves: the `sync_state`-only scalar fields (`last_phase_at`, `tape_through`, `lag`, `behind`, `coverage_from`) and the four boundary-sensitive counts over `selection_sql(rule, "ever")`. The counts are where the risk is — three of the four turn on `coverage_from` vs `open_time`/`close_time` ordering — and they would benefit from being their own task with their own success criterion. Part 1 carries effort-5 tasks too (4.2, 4.3), so this may be a deliberate departure from 264's ceiling; if so, saying so once in part 1's Context Summary would settle it.

### [NOTE] Task 5.1 deviates from the design's component structure; the deviation is justified but the re-export is not

The design's Component Structure places `TradeStatus` and `read_trade_status` in `data/kalshi/status.py`. Task 5.1 puts them in a new `data/kalshi/trade_status.py` instead. The justification is sound and verified — `status.py` is exactly 309 lines today, already over the ~300-line guideline before this slice adds four counts and a dataclass.

The re-export back through `status.py` "so the CLI's import site does not fragment" is the part worth reconsidering: it creates a module whose only job is to forward a name, and Task 5.3 then wires the CLI up anyway. Importing `read_trade_status` from `trade_status` directly at the one CLI call site is simpler and keeps the extraction honest. If the re-export is kept for the existing `test_status_imports.py` guard's sake, say that explicitly — the current rationale ("does not fragment") does not survive CLAUDE.md's resist-complexity rule on its own.

### [PASS] Every success criterion traces to at least one task, across both files

Criterion 1 → part 1 Task 4.4 (unit), part 2 Tasks 6.1, 7.3. Criterion 2 → part 1 3.2 (structural identity assertion), 3.3, 4.3 case 4; part 2 7.3. Criterion 3 → part 1 3.3 case 4; part 2 6.1, 7.4. Criterion 4 → part 1 4.3 cases 5–6; part 2 7.4, 7.6. Criterion 5 → part 1 Section 1 entire, 3.3 case 5; part 2 7.2. Criterion 6 → part 1 4.3 cases 1, 8; part 2 9.2 (with the substitution explicitly recorded in 7.3). Criterion 7 → part 1 4.3 case 2; part 2 7.3. Criterion 8 → part 1 4.3 case 7; part 2 9.2 (see the Task 7.3 note above). Criterion 9 → part 1 3.3 case 2, 4.3 case 10; part 2 7.3, 7.7. Criterion 10 → part 2 6.2, 7.2. Criterion 11 → part 2 5.1, 5.2, 5.4. Criterion 12 → part 1 2.3 (policy job on a real chunk); part 2 7.5 (the two timings). Criterion 13 → part 2 9.2, 9.3.

Criterion 6's handling is a particular strength: the rehearsal cannot exercise the cutoff-start floor against an hours-old throwaway catalog, and Task 7.3 says so in writing and names Task 9.2 as the step that does prove it, rather than letting the gap pass unnoticed.

### [PASS] No scope creep; every task traces back to the design

Each of the nine tasks in Sections 5–8 maps to a named design element: Section 5 to *CLI and rendering* and Decision 10; Section 6 to the last three items of *Tests — Integration*; Section 7 to *Verification Walkthrough* steps 1–7; Section 8 to *Runbook 100 and CHANGELOG*; Section 9 to walkthrough steps 8–10. Task 8.1's runbook line references were verified accurate — `100-production-operations.md` carries `MT_KALSHI_CANDLE_*` at exactly lines 131 and 415, both of which the rename must touch. Task 5.1's claim that `status.py` is 309 lines is likewise exact.

### [PASS] Commit checkpoints are distributed, and tests follow their implementation

Checkpoints land at 5.5, 6.3, 7.7, and 8.3 — one per section, matching the design's Development Approach ("Sections, each a checkpoint commit") and the PM-confirmed granularity of one checkpoint per section rather than per numbered subtask. None are batched at the end.

Test-with sequencing holds within each section: 5.2 (reader) → 5.3 (rendering plus its unit tests) → 5.4 (integration tests for the reader); 6.1 and 6.2 are themselves test tasks following part 1's implementation. Section dependencies are linear and acyclic — 5 and 6 both require Sections 1–4, 7 requires 5 and 6, 8 is independent of 7, 9 requires 8. Section 6 could run before Section 5 without harm, but nothing forces an out-of-order dependency.

### [PASS] No load test is required, consistent with the precedent set for this slice family

The slice's *Workload (derived)* table and its throughput figures (~420 requests/hour steady state, ~3-minute pass, ~155 GB/year) are measurements and derived estimates, not thresholds — the design sets no NFR and states no bound a load test could gate on. This matches the resolution recorded for the sibling slice: 264's task review closed the same question as "F008 (note) — no load test is required. Agreed, no action… Adding a `test/load/` task would invent a bound the design declined to set" (`264-tasks.candlestick-collection-1.md:578-582`). `test/load/` exists in this repo and holds NFR suites for slices that do state thresholds (146, 167, 169, 187), so the absence here is a deliberate pattern, not an oversight.

The one figure that behaves like a threshold — "a window taking minutes rather than seconds" against a compressed chunk — is correctly handled as a rehearsal measurement (Tasks 7.3 and 7.5 record both timings as numbers) with a runbook lever rather than an automated gate, because the remedy is an operator pause/resume the application role cannot perform. No CI gating task is needed since no load test is added; note for completeness that `.github/workflows/ci.yml` currently runs only publish-on-tag, so any future load-test gate in this repo would need CI wiring built from scratch.
