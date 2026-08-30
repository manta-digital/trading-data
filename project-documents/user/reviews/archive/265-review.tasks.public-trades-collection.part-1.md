---
docType: review
layer: project
reviewType: tasks
slice: public-trades-collection
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/tasks/265-tasks.public-trades-collection-1.md
aiModel: claude-opus-5
status: complete
dateCreated: 20260829
dateUpdated: 20260829
reviewedSha: ca487a1be8edf17a7906aa83be8f8cd6954044af
findings:
  - id: F001
    severity: concern
    category: coverage-gap
    summary: "The trades `cutoff` the status block renders has no persisted source, and only part 1's migration can give it one"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:300-327"
  - id: F002
    severity: concern
    category: test-coverage
    summary: "`is_block_trade NOT NULL` is argued for at length in Task 2.2 but no task tests it"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:313-318"
  - id: F003
    severity: note
    category: sequencing
    summary: "Task 4.2 needs the phase-name string two tasks before Task 4.4 defines it, and the existing precedent is unstated"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:507-509"
  - id: F004
    severity: note
    category: test-coverage
    summary: "No task asserts `KalshiClient` structurally satisfies the `TradeSource` Protocol"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:467-469"
  - id: F005
    severity: note
    category: test-with-pattern
    summary: "Section 1 batches its tests into Task 1.5 rather than pairing them with Tasks 1.2–1.4"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:238-268"
  - id: F006
    severity: pass
    category: coverage
    summary: "Every success criterion traces to at least one task, and no task lacks a criterion"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:372-386"
  - id: F007
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed one per section, not batched"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:270-275"
  - id: F008
    severity: pass
    category: nfr-gating
    summary: "No NFR is restated, so the absence of a `test/load/` task is correct"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:475-479"
---

# Review: tasks — slice 265

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] The trades `cutoff` the status block renders has no persisted source, and only part 1's migration can give it one

The design's Rich block (slice line 336) prints `cutoff 2026-06-29` on the trades header line, but `TradeStatus`'s field list (slice lines 325-331) contains only `last_phase_at`, `tape_through`, `lag`, `behind`, `coverage_from` — no cutoff — and Decision 10 forbids `status` from calling the client. Task 2.2's migration adds only `coverage_from_ts` to `sync_state`; nothing writes the observed `trades_created_ts` anywhere.

This is not covered by `coverage_from_ts`. Those two values coincide only on the first run: the design's own Discovery Findings measure the cutoff advancing ~one day per day, while `coverage_from_ts` is "set once, never moved." By day two they differ, and part 2's Task 5.3 ("render the design's *Rich block* layout", test: "the Rich block renders every field") has no value to render.

Failure scenario: the implementer reaches Task 5.3 in part 2, finds no source for `cutoff`, and either (a) renders `coverage_from` twice under two labels — a silently wrong operator-facing number that hides exactly the signal the design calls out ("a cutoff that reaches the watermark is the signal that 266 has become urgent"), or (b) calls `get_historical_cutoff()` from `status.py`, breaking Criterion 11 and the `test_status_imports.py` guard, or (c) adds a `kalshi_007` migration for a column `kalshi_006` should have carried.

Note the candle surface solves this by storing its cutoff in `sync_state['candlesticks'].watermark_ts` (`status.py:259-266`) — a slot that is *not* available to trades, because for trades `watermark_ts` is the tape watermark. Resolve in Task 2.2 by adding the column (e.g. `cutoff_observed_ts`) alongside `coverage_from_ts` and having Task 3.1/4.2 write it each run, or strike `cutoff` from the Rich block in part 2 and record why.

### [CONCERN] `is_block_trade NOT NULL` is argued for at length in Task 2.2 but no task tests it

Task 2.2 spends a full bullet justifying keeping the column `NOT NULL` while 261's model types it `bool | None = None` (verified: `data/kalshi/models.py:153`), explicitly invoking "the same posture the slice takes toward a non-UUID `trade_id` (Task 3.3 case 6)". But Task 3.3's fixture set (lines 432-441) has a case for the non-UUID id and no case for a null `is_block_trade`.

Failure scenario: Kalshi omits `is_block_trade` on some trade class (the sample that justified the decision was 352,000 rows from four hours of one day). The insert raises `psycopg.errors.NotNullViolation` — an `IntegrityError`, not an `OperationalError` — which under Task 3.2's taxonomy "any other `psycopg.Error` propagates as a bug" aborts the entire pass with an unclassified exception rather than a `STORAGE_ABORT`. Whether that is the intended blast radius is untested and undocumented; the sibling behavior (non-UUID id) at least has case 6 to pin its exception type. Add a seventh case to Task 3.3: a page row with `is_block_trade=None` fails the write, asserting the concrete exception type that propagates.

### [NOTE] Task 4.2 needs the phase-name string two tasks before Task 4.4 defines it, and the existing precedent is unstated

Task 4.2 step 6 requires emitting `phase_finished` with `phase="trades"`, and Task 4.3b case 9 asserts it — but `PassPhaseName.TRADES` is added in Task 4.4, and Task 2.1 (line 295-296) explicitly defers it there. Part 1's hard rule "Every comparison value is a named constant" then leaves the implementer with no constant to use.

The repository already resolves this: `candle_sync.py:64-67` defines a module-local `PHASE = "candles"` with a comment explaining that the core cannot import `collection_pass` (which imports it). Task 4.2 should name that precedent and instruct the same `PHASE = "trades"` shape with the same comment; otherwise a junior implementer either hardcodes a bare literal against the stated rule, or introduces the circular import the candle module was written to avoid. Low risk of a wrong outcome, high risk of a wasted debugging cycle.

### [NOTE] No task asserts `KalshiClient` structurally satisfies the `TradeSource` Protocol

Task 4.1 defines `TradeSource` with explicit keyword parameters (`min_ts: int, max_ts: int, limit: int`), while the real client is `get_trades(self, *, cursor=None, **query: Unpack[TradesQuery])` (`client.py:350-352`) with `TradesQuery` declaring those keys as `int | None` under `total=False` (`client.py:106-113`). Conformance should hold structurally, and the mypy/pyright gate is the intended net — but MEMORY records that this exact `Unpack` pattern produces false errors under narrower mypy invocations in this package, so the gate is the least reliable place to learn about a mismatch. A one-line `_: TradeSource = client` conformance assertion in Task 4.3a's fakes module would pin it deterministically. Nothing is currently wrong; this is cheap insurance on a known-fragile seam.

### [NOTE] Section 1 batches its tests into Task 1.5 rather than pairing them with Tasks 1.2–1.4

Sections 2, 3, and 4 pair each implementation task with its test task immediately (2.2→2.3, 3.2→3.3, 4.2→4.3a/4.3b). Section 1 instead accumulates four implementation tasks (1.1–1.4) and tests them all in 1.5. This is defensible — Section 1 is a pure rename whose regression net is the existing green suite, and Task 1.1 does carry its own baseline snapshot test written *before* the `git mv` — but the two genuinely new behaviors (Task 1.3's `"any"` form, Task 1.4's dual-source guard) are new code whose tests sit two and one tasks downstream respectively. Consider folding the `"any"` and guard tests into 1.3 and 1.4 and leaving 1.5 as the mechanical rename sweep. Not blocking.

### [PASS] Every success criterion traces to at least one task, and no task lacks a criterion

Criteria 1→Tasks 4.4/6.1; 2→3.2/3.3/4.3b(4)/7.3; 3→3.3(4)/6.1/7.4; 4→4.3b(5,6)/7.4; 5→Section 1 entire/7.2; 6→4.3b(1,8)/9.2; 7→4.3b(2,3)/7.3; 8→4.3b(7)/9.3; 9→4.2/4.3b(10)/3.3(2); 10→6.2/7.2; 11→5.1/5.2a/5.4; 12→2.3/7.5; 13→9.2/9.3 plus an explicit handoff for the multi-day clause. Conversely, no part-1 task is untraceable: even the additions beyond the design's letter (Task 1.1's `MARKET_JOIN` snapshot baseline, Task 1.2's bound-parameter rename, Task 1.4's env-example header fix) each carry an in-line justification tied to Criterion 5 or walkthrough step 8. I found no scope creep.

Two criteria are deliberately proven in production rather than the rehearsal (6's cutoff start, 8's cap), and part 2's Task 7.6 requires that gap to be written down with its reason and its substitute location — a good pattern that keeps the deferral visible rather than implicit.

### [PASS] Commit checkpoints are distributed one per section, not batched

Part 1 carries four checkpoints (Tasks 1.6, 2.4, 3.4, 4.7), each with a semantic message matching CLAUDE.md's prefix table (`refactor:`, `feat:`, `feat:`, `feat:`), each preceded by scoped ruff/mypy/pyright gates. Part 2 adds five more (5.5, 6.3, 7.6, 8.2, 9.x). This matches the PM-confirmed granularity in MEMORY ("checkpoint per section, not per numbered subtask"). Task 8.2 correctly notes it omits the lint gates because it edits markdown only — the kind of stated exception that stops a reader from assuming an oversight. No git merge or branch steps appear as tasks, per the standing veto.

### [PASS] No NFR is restated, so the absence of a `test/load/` task is correct

I grepped the slice for NFR language and found none; the repo does maintain the convention (`test/load/test_167_data_status_nfr.py`, `test_169_...nfr.py`, etc.), so its absence here is a choice rather than an omission. The quantitative figures the slice does carry — ~420 requests/hour steady state, ~3,000-request cap, per-window wall time before and after compression — are capacity estimates and one-off drain observations, not committed thresholds, and they are correctly assigned to recorded measurements (Tasks 7.3, 7.5, 9.3) that report numbers rather than assert bounds. Task 9.3's insistence that all five numbers come "from the firing that just completed" is the right way to keep that measurable without a wait-blocked item. The one latent performance risk the design flags — `write_page`'s 1,000-row catalog join, ~10k statements/day — is observable through the per-window wall time those tasks capture, with the per-phase ticker cache named as the lever if it dominates.
