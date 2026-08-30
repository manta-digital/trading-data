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
reviewedSha: 1136f9ee6caa2c66c21f347444769c86fa2b9eee
findings:
  - id: F001
    severity: concern
    category: spec-ambiguity
    summary: "`end` means two different instants inside Task 4.2, and the wrong reading silently defeats the request cap"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:519-535"
  - id: F002
    severity: concern
    category: testability
    summary: "The `.env` guard test and the guard implementation specify incompatible seams"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:213-252"
  - id: F003
    severity: note
    category: consistency
    summary: "`init_state` idempotency is asserted in Task 3.3 but not specified in Task 3.1"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:400-402"
  - id: F004
    severity: note
    category: test-coverage
    summary: "Task 3.3 case 7 only fires if the `is_block_trade=None` row is on a *selected* market"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:467-470"
  - id: F005
    severity: note
    category: test-coverage
    summary: "The fake repository's enumerated surface omits `transaction()` and any storage-failure injection"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:555-560"
  - id: F006
    severity: note
    category: sequencing
    summary: "The loud rename guard can abort host commands that Task 9.1 runs before the env file is renamed"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:203-211"
  - id: F007
    severity: note
    category: error-handling
    summary: "`PageCounts.__post_init__` uses `assert` for a check the task calls structural"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:437-443"
  - id: F008
    severity: note
    category: task-scoping
    summary: "Task 4.2 at effort 5 is the one task a junior could stall on"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:511-548"
  - id: F009
    severity: pass
    category: completeness
    summary: "Every success criterion maps to at least one task, and no task is orphaned"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:373-387"
  - id: F010
    severity: pass
    category: process
    summary: "Commit checkpoints are distributed, and tests sit with the code they cover"
    location: "project-documents/user/tasks/265-tasks.public-trades-collection-1.md:279-284"
  - id: F011
    severity: pass
    category: test-coverage
    summary: "No load-test task is required, consistent with the 264 precedent"
    location: "project-documents/user/slices/265-slice.public-trades-collection.md:88-96"
---

# Review: tasks — slice 265

**Verdict:** CONCERNS
**Model:** claude-opus-5

## Findings

### [CONCERN] `end` means two different instants inside Task 4.2, and the wrong reading silently defeats the request cap

Step 2 binds `end` to the phase-level bound (`sync_state['catalog'].last_full_sync_at − TRADE_LATE_ARRIVAL_GUARD`). Step 3 then says windows step from `watermark_ts` "the last clamped to `end`" — still the phase bound. But step 4 says `get_trades(min_ts=start − WINDOW_OVERLAP, max_ts=end, …)` and step 5 says `advance_watermark(end)`, where both must mean *this window's* end (`min(start + TRADE_WINDOW, phase_end)`), not the phase bound.

Read literally under the task's own binding, a junior implementer produces: window 1 requests `[watermark, phase_end)` — during the first-run drain that is ~60 days ≈ 600 k pages in a single window — and then advances the watermark straight to `phase_end`. Since Decision 8's cap is checked *before each window* (step 3), a single window means the cap is checked exactly once and never bounds the pass; Criterion 4 ("watermark equals the end of the last completed window") and Criterion 8 both fail. The design carries the same collision (Data Flow steps 4–5), so it will not correct the reader. Rename the window-local bound in step 4/5 (e.g. `window_end`) and reserve `end` for the phase bound. Task 4.3b case 2 should additionally assert the recorded `max_ts` of each request equals its own window's end, not just that the sequence is clamped.

### [CONCERN] The `.env` guard test and the guard implementation specify incompatible seams

The implementation bullet says the `model_validator` scans `os.environ` **and** `dotenv_values(ENV_FILE)` — `ENV_FILE` being the module-level constant `".env"` (`config/__init__.py`). The test bullet then requires the five old names "each written alone into a temporary env file that `Settings` is pointed at". Nothing in the task says how `Settings` is pointed at a temporary file: `model_config` hardcodes `env_file=ENV_FILE`, so the test must either monkeypatch the constant, or pass `_env_file=`, in which case a guard that reads the constant scans the developer's real `.env` and the test asserts nothing about the temp file. The rationale bullet is correct that this hole is the one a developer hits, which makes the test the important one — so pin the seam explicitly: the guard reads the *effective* env file (`self.model_config["env_file"]`, or an injectable module-level path), and the test states which mechanism it uses. As written, the implementer will pick one and the test will fail or pass vacuously depending on the pick.

### [NOTE] `init_state` idempotency is asserted in Task 3.3 but not specified in Task 3.1

Task 3.1 says `init_state(cutoff)` "inserts the row with both `watermark_ts` and `coverage_from_ts` set to the cutoff — first run only". Task 3.3 asserts it "sets both and is a no-op on a second call" (line 477). A plain `INSERT` raises on the second call (`surface` is the primary key of `kalshi.sync_state`); the no-op behavior needs `ON CONFLICT (surface) DO NOTHING`, which no implementation bullet mentions. Add it to Task 3.1, or drop the claim from the test — silently, this is the difference between a `UniqueViolation` propagating as a bug and a benign re-entry.

### [NOTE] Task 3.3 case 7 only fires if the `is_block_trade=None` row is on a *selected* market

`write_page` inserts only rows where `selected` is true, so a `NotNullViolation` for `is_block_trade` is reachable only when the null-carrying row belongs to a market the rule selects. Case 6 (non-UUID `trade_id`) fails earlier, at the `::uuid[]` cast in `unnest`, so it is unconditional — case 7 is not. State the precondition in the case, otherwise the implementer debugs a `pytest.raises` that never fires and may "fix" it by weakening the NOT NULL that Task 2.2 argues for at length.

### [NOTE] The fake repository's enumerated surface omits `transaction()` and any storage-failure injection

Every core in this codebase wraps repository writes in `async with self.repository.transaction():` (`candle_sync.py:127,237`, `sync.py:158`, `sync_writer.py:58,102`), and `fake_candle_repository.py` supplies it plus a `fail_on(method, exc, at=…)` hook. Task 4.3a lists `read_state`, `init_state`, `advance_watermark`, `set_last_full_sync`, `read_catalog_walk_start`, `write_page` — no `transaction()`, and no injection hook. "Model both on `fake_candle_repository.py`" probably carries it, but naming it costs a line. Related: Task 4.1's success criterion requires `classify_trades` to return `STORAGE_ABORT`, yet no core-level test can produce a `psycopg.OperationalError` mid-window without such a hook, so the abort-leaves-the-watermark behavior is proven only for `ProviderError` (case 6).

### [NOTE] The loud rename guard can abort host commands that Task 9.1 runs before the env file is renamed

The guard raises at `Settings` construction, so it fires for *every* command, including `mt data migrate status/apply`. Part 2's Task 9.1 orders the deploy as migrate → apply → *then* replace the `MT_KALSHI_CANDLE_*` lines in `/etc/manta-trading.env`, and the installed timer may fire in that window with the old variables still in `EnvironmentFile`. This is harmless if production runs at the defaults (all five lines are commented in `deploy/manta-trading.env.example:25-29`), which is likely — but the ordering is only safe by accident. Task 9.1 should either rename the env lines first, or state that it verified none are set before applying.

### [NOTE] `PageCounts.__post_init__` uses `assert` for a check the task calls structural

The reasoning for carrying `selected` rather than deriving it is right — derived, the identity collapses to `fetched = fetched`. But `assert` is stripped under `python -O`, so the "structural" guarantee for Criterion 2's exact accounting disappears in any optimized run. Raise an explicit exception instead; the surrounding taxonomy already expects non-`OperationalError` failures on this path to propagate as bugs.

### [NOTE] Task 4.2 at effort 5 is the one task a junior could stall on

The Context Summary pre-justifies the ceiling breach, and the argument (splitting yields untestable pieces) is largely right. Worth noting anyway that the design itself names two units — `TradeSync.run()` (steps 1–3, 6) and `_window(start, end)` (steps 4–5) — and Task 4.3b's eleven cases already partition cleanly along that seam (cases 1, 2, 3, 7, 8 on `run`; 4, 5, 6, 11 on `_window`). If the section runs long, that is where to cut. No change required.

### [PASS] Every success criterion maps to at least one task, and no task is orphaned

Criterion 1 → Tasks 4.4, 6.1; 2 → 3.2, 3.3, 4.3b/4; 3 → 3.3/4, 6.1, 7.4; 4 → 4.3b/5–6, 7.4; 5 → 1.1–1.5, 3.3/5, 7.2; 6 → 4.2 step 1, 4.3b/1 and /8, 9.2; 7 → 4.2 step 2, 4.3b/2–3, 7.3; 8 → 4.2 step 3, 4.3b/7, 4.6, 9.3; 9 → 3.3/2, 4.3b/10, 7.3; 10 → 2.2, 6.2, 7.2; 11 → part 2 Section 5; 12 → 2.3 (last bullet), 7.5; 13 → part 2 Section 9. Nothing in part 1 fails to trace back — Task 1.1's `MARKET_JOIN` snapshot is the baseline for Criterion 5's last clause (verified: `test/unit/data/kalshi/test_selection_sql.py` has no such assertion today), and Task 4.5's fixtures are the design's *Fixtures and recorder* item.

### [PASS] Commit checkpoints are distributed, and tests sit with the code they cover

One gate-and-commit task per section (1.6, 2.4, 3.4, 4.7), matching the design's *Development Approach* and the project's per-section commit granularity — nothing batched at the end. New behavior is tested where it is written (1.3, 1.4, 2.1, 4.1, 4.4, 4.5, 4.6 carry their own test bullets), with only two deferrals, both to the immediately following task (3.1 → 3.3, 4.2 → 4.3b), and both explicitly owned there.

### [PASS] No load-test task is required, consistent with the 264 precedent

`test/load/` exists in this repo (146, 167, 169, 187), but slice 265 states no NFR: its *Workload (derived)* figures are measurements and estimates, and no success criterion sets a threshold to gate on. This matches the resolution recorded for 264 (F008: "adding a `test/load/` task would invent a bound the design declined to set") and part 2's F007. The performance evidence Criterion 12 does ask for — per-window wall time uncompressed vs. compressed — is correctly a rehearsal measurement (part 2 Tasks 7.3–7.5), not a CI-gated load test, and part 2 is careful to compare re-walk against re-walk rather than first-pass against re-walk.
